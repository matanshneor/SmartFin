"""A new password: at least 8 characters, with letters and digits (Matan,
10.11.2026).

See backend/passwords.py. Here: the rule itself, that it is the same in the
browser and on the server, that sign-in never checks it (existing passwords keep
working), and that all three places where a password is chosen (sign-up, reset,
change) really check.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend import app as app_module
from backend import passwords
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent

# (password, expected message or None)
_CASES = [
    ("abc123",        passwords.TOO_SHORT),   # 6: too short
    ("abcd123",       passwords.TOO_SHORT),   # 7: one short
    ("12345678",      passwords.NO_LETTER),   # digits only
    ("abcdefgh",      passwords.NO_DIGIT),    # letters only
    ("!@#$%^&*",      passwords.NO_LETTER),   # symbols only
    ("abcdefg1",      None),                  # exactly 8, letter + digit
    ("Dana2026",      None),
    ("שלום1234",      None),                  # Hebrew letters count as letters
    ("a1!b2@c3#",     None),                  # symbols are allowed, not required
]


# ─── The rule ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("password, message", _CASES)
def test_the_rule(password, message):
    assert passwords.problem(password) == message


def test_the_leaked_password_check_is_gone():
    """Matan, 10.11: "I don't want this check". No call to an external service."""
    source = (_ROOT / "backend/passwords.py").read_text(encoding="utf-8")
    assert "pwnedpasswords" not in source and "httpx" not in source
    assert not hasattr(passwords, "is_leaked")
    privacy = (_ROOT / "frontend/templates/privacy.html").read_text(encoding="utf-8")
    assert "Pwned" not in privacy


@pytest.mark.skipif(not shutil.which("node"), reason="node isn't installed")
def test_the_browser_says_exactly_what_the_server_says():
    """The browser check runs first; if it disagreed with the server, a password
    could pass one and fail the other with a different message."""
    script = (_ROOT / "frontend/static/js/password-rules.js").read_text(encoding="utf-8")
    probe = ("var window = {};\n" + script +
             "\nconsole.log(JSON.stringify(%s.map(window.sfPasswordProblem)));"
             % json.dumps([p for p, _ in _CASES]))
    out = subprocess.run(["node", "-e", probe], capture_output=True, text=True, check=True)
    browser = json.loads(out.stdout)
    assert browser == [m or "" for _, m in _CASES]


# ─── Already registered (Matan, 10.11: "whoever is signed in stays signed in with their password") ──

@pytest.fixture
def client():
    app.config["TESTING"] = True
    limiter.reset()
    yield app.test_client()
    limiter.reset()


def test_sign_in_never_checks_the_password_rules(client, monkeypatch):
    """A short password, or one without digits: whoever is already registered with
    it signs in as usual, with no request to change it. The rules apply only to a
    **new** password."""
    def boom(*a, **k):
        pytest.fail("sign-in checked the new-password rules")
    monkeypatch.setattr(passwords, "problem", boom)
    from types import SimpleNamespace
    ok = SimpleNamespace(
        user=SimpleNamespace(id="u1", email="old@gmail.com"),
        session=SimpleNamespace(access_token="a", refresh_token="r", expires_at=9999999999))
    monkeypatch.setattr(app_module.db, "sign_in", lambda e, p: (ok, None))
    monkeypatch.setattr(app_module, "_begin_session", lambda *a: None)
    for old_password in ("123456", "abcdefgh"):
        with client.session_transaction() as s:
            s.clear()
        res = client.post("/login", data={"identifier": "old@gmail.com",
                                          "password": old_password})
        assert res.status_code == 302, res.get_data(as_text=True)


# ─── The three places ────────────────────────────────────────────────────────

_BAD = [("abcd123", passwords.TOO_SHORT),
        ("12345678", passwords.NO_LETTER),
        ("abcdefgh", passwords.NO_DIGIT)]


def _signup(client, password):
    return client.post("/signup", data={
        "first_name": "דנה", "last_name": "כהן", "email": "dana@gmail.com",
        "phone": "054-1234567", "password": password}).get_data(as_text=True)


@pytest.mark.parametrize("password, message", _BAD)
def test_signup_checks(client, monkeypatch, password, message):
    monkeypatch.setattr(app_module.db, "sign_up", lambda *a, **k: pytest.fail("signed up"))
    assert message in _signup(client, password)


def test_signup_accepts_a_valid_password(client, monkeypatch):
    called = []
    monkeypatch.setattr(app_module.db, "sign_up",
                        lambda *a, **k: (called.append(a), (None, "stop here"))[1])
    _signup(client, "Dana2026")
    assert called, "a valid password was refused"


def test_reset_checks(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "update_password", lambda *a: pytest.fail("changed"))
    for pw, msg in _BAD:
        res = client.post("/api/auth/reset", json={
            "access_token": "t", "password": pw, "password_confirm": pw})
        assert res.status_code == 422 and res.get_json()["error"] == msg


def test_change_in_settings_checks(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "sign_in", lambda *a: pytest.fail("the current password was checked"))
    monkeypatch.setattr(app_module.db, "update_password", lambda *a: pytest.fail("changed"))
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda t: None)
    with client.session_transaction() as s:
        s["user_id"], s["access_token"] = "u1", "tok"
    for pw, msg in _BAD:
        res = client.put("/api/profile/password", json={
            "current_password": "old", "password": pw, "password_confirm": pw})
        assert res.status_code == 422, res.get_data(as_text=True)
        assert res.get_json()["error"] == msg


def test_the_forms_use_the_shared_rule():
    """All three forms check through sfPasswordProblem, and each page loads it
    before its own script."""
    for page, script in (("login.html", "login.js"),
                         ("reset_password.html", "reset-password.js"),
                         ("settings.html", "settings.js")):
        html = (_ROOT / "frontend/templates" / page).read_text(encoding="utf-8")
        assert html.index("js/password-rules.js") < html.index(f"js/{script}'"), page
        assert "אותיות וספרות" in html, f"{page} doesn't tell the user the rule"
        js = (_ROOT / "frontend/static/js" / script).read_text(encoding="utf-8")
        assert "sfPasswordProblem(" in js, script
    text = "".join((_ROOT / f).read_text(encoding="utf-8") for f in (
        "frontend/templates/login.html", "frontend/templates/reset_password.html",
        "frontend/templates/settings.html"))
    assert 'minlength="6"' not in text and "לפחות 6 תווים" not in text
