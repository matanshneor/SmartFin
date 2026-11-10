"""Deleting the first occurrence in a series destroyed the whole series, and the
screen said the opposite.

recurring_parent_id is on delete set null. When the deleted row was the template
(and it is always the template when deleting the month the series started) all
the occurrences created from it lost the link at once:

- the series stopped producing new occurrences (no more rent from next month)
- everything already created vanished from "עסקאות קבועות" and from the panel on
  the month page
- the skip was recorded on the deleted row, that is, lost

And at that same moment the UI announced: **"העסקה נמחקה. שאר הסדרה נשארה"**.

The distinction between "template" and occurrence is entirely internal, and whoever
deletes March's rent shouldn't care that March happens to be the month the series
started. So deleting the template hands the role to the next occurrence.

Verified against the live database in a rollback: template + 2 occurrences,
deleting the template with "רק את זו" → a new template (April), one linked
occurrence, **0 orphans**. Before the fix: 0 templates, 2 orphans.
"""
from pathlib import Path

import pytest

from backend import supabase_config as db

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_MIGRATIONS = _ROOT / "backend/supabase/migrations"
_SQL = (_MIGRATIONS / "20260922120000_delete_recurring_occurrence.sql").read_text(encoding="utf-8")


class _Rpc:
    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return type("R", (), {"data": self._result})()


class _Client:
    """A double that records the calls, so it is possible to check **how** it was
    deleted."""

    def __init__(self, rpc_result=1):
        self.rpcs = []
        self.tables = []
        self._rpc_result = rpc_result

    def rpc(self, name, params=None):
        self.rpcs.append((name, dict(params or {})))
        return _Rpc(self._rpc_result)

    def table(self, name):
        self.tables.append(name)
        raise AssertionError(
            f"המחיקה נגעה ישירות בטבלה {name!r}. שלוש קריאות נפרדות הן "
            f"בדיוק מה שאפשר גם את הייתום וגם את מרוץ הדילוגים."
        )


@pytest.fixture
def client(monkeypatch):
    c = _Client()
    monkeypatch.setattr(db, "get_client", lambda: c)
    return c


# ═══ The delete goes through the database, in one action ═════════════════════

def test_deleting_an_occurrence_is_one_atomic_call(client):
    """The heart. Before it was: read skips → write skips → delete. Three calls that
    can stop midway, and two family members can interleave."""
    ok, err = db.delete_one_occurrence("tx-1", "tpl-1", "2026-03-01", "fam-1")

    assert ok and err is None
    assert [name for name, _ in client.rpcs] == ["delete_recurring_occurrence"]
    assert client.tables == [], "something still touched the table directly"


def test_the_row_identifies_itself_to_the_database(client):
    """The template and date are derived from the row inside the database, not sent
    from here; otherwise one row could be deleted and a skip recorded on another."""
    db.delete_one_occurrence("tx-1", "tpl-1", "2026-03-01", "fam-1")

    _, params = client.rpcs[0]
    assert params == {"p_tx_id": "tx-1", "p_family_id": "fam-1"}


def test_deleting_nothing_is_reported_as_not_found(monkeypatch):
    """A row another family member already deleted must be said, not swallowed as
    success."""
    monkeypatch.setattr(db, "get_client", lambda: _Client(rpc_result=0))

    ok, err = db.delete_one_occurrence("tx-נעלם", "tpl-1", "2026-03-01", "fam-1")

    assert ok is False
    assert err == "not found"


def test_a_database_failure_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(db, "get_client",
                        lambda: _Client(rpc_result=RuntimeError("boom")))

    ok, err = db.delete_one_occurrence("tx-1", "tpl-1", "2026-03-01", "fam-1")

    assert ok is False and err


# ═══ What the database function guarantees ═══════════════════════════════════

def test_the_migration_promotes_an_heir_instead_of_orphaning_the_series():
    """This is the fix itself. Without the hand-over, deleting the template detaches
    every occurrence created from it, and that happens every time the first month
    is deleted."""
    assert "recurring_parent_id = v_heir.id" in _SQL, \
        "the siblings aren't moved to the successor: they'll be orphaned the moment the template is deleted"
    assert "is_recurring        = true" in _SQL, \
        "the successor doesn't become the template, so the series stops producing occurrences"


def test_the_heir_carries_what_defines_the_series():
    """A successor without a frequency is a template that produces nothing, and a
    successor without an end date is a series that never ends."""
    for field in ("recurring_frequency", "recurring_end_date", "recurring_skips"):
        assert f"{field}" in _SQL.split("v_heir.id")[0], f"{field} isn't passed to the successor"


def test_the_skip_is_recorded_before_anything_in_the_series_is_deleted():
    """In the reverse order, a write failure leaves a deleted occurrence without a
    mark, and it comes back tomorrow.

    Measured on the series path only: a row with no template is deleted earlier on
    purpose, because there is no series to record a skip on."""
    series = _SQL[_SQL.index("v_template is null"):]
    series = series[series.index("end if;"):]      # after the template-less branch

    skip_at = series.index("set recurring_skips")
    deletes = [i for i in range(len(series))
               if series.startswith("delete from public.transactions", i)]

    assert deletes, "there is no delete at all on the series path"
    assert skip_at < min(deletes), "deleting before the skip is recorded"


def test_the_function_does_not_trust_the_family_id_it_is_given():
    """security definer bypasses RLS. Without the explicit check, any signed-in user
    could delete a transaction of any other family in the world."""
    assert "auth.uid()" in _SQL
    assert "security definer" in _SQL


def test_the_function_is_not_callable_by_anonymous_visitors():
    assert "revoke all on function public.delete_recurring_occurrence" in _SQL
    assert "to authenticated" in _SQL
