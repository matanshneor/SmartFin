""""₪1,850 a month on average" next to each category in settings (Matan, 3.10,
idea 38).

The last three whole months, divided by 3 (like the overage alerts), without
projects, and in every department.
"""
import datetime

import pytest

from backend import supabase_config as db
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_TODAY = datetime.date(2026, 10, 3)          # the window: July–September


def _tx(amount, date, cat="food", type_="expense", project=None):
    return {"id": f"{cat}-{date}-{amount}", "family_id": _FAM, "amount": amount, "type": type_,
            "date": date, "category_id": cat, "project_id": project}


def _avg(rows, monkeypatch):
    monkeypatch.setattr(db, "get_client", lambda: FakeSupabase(transactions=rows))
    # `conftest` replaces the function with an empty default for page tests;
    # here the real one is tested
    return db._real_category_monthly_averages(_FAM, today=_TODAY)


def test_three_full_months_divided_by_three(monkeypatch):
    rows = [_tx(1500, "2026-07-10"), _tx(2100, "2026-08-05"), _tx(1950, "2026-09-20")]
    assert _avg(rows, monkeypatch) == {"food": 1850}


def test_the_current_month_and_older_months_are_outside_the_window(monkeypatch):
    rows = [_tx(900, "2026-07-01"), _tx(5000, "2026-10-02"), _tx(5000, "2026-06-30")]
    assert _avg(rows, monkeypatch) == {"food": 300}


def test_a_quiet_month_counts_as_zero_not_skipped(monkeypatch):
    """A yearly insurance of ₪1,200 in one month is ₪400 a month on average, not ₪1,200."""
    assert _avg([_tx(1200, "2026-08-15", cat="insurance")], monkeypatch) == {"insurance": 400}


def test_project_money_is_not_counted(monkeypatch):
    assert _avg([_tx(3000, "2026-08-01", project="p1")], monkeypatch) == {}


def test_every_department(monkeypatch):
    rows = [_tx(30000, "2026-08-01", cat="salary", type_="income"),
            _tx(1500, "2026-09-01", cat="fund", type_="savings")]
    assert _avg(rows, monkeypatch) == {"salary": 10000, "fund": 500}


def test_settings_shows_it_under_the_name_and_a_rename_keeps_it():
    from pathlib import Path
    from backend.app import app
    root = Path(__file__).resolve().parent.parent
    tpl = (root / "frontend/templates/settings.html").read_text(encoding="utf-8")
    start = tpl.index('<span class="cat-row-text">')
    snippet = tpl[start:tpl.index("</span>\n", tpl.index("cat-row-avg", start)) + len("</span>")]
    snippet = snippet[:snippet.rindex("</span>") + 7]
    with app.test_request_context():
        html = app.jinja_env.from_string("{% set cat_averages = avgs %}" + snippet).render(
            cat={"id": "food", "name": "סופר"}, avgs={"food": 1850})
        none = app.jinja_env.from_string("{% set cat_averages = avgs %}" + snippet).render(
            cat={"id": "gift", "name": "מתנות"}, avgs={"food": 1850})
    assert "בממוצע ₪1,850 בחודש" in html
    assert "cat-row-avg" not in none, "no history: no line"
    # Renaming writes only into `.cat-row-name`; the average isn't in it
    assert '<span class="cat-row-name">{{ cat.name }}</span>' in tpl
