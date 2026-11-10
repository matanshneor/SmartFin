"""A real browser test: a project page shows the 5 latest transactions, and the rest
behind "הצג את כל העסקאות".

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/project_tx_collapsed.py
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

        st, proj = api("POST", "/api/projects", {"name": "LIST-PROJ", "track_expense": True,
                                                 "track_income": False, "track_savings": False})
        pid = (proj.get("project") or proj)["id"]
        pcats = page.request.get(BASE + f"/api/projects/{pid}/categories?type=expense").json()
        for i in range(8):
            api("POST", "/api/transactions", {"amount": 10 + i, "type": "expense", "date": f"2026-09-{10 + i:02d}",
                                              "description": f"LIST-TX-{i}", "project_id": pid,
                                              "project_category_id": pcats[0]["id"]})
        visible = "() => [...document.querySelectorAll('#projectTxList .cat-tx-row')].filter(r => r.offsetParent !== null).length"
        page.goto(BASE + f"/projects/{pid}")
        page.wait_for_timeout(800)
        btn = page.locator("#showAllProjectTx")
        print("rows shown:", page.evaluate(visible), "| button:", btn.inner_text(), "| expanded:", btn.get_attribute("aria-expanded"))
        page.locator("#showAllProjectTx").scroll_into_view_if_needed(); page.screenshot(path="/tmp/ptx_closed.png")
        print("newest first:", page.eval_on_selector("#projectTxList .cat-tx-row", "e => e.textContent.includes('LIST-TX-7')"))
        btn.click(); page.wait_for_timeout(300)
        print("after click:", page.evaluate(visible), "| button:", btn.inner_text(), "| expanded:", btn.get_attribute("aria-expanded"))
        pass
        btn.click(); page.wait_for_timeout(300)
        print("after 2nd click:", page.evaluate(visible), "| button:", btn.inner_text())
        # without the charts library
        page.route("**/chart.umd.min.js*", lambda r: r.abort())
        page.goto(BASE + f"/projects/{pid}"); page.wait_for_timeout(800)
        page.click("#showAllProjectTx"); page.wait_for_timeout(300)
        print("without Chart.js, after click:", page.evaluate(visible))
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
        "for p in t('projects').select('id').eq('family_id', fid).eq('name', 'LIST-PROJ').execute().data: print('deleted', db.delete_project(p['id'], fid))",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
