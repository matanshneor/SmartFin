"""A real browser test: whoever's family changed behind their back doesn't get stuck
on a fault page.

Signs in as test user B, moves them (SQL with owner privileges) to A's family (what
removal or leaving on another device does) and opens the month page. Moves them
back at the end.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/family_changed_session.py
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
EMAIL_A = env.get("RLS_TEST_EMAIL_A", "rls-test-family-a@smartfin.test")
EMAIL_B = env.get("RLS_TEST_EMAIL_B", "rls-test-family-b@smartfin.test")


def sql(q):
    out = subprocess.run(["supabase", "db", "query", q, "--linked", "-o", "json"],
                         cwd=os.path.join(ROOT, "backend"), capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    txt = out.stdout
    return json.loads(txt[txt.index("{"):])["rows"]


ids = sql(f"select u.email, p.id, p.family_id from auth.users u join profiles p on p.id = u.id "
          f"where u.email in ('{EMAIL_A}', '{EMAIL_B}')")
a = next(r for r in ids if r["email"] == EMAIL_A)
b = next(r for r in ids if r["email"] == EMAIL_B)
manager = sql(f"select manager_id from families where id = '{a['family_id']}'")[0]["manager_id"]

srv = subprocess.Popen([os.path.join(ROOT, ".venv/bin/python3"), "-m", "backend.app"], cwd=ROOT, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
BASE = "http://127.0.0.1:8099"
try:
    for _ in range(40):
        try:
            import urllib.request; urllib.request.urlopen(BASE + "/health", timeout=1); break
        except Exception: time.sleep(0.5)
    with sync_playwright() as p:
        br = p.chromium.launch()
        page = br.new_page(viewport={"width": 390, "height": 844})
        page.goto(BASE + "/login")
        page.fill("#identifier", EMAIL_B)
        page.fill("#password", env["RLS_TEST_PASSWORD_B"])
        page.click("button.submit-btn")
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
        page.goto(BASE + "/month")
        print("before the move, month page:", "לא הצלחנו לטעון" not in page.content())

        # behind their back: B moves to A's family (what removal or leaving on another
        # device does)
        sql(f"update profiles set family_id = '{a['family_id']}' where id = '{b['id']}'")

        page.goto(BASE + "/month")
        page.wait_for_timeout(1200)
        print("landed on:", page.url.replace(BASE, ""),
              "| error page:", "לא הצלחנו לטעון" in page.content())
        toast = page.inner_text(".toast") if page.is_visible(".toast") else None
        print("toast:", toast)
        page.screenshot(path="/tmp/family_changed.png")
        page.goto(BASE + "/month")
        page.wait_for_timeout(1200)
        again = page.eval_on_selector(".toast", "t => t.classList.contains('show') ? t.textContent : null")
        print("month page now works:", "לא הצלחנו לטעון" not in page.content(),
              "| notice again:", again, "| data block:", page.locator("#sfNotice").count())
        br.close()
finally:
    sql(f"update profiles set family_id = '{b['family_id']}' where id = '{b['id']}'")
    back = sql(f"select family_id from profiles where id = '{b['id']}'")[0]["family_id"]
    print("B restored:", back == b["family_id"])
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
