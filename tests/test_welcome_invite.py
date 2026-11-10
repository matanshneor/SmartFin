""""איך מצרפים בן משפחה" (Matan, 5.10).

When a family is opened, the first visit to the home page explains how to add a
family member, with a button that sends the invite. Once: only when arriving from
the opening wizard (?welcome=1), and only when there is no one else in the family
yet.
"""
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"
_ME_ROW = {"id": _ME, "name": "מתן"}


@pytest.fixture
def home(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    summary = {"income": 0.0, "expense": 0.0, "savings": 0.0, "balance": 0.0,
               "remaining": 0.0, "expense_pct": 0}
    for fn, val in (("get_family_settings", dict(db.DEFAULT_FAMILY_SETTINGS)),
                    ("get_categories", [{"id": "c", "name": "x", "type": "expense"}]),
                    ("family_has_no_transactions", True), ("get_monthly_summary", summary),
                    ("week_spending", None), ("fetch_month_rows", []), ("get_recent_transactions", [])):
        monkeypatch.setattr(db, fn, lambda *a, _v=val, **k: _v)
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {
        "id": _FAM, "name": "משפחת שניאור", "invite_code": "K4F2QX",
        "invite_code_expires_at": "2099-01-01T00:00:00+00:00"})

    def render(url="/?welcome=1", members=(_ME_ROW,)):
        monkeypatch.setattr(db, "get_family_members", lambda *a, **k: list(members))
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = _ME
                sess["family_id"] = _FAM
            res = c.get(url)
            assert res.status_code == 200
            return res.get_data(as_text=True)
    return render


def test_right_after_opening_a_family_the_home_page_explains_how_to_add_someone(home):
    html = home()

    assert 'id="welcomeInvite"' in html
    assert "איך מצרפים בן משפחה?" in html
    assert 'data-code="K4F2QX"' in html and 'data-family="משפחת שניאור"' in html
    assert ">שליחת הזמנה</button>" in html
    assert "js/welcome-invite.js" in html


def test_not_on_an_ordinary_visit(home):
    assert 'id="welcomeInvite"' not in home("/")


def test_not_once_someone_has_already_joined(home):
    assert 'id="welcomeInvite"' not in home(members=(_ME_ROW, {"id": "x", "name": "אור"}))


def test_not_with_an_expired_code_that_could_not_be_renewed(home, monkeypatch):
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {
        "id": _FAM, "name": "x", "invite_code": "OLD123",
        "invite_code_expires_at": "2000-01-01T00:00:00+00:00"})
    monkeypatch.setattr(db, "renew_expired_invite_code", lambda: (None, "boom"))

    assert 'id="welcomeInvite"' not in home()


def test_the_wizard_lands_on_it_and_a_refresh_does_not_bring_it_back():
    wizard = (_ROOT / "frontend/static/js/onboarding.js").read_text(encoding="utf-8")
    assert "window.location.href = '/?welcome=1';" in wizard
    js = (_ROOT / "frontend/static/js/welcome-invite.js").read_text(encoding="utf-8")
    assert "url.searchParams.delete('welcome');" in js
    assert "window.sfInviteMessage(sheet.dataset.code, sheet.dataset.family)" in js
