"""A real browser test: choosing an icon from a grid and a suggestion by name (Matan,
30.9: round 6, item 8).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/icon_picker.py
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
        page = b.new_page(viewport={"width": 390, "height": 844})
        page.goto(BASE + "/login")
        page.fill("#identifier", env.get("RLS_TEST_EMAIL_A", "rls-test-family-a@smartfin.test"))
        page.fill("#password", env["RLS_TEST_PASSWORD_A"])
        page.click("button.submit-btn")
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)

        page.goto(BASE + "/projects/new"); page.wait_for_timeout(1000)
        page.fill("#projectName", "טיול ליפן"); page.wait_for_timeout(100)
        print("suggested for 'טיול ליפן':", page.input_value("#projectIcon"))
        page.click("#projectIcon"); page.wait_for_timeout(300)
        print("picker open:", page.is_visible(".icon-picker"), "| keyboard focus on field:", page.evaluate("document.activeElement.id") == "projectIcon")
        page.screenshot(path="/tmp/icon_picker.png")
        page.locator(".icon-picker-btn", has_text="🏖").click(); page.wait_for_timeout(450)   # the picker goes down in 250ms
        print("picked:", page.input_value("#projectIcon"), "| picker closed:", not page.is_visible(".icon-picker"))
        page.fill("#projectName", "דלק"); page.wait_for_timeout(100)
        print("hand pick not overwritten by suggestion:", page.input_value("#projectIcon"))

        page.goto(BASE + "/settings"); page.wait_for_timeout(900)
        page.locator(".settings-group-header", has_text="קטגוריות").click(); page.wait_for_timeout(300)
        page.click("#manageCatsBtn"); page.wait_for_timeout(300)
        page.fill("#newCatName", "חשבון חשמל"); page.wait_for_timeout(100)
        print("suggested for 'חשבון חשמל':", page.input_value("#newCatIcon"))
        page.click("#newCatIcon"); page.wait_for_timeout(300)
        page.click(".icon-picker-other"); page.wait_for_timeout(200)
        print("other → keyboard on field:", page.evaluate("document.activeElement.id"))
        page.keyboard.type("⚡"); page.wait_for_timeout(100)
        print("typed:", page.input_value("#newCatIcon"))
        b.close()
finally:
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
