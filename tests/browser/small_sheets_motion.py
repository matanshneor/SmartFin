"""A real browser test: the small windows rise from the bottom and go down the same
path (Matan, 7.10: motion round, item 2).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/small_sheets_motion.py

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

# taps trigger, and from that moment measures the card every frame: how far below its
# place it is (translateY) and the backdrop's opacity. sheet: the window's selector.
TRACK = """([trigger, sheet, card, ms]) => new Promise(done => {
    const out = [];
    const t = document.querySelector(trigger);
    // בורר האייקונים נפתח ב-‎pointerdown‎ (כדי שהמקלדת לא תקפוץ), השאר ב-‎click‎
    if (t) { t.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true })); t.click(); }
    const t0 = performance.now();
    (function f() {
        const s = document.querySelector(sheet), c = s && s.querySelector(card);
        const shown = !!s && getComputedStyle(s).display !== 'none';
        out.push({ shown, y: c && shown ? new DOMMatrix(getComputedStyle(c).transform).m42 : null,
                   o: shown ? parseFloat(getComputedStyle(s).opacity) : null,
                   hidden: !!s && s.hidden });
        if (performance.now() - t0 < ms) requestAnimationFrame(f); else done(out);
    })();
})"""

def verdict(name, frames, opening):
    ys = [f["y"] for f in frames if f["y"] is not None]
    mid = [y for y in ys if 1 < y]
    if opening:
        check(f"{name}: opens by rising from below ({len(mid)} frames on the way)", len(mid) >= 3)
        check(f"{name}: …and settles in place (last y {ys[-1] if ys else None})", bool(ys) and abs(ys[-1]) < 0.5)
        check(f"{name}: …without overshooting (min y {min(ys) if ys else float('nan'):.1f})", bool(ys) and min(ys) > -0.5)
        check(f"{name}: …and the backdrop fades in (first opacity {frames[0]['o']})",
              frames[0]["o"] is not None and frames[0]["o"] < 0.9)
    else:
        check(f"{name}: closes by sliding back down ({len(mid)} frames on the way)", len(mid) >= 3)
        check(f"{name}: …and is really gone in the end", not frames[-1]["shown"])
        downs = [b - a for a, b in zip(ys, ys[1:])]
        check(f"{name}: …moving only downward", all(d >= -0.5 for d in downs))

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

        # budgets: the month page. The button appears only when the month has expenses
        cats = page.request.get(BASE + "/api/categories").json()
        page.request.fetch(BASE + "/api/transactions", method="POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"amount": 120, "type": "expense", "description": "SHEET-MOTION",
                             "category_id": next(c["id"] for c in cats if c["type"] == "expense"),
                             "date": datetime.date.today().isoformat()}))
        page.goto(BASE + "/month"); page.wait_for_timeout(1500)
        check("the budgets button is there", page.locator("#budgetsOpen").count() == 1)
        verdict("budgets", page.evaluate(TRACK, ["#budgetsOpen", ".budget-sheet", ".color-sheet-card", 700]), True)
        page.wait_for_timeout(200)
        page.keyboard.press("Escape")
        frames = page.evaluate(TRACK, ["#nothing", ".budget-sheet", ".color-sheet-card", 600])
        verdict("budgets", frames, False)
        # while leaving, the window doesn't block taps on the page
        page.evaluate("document.getElementById('budgetsOpen').click()"); page.wait_for_timeout(500)
        page.keyboard.press("Escape"); page.wait_for_timeout(60)
        blocks = page.evaluate("""(() => { const s = document.querySelector('.budget-sheet');
            return getComputedStyle(s).display !== 'none' && getComputedStyle(s).pointerEvents !== 'none'; })()""")
        check("budgets: while sliding out it doesn't block taps", not blocks)
        page.wait_for_timeout(500)

        # the icon picker: a new project
        page.goto(BASE + "/projects/new"); page.wait_for_timeout(1200)
        verdict("icon picker", page.evaluate(TRACK, ["#projectIcon", ".icon-picker", ".icon-picker-card", 700]), True)
        page.wait_for_timeout(200)
        frames = page.evaluate(TRACK, [".icon-picker-btn", ".icon-picker", ".icon-picker-card", 600])
        verdict("icon picker", frames, False)
        b.close()
finally:
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)

print("\n%d failed" % len(failed))
sys.exit(1 if failed else 0)
