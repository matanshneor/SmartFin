"""A real browser test: the three fixes from the review (Matan, 1.10): closing in the
middle of "בודק…", and pull-to-refresh that isn't caught inside an open window.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/review_fixes.py
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
        ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        ctx.add_init_script("Object.defineProperty(navigator, 'standalone', {get: () => true})")
        page = ctx.new_page()
        page.goto(BASE + "/login")
        page.fill("#identifier", env.get("RLS_TEST_EMAIL_A", "rls-test-family-a@smartfin.test"))
        page.fill("#password", env["RLS_TEST_PASSWORD_A"])
        page.click("button.submit-btn")
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        page.wait_for_timeout(800)
        cat = next(c["id"] for c in page.request.get(BASE + "/api/categories").json() if c["type"] == "expense")
        today = datetime.date.today()

        # ── 1. Closing the form in the middle of "בודק…" ──
        held = []
        page.route("**/api/transactions/precheck", lambda route: held.append(route))
        page.click("#fabBtn"); page.wait_for_timeout(800)
        page.fill("#txAmount", "61"); page.fill("#txDescription", "FX-TEST נסגר")
        page.click("#submitBtn"); page.wait_for_timeout(300)
        print("button while checking:", page.inner_text("#submitBtn").strip())
        page.keyboard.press("Escape"); page.wait_for_timeout(300)
        print("form closed:", page.locator(".modal-overlay.open").count() == 0)
        for r in held: r.continue_()
        page.unroute("**/api/transactions/precheck")
        page.wait_for_timeout(2500)
        saved = page.request.get(BASE + "/api/search?q=FX-TEST").json()["count"]
        print("closed during check — saved?", saved)

        # ── 2. Dragging down inside the search results ──
        for i in range(25):
            page.request.fetch(BASE + "/api/transactions", method="POST", headers={"Content-Type": "application/json"},
                               data=json.dumps({"amount": 10 + i, "type": "expense", "category_id": cat,
                                                "date": (today - datetime.timedelta(days=i)).isoformat(),
                                                "description": f"FX-TEST שורה {i}"}))
        page.goto(BASE + "/"); page.wait_for_timeout(1000)
        reloads = []
        page.on("request", lambda r: reloads.append(r.url) if r.headers.get("x-requested-with") == "sf-soft-reload" else None)
        page.click("#searchOpen"); page.fill("#searchInput", "FX-TEST"); page.wait_for_timeout(1500)
        page.evaluate("document.getElementById('searchResults').scrollTop = 600"); page.wait_for_timeout(200)
        cdp = ctx.new_cdp_session(page)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": 200, "y": 300}]})
        for i in range(1, 13):
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": 200, "y": 300 + 20 * i}]})
            page.wait_for_timeout(16)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_timeout(1500)
        print("drag down inside search — page refreshed?", len(reloads) > 0,
              "| pull arrow shown?", page.evaluate("getComputedStyle(document.querySelector('.ptr')).opacity") != "0",
              "| list scrolled up:", page.evaluate("document.getElementById('searchResults').scrollTop") < 600)
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
        "print('cleaned', len(t('transactions').delete().eq('family_id', fid).like('description', 'FX-TEST%').execute().data))",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
