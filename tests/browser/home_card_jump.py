"""A real browser test: from a card on the home page to the class on the month page,
without the sticky header at the top hiding the top of the card (Matan, 2.10).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/home_card_jump.py
"""
import os, json, time, datetime, subprocess, signal
from playwright.sync_api import sync_playwright
import _accounts  # noqa: F401 (temporary test accounts, deleted at the end of the run)

ROOT = os.getcwd()
env = dict(os.environ)
for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
env["PORT"] = "8091"
srv = subprocess.Popen([os.path.join(ROOT, ".venv/bin/python3"), "-m", "backend.app"], cwd=ROOT, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
BASE = "http://127.0.0.1:8091"
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
        def api(m, u, body=None):
            r = page.request.fetch(BASE + u, method=m, data=json.dumps(body) if body else None,
                                   headers={"Content-Type": "application/json"})
            return r.status, (r.json() if r.body() else None)
        _, cats = api("GET", "/api/categories")
        today = datetime.date.today().isoformat()
        for typ, amt in (("income", 9000), ("expense", 1200), ("savings", 500)):
            cat = next(c for c in cats if c["type"] == typ)
            api("POST", "/api/transactions", {"amount": amt, "type": typ, "category_id": cat["id"],
                                              "date": today, "description": "HJ-TEST"})
        for which in ("income", "expense", "savings"):
            page.goto(BASE + "/"); page.wait_for_timeout(800)
            page.locator(f'a.summary-card[href*="#{which}-breakdown"]').first.click()
            page.wait_for_url(lambda u: "/month" in u, timeout=15000); page.wait_for_timeout(1500)
            r = page.evaluate("""(id) => {
                const bar = document.querySelector('.month-sticky');
                const barBottom = bar && !bar.hidden ? bar.getBoundingClientRect().bottom : 0;
                const top = document.getElementById(id).getBoundingClientRect().top;
                return {barShown: !!(bar && !bar.hidden), barBottom: Math.round(barBottom), cardTop: Math.round(top)};
            }""", f"{which}-breakdown")
            print(which, r, "| covered" if r["cardTop"] < r["barBottom"] else "| clear")
            page.screenshot(path=f"/tmp/home_card_jump_{which}.png", clip={"x": 0, "y": 0, "width": 390, "height": 260})
        page.screenshot(path="/tmp/home_card_jump.png")
        b.close()
finally:
    cleanup = "\n".join(["import os, sys", "sys.path.insert(0, '.')", "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "print('cleaned', len(db.get_client().table('transactions').delete().eq('description', 'HJ-TEST').execute().data))"])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
