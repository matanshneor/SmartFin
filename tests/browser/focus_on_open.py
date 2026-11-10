"""A real browser test: an opened window gets the focus (Matan, 7.10).

The backdrop's visibility transitioned over 0.25s, and in the first frame it was
still hidden, exactly when the code focused the amount field. The focus stayed on
+, and Escape (caught only inside the window) didn't close it. The confirmation
window too: "ביטול" didn't get the focus.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/focus_on_open.py
"""
import os, sys, time, subprocess, signal
from playwright.sync_api import sync_playwright
import _accounts  # noqa: F401 (temporary test accounts, deleted at the end of the run)

ROOT = os.getcwd()
env = dict(os.environ); env["PORT"] = "8099"
for line in open(".env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); v = v.strip().strip('"').strip("'")
        if v: env.setdefault(k.strip(), v)
srv = subprocess.Popen([os.path.join(ROOT, ".venv/bin/python3"), "-m", "backend.app"], cwd=ROOT, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
BASE = "http://127.0.0.1:8099"
failed = []

def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok: failed.append(name)

try:
    for _ in range(40):
        try:
            import urllib.request; urllib.request.urlopen(BASE + "/health", timeout=1); break
        except Exception: time.sleep(0.5)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 390, "height": 844})
        page.goto(BASE + "/login")
        page.fill("#identifier", env["RLS_TEST_EMAIL_A"])
        page.fill("#password", env["RLS_TEST_PASSWORD_A"])
        page.click("button.submit-btn")
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        page.goto(BASE + "/"); page.wait_for_timeout(1000)
        focused = lambda: page.evaluate("document.activeElement.id || document.activeElement.className")

        page.click("#fabBtn"); page.wait_for_timeout(800)
        check("+ opens the form with focus in the amount (%s)" % focused(), focused() == "txAmount")
        page.keyboard.press("Escape"); page.wait_for_timeout(400)
        check("Escape closes the form", page.locator("#modalOverlay.open").count() == 0)
        check("…and focus goes back to + (%s)" % focused(), focused() == "fabBtn")

        page.evaluate("() => { window.appConfirm({ title: 'בדיקה', message: 'בדיקה', confirmText: 'כן' }); }")
        page.wait_for_timeout(500)
        check("a confirm dialog focuses the safe button (%s)" % focused(), focused() == "confirmNo")
        b.close()
finally:
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)

print("\n%d failed" % len(failed))
sys.exit(1 if failed else 0)
