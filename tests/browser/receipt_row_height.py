"""A real browser test: an attached receipt doesn't make the transaction row taller
(on the home page and the month page).

Matan (30.9): "the transaction row expands and I don't like it". On the home page
the pin sat on its own line below the amount (+9px). Prints the height of both
rows: they should be identical. A one-pixel difference on the month page is the
divider (the last row has none).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/receipt_row_height.py
"""
import os, json, time, datetime, subprocess, signal
from playwright.sync_api import sync_playwright
from _month_fill import fill_month
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
FAMILY_ID = subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", "\n".join([
    "import os, sys", "sys.path.insert(0, '.')", "from dotenv import load_dotenv; load_dotenv('.env')",
    "from backend import supabase_config as db",
    "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
    "db.set_auth_token(r.session.access_token)", "print(db.get_profile(r.user.id)['family_id'])"])],
    cwd=ROOT, capture_output=True, text=True).stdout.strip().splitlines()[-1]
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

        cats = page.request.get(BASE + "/api/categories").json()
        cat = next(c["id"] for c in cats if c["type"] == "expense")
        fid = page.evaluate("fetch('/api/family/members').then(r => r.json())")
        import uuid as _u
        prof = sql_family = None
        today = datetime.date.today().isoformat()
        me_family = page.evaluate("() => document.cookie") and None
        st, a = api("POST", "/api/transactions", {"amount": 11, "type": "expense", "date": today, "description": "RCPT-NO", "category_id": cat})
        fam = FAMILY_ID
        st, b2 = api("POST", "/api/transactions", {"amount": 12, "type": "expense", "date": today, "description": "RCPT-YES", "category_id": cat,
                                                    "receipt_path": f"{fam}/{_u.uuid4()}.jpg"})
        print("created:", st)
        H = """desc => [...document.querySelectorAll('.transaction-item, .cat-tx-row')].filter(r => r.textContent.includes(desc) && r.offsetParent !== null)
                 .map(r => ((r.querySelector(".tx-head") || r).getBoundingClientRect().height).toFixed(2))"""
        fill_month(page, BASE)               # "לכל עסקאות החודש": only above 5 transactions
        for path in ("/", "/month"):
            page.goto(BASE + path); page.wait_for_timeout(900)
            if path == "/month":
                page.locator("#txScreenOpen").click(); page.wait_for_timeout(400)
            print(path, "row without receipt:", page.evaluate(H, "RCPT-NO"), "| with receipt:", page.evaluate(H, "RCPT-YES"))
            if path == "/month":
                for d in ("RCPT-NO", "RCPT-YES"):
                    print(" ", d, page.evaluate("""desc => { const r = [...document.querySelectorAll('.cat-tx-row')].find(x => x.textContent.includes(desc) && x.offsetParent);
                        return [...r.children].map(c => c.className + ':' + c.getBoundingClientRect().height.toFixed(1) + (getComputedStyle(c).whiteSpace === 'nowrap' ? '' : '(wraps)')); }""", d))
        page.goto(BASE + "/month"); page.wait_for_timeout(900); page.locator("#txScreenOpen").click(); page.wait_for_timeout(400)
        page.goto(BASE + "/"); page.wait_for_timeout(900); page.locator(".transaction-item", has_text="RCPT-YES").first.screenshot(path="/tmp/rcpt_row.png")
        b.close()
finally:
    cleanup = "\n".join([
        "import os, sys", "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "db.get_client().table('transactions').delete().eq('family_id', fid).in_('description', ['RCPT-NO', 'RCPT-YES']).execute()",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
