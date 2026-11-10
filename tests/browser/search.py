"""A real browser test: search across all months from the home page (Matan, 30.9:
round 6, item 2).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/search.py
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

        cat = next(c["id"] for c in page.request.get(BASE + "/api/categories").json() if c["type"] == "expense")
        today = datetime.date.today()
        prev = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=12)
        old = (prev.replace(day=1) - datetime.timedelta(days=40)).replace(day=12)
        for d, amount, desc in ((today, 690, "SR-TEST ביטוח רכב"), (prev, 690, "SR-TEST ביטוח רכב"),
                                (old, 210, "SR-TEST ביטוח בריאות"), (today, 55, "SR-TEST קפה")):
            api("POST", "/api/transactions", {"amount": amount, "type": "expense", "category_id": cat,
                                              "date": d.isoformat(), "description": desc})
        page.goto(BASE + "/"); page.wait_for_timeout(1000)
        page.screenshot(path="/tmp/search_home.png", clip={"x": 0, "y": 0, "width": 390, "height": 160})
        page.click("#searchOpen"); page.wait_for_timeout(300)
        print("open, focused:", page.evaluate("document.activeElement.id"))
        page.fill("#searchInput", "ביטוח"); page.wait_for_timeout(1500)
        print("summary:", page.inner_text("#searchSummary"))
        print("months:", page.locator(".search-month").all_inner_texts())
        print("rows:", [t.replace("\n", " ") for t in page.locator("#searchResults .cat-tx-row").all_inner_texts()])
        page.screenshot(path="/tmp/search.png")
        page.fill("#searchInput", "690"); page.wait_for_timeout(1200)
        print("690:", page.inner_text("#searchSummary"))
        page.fill("#searchInput", "זזזזז"); page.wait_for_timeout(1200)
        print("nothing:", page.inner_text("#searchSummary"))
        page.fill("#searchInput", "קפה"); page.wait_for_timeout(1200)
        page.locator("#searchResults .cat-tx-row").first.click(); page.wait_for_timeout(1200)
        print("edit modal over search:", page.locator(".modal-overlay.open").count(),
              "| search still open:", page.is_visible("#searchScreen"))
        page.fill("#txAmount", "58"); page.click("#submitBtn"); page.wait_for_timeout(2500)
        print("after edit, result updated:", [t.replace("\n", " ") for t in page.locator("#searchResults .cat-tx-row").all_inner_texts()])
        page.keyboard.press("Escape"); page.wait_for_timeout(300)
        print("escape closes search:", not page.is_visible("#searchScreen"))
        page.set_viewport_size({"width": 320, "height": 700}); page.wait_for_timeout(200)
        print("320 sideways:", page.evaluate("document.documentElement.scrollWidth > innerWidth"))
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
        "print('cleaned', len(t('transactions').delete().eq('family_id', fid).like('description', 'SR-TEST%').execute().data))",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
