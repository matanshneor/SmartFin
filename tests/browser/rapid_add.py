"""A real browser test: consecutive adding on the home page: a save that returns
while the next one is being typed (B2).

Not collected by pytest (it has no test_ prefix), because it brings up a local
server, signs in as test user A (RLS_TEST_* in .env) and writes to the real
database, and cleans up after itself. Server responses to the slow requests are
mocked in Playwright.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/rapid_add.py
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
        r = page.request.post(BASE + "/api/transactions", data=json.dumps(
            {"amount": "180", "type": "expense", "date": today, "description": "SUPER-ORIG", "category_id": cat}),
            headers={"Content-Type": "application/json"})
        created = r.json().get("transaction", r.json()).get("id")
        print("created tx:", r.status, bool(created))
        fid = page.evaluate("() => document.cookie") and None

        held = []
        def hold(route):
            if route.request.method == "POST": held.append(route)
            else: route.continue_()
        page.route("**/api/transactions", hold)
        page.route("**/api/receipts/discard", lambda r: r.fulfill(status=200, body="{}"))
        page.goto(BASE + "/")
        form = lambda: page.evaluate("""() => ({open: modalOverlay.classList.contains('open'),
                     amount: txAmount.value, desc: txDescription.value, error: formError.textContent,
                     toast: document.getElementById('appToast').textContent})""")

        def start(amount, desc):
            page.click("#fabBtn"); page.wait_for_selector("#modalOverlay.open")
            page.fill("#txAmount", amount); page.fill("#txDescription", desc)

        # ── A. The first succeeds while the second is being typed
        start("180", "FIRST"); page.click("#submitBtn")
        page.wait_for_function("() => !modalOverlay.classList.contains('open')")
        start("12", "SECOND-TYPING")
        page.wait_for_timeout(300); held.pop().continue_()           # the first save returns now
        page.wait_for_timeout(2500)
        a = form()
        print("A) after 1st save returned:", a)
        print("   second form kept:", a["open"] and a["amount"] == "12" and a["desc"] == "SECOND-TYPING")
        page.click("#modalClose")

        # ── B. The first fails while the second is being typed
        page.wait_for_timeout(500)
        start("50", "THIRD"); page.click("#submitBtn")
        page.wait_for_function("() => !modalOverlay.classList.contains('open')")
        start("7", "FOURTH-TYPING")
        page.wait_for_timeout(300)
        held.pop().fulfill(status=500, content_type="application/json", body='{"error": "תקלה מדומה"}')
        page.wait_for_timeout(1500)
        bb = form()
        print("B) after 1st save failed:", bb)
        print("   second form kept, no error in it:", bb["open"] and bb["amount"] == "7" and bb["desc"] == "FOURTH-TYPING" and bb["error"] == "")
        print("   says which one failed:", "₪50" in bb["toast"] and "לא נשמרה" in bb["toast"])
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
        "['SUPER-ORIG', 'FIRST', 'THIRD', 'CHART-1', 'CHART-2']).execute()",
        "t('categories').delete().eq('family_id', fid).eq('name', 'SCANTEST-CAT').execute()",
        "print('cleaned')",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)   # Flask's child process closes a moment later
