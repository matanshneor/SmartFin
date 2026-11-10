"""The projects and comparison pages in one trip to the database (Matan, 13.10)."""
from datetime import date
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_SQL = (_ROOT / "backend/supabase/migrations/20261013130000_projects_and_months_page_fn.sql").read_text(encoding="utf-8")
_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"
_OTHER = "33333333-3333-3333-3333-333333333333"


class _FakeRpc:
    def __init__(self, data):
        self.data, self.calls = data, []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return self

    def execute(self):
        return self


def _proj(pid, **over):
    p = {"id": pid, "name": pid, "owner_id": None, "archived": False, "budget_target": None,
         "track_expense": True, "track_income": False, "track_savings": False}
    p.update(over)
    return p


@pytest.fixture
def fake(monkeypatch):
    def install(name, data):
        monkeypatch.setattr(db, name, getattr(db, "_real_" + name))
        rpc = _FakeRpc(data)
        monkeypatch.setattr(db, "get_client", lambda: rpc)
        return rpc
    return install


def test_projects_page_splits_active_and_finished_and_hides_others_personal(fake):
    rpc = fake("fetch_projects_page", {
        "family": {"id": _FAM, "settings": {}},
        "members": [{"id": _ME, "name": "מתן שניאור"}],
        "projects": [_proj("trip", budget_target=12000), _proj("old", archived=True),
                     _proj("secret", owner_id=_OTHER), _proj("mine", owner_id=_ME)],
        "totals": {"trip": {"expense": 4200, "income": 0, "savings": 0}},
        "last_activity": {"trip": "2026-09-10"},
    })

    page = db.fetch_projects_page(_FAM, _ME)

    assert rpc.calls == [("get_projects_page", {"p_family_id": _FAM})]
    assert [p["id"] for p in page["active"]] == ["trip", "mine"]
    assert [p["id"] for p in page["finished"]] == ["old"]
    trip = page["active"][0]
    assert trip["spent"] == 4200 and trip["amount"] == -4200 and trip["budget_target"] == 12000
    assert page["last_activity"] == {"trip": "2026-09-10"}
    assert page["members"][0]["name"] == "מתן"


def test_months_window_is_the_trend_window():
    assert db.months_window(date(2026, 10, 13)) == ("2025-11-01", "2026-11-01")
    assert db.months_window(date(2027, 1, 1)) == ("2026-02-01", "2027-02-01")


def test_months_page_one_call(fake):
    rpc = fake("fetch_months_page", {"family": {"id": _FAM}, "members": [], "categories": [],
                                     "archive": [], "expense_rows": []})

    db.fetch_months_page(_FAM, date(2026, 10, 13))

    assert rpc.calls == [("get_months_page", {"p_family_id": _FAM, "p_from": "2025-11-01",
                                              "p_to": "2026-11-01"})]


@pytest.mark.parametrize("name", ["fetch_projects_page", "fetch_months_page"])
@pytest.mark.parametrize("data", [None, {}])
def test_someone_elses_family_or_a_failure_is_not_empty(fake, name, data):
    fake(name, data)
    with pytest.raises(db.DataUnavailable):
        getattr(db, name)(_FAM, *(["x"] if name == "fetch_projects_page" else [date(2026, 10, 1)]))


def test_one_scan_feeds_both_trends():
    months = [(2026, 9), (2026, 10)]
    rows = [{"id": "a", "amount": 100, "category_id": "food", "date": "2026-09-03", "description": "",
             "user_id": _ME, "profiles": {"name": "מתן שניאור"}},
            {"id": "b", "amount": 50, "category_id": "food", "date": "2026-10-03", "description": "",
             "user_id": None, "profiles": None},
            # outside the window: discarded
            {"id": "c", "amount": 999, "category_id": "food", "date": "2025-11-03", "description": "",
             "user_id": _ME, "profiles": {"name": "מתן"}}]
    cats = [{"id": "food", "name": "סופר", "icon": "🛒", "type": "expense"}]

    trend = db.category_trend_from_rows(rows, months, cats, today=date(2026, 10, 13))
    members = db.member_trend_from_rows(rows, months)

    assert trend["categories"][0]["values"] == [100, 50]
    assert {m["name"]: m["values"] for m in members} == {"מתן": [100, 0], "משותפת": [0, 50]}


def _client():
    app.config["TESTING"] = True
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = _ME
        sess["family_id"] = _FAM
    return c


def test_the_projects_page_makes_no_other_trip(monkeypatch):
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    for fn in ("get_projects", "project_last_activity", "_fetch_family", "_fetch_family_members"):
        monkeypatch.setattr(db, fn, lambda *a, **k: pytest.fail("separate query"))
    monkeypatch.setattr(db, "fetch_projects_page", lambda *a, **k: {
        "family": {"id": _FAM}, "settings": dict(db.DEFAULT_FAMILY_SETTINGS),
        "members": [{"id": _ME, "name": "מתן"}],
        "active": [db.projects_from_rows([_proj("trip", name="טיול ליוון")], {}, _ME)[0]],
        "finished": [], "last_activity": {}})

    res = _client().get("/projects")

    assert res.status_code == 200 and "טיול ליוון" in res.get_data(as_text=True)


def test_the_months_page_makes_no_other_trip(monkeypatch):
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    for fn in ("get_months_archive", "category_trend", "member_trend",
               "_fetch_categories", "_fetch_family", "_fetch_family_members"):
        monkeypatch.setattr(db, fn, lambda *a, **k: pytest.fail("separate query"))
    monkeypatch.setattr(db, "fetch_months_page", lambda *a, **k: {
        "family": {"id": _FAM}, "settings": dict(db.DEFAULT_FAMILY_SETTINGS),
        "members": [{"id": _ME, "name": "מתן"}], "categories": [],
        "archive": [{"year": 2026, "month": 10, "income": 100, "expense": 50, "savings": 0, "balance": 50}],
        "expense_rows": []})

    assert _client().get("/months").status_code == 200


def test_the_functions_guard_the_family_like_their_sisters():
    assert _SQL.count("security definer") == 2
    assert _SQL.count("where p_family_id = public.get_my_family_id();") == 2
    assert _SQL.count("from public, anon;") == 2
