"""The receipt path comes from the client: the server checks it, and doesn't
delete a file still in use.

Storage already blocks another family (the receipts_family_* policies). What was
left open was within the family, and only with a hand-crafted request: attaching
transaction A's receipt to transaction B. Then deleting B deleted the file, and
transaction A (a fridge, a warranty) was left with a broken receipt.
"""
import pytest

from backend import supabase_config as db
from backend.app import app, limiter

from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM   = "11111111-1111-1111-1111-111111111111"
_OTHER = "99999999-9999-9999-9999-999999999999"
_ME    = "22222222-2222-2222-2222-222222222222"
_CAT   = "33333333-3333-3333-3333-333333333333"
_FRIDGE, _COFFEE = "aaaaaaaa-0000-0000-0000-00000000000a", "aaaaaaaa-0000-0000-0000-00000000000b"
_FILE  = f"{_FAM}/5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b.jpg"


def _tx(tid, **kw):
    return {"id": tid, "family_id": _FAM, "type": "expense", "amount": 10.0, "date": "2026-09-01",
            "category_id": _CAT, "project_id": None, "receipt_path": None, **kw}


@pytest.fixture
def env(monkeypatch):
    fake = FakeSupabase(
        transactions=[_tx(_FRIDGE, receipt_path=_FILE), _tx(_COFFEE, receipt_path=_FILE)],
        categories=[{"id": _CAT, "family_id": _FAM, "name": "x", "type": "expense", "is_custom": True}],
        families=[{"id": _FAM, "name": "f", "settings": {}, "manager_id": _ME}])
    deleted = []
    monkeypatch.setattr(db, "get_client", lambda: fake)
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "get_categories", lambda fam=None: fake.rows("categories"))
    monkeypatch.setattr(db, "delete_receipts", lambda token, paths: deleted.extend(p for p in paths if p))
    for name, value in (("materialize_recurring", (0, True)), ("personal_project_owner", None),
                        ("recurring_occurrence", (None, True)), ("is_recurring_instance", False),
                        ("existing_occurrence_dates", None),
                        ("transaction_links", {"user_id": None, "project_id": None})):
        monkeypatch.setattr(db, name, lambda *a, _v=value, **k: _v)
    limiter.reset()
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"], sess["family_id"] = _ME, _FAM
        yield c, fake, deleted


# ─── The path itself ────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    f"{_OTHER}/5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b.jpg",   # another family's folder
    f"{_FAM}/../{_OTHER}/x.jpg",                            # escaping the folder
    f"{_FAM}/receipt.jpg",                                  # a name the app doesn't create
    "x" * 300,
], ids=["other-family", "traversal", "made-up-name", "garbage"])
def test_a_path_the_app_did_not_create_is_refused(env, path):
    client, fake, _ = env
    before = len(fake.rows("transactions"))

    r = client.post("/api/transactions", json={"amount": 5, "type": "expense", "date": "2026-09-02",
                                               "category_id": _CAT, "receipt_path": path})

    assert r.status_code == 422
    assert len(fake.rows("transactions")) == before


def test_a_path_the_app_created_is_accepted(env):
    """Control."""
    client, fake, _ = env

    r = client.post("/api/transactions", json={"amount": 5, "type": "expense", "date": "2026-09-02",
                                               "category_id": _CAT, "receipt_path": _FILE})

    assert r.status_code == 201, r.get_json()
    assert fake.rows("transactions")[-1]["receipt_path"] == _FILE


# ─── Deletion ───────────────────────────────────────────────────────────────

def test_deleting_the_coffee_keeps_the_fridge_receipt(env):
    client, _, deleted = env

    assert client.delete(f"/api/transactions/{_COFFEE}").status_code == 200
    assert deleted == [], "the fridge's receipt was deleted with the coffee"


def test_deleting_the_last_user_of_a_file_deletes_it(env):
    """Control: a receipt nobody points to is still deleted with the transaction."""
    client, _, deleted = env
    client.delete(f"/api/transactions/{_COFFEE}")

    assert client.delete(f"/api/transactions/{_FRIDGE}").status_code == 200
    assert deleted == [_FILE]


def test_when_the_check_fails_the_file_stays(env, monkeypatch):
    """"Unknown if in use": better a redundant file in storage than a broken receipt."""
    client, fake, deleted = env
    fake.tables["transactions"] = [_tx(_FRIDGE, receipt_path=_FILE)]
    def down(*a):
        raise db.DataUnavailable("down")
    monkeypatch.setattr(db, "receipts_in_use", down)

    assert client.delete(f"/api/transactions/{_FRIDGE}").status_code == 200
    assert deleted == []


def test_deleting_a_project_keeps_files_used_outside_it(env, monkeypatch):
    client, fake, deleted = env
    fake.tables["transactions"][1]["project_id"] = "p1"          # the coffee in the project, the fridge not
    monkeypatch.setattr(db, "get_project_for_transaction",
                        lambda pid, fam: {"id": pid, "owner_id": None})
    def delete_project(pid, fam):
        fake.tables["transactions"] = [t for t in fake.rows("transactions") if t["project_id"] != pid]
        return True, 1
    monkeypatch.setattr(db, "delete_project", delete_project)

    assert client.delete("/api/projects/p1").status_code == 200
    assert deleted == []
