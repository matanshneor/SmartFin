"""Looking up an email by phone requires a secret only the server knows (Matan,
10.11.2026).

The `email_for_phone` function is open to anon because it runs before sign-in.
Without the secret, anyone holding the anon key could walk through phone numbers
and collect users' emails. Two parts:

  · Unit: the server sends the secret, and without a secret it doesn't ask at all.
  · Against the real database: without the secret, or with a wrong one, the
    database returns nothing; with the right secret it returns. And a control:
    the old version, without a secret, doesn't exist.
"""
import os
import secrets as _secrets

import pytest

from backend import supabase_config as db


# ─── Unit ─────────────────────────────────────────────────────────────────────

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


# ─── Against the real database ───────────────────────────────────────────────

def _anon_lookup(params):
    """A direct call to the RPC with the anon key only: what an attacker holding the
    key would do, without going through the site."""
    db.set_auth_token("")
    return db.get_client().rpc("email_for_phone", params).execute().data


@pytest.fixture
def phone_of_a(family_a):
    """A random phone for test account A (the test accounts have no phone)."""
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
        pytest.skip("no LOGIN_LOOKUP_SECRET in the local .env")
    try:
        db.set_auth_token("")
        assert db.get_email_by_phone(phone_of_a) == _test_pair["a"]["email"]
    finally:
        db.set_auth_token(family_a["token"])


def test_old_unguarded_version_is_gone(phone_of_a, family_a):
    """The version without a secret was the hole itself. If it exists, everything
    else is worth nothing."""
    from postgrest.exceptions import APIError
    try:
        with pytest.raises(APIError):
            _anon_lookup({"p_phone": phone_of_a})
    finally:
        db.set_auth_token(family_a["token"])
