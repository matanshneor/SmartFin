""""בטל" after an edit (Matan, 30.9: round 6, item 10).

Undo is another edit with the previous values, and with if_match: the values our
edit left. If someone changed the transaction since, 409, and their change isn't
overwritten.
"""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_TX  = "22222222-2222-2222-2222-222222222222"
_CAT = "44444444-4444-4444-4444-444444444444"
_ME  = "33333333-3333-3333-3333-333333333333"

_AFTER = {"amount": 320, "type": "expense", "date": "2026-09-30", "description": "רמי לוי",
          "category_id": _CAT, "project_category_id": None, "user_id": None}


@pytest.fixture
def client(monkeypatch):
    state = {"row": dict(_AFTER, project_id=None), "writes": []}
    monkeypatch.setattr(app_module.db, "personal_project_owner", lambda *a: None)
    monkeypatch.setattr(app_module.db, "transaction_links", lambda *a: dict(state["row"]))
    monkeypatch.setattr(app_module.db, "get_categories",
                        lambda *a, **k: [{"id": _CAT, "name": "מכולת", "type": "expense"}])
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    monkeypatch.setattr(app_module.db, "is_recurring_instance", lambda *a: False)

    def update(tx_id, fam, payload):
        state["writes"].append(payload)
        return {"id": tx_id, **payload}, None
    monkeypatch.setattr(app_module.db, "update_transaction", update)
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        c.state = state
        yield c


def _undo(client, if_match):
    return client.put(f"/api/transactions/{_TX}", json={
        "amount": 230, "type": "expense", "date": "2026-09-30", "description": "רמי לוי",
        "category_id": _CAT, "if_match": if_match})


def test_undo_restores_when_nothing_changed_since(client):
    res = _undo(client, _AFTER)

    assert res.status_code == 200
    assert client.state["writes"][-1]["amount"] == 230


def test_undo_does_not_overwrite_someone_elses_later_edit(client):
    client.state["row"]["amount"] = 350          # Or fixed it in the meantime

    res = _undo(client, _AFTER)

    assert res.status_code == 409 and res.get_json()["conflict"] is True
    assert "השתנתה" in res.get_json()["error"]
    assert client.state["writes"] == []


@pytest.mark.parametrize("field,value", [("description", "שופרסל"), ("date", "2026-09-29"),
                                         ("category_id", "55555555-5555-5555-5555-555555555555")])
def test_any_field_changed_since_blocks_the_undo(client, field, value):
    client.state["row"][field] = value
    assert _undo(client, _AFTER).status_code == 409


def test_money_is_compared_as_money(client):
    """320 from the database versus 320.0 from the browser: that is the same amount."""
    client.state["row"]["amount"] = "320.00"
    assert _undo(client, {**_AFTER, "amount": 320.0}).status_code == 200


def test_a_normal_edit_is_untouched(client):
    client.state["row"]["amount"] = 999
    res = client.put(f"/api/transactions/{_TX}", json={
        "amount": 230, "type": "expense", "date": "2026-09-30", "category_id": _CAT})
    assert res.status_code == 200
