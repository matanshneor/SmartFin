"""Whoever's family changed behind their back doesn't get stuck on an error page.

The session remembers the family from the moment of sign-in. Whoever was removed
from the family (or left it on another device) keeps sending the old id, and the
database no longer answers for it: almost every screen became "we couldn't load
the data. Try refreshing", and refreshing didn't help. Until they signed out and
in again.

There is no extra check in normal use. Only when the query has already failed,
before the error page, one question is asked: is the user still in the family in
the session?
"""
import json

import pytest

from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_ME  = "22222222-2222-2222-2222-222222222222"
_OLD = "11111111-1111-1111-1111-111111111111"
_NEW = "33333333-3333-3333-3333-333333333333"


def _broken(*a, **k):
    raise db.DataUnavailable("family")


@pytest.fixture
def client(monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    # The route's query fails, as it fails for whoever was removed
    monkeypatch.setattr(db, "get_family_members", _broken)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _OLD
        yield c


def _profile_in(monkeypatch, family_id, ok=True):
    calls = []
    def fetch(uid):
        calls.append(uid)
        return ({"id": uid, "family_id": family_id,
                 "families": {"name": "המשפחה שלי"}} if ok else None), ok
    monkeypatch.setattr(db, "fetch_profile", fetch)
    return calls


def _session(client):
    with client.session_transaction() as sess:
        return dict(sess)


def test_an_api_call_says_the_family_changed_and_fixes_the_session(client, monkeypatch):
    _profile_in(monkeypatch, _NEW)

    r = client.get("/api/family/members")

    assert r.status_code == 409
    assert r.get_json()["family_changed"] is True
    assert _session(client)["family_id"] == _NEW


def test_a_page_goes_home_with_a_notice(client, monkeypatch):
    _profile_in(monkeypatch, _NEW)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(db, "fetch_month_page", _broken)    # the month page of someone who was removed

    r = client.get("/month")

    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    s = _session(client)
    assert s["family_id"] == _NEW
    assert "המשפחה שלי" in s["sf_notice"]


def test_the_same_family_is_an_ordinary_failure(client, monkeypatch):
    """Control: a real failure stays a failure: no redirect and the session isn't touched."""
    _profile_in(monkeypatch, _OLD)

    r = client.get("/api/family/members")

    assert r.status_code == 503
    assert _session(client)["family_id"] == _OLD


def test_a_failed_profile_read_changes_nothing(client, monkeypatch):
    """"Couldn't check" isn't "the family changed"."""
    _profile_in(monkeypatch, None, ok=False)

    r = client.get("/api/family/members")

    assert r.status_code == 503
    assert _session(client)["family_id"] == _OLD


def test_nothing_is_checked_when_nothing_failed(client, monkeypatch):
    """A promise to Matan: in normal use there isn't a single extra query."""
    calls = _profile_in(monkeypatch, _NEW)
    monkeypatch.setattr(db, "get_family_members", lambda fid: [])

    assert client.get("/api/family/members").status_code == 200
    assert calls == []


def test_the_notice_is_shown_once():
    """The message sits in the session until a page shows it, and then disappears."""
    from flask import render_template, session
    with app.test_request_context():
        session["sf_notice"] = "המשפחה שלך השתנתה"
        first = render_template("_notice.html")
        second = render_template("_notice.html")

    assert 'id="sfNotice"' in first
    assert json.loads(first[first.index(">", first.index("sfNotice")) + 1:first.index("</script>")]) \
        == "המשפחה שלך השתנתה"
    assert 'id="sfNotice"' not in second


def test_every_page_includes_the_notice():
    base = open("frontend/templates/base.html", encoding="utf-8").read()

    assert '{% include "_notice.html" %}' in base
