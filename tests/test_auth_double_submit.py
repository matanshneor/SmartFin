"""
Tests for double taps on the sign-in and signup forms.

This isn't a theoretical bug: the login_events table shows almost every sign-in
recorded twice, a second apart. The reason is that these two forms were the only
ones in the app without a button lock, and in the app installed on a phone there
isn't even a browser spinner, so after a tap the screen doesn't respond, and the
user taps again.

The second tap burns into the quota of 10 a minute, and crossing the quota
landed the user on error.html, which has only "back to the home page": no back
button, no form, and none of what they typed.
"""
from pathlib import Path

import pytest

from backend import app as app_module
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def anon(monkeypatch):
    app.config["TESTING"] = True
    # What is tested here is the behaviour after a failure and rate limiting,
    # not the sign-in itself. Without this fake every run sent 12 real sign-in
    # attempts to production Supabase, and burned the sign-in quota of other tests.
    monkeypatch.setattr(app_module.db, "sign_in", lambda e, p: (None, "invalid"))
    monkeypatch.setattr(app_module.db, "get_email_by_phone", lambda p: None)
    return app.test_client()


@pytest.fixture(autouse=True)
def _clean_rate_limit_counters():
    """flask_limiter's counters live in process memory and don't expire within the
    test run. Without this cleanup the tests here, which exhaust the quota on
    purpose, break every other test that signs in after them, and it looks like
    a bug somewhere else entirely."""
    limiter.reset()
    yield
    limiter.reset()


# ─── The lock in the browser ────────────────────────────────────────────────

def test_both_auth_forms_lock_their_submit_button():
    js = (_ROOT / "frontend/static/js/login.js").read_text(encoding="utf-8")

    for panel in ("loginPanel", "signupPanel"):
        assert panel in js, f"{panel} doesn't get a submit lock"
    assert "disabled = true" in js


def test_the_lock_can_always_be_released():
    """A lock without a release is worse than the bug: a dead form you can't get
    out of. The two release paths: a time valve, and coming back from the
    browser's memory."""
    js = (_ROOT / "frontend/static/js/login.js").read_text(encoding="utf-8")

    assert "setTimeout(release" in js, "no safety valve to release the button"
    assert "pageshow" in js, "coming back with 'back' would leave the button locked"


# ─── Rate limiting no longer leads to a dead end ────────────────────────────

def _exhaust_login_limit(client):
    """The limit is 10 a minute; the 11th request is blocked."""
    last = None
    for _ in range(12):
        last = client.post("/login", data={"identifier": "x@y.z", "password": "nope"})
        if last.status_code == 429:
            return last
    return last


def test_hitting_the_rate_limit_returns_the_login_form_not_a_dead_end(anon):
    response = _exhaust_login_limit(anon)

    assert response.status_code == 429, "the limit wasn't enforced: the test tested nothing"
    body = response.get_data(as_text=True)
    assert 'id="loginPanel"' in body, "the user was thrown out of the form instead of returning to it"
    assert "יותר מדי ניסיונות" in body
    assert "חזור לדף הראשי" not in body, "the error page with no way out is still served"


def test_the_rate_limited_form_still_remembers_who_you_are(anon):
    """Someone blocked after a double tap shouldn't have to type their email again."""
    anon.set_cookie("sf_last_id", "dana@example.com")

    response = _exhaust_login_limit(anon)

    assert 'value="dana@example.com"' in response.get_data(as_text=True)
