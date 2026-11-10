"""A real browser test: choosing a colour for a family member in settings (Matan,
30.9: round 6, item 13).

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/member_colors.py
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
        cats = page.request.get(BASE + "/api/categories").json()
        inc = next(c["id"] for c in cats if c["type"] == "income")
        page.request.fetch(BASE + "/api/transactions", method="POST", headers={"Content-Type": "application/json"},
                           data=json.dumps({"amount": 5000, "type": "income", "category_id": inc,
                                            "date": datetime.date.today().isoformat(), "description": "MC-TEST"}))
        def open_family():
            page.goto(BASE + "/settings"); page.wait_for_timeout(900)
            page.locator(".settings-group-header", has_text="המשפחה שלי").click(); page.wait_for_timeout(300)
        open_family()
        print("no dots under names:", page.locator(".member-colors").count() == 0,
              "| button only on my row:", page.locator(".member-settings-row.is-me #memberColorOpen").count(),
              page.locator(".member-color-open").count())
        page.click("#memberColorOpen"); page.wait_for_timeout(300)
        opts = page.locator(".color-option")
        print("window open:", page.is_visible("#memberColorSheet"), "| options:", opts.count(),
              "| preview:", opts.first.locator(".owner-pill").inner_text(),
              "| current note:", page.locator(".color-option.is-current .color-option-note").inner_text())
        page.screenshot(path="/tmp/color_sheet.png")
        opts.nth(6).click(); page.wait_for_timeout(2000)
        open_family()
        page.click("#memberColorOpen"); page.wait_for_timeout(300)
        print("after pick:", page.locator(".color-option.is-current").get_attribute("data-color"),
              "| button swatch:", page.locator(".member-color-swatch").get_attribute("style"))
        page.keyboard.press("Escape"); page.wait_for_timeout(450)   # the window goes down in 250ms
        print("escape closes:", not page.is_visible("#memberColorSheet"))
        page.goto(BASE + "/"); page.wait_for_timeout(1000)
        print("home pill class:", page.locator(".owner-pill").first.get_attribute("class") if page.locator(".owner-pill").count() else "(no pills — attribution off)")
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
        "print('cleaned', len(t('transactions').delete().eq('family_id', fid).eq('description', 'MC-TEST').execute().data))",
        "print('colors reset', db.update_family_settings(fid, {'member_colors': {}}))",
    ])
    subprocess.run([os.path.join(ROOT, ".venv/bin/python3"), "-c", cleanup], cwd=ROOT)
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)
