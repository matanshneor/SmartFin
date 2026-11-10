"""A real browser test: the date row in the add window fits the screen (Matan,
7.10).

On a 320-wide screen, and with "very large" text also at 390, "תאריך אחר" went off
screen by about 40px. Now it wraps to the next line. And while at it: the recurring
transaction fields at 16px (no zoom on iPhone), one below the other, and fully
visible.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/date_row_fits.py
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

# what inside the window sticks out past its edges
OUT = """() => { const s = document.querySelector('.modal-sheet'), r = s.getBoundingClientRect();
    return [...s.querySelectorAll('*')].filter(e => { const b = e.getBoundingClientRect();
        return b.width > 0 && (b.left < r.left - 1 || b.right > r.right + 1); }).map(e => e.id || e.className).slice(0, 4); }"""
# a select field's scrollWidth is all its text; if it is wider than the field, the
# text is cut
CLIPPED = """id => { const e = document.getElementById(id); return e.scrollWidth > e.clientWidth + 1; }"""

try:
    for _ in range(40):
        try:
            import urllib.request; urllib.request.urlopen(BASE + "/health", timeout=1); break
        except Exception: time.sleep(0.5)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for width, size in ((320, None), (390, "xlarge"), (390, None)):
            ctx = b.new_context(viewport={"width": width, "height": 760}, has_touch=True, is_mobile=True)
            if size:
                ctx.add_init_script(f"try {{ localStorage.setItem('sf_text_size', '{size}'); }} catch (e) {{}}")
            page = ctx.new_page()
            page.goto(BASE + "/login")
            page.fill("#identifier", env["RLS_TEST_EMAIL_A"])
            page.fill("#password", env["RLS_TEST_PASSWORD_A"])
            page.click("button.submit-btn")
            page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
            page.goto(BASE + "/"); page.wait_for_timeout(800)
            page.click("#fabBtn"); page.wait_for_timeout(800)
            label = f"{width}px{' + ' + size if size else ''}"
            check(f"{label}: nothing sticks out of the add form {page.evaluate(OUT)}", not page.evaluate(OUT))
            page.locator(".checkbox-label").first.click(); page.wait_for_timeout(300)
            fs = page.evaluate("[parseFloat(getComputedStyle(txFrequency).fontSize), parseFloat(getComputedStyle(txEndDate).fontSize)]")
            check(f"{label}: recurring fields are 16px or more (no iPhone zoom) {fs}", min(fs) >= 16)
            check(f"{label}: …and the frequency text isn't cut", not page.evaluate(CLIPPED, "txFrequency"))
            check(f"{label}: …and still nothing sticks out {page.evaluate(OUT)}", not page.evaluate(OUT))
            ctx.close()
        b.close()
finally:
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)

print("\n%d failed" % len(failed))
sys.exit(1 if failed else 0)
