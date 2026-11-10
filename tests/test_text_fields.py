"""A text field that receives something else answers "ערך לא תקין", not "server
error".

The forms always send text, so a normal user doesn't hit this. But a hand-crafted
request with {"first_name": 123} crashed the route on .strip() of a number: a 500,
recorded on our side as a real fault. Found in an automated scan of all the
writing routes.
"""
import pytest

from backend import supabase_config as db
from backend.app import app, limiter

from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"
_CAT = "33333333-3333-3333-3333-333333333333"
_P   = "44444444-4444-4444-4444-444444444444"

_PROJECT = {"name": "p", "description": "", "budget_target": 100, "icon": "🏠",
            "track_expense": True, "track_income": False, "track_savings": False}
_TX = {"amount": 10, "type": "expense", "date": "2026-09-01", "category_id": _CAT}

_CASES = [
    ("put",  "/api/profile", {"first_name": "a", "last_name": "b", "phone": "", "workplace": ""},
     ["first_name", "last_name", "phone", "workplace"]),
    ("put",  "/api/family", {"name": "n"}, ["name"]),
    ("post", "/api/family/join", {"code": "ABC123"}, ["code"]),
    ("post", "/api/auth/forgot", {"email": "a@b.co"}, ["email"]),
    ("post", "/api/projects", _PROJECT, ["name", "description", "icon"]),
    ("put",  f"/api/projects/{_P}", _PROJECT, ["name", "description", "icon"]),
    ("post", "/api/transactions", _TX, ["description"]),
    ("put",  f"/api/transactions/{_CAT}", _TX, ["description"]),
]
_PARAMS = [(m, url, base, key) for m, url, base, keys in _CASES for key in keys]


@pytest.fixture
def client(monkeypatch):
    fake = FakeSupabase(
        transactions=[{"id": _CAT, "family_id": _FAM, "type": "expense", "amount": 1.0,
                       "date": "2026-09-01", "category_id": _CAT, "project_id": None}],
        categories=[{"id": _CAT, "family_id": _FAM, "name": "x", "type": "expense", "is_custom": True}],
        families=[{"id": _FAM, "name": "f", "settings": {}, "manager_id": _ME}],
        projects=[{"id": _P, "family_id": _FAM, "name": "p", "owner_id": None, "track_expense": True}])
    monkeypatch.setattr(db, "get_client", lambda: fake)
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "get_categories", lambda fam=None: fake.rows("categories"))
    monkeypatch.setattr(db, "get_family_members", lambda fid: [{"id": _ME, "name": "מ"}])
    monkeypatch.setattr(db, "get_project_for_transaction", lambda pid, fam: fake.rows("projects")[0])
    monkeypatch.setattr(db, "send_reset_email", lambda email, redirect_to: (True, None))
    for name, value in (("materialize_recurring", (0, True)), ("personal_project_owner", None),
                        ("existing_occurrence_dates", None), ("is_recurring_instance", False),
                        ("get_transaction_receipt_path", None),
                        ("transaction_links", {"user_id": None, "project_id": None})):
        monkeypatch.setattr(db, name, lambda *a, _v=value, **k: _v)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _send(client, method, url, body):
    limiter.reset()
    with client.session_transaction() as sess:
        sess["user_id"], sess["family_id"] = _ME, _FAM
    return getattr(client, method)(url, json=body)


@pytest.mark.parametrize("bad", [123, ["x"], {"a": 1}, True], ids=["number", "list", "object", "bool"])
@pytest.mark.parametrize("method,url,base,key", _PARAMS,
                         ids=[f"{u.split('/')[2]}-{k}" for _, u, _, k in _PARAMS])
def test_a_non_text_value_is_refused_clearly(client, method, url, base, key, bad):
    r = _send(client, method, url, {**base, key: bad})

    assert r.status_code == 422, r.get_json()
    assert r.get_json()["error"] == "ערך לא תקין"


@pytest.mark.parametrize("method,url,base,key", _PARAMS,
                         ids=[f"{u.split('/')[2]}-{k}" for _, u, _, k in _PARAMS])
def test_text_still_goes_through(client, method, url, base, key):
    """Control: a valid value isn't rejected because of the new check."""
    r = _send(client, method, url, dict(base))

    assert not (r.status_code == 422 and r.get_json().get("error") == "ערך לא תקין")
    assert r.status_code < 500, r.get_json()
