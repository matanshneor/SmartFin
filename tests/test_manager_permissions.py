"""
The "family manager" role was a tag, not a permission.

It was added so someone would be responsible, and enforced on one action only:
removing a member. Everything else stayed open to anyone who joined with a
six-character code, above all `/api/account/reset`: its `scope` is "the whole
family" by default, there is no manager check, and the only verification is the
caller's password. So a single member could delete the whole family's money
history.

And a second gap paralysed the role itself: `delete_my_account` was written two
months before `manager_id` was added and wasn't updated. A manager who deleted
their account left the field empty, and from then on `remove_family_member`
failed forever (`NULL is distinct from <uuid>` is always true), and no code
anywhere appoints a manager again.
"""
import pytest

from backend import app as app_module
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _fresh_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "sign_in",
                        lambda e, p: (type("R", (), {"session": type("S", (), {
                            "access_token": "t"})()})(), None))
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda t: None)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]    = _ME
            sess["family_id"]  = _FAM
            sess["user_email"] = "me@example.com"
        yield c


def _manager(monkeypatch, yes):
    monkeypatch.setattr(app_module.db, "is_family_manager", lambda: yes)


def _reset(client, scope="family"):
    return client.post("/api/account/reset", json={"password": "pw", "scope": scope})


# ─── Resetting the family's transactions ────────────────────────────────────

def test_a_plain_member_cannot_erase_the_family_history(client, monkeypatch):
    """The heart. Until now this worked."""
    _manager(monkeypatch, False)
    monkeypatch.setattr(app_module.db, "reset_transactions",
                        lambda *a, **k: pytest.fail("the family's history was deleted by an ordinary member"))

    res = _reset(client)

    assert res.status_code == 403
    assert "רק מנהל המשפחה" in res.get_json()["error"]


def test_the_manager_still_can(client, monkeypatch):
    """Control: the feature itself must keep working."""
    _manager(monkeypatch, True)
    monkeypatch.setattr(app_module.db, "reset_transactions", lambda *a, **k: (12, None))

    res = _reset(client)

    assert res.status_code == 200
    assert res.get_json() == {"status": "ok", "deleted": 12}


def test_a_plain_member_can_still_erase_only_their_own(client, monkeypatch):
    """An important control: this is the requester's own money, and there is no
    reason to block it."""
    _manager(monkeypatch, lambda: pytest.fail("a manager permission was checked on 'only mine'"))
    monkeypatch.setattr(app_module.db, "is_family_manager",
                        lambda: pytest.fail("a manager permission was checked on 'only mine'"))
    seen = {}
    monkeypatch.setattr(app_module.db, "reset_transactions",
                        lambda fam, only_user_id=None: (seen.update(u=only_user_id) or (3, None)))

    assert _reset(client, scope="mine").status_code == 200
    assert seen["u"] == _ME


# ─── The two other destructive actions ──────────────────────────────────────

def test_a_plain_member_cannot_delete_a_category(client, monkeypatch):
    _manager(monkeypatch, False)
    monkeypatch.setattr(app_module.db, "get_client",
                        lambda: pytest.fail("a category was deleted by an ordinary member"))

    assert client.delete("/api/categories/abc").status_code == 403


def test_a_plain_member_cannot_rotate_the_invite_code(client, monkeypatch):
    """Replacing the code also invalidates invitations already sent."""
    _manager(monkeypatch, False)
    monkeypatch.setattr(app_module.db, "rotate_invite_code",
                        lambda: pytest.fail("the code was replaced by an ordinary member"))

    assert client.post("/api/family/invite-code").status_code == 403


def test_the_manager_can_rotate_it(client, monkeypatch):
    _manager(monkeypatch, True)
    monkeypatch.setattr(app_module.db, "rotate_invite_code", lambda: ("ABC123", None))

    res = client.post("/api/family/invite-code")

    assert res.get_json() == {"status": "ok", "invite_code": "ABC123"}


# ─── What stays shared ──────────────────────────────────────────────────────

def test_budgets_and_settings_stay_shared(client, monkeypatch):
    """An explicit decision: budgets, alerts and the family name are decisions the
    family makes together, not the manager's."""
    monkeypatch.setattr(app_module.db, "is_family_manager",
                        lambda: pytest.fail("a manager permission was required for shared settings"))
    monkeypatch.setattr(app_module.db, "update_family_settings", lambda *a, **k: True)
    monkeypatch.setattr(app_module.db, "get_family_settings",
                        lambda fid: dict(app_module.db.DEFAULT_FAMILY_SETTINGS))
    monkeypatch.setattr(app_module, "family_settings",
                        lambda: dict(app_module.db.DEFAULT_FAMILY_SETTINGS))

    res = client.put("/api/family/settings", json={"owner_attribution": {"expense": True}})

    assert res.status_code == 200


# ─── "Unknown" isn't "yes" ──────────────────────────────────────────────────

def test_an_unverifiable_permission_is_refused(client, monkeypatch):
    def _boom():
        raise app_module.db.DataUnavailable("is_family_manager")
    monkeypatch.setattr(app_module.db, "is_family_manager", _boom)
    monkeypatch.setattr(app_module.db, "reset_transactions",
                        lambda *a, **k: pytest.fail("deleted without knowing who is asking"))

    assert _reset(client).status_code == 503


# ─── The rule is enforced in the database, not only by hiding a button ──────

def _migration():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent
            / "backend/supabase/migrations/20260921210000_manager_only_destructive.sql"
            ).read_text(encoding="utf-8")


def test_the_check_is_derived_from_the_token_not_a_parameter():
    """A function that takes an id as a parameter can be asked about someone else."""
    sql = _migration()
    body = sql[sql.index("create or replace function public.is_family_manager"):]
    body = body[:body.index("$$;")]

    assert "auth.uid()" in body
    assert "returns boolean" in body


def test_the_manager_role_is_handed_over_when_the_account_is_deleted():
    """Verified in a rollback against the live database: a manager deleted their
    account, and management passed to a member who stayed. Without this the
    family can never remove a member."""
    sql = _migration()
    body = sql[sql.index("create or replace function public.delete_my_account"):]

    assert "update public.families set manager_id = v_heir" in body
    assert "order by p.created_at limit 1" in body


def test_anon_cannot_ask():
    sql = _migration()

    assert "revoke all on function public.is_family_manager() from public, anon" in sql
