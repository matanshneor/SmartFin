"""A real browser test: "שכחתי סיסמה" shows what the server really said (B4).

Not collected by pytest (it has no test_ prefix), because it brings up a local
server, and doesn't touch the database: every server response is mocked in
Playwright. Server responses to the slow requests are mocked in Playwright.

Run (Playwright is installed in the system Python, not in .venv):
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 tests/browser/forgot_password.py
"""
import os, json, time, subprocess, signal
from playwright.sync_api import sync_playwright

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
JPEG = "/tmp/tiny.jpg"
open(JPEG, "wb").write(bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9"))
try:
    for _ in range(40):
        try:
            import urllib.request; urllib.request.urlopen(BASE + "/health", timeout=1); break
        except Exception: time.sleep(0.5)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 390, "height": 844})
        answer = {}
        page.route("**/api/auth/forgot", lambda route: route.fulfill(
            status=answer["status"], content_type="application/json", body=json.dumps(answer["body"])))
        page.goto(BASE + "/login")
        page.click("#forgotLink")
        page.fill("#forgotEmail", "someone@example.com")
        cases = [
            ("bad email",      422, {"error": "כתובת המייל אינה תקינה"}),
            ("mail limit",     503, {"error": "שלחנו יותר מדי מיילים בשעה האחרונה — נסו שוב בעוד כשעה"}),
            ("rate limited",   429, {"error": "יותר מדי ניסיונות בזמן קצר — נסו שוב בעוד דקה"}),
            ("sent",           200, {"status": "ok"}),
        ]
        for name, status, body in cases:
            answer.update(status=status, body=body)
            page.click("#forgotBtn"); page.wait_for_timeout(700)
            err = page.text_content("#forgotError"); ok = page.text_content("#forgotSuccess")
            right = (ok.startswith("אם המייל רשום") and not err) if status == 200 \
                    else (err == body["error"] and not ok)
            print(f"{name:13} [{status}] error={err!r} success={ok[:20]!r}  ->", "OK" if right else "WRONG")
        b.close()
finally:
    os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
    time.sleep(2)   # Flask's child process closes a moment later
