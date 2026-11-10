"""A real browser test: "שייך לפרויקט" at the bottom of the add form: choosing it
takes you to the project's categories.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/project_field_at_bottom.py
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

        st, proj = api("POST", "/api/projects", {"name": "MOVE-PROJ", "track_expense": True,
                                                 "track_income": False, "track_savings": False})
        pid = (proj.get("project") or proj)["id"]
        print("temp project:", st)

        page.goto(BASE + "/")
        page.click("#fabBtn"); page.wait_for_selector("#modalOverlay.open"); page.wait_for_timeout(600)
        order = page.evaluate("""() => [...document.querySelectorAll('#txForm .form-group')]
            .filter(g => g.offsetParent !== null && !g.closest('.recurring-fields'))
            .map(g => (g.querySelector('.form-label, .checkbox-text') || {}).textContent || '?').map(t => t.trim().slice(0, 18))""")
        print("order (expense):", order)

        page.fill("#txAmount", "77")
        page.select_option("#txProject", pid)
        page.wait_for_timeout(1200)
        label_top = page.eval_on_selector("#categoryLabel", "e => e.getBoundingClientRect().top")
        print("label:", page.inner_text("#categoryLabel"), "| label on screen at", round(label_top),
              "| grid flash:", page.evaluate("document.getElementById('categoryGrid').classList.contains('flash')"),
              "| project categories:", page.locator("#categoryGrid .cat-btn").count())
        page.screenshot(path="/tmp/proj_pick.png")
        page.locator("#categoryGrid .cat-btn").first.click()
        page.fill("#txDescription", "MOVE-TEST")
        page.locator("#submitBtn").click()
        page.wait_for_timeout(2500)
        print("saved in project:", page.evaluate("document.getElementById('modalOverlay').classList.contains('open')") is False)

        page.click("#fabBtn"); page.wait_for_selector("#modalOverlay.open"); page.wait_for_timeout(500)
        page.click('#toggleIncome')
        page.wait_for_timeout(400)
        print("income form shows project field:", page.is_visible("#projectGroup"),
              "| label:", page.inner_text("#categoryLabel"))
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
        "n = len(t('transactions').delete().eq('family_id', fid).eq('description', 'MOVE-TEST').execute().data)",
        "for p in t('projects').select('id').eq('family_id', fid).eq('name', 'MOVE-PROJ').execute().data: db.delete_project(p['id'], fid)",
        "print('cleaned', n)",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
