"""`get_month_page` must return exactly what the five queries returned.

This is the test that justifies the replacement. Without it "it looks the same" is
all there is, and the month page is numbers: a silent deviation in it is exactly
the kind of failure this app has been burned by before (see `data_unavailable` in
app.py).

Runs against the real database as the test family, like `test_rls_isolation`, and
cleans up after itself in finally even when it fails.
"""
import uuid

import pytest

from backend import supabase_config as db
from tests.conftest import category_of

YEAR, MONTH = 2031, 3


def _table(name):
    return db.get_client().table(name)


@pytest.fixture
def month_fixture(family_a):
    """A month with all four links the old query made: category, profile, project
    and project category, and a transaction with none of them, because `null`
    versus `{}` is exactly the difference the templates check."""
    db.set_auth_token(family_a["token"])
    fid = family_a["family_id"]
    marker = f"MP-{uuid.uuid4().hex[:8]}"
    created = {"transactions": [], "categories": [], "project_categories": [], "projects": []}

    try:
        cat = _table("categories").insert({
            "family_id": fid, "name": f"{marker}-cat", "icon": "🧪",
            "type": "expense", "is_custom": True, "sort_order": 999,
        }).execute().data[0]
        created["categories"].append(cat["id"])

        proj = _table("projects").insert({
            "family_id": fid, "name": f"{marker}-proj", "icon": "🏗️",
            "owner_id": family_a["user_id"],
        }).execute().data[0]
        created["projects"].append(proj["id"])

        pcat = _table("project_categories").insert({
            "family_id": fid, "project_id": proj["id"],
            "name": f"{marker}-pcat", "icon": "🔩", "type": "expense",
        }).execute().data[0]
        created["project_categories"].append(pcat["id"])

        rows = [
            # category + profile
            {"family_id": fid, "amount": 120.5, "type": "expense", "date": f"{YEAR}-{MONTH:02d}-05",
             "description": f"{marker}-with-cat", "category_id": cat["id"],
             "user_id": family_a["user_id"]},
            # project + project category
            {"family_id": fid, "amount": 300, "type": "expense", "date": f"{YEAR}-{MONTH:02d}-11",
             "description": f"{marker}-project", "project_id": proj["id"],
             "project_category_id": pcat["id"], "user_id": family_a["user_id"]},
            # No link besides the category: a household transaction with no
            # category is rejected by the database
            # (`transactions_category_required`). The other three should come out null
            {"family_id": fid, "amount": 42, "type": "income", "date": f"{YEAR}-{MONTH:02d}-20",
             "description": f"{marker}-bare", "category_id": category_of(fid, "income")},
            # The same date as the previous one: this is where instability in the
            # order shows up, if there is any
            {"family_id": fid, "amount": 7, "type": "savings", "date": f"{YEAR}-{MONTH:02d}-20",
             "description": f"{marker}-same-day", "category_id": category_of(fid, "savings")},
        ]
        for r in rows:
            created["transactions"].append(_table("transactions").insert(r).execute().data[0]["id"])

        yield family_a
    finally:
        db.set_auth_token(family_a["token"])
        for table in ("transactions", "project_categories", "projects", "categories"):
            for row_id in created[table]:
                try:
                    _table(table).delete().eq("id", row_id).execute()
                except Exception:
                    pass


def _old_path(fid):
    """The five queries, exactly as month_view called them."""
    return {
        "settings":   db.get_family_settings(fid),
        "members":    db.get_family_members(fid),
        "categories": db.get_categories(fid),
        "rows":       db.fetch_month_rows(fid, YEAR, MONTH),
        "archive":    db.get_months_archive(fid),
    }


def test_settings_members_categories_archive_match(month_fixture):
    """What the category comparison here does **not** cover, and why.

    Both paths fetch `family_id is null or family_id = X`: the global categories
    seeded for everyone. The database has none (0 of 17, checked 23.9.2026), so
    that branch is dead on both sides and a comparison can't tell it apart: a
    mutation removing it entirely from the new function passes this test.

    This isn't a flaw in the function (it copies the old condition faithfully),
    but whoever seeds global categories in the future needs to know there is no
    net here."""
    fid = month_fixture["family_id"]
    db.set_auth_token(month_fixture["token"])

    old = _old_path(fid)
    new = db.fetch_month_page(fid, YEAR, MONTH)

    assert new["settings"]   == old["settings"]
    assert new["members"]    == old["members"]
    assert new["categories"] == old["categories"]
    assert new["archive"]    == old["archive"]


def test_month_rows_match_exactly(month_fixture):
    """The rows' content, including the nested structure. Sorted by id so the content
    comparison doesn't fail on order; the order itself is checked separately below."""
    fid = month_fixture["family_id"]
    db.set_auth_token(month_fixture["token"])

    old_rows = sorted(db.fetch_month_rows(fid, YEAR, MONTH), key=lambda r: r["id"])
    new_rows = sorted(db.fetch_month_page(fid, YEAR, MONTH)["rows"], key=lambda r: r["id"])

    assert len(new_rows) == len(old_rows) > 0
    for old_row, new_row in zip(old_rows, new_rows):
        assert new_row == old_row, f"row {old_row['id']} differs"


def test_nested_joins_are_null_not_empty(month_fixture):
    """The transaction with no links: `None` and not `{}`. The templates check
    existence, and a false `{}` would show an empty category instead of skipping."""
    fid = month_fixture["family_id"]
    db.set_auth_token(month_fixture["token"])

    rows = db.fetch_month_page(fid, YEAR, MONTH)["rows"]
    bare = [r for r in rows if r["description"].endswith("-bare")]
    assert len(bare) == 1
    for key in ("project_categories", "profiles", "projects"):
        assert bare[0][key] is None, f"{key} should be None on an unlinked row"

    linked = [r for r in rows if r["description"].endswith("-with-cat")][0]
    assert linked["categories"]["icon"] == "🧪"
    assert linked["profiles"] is not None
    assert linked["projects"] is None


def test_rows_are_ordered_newest_first(month_fixture):
    fid = month_fixture["family_id"]
    db.set_auth_token(month_fixture["token"])

    dates = [r["date"] for r in db.fetch_month_page(fid, YEAR, MONTH)["rows"]]
    assert dates == sorted(dates, reverse=True)


def test_other_family_gets_nothing(month_fixture, family_b):
    """The test that justifies `SECURITY DEFINER`: the function bypasses RLS, so
    the only protection is the `get_my_family_id()` inside it. Family B asking for
    family A's id must get a failure: not data, and not "an empty family" either."""
    fid = month_fixture["family_id"]

    db.set_auth_token(family_b["token"])
    with pytest.raises(db.DataUnavailable):
        db.fetch_month_page(fid, YEAR, MONTH)

    # Control: without it the test passes even if the call always fails, for
    # any reason, and then it isn't testing the protection but that the RPC is
    # broken.
    own = db.fetch_month_page(family_b["family_id"], YEAR, MONTH)
    assert own["settings"], "family B must still be able to read its own month"
