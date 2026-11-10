"""A real browser test: category breakdown and groups in settings open with a slide
and not a jump (Matan, 7.10: motion round, item 5).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/reveal_motion.py

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

# taps (clicks: a list of [selector, after how many ms]) and measures body's height every frame
TRACK = """([clicks, body, ms]) => new Promise(done => {
    const out = [], t0 = performance.now();
    const pending = clicks.slice();
    (function f() {
        const now = performance.now() - t0;
        while (pending.length && pending[0][1] <= now) document.querySelector(pending.shift()[0]).click();
        const el = document.querySelector(body), cs = getComputedStyle(el);
        out.push({ t: now, h: cs.display === 'none' ? 0 : el.getBoundingClientRect().height });
        if (now < ms) requestAnimationFrame(f); else done(out);
    })();
})"""

def grows(name, frames, full=None):
    hs = [f["h"] for f in frames]
    end = hs[-1]
    mid = [h for h in hs if 2 < h < end - 2]
    check(f"{name}: opens by growing ({len(mid)} frames on the way, ends at {end:.0f}px)", len(mid) >= 3 and end > 20)
    check(f"{name}: …never taller than where it ends", max(hs) <= end + 0.5)
    return end

def shrinks(name, frames, full):
    hs = [f["h"] for f in frames]
    mid = [h for h in hs if 2 < h < full - 2]
    check(f"{name}: closes by shrinking ({len(mid)} frames on the way)", len(mid) >= 3)
    check(f"{name}: …and is really closed in the end", hs[-1] == 0)

try:
    for _ in range(40):
        try:
            import urllib.request; urllib.request.urlopen(BASE + "/health", timeout=1); break
        except Exception: time.sleep(0.5)
    with sync_playwright() as p:
        b = p.chromium.launch()

        def session(reduce):
            ctx = b.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce" if reduce else "no-preference")
            page = ctx.new_page()
            page.goto(BASE + "/login")
            page.fill("#identifier", env["RLS_TEST_EMAIL_A"])
            page.fill("#password", env["RLS_TEST_PASSWORD_A"])
            page.click("button.submit-btn")
            page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
            return page

        page = session(False)
        cat = next(c["id"] for c in page.request.get(BASE + "/api/categories").json() if c["type"] == "expense")
        for i in range(3):
            page.request.fetch(BASE + "/api/transactions", method="POST", headers={"Content-Type": "application/json"},
                data=json.dumps({"amount": 30 + i, "type": "expense", "category_id": cat, "description": "REVEAL",
                                 "date": datetime.date.today().isoformat()}))
        page.goto(BASE + "/month"); page.wait_for_timeout(1500)
        page.evaluate("""(() => { const t = document.querySelector('.legend-item.clickable[aria-expanded]');
            t.id = 'revealTrigger'; t.closest('.legend-item-wrap').querySelector('.cat-tx-list').id = 'revealList';
            t.scrollIntoView({ block: 'center' }); })()""")
        page.wait_for_timeout(300)
        full = grows("category details", page.evaluate(TRACK, [[["#revealTrigger", 0]], "#revealList", 500]))
        check("category details: aria-expanded is true", page.get_attribute("#revealTrigger", "aria-expanded") == "true")
        shrinks("category details", page.evaluate(TRACK, [[["#revealTrigger", 0]], "#revealList", 450]), full)
        check("category details: aria-expanded is false right away",
              page.get_attribute("#revealTrigger", "aria-expanded") == "false")
        # a tap in the middle of closing: reopens from the height on screen, without
        # jumping to 0
        frames = page.evaluate(TRACK, [[["#revealTrigger", 0], ["#revealTrigger", 600], ["#revealTrigger", 680]], "#revealList", 1200])
        after = [f["h"] for f in frames if f["t"] >= 680]
        # from the second tap: doesn't drop to 0, and only rises to the full height
        rises = all(b2 >= a2 - 0.5 for a2, b2 in zip(after, after[1:]))
        check("tap again while closing: reopens from where it was (starts at %.0f, only grows, ends %.0f)"
              % (after[0], after[-1]), after[0] > 2 and rises and after[-1] > full - 1)

        page.goto(BASE + "/settings"); page.wait_for_timeout(1200)
        page.evaluate("""(() => { const h = [...document.querySelectorAll('.settings-group-header')].find(x => x.getAttribute('aria-expanded') !== 'true');
            h.id = 'revealGroup'; h.closest('.settings-group').querySelector('.settings-group-body').id = 'revealBody'; })()""")
        grows("settings group", page.evaluate(TRACK, [[["#revealGroup", 0]], "#revealBody", 500]))
        page.context.close()

        page = session(True)
        page.goto(BASE + "/month"); page.wait_for_timeout(1500)
        page.evaluate("""(() => { const t = document.querySelector('.legend-item.clickable[aria-expanded]');
            t.id = 'revealTrigger'; t.closest('.legend-item-wrap').querySelector('.cat-tx-list').id = 'revealList'; })()""")
        hs = [f["h"] for f in page.evaluate(TRACK, [[["#revealTrigger", 0]], "#revealList", 300])]
        check("reduced motion: opens at once (%d in-between frames)" % len([h for h in hs if 2 < h < hs[-1] - 2]),
              not [h for h in hs if 2 < h < hs[-1] - 2] and hs[-1] > 20)
        b.close()
finally:
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)

print("\n%d failed" % len(failed))
sys.exit(1 if failed else 0)
