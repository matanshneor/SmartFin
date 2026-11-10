"""A real browser test: a loading skeleton, only on a slow network (Matan, 30.9:
round 6, item 14).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/skeleton.py
"""
import os, time, subprocess, signal
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
        # service_workers="block": the browser's network throttling doesn't apply to
        # requests the SW makes, so without this navigation is "fast" and the test
        # tests nothing
        page = b.new_context(viewport={"width": 390, "height": 844}, service_workers="block").new_page()
        page.goto(BASE + "/login")
        page.fill("#identifier", env.get("RLS_TEST_EMAIL_A", "rls-test-family-a@smartfin.test"))
        page.fill("#password", env["RLS_TEST_PASSWORD_A"])
        page.click("button.submit-btn")
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        page.wait_for_timeout(800)

        # fast network: the skeleton doesn't appear at all
        page.evaluate("""() => { window.__sk = 0; new MutationObserver(m => m.forEach(r => r.addedNodes.forEach(n => {
            if (n.classList && n.classList.contains('skeleton-screen')) window.__sk++; }))).observe(document.body, {childList: true}); }""")
        seen = []
        page.on("framenavigated", lambda f: None)
        page.locator(".bottom-nav a").nth(1).click()
        page.wait_for_load_state("load"); page.wait_for_timeout(300)
        print("fast network — landed:", page.url.replace(BASE, ""), "| skeleton on new page:", page.locator(".skeleton-screen").count())

        # slow network: a 1.5-second delay on every page load
        # "slow network": the skeleton already got the tap, and then navigation is
        # stopped: that is, the next page "doesn't arrive". (The browser's network
        # throttling doesn't apply to a local server, and holding the request in
        # Playwright stalls the test itself.)
        page.evaluate("""() => document.addEventListener('click', e => {
            if (e.target.closest('.bottom-nav a')) e.preventDefault(); }, { once: true })""")
        page.locator(".bottom-nav a").nth(2).click()
        page.wait_for_timeout(100)
        print("after 0.1s — not yet:", page.locator(".skeleton-screen").count() == 0)
        page.wait_for_timeout(400)
        print("slow network — skeleton showing:", page.locator(".skeleton-screen").count())
        page.screenshot(path="/tmp/skeleton.png")
        page.goto(BASE + "/months"); page.wait_for_timeout(1000)
        print("then the page arrives — skeleton gone:", page.locator(".skeleton-screen").count() == 0)
        page.go_back(); page.wait_for_timeout(1200)
        print("back button — no leftover skeleton:", page.locator(".skeleton-screen:visible").count() == 0)
        b.close()
finally:
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
