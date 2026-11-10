"""A real browser test: tapping income/expenses/savings (on the home page and the
month page) leads to their breakdown.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/cards_to_breakdown.py
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
        for amount, kind in (("5000", "income"), ("1200", "expense"), ("300", "savings")):
            api("POST", "/api/transactions", {"amount": amount, "type": kind, "date": today,
                                              "description": "GO-TEST", "category_id": by_type[kind]})

        def landed(expected):
            page.wait_for_timeout(1200)
            top = page.evaluate(f"document.getElementById('{expected}').getBoundingClientRect().top")
            flash = page.evaluate(f"document.getElementById('{expected}').classList.contains('flash')")
            opacity = page.evaluate(f"getComputedStyle(document.getElementById('{expected}')).opacity")
            return f"url #{page.url.split('#')[-1]} | section top at {round(top)}px | flash={flash} | opacity={opacity}"

        for kind in ("income", "expense", "savings"):
            page.goto(BASE + "/")
            page.click(f"a.summary-card.{kind}")
            page.wait_for_url("**/month#*", timeout=8000)
            print(f"home {kind} card →", landed(f"{kind}-breakdown"))

        page.goto(BASE + "/")
        page.screenshot(path="/tmp/go_home.png", clip={"x": 0, "y": 230, "width": 390, "height": 160})
        page.goto(BASE + "/month")
        for kind in ("savings", "income", "expense"):
            page.evaluate("window.scrollTo(0, 0)"); page.wait_for_timeout(300)
            page.click(f'a.kpi-link[href="#{kind}-breakdown"]')
            print(f"month {kind} chip →", landed(f"{kind}-breakdown"))
        page.evaluate("window.scrollTo(0, 0)"); page.wait_for_timeout(300)
        page.click('a.kpi-link[href="#expense-breakdown"]')
        page.wait_for_timeout(2500)                     # after the flash ends
        print("after the flash ends, opacity:",
              page.evaluate("getComputedStyle(document.getElementById('expense-breakdown')).opacity"))
        page.screenshot(path="/tmp/go_landed.png")
        print("remaining chip is a link:", page.locator("a.kpi-chip", has_text="נשאר").count() > 0)
        page.evaluate("window.scrollTo(0, 0)"); page.wait_for_timeout(300)
        page.screenshot(path="/tmp/go_month.png", clip={"x": 0, "y": 0, "width": 390, "height": 200})
        page.goto(BASE + "/month?year=2025&month=1")
        print("empty month: chips are links:", page.locator("a.kpi-link").count())
        b.close()
finally:
    cleanup = "\n".join([
        "import os, sys", "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "db.get_client().table('transactions').delete().eq('family_id', fid).eq('description', 'GO-TEST').execute()",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
