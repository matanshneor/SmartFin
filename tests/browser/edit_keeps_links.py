"""A real browser test: an edit changes only what was changed: an empty category,
an owner who left, a project that doesn't track (B9).

Not collected by pytest (it has no test_ prefix), because it brings up a local
server, signs in as test user A (RLS_TEST_* in .env) and writes to the real
database, and cleans up after itself. Server responses to the slow requests are
mocked in Playwright.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/edit_keeps_links.py
"""
import os, json, time, datetime, subprocess, signal
from playwright.sync_api import sync_playwright
import _accounts  # noqa: F401 (temporary test accounts, deleted at the end of the run)

ROOT = os.getcwd()
def _dotenv(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); out[k.strip()] = v.strip().strip('"').strip("'")
    return out
env = {**os.environ, **{k: v for k, v in _dotenv(os.path.join(ROOT, ".env")).items() if v}}
env["PORT"] = "8099"
srv = subprocess.Popen([os.path.join(ROOT, ".venv/bin/python3"), "-m", "backend.app"], cwd=ROOT, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       start_new_session=True)   # in debug mode Flask spawns a child process that must die with it
BASE = "http://127.0.0.1:8099"
JPEG = "/tmp/tiny.jpg"
open(JPEG, "wb").write(bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9"))
created = None
cat = None
try:
    for _ in range(40):
        try:
            import urllib.request; urllib.request.urlopen(BASE + "/health", timeout=1); break
        except Exception: time.sleep(0.5)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 390, "height": 844})
        page.goto(BASE + "/login")
        page.fill("#identifier", env.get("RLS_TEST_EMAIL_A", "rls-test-family-a@smartfin.test"))
        page.fill("#password", env["RLS_TEST_PASSWORD_A"])
        page.click("button.submit-btn")
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        if "/onboarding" in page.url:
            print("NOTE: test user lands on onboarding"); 
        today = datetime.date.today().isoformat()
        rc = page.request.post(BASE + "/api/categories", data=json.dumps(
            {"name": "SCANTEST-CAT", "icon": "🧪", "type": "expense"}),
            headers={"Content-Type": "application/json"})
        cat = (rc.json().get("category") or rc.json()).get("id")
        print("temp category:", rc.status, bool(cat))
        helper = lambda *a: subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), os.path.join(ROOT, "tests/browser/edit_keep_helper.py"), *a],
                                           cwd=ROOT, capture_output=True, text=True).stdout.strip().splitlines()[-1]
        post = lambda url, body: page.request.post(BASE + url, data=json.dumps(body),
                                                  headers={"Content-Type": "application/json"}).json()
        put = lambda url, body: page.request.put(BASE + url, data=json.dumps(body),
                                                headers={"Content-Type": "application/json"}).json()
        print("setup:", helper("setup", today))
        # "a transaction without a category" was dropped from here (7.10): since 30.9
        # the database itself doesn't allow it (transactions_category_required), so
        # there is nothing to preserve on edit
        # a project tracking income, an income in it, and then it stops tracking
        proj = post("/api/projects", {"name": "KEEP-PROJ", "track_expense": True, "track_income": True})
        pid = (proj.get("project") or proj).get("id")
        # category required since 30.9, and in a project, a category of the project
        # itself
        pcats = page.request.get(BASE + f"/api/projects/{pid}/categories?type=income").json()
        pcats = pcats.get("categories", pcats) if isinstance(pcats, dict) else pcats
        print("project income category:", bool(pcats))
        post("/api/transactions", {"amount": "700", "type": "income", "date": today,
                                   "project_category_id": pcats[0]["id"] if pcats else None,
                                   "description": "KEEP-PROJ-INC", "project_id": pid})
        put(f"/api/projects/{pid}", {"name": "KEEP-PROJ", "track_expense": True, "track_income": False})
        before = json.loads(helper("read"))

        def edit(selector, new_amount):
            page.goto(BASE + "/month")
            for h in page.locator("#txScreenOpen, .legend-item.clickable").all():
                try: h.click(timeout=1000)
                except Exception: pass
            page.locator(selector).locator("visible=true").first.click()
            page.wait_for_selector("#modalOverlay.open")
            page.fill("#txAmount", new_amount); page.click("#submitBtn"); page.wait_for_timeout(2500)

        for desc, amount in (("KEEP-GONE", "91"), ("KEEP-PROJ-INC", "701")):
            tid = page.evaluate(f"""() => {{ const r = [...document.querySelectorAll('.cat-tx-row')]
                .find(e => e.dataset.description === '{desc}'); return r ? r.dataset.id : null; }}""") \
                or None
            page.goto(BASE + "/month")
            tid = page.evaluate(f"""() => {{ const r = [...document.querySelectorAll('.cat-tx-row')]
                .find(e => e.dataset.description === '{desc}'); return r ? r.dataset.id : null; }}""")
            edit(f'.cat-tx-row[data-id="{tid}"]', amount)

        after = json.loads(helper("read"))
        for desc, field in (("KEEP-GONE", "user_is_B"), ("KEEP-PROJ-INC", "project_id")):
            was, now = before[desc], after[desc]
            print(f"{desc:14} amount {was['amount']}→{now['amount']} | {field}: {was[field]!s:.8} → {now[field]!s:.8}",
                  "->", "OK" if now[field] == was[field] and now["amount"] != was["amount"] else "WRONG")
        b.close()
finally:
    # Cleanup: everything the script created in the test family, even if it failed
    # midway. Plain code and not a concatenated string: the previous version broke
    # on created = None ("invalid input syntax for type uuid") and left leftovers
    # for the next run.
    cleanup = "\n".join([
        "import os, sys",
        "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "t = db.get_client().table",
        "t('transactions').delete().eq('family_id', fid).in_('description', "
        "['SUPER-ORIG', 'FIRST', 'THIRD', 'CHART-1', 'CHART-2', 'PAST-ADD', 'ROW-A', 'ROW-B']).execute()",
        "t('categories').delete().eq('family_id', fid).eq('name', 'SCANTEST-CAT').execute()",
        "print('cleaned')",
    ])
    # First the transactions (KEEP-*) and only then the temporary category: deleting
    # a category a transaction still uses nulls the transaction's category, and the
    # database refuses (category required, 30.9)
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), os.path.join(ROOT, "tests/browser/edit_keep_helper.py"), "cleanup"], cwd=ROOT)
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)   # Flask's child process closes a moment later
