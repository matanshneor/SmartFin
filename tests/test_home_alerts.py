"""The month's alerts on the home page, and only there (Matan, 11.10).

When there are alerts, they appear on the home page (`db.month_alerts`). When
there aren't, there is no empty area at all. Tapping an alert leads to its target
on the month page: the category that went over (`#cat-<id>`, opened with its
transactions), the expense breakdown, or the top of the page, where the balance is.
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
_FOOD = "33333333-3333-3333-3333-333333333333"


def _read(rel):
    return (_ROOT / rel).read_text(encoding="utf-8")


def _summary(income, expense, savings=0.0):
    return {"income": income, "expense": expense, "savings": savings,
            "remaining": round(income - expense - savings, 2)}


# ─── The target of each alert ───────────────────────────────────────────

def test_a_budget_alert_leads_to_its_category():
    rows = [{"category_id": _FOOD, "name": "סופר", "icon": "🛒", "total": 2350}]
    settings = {"limits": {_FOOD: {"amount": 2000}}}
    alerts = db.budget_alerts(db.apply_budgets(rows, settings))
    assert [a["target"] for a in alerts] == [f"cat-{_FOOD}"]


def test_an_uncategorized_alert_falls_back_to_the_expenses():
    assert db.category_anchor(None) == "expense-breakdown"
    assert db.category_anchor(_FOOD) == f"cat-{_FOOD}"


def test_alerts_are_budget_overruns_only():
    """Matan (11.10): the "above average" alert and the balance alerts were removed:
    whoever wants alerts sets a budget. A category without a budget doesn't
    alert, even when it is very high."""
    rows = [{"category_id": _FOOD, "name": "סופר", "icon": "🛒", "total": 99999}]
    assert db.month_alerts(db.apply_budgets(rows, {})) == []
    assert not hasattr(db, "get_anomalies") and not hasattr(db, "balance_alerts")
    import inspect
    src = inspect.getsource(app_module.update_family_settings_route)
    assert '"anomaly"' not in src and '"show_workplace"' not in src


# ─── The home page ──────────────────────────────────────────────────────

@pytest.fixture
def home(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    budgeted = {**db.DEFAULT_FAMILY_SETTINGS, "limits": {_FOOD: {"amount": 2000}}}
    for fn, val in (("get_family_settings", budgeted),
                    ("get_categories", [{"id": _FOOD, "name": "סופר", "type": "expense"}]),
                    ("family_has_no_transactions", False), ("get_monthly_summary", _summary(9000, 1200)),
                    ("week_spending", None), ("fetch_month_rows", []), ("get_recent_transactions", []),
                    ("get_family_members", [{"id": _ME, "name": "מתן"}])):
        monkeypatch.setattr(db, fn, lambda *a, _v=val, **k: _v)

    def render(alerts):
        monkeypatch.setattr(db, "month_alerts", lambda *a, **k: list(alerts))
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = _ME
                sess["family_id"] = _FAM
            res = c.get("/")
            assert res.status_code == 200
            return res.get_data(as_text=True)
    return render


def test_without_budgets_the_month_is_not_even_fetched(monkeypatch):
    """Without budgets no alert is possible, and the month's rows aren't fetched at all."""
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    for fn, val in (("get_family_settings", dict(db.DEFAULT_FAMILY_SETTINGS)),
                    ("get_categories", [{"id": _FOOD, "name": "סופר", "type": "expense"}]),
                    ("family_has_no_transactions", False), ("get_monthly_summary", _summary(9000, 1200)),
                    ("week_spending", None), ("get_recent_transactions", []),
                    ("get_family_members", [{"id": _ME, "name": "מתן"}])):
        monkeypatch.setattr(db, fn, lambda *a, _v=val, **k: _v)
    monkeypatch.setattr(db, "fetch_month_rows", lambda *a, **k: pytest.fail("rows were fetched with no budgets"))
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        html = c.get("/").get_data(as_text=True)
    assert 'class="alerts-section"' not in html


def test_no_alerts_no_empty_area(home):
    html = home([])
    assert 'class="alerts-section"' not in html


def test_each_alert_on_the_home_page_links_to_its_target(home):
    html = home([
        {"severity": "warning", "text": "🛒 סופר: חריגה", "target": f"cat-{_FOOD}"},
        {"severity": "danger", "text": "ההוצאות גבוהות מההכנסות", "target": "expense-breakdown"},
        {"severity": "danger", "text": "מאזן החודש שלילי", "target": None},
    ])
    assert 'class="alerts-section"' in html
    assert f'<a class="alert-card warning"\n       href="/month#cat-{_FOOD}">' in html
    assert '<a class="alert-card danger"\n       href="/month#expense-breakdown">' in html
    assert '<a class="alert-card danger"\n       href="/month">' in html


def test_a_failure_in_the_alerts_does_not_take_the_home_page_down(home, monkeypatch):
    def boom(*a, **k):
        raise db.DataUnavailable("x")
    monkeypatch.setattr(db, "fetch_month_rows", boom)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        assert c.get("/").status_code == 200


# ─── The month page ─────────────────────────────────────────────────────

def test_the_month_page_has_an_anchor_per_category():
    tpl = _read("frontend/templates/month.html")
    assert '<li class="legend-item-wrap"{% if item.category_id %} id="cat-{{ item.category_id }}"{% endif %}>' in tpl


def test_alerts_are_on_the_home_page_only():
    """Matan (11.10): alerts only on home. On the month page a budget overage is
    already written under the category, and a negative balance is red at the top
    of the page."""
    import inspect
    tpl = _read("frontend/templates/month.html")
    assert "alerts-section" not in tpl and "alert-card" not in tpl
    assert "month_alerts" not in inspect.getsource(app_module.month_view)
    assert "a.alert-card" not in _read("frontend/static/js/month.js")


def test_arriving_at_a_category_opens_it():
    js = _read("frontend/static/js/month.js")
    assert "else if (arrived.indexOf('cat-') === 0) showCategory(arrived);" in js
    body = js[js.index("function showCategory"):]
    body = body[:body.index("\n}\n")]
    assert "toggleExpand(trigger)" in body and "flashBreakdown('expense-breakdown')" in body
