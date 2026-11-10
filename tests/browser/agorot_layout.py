"""A real browser test: agorot in the display: how long amounts look in the small
squares (item 1-3).

Not collected by pytest (it has no test_ prefix), because it brings up a local
server, signs in as test user A (RLS_TEST_* in .env) and writes to the real
database, and cleans up after itself. Server responses to the slow requests are
mocked in Playwright.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/agorot_layout.py
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
        created = None
        for amount, kind in (("12345.67", "income"), ("1234.56", "expense"), ("12.50", "expense"), ("250.05", "savings")):
            page.request.post(BASE + "/api/transactions", data=json.dumps(
                {"amount": amount, "type": kind, "date": today, "description": "AGOROT",
                 "category_id": cat if kind == "expense" else None}), headers={"Content-Type": "application/json"})
        for path, shot in (("/", "/tmp/agorot_home.png"), ("/month", "/tmp/agorot_month.png")):
            page.goto(BASE + path); page.wait_for_timeout(1800)
            page.screenshot(path=shot)
            over = page.evaluate("""() => [...document.querySelectorAll('.kpi-value, .card-amount, .hero-amount')]
                .filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.textContent.trim())""")
            print(path, "overflowing:", over)
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
        "['AGOROT']).execute()",
        "t('categories').delete().eq('family_id', fid).eq('name', 'SCANTEST-CAT').execute()",
        "print('cleaned')",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)   # Flask's child process closes a moment later
