"""Email and phone in the "account" box in settings (Matan, 1.10).

The phone: a direct change, but not empty: it is also a sign-in identifier. And
when saving a name or workplace from "my family" (which no longer sends a
phone) it isn't deleted.

The email: a new email + the current password, then a confirmation link from
Supabase. Without the password, whoever grabs an unlocked phone moves the
account to their own email.
"""
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_ME = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _fresh_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda t: None)
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = _ME
        sess["family_id"] = None
        sess["user_email"] = "old@example.com"
        sess["access_token"] = "tok"
    return c


# ─── Phone ────────────────────────────────────────────────────────────────

def test_changing_the_phone(client, monkeypatch):
    saved = []
    monkeypatch.setattr(app_module.db, "update_phone", lambda uid, phone: saved.append((uid, phone)) or (True, None))
    res = client.put("/api/profile/phone", json={"phone": "050-1234567"})
    assert res.status_code == 200 and res.get_json()["phone"] == "0501234567"
    assert saved == [(_ME, "0501234567")]


@pytest.mark.parametrize("phone", ["", "1", "+1-555-123-4567"])
def test_an_empty_or_bad_phone_is_refused(client, monkeypatch, phone):
    """Empty isn't allowed here, as on signup: the phone is also a way to sign in."""
    monkeypatch.setattr(app_module.db, "update_phone", lambda *a: pytest.fail("an invalid phone was saved"))
    res = client.put("/api/profile/phone", json={"phone": phone})
    assert res.status_code == 422


def test_a_taken_phone_says_so(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "update_phone", lambda *a: (False, "מספר הטלפון כבר רשום למשתמש אחר"))
    res = client.put("/api/profile/phone", json={"phone": "050-1234567"})
    assert res.status_code == 500 and res.get_json()["error"] == "מספר הטלפון כבר רשום למשתמש אחר"


def test_saving_name_without_phone_keeps_the_phone(client, monkeypatch):
    """Editing details in "my family" no longer sends a phone. Without keeping
    it, every name change would delete it, and phone sign-in with it."""
    saved = []
    monkeypatch.setattr(app_module.db, "get_profile", lambda *a, **k: {"phone": "0501234567"})
    monkeypatch.setattr(app_module.db, "update_profile",
                        lambda uid, name, phone, workplace: saved.append(phone) or (True, None))
    res = client.put("/api/profile", json={"first_name": "ישראל", "last_name": "ישראלי"})
    assert res.status_code == 200 and saved == ["0501234567"]


# ─── Email ────────────────────────────────────────────────────────────────

@pytest.fixture
def email_flow(monkeypatch):
    calls = {"sign_in": [], "change": []}
    monkeypatch.setattr(app_module.db, "sign_in",
                        lambda e, p: calls["sign_in"].append((e, p)) or (None, None if p == "right" else "bad"))
    monkeypatch.setattr(app_module.db, "request_email_change",
                        lambda tok, new, redirect: calls["change"].append((tok, new, redirect)) or (True, None))
    return calls


def test_changing_the_email_asks_supabase_to_confirm(client, email_flow):
    res = client.put("/api/profile/email", json={"email": "New@Example.com", "current_password": "right"})
    assert res.status_code == 200 and res.get_json()["pending_email"] == "new@example.com"
    assert email_flow["sign_in"] == [("old@example.com", "right")]
    tok, new, redirect = email_flow["change"][0]
    assert tok == "tok" and new == "new@example.com" and redirect.endswith("/settings")


def test_the_wrong_password_changes_nothing(client, email_flow):
    res = client.put("/api/profile/email", json={"email": "new@example.com", "current_password": "wrong"})
    assert res.status_code == 403 and email_flow["change"] == []


@pytest.mark.parametrize("body,status", [
    ({"email": "not-an-email", "current_password": "right"}, 422),
    ({"email": "new@example.com", "current_password": ""}, 422),
    ({"email": "OLD@example.com", "current_password": "right"}, 422),
])
def test_bad_requests_send_nothing(client, email_flow, body, status):
    res = client.put("/api/profile/email", json=body)
    assert res.status_code == status and email_flow["change"] == []


def test_the_password_is_checked_against_the_current_email(client, email_flow, monkeypatch):
    """After a new email was confirmed, the session still remembers the old one,
    and the password check against it failed. The email is taken from Supabase,
    and the session is updated."""
    monkeypatch.setattr(app_module.db, "get_auth_email", lambda tok: ("confirmed@example.com", None))
    client.put("/api/profile/email", json={"email": "next@example.com", "current_password": "right"})
    assert email_flow["sign_in"] == [("confirmed@example.com", "right")]
    with client.session_transaction() as sess:
        assert sess["user_email"] == "confirmed@example.com"


def test_the_email_change_is_rate_limited():
    src = (_ROOT / "backend/app.py").read_text(encoding="utf-8")
    after = src[src.index('@app.route("/api/profile/email", methods=["PUT"])'):]
    assert '@limiter.limit("5 per minute")' in after[:after.index("\ndef ")]


# ─── The DB layer against Supabase ─────────────────────────────────────────

class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.content = b"x"

    def json(self):
        return self._body


@pytest.mark.parametrize("status,body,expected", [
    (200, {}, (True, None)),
    (422, {"error_code": "email_exists", "msg": "A user with this email address has already been registered"},
     (False, "המייל הזה כבר רשום בחשבון אחר")),
    (429, {"error_code": "over_email_send_rate_limit", "msg": "email rate limit exceeded"},
     (False, "נשלחו יותר מדי מיילים — נסו שוב בעוד כמה דקות")),
    (500, {"msg": "boom"}, (False, None)),
])
def test_supabase_answers_become_hebrew(monkeypatch, status, body, expected):
    sent = []
    monkeypatch.setattr(db, "_auth_user_request",
                        lambda method, tok, **kw: sent.append((method, kw)) or _Resp(status, body))
    assert db.request_email_change("tok", "new@example.com", "https://x/settings") == expected
    method, kw = sent[0]
    assert method == "put" and kw["json"] == {"email": "new@example.com"}
    assert kw["params"] == {"redirect_to": "https://x/settings"}


# ─── The screen ───────────────────────────────────────────────────────────

def test_the_account_box_has_email_and_phone_and_the_family_box_has_no_phone():
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    account = html[html.index('<p class="recurring-group-title">התחברות</p>'):html.index('<span class="acct-label">סיסמה</span>')]
    assert 'id="toggleEmailBtn"' in account and 'id="emailCurrentPassword"' in account
    assert 'id="togglePhoneBtn"' in account and 'id="newPhone"' in account
    profile_edit = html[html.index('id="profileEdit"'):html.index('id="saveProfileBtn"')]
    assert 'type="tel"' not in profile_edit
    js = (_ROOT / "frontend/static/js/settings.js").read_text(encoding="utf-8")
    submit = js[js.index("function submitProfile"):js.index("if (saveProfileBtn)")]
    assert "phone" not in submit
