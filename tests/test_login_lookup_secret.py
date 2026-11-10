"""מציאת מייל לפי טלפון דורשת סוד שרק השרת מכיר (מתן, 10.11.2026).

הפונקציה ‎email_for_phone‎ פתוחה ל-anon כי היא רצה לפני ההתחברות. בלי
הסוד, כל מי שמחזיק את מפתח ה-anon היה יכול לעבור על מספרי טלפון ולאסוף
את המיילים של המשתמשים. שני חלקים:

  · יחידה — השרת שולח את הסוד, ובלי סוד הוא לא שואל בכלל.
  · מול המסד האמיתי — בלי הסוד, או עם סוד שגוי, המסד לא מחזיר כלום;
    עם הסוד הנכון הוא מחזיר. ובקרת-נגד: הגרסה הישנה, בלי סוד, לא קיימת.
"""
import os
import secrets as _secrets

import pytest

from backend import supabase_config as db


# ─── יחידה ────────────────────────────────────────────────────────────────────

class _RecordingClient:
    def __init__(self):
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))

        class _Result:
            data = "someone@example.com"

        class _Query:
            def execute(self_inner):
                return _Result()

        return _Query()


@pytest.mark.unit
def test_lookup_sends_the_secret(monkeypatch):
    client = _RecordingClient()
    monkeypatch.setattr(db, "get_client", lambda: client)
    monkeypatch.setenv("LOGIN_LOOKUP_SECRET", "s3cret")

    assert db.get_email_by_phone("0501234567") == "someone@example.com"
    assert client.calls == [("email_for_phone",
                             {"p_phone": "0501234567", "p_secret": "s3cret"})]


@pytest.mark.unit
def test_lookup_without_secret_does_not_ask(monkeypatch):
    client = _RecordingClient()
    monkeypatch.setattr(db, "get_client", lambda: client)
    monkeypatch.delenv("LOGIN_LOOKUP_SECRET", raising=False)

    assert db.get_email_by_phone("0501234567") is None
    assert client.calls == []


# ─── מול המסד האמיתי ─────────────────────────────────────────────────────────

def _anon_lookup(params):
    """קריאה ישירה ל-RPC עם מפתח ה-anon בלבד — מה שתוקף שמחזיק את המפתח
    היה עושה, בלי לעבור דרך האתר."""
    db.set_auth_token("")
    return db.get_client().rpc("email_for_phone", params).execute().data


@pytest.fixture
def phone_of_a(family_a):
    """טלפון אקראי לחשבון הבדיקה א' (לחשבונות הבדיקה אין טלפון)."""
    phone = "059" + "".join(_secrets.choice("0123456789") for _ in range(7))
    db.set_auth_token(family_a["token"])
    ok, err = db.update_phone(family_a["user_id"], phone)
    assert ok, err
    yield phone


def test_anon_without_secret_gets_nothing(phone_of_a, family_a):
    try:
        assert _anon_lookup({"p_phone": phone_of_a, "p_secret": None}) is None
        assert _anon_lookup({"p_phone": phone_of_a, "p_secret": ""}) is None
        assert _anon_lookup({"p_phone": phone_of_a, "p_secret": "wrong"}) is None
    finally:
        db.set_auth_token(family_a["token"])


def test_server_with_secret_finds_the_email(phone_of_a, family_a, _test_pair):
    if not os.environ.get("LOGIN_LOOKUP_SECRET"):
        pytest.skip("אין LOGIN_LOOKUP_SECRET ב-.env המקומי")
    try:
        db.set_auth_token("")
        assert db.get_email_by_phone(phone_of_a) == _test_pair["a"]["email"]
    finally:
        db.set_auth_token(family_a["token"])


def test_old_unguarded_version_is_gone(phone_of_a, family_a):
    """הגרסה בלי סוד הייתה הפרצה עצמה. אם היא קיימת, כל השאר לא שווה כלום."""
    from postgrest.exceptions import APIError
    try:
        with pytest.raises(APIError):
            _anon_lookup({"p_phone": phone_of_a})
    finally:
        db.set_auth_token(family_a["token"])
