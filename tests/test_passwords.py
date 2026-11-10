"""A new password: at least 8 characters, and not one that leaked (Matan,
10.11.2026).

See backend/passwords.py. Here: the rule itself, that the password doesn't leave
the server, that the external service doesn't block sign-up when it is down, and
that all three places where a password is chosen (sign-up, reset, change) really
check.
"""
import hashlib
import re
from pathlib import Path

import httpx
import pytest

from backend import app as app_module
from backend import passwords
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_LEAKED = "Password123!"          # as if leaked: set in the fake response
_FINE = "a-fine-long-passphrase"
# captured at import time, before conftest replaces it for every unit test
_REAL_FETCH = passwords._fetch_range


def _sha(pw):
    return hashlib.sha1(pw.encode("utf-8")).hexdigest().upper()


@pytest.fixture
def pwned(monkeypatch):
    """A fake service: _LEAKED leaked 42 times; _FINE appears only as a padding row."""
    asked = []

    def fake(prefix):
        asked.append(prefix)
        lines = ["0000000000000000000000000000000000A:3"]
        if _sha(_LEAKED)[:5] == prefix:
            lines.append(_sha(_LEAKED)[5:] + ":42")
        if _sha(_FINE)[:5] == prefix:
            lines.append(_sha(_FINE)[5:] + ":0")
        return "\r\n".join(lines)
    monkeypatch.setattr(passwords, "_fetch_range", fake)
    return asked


# ─── The rule ────────────────────────────────────────────────────────────────

def test_seven_is_too_short_eight_is_enough(pwned):
    assert passwords.problem("1234567x"[:7]) == passwords.TOO_SHORT
    assert passwords.problem(_FINE) is None
    assert passwords.problem("abcdefgh") is None


def test_a_leaked_password_is_refused(pwned):
    assert passwords.problem(_LEAKED) == passwords.LEAKED


def test_padding_rows_are_not_leaks(pwned):
    """Add-Padding returns rows with count 0: they aren't real passwords."""
    assert passwords.is_leaked(_FINE) is False


def test_only_five_hash_characters_leave_the_server(pwned):
    passwords.is_leaked(_LEAKED)
    assert pwned == [_sha(_LEAKED)[:5]]
    assert all(re.fullmatch(r"[0-9A-F]{5}", p) for p in pwned)


def test_the_request_asks_for_padding(monkeypatch):
    seen = {}

    class _Resp:
        text = ""
        def raise_for_status(self):
            pass

    def fake_get(url, **kw):
        seen["url"], seen["headers"] = url, kw.get("headers", {})
        return _Resp()
    monkeypatch.setattr(httpx, "get", fake_get)
    _REAL_FETCH("ABCDE")
    assert seen["url"] == "https://api.pwnedpasswords.com/range/ABCDE"
    assert seen["headers"].get("Add-Padding") == "true"


# ─── Already registered (Matan, 10.11: "whoever is signed in stays signed in with their password") ──

def test_sign_in_never_checks_the_password_rules(client, monkeypatch):
    """A short or leaked password: whoever is already registered with it signs in as
    usual, with no request to change it. The rules apply only to a **new**
    password."""
    def boom(*a, **k):
        pytest.fail("sign-in checked the new-password rules")
    monkeypatch.setattr(passwords, "is_leaked", boom)
    monkeypatch.setattr(passwords, "problem", boom)
    from types import SimpleNamespace
    ok = SimpleNamespace(
        user=SimpleNamespace(id="u1", email="old@gmail.com"),
        session=SimpleNamespace(access_token="a", refresh_token="r", expires_at=9999999999))
    monkeypatch.setattr(app_module.db, "sign_in", lambda e, p: (ok, None))
    monkeypatch.setattr(app_module, "_begin_session", lambda *a: None)
    for old_password in ("123456", _LEAKED):
        with client.session_transaction() as s:
            s.clear()
        res = client.post("/login", data={"identifier": "old@gmail.com",
                                          "password": old_password})
        assert res.status_code == 302, res.get_data(as_text=True)


def test_when_the_service_is_down_signup_is_not_stuck(monkeypatch):
    def down(prefix):
        raise httpx.ConnectTimeout("down")
    monkeypatch.setattr(passwords, "_fetch_range", down)
    assert passwords.problem(_LEAKED) is None


# ─── The three places ────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app.config["TESTING"] = True
    limiter.reset()
    yield app.test_client()
    limiter.reset()


def _signup(client, password):
    return client.post("/signup", data={
        "first_name": "דנה", "last_name": "כהן", "email": "dana@gmail.com",
        "phone": "054-1234567", "password": password}).get_data(as_text=True)


@pytest.mark.parametrize("password, message", [
    ("seven77", passwords.TOO_SHORT),
    (_LEAKED, passwords.LEAKED),
])
def test_signup_checks(client, monkeypatch, pwned, password, message):
    monkeypatch.setattr(app_module.db, "sign_up", lambda *a, **k: pytest.fail("signed up"))
    assert message in _signup(client, password)


def test_reset_checks(client, monkeypatch, pwned):
    monkeypatch.setattr(app_module.db, "update_password", lambda *a: pytest.fail("changed"))
    for pw, msg in (("seven77", passwords.TOO_SHORT), (_LEAKED, passwords.LEAKED)):
        res = client.post("/api/auth/reset", json={
            "access_token": "t", "password": pw, "password_confirm": pw})
        assert res.status_code == 422 and res.get_json()["error"] == msg


def test_change_in_settings_checks(client, monkeypatch, pwned):
    monkeypatch.setattr(app_module.db, "sign_in", lambda *a: pytest.fail("the current password was checked"))
    monkeypatch.setattr(app_module.db, "update_password", lambda *a: pytest.fail("changed"))
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda t: None)
    with client.session_transaction() as s:
        s["user_id"], s["access_token"] = "u1", "tok"
    for pw, needle in (("seven77", "לפחות 8 תווים"), (_LEAKED, passwords.LEAKED)):
        res = client.put("/api/profile/password", json={
            "current_password": "old", "password": pw, "password_confirm": pw})
        assert res.status_code == 422, res.get_data(as_text=True)
        assert needle in res.get_json()["error"]


def test_the_forms_say_eight():
    files = ["frontend/templates/login.html", "frontend/templates/reset_password.html",
             "frontend/templates/settings.html", "frontend/static/js/login.js",
             "frontend/static/js/reset-password.js", "frontend/static/js/settings.js"]
    text = "".join((_ROOT / f).read_text(encoding="utf-8") for f in files)
    assert 'minlength="6"' not in text and "לפחות 6 תווים" not in text
    assert "v.length >= 8" in text and "password.length < 8" in text
