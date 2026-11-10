"""A real browser test: duplicate and unusual-amount warning before saving (Matan,
30.9: round 6, items 6–7).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/precheck.py
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

        _, c2 = api("POST", "/api/categories", {"name": "PC-סופר", "icon": "🛒", "type": "expense"})
        cat = (c2.get("category") or c2)["id"]
        today = datetime.date.today()
        for i, a in enumerate((60, 120, 200, 450, 300)):
            api("POST", "/api/transactions", {"amount": a, "type": "expense", "category_id": cat,
                                              "date": (today - datetime.timedelta(days=10 + i * 7)).isoformat(),
                                              "description": "PC-TEST היסטוריה"})
        api("POST", "/api/transactions", {"amount": 320, "type": "expense", "category_id": cat,
                                          "date": today.isoformat(), "description": "PC-TEST רמי לוי"})
        count = lambda: len([t for t in page.request.get(BASE + "/api/search?q=PC-TEST").json()["results"]])

        def add(amount, desc):
            page.goto(BASE + "/"); page.wait_for_timeout(900)
            page.click("#fabBtn"); page.wait_for_timeout(900)
            page.locator("#categoryGrid .cat-btn", has_text="PC-סופר").click()
            page.fill("#txAmount", str(amount)); page.fill("#txDescription", desc)
            page.click("#submitBtn"); page.wait_for_timeout(1500)

        n0 = count()
        add(320, "PC-TEST שוב")
        print("duplicate asks:", page.inner_text("#confirmTitle"), "|", page.inner_text("#confirmMessage").replace("\n", " / "))
        page.screenshot(path="/tmp/precheck_dup.png")
        page.click("#confirmNo"); page.wait_for_timeout(800)
        print("cancel — nothing saved:", count() == n0, "| form still open:", page.locator(".modal-overlay.open").count())
        page.click("#submitBtn"); page.wait_for_timeout(1500)
        page.click("#confirmYes"); page.wait_for_timeout(2500)
        print("add anyway — saved:", count() == n0 + 1)

        add(3200, "PC-TEST הקלדה")
        print("unusual asks:", page.inner_text("#confirmTitle"), "|", page.inner_text("#confirmMessage"))
        page.screenshot(path="/tmp/precheck_unusual.png")
        page.click("#confirmNo"); page.wait_for_timeout(600)
        print("לתקן — focus on amount:", page.evaluate("document.activeElement.id"), "| saved?", count() != n0 + 1)
        page.fill("#txAmount", "80"); page.click("#submitBtn"); page.wait_for_timeout(2500)
        print("normal amount — no question, saved:", page.locator("#confirmOverlay.open").count() == 0, count() == n0 + 2)
        b.close()
finally:
    cleanup = "\n".join([
        "import os, sys", "sys.path.insert(0, '.')",
        "from dotenv import load_dotenv; load_dotenv('.env')",
        "from backend import supabase_config as db",
        "r, _ = db.sign_in(os.environ.get('RLS_TEST_EMAIL_A', 'rls-test-family-a@smartfin.test'), os.environ['RLS_TEST_PASSWORD_A'])",
        "db.set_auth_token(r.session.access_token)",
        "fid = db.get_profile(r.user.id)['family_id']",
        "t = db.get_client().table",
        "print('cleaned', len(t('transactions').delete().eq('family_id', fid).like('description', 'PC-TEST%').execute().data))",
        "t('categories').delete().eq('family_id', fid).like('name', 'PC-%').execute()",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
