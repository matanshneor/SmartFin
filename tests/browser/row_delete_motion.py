"""A real browser test: a deleted row leaves gently, and the rows below rise
smoothly (Matan, 7.10: motion round, item 1).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/row_delete_motion.py

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

# taps "מחיקה" in the confirmation, and from that moment measures every frame where
# the row below the deleted one is
WATCH = """([gone, below]) => new Promise(done => {
    const row = document.querySelector('.transaction-item[data-id="' + gone + '"]');
    const next = document.querySelector('.transaction-item[data-id="' + below + '"]');
    // ביחס לרשימה ולא למסך: הדפדפן מזיז את הגלילה כדי לשמור תוכן במקום
    // (scroll anchoring), ואז מדידה ביחס למסך לא רואה שום תזוזה
    const list = row.parentElement;
    const rel = () => next.getBoundingClientRect().top - list.getBoundingClientRect().top;
    const start = rel(), h = row.getBoundingClientRect().height;
    const gap = parseFloat(getComputedStyle(row.parentElement).rowGap) || 0;
    const tops = [];
    let beforeRemoval = null;       // איפה השורה שמתחת עמדה בפריים האחרון שבו הנמחקת עוד הייתה
    let idGoneAt = null;
    document.getElementById('confirmYes').click();
    const t0 = performance.now();
    (function f() {
        tops.push(start - rel());
        if (row.isConnected) beforeRemoval = tops[tops.length - 1];
        if (idGoneAt === null && !document.querySelector('.transaction-item[data-id="' + gone + '"]'))
            idGoneAt = performance.now() - t0;
        if (performance.now() - t0 < 1500) requestAnimationFrame(f);
        else done({ tops, h, gap, stillThere: row.isConnected, idGoneAt, beforeRemoval });
    })();
})"""

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

        def setup(page, tag):
            cats = page.request.get(BASE + "/api/categories").json()
            cat = next(c["id"] for c in cats if c["type"] == "expense")
            ids = []
            for i in range(3):          # today, yesterday, the day before: the order on the home page is known
                r = page.request.fetch(BASE + "/api/transactions", method="POST", headers={"Content-Type": "application/json"},
                    data=json.dumps({"amount": 10 + i, "type": "expense", "category_id": cat, "description": f"{tag}-{i}",
                                     "date": (datetime.date.today() - datetime.timedelta(days=i)).isoformat()}))
                body = r.json(); ids.append((body.get("transaction") or body)["id"])
            page.goto(BASE + "/"); page.wait_for_timeout(1500)
            # by the order on screen: delete the middle one and measure the one below it
            order = page.evaluate("[...document.querySelectorAll('.transaction-item[data-id]')].map(e => e.dataset.id)")
            return [i for i in order if i in ids] if all(i in order for i in ids) else ids

        def delete_middle(page, ids):
            page.locator(f'.transaction-item[data-id="{ids[1]}"] .tx-head').click()
            try:
                page.wait_for_selector("#modalOverlay.open #deleteTxBtn", timeout=5000)
            except Exception:
                page.screenshot(path="/tmp/del_motion_debug.png"); raise
            page.click("#deleteTxBtn")
            page.wait_for_selector("#confirmOverlay.open", timeout=5000)
            page.wait_for_timeout(300)
            # the server's response takes time: wait for the row to start leaving, not before
            page.route("**/api/transactions/*", lambda route: route.continue_())
            return page.evaluate(WATCH, [ids[1], ids[2]])

        page = session(False)
        ids = setup(page, "DEL-MOTION")
        order = page.evaluate("[...document.querySelectorAll('.transaction-item[data-id]')].map(e => e.dataset.id)")
        check("the three rows are on the home page", all(i in order for i in ids))
        r = delete_middle(page, ids)
        tops = r["tops"]
        moved = [t for t in tops if t > 0.5]
        full = r["h"] + r["gap"]
        steps = sorted({round(t) for t in moved if t < full - 1})
        check("the row is gone in the end", not r["stillThere"])
        check("it stops being a transaction right away (gone from the list after %.0fms)" % (r["idGoneAt"] or -1),
              r["idGoneAt"] is not None and r["idGoneAt"] < 1000)
        check("the row below rises smoothly, not in one jump (%d in-between positions)" % len(steps), len(steps) >= 4)
        check("…and ends exactly where the deleted row was, gap included (moved %.1f, want %.1f)" % (tops[-1], full),
              abs(tops[-1] - full) < 1)
        check("…and the list gap closes too, no jump when the row is removed (was at %.1f of %.1f)" % (r["beforeRemoval"], full),
              abs(r["beforeRemoval"] - full) < 1.5)
        check("…without overshooting (max %.1f)" % max(tops), max(tops) <= full + 0.5)
        page.context.close()

        page = session(True)
        ids = setup(page, "DEL-CALM")
        r = delete_middle(page, ids)
        steps = sorted({round(t) for t in r["tops"] if 0.5 < t < r["h"] + r["gap"] - 1})
        check("reduced motion: the row still goes away", not r["stillThere"])
        check("reduced motion: no sliding, rows close up at once (%d in-between positions)" % len(steps), len(steps) == 0)
        b.close()
finally:
    cleanup = "\n".join([
        "import os, sys", "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ['RLS_TEST_EMAIL_A'], os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "t = db.get_client().table('transactions')",
        "t.delete().eq('family_id', fid).like('description', 'DEL-%').execute()",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)

print("\n%d failed" % len(failed))
sys.exit(1 if failed else 0)
