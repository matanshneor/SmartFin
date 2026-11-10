"""
Automatic RLS tests: make sure family A can't read, update or delete family B's
data, in every table with a family_id. They run against the real project (there is
no separate Supabase project dedicated to tests at this stage), against two
temporary test users that each run creates and deletes (tests/_test_accounts.py).

Every test cleans up after itself even if it fails (finally), so as not to leave
garbage in the tables.
"""
import uuid

from backend import supabase_config as db
from tests.conftest import category_of


def _table(name):
    return db.get_client().table(name)


def _assert_isolated(table_name, insert_payload, family_a, family_b, update_patch):
    """A pattern shared by all tables: insert a row as family A, and make sure family B
    doesn't see it, can't update it and can't delete it, while family A itself can
    access it as usual (control: that RLS doesn't simply block everyone)."""
    db.set_auth_token(family_a["token"])
    inserted = _table(table_name).insert(insert_payload).execute()
    assert inserted.data, f"insert as family A failed for {table_name}"
    row_id = inserted.data[0]["id"]

    try:
        own_read = _table(table_name).select("id").eq("id", row_id).execute()
        assert len(own_read.data) == 1, "family A should see its own row"

        db.set_auth_token(family_b["token"])
        cross_read = _table(table_name).select("id").eq("id", row_id).execute()
        assert cross_read.data == [], f"family B must NOT see family A's row in {table_name}"

        _table(table_name).update(update_patch).eq("id", row_id).execute()
        db.set_auth_token(family_a["token"])
        after_cross_update = _table(table_name).select("*").eq("id", row_id).single().execute().data
        for key, cross_value in update_patch.items():
            assert after_cross_update[key] != cross_value, \
                f"family B's update leaked into family A's row ({table_name}.{key})"

        db.set_auth_token(family_b["token"])
        _table(table_name).delete().eq("id", row_id).execute()
        db.set_auth_token(family_a["token"])
        still_there = _table(table_name).select("id").eq("id", row_id).execute()
        assert len(still_there.data) == 1, f"family B's delete must NOT affect family A's row in {table_name}"
    finally:
        db.set_auth_token(family_a["token"])
        _table(table_name).delete().eq("id", row_id).execute()


def test_categories_isolation(family_a, family_b):
    marker = f"RLS-TEST-{uuid.uuid4().hex[:8]}"
    _assert_isolated(
        "categories",
        {"family_id": family_a["family_id"], "name": marker, "icon": "🧪", "type": "expense", "is_custom": True},
        family_a, family_b,
        update_patch={"name": "HACKED-BY-FAMILY-B"},
    )


def test_transactions_isolation(family_a, family_b):
    db.set_auth_token(family_a["token"])
    _assert_isolated(
        "transactions",
        {"family_id": family_a["family_id"], "amount": 1, "type": "expense",
         "date": "2026-01-01", "description": "RLS test",
         "category_id": category_of(family_a["family_id"])},
        family_a, family_b,
        update_patch={"amount": 999999},
    )


def test_projects_isolation(family_a, family_b):
    marker = f"RLS-TEST-{uuid.uuid4().hex[:8]}"
    _assert_isolated(
        "projects",
        {"family_id": family_a["family_id"], "name": marker},
        family_a, family_b,
        update_patch={"name": "HACKED-BY-FAMILY-B"},
    )


def test_project_categories_isolation(family_a, family_b):
    db.set_auth_token(family_a["token"])
    project = _table("projects").insert({"family_id": family_a["family_id"], "name": "RLS test project"}).execute()
    project_id = project.data[0]["id"]
    try:
        marker = f"RLS-TEST-{uuid.uuid4().hex[:8]}"
        _assert_isolated(
            "project_categories",
            {"family_id": family_a["family_id"], "project_id": project_id, "name": marker, "type": "expense"},
            family_a, family_b,
            update_patch={"name": "HACKED-BY-FAMILY-B"},
        )
    finally:
        db.set_auth_token(family_a["token"])
        _table("projects").delete().eq("id", project_id).execute()


def test_receipt_scans_isolation(family_a, family_b):
    """receipt_scans is only a usage counter (append-only): it has an INSERT+SELECT
    policy only, on purpose, with no UPDATE/DELETE. So there is nothing to "clean"
    here at the normal RLS level; the single row the test creates stays (and that is
    fine, exactly like every real receipt scan that stays permanently for counting
    the monthly/total quota)."""
    db.set_auth_token(family_a["token"])
    inserted = _table("receipt_scans").insert(
        {"family_id": family_a["family_id"], "user_id": family_a["user_id"]}
    ).execute()
    row_id = inserted.data[0]["id"]

    db.set_auth_token(family_b["token"])
    cross_read = _table("receipt_scans").select("id").eq("id", row_id).execute()
    assert cross_read.data == [], "family B must NOT see family A's receipt scan record"

    own_count = _table("receipt_scans").select("id").eq("family_id", family_b["family_id"]).execute()
    assert row_id not in [r["id"] for r in own_count.data]


def test_families_table_isolation(family_a, family_b):
    """Family B can't read family A's families row, not even by direct id."""
    db.set_auth_token(family_b["token"])
    cross_read = _table("families").select("id").eq("id", family_a["family_id"]).execute()
    assert cross_read.data == [], "family B must NOT be able to read family A's families row"


def test_profiles_table_isolation(family_a, family_b):
    """Family B can't read the profile of the user in family A."""
    db.set_auth_token(family_b["token"])
    cross_read = _table("profiles").select("id").eq("id", family_a["user_id"]).execute()
    assert cross_read.data == [], "family B must NOT be able to read family A's profile row"


def _assert_owner_only_table(table_name, insert_payload, family_a):
    """The site owner's internal tables (owner_archive, login_events) are
    RLS-enabled-with-no-policy: no client (not even a signed-in user) reads or
    writes. Writing happens only through SECURITY DEFINER functions/triggers on
    the DB side."""
    db.set_auth_token(family_a["token"])

    read = _table(table_name).select("id").limit(5).execute()
    assert read.data == [], \
        f"authenticated client must get [] from {table_name} (RLS, no policies)"

    insert_blocked = False
    try:
        _table(table_name).insert(insert_payload).execute()
    except Exception:
        insert_blocked = True
    assert insert_blocked, \
        f"insert into {table_name} must be rejected (RLS enabled, no INSERT policy)"


def test_owner_archive_is_owner_only(family_a):
    _assert_owner_only_table(
        "owner_archive",
        {"kind": "transaction", "payload": {"rls_test": True}},
        family_a,
    )


def test_login_events_is_owner_only(family_a):
    _assert_owner_only_table(
        "login_events",
        {"user_id": family_a["user_id"], "email": "rls-test@fake.test", "event": "login"},
        family_a,
    )
