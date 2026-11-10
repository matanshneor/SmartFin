""""September closed" on the home page (Matan, 13.10).

"This should appear on the first day of the new month, and can be dismissed with
an X, but the day after it disappears by itself."
"""
from datetime import date

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"
_CATS = [{"id": "food", "name": "סופר", "icon": "🛒", "type": "expense"},
         {"id": "fuel", "name": "דלק", "icon": "⛽", "type": "expense"},
         {"id": "sal", "name": "משכורת", "icon": "💼", "type": "income"}]


def _row(amount, type_, cat, project=None):
    return {"amount": amount, "type": type_, "category_id": cat, "project_id": project,
            "categories": None}


_SEPT = [_row(18400, "income", "sal"), _row(3180, "expense", "food"), _row(900, "expense", "fuel"),
         _row(1500, "savings", None),
         # project: not counted, as in every monthly summary
         _row(9999, "expense", "food", project="p1")]


@pytest.fixture
def recap_for(monkeypatch):
    asked = []
    monkeypatch.setattr(db, "month_recap", db._real_month_recap)

    def run(today, sept=_SEPT, aug_expense=5320.0):
        monkeypatch.setattr(db, "fetch_month_rows",
                            lambda fid, y, m: (asked.append((y, m)) or list(sept)))
        monkeypatch.setattr(db, "get_monthly_summary",
                            lambda fid, y, m: (asked.append((y, m)) or dict(db._empty_summary(), expense=aug_expense)))
        return db.month_recap(_FAM, today, _CATS)
    run.asked = asked
    return run


def test_only_on_the_first_of_the_month(recap_for):
    assert recap_for(date(2026, 10, 2)) is None
    assert recap_for(date(2026, 10, 31)) is None
    assert recap_for.asked == [], "on the other days there isn't even a query"
    assert recap_for(date(2026, 10, 1)) is not None


def test_the_figures(recap_for):
    r = recap_for(date(2026, 10, 1))

    assert (r["year"], r["month"]) == (2026, 9)
    assert recap_for.asked == [(2026, 9), (2026, 8)]
    assert (r["income"], r["expense"], r["balance"], r["savings"]) == (18400, 4080, 14320, 1500)
    # 4,080 − 5,320: you spent less
    assert r["diff"] == -1240
    assert r["top"]["name"] == "סופר" and r["top"]["total"] == 3180


def test_january_looks_back_at_december(recap_for):
    r = recap_for(date(2027, 1, 1))

    assert (r["year"], r["month"]) == (2026, 12)
    assert recap_for.asked[-1] == (2026, 11)


def test_no_comparison_with_an_empty_month(recap_for):
    assert recap_for(date(2026, 10, 1), aug_expense=0.0)["diff"] is None


def test_nothing_to_close_when_last_month_was_empty(recap_for):
    assert recap_for(date(2026, 10, 1), sept=[_row(500, "expense", "food", project="p1")]) is None


@pytest.fixture
def home(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    payload = {"family": {"id": _FAM}, "settings": dict(db.DEFAULT_FAMILY_SETTINGS),
               "members": [{"id": _ME, "name": "מתן"}], "categories": _CATS, "rows": [],
               "summary": db.summary_from_rows([]), "recent": [], "week": None, "is_new": False}
    monkeypatch.setattr(db, "fetch_home_page", lambda *a, **k: payload)

    def render(recap):
        monkeypatch.setattr(db, "month_recap", lambda *a, **k: recap)
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = _ME
                sess["family_id"] = _FAM
            return c.get("/").get_data(as_text=True)
    return render


_RECAP = {"year": 2026, "month": 9, "income": 18400.0, "expense": 14950.0, "balance": 3450.0,
          "savings": 1500.0, "diff": -1240.0, "top": {"name": "סופר", "icon": "🛒", "total": 3180.0}}


def test_the_card_on_the_home_page(home):
    html = home(_RECAP)

    """Matan (1.11): income · expenses · savings, and the two rows: "that's it"."""
    import re
    assert "ספטמבר נסגר" in html
    card = html[html.index('class="recap-card"'):]
    card = card[:card.index("</section>")]
    labels = re.findall(r'recap-fig-label">([^<]+)<', card)
    assert labels == ["הכנסות", "הוצאות", "חיסכון"]
    assert "₪18,400" in card and "₪14,950" in card and "₪1,500" in card
    assert "₪3,450" not in card                                   # without "נשאר"
    assert "₪1,240 פחות</span> מאוגוסט" in card
    assert "<b>סופר</b> — ₪3,180" in card
    assert "חסכתם" not in card                                   # already in the tiles
    assert "recap-link" not in card and "/month?" not in card    # without a link


def test_x_hides_it_on_this_phone(home):
    """The same mechanism as the notifications (core.js): a key by the month that closed."""
    html = home(_RECAP)

    assert 'class="alert-wrap recap-wrap" data-alert-key="recap|2026-9"' in html
    assert 'class="alert-dismiss recap-close"' in html


def test_a_month_that_ended_in_the_red(home):
    """Without "נשאר" there is no deficit to show: only the numbers, and "יותר" in red."""
    html = home(dict(_RECAP, balance=-600.0, diff=800.0, savings=0.0))

    assert "-₪600" not in html and 'recap-fig bad' not in html
    assert "₪800 יותר</span> מאוגוסט" in html
    assert "חסכתם" not in html


def test_no_card_on_other_days(home):
    assert "recap-card" not in home(None)
