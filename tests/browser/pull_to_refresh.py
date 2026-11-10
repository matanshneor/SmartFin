"""A real browser test: pull down to refresh in the installed app (Matan, 30.9: idea
14).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/pull_to_refresh.py
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

        def phone(installed):
            ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
            if installed:
                ctx.add_init_script("Object.defineProperty(navigator, 'standalone', {get: () => true})")
            page = ctx.new_page()
            page.goto(BASE + "/login")
            page.fill("#identifier", env.get("RLS_TEST_EMAIL_A", "rls-test-family-a@smartfin.test"))
            page.fill("#password", env["RLS_TEST_PASSWORD_A"])
            page.click("button.submit-btn")
            page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
            page.wait_for_timeout(800)
            return page

        def drag(page, cdp, x0, y0, x1, y1, shot=None):
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
            for i in range(1, 13):
                cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [
                    {"x": x0 + (x1 - x0) * i / 12, "y": y0 + (y1 - y0) * i / 12}]})
                page.wait_for_timeout(16)
            if shot: page.screenshot(path=shot)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

        plain = phone(False)
        print("regular browser — no indicator:", plain.locator(".ptr").count() == 0)
        plain.context.close()

        page = phone(True)
        cdp = page.context.new_cdp_session(page)
        print("installed — indicator:", page.locator(".ptr").count())
        page.evaluate("window.__stay = 1")
        cats = page.request.get(BASE + "/api/categories").json()
        cat = next(c["id"] for c in cats if c["type"] == "expense")
        page.request.fetch(BASE + "/api/transactions", method="POST", headers={"Content-Type": "application/json"},
                           data=json.dumps({"amount": 42, "type": "expense", "category_id": cat,
                                            "date": datetime.date.today().isoformat(), "description": "PTR-TEST"}))
        print("before pull, new tx on screen:", page.locator("text=PTR-TEST").count())

        drag(page, cdp, 200, 300, 330, 320)          # sideways: doesn't refresh
        page.wait_for_timeout(800)
        print("sideways drag refreshed?", page.locator("text=PTR-TEST").count() > 0)

        drag(page, cdp, 200, 250, 200, 330)          # a short pull: doesn't refresh
        page.wait_for_timeout(800)
        print("short pull refreshed?", page.locator("text=PTR-TEST").count() > 0)

        drag(page, cdp, 200, 250, 200, 480, shot="/tmp/ptr_pull.png")
        page.wait_for_timeout(2500)
        print("long pull refreshed:", page.locator("text=PTR-TEST").count() > 0,
              "| soft (no page load):", page.evaluate("window.__stay === 1"),
              "| indicator hidden again:", page.evaluate("getComputedStyle(document.querySelector('.ptr')).opacity"))

        reloads = []
        page.on("request", lambda r: reloads.append(r.url) if r.headers.get("x-requested-with") == "sf-soft-reload" else None)
        page.goto(BASE + "/month"); page.wait_for_timeout(1500)
        page.evaluate("window.scrollTo(0, 600)"); page.wait_for_timeout(300)
        print("month page scrolled to:", page.evaluate("scrollY"))
        drag(page, cdp, 200, 250, 200, 480)
        page.wait_for_timeout(1200)
        print("scrolled down — pull refreshed?", len(reloads) > 0)
        page.evaluate("window.scrollTo(0, 0)"); page.wait_for_timeout(300)
        drag(page, cdp, 200, 250, 200, 480)
        page.wait_for_timeout(2000)
        print("at the top of the month page — refreshed:", len(reloads))
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
        "print('cleaned', len(t('transactions').delete().eq('family_id', fid).eq('description', 'PTR-TEST').execute().data))",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
