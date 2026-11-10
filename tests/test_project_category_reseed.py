"""Turning tracking off in a project and on again doesn't duplicate the
categories.

Turning off doesn't delete the project's categories, and turning on seeded the
defaults again: that is how "משכורת" and "הכנסה נוספת" were created twice in a
real project (Matan, 9.10)."""
import pytest

from backend import supabase_config as db
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_P   = "aaaaaaaa-0000-0000-0000-000000000001"


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabase(
        projects=[{"id": _P, "family_id": _FAM, "owner_id": None, "name": "טיול",
                   "track_expense": True, "track_income": False, "track_savings": False}],
        project_categories=[],
        categories=[{"id": "c1", "family_id": _FAM, "name": "משכורת", "icon": "💼",
                     "type": "income", "is_custom": True, "sort_order": 1},
                    {"id": "c2", "family_id": _FAM, "name": "הכנסה נוספת", "icon": "💵",
                     "type": "income", "is_custom": True, "sort_order": 2}])
    monkeypatch.setattr(db, "get_client", lambda: f)
    monkeypatch.setattr(db, "get_categories",
                        lambda fam, *a, **k: [c for c in f.rows("categories")
                                              if c["family_id"] == fam])
    return f


def _income_names(fake):
    return sorted(c["name"] for c in fake.rows("project_categories") if c["type"] == "income")


def _set_income(on):
    assert db.update_project(_P, _FAM, "טיול", track_expense=True, track_income=on)


def test_turning_income_on_seeds_the_family_income_categories(fake):
    _set_income(True)
    assert _income_names(fake) == ["הכנסה נוספת", "משכורת"]


def test_turning_income_off_and_on_again_does_not_duplicate(fake):
    _set_income(True)
    _set_income(False)
    _set_income(True)
    assert _income_names(fake) == ["הכנסה נוספת", "משכורת"]


def test_a_family_category_added_meanwhile_is_still_seeded(fake):
    _set_income(True)
    _set_income(False)
    fake.tables["categories"].append({"id": "c3", "family_id": _FAM, "name": "מתנות",
                                      "icon": "🎁", "type": "income", "is_custom": True,
                                      "sort_order": 3})
    _set_income(True)
    assert _income_names(fake) == ["הכנסה נוספת", "משכורת", "מתנות"]
