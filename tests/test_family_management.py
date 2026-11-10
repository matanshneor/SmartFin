"""
Tests for managing family members: removal, leaving and replacing the invite code.

Until now whoever joined the family (by mistake, or with a code leaked in a
WhatsApp group) stayed in it forever. There was no route to remove them, no way
to leave, and the code was created once and couldn't be changed. So a leak of six
characters meant permanent access to the family's finances, and to every member's
phone number and workplace.

The model was completely flat. One role was added (the family manager), and it
is the only one allowed to remove. The rules are enforced in the DB; the tests
here check that the layer above doesn't soften them and that the UI doesn't offer
actions that will fail.
"""
from pathlib import Path

import pytest

from backend import app as app_module
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_ME    = "11111111-1111-1111-1111-111111111111"
_OTHER = "22222222-2222-2222-2222-222222222222"
_FAM   = "33333333-3333-3333-3333-333333333333"


deleted_receipts = []


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    limiter.reset()
    # The default here is manager: the tests in this file are about what the
    # actions do, not who may. The tests on the permission itself sit in a
    # separate file.
    monkeypatch.setattr(app_module.db, "is_family_manager", lambda: True)
    # Receipts: no projects and no files by default. Deleting files never goes
    # out to the real storage from a test: it is recorded here, and the tests
    # below read it.
    monkeypatch.setattr(app_module.db, "personal_project_ids", lambda fid, uid: [])
    monkeypatch.setattr(app_module.db, "receipt_paths", lambda *a, **k: [])
    deleted_receipts.clear()
    monkeypatch.setattr(app_module.db, "delete_receipts",
                        lambda token, paths: deleted_receipts.extend(paths))
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]   = _ME
            sess["family_id"] = _FAM
        yield c
    limiter.reset()


# ─── Removing a member ──────────────────────────────────────────────────────

def test_removing_a_member_passes_the_transaction_choice_through(client, monkeypatch):
    """The choice about the transactions is the user's; the server mustn't decide it
    silently."""
    seen = {}
    monkeypatch.setattr(app_module.db, "remove_family_member",
                        lambda uid, keep_transactions, projects="ask": (seen.update(
                            uid=uid, keep=keep_transactions) or (True, None)))

    client.delete(f"/api/family/members/{_OTHER}", json={"keep_transactions": False})

    assert seen == {"uid": _OTHER, "keep": False}


def test_keeping_transactions_is_the_default_when_nothing_is_said(client, monkeypatch):
    """A request with no explicit preference shouldn't delete money."""
    seen = {}
    monkeypatch.setattr(app_module.db, "remove_family_member",
                        lambda uid, keep_transactions, projects="ask": (seen.update(
                            keep=keep_transactions) or (True, None)))

    client.delete(f"/api/family/members/{_OTHER}", json={})

    assert seen["keep"] is True


def test_a_non_manager_is_refused_with_a_hebrew_reason(client, monkeypatch):
    """The DB refuses in English; the user needs to know why."""
    monkeypatch.setattr(app_module.db, "remove_family_member",
                        lambda uid, keep_transactions, projects="ask":
                            (False, "only the family manager can remove members"))

    response = client.delete(f"/api/family/members/{_OTHER}", json={})

    assert response.status_code == 403
    assert "מנהל המשפחה" in response.get_json()["error"]


# ─── Leaving ────────────────────────────────────────────────────────────────

def test_leaving_updates_the_session_to_the_new_family(client, monkeypatch):
    """Without this the session keeps pointing at the old family, every query is
    filtered by a family the user is no longer in, and the app looks empty."""
    monkeypatch.setattr(app_module.db, "leave_family",
                        lambda keep_transactions, projects="ask": ("44444444-4444-4444-4444-444444444444", None))

    client.post("/api/family/leave", json={})

    with client.session_transaction() as sess:
        assert sess["family_id"] == "44444444-4444-4444-4444-444444444444"


def test_a_failed_leave_does_not_move_the_session(client, monkeypatch):
    """Control: a failure mustn't leave the user pointing nowhere."""
    monkeypatch.setattr(app_module.db, "leave_family",
                        lambda keep_transactions, projects="ask": (None, "boom"))

    client.post("/api/family/leave", json={})

    with client.session_transaction() as sess:
        assert sess["family_id"] == _FAM


# ─── Personal projects of whoever leaves ─────────────────────────────────────
#
# Until 28.9.2026 they vanished: recorded under someone no longer in the family,
# hidden from everyone, and the money in them counted nowhere. Now whoever decides
# is asked: "delete" or "keep as shared". The database behaviour was checked in a
# simulation on the real database inside `begin … rollback`.

@pytest.mark.parametrize("sent", [{}, {"projects": "share"}, {"projects": "ask"}])
def test_a_removed_members_personal_projects_are_always_deleted(client, monkeypatch, sent):
    """Matan's decision: the manager isn't asked and doesn't choose, even with a
    request asking for "shared"."""
    seen = {}
    monkeypatch.setattr(app_module.db, "remove_family_member",
                        lambda uid, keep_transactions, projects:
                            (seen.update(projects=projects) or (True, None)))

    response = client.delete(f"/api/family/members/{_OTHER}", json=sent)

    assert response.status_code == 200
    assert seen["projects"] == "delete"


@pytest.mark.parametrize("sent,passed", [
    ({"projects": "share"}, "share"), ({"projects": "delete"}, "delete"),
    ({}, "ask"), ({"projects": "drop table"}, "ask"),
])
def test_whoever_leaves_decides_about_their_own_projects(client, monkeypatch, sent, passed):
    seen = {}
    monkeypatch.setattr(app_module.db, "leave_family",
                        lambda keep_transactions, projects:
                            (seen.update(projects=projects) or ("new-fam", None)))

    client.post("/api/family/leave", json=sent)

    assert seen["projects"] == passed


def test_leaving_with_undecided_projects_asks_and_touches_nothing(client, monkeypatch):
    """Nobody leaves and no file is deleted until the question is answered; the
    session doesn't move either."""
    monkeypatch.setattr(app_module.db, "personal_project_ids", lambda fid, uid: ["p1"])
    monkeypatch.setattr(app_module.db, "receipt_paths", lambda *a, **k: ["fam/r1.jpg"])
    monkeypatch.setattr(app_module.db, "leave_family",
                        lambda *a, **k: pytest.fail("left before being asked"))

    response = client.post("/api/family/leave", json={})

    assert response.status_code == 409
    assert response.get_json()["project_count"] == 1
    assert deleted_receipts == []
    with client.session_transaction() as sess:
        assert sess["family_id"] == _FAM


# ─── Receipts of what is deleted ────────────────────────────────────────────
#
# There is no nightly cleanup (`purge_orphan_receipts` failed on every run and was
# removed): Supabase blocks deleting directly from storage. So whatever is deleted
# on removal or leaving takes its files with it.

def _receipts_by(monkeypatch, mapping):
    """A fake `receipt_paths`: returns files according to what was asked, to check
    *what* was collected."""
    def fake(fid, project_ids=None, user_id=None):
        out = []
        for pid in project_ids or []:
            out += mapping.get(pid, [])
        if user_id:
            out += mapping.get(("own", user_id), [])
        return out
    monkeypatch.setattr(app_module.db, "receipt_paths", fake)
    # After the delete no transaction points at the files (see `_delete_unused_receipts`)
    monkeypatch.setattr(app_module.db, "receipts_in_use", lambda fid, paths: set())


@pytest.mark.parametrize("keep,expected", [
    (True,  ["proj.jpg"]),
    (False, ["proj.jpg", "own.jpg"]),
])
def test_removing_takes_the_receipts_of_whatever_it_deletes(client, monkeypatch, keep, expected):
    monkeypatch.setattr(app_module.db, "personal_project_ids", lambda fid, uid: ["p1"])
    _receipts_by(monkeypatch, {"p1": ["proj.jpg"], ("own", _OTHER): ["own.jpg"]})
    monkeypatch.setattr(app_module.db, "remove_family_member", lambda *a, **k: (True, None))

    client.delete(f"/api/family/members/{_OTHER}", json={"keep_transactions": keep})

    assert sorted(deleted_receipts) == sorted(expected)


def test_a_refused_removal_deletes_no_files(client, monkeypatch):
    _receipts_by(monkeypatch, {("own", _OTHER): ["own.jpg"]})
    monkeypatch.setattr(app_module.db, "remove_family_member",
                        lambda *a, **k: (False, "only the family manager can remove members"))

    client.delete(f"/api/family/members/{_OTHER}", json={"keep_transactions": False})

    assert deleted_receipts == []


@pytest.mark.parametrize("body,expected", [
    ({"projects": "share"},  []),
    ({"projects": "delete"}, ["proj.jpg"]),
    ({"projects": "share", "keep_transactions": False}, ["proj.jpg", "own.jpg"]),
])
def test_leaving_deletes_the_receipts_first_and_only_of_what_goes(client, monkeypatch, body, expected):
    """Before leaving: afterwards whoever left is no longer allowed to touch the
    family's folder."""
    order = []
    monkeypatch.setattr(app_module.db, "personal_project_ids", lambda fid, uid: ["p1"])
    _receipts_by(monkeypatch, {"p1": ["proj.jpg"], ("own", _ME): ["own.jpg"]})
    monkeypatch.setattr(app_module.db, "delete_receipts",
                        lambda t, paths: (order.append("files"), deleted_receipts.extend(paths)))
    monkeypatch.setattr(app_module.db, "leave_family",
                        lambda *a, **k: (order.append("leave") or ("new-fam", None)))

    client.post("/api/family/leave", json=body)

    assert sorted(deleted_receipts) == sorted(expected)
    assert order == ["files", "leave"]


def test_other_errors_are_not_mistaken_for_the_question():
    """A control for the parsing."""
    assert app_module.db.project_choice_needed("needs_project_choice:3") == 3
    assert app_module.db.project_choice_needed("only the family manager") == 0
    assert app_module.db.project_choice_needed(None) == 0


# ─── Replacing the code ─────────────────────────────────────────────────────

def test_rotating_returns_the_new_code_so_the_screen_can_show_it(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "rotate_invite_code", lambda: ("NEW123", None))

    response = client.post("/api/family/invite-code")

    assert response.get_json()["invite_code"] == "NEW123"


# ─── The UI ─────────────────────────────────────────────────────────────────

def test_escaping_the_transaction_dialog_keeps_the_money():
    """The trap. appConfirm returns false on Escape, on a tap outside the dialog and
    on ✕, so the destructive option must be the confirm button itself; otherwise
    every escape from the dialog deletes transactions. That is exactly the bug
    that exists today in deleting a project."""
    js = (_ROOT / "frontend/static/js/settings.js").read_text(encoding="utf-8")
    block = js[js.index("function askAboutTransactions"):][:900]

    assert "confirmText: 'למחוק אותן'" in block, \
        "the safe option is the confirm button: escaping the dialog will delete transactions"
    assert "return wipe ? WIPE : KEEP;" in block


def test_the_remove_button_is_only_offered_to_the_manager():
    """The rule is enforced in the DB anyway; hiding it here is so as not to offer
    an action that will fail."""
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")

    assert "user.id == family.get('manager_id')" in html
    assert "manager-badge" in html, "the family manager isn't marked in the member list"


def test_leaving_is_hidden_for_a_family_of_one():
    """Leaving a family you are alone in just creates another empty family."""
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")

    # Since 11.10 leaving and joining are one row: someone alone sees only
    # "join", and "open a new family" (the leaving itself) is shown only when
    # there is someone else
    assert "{% set alone = members | length <= 1 %}" in html
    leave = html[html.index("{% if not alone %}"):]
    assert 'id="leaveFamilyBtn"' in leave[:leave.index("{% endif %}")]
