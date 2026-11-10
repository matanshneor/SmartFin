"""A real browser test: + on a project page opens a form with the project already
selected (Matan, 30.9: idea 8).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/project_add.py
"""
import os, json, time, subprocess, signal
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

        st, proj = api("POST", "/api/projects", {"name": "PJ-שיפוץ", "icon": "🔨",
                                                 "track_expense": True, "track_income": False, "track_savings": False})
        st2, inc_proj = api("POST", "/api/projects", {"name": "PJ-השכרה", "icon": "🏘",
                                                      "track_expense": False, "track_income": True, "track_savings": False})
        print("projects:", st, st2)

        def open_form():
            page.click("#fabBtn"); page.wait_for_timeout(900)
            chosen = page.locator("#txProject option:checked")
            return {"project": chosen.inner_text() if page.locator("#txProject").is_visible() else "(hidden)",
                    "type": page.locator(".toggle-btn.active").first.inner_text(),
                    "label": page.locator("#categoryLabel").inner_text()}

        page.goto(BASE + f"/projects/{proj['id']}"); page.wait_for_timeout(1200)
        print("expense project page:", open_form())
        page.click("#toggleIncome"); page.wait_for_timeout(300)
        print("  after switching to income:", page.locator("#txProject").input_value() or "(none)")
        page.keyboard.press("Escape"); page.wait_for_timeout(400)

        page.goto(BASE + f"/projects/{inc_proj['id']}"); page.wait_for_timeout(1200)
        print("income project page:", open_form())
        page.fill("#txAmount", "1234"); page.fill("#txDescription", "PJ-TEST שכירות")
        page.click("#submitBtn"); page.wait_for_timeout(2500)
        print("saved into project:", page.locator(".cat-tx-row", has_text="PJ-TEST").count())
        page.screenshot(path="/tmp/project_add.png")

        page.goto(BASE + "/"); page.wait_for_timeout(1000)
        print("home page:", open_form())
        page.keyboard.press("Escape")
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
        "print('cleaned', len(t('transactions').delete().eq('family_id', fid).like('description', 'PJ-TEST%').execute().data))",
        "print('projects', len(t('projects').delete().eq('family_id', fid).like('name', 'PJ-%').execute().data))",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
