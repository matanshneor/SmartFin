"""A row that doesn't exist is "none", not "we couldn't check".

In our postgrest-py `.maybe_single().execute()` returns `None` when there is no
row, and `.execute().data` raised. Four functions turned a transaction deleted a
moment earlier into a failure that way; the most visible: deleting a transaction
another family member had already deleted answered "we couldn't check whether
this is a recurring transaction, try again". (Found in a browser test; the double
returned a "polite" empty response and hid it, and now it behaves like the real
one.)
"""
import pytest

from backend import supabase_config as db

from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM  = "11111111-1111-1111-1111-111111111111"
_GONE = "44444444-4444-4444-4444-444444444444"


@pytest.fixture
def empty(monkeypatch):
    fake = FakeSupabase(transactions=[])
    monkeypatch.setattr(db, "get_client", lambda: fake)
    return fake


def test_a_gone_transaction_is_not_part_of_a_series(empty):
    assert db.recurring_occurrence(_GONE, _FAM) == (None, True)


def test_a_gone_transaction_is_not_an_instance(empty):
    assert db.is_recurring_instance(_GONE, _FAM) is False


def test_stopping_a_series_whose_template_is_gone(empty):
    assert db.delete_occurrences_from(_GONE, "2026-09-01", _FAM) == (0, None)


def test_the_fake_behaves_like_postgrest(empty):
    """Without this the tests above would pass on the old code too."""
    assert empty.table("transactions").select("id").eq("id", _GONE).maybe_single().execute() is None
