"""A real browser test: deleting a category with transactions moves them before the
deletion.

Not collected by pytest (it has no test_ prefix), because it brings up a local
server, signs in as test user A (RLS_TEST_* in .env) and writes to the real
database, and cleans up after itself.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/category_delete_move.py
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

        _, src = api("POST", "/api/categories", {"name": "MOVE-FROM", "icon": "🧪", "type": "expense"})
        _, dst = api("POST", "/api/categories", {"name": "MOVE-TO", "icon": "🎯", "type": "expense"})
        src, dst = (src.get("category") or src)["id"], (dst.get("category") or dst)["id"]
        today = datetime.date.today().isoformat()
        for amount in (11, 22):
            st, _ = api("POST", "/api/transactions", {"amount": amount, "type": "expense", "date": today,
                                                      "description": "MOVECAT", "category_id": src})
            print("tx created:", st)
        st, body = api("POST", "/api/transactions", {"amount": 5, "type": "expense", "date": today,
                                                     "description": "MOVECAT"})
        print("without category:", st, body)

        page.goto(BASE + "/settings")
        btn = page.locator(f'.delete-cat-btn[data-id="{src}"]')

        # 1. Cancel: nothing is deleted. (Not Escape: in a headless browser the page
        # has no keyboard focus, and the dialog doesn't get the key: a first test
        # failed on that.)
        btn.evaluate("b => b.click()")
        page.wait_for_selector("#confirmOverlay.open", timeout=5000)
        print("dialog:", page.inner_text("#confirmMessage"))
        options = page.eval_on_selector_all("#confirmChoice option", "os => os.map(o => o.textContent)")
        print("choices include MOVE-TO:", any("MOVE-TO" in o for o in options),
              "| MOVE-FROM offered:", any("MOVE-FROM" in o for o in options))
        page.click("#confirmNo")
        page.wait_for_selector("#confirmOverlay:not(.open)", state="attached", timeout=3000)
        page.wait_for_timeout(300)
        print("after cancel, row still there:", btn.count() == 1)

        # a double tap: only one question
        n_usage = []
        page.on("request", lambda r: n_usage.append(1) if r.url.endswith("/usage") else None)

        # 2. Choose and confirm
        btn.evaluate("b => { b.click(); b.click(); }")
        page.wait_for_selector("#confirmOverlay.open", timeout=5000)
        page.select_option("#confirmChoice", dst)
        page.click("#confirmYes")
        page.wait_for_selector(".toast.show", timeout=5000)
        print("toast:", page.inner_text(".toast"))
        page.wait_for_timeout(600)
        print("row removed:", btn.count() == 0, "| usage requests for the double tap:", len(n_usage))
        print("dialog closed:", not page.eval_on_selector("#confirmOverlay", "e => e.classList.contains('open')"),
              "| visible:", page.eval_on_selector("#confirmOverlay", "e => getComputedStyle(e).visibility"))
        page.screenshot(path="/tmp/catmove.png")
        b.close()
finally:
    check = "\n".join([
        "import os, sys",
        "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "t = db.get_client().table",
        "rows = t('transactions').select('amount, categories(name)').eq('family_id', fid).eq('description', 'MOVECAT').execute().data",
        "print('in DB:', sorted((float(x['amount']), (x['categories'] or {}).get('name')) for x in rows))",
        "t('transactions').delete().eq('family_id', fid).eq('description', 'MOVECAT').execute()",
        "t('categories').delete().eq('family_id', fid).in_('name', ['MOVE-FROM', 'MOVE-TO']).execute()",
        "print('cleaned')",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", check], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)   # Flask's child process closes a moment later
