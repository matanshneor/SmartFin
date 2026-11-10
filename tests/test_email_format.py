"""
The email address wasn't checked at all.

Both auth forms have `novalidate` (on purpose, so errors are in Hebrew and in the
app's design rather than an English browser bubble), and the server checked
nothing. So whoever typed `israel@gmial` or `israel@gmail` signed up
successfully.

And they noticed nothing at that moment. They found out a month later, when they
tried to reset a password and the link was sent to an address that doesn't
exist: they have no way out, and the app has no route to fix an email address.
The database already has such an account from July 2026: `notanemail@nodottld`.
"""
import pytest

from backend import app as app_module
from backend.app import app, limiter, _looks_like_email

pytestmark = pytest.mark.unit


@pytest.fixture
def anon():
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(autouse=True)
def _fresh_limits():
    """flask_limiter's counters live in process memory and don't expire within one
    run; without a reset, a test here burns the quota of a test in another file."""
    limiter.reset()
    yield
    limiter.reset()


def _signup(client, email, **extra):
    form = {
        "first_name": "ישראל", "last_name": "ישראלי",
        "email": email, "phone": "050-1234567",
        "password": "sixchars", "password_confirm": "sixchars",
    }
    form.update(extra)
    return client.post("/signup", data=form)


# ─── The address that really signed up ──────────────────────────────────────

def test_the_address_that_actually_got_through_is_now_rejected(anon, monkeypatch):
    """`notanemail@nodottld`: not a made-up example, but an existing account in the
    database."""
    called = []
    monkeypatch.setattr(app_module.db, "sign_up",
                        lambda *a, **k: (called.append(a) or (None, "לא אמור להיקרא")))

    res = _signup(anon, "notanemail@nodottld")

    assert "כתובת המייל אינה תקינה" in res.get_data(as_text=True)
    assert called == [], "an account was created despite an invalid address"


def test_a_missing_suffix_is_rejected(anon, monkeypatch):
    """The common mistake: typing `israel@gmail` and forgetting `.com`."""
    monkeypatch.setattr(app_module.db, "sign_up",
                        lambda *a, **k: pytest.fail("signed up with an address without a TLD"))

    assert "כתובת המייל אינה תקינה" in _signup(anon, "israel@gmail").get_data(as_text=True)


def test_the_form_comes_back_with_the_signup_tab_open(anon):
    """Control: an error that sends the user back to the sign-in tab looks as if the
    signup succeeded."""
    html = _signup(anon, "israel@gmail").get_data(as_text=True)

    assert 'id="signupPanel"' in html
    assert "כתובת המייל אינה תקינה" in html


# ─── What must keep working ─────────────────────────────────────────────────

def test_a_real_address_still_reaches_supabase(anon, monkeypatch):
    """Control, and the most important one here: a check too strict blocks real
    signups, and that is worse than the bug."""
    seen = []
    monkeypatch.setattr(app_module.db, "sign_up",
                        lambda email, *a, **k: (seen.append(email) or (None, "stop")))

    _signup(anon, "israel@gmail.com")

    assert seen == ["israel@gmail.com"]


@pytest.mark.parametrize("email", [
    "israel@gmail.com",
    "first.last@company.co.il",      # an Israeli subdomain, the most common here
    "user+tag@gmail.com",            # a Gmail tag
    "user_name@my-domain.org",
    "UPPER@Example.COM",
    "a@b.co",
    "ישראל@gmail.com",               # a local part in Hebrew: legal
])
def test_valid_addresses_pass(email):
    assert _looks_like_email(email), f"{email} was rejected by mistake"


@pytest.mark.parametrize("email", [
    "notanemail@nodottld",
    "israel@gmail",
    "israel",
    "@gmail.com",
    "israel@",
    "israel@.com",
    "israel@gmail..com",
    "israel@@gmail.com",
    "israel isra@gmail.com",         # a space
    "a@b.c",                         # a one-character TLD doesn't exist
    "",
    "a@" + "b" * 250 + ".com",       # over 254 characters
])
def test_invalid_addresses_fail(email):
    assert not _looks_like_email(email), f"{email} passed by mistake"


# ─── "Forgot password" ──────────────────────────────────────────────────────

def test_a_broken_address_is_told_so_instead_of_pretending(anon, monkeypatch):
    """This route always answers "sent", on purpose, so as not to reveal which
    addresses are registered. But an invalid format isn't information about who
    is registered, and without the check, whoever typed a broken address waited
    for a link that was sent nowhere."""
    monkeypatch.setattr(app_module.db, "send_reset_email",
                        lambda *a, **k: pytest.fail("an email was sent to an invalid address"))

    res = anon.post("/api/auth/forgot", json={"email": "israel@gmail"})

    assert res.status_code == 422
    assert res.get_json()["error"] == "כתובת המייל אינה תקינה"


def test_a_valid_address_still_gets_the_neutral_answer(anon, monkeypatch):
    """Control: the secrecy is kept: a valid address always gets "sent", whether
    it is registered or not."""
    # The real signature is `(ok, error)`. The double returned `None`, because
    # the route ignored the result, and that is exactly what let a send failure
    # look like success.
    monkeypatch.setattr(app_module.db, "send_reset_email", lambda *a, **k: (True, None))

    res = anon.post("/api/auth/forgot", json={"email": "nobody@example.com"})

    assert res.status_code == 200
    assert res.get_json() == {"status": "ok"}


# ─── A send failure is said, not swallowed ──────────────────────────────────
#
# `db.send_reset_email` builds `(ok, error)` and nobody read it. Supabase's email
# provider is limited to two emails **an hour, for the whole project**, so the
# third in an hour got "we sent you a link" and received nothing. Email
# confirmation is off, so they have no other recovery route, and their account
# is locked. Nothing was logged and nothing reached Sentry.

def test_a_failed_send_is_not_reported_as_sent(anon, monkeypatch):
    """The heart: never promise an email that didn't go out."""
    monkeypatch.setattr(app_module.db, "send_reset_email",
                        lambda *a, **k: (False, "smtp down"))

    res = anon.post("/api/auth/forgot", json={"email": "someone@example.com"})

    assert res.status_code == 503
    assert res.get_json().get("status") != "ok"


def test_hitting_the_providers_limit_says_to_try_again(anon, monkeypatch):
    """A rate limit is a temporary state, and the user deserves to know it is worth
    waiting, not that their account doesn't exist."""
    monkeypatch.setattr(app_module.db, "send_reset_email",
                        lambda *a, **k: (False, "email rate limit exceeded"))

    res = anon.post("/api/auth/forgot", json={"email": "someone@example.com"})

    assert "בעוד כמה דקות" in res.get_json()["error"]


def test_a_failed_send_reaches_sentry(anon, monkeypatch, caplog):
    """`warning` is a breadcrumb in Sentry, not an event, and Railway's log gets
    wiped and nobody reads it. Without `error` there is no way to know this is
    happening."""
    import logging
    monkeypatch.setattr(app_module.db, "send_reset_email",
                        lambda *a, **k: (False, "smtp down"))

    with caplog.at_level(logging.ERROR):
        anon.post("/api/auth/forgot", json={"email": "someone@example.com"})

    assert any(r.levelno >= logging.ERROR for r in caplog.records), \
        "the failure isn't logged at a level that produces an event"


def test_a_failure_does_not_reveal_whether_the_address_is_registered(anon, monkeypatch):
    """Control: the message is identical for a registered and an unregistered
    address; otherwise the failure itself becomes an oracle."""
    monkeypatch.setattr(app_module.db, "send_reset_email",
                        lambda *a, **k: (False, "smtp down"))

    a = anon.post("/api/auth/forgot", json={"email": "known@example.com"}).get_json()
    b = anon.post("/api/auth/forgot", json={"email": "unknown@example.com"}).get_json()

    assert a == b
