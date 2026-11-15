"""The month page's "by family member" cards (Matan, 16.11).

- "Income by family member" is gone: the income card above already splits
  income per person.
- Tapping a row of "expenses by family member" opens that person's (or the
  shared) expenses of the month, like a category row.
"""
import re

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME = "22222222-2222-2222-2222-222222222222"
_OR = "33333333-3333-3333-3333-333333333333"
_SETTINGS = dict(db.DEFAULT_FAMILY_SETTINGS,
                 owner_attribution={"expense": True, "income": True, "savings": False})


def _row(desc, amount, user=None, type_="expense", project=None):
    names = {_ME: "מתן", _OR: "אור"}
    return {"id": f"t-{desc}", "family_id": _FAM, "amount": amount, "type": type_,
            "date": "2026-03-10", "description": desc, "user_id": user,
            "category_id": "c1", "project_id": project, "project_category_id": None,
            "is_recurring": False, "recurring_parent_id": None, "recurring_frequency": None,
            "recurring_end_date": None, "receipt_path": None, "workplace": None,
            "categories": {"name": "מכולת", "icon": "🛒"}, "project_categories": None,
            "profiles": {"name": names[user]} if user else None,
            "projects": {"owner_id": None, "name": "שיפוץ", "icon": "🔨"} if project else None}


@pytest.fixture
def month_page(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {"id": _FAM})
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(_SETTINGS))
    members = [{"id": _ME, "name": "מתן"}, {"id": _OR, "name": "אור"}]
    monkeypatch.setattr(db, "get_family_members", lambda fid: members)

    def render(rows):
        monkeypatch.setattr(db, "fetch_month_page", lambda fid, y, m: {
            "family": {"id": _FAM}, "settings": dict(_SETTINGS), "members": members,
            "categories": [{"id": "c1", "name": "מכולת", "icon": "🛒", "type": "expense"}],
            "rows": rows, "archive": [{"year": 2026, "month": 3}]})
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = _ME
                sess["family_id"] = _FAM
            res = c.get("/month?year=2026&month=3")
            assert res.status_code == 200
            return res.get_data(as_text=True)
    return render


def _member_card(html):
    start = html.index("הוצאות לפי בן משפחה")
    return html[start:html.index("</section>", start)]


def _opened_list(card, name):
    """The transaction list under the row of `name`."""
    row = card.index(f'<span class="member-bar-name">{name}</span>')
    end = card.find('<li class="legend-item-wrap">', row)
    return card[row:end if end != -1 else len(card)]


def test_no_income_by_family_member_card(month_page):
    html = month_page([_row("משכורת", 7000, _OR, type_="income"),
                       _row("בונוס", 1200, _ME, type_="income"),
                       _row("סופר", 100, _ME), _row("דלק", 50, _OR)])
    assert "הכנסות לפי בן משפחה" not in html
    assert "הוצאות לפי בן משפחה" in html, "control: the expense card stays"


def test_each_member_row_opens_exactly_their_household_expenses(month_page):
    card = _member_card(month_page([
        _row("סופר", 100, _ME), _row("דלק", 50, _OR), _row("חתונה", 900),
        _row("משכורת", 7000, _OR, type_="income"),
        _row("קבלן", 4000, _ME, project="p1")]))

    assert card.count('class="member-bar clickable" role="button" tabindex="0" aria-expanded="false"') == 3
    mine, ors, shared = (_opened_list(card, n) for n in ("מתן", "אור", "משותפת"))
    assert "סופר" in mine and "דלק" not in mine and "חתונה" not in mine
    assert "קבלן" not in mine, "project spending isn't in the member totals"
    assert "דלק" in ors and "משכורת" not in ors, "income doesn't open under expenses"
    assert "חתונה" in shared and "סופר" not in shared
    assert all(len(re.findall(r'class="cat-tx-row"', part)) == 1 for part in (mine, ors, shared))


def test_the_month_script_opens_member_rows():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "frontend/static/js/month.js").read_text(encoding="utf-8")
    assert js.count("e.target.closest('.legend-item.clickable, .member-bar.clickable')") == 2
    assert '.member-bar.clickable[aria-expanded="true"]' in js
