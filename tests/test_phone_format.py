"""
The phone number wasn't validated either.

_normalize_phone stripped everything that isn't a digit and returned what was
left, and that was it. So 1 was a valid phone number: it was saved in the profile,
became a meaningless alternative sign-in identifier, and because of the unique
index on the field it also blocked that value for all other users, forever.

And a second bug in the same place: a +972 prefix was saved as is. Whoever signed
up with +972-54-1234567 was saved as 972541234567, then tried to sign in with
054-1234567, and wasn't found. Two people could also "grab" the same number, each
in a different spelling, without the unique index noticing.
"""
import pytest

from backend import app as app_module
from backend.app import app, limiter, _looks_like_phone, _normalize_phone

pytestmark = pytest.mark.unit


@pytest.fixture
def anon(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "sign_up",
                        lambda *a, **k: pytest.fail("signed up with an invalid phone"))
    return app.test_client()


@pytest.fixture(autouse=True)
def _fresh_limits():
    limiter.reset()
    yield
    limiter.reset()


def _signup(client, phone):
    return client.post("/signup", data={
        "first_name": "ישראל", "last_name": "ישראלי",
        "email": "israel@example.com", "phone": phone,
        "password": "sixchars8", "password_confirm": "sixchars8",
    })


# ─── What gets registered today and shouldn't ───────────────────────────────

@pytest.mark.parametrize("phone", ["1", "12345", "*6120", "0512345"])
def test_a_number_that_is_not_a_number_is_rejected(anon, phone):
    assert "מספר הטלפון אינו תקין" in _signup(anon, phone).get_data(as_text=True)


def test_a_foreign_number_is_rejected(anon):
    """The phone is an Israeli sign-in identifier, and a foreign number will never be
    found in a lookup."""
    assert "מספר הטלפון אינו תקין" in _signup(anon, "+1-555-123-4567").get_data(as_text=True)


@pytest.mark.parametrize("phone,why", [
    ("050123456",   "ספרה חסרה"),
    ("05012345678", "ספרה עודפת"),
])
def test_the_wrong_length_is_rejected(anon, phone, why):
    assert "מספר הטלפון אינו תקין" in _signup(anon, phone).get_data(as_text=True), why


# ─── What must keep working ─────────────────────────────────────────────────

@pytest.mark.parametrize("phone", [
    "050-1234567",      # the spelling in the input box
    "0541234567",       # real format, made-up number
    "0509876543",       # real format, made-up number
    "03-1234567",       # Tel Aviv landlines
    "09-8765432",       # Sharon landlines
    "04 987 6543",      # with spaces
    "077-1234567",      # 07X
])
def test_real_numbers_pass(phone):
    """Control, and the important one here: an overly strict check blocks real
    sign-ups, and that is worse than the bug. The two numbers in the middle belong
    to the existing accounts."""
    assert _looks_like_phone(_normalize_phone(phone)), f"{phone} was rejected by mistake"


def test_a_valid_number_reaches_supabase(monkeypatch):
    app.config["TESTING"] = True
    limiter.reset()
    seen = []
    monkeypatch.setattr(app_module.db, "sign_up",
                        lambda e, p, n, phone=None: (seen.append(phone) or (None, "stop")))

    _signup(app.test_client(), "050-1234567")

    assert seen == ["0501234567"]


# ─── +972 is the same number ────────────────────────────────────────────────

@pytest.mark.parametrize("written", ["+972-54-123-4567", "972541234567", "+972 54 1234567"])
def test_the_country_code_becomes_a_local_number(written):
    """The heart of the second bug: the same number was saved in two forms, and
    signing in with the second form didn't find it."""
    assert _normalize_phone(written) == "0541234567"


def test_both_spellings_look_up_the_same_person(monkeypatch):
    """What actually happens on the sign-in screen."""
    asked = []
    monkeypatch.setattr(app_module.db, "get_email_by_phone",
                        lambda p: asked.append(p) or None)
    monkeypatch.setattr(app_module.db, "sign_in", lambda e, p: (None, "no"))
    app.config["TESTING"] = True
    limiter.reset()
    c = app.test_client()

    c.post("/login", data={"identifier": "054-1234567",      "password": "x"})
    c.post("/login", data={"identifier": "+972-54-1234567",  "password": "x"})

    assert asked == ["0541234567", "0541234567"], "the same number was looked up in two forms"


# ─── Editing the profile ────────────────────────────────────────────────────

def test_editing_a_profile_cannot_smuggle_a_bad_number(monkeypatch):
    """Sign-up isn't the only way in: the phone can be edited later."""
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "update_profile",
                        lambda *a, **k: pytest.fail("an invalid phone was saved"))
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"]   = "00000000-0000-0000-0000-000000000000"
        sess["family_id"] = None

    res = c.put("/api/profile", json={"first_name": "ישראל", "last_name": "ישראלי",
                                      "phone": "1"})

    assert res.status_code == 422
    assert res.get_json()["error"] == "מספר הטלפון אינו תקין — למשל 050-1234567"


def test_clearing_the_phone_in_the_profile_is_still_allowed(monkeypatch):
    """Control: when editing the field isn't required, and a check that rejects an
    empty one would lock the whole details-editing screen for anyone with no saved
    phone."""
    app.config["TESTING"] = True
    saved = []
    monkeypatch.setattr(app_module.db, "get_profile", lambda *a, **k: {})
    monkeypatch.setattr(app_module.db, "update_profile",
                        lambda uid, name, phone, workplace: saved.append(phone) or (True, None))
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"]   = "00000000-0000-0000-0000-000000000000"
        sess["family_id"] = None

    res = c.put("/api/profile", json={"first_name": "ישראל", "last_name": "ישראלי",
                                      "phone": ""})

    assert res.status_code == 200
    assert saved == [None]
