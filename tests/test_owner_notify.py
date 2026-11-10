"""An email to the site owner when a family signs up or someone joins (Matan, 5.10)."""
import pytest

from backend import app as app_module
from backend import notify
from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME = "22222222-2222-2222-2222-222222222222"


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(notify, "_send", lambda key, payload: out.append((key, payload)))

    class _Now:          # the thread runs immediately, so the test sees the result
        def __init__(self, target, args, daemon):
            self.t, self.a = target, args
        def start(self):
            self.t(*self.a)
    monkeypatch.setattr(notify.threading, "Thread", _Now)
    return out


def test_without_a_key_nothing_is_sent(sent, monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("OWNER_NOTIFY_EMAIL", "owner@example.com")

    assert notify.notify_owner("x", ["y"]) is False
    assert sent == []


def test_the_mail_goes_to_the_owner_in_hebrew_and_escaped(sent, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.setenv("OWNER_NOTIFY_EMAIL", "owner@example.com")

    assert notify.notify_owner("משפחה חדשה", ["משפחה חדשה נרשמה: <b>כהן</b>", "חברי משפחה: 1"]) is True
    key, payload = sent[0]
    assert key == "re_test" and payload["to"] == ["owner@example.com"]
    assert payload["from"] == "SmartFin <onboarding@resend.dev>"
    assert 'dir="rtl"' in payload["html"] and "&lt;b&gt;כהן&lt;/b&gt;" in payload["html"]
    assert payload["text"] == "משפחה חדשה נרשמה: <b>כהן</b>\nחברי משפחה: 1"


def test_a_failing_send_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(notify.httpx, "post", boom)
    notify._send("re_test", {"to": ["x"]})        # doesn't raise


@pytest.fixture
def mails(monkeypatch):
    out = []
    monkeypatch.setattr(app_module.notify, "notify_owner", lambda subject, lines: out.append((subject, lines)))
    monkeypatch.setattr(db, "get_family_members", lambda fid: [
        {"id": _ME, "name": "מתן", "full_name": "מתן שניאור", "email": "m@example.com", "phone": "0501234567"},
        {"id": "x", "name": "אור", "full_name": "אור שניאור", "email": "o@example.com", "phone": None}])
    monkeypatch.setattr(db, "get_family", lambda fid: {"id": fid, "name": "משפחת שניאור"})
    return out


def test_finishing_the_wizard_mails_the_name_and_count(mails):
    with app.test_request_context():
        app_module._notify_owner_about_family("new", _FAM, "משפחת כהן", person_id=_ME)
    subject, lines = mails[0]
    assert subject == "משפחה חדשה ב-SmartFin: משפחת כהן"
    assert "משפחה חדשה נרשמה: משפחת כהן" in lines and "חברי משפחה: 2" in lines
    # who opened it: full name, email and phone (Matan, 5.10)
    assert lines[lines.index("מי פתח אותה:") + 1:] == ["שם: מתן שניאור", "מייל: m@example.com", "טלפון: 0501234567"]


def test_joining_mails_the_new_count(mails):
    with app.test_request_context():
        app_module._notify_owner_about_family("joined", _FAM, person_id="x")
    subject, lines = mails[0]
    assert subject == "מישהו הצטרף למשפחת שניאור ב-SmartFin"
    assert "חברי משפחה עכשיו: 2" in lines
    assert lines[lines.index("מי הצטרף:") + 1:] == ["שם: אור שניאור", "מייל: o@example.com", "טלפון: לא הוזן"]


def test_a_failure_while_preparing_the_mail_does_not_break_the_join(monkeypatch):
    def gone(fid):
        raise db.DataUnavailable("x")
    monkeypatch.setattr(db, "get_family_members", gone)
    with app.test_request_context():
        app_module._notify_owner_about_family("joined", _FAM)      # doesn't raise


def test_all_three_entry_points_send_it():
    import inspect
    src = inspect.getsource(app_module)
    assert '_notify_owner_about_family("new", user["family_id"], family_name, person_id=user["id"])' in inspect.getsource(app_module.onboarding_complete)
    assert '_notify_owner_about_family("joined", family_id, person_id=user["id"])' in inspect.getsource(app_module.join_family)
    assert '_notify_owner_about_family("joined", joined_id, person_id=response.user.id)' in src   # joining at sign-up with a code
