"""A real browser test: "קביעת תקציבים": one window for all categories on the month
page (Matan, 30.9).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/budget_sheet.py
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

        ids = {}
        for name, icon in (("BG-סופר", "🛒"), ("BG-דלק", "⛽")):
            _, c2 = api("POST", "/api/categories", {"name": name, "icon": icon, "type": "expense"})
            ids[name] = (c2.get("category") or c2)["id"]
        today = datetime.date.today().isoformat()
        for name, amount in (("BG-סופר", 1760), ("BG-דלק", 380)):
            api("POST", "/api/transactions", {"amount": amount, "type": "expense", "category_id": ids[name],
                                              "date": today, "description": "BG-TEST"})
        api("PUT", "/api/family/settings", {"limits": {ids["BG-דלק"]: {"amount": 500, "alert": True}}})

        page.goto(BASE + "/month"); page.wait_for_timeout(1500)
        print("per-row link gone:", page.locator("text=קביעת תקציב לקטגוריה").count() == 0)
        page.click("#budgetsOpen"); page.wait_for_timeout(400)
        rows = page.locator(".budget-sheet-row")
        print("window rows:", [t.replace("\n", " / ") for t in rows.all_inner_texts() if "BG-" in t])
        page.screenshot(path="/tmp/budget_sheet.png")
        page.fill(f'input[data-cat="{ids["BG-סופר"]}"]', "2000")
        page.fill(f'input[data-cat="{ids["BG-דלק"]}"]', "")
        page.click(".budget-sheet [data-save]"); page.wait_for_timeout(2500)
        print("toast:", page.locator("#appToast .toast-msg").all_inner_texts())
        card = page.locator("#expense-breakdown")
        print("bars now:", [t for t in card.locator(".cat-budget-text").all_inner_texts()])
        page.click("#budgetsOpen"); page.wait_for_timeout(300)
        print("reopen shows:", page.input_value(f'input[data-cat="{ids["BG-סופר"]}"]'), "|", repr(page.input_value(f'input[data-cat="{ids["BG-דלק"]}"]')))
        page.keyboard.press("Escape")
        prev = (datetime.date.today().replace(day=1) - datetime.timedelta(days=1))
        page.goto(f"{BASE}/month?year={prev.year}&month={prev.month}"); page.wait_for_timeout(1000)
        print("past month — no button:", page.locator("#budgetsOpen").count() == 0)
        b.close()
finally:
    cleanup = "\n".join([
        "import os, sys", "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "t = db.get_client().table",
        "print('cleaned', len(t('transactions').delete().eq('family_id', fid).eq('description', 'BG-TEST').execute().data))",
        "t('categories').delete().eq('family_id', fid).like('name', 'BG-%').execute()",
        "print('limits reset', db.update_family_settings(fid, {'limits': {}}))",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
