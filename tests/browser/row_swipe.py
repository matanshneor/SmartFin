"""A real browser test: swiping a transaction row by finger speed (Matan, 7.10:
Apple-style design, item 2).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/row_swipe.py

Prints PASS/FAIL for each case and exits with code 1 if something failed.
"""
import os, sys, json, time, datetime, subprocess, signal
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

# A whole gesture from inside the page: a move every gap ms (8: a real screen;
# through CDP every move is delayed ~40ms). xs: horizontal offsets from the start
# point; ys: vertical ones, if any. Returns the row's position just before release,
# as it is really painted.
GESTURE = """([sel, xs, ys, gap, hold]) => new Promise(done => {
    const row = document.querySelector(sel);
    const r = row.getBoundingClientRect();
    const x0 = r.left + r.width / 2, y0 = r.top + r.height / 2;
    const el = document.elementFromPoint(x0, y0);
    const at = (dx, dy) => new Touch({ identifier: 1, target: el, clientX: x0 + dx, clientY: y0 + dy });
    let last = at(0, 0);
    const fire = (type, t) => el.dispatchEvent(new TouchEvent(type, { bubbles: true, cancelable: true,
        touches: t ? [t] : [], targetTouches: t ? [t] : [], changedTouches: [t || last] }));
    fire('touchstart', last);
    let i = 0;
    const tick = () => {
        if (i < xs.length) { last = at(xs[i], ys ? ys[i] : 0); fire('touchmove', last); i++; setTimeout(tick, gap); return; }
        setTimeout(() => {
            const c = row.querySelector('.swipe-content');
            const mid = c ? new DOMMatrix(getComputedStyle(c).transform).m41 : 0;
            fire('touchend', null);
            done(mid);
        }, hold);
    };
    setTimeout(tick, gap);
})"""

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
        cats = page.request.get(BASE + "/api/categories").json()
        cat = next(c["id"] for c in cats if c["type"] == "expense")
        page.request.fetch(BASE + "/api/transactions", method="POST", headers={"Content-Type": "application/json"},
                           data=json.dumps({"amount": 42, "type": "expense", "category_id": cat,
                                            "date": datetime.date.today().isoformat(), "description": "SWIPE-TEST"}))
        page.goto(BASE + "/")
        page.wait_for_timeout(1200)
        SEL = ".transaction-item[data-id]"
        check("there is a row to swipe", page.locator(SEL).count() > 0)

        def gesture(xs, gap=8, hold=0, ys=None):
            return page.evaluate(GESTURE, [SEL, xs, ys, gap, hold])

        def state():
            return page.evaluate("""(sel) => { const r = document.querySelector(sel), c = r.querySelector('.swipe-content');
                return { open: r.classList.contains('swipe-open'),
                         x: c ? new DOMMatrix(getComputedStyle(c).transform).m41 : 0 }; }""", SEL)

        def reset():
            page.evaluate("""(sel) => { const r = document.querySelector(sel), c = r.querySelector('.swipe-content');
                r.classList.remove('swipe-open');
                // בלי מעבר רק לרגע — ואז ה-CSS חוזר, כדי שמקרה 4 יראה השהיה אם יש כזו
                if (c) { c.style.transition = 'none'; c.style.transform = ''; void c.offsetWidth; c.style.transition = ''; } }""", SEL)
            page.wait_for_timeout(100)

        # 1. A short, fast flick left (36px): opens, and stops at the button's width
        gesture([-12, -24, -36])
        page.wait_for_timeout(900)
        s = state()
        check("short fast flick opens the row (open=%s, x=%.0f)" % (s["open"], s["x"]), s["open"] and abs(s["x"] + 76) < 2)
        reset()

        # 2. The same distance, slowly and with a stop: stays closed (the decision is
        # by speed, not only distance)
        gesture([-6, -12, -18, -24, -30, -36], gap=60, hold=200)
        page.wait_for_timeout(900)
        s = state()
        check("same distance, slow and stopped, stays closed (open=%s, x=%.0f)" % (s["open"], s["x"]),
              not s["open"] and abs(s["x"]) < 1)
        reset()

        # 3. A long, slow drag right with a stop: opens (distance alone still works)
        gesture([10, 20, 30, 40, 50, 60, 70], gap=40, hold=200)
        page.wait_for_timeout(900)
        s = state()
        check("long slow drag right opens (open=%s, x=%.0f)" % (s["open"], s["x"]), s["open"] and abs(s["x"] - 76) < 2)
        reset()

        # 4. The row moves with the finger one to one, with no delay (50px → 42,
        # after 8 for the decision)
        mid = gesture([-10, -20, -30, -40, -50], gap=16, hold=0)
        check("row follows the finger with no lag (got %.0f, want -42)" % mid, abs(mid + 42) < 2)
        page.wait_for_timeout(900)
        reset()

        # 5. Past the button's width: gradual resistance, not a wall
        mid = gesture([-40, -80, -120, -160, -200], gap=16, hold=0)
        # without resistance: -192; the old wall: -100
        check("past the button it resists, no hard stop (got %.0f)" % mid, -160 < mid < -110)
        page.wait_for_timeout(900)
        reset()

        # 6. A flick back after a long drag: closes (the direction it is heading)
        gesture([-15, -30, -45, -60, -70, -70, -70, -60, -45, -30], gap=12)
        page.wait_for_timeout(900)
        s = state()
        check("flick back after a long drag closes (open=%s, x=%.0f)" % (s["open"], s["x"]), not s["open"] and abs(s["x"]) < 1)
        reset()

        # 6b. A strong change of mind: left, then a fast flick right: closes, "עריכה"
        # doesn't open from the other side
        gesture([-15, -30, -45, -60, -70, -70, -50, -30, -15], gap=8)
        page.wait_for_timeout(900)
        s = state()
        check("drag left then hard flick right doesn't open the other side (open=%s, x=%.0f)" % (s["open"], s["x"]),
              not s["open"] and abs(s["x"]) < 1)
        reset()

        # 7. Vertical scrolling: the row doesn't move
        gesture([2, 4, 6, 8], ys=[-20, -40, -60, -80])
        page.wait_for_timeout(600)
        s = state()
        check("vertical scroll doesn't swipe (x=%.0f)" % s["x"], not s["open"] and abs(s["x"]) < 1)

        # 8. Touching an open row closes it (as before)
        gesture([-12, -24, -36])
        page.wait_for_timeout(900)
        gesture([], hold=30)
        page.wait_for_timeout(600)
        s = state()
        check("a tap on an open row closes it (open=%s, x=%.0f)" % (s["open"], s["x"]), not s["open"] and abs(s["x"]) < 1)
        b.close()
finally:
    cleanup = "\n".join([
        "import os, sys", "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ['RLS_TEST_EMAIL_A'], os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "db.get_client().table('transactions').delete().eq('family_id', fid).eq('description', 'SWIPE-TEST').execute()",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)

print("\n%d failed" % len(failed))
sys.exit(1 if failed else 0)
