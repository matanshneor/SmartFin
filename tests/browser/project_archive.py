"""A real browser test: "הפרויקט הסתיים" and reopening (Matan, 30.9: round 6, item
3).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/project_archive.py
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

        _, proj = api("POST", "/api/projects", {"name": "PA-שיפוץ", "icon": "🔨", "track_expense": True})
        pid = proj["id"]
        pcat = api("GET", f"/api/projects/{pid}/categories?type=expense")[1][0]["id"]
        api("POST", "/api/transactions", {"amount": 4000, "type": "expense", "project_id": pid,
                                          "project_category_id": pcat, "date": datetime.date.today().isoformat(),
                                          "description": "PA-TEST קבלן"})
        # since 9.10 finishing is at the bottom of the project page, not on the edit page
        page.goto(BASE + f"/projects/{pid}/edit"); page.wait_for_timeout(1000)
        print("edit page finish buttons (expect 0):", page.locator("[data-archive-project]").count())
        page.goto(BASE + f"/projects/{pid}"); page.wait_for_timeout(1000)
        page.locator(".project-finish [data-archive-project]").scroll_into_view_if_needed()
        page.screenshot(path="/tmp/finish_bottom.png", full_page=True)
        page.locator(".project-finish [data-archive-project]").click(); page.wait_for_timeout(400)
        print("confirm asks:", page.locator(".confirm-overlay.open, #confirmOverlay.open").count() or page.locator("text=לסמן את").count())
        page.locator("button", has_text="סיום הפרויקט").last.click(); page.wait_for_timeout(2000)
        print("project page now: banner", page.locator(".project-archived-banner").count(),
              "| bottom finish block (expect 0):", page.locator(".project-finish").count())

        page.goto(BASE + "/projects"); page.wait_for_timeout(1000)
        print("in main list:", page.locator("#projectsList .project-tile-name", has_text="PA-שיפוץ").count())
        # since the page's new design: the active ones in a gallery with no heading,
        # and finished ones as "chips"
        print("sections:", page.get_attribute("#activeProjects", "aria-label"), "|",
              page.locator("#finishedProjects .projects-section-title").inner_text(),
              "| finished list:", [t.strip() for t in page.locator("#finishedProjects .finished-chip").all_inner_texts()])
        page.screenshot(path="/tmp/projects_two.png")
        page.locator(".finished-projects .finished-chip", has_text="PA-שיפוץ").click(); page.wait_for_timeout(1200)
        print("banner:", page.locator(".project-archived-banner").inner_text().replace("\n", " | "))
        page.screenshot(path="/tmp/archived.png")

        # an old transaction in a finished project opens for editing and stays in it
        page.locator(".cat-tx-row:visible, .transaction-item:visible", has_text="PA-TEST").first.click(); page.wait_for_timeout(1200)
        print("editing old tx — project kept:", page.locator("#txProject option:checked").inner_text())
        page.keyboard.press("Escape"); page.wait_for_timeout(400)

        # a new transaction: the project isn't offered
        page.goto(BASE + "/"); page.wait_for_timeout(800)
        page.click("#fabBtn"); page.wait_for_timeout(900)
        opts = page.locator("#txProject option").all_inner_texts() if page.locator("#txProject").is_visible() else []
        print("new tx project options:", opts)
        page.keyboard.press("Escape"); page.wait_for_timeout(300)

        page.goto(BASE + f"/projects/{pid}"); page.wait_for_timeout(1000)
        page.locator(".project-archived-banner [data-archive-project]").click(); page.wait_for_timeout(2000)
        print("after back to active — banner:", page.locator(".project-archived-banner").count())
        page.goto(BASE + "/projects"); page.wait_for_timeout(800)
        print("back in main list:", page.locator("#projectsList .project-tile-name", has_text="PA-שיפוץ").count(),
              "| finished section:", page.locator(".finished-projects").count())
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
        "print('cleaned', len(t('transactions').delete().eq('family_id', fid).like('description', 'PA-TEST%').execute().data))",
        "print('projects', len(t('projects').delete().eq('family_id', fid).like('name', 'PA-%').execute().data))",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
