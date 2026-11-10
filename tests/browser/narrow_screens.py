"""A real browser test: no page scrolls sideways at phone widths (320–430).

Amounts with agorot ("₪28,765.43") pushed the three home cards off screen at width
375, and shifted the page at 390 (30.9.2026). Temporarily adds transactions with
realistic amounts, prints every page that overflows or text that went off screen,
and cleans up. "clipped" on a long name ending in "…" is intentional.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/narrow_screens.py
"""
import os, json, time, datetime, subprocess, signal
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


created = None
cat = None
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

        def api(method, url, body=None):
            r = page.request.fetch(BASE + url, method=method, data=json.dumps(body) if body else None,
                                   headers={"Content-Type": "application/json"})
            return r.status, (r.json() if r.body() else None)

        def api(method, url, body=None):
            r = page.request.fetch(BASE + url, method=method, data=json.dumps(body) if body else None,
                                   headers={"Content-Type": "application/json"})
            return r.status, (r.json() if r.body() else None)

        cats = page.request.get(BASE + "/api/categories").json()
        by_type = {}
        for c in cats: by_type.setdefault(c["type"], c["id"])
        today = datetime.date.today().isoformat()
        for amount, kind in (("34567.89", "income"), ("28765.43", "expense"), ("4321.09", "savings")):
            print("tx", api("POST", "/api/transactions", {"amount": amount, "type": kind, "date": today,
                   "description": "WIDE-TEST", "category_id": by_type.get(kind)})[0])
        CHECK = """() => {
            const out = [];
            const W = document.documentElement.clientWidth;
            if (document.documentElement.scrollWidth > W + 1) out.push('PAGE SCROLLS SIDEWAYS: ' + document.documentElement.scrollWidth);
            for (const e of document.querySelectorAll('main *, header *')) {
                const r = e.getBoundingClientRect();
                if (!r.width || getComputedStyle(e).visibility === 'hidden') continue;
                const txt = (e.childElementCount === 0 ? e.textContent.trim() : '');
                if (r.right > W + 1 || r.left < -1) { if (txt) out.push('off-screen: ' + txt.slice(0, 30)); continue; }
                if (txt && e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflow !== 'visible')
                    out.push('clipped: ' + txt.slice(0, 30));
            }
            return [...new Set(out)].slice(0, 12);
        }"""
        for width in (430, 393, 390, 375, 360, 320):
            page.set_viewport_size({"width": width, "height": 800})
            for path in ("/", "/month", "/months", "/projects", "/settings"):
                page.goto(BASE + path)
                page.wait_for_timeout(900)
                if path == "/settings":
                    page.click("button:has(.group-title:text-is('המשפחה שלי'))"); page.wait_for_timeout(400)
                print(width, path, page.evaluate(CHECK))
                page.screenshot(path=f"/tmp/w{width}{path.replace('/', '_') or '_home'}.png", full_page=True)
        b.close()
finally:
    cleanup = "\n".join([
        "import os, sys", "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "print('cleaned', len(db.get_client().table('transactions').delete().eq('family_id', fid).eq('description', 'WIDE-TEST').execute().data))",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
