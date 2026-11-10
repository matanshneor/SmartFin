"""The home page in one trip to the database (Matan, 13.10: "as fast as possible").

Nine queries in sequence (`300-400ms` in the logs) became one function,
`get_home_page`, like the month page's `get_month_page`. The calculations stayed
the same functions.
"""
from datetime import date
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_SQL = (_ROOT / "backend/supabase/migrations/20261013120000_home_page_fn.sql").read_text(encoding="utf-8")
_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"
_TODAY = date(2026, 10, 13)   # Tuesday


class _FakeRpc:
    def __init__(self, data):
        self.data, self.calls = data, []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return self

    def execute(self):
        return self


def _payload(**over):
    data = {
        "family": {"id": _FAM, "name": "שניאור", "settings": {"limits": {"c1": 500}}},
        "members": [{"id": _ME, "name": "מתן שניאור"}],
        "categories": [{"id": "c1", "name": "סופר", "type": "expense"}],
        "rows": [
            {"id": "t1", "type": "expense", "amount": 100, "date": "2026-10-12", "project_id": None},
            {"id": "t2", "type": "income", "amount": 1000, "date": "2026-10-02", "project_id": None},
            {"id": "t3", "type": "expense", "amount": 9999, "date": "2026-10-05", "project_id": "p1"},
        ],
        "recent": [{"id": "t1"}],
        "week_rows": [{"amount": 100, "date": "2026-10-12", "description": "", "category_id": "c1",
                       "is_recurring": False, "recurring_parent_id": None, "project_id": None,
                       "categories": {"name": "סופר", "icon": "🛒"}}],
        "week_has_older": True,
        "has_transactions": True,
    }
    data.update(over)
    return data


@pytest.fixture
def real_fetch(monkeypatch):
    monkeypatch.setattr(db, "fetch_home_page", db._real_fetch_home_page)

    def run(data):
        fake = _FakeRpc(data)
        monkeypatch.setattr(db, "get_client", lambda: fake)
        return db.fetch_home_page(_FAM, _TODAY, 10, _ME), fake.calls
    return run


def test_one_call_with_the_dates_computed_in_israel_time(real_fetch):
    _, calls = real_fetch(_payload())

    assert calls == [("get_home_page", {
        "p_family_id": _FAM, "p_year": 2026, "p_month": 10,
        "p_recent_from": "2026-10-01",
        # `* 3` as in `get_recent_transactions`: private rows will be filtered out
        "p_recent_limit": 30,
        "p_week_from": "2026-10-04", "p_week_start": "2026-10-11", "p_week_to": "2026-10-17",
    })]


def test_the_figures_are_the_same_functions_as_before(real_fetch):
    home, _ = real_fetch(_payload())

    # The month summary without the project transaction, like `get_monthly_summary`
    assert home["summary"] == db.summary_from_rows([r for r in _payload()["rows"] if not r["project_id"]])
    assert home["summary"]["expense"] == 100
    # All rows (including project) are kept for the budget alerts
    assert len(home["rows"]) == 3
    assert home["week"] == db.week_from_rows(_payload()["week_rows"], True, _TODAY)
    assert home["week"]["has_older"] is True
    assert home["members"][0]["name"] == "מתן" and home["members"][0]["full_name"] == "מתן שניאור"
    assert home["settings"]["limits"] == {"c1": 500}
    assert home["is_new"] is False
    assert real_fetch(_payload(has_transactions=False))[0]["is_new"] is True


@pytest.mark.parametrize("data", [None, {}])
def test_someone_elses_family_or_a_failure_is_not_an_empty_family(real_fetch, data):
    with pytest.raises(db.DataUnavailable):
        real_fetch(data)


@pytest.fixture
def home(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))

    def render(payload):
        monkeypatch.setattr(db, "fetch_home_page", lambda *a, **k: payload)
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = _ME
                sess["family_id"] = _FAM
            return c.get("/")
    return render


def _home_payload(**over):
    payload = {
        "family": {"id": _FAM, "name": "שניאור"},
        "settings": dict(db.DEFAULT_FAMILY_SETTINGS),
        "members": [{"id": _ME, "name": "מתן", "full_name": "מתן שניאור"}],
        "categories": [{"id": "c1", "name": "סופר", "type": "expense"}],
        "rows": [],
        "summary": db.summary_from_rows([]),
        "recent": [],
        "week": None,
        "is_new": True,
    }
    payload.update(over)
    return payload


def test_the_home_page_makes_no_other_trip(home, monkeypatch):
    def separate(*a, **k):
        raise AssertionError("שליפה נפרדת — היה אמור להגיע מ-get_home_page")
    # `_fetch_*` and not `get_*`: these go through the request cache, which the
    # new path fills
    for fn in ("get_family_settings", "get_monthly_summary", "_fetch_categories",
               "_fetch_family_members", "_fetch_family", "family_has_no_transactions",
               "get_recent_transactions", "week_spending", "fetch_month_rows"):
        monkeypatch.setattr(db, fn, separate)

    res = home(_home_payload())

    assert res.status_code == 200


def test_budget_alerts_use_the_rows_already_fetched(home, monkeypatch):
    monkeypatch.setattr(db, "fetch_month_rows", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("שליפה נוספת לשורות החודש")))
    settings = dict(db.DEFAULT_FAMILY_SETTINGS, limits={"c1": 50})
    rows = [{"id": "t1", "type": "expense", "amount": 80, "date": "2026-10-12", "project_id": None,
             "category_id": "c1", "categories": {"name": "סופר", "icon": "🛒"}}]

    res = home(_home_payload(settings=settings, rows=rows, summary=db.summary_from_rows(rows)))

    assert res.status_code == 200


def test_no_categories_still_goes_to_the_wizard(home):
    res = home(_home_payload(categories=[]))

    assert res.status_code == 302 and "/onboarding" in res.headers["Location"]


def test_the_function_guards_the_family_like_its_sister():
    assert "security definer" in _SQL
    assert "where p_family_id = public.get_my_family_id();" in _SQL
    assert "from public, anon;" in _SQL
    assert "to authenticated;" in _SQL


def test_each_part_mirrors_the_query_it_replaces():
    # `_recent_series_filter`: `is_recurring.not.is.true` and not `= false`: a
    # `NULL` is a regular transaction
    assert "(t.is_recurring is not true and t.recurring_parent_id is null)" in _SQL
    assert "or t.date >= p_recent_from" in _SQL
    assert "order by t.created_at desc" in _SQL
    # "Is there a previous week": `.eq("is_recurring", False)`: here `NULL`
    # isn't counted
    assert "and t.is_recurring = false" in _SQL
    # Categories: the same order as `_fetch_categories`
    assert "order by c.sort_order nulls last, c.name" in _SQL
