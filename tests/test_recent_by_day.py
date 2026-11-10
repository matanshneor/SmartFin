""""עסקאות אחרונות" on the home page (Matan, 30.9).

The ten most recently entered, in entry order: the newest on top. The day is
written inside each row: "היום · רמי לוי", "אתמול", "ראשון, 27.9". (There used to
be headings by day, but in entry order they repeat themselves.)
"""
import datetime

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app
from backend.wording import day_label

pytestmark = pytest.mark.unit

_TODAY = datetime.date(2026, 9, 30)   # Wednesday


@pytest.mark.parametrize("date, expected", [
    ("2026-09-30", "היום"),
    ("2026-09-29", "אתמול"),
    ("2026-09-27", "ראשון, 27.9"),
    ("2026-09-26", "שבת, 26.9"),
    ("2026-08-03", "שני, 3.8"),
    ("2025-12-31", "רביעי, 31.12.2025"),   # a different year: with the year
])
def test_day_label(date, expected):
    assert day_label(date, _TODAY) == expected


def test_a_bad_date_does_not_break_the_page():
    assert day_label("", _TODAY) == ""
    assert day_label(None, _TODAY) == ""


_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"


def _tx(n, date, desc=""):
    return {"id": f"t{n}", "type": "expense", "amount": 10 * n, "date": date,
            "description": desc, "category_name": f"קט{n}", "category_icon": "🛒",
            "is_recurring": False, "user_id": None}


@pytest.fixture
def home(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {"id": _FAM})
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    monkeypatch.setattr(app_module.clock, "today", lambda: _TODAY)
    summary = {"income": 100.0, "expense": 60.0, "savings": 0.0, "balance": 40.0,
               "remaining": 40.0, "expense_pct": 60}
    for fn, val in (("get_family_settings", dict(db.DEFAULT_FAMILY_SETTINGS)),
                    ("get_categories", [{"id": "c", "name": "x", "type": "expense"}]),
                    ("get_family_members", []), ("family_has_no_transactions", False),
                    ("get_monthly_summary", summary), ("week_spending", None), ("fetch_month_rows", [])):
        monkeypatch.setattr(db, fn, lambda *a, _v=val, **k: _v)

    def render(transactions):
        monkeypatch.setattr(db, "get_recent_transactions", lambda *a, **k: transactions)
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = _ME
                sess["family_id"] = _FAM
            html = c.get("/").get_data(as_text=True)
        return html[html.index('class="transactions-list'):html.index("</ul>", html.index('class="transactions-list'))]
    return render


def test_entry_order_is_kept_and_the_day_is_in_the_row(home):
    """The server returns in entry order, and the same order is shown, even when the
    dates aren't in order."""
    lst = home([_tx(1, "2026-09-27", "מסעדה"), _tx(2, "2026-09-30", "רמי לוי"), _tx(3, "2026-09-29")])

    assert 'class="tx-day' not in lst
    assert lst.index("קט1") < lst.index("קט2") < lst.index("קט3")
    assert "ראשון, 27.9 · מסעדה" in lst
    assert "היום · רמי לוי" in lst
    assert "אתמול" in lst


def test_the_old_numeric_date_is_gone(home):
    lst = home([_tx(1, "2026-09-30", "רמי לוי"), _tx(2, "2026-09-29")])

    assert "30.09" not in lst and "29.09" not in lst


def test_ten_rows_are_asked_for(monkeypatch):
    """"A few more transactions": ten instead of five."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "backend/app.py").read_text(encoding="utf-8")
    assert "db.get_recent_transactions(family_id, limit=10," in src


def test_recent_skips_recurring_rows_from_months_that_are_over():
    """Matan (5.10): rent "from July" created July–October immediately, and they all
    jumped to the top of "עסקאות אחרונות". A normal transaction from the past stays;
    from the series, only from the current month.
    not.is.true: the column allows NULL, and such a row is a normal transaction."""
    f = db._recent_series_filter(datetime.date(2026, 10, 5))
    assert f == "and(is_recurring.not.is.true,recurring_parent_id.is.null),date.gte.2026-10-01"
    import inspect
    assert ".or_(_recent_series_filter())" in inspect.getsource(db.get_recent_transactions)
