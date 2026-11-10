"""A real browser test: the month page in Matan's order: a wide monthly balance,
three equal boxes, and the sections in order.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/month_order.py
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
        by_type = {}
        for c in cats: by_type.setdefault(c["type"], c["id"])
        today = datetime.date.today().isoformat()
        for amount, kind in (("11527", "income"), ("4459.80", "expense"), ("8000", "savings")):
            api("POST", "/api/transactions", {"amount": amount, "type": kind, "date": today,
                                              "description": "ORDER-TEST", "category_id": by_type[kind]})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/month"); page.wait_for_timeout(1800)
        print("net:", page.inner_text(".month-net-value").strip(), "| class:", page.get_attribute(".month-net", "class"))
        print("three chips widths:", page.eval_on_selector_all(".kpi-chips-three .kpi-chip", "es => es.map(e => Math.round(e.getBoundingClientRect().width))"))
        print("section order:", page.locator("main .chart-title").all_inner_texts())
        page.click('a.kpi-link[href="#savings-breakdown"]'); page.wait_for_timeout(900)
        print("savings chip →", page.evaluate("Math.round(document.getElementById('savings-breakdown').getBoundingClientRect().top)"))
        for w in (320, 375):
            page.set_viewport_size({"width": w, "height": 800}); page.wait_for_timeout(300)
            print(w, "sideways:", page.evaluate("document.documentElement.scrollWidth > innerWidth"))
        page.set_viewport_size({"width": 390, "height": 844}); page.evaluate("scrollTo(0,0)"); page.wait_for_timeout(300)
        page.screenshot(path="/tmp/month_top.png", clip={"x": 0, "y": 0, "width": 390, "height": 330})
        print("errors:", errors)
        b.close()
finally:
    cleanup = "\n".join([
        "import os, sys", "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "db.get_client().table('transactions').delete().eq('family_id', fid).eq('description', 'ORDER-TEST').execute()",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
