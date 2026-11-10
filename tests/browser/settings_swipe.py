"""A real browser test: swiping a recurring transaction in settings stops the series
and doesn't delete money (B10).

Not collected by pytest (it has no test_ prefix), because it brings up a local
server, signs in as test user A (RLS_TEST_* in .env) and writes to the real
database, and cleans up after itself. Server responses to the slow requests are
mocked in Playwright.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/settings_swipe.py
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
            {"amount": "5000", "type": "expense", "date": today, "description": "SWIPE-RENT",
             "category_id": cat, "is_recurring": True, "recurring_frequency": "monthly_1"}),
            headers={"Content-Type": "application/json"})
        print("series created:", r.status)

        page.goto(BASE + "/settings")
        page.locator(".settings-group-header", has_text="עסקאות קבועות").click()
        row = page.locator('.recurring-row[data-description="SWIPE-RENT"]')
        row.wait_for()
        # a real finger swipe (touch events), and then the revealed action
        row.evaluate("""(el) => {
            const r = el.getBoundingClientRect(), y = r.top + r.height / 2, x0 = r.left + r.width / 2;
            const fire = (type, x) => {
                const t = new Touch({identifier: 1, target: el, clientX: x, clientY: y});
                el.dispatchEvent(new TouchEvent(type, {touches: type === 'touchend' ? [] : [t],
                    changedTouches: [t], bubbles: true, cancelable: true}));
            };
            fire('touchstart', x0);
            for (let i = 1; i <= 6; i++) fire('touchmove', x0 - i * 18);
            fire('touchend', x0 - 108);
        }""")
        label = row.locator(".swipe-action-delete").text_content()
        row.locator(".swipe-action-delete").evaluate("el => el.click()")
        page.wait_for_selector("#confirmOverlay.open", timeout=5000)
        title = page.text_content("#confirmTitle")
        print("swipe label:", repr(label), "| dialog:", repr(title))
        page.click("#confirmYes"); page.wait_for_timeout(2500)

        check = subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", "\n".join([
            "import os, sys, json", "sys.path.insert(0, '.')",
            "from dotenv import load_dotenv; load_dotenv('.env')",
            "from backend import supabase_config as db",
            "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
            "db.set_auth_token(r.session.access_token)",
            "fid = db.get_profile(r.user.id)['family_id']",
            "rows = db.get_client().table('transactions').select('amount, is_recurring, recurring_end_date')"
            ".eq('family_id', fid).eq('description', 'SWIPE-RENT').execute().data",
            "print(json.dumps(rows))"])], cwd=ROOT, capture_output=True, text=True).stdout.strip().splitlines()[-1]
        rows = json.loads(check)
        print("after confirming:", rows)
        # stopping a series (like the ✕) turns off "recurring" and keeps the row: the
        # money that was recorded
        print("   money kept, series stopped:",
              len(rows) == 1 and rows[0]["amount"] == 5000 and rows[0]["is_recurring"] is False)
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
        "['SUPER-ORIG', 'FIRST', 'THIRD', 'CHART-1', 'CHART-2', 'PAST-ADD', 'ROW-A', 'ROW-B', 'SWIPE-RENT']).execute()",
        "t('categories').delete().eq('family_id', fid).eq('name', 'SCANTEST-CAT').execute()",
        "print('cleaned')",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)   # Flask's child process closes a moment later
