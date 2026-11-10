"""An invitation by link (Matan, 3.10, idea 43): `/join/<code>` opens signup with
the code filled in, or (for someone already signed in) settings with the code in
the join row."""
import json
import subprocess
from pathlib import Path

import pytest

from backend.app import app, limiter

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    limiter.reset()
    app.config["TESTING"] = True
    return app.test_client()


def test_without_an_account_the_link_opens_signup_with_the_code(client):
    res = client.get("/join/k4f2qx")
    assert res.status_code == 302 and res.headers["Location"].endswith("/signup?invite=K4F2QX")


def test_signed_in_the_link_opens_the_join_row_in_settings(client):
    with client.session_transaction() as sess:
        sess["user_id"] = "u1"
        sess["family_id"] = "f1"
    res = client.get("/join/K4F2QX")
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/settings?join=K4F2QX#join-family")


def test_only_letters_and_digits_get_through(client):
    res = client.get('/join/K4"><script>1')
    assert res.headers["Location"].endswith("/signup?invite=K4SCRIPT1")


def test_signup_shows_the_code_already_filled_in(client):
    html = client.get("/signup?invite=K4F2QX").get_data(as_text=True)
    assert 'value="K4F2QX"' in html
    assert 'id="tabSignup"' in html


def test_signup_without_a_link_is_empty(client):
    html = client.get("/signup").get_data(as_text=True)
    start = html.index('id="invite_code"')
    assert 'value=""' in html[start:start + 200]


def test_the_copied_message_has_the_link_and_still_the_code():
    harness = """
    global.window = { location: { origin: 'https://smartfin.up.railway.app' } };
    eval(require('fs').readFileSync(process.argv[1], 'utf8'));
    console.log(JSON.stringify(window.sfInviteMessage('K4F2QX', 'משפחת כהן')));
    """
    out = subprocess.run(["node", "-e", harness, str(_ROOT / "frontend/static/js/clipboard.js")],
                         capture_output=True, text=True, check=True)
    msg = json.loads(out.stdout)
    assert "https://smartfin.up.railway.app/join/K4F2QX" in msg
    assert "\nK4F2QX" in msg, "the code stays: for whoever prefers to type"


def test_settings_fills_the_code_and_does_not_join_by_itself():
    js = (_ROOT / "frontend/static/js/settings.js").read_text(encoding="utf-8")
    part = js[js.index("new URLSearchParams(location.search).get('join')"):]
    assert "input.value = code.replace(/[^A-Za-z0-9]/g, '').slice(0, 12).toUpperCase();" in part
    assert "joinBtn.click()" not in part and "/api/family/join" not in part
