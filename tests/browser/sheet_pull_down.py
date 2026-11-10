"""A real browser test: pulling down closes the add window (Matan, 7.10: Apple-style
design, item 1).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/sheet_pull_down.py

Prints PASS/FAIL for each case and exits with code 1 if something failed.
"""
import os, sys, time, subprocess, signal
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
                       start_new_session=True)
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
        ctx = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        page = ctx.new_page()
        page.goto(BASE + "/login")
        page.fill("#identifier", env["RLS_TEST_EMAIL_A"])
        page.fill("#password", env["RLS_TEST_PASSWORD_A"])
        page.click("button.submit-btn")
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        page.wait_for_timeout(800)
        cdp = ctx.new_cdp_session(page)

        def is_open():
            return page.evaluate("document.getElementById('modalOverlay').classList.contains('open')")

        def open_sheet():
            page.click(".fab")
            page.wait_for_timeout(600)
            page.evaluate("document.activeElement.blur()")   # the keyboard doesn't matter here, it only gets in the screenshot's way

        def handle_y():
            return page.evaluate("document.querySelector('.modal-handle').getBoundingClientRect().top") + 6

        def touch(x0, y0, x1, y1, steps, gap_ms, hold_ms=0):
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
            for i in range(1, steps + 1):
                if gap_ms: page.wait_for_timeout(gap_ms)
                cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [
                    {"x": x0 + (x1 - x0) * i / steps, "y": y0 + (y1 - y0) * i / steps}]})
            if hold_ms: page.wait_for_timeout(hold_ms)    # a stop before release: zero speed
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

        # A fast flick is sent from inside the page, a move every 8ms like a real
        # screen. Through CDP every move is delayed ~40ms, and the "flick" comes out
        # slow and sits exactly on the threshold.
        def flick(y0, dist):
            page.evaluate("""([y0, dist]) => new Promise(done => {
                const el = document.elementFromPoint(200, y0);
                const at = y => new Touch({ identifier: 1, target: el, clientX: 200, clientY: y });
                const fire = (type, t) => el.dispatchEvent(new TouchEvent(type, { bubbles: true, cancelable: true,
                    touches: t ? [t] : [], targetTouches: t ? [t] : [], changedTouches: [t || at(y0 + dist)] }));
                fire('touchstart', at(y0));
                let i = 0;
                const tick = () => {
                    i++;
                    if (i <= 5) { fire('touchmove', at(y0 + dist * i / 5)); setTimeout(tick, 8); }
                    else { fire('touchend', null); done(); }
                };
                setTimeout(tick, 8);
            })""", [y0, dist])

        # 0. When opened by tap the window rises and stops: doesn't overshoot and
        # come back
        page.evaluate("document.getElementById('fabBtn').click()")
        tops = page.evaluate("""() => new Promise(done => {
            const sh = document.querySelector('.modal-sheet'), out = [], t0 = performance.now();
            (function f() { out.push(sh.getBoundingClientRect().top);
                if (performance.now() - t0 < 700) requestAnimationFrame(f); else done(out); })();
        })""")
        final = tops[-1]
        check("opening by tap doesn't overshoot (highest %.1f px above the final spot)" % (final - min(tops)),
              min(tops) >= final - 0.5)
        page.click(".modal-close"); page.wait_for_timeout(500)

        # 1. A short, slow drag, stop, release: returns to place
        open_sheet()
        hy = handle_y()
        touch(200, hy, 200, hy + 90, 12, 30, hold_ms=200)
        page.wait_for_timeout(900)
        check("short slow drag keeps the sheet open", is_open())
        check("…and the sheet is back in place",
              page.evaluate("document.querySelector('.modal-sheet').style.transform") == "")

        # 2. The window moves with the finger in the middle of the drag
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": 200, "y": hy}]})
        for i in range(1, 8):
            page.wait_for_timeout(16)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": 200, "y": hy + i * 20}]})
        moved = page.evaluate("new DOMMatrix(getComputedStyle(document.querySelector('.modal-sheet')).transform).m42")
        page.screenshot(path="/tmp/sheet_mid_drag.png")
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": 200, "y": hy}]})
        page.wait_for_timeout(200)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_timeout(900)
        check("sheet follows the finger 1:1 (~140px down: got %.0f)" % moved, 125 <= moved <= 145)
        check("dragging back up and letting go keeps it open", is_open())

        # 3. A short, fast flick: closes, although it is shorter than case 1's drag
        flick(hy, 70)
        page.wait_for_timeout(900)
        check("short fast flick closes the sheet", not is_open())

        # 4. Opens again clean, in place
        open_sheet()
        page.wait_for_timeout(300)
        top = page.evaluate("document.querySelector('.modal-sheet').getBoundingClientRect().bottom")
        check("reopens in place after a pull-close (bottom %.0f)" % top, abs(top - 844) < 2)

        # 4b. Opens right after closing by pull: before the close's cleanup had time
        # to run
        page.click(".modal-close"); page.wait_for_timeout(500)
        open_sheet()
        flick(hy, 70)
        for _ in range(150):                              # wait_for_function is blocked by the site's CSP
            if not is_open(): break
            page.wait_for_timeout(20)
        page.evaluate("document.getElementById('fabBtn').click()")
        check("reopening right after a pull-close starts clean",
              page.evaluate("document.querySelector('.modal-sheet').style.transform") == "")
        page.wait_for_timeout(600)

        # 5. A long, slow drag: closes
        touch(200, hy, 200, hy + 330, 25, 30, hold_ms=150)
        page.wait_for_timeout(900)
        check("long slow drag closes the sheet", not is_open())

        # 6. Pulling up: doesn't close, and no jump
        open_sheet()
        touch(200, hy + 40, 200, hy - 150, 10, 20)
        page.wait_for_timeout(900)
        check("pulling up keeps it open", is_open())

        # 7. Sideways movement: isn't dragged
        touch(330, hy + 120, 60, hy + 140, 10, 16)
        page.wait_for_timeout(600)
        check("sideways swipe keeps it open", is_open())

        # 8. When the content is scrolled inside: pulling down scrolls, doesn't close
        # (a short screen, so there is something to scroll)
        page.click(".modal-close"); page.wait_for_timeout(500)
        page.set_viewport_size({"width": 390, "height": 560})
        open_sheet()
        scrolled = page.evaluate("(() => { const s = document.querySelector('.modal-sheet'); s.scrollTop = 200; return s.scrollTop; })()")
        check("short screen: the sheet scrolls inside (scrollTop %d)" % scrolled, scrolled > 0)
        touch(200, 300, 200, 520, 6, 10)
        page.wait_for_timeout(900)
        check("pull while scrolled inside scrolls instead of closing", is_open())

        # 9. The ✕ still closes
        page.click(".modal-close")
        page.wait_for_timeout(500)
        check("the close button still works", not is_open())
        b.close()
finally:
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)

print("\n%d failed" % len(failed))
sys.exit(1 if failed else 0)
