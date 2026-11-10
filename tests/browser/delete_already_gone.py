"""A real browser test: deleting a transaction another family member already
deleted: the row goes away, and "העסקה כבר נמחקה" appears instead of "מחיקה נכשלה".

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/delete_already_gone.py
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

        def api(method, url, body=None):
            r = page.request.fetch(BASE + url, method=method, data=json.dumps(body) if body else None,
                                   headers={"Content-Type": "application/json"})
            return r.status, (r.json() if r.body() else None)

        cats = page.request.get(BASE + "/api/categories").json()
        cat = next(c["id"] for c in cats if c["type"] == "expense")
        today = datetime.date.today().isoformat()
        st, created = api("POST", "/api/transactions", {"amount": 77, "type": "expense", "date": today,
                                                       "description": "GONE-TEST", "category_id": cat})
        tx = (created.get("transaction") or created)["id"]
        print("created:", st)

        page.goto(BASE + "/")
        row = page.locator(f'.transaction-item[data-id="{tx}"]')
        print("row on screen:", row.count())

        # another family member deletes it in the meantime
        print("deleted behind the back:", api("DELETE", f"/api/transactions/{tx}")[0])

        row.locator(".tx-head").click()
        page.wait_for_selector("#modalOverlay.open #deleteTxBtn", timeout=5000)
        page.click("#deleteTxBtn")
        page.wait_for_selector("#confirmOverlay.open", timeout=5000)
        page.click("#confirmYes")
        page.wait_for_timeout(1200)
        shown = page.eval_on_selector(".toast", "t => t.classList.contains('show') ? t.textContent : null")
        print("toast:", shown, "| row still on screen:", row.count())
        page.screenshot(path="/tmp/already_gone.png")
        b.close()
finally:
    cleanup = "\n".join([
        "import os, sys",
        "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "db.get_client().table('transactions').delete().eq('family_id', fid).eq('description', 'GONE-TEST').execute()",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
