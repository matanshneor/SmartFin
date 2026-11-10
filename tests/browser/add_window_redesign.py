"""A real browser test: the add window and summary card in the new design (Matan,
11.10).

A large amount in the centre in the class colour, categories with the full name,
the details in one block, a dark button, receipt scanning in the header row, and
everything on one screen when there is no recurring transaction. Saves screenshots
to /tmp/sf_redesign_*.png.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/add_window_redesign.py
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

OUT = """() => { const s = document.querySelector('.modal-sheet'), r = s.getBoundingClientRect();
    return [...s.querySelectorAll('*')].filter(e => { const b = e.getBoundingClientRect();
        return b.width > 0 && (b.left < r.left - 1 || b.right > r.right + 1); }).map(e => e.id || e.className).slice(0, 4); }"""
# a category name that doesn't fit: wider than its button, or cut off
CUT_NAMES = """() => [...document.querySelectorAll('#categoryGrid .cat-btn')].filter(b => {
    const n = b.lastElementChild, br = b.getBoundingClientRect(), nr = n.getBoundingClientRect();
    return n.scrollWidth > n.clientWidth + 1 || nr.left < br.left - 1 || nr.right > br.right + 1
        || nr.bottom > br.bottom + 1; }).map(b => b.textContent.trim())"""
FITS = "() => { const s = document.querySelector('.modal-sheet'); return s.scrollHeight <= s.clientHeight + 1; }"
# the ₪ is attached to the number: the gap between the sign and the first digit
# (not the field's edge) is small
GAP = """() => { const c = document.querySelector('.amount-currency').getBoundingClientRect();
    const i = document.getElementById('txAmount'), r = i.getBoundingClientRect();
    return Math.round(r.left + parseFloat(getComputedStyle(i).paddingLeft) - c.right); }"""
# "תאריך אחר" on the same row as today/yesterday
ONE_ROW = """() => { const t = document.querySelectorAll('.date-quick-chips .date-chip, .date-pick');
    const tops = [...t].map(e => Math.round(e.getBoundingClientRect().top)); return Math.max(...tops) - Math.min(...tops) < 6; }"""

try:
    for _ in range(40):
        try:
            import urllib.request; urllib.request.urlopen(BASE + "/health", timeout=1); break
        except Exception: time.sleep(0.5)
    with sync_playwright() as p:
        b = p.chromium.launch()
        for width, height, theme in ((390, 844, None), (320, 640, None), (390, 844, "dark")):
            ctx = b.new_context(viewport={"width": width, "height": height}, has_touch=True, is_mobile=True,
                                device_scale_factor=2)
            if theme:
                ctx.add_init_script(f"try {{ localStorage.setItem('sf_theme', '{theme}'); }} catch (e) {{}}")
            page = ctx.new_page()
            page.goto(BASE + "/login")
            page.fill("#identifier", env["RLS_TEST_EMAIL_A"])
            page.fill("#password", env["RLS_TEST_PASSWORD_A"])
            page.click("button.submit-btn")
            page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
            page.goto(BASE + "/"); page.wait_for_timeout(1200)
            label = f"{width}x{height}{' ' + theme if theme else ''}"
            tag = f"{width}{'_' + theme if theme else ''}"
            page.screenshot(path=f"/tmp/sf_redesign_home_{tag}.png")
            cells = page.evaluate("[...document.querySelectorAll('.summary-card')].map(e => getComputedStyle(e).boxShadow)")
            check(f"{label}: the three cells have no shadow of their own", all(s == "none" for s in cells))

            page.click("#fabBtn"); page.wait_for_timeout(900)
            check(f"{label}: the scan button is in the title row",
                  page.evaluate("!!document.querySelector('.modal-title-row #scanBtn')"))
            check(f"{label}: nothing sticks out {page.evaluate(OUT)}", not page.evaluate(OUT))
            check(f"{label}: every category name shows whole {page.evaluate(CUT_NAMES)}", not page.evaluate(CUT_NAMES))
            page.fill("#txAmount", "512.50"); page.wait_for_timeout(100)
            check(f"{label}: ₪ sits next to the number (gap {page.evaluate(GAP)}px)", abs(page.evaluate(GAP)) <= 8)
            red = page.evaluate("getComputedStyle(txAmount).color")
            page.click("#toggleIncome"); page.wait_for_timeout(250)
            green = page.evaluate("getComputedStyle(txAmount).color")
            check(f"{label}: the amount changes color with the type ({red} → {green})", red != green)
            bg = page.evaluate("getComputedStyle(submitBtn).backgroundImage")
            page.click("#toggleExpense"); page.wait_for_timeout(250)
            check(f"{label}: the save button stays the same for every type",
                  bg == page.evaluate("getComputedStyle(submitBtn).backgroundImage"))
            if height >= 844:
                check(f"{label}: without a recurring transaction it all fits on one screen", page.evaluate(FITS))
                check(f"{label}: the date row is one line", page.evaluate(ONE_ROW))
            page.screenshot(path=f"/tmp/sf_redesign_add_{tag}.png")
            page.locator(".recurring-group .checkbox-label").click(); page.wait_for_timeout(350)
            check(f"{label}: the switch turns recurring on", page.evaluate("txRecurring.checked"))
            check(f"{label}: …the fields open inside the block",
                  page.evaluate("!!document.querySelector('.tx-details #recurringFields.visible')"))
            check(f"{label}: …and still nothing sticks out {page.evaluate(OUT)}", not page.evaluate(OUT))
            page.evaluate("document.querySelector('.modal-sheet').scrollTo(0, 9999)"); page.wait_for_timeout(200)
            page.screenshot(path=f"/tmp/sf_redesign_rec_{tag}.png")
            ctx.close()
        b.close()
finally:
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)

print("\n%d failed" % len(failed))
sys.exit(1 if failed else 0)
