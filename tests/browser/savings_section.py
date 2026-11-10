"""A real browser test: "חיסכון — לאן הופרש" (proposal B): set aside this month, a
percentage of income, and a percentage of savings for each destination.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/savings_section.py
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
        inc = next(c["id"] for c in cats if c["type"] == "income")
        sav = [c["id"] for c in cats if c["type"] == "savings"]
        created = []
        if len(sav) < 2:
            _, c2 = api("POST", "/api/categories", {"name": "SAV-SECOND", "icon": "🏦", "type": "savings"})
            sav.append((c2.get("category") or c2)["id"])
        today = datetime.date.today().isoformat()
        for amount, kind, cat in ((11527, "income", inc), (5000, "savings", sav[0]), (3000, "savings", sav[1])):
            api("POST", "/api/transactions", {"amount": amount, "type": kind, "date": today,
                                              "description": "SAV-TEST", "category_id": cat})
        page.goto(BASE + "/month"); page.wait_for_timeout(1500)
        sec = page.locator("#savings-breakdown")
        print("title:", sec.locator(".chart-title").inner_text(), "| center:", sec.locator(".savings-total-value").inner_text(),
              "|", sec.locator(".savings-of-income").inner_text(),
              "| canvas:", sec.locator("canvas").count())
        print("rows:", list(zip(sec.locator(".legend-name").all_inner_texts(), sec.locator(".legend-pct").all_inner_texts())))
        sec.locator(".legend-item.clickable").first.click(); page.wait_for_timeout(400)
        print("expanded:", sec.locator(".cat-tx-row:visible").count(), "transaction(s)")
        for w in (320, 390):
            page.set_viewport_size({"width": w, "height": 800}); page.wait_for_timeout(300)
            print(w, "sideways:", page.evaluate("document.documentElement.scrollWidth > innerWidth"))
        page.set_viewport_size({"width": 390, "height": 844}); sec.scroll_into_view_if_needed()
        sec.screenshot(path="/tmp/savings_b.png")
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
        "print('cleaned', len(t('transactions').delete().eq('family_id', fid).eq('description', 'SAV-TEST').execute().data))",
        "t('categories').delete().eq('family_id', fid).eq('name', 'SAV-SECOND').execute()",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
