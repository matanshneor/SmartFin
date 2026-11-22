""""מה יורד השבוע" on the home page (Matan, 23.11): the recurring transactions and
installments of the next seven days, one line each, under "השבוע"."""
from datetime import date
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import upcoming_charges

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_TODAY = date(2026, 11, 22)                    # a Sunday


def _tpl(**kw):
    base = {"id": "t1", "type": "expense", "amount": 100.0, "description": "שכירות",
            "category_name": "דיור", "category_icon": "🏠", "date": "2026-01-23",
            "is_recurring": True, "recurring_frequency": "monthly_same", "recurring_end_date": None,
            "project_id": None, "installments": None, "installment_total": None}
    return {**base, **kw}


def test_the_next_seven_days_tomorrow_first():
    out = upcoming_charges([
        _tpl(id="rent", amount=5400.0, date="2026-01-23"),                      # tomorrow
        _tpl(id="gym", amount=120.0, description="חדר כושר", date="2026-02-28"),  # Saturday
        _tpl(id="far", amount=50.0, date="2026-01-10"),                         # 10.12: too far
        _tpl(id="today", amount=70.0, date="2026-01-22"),                       # today: already recorded
    ], _TODAY)
    assert [(i["id"], i["day"], i["soon"]) for i in out["items"]] == [("rent", "מחר", True), ("gym", "ש׳", False)]
    assert out["total"] == 5520.0


def test_only_money_that_leaves_the_month():
    out = upcoming_charges([
        _tpl(id="salary", type="income", date="2026-01-24"),
        _tpl(id="trip", project_id="p1", date="2026-01-24"),
        _tpl(id="ended", date="2026-01-24", recurring_end_date="2026-10-24"),
        _tpl(id="fund", type="savings", date="2026-01-24"),
    ], _TODAY)
    assert [i["id"] for i in out["items"]] == ["fund"]


def test_an_installment_after_the_first_is_the_regular_payment():
    """The template carries the first payment, with the remainder: ₪1,000 / 3 is
    333.34 and then 333.33."""
    out = upcoming_charges([_tpl(id="sofa", amount=333.34, date="2026-10-25", installments=3,
                                 installment_total=1000.0, recurring_end_date="2026-12-25")], _TODAY)
    assert out["items"][0]["amount"] == 333.33


def test_the_templates_are_fetched_only_when_a_charge_is_due(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "get_recurring_transactions", lambda *a, **k: calls.append(1) or [_tpl()])
    with app_module.app.test_request_context("/"):
        far = {"recurring_next": {"due": "2026-12-15", "checked": "2026-11-22"}}
        assert app_module._upcoming_for_home("f", "u", far, _TODAY) is None
        assert app_module._upcoming_for_home("f", "u", {}, _TODAY) is None
        assert app_module._upcoming_for_home("f", "u", {"recurring_next": {"due": None, "checked": "2026-11-22"}}, _TODAY) is None
        assert calls == [], "no query when nothing is due within the week"
        near = {"recurring_next": {"due": "2026-11-23", "checked": "2026-11-22"}}
        assert app_module._upcoming_for_home("f", "u", near, _TODAY)["items"][0]["day"] == "מחר"
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
