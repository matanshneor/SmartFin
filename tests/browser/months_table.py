"""A real browser test: the comparison page as a table: no amount is cut at any
phone width, and tapping a row opens the month.

Uses the numbers from Matan's screenshot (July–October 2026) and cleans up after
itself.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/months_table.py
"""
import os, json, time, subprocess, signal
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
        data = {"2026-07": (3865, 10174.30, 10000), "2026-08": (8195, 3665.80, 8000),
                "2026-09": (11527, 4459.80, 8000), "2026-10": (0, 800, 0),
                "2025-12": (5000, 1200, 0), "2025-11": (5000, 900, 0), "2025-10": (5000, 700, 0),
                "2024-05": (4000, 500, 0)}
        for mm, (inc, exp, sav) in data.items():
            for amount, kind in ((inc, "income"), (exp, "expense"), (sav, "savings")):
                if amount:
                    api("POST", "/api/transactions", {"amount": amount, "type": kind, "date": f"{mm}-05",
                                                      "description": "TABLE-TEST", "category_id": by_type[kind]})
        OVER = """() => {
            const W = document.documentElement.clientWidth, out = [];
            if (document.documentElement.scrollWidth > W + 1) out.push('page scrolls sideways');
            for (const td of document.querySelectorAll('.months-table td')) {
                if (td.scrollWidth > td.clientWidth + 1) out.push('clipped: ' + td.textContent.trim());
            }
            return out;
        }"""
        for width in (320, 360, 375, 390, 430):
            page.set_viewport_size({"width": width, "height": 800})
            page.goto(BASE + "/months"); page.wait_for_timeout(900)
            print(width, page.evaluate(OVER) or "ok")
        page.set_viewport_size({"width": 375, "height": 800})
        page.goto(BASE + "/months"); page.wait_for_timeout(1200)
        page.locator(".chart-card").first.screenshot(path="/tmp/compare_chart.png")
        print("x reversed:", page.evaluate("Chart.getChart(document.getElementById('compareChart')).options.scales.x.reverse"),
              "| y axis side:", page.evaluate("Chart.getChart(document.getElementById('compareChart')).options.scales.y.position"))
        page.locator("#monthsArchive").screenshot(path="/tmp/months_table.png")
        print("header vs numbers (left edges):", page.evaluate("""() => {
            const th = document.querySelectorAll('.months-table thead th')[1], td = document.querySelector('.months-table td.inc');
            const r = el => { const g = document.createRange(); g.selectNodeContents(el); const b = g.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.right)]; };
            return {header: r(th), number: r(td), align: getComputedStyle(th).textAlign};
        }"""))
        years = """() => [...document.querySelectorAll('.year-block')].map(y => y.querySelector('.year-title').textContent.trim()
            + ':' + (y.open ? [...y.querySelectorAll('tr.month-row')].filter(r => r.offsetParent !== null).length + ' months shown' : 'closed'))"""
        print("on arrival:", page.evaluate(years))
        page.locator(".year-title", has_text="2025").click(); page.wait_for_timeout(300)
        print("after tapping 2025:", page.evaluate(years))
        page.screenshot(path="/tmp/months_years.png", full_page=True)
        page.locator("tr.month-row").nth(1).locator("td.exp").click()
        page.wait_for_url("**/month?*", timeout=5000)
        print("tapping a row opens:", page.url.split("/")[-1])
        b.close()
finally:
    cleanup = "\n".join([
        "import os, sys", "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "print('cleaned', len(db.get_client().table('transactions').delete().eq('family_id', fid).eq('description', 'TABLE-TEST').execute().data))",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
