"""A real browser test: fast taps on two transactions: the last tap decides (B8).

Not collected by pytest (it has no test_ prefix), because it brings up a local
server, signs in as test user A (RLS_TEST_* in .env) and writes to the real
database, and cleans up after itself. Server responses to the slow requests are
mocked in Playwright.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/fast_row_clicks.py
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
        ids = {}
        for desc, amount in (("ROW-A", "180"), ("ROW-B", "250")):
            r = page.request.post(BASE + "/api/transactions", data=json.dumps(
                {"amount": amount, "type": "expense", "date": today, "description": desc, "category_id": cat}),
                headers={"Content-Type": "application/json"})
            ids[desc] = r.json().get("transaction", r.json()).get("id")

        held = []
        def hold(route): held.append(route)
        def row(desc):
            return page.locator(f'.cat-tx-row[data-id="{ids[desc]}"]').locator("visible=true").first
        def form():
            return page.evaluate("""() => ({open: modalOverlay.classList.contains('open'),
                title: document.getElementById('modalTitle').textContent,
                amount: txAmount.value, desc: txDescription.value})""")
        def release_newest_first():
            """The first tap's response arrives last, and that is guaranteed, not
            accidental: the real responses are collected from the server first, and
            delivered to the browser one by one."""
            page.wait_for_timeout(300)
            answers = [(r, r.fetch()) for r in held]
            for r, resp in reversed(answers):
                r.fulfill(response=resp)
                page.wait_for_timeout(500)
            held.clear()
            page.wait_for_timeout(1000)

        # ── 1. Transaction A, and immediately transaction B, and A's responses arrive last
        # "לכל עסקאות החודש" appears only above 5 transactions (since 5.10). Here there
        # are fewer, and they are in the short list, where + is also reachable (the
        # full screen covers it)
        open_all = lambda: page.locator("#txScreenOpen").click() if page.locator("#txScreenOpen").count() else None
        page.route("**/api/categories", hold)
        page.goto(BASE + "/month")
        open_all()      # "כל העסקאות": the list is collapsed
        row("ROW-A").click(); row("ROW-B").click()
        release_newest_first()
        f = form()
        print("A then B:", f)
        print("   shows B:", f["open"] and f["desc"] == "ROW-B" and f["amount"] in ("250", "250.0"))
        page.fill("#txAmount", "251"); page.click("#submitBtn"); page.wait_for_timeout(2500)
        rows = page.evaluate("""(ids) => ids.map(id => {
            const el = document.querySelector('.cat-tx-row[data-id="' + id + '"]');
            return el ? el.dataset.amount + ' ' + el.dataset.description : null; })""", [ids["ROW-A"], ids["ROW-B"]])
        # Amount **and description**: the previous version of the test checked only
        # the amount, and mistakenly approved "251 ROW-A": B overwritten with A's
        # description.
        print("   after save:", rows, "->",
              "OK" if rows == ["180.0 ROW-A", "251.0 ROW-B"] else "WRONG")

        # ── 2. A transaction, and immediately +, and the transaction's responses arrive last
        page.goto(BASE + "/month")        # the cache empty again
        open_all()
        row("ROW-A").click(); page.click("#fabBtn")
        release_newest_first()
        f = form()
        print("row then +:", f)
        print("   empty add form:", f["open"] and f["title"] == "הוספת עסקה" and f["amount"] == "" and f["desc"] == "")
        page.click("#modalClose")
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
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)   # Flask's child process closes a moment later
