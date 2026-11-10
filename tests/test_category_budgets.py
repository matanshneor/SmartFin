"""
Tests for a target budget per category.

Until now the bar next to each category measured its share of the month's total
expenses ("groceries are 35% of expenses"), not "₪200 left". And the only alert
compared against the average of the previous three months, i.e. against habit
and not a decision: whoever overspent three months in a row raises the average,
and the app stops alerting. It gets used to it.

Two separate decisions per category, per Matan's request: whether there is a
budget, and if so, whether to alert on overage.
"""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_CAT = "11111111-1111-1111-1111-111111111111"


def _settings(amount=2000, alert=True):
    return {"limits": {_CAT: {"amount": amount, "alert": alert}}}


_CATEGORIES = [{"id": _CAT, "name": "מכולת", "icon": "🛒", "type": "expense"}]


def _tx(amount, category_id=_CAT, name="מכולת"):
    return {"type": "expense", "amount": amount, "project_id": None,
            "user_id": None, "category_id": category_id,
            "categories": {"name": name, "icon": "🛒"},
            "profiles": None, "projects": None}


def _rows(total, categories=None, txs=None):
    """The breakdown rows as the real code produces them.

    **This is what hid the bug for a week.** The previous version built a
    hand-written dict here with `category_id`, a key `category_breakdown_from_rows`
    didn't produce at all. The tests passed on made-up data, while in the real
    app `apply_budgets` looked for an id and never found it, so no budget was
    applied, no bar was shown and no overage alert fired, from the day the
    feature shipped. Now everything goes through the real producer."""
    return db.category_breakdown_from_rows(
        txs if txs is not None else [_tx(total)],
        categories if categories is not None else _CATEGORIES,
        "expense")


# ─── The calculation ────────────────────────────────────────────────────────

def test_a_category_under_budget_shows_what_is_left():
    row = db.apply_budgets(_rows(1800), _settings())[0]

    assert row["budget_left"] == 200
    assert row["budget_pct"] == 90
    assert row["budget_over"] is False


def test_a_category_over_budget_shows_by_how_much():
    row = db.apply_budgets(_rows(2350), _settings())[0]

    assert row["budget_over"] is True
    assert row["budget_excess"] == 350
    assert row["budget_left"] == -350


def test_the_bar_stops_at_full_rather_than_overflowing():
    """A bar overflowing its width adds no information and breaks the layout."""
    assert db.apply_budgets(_rows(9999), _settings())[0]["budget_pct"] == 100


def test_a_category_without_a_budget_is_untouched():
    """Control: whoever didn't use the feature shouldn't see any change."""
    row = db.apply_budgets(_rows(1800), {"limits": {}})[0]

    assert "budget" not in row
    assert row["total"] == 1800


@pytest.mark.parametrize("amount", [0, -5, None, "", "abc"])
def test_a_meaningless_budget_is_treated_as_none(amount):
    """`0` is also how a budget is removed in the UI, so it must read as 'none'."""
    assert db.category_budget({"limits": {_CAT: {"amount": amount}}}, _CAT) is None


def test_a_malformed_entry_does_not_crash_the_month_page():
    """The JSONB was edited by hand in the past; an unexpected shape shouldn't
    break a page."""
    for broken in (None, "2000", [], {"alert": True}):
        assert db.category_budget({"limits": {_CAT: broken}}, _CAT) is None


# ─── The two decisions, separately ──────────────────────────────────────────

def test_exceeding_a_budget_alerts_when_asked():
    alerts = db.budget_alerts(db.apply_budgets(_rows(2350), _settings(alert=True)))

    assert len(alerts) == 1
    assert "חריגה של ₪350" in alerts[0]["text"]
    assert "מכולת" in alerts[0]["text"]


def test_exceeding_a_budget_stays_quiet_when_not_asked():
    """The heart of what Matan asked for: you can see the number without being
    nagged about it."""
    alerts = db.budget_alerts(db.apply_budgets(_rows(2350), _settings(alert=False)))

    assert alerts == []


def test_staying_within_budget_never_alerts():
    assert db.budget_alerts(db.apply_budgets(_rows(1800), _settings())) == []


def test_alerting_defaults_to_on_when_a_budget_is_set():
    """Whoever bothered to set a budget probably wants to know when they went over;
    turning it off is explicit."""
    assert db.category_budget({"limits": {_CAT: {"amount": 2000}}}, _CAT)["alert"] is True


# ─── Saving ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    limiter.reset()
    monkeypatch.setattr(app_module.db, "get_categories",
                        lambda fid: [{"id": _CAT, "name": "מכולת", "type": "expense"}])
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    saved = {}
    monkeypatch.setattr(app_module.db, "update_family_settings",
                        lambda fid, patch: (saved.update(patch) or True))
    monkeypatch.setattr(app_module.db, "get_family_settings", lambda fid: saved)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]   = "22222222-2222-2222-2222-222222222222"
            sess["family_id"] = "33333333-3333-3333-3333-333333333333"
        yield c, saved
    limiter.reset()


def test_setting_a_budget_stores_both_decisions(client):
    c, saved = client

    c.put("/api/family/settings", json={"limits": {_CAT: {"amount": 2000, "alert": False}}})

    assert saved["limits"][_CAT] == {"amount": 2000.0, "alert": False}


def test_clearing_the_amount_removes_the_budget(client):
    """"Don't set a budget" and "remove the budget" are the same route: there is no
    separate delete action to forget."""
    c, saved = client
    c.put("/api/family/settings", json={"limits": {_CAT: {"amount": 2000}}})

    c.put("/api/family/settings", json={"limits": {_CAT: {"amount": ""}}})

    assert _CAT not in saved["limits"]


def test_a_budget_for_a_category_of_another_family_is_refused(client):
    """The amounts come from the client, including the id."""
    c, _ = client

    response = c.put("/api/family/settings",
                     json={"limits": {"99999999-9999-9999-9999-999999999999": {"amount": 100}}})

    assert response.status_code == 422


@pytest.mark.parametrize("bad", [-5, "abc", 1e400])
def test_a_nonsense_amount_is_refused(client, bad):
    c, _ = client

    assert c.put("/api/family/settings",
                 json={"limits": {_CAT: {"amount": bad}}}).status_code == 422


# ─── The wiring: that the budget really flows from the transactions to the screen ─

def test_the_breakdown_carries_the_id_the_budget_is_stored_under():
    """The test that was missing. A budget is stored by category id; if the
    breakdown row carries no id, the lookup silently fails and the whole feature
    is dead."""
    row = db.category_breakdown_from_rows([_tx(800)], _CATEGORIES, "expense")[0]

    assert row["category_id"] == _CAT


def test_a_real_overspend_produces_a_real_alert():
    """End to end on the real path: transaction → breakdown → budget → alert."""
    breakdown = db.apply_budgets(_rows(800), {"limits": {_CAT: {"amount": 500, "alert": True}}})

    assert breakdown[0]["budget_over"] is True
    alerts = db.budget_alerts(breakdown)
    assert len(alerts) == 1
    assert "מכולת" in alerts[0]["text"]


def test_a_category_that_was_deleted_does_not_merge_into_another():
    """A transaction that lost its category used to be counted into the existing
    "אחר", because grouping was by name. The monthly total stayed right and the
    breakdown became a lie."""
    cats = [{"id": _CAT, "name": "אחר", "icon": "📦", "type": "expense"}]
    orphan = _tx(300, category_id=None, name=None)
    orphan["categories"] = None

    out = db.category_breakdown_from_rows([_tx(100), orphan], cats, "expense")
    by_name = {r["name"]: r["total"] for r in out}

    assert by_name == {"אחר": 100.0, "ללא קטגוריה": 300.0}


def test_two_categories_with_the_same_name_stay_apart():
    """The database has no uniqueness constraint on a category name. Grouping by
    name merged them."""
    other = "22222222-2222-2222-2222-222222222222"
    cats = [{"id": _CAT,  "name": "מכולת", "icon": "🛒", "type": "expense"},
            {"id": other, "name": "מכולת", "icon": "🛒", "type": "expense"}]

    out = db.category_breakdown_from_rows(
        [_tx(100), _tx(250, category_id=other)], cats, "expense")

    assert sorted(r["total"] for r in out) == [100.0, 250.0]


# ─── Removing a budget must delete it ───────────────────────────────────────

def test_removing_a_budget_actually_removes_it():
    """`.update()` can only add or overwrite, never delete, so a removal was never
    saved. The screen showed off, the server kept it."""
    stored = {"limits": {"A": {"amount": 500}, "B": {"amount": 300}}}

    merged = db._merge_settings(stored, {"limits": {"B": {"amount": 300}}})

    assert merged["limits"] == {"B": {"amount": 300}}


def test_removing_the_last_budget_leaves_an_empty_map():
    merged = db._merge_settings({"limits": {"A": {"amount": 500}}}, {"limits": {}})

    assert merged["limits"] == {}


def test_the_other_nested_settings_still_merge():
    """Control: `owner_attribution` and `anomaly` are records with fixed fields, and
    a partial update of them shouldn't delete the rest."""
    stored = {"owner_attribution": {"expense": True, "income": True, "savings": False}}

    merged = db._merge_settings(stored, {"owner_attribution": {"income": False}})

    assert merged["owner_attribution"] == {"expense": True, "income": False, "savings": False}
