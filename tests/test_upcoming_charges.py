""""צפוי השבוע" on the home page (Matan, 23–24.11): the recurring transactions and
installments still to come this week, up to Saturday, under "השבוע": what comes
in, what goes out and what goes to savings, each in the order of its dates."""
from datetime import date
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import upcoming_charges

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_TODAY = date(2026, 11, 22)                    # a Sunday


def _rows(out, kind="expense"):
    return next((p["items"] for p in out["parts"] if p["kind"] == kind), [])


def _tpl(**kw):
    base = {"id": "t1", "type": "expense", "amount": 100.0, "description": "שכירות",
            "category_name": "דיור", "category_icon": "🏠", "date": "2026-01-23",
            "is_recurring": True, "recurring_frequency": "monthly_same", "recurring_end_date": None,
            "project_id": None, "installments": None, "installment_total": None}
    return {**base, **kw}


def test_the_rest_of_this_week_tomorrow_first():
    out = upcoming_charges([
        _tpl(id="rent", amount=5400.0, date="2026-01-23"),                      # tomorrow, Monday
        _tpl(id="gym", amount=120.0, description="חדר כושר", date="2026-02-28"),  # Saturday: the last day
        _tpl(id="next", amount=50.0, date="2026-01-29"),                        # Sunday 29.11: next week
        _tpl(id="today", amount=70.0, date="2026-01-22"),                       # today: already recorded
    ], _TODAY)
    assert [(i["id"], i["day"], i["soon"]) for i in _rows(out)] == [("rent", "מחר", True), ("gym", "ש׳", False)]
    assert out["parts"][0]["total"] == 5520.0


def test_a_charge_a_week_ahead_is_not_this_week():
    """Matan's phone, Tuesday 24.11: the charges of 1.12, seven days ahead, showed
    as "ג׳" under a week card that said 22.11–28.11."""
    tuesday = date(2026, 11, 24)
    first = _tpl(id="fund", type="savings", date="2026-01-01", recurring_frequency="monthly_1")
    assert upcoming_charges([first], tuesday)["parts"] == []
    # From Sunday 29.11 it belongs to the week
    assert [i["day"] for i in _rows(upcoming_charges([first], date(2026, 11, 29)), "savings")] == ["ג׳"]
    # Saturday: nothing is left of the week
    assert upcoming_charges([_tpl(date="2026-01-29")], date(2026, 11, 28))["parts"] == []


def test_three_parts_each_with_its_own_total():
    """Matan (24.11): incomes too, and never one sum of everything: ₪10,000 of
    deposits made a quiet week look like an expensive one."""
    out = upcoming_charges([
        _tpl(id="salary", type="income", amount=14000.0, date="2026-01-24"),
        _tpl(id="rent", amount=5400.0, date="2026-01-23"),
        _tpl(id="net", amount=70.0, date="2026-01-25"),
        _tpl(id="fund", type="savings", amount=5000.0, date="2026-01-24"),
        _tpl(id="trip", project_id="p1", date="2026-01-24"),                       # project money: nowhere on home
        _tpl(id="ended", date="2026-01-24", recurring_end_date="2026-10-24"),
    ], _TODAY)
    assert [(p["kind"], p["title"], p["total"]) for p in out["parts"]] == [
        ("income", "נכנס", 14000.0), ("expense", "יורד", 5470.0), ("savings", "לחיסכון", 5000.0)]
    assert [i["id"] for i in _rows(out)] == ["rent", "net"], "each part in the order of its dates"
    assert "total" not in out, "no sum of all three"
    # A part with nothing in it isn't there
    assert [p["kind"] for p in upcoming_charges([_tpl(date="2026-01-23")], _TODAY)["parts"]] == ["expense"]


def test_an_installment_after_the_first_is_the_regular_payment():
    """The template carries the first payment, with the remainder: ₪1,000 / 3 is
    333.34 and then 333.33."""
    out = upcoming_charges([_tpl(id="sofa", amount=333.34, date="2026-10-25", installments=3,
                                 installment_total=1000.0, recurring_end_date="2026-12-25")], _TODAY)
    assert _rows(out)[0]["amount"] == 333.33


def test_the_templates_are_fetched_only_when_a_charge_is_due(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "get_recurring_transactions", lambda *a, **k: calls.append(1) or [_tpl()])
    with app_module.app.test_request_context("/"):
        far = {"recurring_next": {"due": "2026-11-29", "checked": "2026-11-22"}}     # next Sunday
        assert app_module._upcoming_for_home("f", "u", far, _TODAY) is None
        assert app_module._upcoming_for_home("f", "u", {}, _TODAY) is None
        assert app_module._upcoming_for_home("f", "u", {"recurring_next": {"due": None, "checked": "2026-11-22"}}, _TODAY) is None
        assert calls == [], "no query when nothing is due by Saturday"
        near = {"recurring_next": {"due": "2026-11-23", "checked": "2026-11-22"}}
        assert _rows(app_module._upcoming_for_home("f", "u", near, _TODAY))[0]["day"] == "מחר"
        assert calls == [1]


def test_a_failure_leaves_the_home_page_standing(monkeypatch):
    def boom(*a, **k):
        raise db.DataUnavailable("recurring")
    monkeypatch.setattr(db, "get_recurring_transactions", boom)
    with app_module.app.test_request_context("/"):
        near = {"recurring_next": {"due": "2026-11-23", "checked": "2026-11-22"}}
        assert app_module._upcoming_for_home("f", "u", near, _TODAY) is None


def test_it_sits_under_the_week_card_and_opens_the_series():
    tpl = (_ROOT / "frontend/templates/index.html").read_text(encoding="utf-8")
    assert tpl.index('{% include "_week_card.html" %}') < tpl.index('class="chart-card upcoming-card"')
    assert tpl.index('class="chart-card upcoming-card"') < tpl.index("עסקאות אחרונות")
    assert """href="{{ url_for('settings') }}?series={{ item.id }}\"""" in tpl
