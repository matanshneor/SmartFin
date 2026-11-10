"""A real browser test: two edits in a row on the same page: both are saved.

Matan (30.9): "when I try to edit a transaction, sometimes it doesn't let me tap
save changes". After a successful save the button stayed "שומר…" (isSubmitting),
and on a page that refreshes with a soft refresh (home) the JS isn't reloaded: the
next edit wasn't sent.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/edit_twice.py
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

        def api(method, url, body=None):
            r = page.request.fetch(BASE + url, method=method, data=json.dumps(body) if body else None,
                                   headers={"Content-Type": "application/json"})
            return r.status, (r.json() if r.body() else None)

        cats = page.request.get(BASE + "/api/categories").json()
        cat = next(c["id"] for c in cats if c["type"] == "expense")
        today = datetime.date.today().isoformat()
        ids = []
        for amount in (10, 20):
            st, created = api("POST", "/api/transactions", {"amount": amount, "type": "expense", "date": today,
                                                           "description": "EDIT-TWICE", "category_id": cat})
            ids.append((created.get("transaction") or created)["id"])
        puts = []
        page.on("request", lambda r: puts.append(r.url.rsplit("/", 1)[-1][:8]) if r.method == "PUT" and "/api/transactions/" in r.url else None)

        def edit(tx, new_amount, where):
            row = page.locator(f'[data-id="{tx}"]').first
            page.wait_for_timeout(800)
            (row.locator(".tx-head") if row.locator(".tx-head").count() else row).evaluate("e => e.click()")
            page.wait_for_selector(".inline-more, #modalOverlay.open", timeout=5000)
            if not page.evaluate("document.getElementById('modalOverlay').classList.contains('open')"):
                page.click(".inline-more")
                page.wait_for_selector("#modalOverlay.open", timeout=5000)
            page.wait_for_timeout(600)
            page.fill("#txAmount", str(new_amount))
            before = len(puts)
            page.locator("#submitBtn").click(force=True)   # like a finger: also taps a "stuck" button
            page.wait_for_timeout(2500)
            busy = page.evaluate("document.getElementById('submitBtn').classList.contains('is-busy')")
            still_open = page.evaluate("document.getElementById('modalOverlay').classList.contains('open')")
            print(f"{where}: PUT sent={len(puts) > before} | modal still open={still_open} | button busy={busy}")
            if still_open:
                page.keyboard.press("Escape"); page.wait_for_timeout(400)

        page.goto(BASE + "/")
        edit(ids[0], 11, "home, 1st edit")
        edit(ids[1], 21, "home, 2nd edit")
        page.goto(BASE + "/month")
        edit(ids[0], 12, "month, 1st edit")
        edit(ids[1], 22, "month, 2nd edit")
        b.close()
finally:
    cleanup = "\n".join([
        "import os, sys", "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "db.get_client().table('transactions').delete().eq('family_id', fid).eq('description', 'EDIT-TWICE').execute()",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
