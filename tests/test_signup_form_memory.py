"""
Two bugs on the sign-in screen, both hurting whoever signs up for the first time.

**B3: the form is wiped.** Every sign-up error wiped all six fields, including the
invite code. Whoever got a code on WhatsApp and typed too short a password was left
without the code and without the details, and had to go back to the chat and
search. That is the complete opposite of the sign-in side, where there is an
explicit comment about keeping the identifier "so they don't have to type it again
after a password mistake".

**B4: every failure is called "wrong password".** db.sign_in catches every
exception and returns its text, and all results were mapped to one message. The
case that turns this from a nuisance into a trap: if email confirmation is ever
enabled, every new user is locked out permanently: signs up, gets "כעת ניתן
להתחבר", and gets "wrong password" forever.
"""
import pytest

from backend import app as app_module
from backend.app import app, limiter, _login_error

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _fresh_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def anon(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "get_email_by_phone", lambda p: None)
    return app.test_client()


_FORM = {
    "first_name": "ישראל", "last_name": "ישראלי",
    "email": "israel@example.com", "phone": "050-1234567",
    "invite_code": "K4F2QX",
    "password": "sixchars", "password_confirm": "sixchars",
}


def _signup(client, **over):
    form = dict(_FORM)
    form.update(over)
    return client.post("/signup", data=form).get_data(as_text=True)


# ─── B3: what was typed comes back ──────────────────────────────────────────

@pytest.mark.parametrize("broken,why", [
    ({"email": "israel@gmail"},        "מייל בלי סיומת"),
    ({"phone": "1"},                   "טלפון פסול"),
    ({"password_confirm": "other123"}, "סיסמאות לא תואמות"),
    ({"password": "abc"},              "סיסמה קצרה"),
])
def test_a_failed_signup_keeps_what_was_typed(anon, monkeypatch, broken, why):
    monkeypatch.setattr(app_module.db, "sign_up",
                        lambda *a, **k: pytest.fail("shouldn't reach the server"))

    html = _signup(anon, **broken)

    # the fields we didn't touch come back as typed
    assert 'value="ישראל"' in html, why
    assert 'value="ישראלי"' in html, why
    for field, typed in broken.items():
        if field.startswith("password"):
            continue
        # and the wrong field comes back too: you fix it, not type everything again
        assert f'value="{typed}"' in html, f"{why}: {field} didn't come back"


def test_the_invite_code_survives_too(anon, monkeypatch):
    """This is the worst of all: the code came on WhatsApp, and without it the user
    opens a new family instead of joining the existing one, and splits the family
    in two."""
    monkeypatch.setattr(app_module.db, "sign_up", lambda *a, **k: (None, "x"))

    html = _signup(anon, password="abc")

    assert 'value="K4F2QX"' in html


def test_an_existing_email_also_keeps_the_form(anon, monkeypatch):
    """This error comes from the server and not the local validation: another path."""
    monkeypatch.setattr(app_module.db, "sign_up",
                        lambda *a, **k: (None, "User already registered"))

    html = _signup(anon)

    # The message doesn't specify **what** already exists: a taken email and a
    # taken phone get the same answer; otherwise the sign-up form answers with
    # certainty "is this address registered here".
    assert "ייתכן שכבר יש חשבון" in html
    assert 'value="K4F2QX"' in html


def test_the_passwords_are_never_echoed_back(anon, monkeypatch):
    """Control: there is no reason for a password to sit in a response's HTML."""
    monkeypatch.setattr(app_module.db, "sign_up", lambda *a, **k: (None, "x"))

    html = _signup(anon, email="israel@gmail")

    assert "sixchars" not in html


def test_a_fresh_signup_page_is_empty(anon):
    """Control: whoever comes in for the first time shouldn't see filled fields."""
    html = anon.get("/signup").get_data(as_text=True)

    assert 'value="ישראל"' not in html


def test_being_rate_limited_does_not_wipe_the_form_either(anon, monkeypatch):
    """Whoever is blocked after a double tap is punished twice: not signed up, and
    lost what they typed."""
    monkeypatch.setattr(app_module.db, "sign_up", lambda *a, **k: (None, "x"))

    html = ""
    for _ in range(8):
        html = _signup(anon)
        if "יותר מדי ניסיונות" in html:
            break

    assert "יותר מדי ניסיונות" in html, "the limit wasn't enforced: the test tested nothing"
    assert 'value="K4F2QX"' in html


# ─── B4: a sign-in failure says what happened ───────────────────────────────

def test_an_unconfirmed_email_says_so():
    """The trap: if email confirmation is enabled, every new user is locked out
    permanently, and "forgot password" won't help either, because the password was
    never the problem."""
    msg = _login_error("Email not confirmed")

    assert "לא אומת" in msg
    assert "סיסמה" not in msg


def test_a_rate_limit_says_to_wait():
    assert "נסו שוב בעוד כמה דקות" in _login_error("Request rate limit reached")


def test_an_outage_is_not_blamed_on_the_user():
    for err in ("Database not configured", "Connection timeout"):
        assert "השירות אינו זמין" in _login_error(err), err


def test_a_genuinely_wrong_password_still_says_so():
    """Control, and the important one here: this is the overwhelming majority of
    cases, and it mustn't become vague."""
    for err in ("Invalid login credentials", "not found", ""):
        assert _login_error(err) == "מייל/טלפון או סיסמה שגויים", err


def test_the_wrong_password_message_does_not_hint_which_field_was_wrong():
    """Security control: a message distinguishing "no such user" from "wrong password"
    confirms to a guesser which addresses are registered."""
    assert _login_error("not found") == _login_error("Invalid login credentials")


# ─── The message doesn't turn the form into an oracle ───────────────────────

def test_a_taken_email_and_a_taken_phone_are_indistinguishable(anon, monkeypatch):
    """The heart. Two different messages turned the sign-up form into a service
    answering "does X have an account here", and with enable_confirmations off,
    also into a way to grab someone else's address so they can never sign up."""
    monkeypatch.setattr(app_module.db, "sign_up",
                        lambda *a, **k: (None, "User already registered"))
    by_email = _signup(anon)

    monkeypatch.setattr(app_module.db, "sign_up",
                        lambda *a, **k: (None, "duplicate key value violates phone_unique"))
    by_phone = _signup(anon)

    def _error_line(html):
        i = html.index("הרשמה נכשלה")
        return html[i:i + 120]

    assert _error_line(by_email) == _error_line(by_phone), \
        "a taken email can be told apart from a taken phone"


def test_the_message_points_somewhere_useful(anon, monkeypatch):
    """Whoever really forgot they have an account needs to know what to do now."""
    monkeypatch.setattr(app_module.db, "sign_up",
                        lambda *a, **k: (None, "User already registered"))

    html = _signup(anon)

    assert "להתחבר" in html and "לאפס סיסמה" in html
