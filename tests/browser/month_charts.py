"""A real browser test: the charts on the month page, after the first transaction in
an empty month, and after adding (B5).

Not collected by pytest (it has no test_ prefix), because it brings up a local
server, signs in as test user A (RLS_TEST_* in .env) and writes to the real
database, and cleans up after itself. Server responses to the slow requests are
mocked in Playwright.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/month_charts.py
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
        page.goto(BASE + "/month")
        state = lambda: page.evaluate("""() => ({
            chartLib: typeof Chart !== 'undefined',
            // "הוצאות מול חיסכון" הוסר (30.9) — בודקים את גרף ההוצאות לפי קטגוריה
            overviewDrawn: typeof Chart !== 'undefined' && !!Chart.getChart(document.getElementById('expenseChart')),
            legend: [...document.querySelectorAll('#expense-breakdown .doughnut-total')].map(e => e.textContent.trim())})""")
        print("empty month:", state())

        def add(amount, desc):
            page.click("#fabBtn"); page.wait_for_selector("#modalOverlay.open")
            page.fill("#txAmount", amount); page.fill("#txDescription", desc)
            page.click("#submitBtn")
            page.wait_for_timeout(3500)

        add("300", "CHART-1")
        s1 = state(); toast1 = page.text_content("#appToast")
        print("after 1st tx:", s1, "| toast:", repr(toast1))
        print("   charts appear in a month that was empty:", s1["overviewDrawn"] and s1["legend"] == ["₪300"])
        add("200", "CHART-2")
        s2 = state()
        print("after 2nd tx:", s2)
        print("   chart numbers follow the new total:", s2["overviewDrawn"] and s2["legend"] == ["₪500"])
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
