"""
Tests for a read that comes right after a write, in the same request.

There is a per-request cache that prevents fetching the same data again.
update_family_settings fetches the existing settings to merge a partial update into
them, and that fetch alone fills the cache with the value from **before** the
write. The route then returns the settings in the same request, and gets the old
ones from the cache.

The damage didn't stop at the display: settings.js saves the response and uses it
to decide whether to show the family-member picker in the transaction modal.
Whoever turned on "שיוך הוצאות" got a response saying it is off, the modal showed
no picker, and the server (reading the fresh value on the next request)
attributed every transaction to whoever is signed in. Silent, wrong attribution
of money, until the next refresh.
"""
from pathlib import Path

import pytest
from flask import g

from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"


class _FakeFamilies:
    """One family row in memory, reflecting writes immediately."""

    def __init__(self):
        self.row = {"id": _FAM, "name": "שניאור",
                    "settings": {"owner_attribution": {"expense": False}}}
        self.reads = 0
        self._pending = None

    def table(self, _n):            return self

    def rpc(self, name, params=None):
        """The merge moved to the database, into one statement with for update, because
        read-merge-write from Python let two family members overwrite each other.
        The double mimics the same semantics; otherwise the test verifies itself
        and not the code."""
        assert name == "merge_family_settings", name
        merged = dict(self.row.get("settings") or {})
        whole = set(params.get("p_whole_keys") or [])
        for k, v in (params.get("p_patch") or {}).items():
            if k not in whole and isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v
        self._pending = {"settings": merged}
        return self
    def select(self, *a, **k):      return self
    def eq(self, *a, **k):          return self
    def single(self, *a, **k):      return self
    def update(self, payload, **k): self._pending = payload; return self
    def maybe_single(self, *a, **k): return self

    def execute(self):
        if self._pending is not None:
            self.row.update(self._pending)
            self._pending = None
        else:
            self.reads += 1
        return self

    @property
    def data(self):                 return dict(self.row)


@pytest.fixture
def fake(monkeypatch):
    client = _FakeFamilies()
    monkeypatch.setattr(db, "get_client", lambda: client)
    return client


def test_reading_settings_after_writing_them_returns_the_new_value(fake):
    """The heart: the same request, a write and then a read."""
    with app.test_request_context("/"):
        db.update_family_settings(_FAM, {"owner_attribution": {"expense": True}})

        after = db.get_family_settings(_FAM)

    assert after["owner_attribution"]["expense"] is True, (
        "the value from before the save was returned: the browser will believe it and add transactions with reversed attribution"
    )


def test_renaming_the_family_is_visible_in_the_same_request(fake):
    with app.test_request_context("/"):
        db.get_family(_FAM)                       # fills the cache
        db.update_family_name(_FAM, "משפחת כהן")

        assert db.get_family(_FAM)["name"] == "משפחת כהן"


def test_both_caches_are_cleared_not_just_one(fake):
    """app.py holds another copy on g.family_settings. Evicting only one leaves the
    old value exactly where it is served to the templates from."""
    with app.test_request_context("/"):
        g.family_settings = {"owner_attribution": {"expense": False}}
        g._sf_cache = {f"family:{_FAM}": {"settings": {}}}

        db.update_family_settings(_FAM, {"owner_attribution": {"expense": True}})

        assert "family_settings" not in g
        assert f"family:{_FAM}" not in g._sf_cache


def test_the_cache_still_does_its_job_when_nothing_was_written(fake):
    """Control: eviction shouldn't cancel the cache itself; otherwise every page goes
    back to fetching the same row three or four times."""
    with app.test_request_context("/"):
        db.get_family(_FAM)
        db.get_family(_FAM)
        db.get_family(_FAM)

    assert fake.reads == 1, f"the row was fetched {fake.reads} times instead of once"


# ─── The merge moved to the database, and both versions must agree ──────────
#
# Read-merge-write from Python let two family members overwrite each other: mum sets
# a budget at 20:00:00.1, dad turns attribution off at 20:00:00.3, both read the
# same JSON, and dad's write carries the limits from before mum and deletes her
# budget. Both got "saved".
#
# The merge is now in one statement with for update. Both semantics remain in the
# code (Python for get_family_settings, SQL for the write), so they could silently
# drift apart, and the tests here don't let them.

_MERGE_SQL = (Path(__file__).resolve().parent.parent /
              "backend/supabase/migrations/20260922140000_atomic_settings_merge.sql"
              ).read_text(encoding="utf-8")


def test_the_write_path_no_longer_merges_in_python():
    """The heart: if the merge came back here, the race came back too."""
    src = (Path(__file__).resolve().parent.parent /
           "backend/supabase_config.py").read_text(encoding="utf-8")
    block = src[src.index("def update_family_settings("):]
    block = block[:block.index("\n\ndef ")]

    assert "merge_family_settings" in block, "the write doesn't go through the database function"
    assert "_merge_settings(" not in block, "the merge went back to Python, and the race with it"


def test_the_database_locks_the_row_while_it_merges():
    """Without for update the function is exactly the same read-merge-write, just in
    another language: the second reader still reads the value from before the
    first."""
    assert "for update" in _MERGE_SQL


def test_the_whole_map_keys_travel_with_the_patch():
    """limits must be replaced and not merged: removing a budget is represented by its
    absence from the map, and a merge can't express absence. The list is sent from
    Python so the two implementations don't hold copies of their own."""
    src = (Path(__file__).resolve().parent.parent /
           "backend/supabase_config.py").read_text(encoding="utf-8")

    assert "p_whole_keys" in src
    assert "sorted(_WHOLE_MAP_KEYS)" in src, \
        "the key list is hard-coded twice: the two versions will drift apart"


def test_the_function_checks_membership_itself():
    """security definer bypasses RLS. Without the check, any signed-in user could write
    settings for any family in the world, including where the money is attributed."""
    assert "not a member of that family" in _MERGE_SQL
    assert "auth.uid()" in _MERGE_SQL
