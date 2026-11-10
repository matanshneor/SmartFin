"""The settings page in one trip to the database (Matan, 13.10: "as fast as
possible").

A sibling of test_home_page_rpc.py: get_settings_page replaces nine sequential
fetches (300-530ms in the logs), and the calculations stay the same functions.
"""
from datetime import date
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_SQL = (_ROOT / "backend/supabase/migrations/20261013120100_settings_page_fn.sql").read_text(encoding="utf-8")
_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"
_OTHER = "33333333-3333-3333-3333-333333333333"
_TODAY = date(2026, 10, 13)


class _FakeRpc:
    def __init__(self, data):
        self.data, self.calls = data, []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return self

    def execute(self):
        return self


def _rent(id_, amount, date_, **over):
    row = {"id": id_, "type": "expense", "amount": amount, "date": date_, "description": "שכר דירה",
           "category_id": "c1", "is_recurring": True, "recurring_end_date": None,
           "project_id": None, "user_id": _ME, "categories": {"name": "דיור", "icon": "🏠"},
           "project_categories": None, "profiles": {"name": "מתן", "workplace": None},
           "projects": None}
    row.update(over)
    return row


def _payload(**over):
    data = {
        "family": {"id": _FAM, "name": "שניאור", "settings": {}},
        "members": [{"id": _ME, "name": "מתן שניאור"}],
        "categories": [{"id": "c1", "name": "דיור", "type": "expense"}],
        "category_totals": {"c1": 15000, "c2": 0},
        "recurring": [
            _rent("new", 5500, "2026-10-01"),
            # a recurring payment in someone else's personal project: not shown (_filter_hidden_personal_projects)
            _rent("secret", 300, "2026-10-01", project_id="p9",
                  projects={"owner_id": _OTHER, "name": "x", "icon": "🔒"}),
        ],
        "ended": [{"id": "old", "amount": 5000, "type": "expense", "category_id": "c1",
                   "description": "שכר דירה", "recurring_end_date": "2026-09-30"}],
        "ended_last": {"old": 5000},
        "transaction_count": 42,
    }
    data.update(over)
    return data


@pytest.fixture
def real_fetch(monkeypatch):
    monkeypatch.setattr(db, "fetch_settings_page", db._real_fetch_settings_page)

    def run(data):
        fake = _FakeRpc(data)
        monkeypatch.setattr(db, "get_client", lambda: fake)
        return db.fetch_settings_page(_FAM, _TODAY, _ME), fake.calls
    return run


def test_one_call_with_the_same_three_month_window(real_fetch):
    _, calls = real_fetch(_payload())

    assert calls == [("get_settings_page", {
        "p_family_id": _FAM, "p_avg_from": "2026-07-01", "p_avg_to": "2026-10-01"})]
    assert db.averages_window(_TODAY) == ("2026-07-01", "2026-10-01")


def test_the_figures_are_the_same_functions_as_before(real_fetch):
    page, _ = real_fetch(_payload())

    # ₪15,000 over three months → ₪5,000 a month; a category with no expense doesn't appear
    assert page["cat_averages"] == {"c1": 5000}
    assert page["tx_count"] == 42
    assert page["members"][0]["name"] == "מתן" and page["members"][0]["full_name"] == "מתן שניאור"
    assert [r["id"] for r in page["recurring"]] == ["new"]
    # "₪5,000 ← ₪5,500 מאוקטובר"
    assert db.price_changes_from(page["recurring"], page["ended"], page["ended_last"]) == {
        "new": {"old": 5000.0, "since": "2026-10-01"}}


@pytest.mark.parametrize("data", [None, {}])
def test_someone_elses_family_or_a_failure_is_not_an_empty_family(real_fetch, data):
    with pytest.raises(db.DataUnavailable):
        real_fetch(data)


def test_the_settings_page_makes_no_other_trip(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)

    def separate(*a, **k):
        raise AssertionError("שליפה נפרדת — היה אמור להגיע מ-get_settings_page")
    # _fetch_* and not get_*: these go through the request cache, which the new path fills
    for fn in ("_fetch_categories", "_fetch_family_members", "_fetch_family",
               "category_monthly_averages", "get_recurring_transactions",
               "recurring_price_changes", "family_transaction_count", "get_profile"):
        monkeypatch.setattr(db, fn, separate)
    page = {
        "family": {"id": _FAM, "name": "שניאור", "invite_code": "K4F2QX",
                   "invite_code_expires_at": "2099-01-01T00:00:00+00:00"},
        "settings": dict(db.DEFAULT_FAMILY_SETTINGS),
        "members": [{"id": _ME, "name": "מתן", "full_name": "מתן שניאור", "email": "m@x.com",
                     "phone": "0501234567", "workplace": ""}],
        "categories": [{"id": "c1", "name": "דיור", "type": "expense", "icon": "🏠"}],
        "cat_averages": {"c1": 5000},
        "recurring": [], "ended": [], "ended_last": {}, "tx_count": 7,
    }
    monkeypatch.setattr(db, "fetch_settings_page", lambda *a, **k: page)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        res = c.get("/settings")

    assert res.status_code == 200
    assert "מתן שניאור" in res.get_data(as_text=True)


def test_the_function_guards_the_family_like_its_sisters():
    assert "security definer" in _SQL
    assert "where p_family_id = public.get_my_family_id();" in _SQL
    assert "from public, anon;" in _SQL
    assert "to authenticated;" in _SQL


def test_each_part_mirrors_the_query_it_replaces():
    # averages: without projects, window [from, to)
    assert "and t.project_id is null" in _SQL
    assert "and t.date >= p_avg_from" in _SQL and "and t.date <  p_avg_to" in _SQL
    # templates from largest to smallest, like .order("amount", desc=True)
    assert ") order by t.amount desc" in _SQL
    # the last occurrence of every template that ended
    assert "distinct on (i.recurring_parent_id)" in _SQL
    assert "order by i.recurring_parent_id, i.date desc" in _SQL
    assert "order by c.sort_order nulls last, c.name" in _SQL
