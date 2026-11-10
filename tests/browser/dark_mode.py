"""A real browser test: screenshots of every page in dark mode (Matan, 30.9: round
6, item 4).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/dark_mode.py
The screenshots are in /tmp/dark.
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



SHOTS = "/tmp/dark"
os.makedirs(SHOTS, exist_ok=True)
try:
    for _ in range(40):
        try:
            import urllib.request; urllib.request.urlopen(BASE + "/health", timeout=1); break
        except Exception: time.sleep(0.5)
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844})
        ctx.add_init_script("try { localStorage.setItem('sf_theme', 'dark') } catch (e) {}")
        page = ctx.new_page()
        page.goto(BASE + "/login"); page.wait_for_timeout(500)
        page.screenshot(path=f"{SHOTS}/login.png")
        page.fill("#identifier", env.get("RLS_TEST_EMAIL_A", "rls-test-family-a@smartfin.test"))
        page.fill("#password", env["RLS_TEST_PASSWORD_A"])
        page.click("button.submit-btn")
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)

        def api(method, url, body=None):
            r = page.request.fetch(BASE + url, method=method, data=json.dumps(body) if body else None,
                                   headers={"Content-Type": "application/json"})
            return r.status, (r.json() if r.body() else None)

        cats = page.request.get(BASE + "/api/categories").json()
        by = {t: next(c["id"] for c in cats if c["type"] == t) for t in ("expense", "income", "savings")}
        _, c2 = api("POST", "/api/categories", {"name": "DK-סופר", "icon": "🛒", "type": "expense"})
        sup = (c2.get("category") or c2)["id"]
        today = datetime.date.today()
        prev = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=10)
        for d, kind, cat, amount, desc in (
                (today, "income", by["income"], 12000, "DK-TEST משכורת"),
                (today, "expense", sup, 1760, "DK-TEST רמי לוי"),
                (today, "expense", by["expense"], 650, "DK-TEST מסעדה"),
                (today, "savings", by["savings"], 2000, "DK-TEST קרן"),
                (prev, "expense", sup, 1500, "DK-TEST שופרסל"),
                (prev, "income", by["income"], 11000, "DK-TEST משכורת")):
            api("POST", "/api/transactions", {"amount": amount, "type": kind, "category_id": cat,
                                              "date": d.isoformat(), "description": desc})
        api("PUT", "/api/family/settings", {"limits": {sup: {"amount": 2000, "alert": False},
                                                       by["expense"]: {"amount": 500, "alert": False}}})
        _, proj = api("POST", "/api/projects", {"name": "DK-שיפוץ", "icon": "🔨", "track_expense": True})

        def shot(name, url=None, full=True):
            if url: page.goto(BASE + url); page.wait_for_timeout(1600)
            page.screenshot(path=f"{SHOTS}/{name}.png", full_page=full)

        fill_month(page, BASE)               # "לכל עסקאות החודש": only above 5 transactions
        shot("home", "/")
        shot("month", "/month")
        page.click("#txScreenOpen"); page.wait_for_timeout(300)
        page.locator(".tx-type-chip", has_text="הוצאות").click(); page.wait_for_timeout(300)
        page.locator("#txScreenOpen").scroll_into_view_if_needed()
        shot("month_filter", full=False)
        shot("months", "/months")
        shot("projects", "/projects")
        shot("project", f"/projects/{proj['id']}")
        shot("project_edit", f"/projects/{proj['id']}/edit")
        page.goto(BASE + "/settings"); page.wait_for_timeout(1000)
        for h in page.locator(".settings-group-header").all()[:4]:
            h.click(); page.wait_for_timeout(150)
        shot("settings")
        page.goto(BASE + "/"); page.wait_for_timeout(1000)
        page.click("#fabBtn"); page.wait_for_timeout(1000)
        shot("add_form", full=False)
        # ✕ and not Escape: after + the focus stays on the button, and Escape is
        # caught only inside the window
        page.click(".modal-close"); page.wait_for_timeout(400)
        page.click("#searchOpen"); page.fill("#searchInput", "DK-TEST"); page.wait_for_timeout(1500)
        shot("search", full=False)
        page.keyboard.press("Escape")
        # delete: to see the confirmation window
        page.goto(BASE + "/month"); page.wait_for_timeout(1200)
        page.click("#txScreenOpen"); page.wait_for_timeout(300)
        print("done")
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
        "print('cleaned', len(t('transactions').delete().eq('family_id', fid).like('description', 'DK-TEST%').execute().data))",
        "t('categories').delete().eq('family_id', fid).like('name', 'DK-%').execute()",
        "t('projects').delete().eq('family_id', fid).like('name', 'DK-%').execute()",
        "fam = t('families').select('settings').eq('id', fid).single().execute().data",
        "s = fam.get('settings') or {}; s['limits'] = {}",
        "t('families').update({'settings': s}).eq('id', fid).execute()",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
