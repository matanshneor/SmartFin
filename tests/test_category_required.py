"""No transaction without a category.

Until now there were three ways to end up with such a transaction: saving
without choosing, deleting a category (its transactions were left
"uncategorised"), and the same with a project category. Matan's decision: no
such state in the app.

- Saving without a category is rejected on the server, not only in the form.
- Deleting a category that has transactions requires choosing where to move
  them, and they are moved **before** the category is deleted.
- The last category of a type isn't deleted: without it no transaction of that
  type can be recorded at all.
"""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter

from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"


def _user():
    return {"id": _ME, "family_id": _FAM}


def _cat(cid, name, type_="expense"):
    return {"id": cid, "family_id": _FAM, "name": name, "icon": "📦",
            "type": type_, "is_custom": True, "sort_order": 1}


def _pcat(cid, name, type_="expense", project="p1"):
    return {"id": cid, "project_id": project, "family_id": _FAM,
            "name": name, "icon": "🧱", "type": type_}


def _tx(tid, **kw):
    return {"id": tid, "family_id": _FAM, "type": "expense", "amount": 10.0,
            "date": "2026-09-01", "project_id": None, "category_id": None,
            "project_category_id": None, **kw}


_PROJECT = {"id": "p1", "family_id": _FAM, "owner_id": None, "name": "שיפוץ",
            "track_expense": True, "track_income": False, "track_savings": False}


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabase(
        categories=[_cat("rest", "מסעדות"), _cat("fun", "בילויים"),
                    _cat("salary", "משכורת", "income")],
        projects=[dict(_PROJECT)],
        families=[{"id": _FAM, "name": "שניאור", "settings": {}, "manager_id": _ME}],
        project_categories=[_pcat("paint", "צבע"), _pcat("tiles", "אריחים"),
                            _pcat("gift", "מתנה", "income")],
        transactions=[_tx("t1", category_id="rest"), _tx("t2", category_id="rest"),
                      _tx("t3", category_id="fun"),
                      _tx("t4", project_id="p1", project_category_id="paint")],
    )
    monkeypatch.setattr(db, "get_client", lambda: f)
    # The real query uses `or_` and the per-request cache; here the table is enough
    monkeypatch.setattr(db, "get_categories",
                        lambda fam=None: [c for c in f.rows("categories") if c["family_id"] == fam])
    return f


@pytest.fixture
def client(fake, monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "is_family_manager", lambda: True)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        yield c


def _cats_of(fake, tids):
    return {t["id"]: t.get("category_id") or t.get("project_category_id")
            for t in fake.rows("transactions") if t["id"] in tids}


# ─── Saving ─────────────────────────────────────────────────────────────────

@pytest.fixture
def ctx():
    with app.test_request_context():
        yield


def test_a_household_transaction_without_a_category_is_refused(fake, ctx):
    _, err = app_module._validated_category(None, _user(), "expense")

    assert err and "קטגוריה" in err


def test_a_household_transaction_with_a_category_is_accepted(fake, ctx):
    """Control."""
    assert app_module._validated_category("rest", _user(), "expense") == ("rest", None)


def test_a_project_transaction_without_a_project_category_is_refused(fake, ctx, monkeypatch):
    monkeypatch.setattr(db, "get_project_for_transaction", lambda pid, fam: dict(_PROJECT))

    result = app_module._apply_project_assignment(
        {"project_id": "p1"}, _user(), "expense")

    assert result[-1] and "קטגוריה" in result[-1]


def test_a_project_transaction_with_a_project_category_is_accepted(fake, ctx, monkeypatch):
    monkeypatch.setattr(db, "get_project_for_transaction", lambda pid, fam: dict(_PROJECT))

    result = app_module._apply_project_assignment(
        {"project_id": "p1", "project_category_id": "paint"}, _user(), "expense")

    assert result[-1] is None and result[1] == "paint"


def test_the_route_refuses_before_writing(client, fake):
    """A 422 that arrives **after** it was already saved isn't a rejection."""
    before = len(fake.rows("transactions"))

    r = client.post("/api/transactions", json={
        "amount": 50, "type": "expense", "date": "2026-09-05", "category_id": None})

    assert r.status_code == 422
    assert len(fake.rows("transactions")) == before


# ─── Deleting a family category ─────────────────────────────────────────────

def test_usage_reports_the_count_and_where_they_can_go(client):
    d = client.get("/api/categories/rest/usage").get_json()

    assert d["count"] == 2
    assert [c["id"] for c in d["alternatives"]] == ["fun"], "income isn't a target for expenses"
    assert d["blocked"] is None


def test_deleting_a_used_category_without_a_target_is_refused(client, fake):
    r = client.delete("/api/categories/rest")

    assert r.status_code == 409
    assert any(c["id"] == "rest" for c in fake.rows("categories"))
    assert _cats_of(fake, {"t1", "t2"}) == {"t1": "rest", "t2": "rest"}


def test_deleting_with_a_target_moves_the_transactions_first(client, fake):
    r = client.delete("/api/categories/rest?move_to=fun")

    assert r.status_code == 200, r.get_json()
    assert not any(c["id"] == "rest" for c in fake.rows("categories"))
    assert _cats_of(fake, {"t1", "t2", "t3"}) == {"t1": "fun", "t2": "fun", "t3": "fun"}
    ops = [(t, op) for t, op, _ in fake.writes]
    assert ops.index(("transactions", "update")) < ops.index(("categories", "delete"))


@pytest.mark.parametrize("target", ["salary", "rest", "nope"],
                         ids=["other-type", "itself", "unknown"])
def test_a_bad_target_is_refused(client, fake, target):
    r = client.delete(f"/api/categories/rest?move_to={target}")

    assert r.status_code == 409
    assert _cats_of(fake, {"t1"}) == {"t1": "rest"}
    assert any(c["id"] == "rest" for c in fake.rows("categories"))


def test_an_unused_category_is_deleted_without_asking(client, fake):
    fake.tables["transactions"] = [t for t in fake.rows("transactions")
                                   if t.get("category_id") != "rest"]

    assert client.delete("/api/categories/rest").status_code == 200
    assert not any(c["id"] == "rest" for c in fake.rows("categories"))


def test_the_last_category_of_a_type_cannot_be_deleted(client, fake):
    """Without "משכורת" there is no income category at all, and no income can be
    recorded."""
    usage = client.get("/api/categories/salary/usage").get_json()
    r = client.delete("/api/categories/salary")

    assert usage["blocked"]
    assert r.status_code == 409
    assert any(c["id"] == "salary" for c in fake.rows("categories"))


def test_a_category_of_another_family_is_not_found(client, fake):
    fake.tables["categories"].append({**_cat("theirs", "זר"), "family_id": "other"})

    assert client.get("/api/categories/theirs/usage").status_code == 404
    assert client.delete("/api/categories/theirs?move_to=fun").status_code == 404


def test_a_plain_member_still_cannot_delete(client, monkeypatch, fake):
    monkeypatch.setattr(db, "is_family_manager", lambda: False)

    assert client.delete("/api/categories/rest?move_to=fun").status_code == 403
    assert _cats_of(fake, {"t1"}) == {"t1": "rest"}


# ─── Deleting a project category ────────────────────────────────────────────

@pytest.fixture
def project_gate(monkeypatch):
    # Like the real function: no `id` and no name, only the owner and the
    # tracking flags
    project = {k: _PROJECT[k] for k in
               ("owner_id", "track_expense", "track_income", "track_savings")}
    monkeypatch.setattr(db, "get_project_for_transaction", lambda pid, fam: project)
    return project


def test_project_usage_reports_the_count_and_where_they_can_go(client, project_gate):
    d = client.get("/api/projects/p1/categories/paint/usage").get_json()

    assert d["count"] == 1
    assert [c["id"] for c in d["alternatives"]] == ["tiles"]


def test_deleting_a_used_project_category_needs_a_target(client, fake, project_gate):
    assert client.delete("/api/projects/p1/categories/paint").status_code == 409
    assert _cats_of(fake, {"t4"}) == {"t4": "paint"}

    r = client.delete("/api/projects/p1/categories/paint?move_to=tiles")

    assert r.status_code == 200, r.get_json()
    assert _cats_of(fake, {"t4"}) == {"t4": "tiles"}
    assert not any(c["id"] == "paint" for c in fake.rows("project_categories"))


def test_a_category_of_another_project_is_not_a_target(client, fake, project_gate):
    fake.tables["project_categories"].append(_pcat("elsewhere", "אחר", project="p2"))

    r = client.delete("/api/projects/p1/categories/paint?move_to=elsewhere")

    assert r.status_code == 409
    assert _cats_of(fake, {"t4"}) == {"t4": "paint"}


def test_the_last_category_of_a_tracked_type_stays(client, fake, project_gate):
    fake.tables["project_categories"] = [c for c in fake.rows("project_categories")
                                         if c["id"] != "tiles"]
    fake.tables["transactions"] = [t for t in fake.rows("transactions") if t["id"] != "t4"]

    assert client.delete("/api/projects/p1/categories/paint").status_code == 409


def test_the_last_category_of_an_untracked_unused_type_can_go(client, fake, project_gate):
    """The project doesn't track income, and it has no transactions: nothing to keep."""
    assert client.delete("/api/projects/p1/categories/gift").status_code == 200
