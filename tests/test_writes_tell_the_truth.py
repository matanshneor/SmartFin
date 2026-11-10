"""A write that touched no row said "saved".

The previous round fixed this for transactions. Eight more writing routes remained:
editing a category, ordering categories, editing a project, project categories,
deleting a project and the family name. All did .update(...).eq(...).execute() and
then return True without looking at .data.

The scenario isn't theoretical. Mum renames "מסעדות" while dad deletes the same
category a second earlier: the PUT matches no row, the function returns True, the
route returns {"status":"ok"} and the JS paints the new name on screen. Mum is sure
the change was saved. It wasn't.

This is exactly the same pattern as DataUnavailable (a fake success), just in the
write direction, where it is even quieter: there isn't even a number that looks
wrong.
"""
import pytest

from backend import supabase_config as db
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_OTHER = "99999999-9999-9999-9999-999999999999"


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabase(
        categories=[{"id": "c1", "family_id": _FAM, "name": "מסעדות",
                     "type": "expense", "icon": "🍽", "is_custom": True, "sort_order": 1}],
        projects=[{"id": "p1", "family_id": _FAM, "name": "שיפוץ", "archived": False,
                   "track_expense": True, "track_income": False, "track_savings": False,
                   "budget_target": 5000, "description": None, "icon": "🔨"}],
        project_categories=[{"id": "pc1", "project_id": "p1", "family_id": _FAM,
                             "name": "חומרים", "icon": "🧱"}],
        families=[{"id": _FAM, "name": "שניאור", "settings": {}}],
        transactions=[{"id": "t1", "family_id": _FAM, "project_id": "p1",
                       "amount": 800.0, "type": "expense", "date": "2026-09-01"},
                      {"id": "t2", "family_id": _FAM, "project_id": "p1",
                       "amount": 200.0, "type": "expense", "date": "2026-09-02"}],
    )
    monkeypatch.setattr(db, "get_client", lambda: f)
    monkeypatch.setattr(db, "_invalidate_family_cache", lambda *a, **k: None)
    return f


# ═══ The thing itself exists ═══════════════════════════════════════════════════

def test_editing_a_category_that_exists_reports_success(fake):
    assert db.update_category("c1", _FAM, "אוכל בחוץ", "🍕") is True
    assert fake.rows("categories")[0]["name"] == "אוכל בחוץ"


def test_renaming_the_family_reports_success(fake):
    assert db.update_family_name(_FAM, "משפחת כהן") is True
    assert fake.rows("families")[0]["name"] == "משפחת כהן"


def test_editing_a_project_category_reports_success(fake):
    assert db.update_project_category("pc1", "p1", _FAM, "צבע", "🎨") is True


# ═══ And what happens when it no longer does ═══════════════════════════════════

def test_editing_a_category_someone_else_deleted_is_not_success(fake):
    """The heart, and the real scenario: another family member deleted it a second
    earlier."""
    fake.tables["categories"] = []

    assert db.update_category("c1", _FAM, "אוכל בחוץ", "🍕") is False


def test_a_category_of_another_family_cannot_be_renamed(fake):
    """Isolation. eq("family_id") filters, but the response said "saved" anyway, so it
    looked like it worked."""
    fake.tables["categories"][0]["family_id"] = _OTHER

    assert db.update_category("c1", _FAM, "שלי עכשיו", "🍕") is False
    assert fake.rows("categories")[0]["name"] == "מסעדות"


def test_renaming_a_family_that_is_not_there_is_not_success(fake):
    assert db.update_family_name("fam-נעלם", "משפחת כהן") is False


def test_editing_a_project_category_of_another_project_is_not_success(fake):
    assert db.update_project_category("pc1", "p-אחר", _FAM, "צבע", "🎨") is False


def test_deleting_a_project_category_that_is_gone_is_not_success(fake):
    fake.tables["project_categories"] = []

    assert db.delete_project_category("pc1", "p1", _FAM) is False


def test_editing_a_project_that_is_gone_is_not_success(fake):
    fake.tables["projects"] = []

    # None = "not found" (the route answers 404), as opposed to False = a fault
    # (500). The main thing: not True: no "saved" on a project that isn't there
    assert db.update_project("p1", _FAM, "שיפוץ", 5000, None, "🔨",
                             True, False, False) is None


# ═══ Deleting a project reports how much money vanished ═══════════════════════

def test_deleting_a_project_with_its_transactions_says_how_many(fake):
    """"Also delete transactions" carried no number. It is the most destructive action
    any member can do with no password and no manager privilege."""
    ok, wiped = db.delete_project("p1", _FAM)

    assert ok is True
    assert wiped == 2
    assert fake.rows("transactions") == []


def test_deleting_a_project_that_is_gone_is_not_success(fake):
    """And without touching the transactions: "not found" after they were already
    deleted is the worst of all."""
    fake.tables["projects"] = []

    ok, wiped = db.delete_project("p1", _FAM)

    assert ok is False and wiped == 0
    assert len(fake.rows("transactions")) == 2, "the transactions were deleted before it turned out there is no project"
