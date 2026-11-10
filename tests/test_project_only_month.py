"""A month with only project transactions looked empty.

The month page's transaction list is filtered of project transactions (they are
shown separately), and the template checked "no transactions" on the filtered
list. A month that was all renovation showed "לא נרשמו עסקאות", and the
"פרויקטים החודש" area, which sits inside the else, wasn't shown at all. All the
month's money exists, and there is no way to see it from the month page.
"""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"


def _row(desc, project=True):
    return {"id": f"t-{desc}", "family_id": _FAM, "amount": 4000.0, "type": "expense",
            "date": "2026-03-10", "description": desc, "user_id": None,
            "category_id": None if project else "c1",
            "project_id": "p1" if project else None,
            "project_category_id": "pc1" if project else None,
            "is_recurring": False, "recurring_parent_id": None, "recurring_frequency": None,
            "recurring_end_date": None, "receipt_path": None, "workplace": None,
            "categories": None if project else {"name": "מכולת", "icon": "🛒"},
            "project_categories": {"name": "חומרים", "icon": "🧱"} if project else None,
            "profiles": None,
            "projects": {"owner_id": None, "name": "שיפוץ", "icon": "🔨"} if project else None}


@pytest.fixture
def month_page(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {"id": _FAM})
    monkeypatch.setattr(app_module, "family_settings",
                        lambda: dict(db.DEFAULT_FAMILY_SETTINGS))

    def render(rows):
        monkeypatch.setattr(db, "fetch_month_page", lambda fid, y, m: {
            "family": {"id": _FAM}, "settings": dict(db.DEFAULT_FAMILY_SETTINGS),
            "members": [{"id": _ME, "name": "מתן"}],
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


def test_a_month_of_only_project_spending_shows_it(month_page):
    html = month_page([_row("קבלן")])

    assert "לא נרשמו עסקאות" not in html
    assert "פרויקטים החודש" in html
    assert "קבלן" in html


def test_a_truly_empty_month_still_says_so(month_page):
    """Control."""
    html = month_page([])

    assert "לא נרשמו עסקאות" in html
    assert "פרויקטים החודש" not in html
