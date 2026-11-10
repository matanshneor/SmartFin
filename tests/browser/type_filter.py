"""A real browser test: filtering "כל העסקאות" by type and by category on the month
page (Matan, 30.9: idea 5, option A).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/type_filter.py
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

        cats = page.request.get(BASE + "/api/categories").json()
        by = {t: next(c["id"] for c in cats if c["type"] == t) for t in ("income", "savings")}
        ex = {}
        for name, icon in (("TF-סופר", "🛒"), ("TF-דלק", "⛽")):
            _, c2 = api("POST", "/api/categories", {"name": name, "icon": icon, "type": "expense"})
            ex[name] = (c2.get("category") or c2)["id"]
        today = datetime.date.today().isoformat()
        for kind, cat, amount, desc in (("expense", ex["TF-סופר"], 100, "TF-TEST רמי לוי"),
                                        ("expense", ex["TF-סופר"], 64, "TF-TEST מכולת"),
                                        ("expense", ex["TF-דלק"], 50, "TF-TEST פז"),
                                        ("income", by["income"], 5000, "TF-TEST משכורת"),
                                        ("savings", by["savings"], 700, "TF-TEST קרן"),
                                        # a sixth: "לכל עסקאות החודש" appears only above 5 (since 5.10)
                                        ("expense", ex["TF-דלק"], 30, "TF-TEST סונול")):
            api("POST", "/api/transactions", {"amount": amount, "type": kind, "category_id": cat,
                                              "date": today, "description": desc})
        page.goto(BASE + "/month"); page.wait_for_timeout(1500)
        page.click("#txScreenOpen"); page.wait_for_timeout(300)
        sel = "#txScreen .tx-screen-list .cat-tx-row:visible"
        visible = lambda: page.locator(sel).count()
        descs = lambda: sorted(t.split("TF-TEST ")[-1] for t in page.locator(sel + " .cat-tx-desc").all_inner_texts())
        count = lambda: page.locator(".all-tx-count").inner_text()
        print("cat row before:", page.locator(".tx-cat-chips:visible").count())
        page.locator(".tx-type-chip", has_text="הוצאות").click(); page.wait_for_timeout(150)
        print("expense chips:", page.locator(".tx-cat-chips:visible .tx-cat-chip").all_inner_texts())
        def tap(text):
            page.locator(".tx-cat-chips:visible .tx-cat-chip", has_text=text).click(); page.wait_for_timeout(150)
            print("tap", text, "->", visible(), count(), descs())
        tap("TF-סופר"); tap("TF-דלק"); tap("TF-סופר"); tap("כל ההוצאות")
        tap("TF-סופר")
        page.fill("#txSearch", "מכולת"); page.wait_for_timeout(150)
        print("סופר + מכולת ->", visible(), count())
        page.fill("#txSearch", ""); page.wait_for_timeout(150)
        page.locator(".tx-type-chip", has_text="הכנסות").click(); page.wait_for_timeout(150)
        print("income ->", visible(), count(), "| rows open:", page.locator(".tx-cat-chips:visible").get_attribute("data-for-type"))
        page.locator(".tx-type-chip", has_text="הוצאות").click(); page.wait_for_timeout(150)
        print("back to expense resets:", visible(), count())
        page.locator(".tx-cat-chips:visible .tx-cat-chip", has_text="TF-סופר").click(); page.wait_for_timeout(150)
        page.screenshot(path="/tmp/cat_filter.png")
        # The date chips in the form still work, and the filter didn't touch the date.
        # First close "כל העסקאות": the screen covers +, for a user too
        page.keyboard.press("Escape"); page.wait_for_timeout(400)
        page.click("#fabBtn"); page.wait_for_timeout(800)
        page.locator(".date-quick-chips .date-chip", has_text="אתמול").click(); page.wait_for_timeout(100)
        print("form date after אתמול:", page.input_value("#txDate"),
              "| active:", page.locator(".date-quick-chips .date-chip.active").all_inner_texts())
        page.keyboard.press("Escape"); page.wait_for_timeout(400)
        for w in (320, 390):
            page.set_viewport_size({"width": w, "height": 800}); page.wait_for_timeout(300)
            print(w, "sideways:", page.evaluate("document.documentElement.scrollWidth > innerWidth"))
        page.set_viewport_size({"width": 390, "height": 844})
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
        "print('cleaned', len(t('transactions').delete().eq('family_id', fid).like('description', 'TF-TEST%').execute().data))",
        "t('categories').delete().eq('family_id', fid).like('name', 'TF-%').execute()",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
