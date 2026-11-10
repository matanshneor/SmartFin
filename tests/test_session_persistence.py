"""
Tests for staying signed in.

The requirement: whoever signed in stays signed in always, unless they sign out
themselves.

Two things can break this, and both are covered here:
1. Cookie expiry: it must be long, and the window must move forward with every
   use; otherwise an active user would be signed out at the end of the period.
2. Token refresh failure: the Supabase access token lives about an hour and is
   replaced automatically. Before, every refresh error cleared the session, so
   one network blip would sign out. Only a real rejection of the refresh token
   justifies signing out.
"""
import time
from datetime import timedelta

import pytest

from backend import app as app_module
from backend.app import app

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_real_refresh(monkeypatch):
    """Default: refresh isn't available, in a non-fatal way.

    Without this every request in this file went to the real GoTrue with a fake
    token: a real network call to production, inside a test marked unit. A test
    checking specific refresh behaviour overrides this itself."""
    monkeypatch.setattr(app_module.db, "refresh_session",
                        lambda _t: (None, "unit test", False))
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda _t: None)


# ─── Cookie settings ─────────────────────────────────────────────────────────

def test_an_active_session_is_not_interrupted():
    """This number applies only to an **abandoned** session: an active user pushes the
    expiry forward on every request (the next test), so 90 days doesn't sign out
    anyone who is using it.

    Ten years means a sold device, a lost phone or a browser on a shared computer
    stay signed in forever, and the user has no action that closes them."""
    assert app.permanent_session_lifetime >= timedelta(days=30), \
        "too short: whoever comes in once a month will be thrown out"
    assert app.permanent_session_lifetime <= timedelta(days=180), \
        "too long: an abandoned session never closes"


def test_changing_the_password_disconnects_other_devices():
    """Without this, changing the password did nothing to whoever is already signed
    in: the session renews itself, and the refresh token keeps replacing itself."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "backend/supabase_config.py").read_text(encoding="utf-8")
    fn = src[src.index("def update_password("):]
    fn = fn[:fn.index("\ndef ")]

    assert "auth/v1/logout" in fn, "a password change doesn't sign out any other device"
    assert '"scope": "others"' in fn, \
        "a global sign-out would also sign out whoever made the change"


def test_the_expiry_window_slides_with_every_request():
    """Without this even a long window would end eventually, regardless of use."""
    assert app.config["SESSION_REFRESH_EACH_REQUEST"] is True


def test_the_cookie_stays_hardened():
    """Persistence doesn't come at the expense of hardening: the cookie carries
    Supabase tokens."""
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


# ─── Telling a transient glitch from a real rejection ────────────────────────

class _FakeSession:
    access_token = "fresh-access"
    refresh_token = "fresh-refresh"
    expires_at = 9999999999


class _FakeResponse:
    session = _FakeSession()


@pytest.fixture
def client_with_expiring_token(monkeypatch):
    """A signed-in client whose token has already expired, so every request tries to
    refresh."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]            = "00000000-0000-0000-0000-000000000000"
            sess["user_name"]          = "בדיקה"
            sess["family_id"]          = None
            sess["access_token"]       = "stale"
            sess["refresh_token"]      = "some-refresh-token"
            sess["token_expires_at"]   = 0        # expired long ago
        yield c


def test_a_network_blip_does_not_sign_the_user_out(client_with_expiring_token, monkeypatch):
    """This is the heart: a transient glitch keeps the user signed in."""
    monkeypatch.setattr(app_module.db, "refresh_session",
                        lambda _t: (None, "temporary failure", False))

    client_with_expiring_token.get("/__test/noop")

    with client_with_expiring_token.session_transaction() as sess:
        assert sess.get("user_id"), "a network blip signed the user out"


def test_a_rejected_refresh_token_does_sign_the_user_out(client_with_expiring_token, monkeypatch):
    """Control: when the token is really rejected there is nothing to recover, and a
    clean sign-out is right."""
    monkeypatch.setattr(app_module.db, "refresh_session",
                        lambda _t: (None, "Invalid Refresh Token", True))

    client_with_expiring_token.get("/__test/noop")

    with client_with_expiring_token.session_transaction() as sess:
        assert not sess.get("user_id")


def test_a_rejection_while_the_access_token_still_works_does_not_sign_out(monkeypatch):
    """Supabase's rotation race: two workers, two parallel requests, and a refresh
    token rotated on every refresh. The request arriving second gets "already used"
    although the sign-in is fully alive: the refresh was simply already done by the
    other.

    What distinguishes a real rejection from a result of the race is the access
    token: as long as it is valid, there is something to keep using and the next
    request will try to refresh again."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]          = "00000000-0000-0000-0000-000000000000"
            sess["access_token"]     = "still-valid"
            sess["refresh_token"]    = "already-rotated-by-the-other-worker"
            # inside the early refresh window (120 seconds) but not yet expired
            sess["token_expires_at"] = time.time() + 60

        monkeypatch.setattr(app_module.db, "refresh_session",
                            lambda _t: (None, "Invalid Refresh Token: Already Used", True))

        response = c.get("/__test/noop")

        with c.session_transaction() as sess:
            assert sess.get("user_id"), "a token rotation race signed the user out"

        # And no less important: the failed request doesn't write a cookie. Had it
        # written, it would have overwritten the new valid token the other request
        # already saved.
        assert not any(h[0] == "Set-Cookie" and h[1].startswith("session=")
                       for h in response.headers), "a failed request overwrote the session cookie"


def _noop_view():
    """An authenticated page that doesn't touch the database.

    The tests here check cookies and tokens, not data. They used /settings as a
    convenient page, and that tied them by chance to real fetches: since a fetch
    failure is raised instead of swallowed (see DataUnavailable), this page fails
    openly with a fake token, and that set off failures in tests unrelated to the
    subject."""
    return "ok"


app.add_url_rule("/__test/noop", "test_noop", _noop_view)


def _session_writing_view():
    """A page that writes to the session while handling the request, as the dashboard
    does with recurring_synced. It exists to check that freezing the cookie holds
    against it too."""
    from flask import session as flask_session
    flask_session["touched_by_the_view"] = "1"
    return "ok"


app.add_url_rule("/__test/session-writer", "test_session_writer", _session_writing_view)


def test_freezing_the_cookie_survives_a_view_that_writes_to_the_session(
        client_with_expiring_token, monkeypatch):
    """A trap: cancelling the write is done specifically after the view. Had it been
    done before, any write to the session inside the view would bring the write
    back to life, and the cookie that would be written would no longer be
    permanent, that is, deleted when the browser closes. That is exactly the
    sign-out this mechanism is meant to prevent."""
    monkeypatch.setattr(app_module.db, "refresh_session",
                        lambda _t: (None, "temporary failure", False))

    response = client_with_expiring_token.get("/__test/session-writer")

    session_cookies = [h[1] for h in response.headers
                       if h[0] == "Set-Cookie" and h[1].startswith("session=")]
    assert not session_cookies, f"the cookie was written anyway: {session_cookies}"


def test_a_network_blip_does_not_overwrite_the_cookie(client_with_expiring_token, monkeypatch):
    """The same reasoning, on a transient glitch: we have nothing to save, so we don't
    touch the cookie."""
    monkeypatch.setattr(app_module.db, "refresh_session",
                        lambda _t: (None, "temporary failure", False))

    response = client_with_expiring_token.get("/__test/noop")

    assert not any(h[0] == "Set-Cookie" and h[1].startswith("session=")
                   for h in response.headers)


def test_a_successful_refresh_stores_the_rotated_token(client_with_expiring_token, monkeypatch):
    """Supabase rotates the refresh token on every refresh. If we don't save the new
    one, the next refresh will fail and the user will be signed out after about an
    hour."""
    monkeypatch.setattr(app_module.db, "refresh_session",
                        lambda _t: (_FakeResponse(), None, False))

    client_with_expiring_token.get("/__test/noop")

    with client_with_expiring_token.session_transaction() as sess:
        assert sess["access_token"]  == "fresh-access"
        assert sess["refresh_token"] == "fresh-refresh"
        assert sess["user_id"]


# ─── Deliberate sign-out ─────────────────────────────────────────────────────

def test_logging_out_really_ends_the_session(client_with_expiring_token):
    """Deliberate sign-out is the only way out, so it must work."""
    client_with_expiring_token.post("/logout")

    with client_with_expiring_token.session_transaction() as sess:
        assert not sess.get("user_id")
        assert not sess.get("refresh_token")


def test_logging_out_works_even_while_a_refresh_is_failing(
        client_with_expiring_token, monkeypatch):
    """The cookie-freezing trap, in the reverse direction.

    The freeze is meant not to overwrite a valid token with a dead token when a
    refresh fails. But it applies to the whole response, including the /logout
    response. Whoever tapped "התנתק" exactly when the refresh failed got a message
    that they signed out, saw the device cookies deleted, and stayed fully signed
    in: their session cookie was never touched, and the next request brought them
    back into the app.

    Deliberate sign-out is the only way out, so it must beat any optimisation."""
    monkeypatch.setattr(app_module.db, "refresh_session",
                        lambda _t: (None, "temporary failure", False))

    # POST and not GET: <img src=".../logout"> on another site signed a visitor
    # out of their account, because SameSite=Lax sends a cookie on a GET navigation.
    response = client_with_expiring_token.post("/logout")

    cleared = [h[1] for h in response.headers
               if h[0] == "Set-Cookie" and h[1].startswith("session=")]
    assert cleared, "the session cookie wasn't deleted: the user stayed signed in"
    assert "1970" in cleared[0]

    with client_with_expiring_token.session_transaction() as sess:
        assert not sess.get("user_id")


# ─── The device's memory: don't go back to the marketing page ────────────────
#
# The landing page is meant for those who don't know the app. Whoever already
# signed in from this device should reach the app, and if the session ended, the
# sign-in form, not a marketing page.

@pytest.fixture
def guest():
    app.config["TESTING"] = True
    return app.test_client()


def test_signing_in_marks_the_device_as_known(guest, monkeypatch):
    """The entry point of the whole mechanism: without it nothing else ever runs."""
    class _Session:
        access_token, refresh_token, expires_at = "a", "r", 9999999999

    class _User:
        id, email = "00000000-0000-0000-0000-000000000000", "dana@example.com"

    class _SignIn:
        session, user = _Session(), _User()

    monkeypatch.setattr(app_module.db, "sign_in",        lambda e, p: (_SignIn(), None))
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(app_module.db, "log_login_event", lambda: None)
    # fetch_profile and not get_profile: the sign-in route needs to know whether
    # the fetch *failed*, not only what it returned; see tests/test_profile_read_failure.py
    monkeypatch.setattr(app_module.db, "fetch_profile",
                        lambda uid: ({"name": "דנה", "avatar_initial": "ד",
                                      "family_id": "11111111-1111-1111-1111-111111111111"}, True))

    response = guest.post("/login", data={"identifier": "dana@example.com",
                                          "password": "whatever"})

    cookies = [h[1] for h in response.headers if h[0] == "Set-Cookie"]
    assert any(c.startswith("sf_returning=1") for c in cookies)
    assert any(c.startswith("sf_last_id=dana%40example.com") or
               c.startswith("sf_last_id=dana@example.com") for c in cookies)
    # The identifier sits on the device, hence HttpOnly: JS has no use for it, and
    # XSS shouldn't read an email address from it.
    assert all("HttpOnly" in c for c in cookies if c.startswith("sf_"))


def test_a_first_time_visitor_gets_the_landing_page(guest):
    response = guest.get("/")

    assert response.status_code == 200
    assert "lp-hero" in response.get_data(as_text=True)


def test_a_known_device_skips_the_landing_page(guest):
    """The heart of the request: a device that already signed in once doesn't see the
    marketing page again."""
    guest.set_cookie("sf_returning", "1")

    response = guest.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_the_landing_page_stays_reachable_on_purpose(guest):
    """The "חזרה" button on the sign-in form points here. Without this escape it would
    send the known device back to /login, that is, a loop."""
    guest.set_cookie("sf_returning", "1")

    response = guest.get("/?intro=1")

    assert response.status_code == 200
    assert "lp-hero" in response.get_data(as_text=True)


def test_the_router_is_never_cached(guest):
    """The response at the root derives from the cookies. A cached response means a
    marketing page for a signed-in user, or the other way round."""
    for path in ("/", "/login"):
        response = guest.get(path)
        assert response.headers.get("Cache-Control") == "no-store", path
        assert "Cookie" in response.headers.get("Vary", ""), path


def test_the_login_form_remembers_who_you_are(guest):
    guest.set_cookie("sf_last_id", "someone@example.com")

    body = guest.get("/login").get_data(as_text=True)

    assert 'value="someone@example.com"' in body


def test_logging_out_brings_you_back_to_the_landing_page(client_with_expiring_token):
    """Deliberate sign-out resets everything. It is the only way out, and whoever
    chooses it gets exactly what a guest gets: the landing page, and from there a
    sign-in button.

    This is also what gives the device mark meaning: after signing out it is
    deleted, so a marked device with no session is necessarily a case where the
    sign-in ended by itself."""
    response = client_with_expiring_token.post("/logout")

    assert response.headers["Location"].endswith("/")

    cookies = [h[1] for h in response.headers if h[0] == "Set-Cookie"]
    for name in ("sf_last_id", "sf_returning"):
        assert any(c.startswith(f"{name}=") and "Expires=Thu, 01 Jan 1970" in c
                   for c in cookies), f"{name} wasn't deleted on sign-out"

    # and the real test: the next request really gets the landing page
    landed = client_with_expiring_token.get("/")
    assert landed.status_code == 200
    assert "lp-hero" in landed.get_data(as_text=True)
