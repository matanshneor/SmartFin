"""Staying signed in even when a response gets lost (Matan, 13.10).

In the logs: 11 sign-outs in two days, all "Invalid Refresh Token: Already Used".
The token was rotated in a request whose response didn't reach the phone, and the
cookie kept the old one. Now the current token is also saved in the database
(web_sessions), by a random identifier in the cookie."""
import time

import pytest

from backend import app as app_module
from backend.app import app

pytestmark = pytest.mark.unit


class _Sess:
    def __init__(self, access, refresh):
        self.access_token, self.refresh_token, self.expires_at = access, refresh, 9999999999


class _Resp:
    def __init__(self, access, refresh):
        self.session = _Sess(access, refresh)


@pytest.fixture
def store(monkeypatch):
    rows = {}
    calls = []
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda _t: None)
    monkeypatch.setattr(app_module.db, "web_session_get", lambda sid: rows.get(sid))

    def rotate(sid, old, access, refresh, exp):
        calls.append(("rotate", sid, old))
        if rows.get(sid, {}).get("refresh_token") == old:
            rows[sid] = {"access_token": access, "refresh_token": refresh, "expires_at": exp}
    monkeypatch.setattr(app_module.db, "web_session_rotate", rotate)
    monkeypatch.setattr(app_module.db, "web_session_close", lambda sid: calls.append(("close", sid)))
    return rows, calls


def _client(refresh="old-refresh", expires=0, sid="sid-1"):
    app.config["TESTING"] = True
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = "00000000-0000-0000-0000-000000000000"
        sess["family_id"] = None
        sess["access_token"] = "stale"
        sess["refresh_token"] = refresh
        sess["token_expires_at"] = expires
        if sid:
            sess["sid"] = sid
    return c


def _noop():
    return "ok"


app.add_url_rule("/__test/stay", "test_stay", _noop)


def test_a_lost_response_no_longer_signs_out(store, monkeypatch):
    """The heart: the token in the cookie was already invalidated, but the current
    one is in the database: take it."""
    rows, _ = store
    rows["sid-1"] = {"access_token": "new-access", "refresh_token": "new-refresh",
                     "expires_at": time.time() + 3000}
    monkeypatch.setattr(app_module.db, "refresh_session",
                        lambda _t: (None, "Invalid Refresh Token: Already Used", True))
    c = _client()
    c.get("/__test/stay")
    with c.session_transaction() as sess:
        assert sess.get("user_id"), "signed out although the current token is in the database"
        assert sess["refresh_token"] == "new-refresh" and sess["access_token"] == "new-access"


def test_recovered_tokens_that_also_expired_are_refreshed(store, monkeypatch):
    rows, _ = store
    rows["sid-1"] = {"access_token": "old-too", "refresh_token": "newer-refresh", "expires_at": 0}
    used = []
    monkeypatch.setattr(app_module.db, "refresh_session",
                        lambda t: (used.append(t) or _Resp("fresh-access", "fresh-refresh"), None, False))
    c = _client()
    c.get("/__test/stay")
    assert used == ["newer-refresh"], "refreshed with the invalidated token from the cookie"
    with c.session_transaction() as sess:
        assert sess["refresh_token"] == "fresh-refresh"
    assert rows["sid-1"]["refresh_token"] == "fresh-refresh"


def test_every_refresh_is_copied_to_the_store(store, monkeypatch):
    rows, calls = store
    rows["sid-1"] = {"access_token": "stale", "refresh_token": "old-refresh", "expires_at": 0}
    monkeypatch.setattr(app_module.db, "refresh_session",
                        lambda _t: (_Resp("a2", "r2"), None, False))
    _client().get("/__test/stay")
    assert ("rotate", "sid-1", "old-refresh") in calls
    assert rows["sid-1"]["refresh_token"] == "r2"


def test_a_parallel_refresh_by_another_worker_is_picked_up(store, monkeypatch):
    """Our refresh was rejected because another worker refreshed a moment before us,
    and saved to the database."""
    rows, _ = store
    rows["sid-1"] = {"access_token": "stale", "refresh_token": "old-refresh", "expires_at": 0}

    def refresh(_t):
        rows["sid-1"] = {"access_token": "other-access", "refresh_token": "other-refresh",
                         "expires_at": time.time() + 3000}
        return None, "Invalid Refresh Token: Already Used", True
    monkeypatch.setattr(app_module.db, "refresh_session", refresh)
    c = _client()
    c.get("/__test/stay")
    with c.session_transaction() as sess:
        assert sess.get("user_id") and sess["refresh_token"] == "other-refresh"


def test_a_truly_dead_session_still_signs_out(store, monkeypatch):
    rows, _ = store
    rows["sid-1"] = {"access_token": "stale", "refresh_token": "old-refresh", "expires_at": 0}
    monkeypatch.setattr(app_module.db, "refresh_session",
                        lambda _t: (None, "Invalid Refresh Token: Revoked", True))
    c = _client()
    c.get("/__test/stay")
    with c.session_transaction() as sess:
        assert not sess.get("user_id")


def test_an_old_cookie_without_sid_works_as_before(store, monkeypatch):
    monkeypatch.setattr(app_module.db, "refresh_session",
                        lambda _t: (_Resp("a2", "r2"), None, False))
    c = _client(sid=None)
    c.get("/__test/stay")
    with c.session_transaction() as sess:
        assert sess["refresh_token"] == "r2"


def test_a_fresh_token_does_not_touch_the_store(store, monkeypatch):
    """A normal request (valid token): no trip to the database: don't slow every page."""
    monkeypatch.setattr(app_module.db, "web_session_get",
                        lambda sid: pytest.fail("a redundant trip to the database on every request"))
    _client(expires=time.time() + 3000).get("/__test/stay")


def test_logout_removes_the_stored_tokens(store):
    _, calls = store
    _client(expires=time.time() + 3000).post("/logout")
    assert ("close", "sid-1") in calls


def test_signing_in_opens_a_stored_copy(monkeypatch):
    opened = []
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda _t: None)
    monkeypatch.setattr(app_module.db, "log_login_event", lambda *a: None)
    monkeypatch.setattr(app_module.db, "fetch_profile",
                        lambda uid: ({"name": "מתן", "family_id": "fam"}, True))
    monkeypatch.setattr(app_module.db, "web_session_open", lambda *a: opened.append(a))
    app.config["TESTING"] = True
    with app.test_request_context("/login"):
        assert app_module._begin_session("u1", "m@example.com", "acc", "ref", 123) is None
        from flask import session
        sid = session["sid"]
    assert len(sid) >= 40
    assert opened == [(sid, "u1", "acc", "ref", 123)]


def test_the_database_only_sees_a_hash_of_the_session_id():
    from backend import supabase_config as db
    src = open(db.__file__, encoding="utf-8").read()
    body = src[src.index("def web_session_open"):src.index("def web_session_close")]
    assert body.count('"p_sid_hash": _sid_hash(sid)') == 3
    mig = open(app_module.os.path.join(app_module.os.path.dirname(db.__file__),
                                       "supabase/migrations/20261013090000_web_sessions.sql"),
               encoding="utf-8").read()
    assert "enable row level security" in mig and "security definer" in mig
    assert "set search_path = ''" in mig and "on conflict (sid_hash) do nothing" in mig
