"""A one-person family (Matan, 5.10): no "של מי".

Not the tag next to every transaction, not the "לפי בן משפחה" chart and not the
question in the window. Display only: the preference doesn't change, the choice in
the window keeps being sent as always, and when someone joins everything comes
back.
"""
import json
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_FAM = "11111111-1111-1111-1111-111111111111"
_ME = "22222222-2222-2222-2222-222222222222"
_ME_ROW = {"id": _ME, "name": "מתן"}
_OR_ROW = {"id": "33333333-3333-3333-3333-333333333333", "name": "אור"}
_TX = {"id": "t1", "type": "expense", "amount": 50, "date": "2026-10-01", "description": "שופרסל",
       "category_name": "סופר", "category_icon": "🛒", "is_recurring": False, "user_id": _ME,
       "user_name": "מתן"}


@pytest.fixture
def home(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {"id": _FAM})
    on = dict(db.DEFAULT_FAMILY_SETTINGS, owner_attribution={"expense": True, "income": True, "savings": False})
    monkeypatch.setattr(app_module, "family_settings", lambda: on)
    summary = {"income": 0.0, "expense": 50.0, "savings": 0.0, "balance": -50.0, "remaining": 0.0, "expense_pct": 0}
    for fn, val in (("get_family_settings", on), ("get_categories", [{"id": "c", "name": "x", "type": "expense"}]),
                    ("family_has_no_transactions", False), ("get_monthly_summary", summary),
                    ("week_spending", None), ("fetch_month_rows", []), ("get_recent_transactions", [_TX])):
        monkeypatch.setattr(db, fn, lambda *a, _v=val, **k: _v)

    def render(members):
        monkeypatch.setattr(db, "get_family_members", lambda *a, **k: list(members))
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = _ME
                sess["family_id"] = _FAM
            return c.get("/").get_data(as_text=True)
    return render


def _page_data(html):
    block = html[html.index('id="sf-page-data">') + len('id="sf-page-data">'):]
    return json.loads(block[:block.index("</script>")])


def test_alone_there_is_no_whose_tag_and_the_window_hides_the_question(home):
    html = home([_ME_ROW])
    rows = html[html.index('class="transactions-list'):]

    assert "owner-pill" not in rows[:rows.index("</ul>")]
    data = _page_data(html)
    assert data["singleMember"] is True
    assert data["attribution"]["expense"] is True, "the preference itself didn't change"


def test_with_someone_else_everything_comes_back(home):
    html = home([_ME_ROW, _OR_ROW])
    rows = html[html.index('class="transactions-list'):]

    assert "owner-pill" in rows[:rows.index("</ul>")]
    assert _page_data(html)["singleMember"] is False


def test_the_window_still_sends_the_choice_when_the_question_is_hidden():
    """Without sending it the server would record a shared transaction under whoever
    is signed in."""
    js = (_ROOT / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
    assert "owner:               window.SF_ATTRIBUTION[currentType] ? (txOwner.value || null) : null," in js
    assert "(hasOwner && !window.SF_PAGE_DATA.singleMember)" in js


def test_the_month_has_no_member_chart_for_one_person():
    src = (_ROOT / "backend/app.py").read_text(encoding="utf-8")
    assert 'if settings_["owner_attribution"].get(t) and len(p1["members"] or []) > 1]' in src
