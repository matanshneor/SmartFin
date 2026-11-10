"""
Tests for the family-join flow.

This is the route that broke silently from the day it was written:
join_family_by_code checked the family exists with a plain SELECT, and the RLS
policy families_member_read allows reading a family only to someone already a
member, i.e. never to the joiner. The check always came back empty, the function
returned False, and the value was thrown away. Without automatic coverage nothing
prevents another silent regression.

Run against the real project (like test_rls_isolation) with the run's two
temporary test accounts (`tests/_test_accounts.py`). Every test that moves a user
between families puts them back in finally, even if it fails.
"""
import uuid

import pytest

from backend import supabase_config as db
import _test_accounts                     # conftest adds tests to sys.path
from tests.conftest import category_of


def _privileged(sql: str):
    """Runs SQL with owner privileges, through the linked CLI.

    Needed only to set up state. Since migration 20260916100000 an authenticated
    session can't create a family or change its own membership: that is exactly
    the protection that was added, and what stopped a user from moving
    themselves into another family and reading its finances. These tests still
    need to set up a temporary family, so they do it outside the app's
    permission model instead of weakening it.

    What is tested (join_family_by_code) runs as usual through the
    authenticated client."""
    return _test_accounts.privileged_sql(sql)


def _profile_family(user_id):
    profile = db.get_profile(user_id)
    return profile.get("family_id") if profile else None


def _dispose_temp_family(client, user_id, temp_id, home_code):
    """Deletes a temporary family and brings the user home.

    There is no DELETE policy on families, so a client can't delete a family
    directly: the abandoned-family cleanup inside join_family_by_code is the
    only way. So the user is placed in the family, it is emptied of
    transactions, and they join back home by code: the RPC itself deletes it on
    the way out.

    The order here is critical: RLS on transactions allows deleting only within
    the user's family, so the move must happen before the delete; otherwise it
    fails silently and the family survives with an orphan transaction."""
    _privileged(f"update public.profiles set family_id='{temp_id}' where id='{user_id}';")
    client.table("transactions").delete().eq("family_id", temp_id).execute()
    db.join_family_by_code(home_code)


@pytest.fixture
def family_a_code(family_a):
    """A **fresh** invite code for family A.

    The code expires after a week (migration 20260922180000), and these tests
    read whatever was stored, so they passed for a week and failed on 29.9.2026,
    with no code change at all. Every run replaces the code, as a real family
    manager would."""
    db.set_auth_token(family_a["token"])
    code, err = db.rotate_invite_code()
    assert code, f"replacing the test family's invite code failed: {err}"
    return code


# ─── Preview ────────────────────────────────────────────────────────────────

def test_preview_returns_the_family_name(family_a, family_a_code):
    expected = db.get_family(family_a["family_id"]).get("name")

    assert db.family_name_for_code(family_a_code) == expected


@pytest.mark.parametrize("mangle", [
    lambda c: c.lower(),                        # lower case
    lambda c: f"  {c}  ",                       # spaces around
    lambda c: f"{c[:3]}-{c[3:]}",               # a dash in the middle
    lambda c: f" {c[:3].lower()}-{c[3:]} ",     # all together
])
def test_preview_normalizes_a_mangled_code(family_a_code, mangle):
    """The code travels over WhatsApp and is read out over the phone: spaces,
    dashes and lower case are the normal state, not an exception."""
    expected = db.family_name_for_code(family_a_code)

    assert db.family_name_for_code(mangle(family_a_code)) == expected


def test_preview_returns_none_for_an_unknown_code():
    assert db.family_name_for_code("ZZZZZZ") is None


def test_preview_returns_none_for_an_empty_code():
    assert db.family_name_for_code("") is None


# ─── Rejecting wrong codes ──────────────────────────────────────────────────

def test_join_rejects_an_unknown_code(family_a):
    before = _profile_family(family_a["user_id"])

    family_id, err = db.join_family_by_code("ZZZZZZ")

    assert family_id is None
    assert err, "a wrong code must return an error: the silent swallow is the whole bug"
    assert _profile_family(family_a["user_id"]) == before, "a failure shouldn't move anyone"


def test_join_rejects_an_empty_code(family_a):
    family_id, err = db.join_family_by_code("   ")

    assert family_id is None
    assert err


def test_join_is_idempotent_for_the_family_you_are_already_in(family_a, family_a_code):
    """Joining a family you are already a member of shouldn't change anything, and
    above all shouldn't run the abandoned-family cleanup on your own family."""
    family_id, err = db.join_family_by_code(family_a_code)

    assert err is None
    assert family_id == family_a["family_id"]
    assert _profile_family(family_a["user_id"]) == family_a["family_id"]


# ─── The join itself + cleaning up the abandoned family ─────────────────────

def test_join_moves_the_user_and_deletes_the_family_left_behind(family_a, family_a_code):
    """The full route, and within it the clause that prevents junk piling up.

    On first sign-in ensure_family opens a family for every user, so a joiner
    always abandons an empty family. Without the cleanup every successful join
    would leave behind another orphan "המשפחה שלי" row: exactly what piled up in
    the table until now.

    To test this without risking the test family itself, a temporary family is
    set up, the user is moved to it directly (not through the RPC), and then
    joined back by code, so the family abandoned and deleted is the temporary
    one and not the real one."""
    client    = db.get_client()
    temp_id   = str(uuid.uuid4())
    temp_code = "TST" + uuid.uuid4().hex[:3].upper()
    original  = family_a["family_id"]

    _privileged(f"insert into public.families(id,name,invite_code) "
                f"values ('{temp_id}','משפחה זמנית לבדיקה','{temp_code}');")

    try:
        # Move directly, so the move back is the one being tested
        _privileged(f"update public.profiles set family_id='{temp_id}' "
                    f"where id='{family_a['user_id']}';")
        assert _profile_family(family_a["user_id"]) == temp_id

        family_id, err = db.join_family_by_code(family_a_code)

        assert err is None, f"the join failed: {err}"
        assert family_id == original
        assert _profile_family(family_a["user_id"]) == original

        # The temporary family is left with no members and no transactions, so it
        # should be deleted. Checked through the RPC and not a SELECT: after the
        # move the user has no read access to the temporary family, but
        # family_name_for_code is security definer.
        assert db.family_name_for_code(temp_code) is None, \
            "the abandoned family wasn't deleted: every join will now leave a junk row"
    finally:
        # Restore even if we failed midway, so as not to leave the test account
        # hanging. If the RPC already deleted the temporary family, this cleanup
        # just finds nothing to do.
        if db.family_name_for_code(temp_code) is not None:
            _dispose_temp_family(client, family_a["user_id"], temp_id, family_a_code)
        _privileged(f"update public.profiles set family_id='{original}' "
                    f"where id='{family_a['user_id']}';")
        # And the temporary family itself, if it survived, together with the
        # archive of its transaction. Without this every run left an empty family
        # and a "join test" archive row in the production database: by 4.10, 8
        # such families and 252 rows had piled up.
        _test_accounts.purge([], extra_family_ids=[temp_id])


def test_join_keeps_a_family_that_still_holds_transactions(family_a, family_a_code):
    """A control for the cleanup: a family with history isn't deleted even when the
    last member leaves.

    This is the clause that separates "cleaning junk" from "losing data", so it
    needs its own coverage. Again through a temporary family, this time one that
    holds one transaction."""
    client    = db.get_client()
    temp_id   = str(uuid.uuid4())
    temp_code = "TST" + uuid.uuid4().hex[:3].upper()
    original  = family_a["family_id"]

    _privileged(f"insert into public.families(id,name,invite_code) "
                f"values ('{temp_id}','משפחה זמנית עם היסטוריה','{temp_code}');")

    try:
        _privileged(f"update public.profiles set family_id='{temp_id}' "
                    f"where id='{family_a['user_id']}';")

        client.table("transactions").insert(
            {"family_id": temp_id, "amount": 1, "type": "expense",
             "date": "2026-01-01", "description": "join test",
             "category_id": category_of(temp_id)},
            returning="minimal",
        ).execute()

        family_id, err = db.join_family_by_code(family_a_code)

        assert err is None
        assert family_id == original
        assert db.family_name_for_code(temp_code) is not None, \
            "a family with transactions was deleted: that is data loss, not cleanup"
    finally:
        _dispose_temp_family(client, family_a["user_id"], temp_id, family_a_code)
        _privileged(f"update public.profiles set family_id='{original}' "
                    f"where id='{family_a['user_id']}';")
        # And the temporary family itself, if it survived, together with the
        # archive of its transaction. Without this every run left an empty family
        # and a "join test" archive row in the production database: by 4.10, 8
        # such families and 252 rows had piled up.
        _test_accounts.purge([], extra_family_ids=[temp_id])
