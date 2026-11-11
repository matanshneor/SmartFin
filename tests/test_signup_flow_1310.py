"""Sign-up (Matan, 13.10): go straight in, every problem below its own field, and
no "אימות סיסמה"."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import app as app_module
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_NEW = "44444444-4444-4444-4444-444444444444"


@pytest.fixture
def anon():
    app.config["TESTING"] = True
    limiter.reset()
    yield app.test_client()
    limiter.reset()


def _form(**extra):
    form = {"first_name": "דנה", "last_name": "כהן", "email": "dana@gmail.com",
            "phone": "054-1234567", "password": "sixchars8"}
    form.update(extra)
    return form


def _response(with_session=True):
    sess = SimpleNamespace(access_token="tok", refresh_token="ref", expires_at=9999999999) \
        if with_session else None
    return SimpleNamespace(user=SimpleNamespace(id=_NEW, email="dana@gmail.com"), session=sess)


def test_after_signup_you_are_in(anon, monkeypatch):
    begun = []
    monkeypatch.setattr(app_module.db, "sign_up", lambda *a, **k: (_response(), None))

    def begin(uid, email, access, refresh, exp):
        begun.append((uid, access, refresh))
        from flask import session
        session["user_id"] = uid
        return None
    monkeypatch.setattr(app_module, "_begin_session", begin)

    res = anon.post("/signup", data=_form())

    assert res.status_code == 302 and res.headers["Location"].endswith("/")
    assert begun == [(_NEW, "tok", "ref")]
    assert "sf_device" in (res.headers.get("Set-Cookie") or "") or res.headers.getlist("Set-Cookie")


def test_an_invite_that_failed_is_still_said_out_loud(anon, monkeypatch):
    monkeypatch.setattr(app_module.db, "sign_up", lambda *a, **k: (_response(), None))
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(app_module.db, "join_family_by_code", lambda code: (None, "הקוד פג"))
    monkeypatch.setattr(app_module, "_begin_session", lambda *a: None)

    with anon as c:
        c.post("/signup", data=_form(invite_code="OLD123"))
        from flask import session
        assert "הקוד פג" in session.get("sf_notice", "")


@pytest.mark.parametrize("why", ["no session (email confirmation on)", "sign-in failed"])
def test_otherwise_it_is_the_login_screen_like_before(anon, monkeypatch, why):
    with_session = why != "no session (email confirmation on)"
    monkeypatch.setattr(app_module.db, "sign_up", lambda *a, **k: (_response(with_session), None))
    monkeypatch.setattr(app_module, "_begin_session", lambda *a: "לא הצלחנו")

    html = anon.post("/signup", data=_form()).get_data(as_text=True)

    assert "נרשמתם בהצלחה. אפשר להתחבר." in html


def test_no_confirm_field_but_an_old_form_is_still_checked(anon, monkeypatch):
    monkeypatch.setattr(app_module.db, "sign_up", lambda *a, **k: pytest.fail("signed up"))

    html = anon.post("/signup", data=_form(password_confirm="different")).get_data(as_text=True)

    assert "הסיסמאות אינן תואמות" in html
    tpl = (_ROOT / "frontend/templates/login.html").read_text(encoding="utf-8")
    assert 'name="password_confirm"' not in tpl


def test_every_field_has_its_own_message_line():
    tpl = (_ROOT / "frontend/templates/login.html").read_text(encoding="utf-8")
    for fid in ("first_name", "last_name", "signup_email", "phone", "signup_password"):
        assert f'<p class="field-error" id="{fid}_error" role="alert" hidden></p>' in tpl


def test_the_live_checks_use_the_server_rules():
    js = (_ROOT / "frontend/static/js/login.js").read_text(encoding="utf-8")
    src = (_ROOT / "backend/app.py").read_text(encoding="utf-8")
    assert r"const EMAIL_RE = /^[^@\s]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/;" in js
    assert r'_EMAIL_RE = re.compile(r"^[^@\s]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")' in src
    assert r"const PHONE_RE = /^0(5\d|7\d|[2-4689])\d{7}$/;" in js
    assert r'_PHONE_RE = re.compile(r"^0(5\d|7\d|[2-4689])\d{7}$")' in src
    # the button lock doesn't lock when the check stopped the submit
    assert "if (e.defaultPrevented || btn.disabled) return;" in js
    assert js.index("signupChecks(document.getElementById('signupPanel'));") < \
        js.index("guardSubmit(document.getElementById('signupPanel')")


def test_a_new_family_starts_with_matans_categories():
    """Matan (13.10): expenses and income as he has them today, and in savings:
    investments and a bank deposit."""
    from backend.app import _DEFAULT_CATEGORIES as cats
    by = lambda t: [c["name"] for c in cats if c["type"] == t]
    assert by("expense") == ["דיור ושכירות", "חשבונות ומנויים", "רכב ותחבורה", "בריאות",
                             "סופר ומזון", "מסעדות ובילויים", "שופינג וקניות", "טיפוח",
                             "ריהוט ואביזרים לבית", "חופשות", "אירועים ומתנות", "אחר"]
    assert by("income") == ["משכורת", "הכנסה נוספת"]
    assert by("savings") == ["השקעות", "פיקדון בנקאי"]
