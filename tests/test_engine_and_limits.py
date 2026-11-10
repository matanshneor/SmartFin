"""Five fixes in the engine and the limits, all of which showed up as a wrong
number and not as an error.

**A batch thrown away whole.** A single `insert` is one statement, so one
conflicting row rolls back all of them. The assumption was that the only possible
conflict is an identical batch: true on full overlap, wrong on partial: if a
parallel request created the salary and not the rent, **both** were dropped, and
`_sync_recurring` marked "synced for today". A whole month without rent, with no
sign.

**An average divided by the number of months that had something.** A single
yearly payment (insurance in July) became the "average" of itself, and every
August looked fine forever.

**The "last 12 months" chart with no upper bound.** A payment made in advance for
next year appeared in it as a bar.

**Any member could appoint themselves manager**: at the RLS level, not through the app.

**A category with no name and no valid type** went into the database.
"""
import re
from pathlib import Path

import pytest

from backend import supabase_config as db

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_SRC = (_ROOT / "backend/supabase_config.py").read_text(encoding="utf-8")


def _fn(name):
    i = _SRC.index(f"def {name}(")
    return _SRC[i:_SRC.index("\ndef ", i)]


# ─── B5: a partial failure doesn't throw the whole batch away ───────────────

def test_a_partial_conflict_retries_the_rest_one_by_one():
    """The heart. `return 0, True` on every conflict is "everything already
    exists": a claim true only when the overlap is full."""
    body = _fn("materialize_recurring")
    conflict = body[body.index("uq_tx_recurring_occurrence"):]

    assert "for row in new_rows" in conflict, \
        "a batch that partially failed is still thrown away whole"
    # And an early return before the loop cancels it entirely, without touching
    # this line.
    assert "return 0, True" not in conflict[:conflict.index("for row in new_rows")], \
        "there is an early return before the retry: the loop is unreachable"


def test_the_count_reflects_what_was_actually_created():
    """After a partial failure `len(new_rows)` isn't what was created, and the
    caller uses that number to decide whether anything happened."""
    body = _fn("materialize_recurring")

    assert "return created, True" in body
    assert "return len(new_rows), True" not in body


def test_a_conflict_that_is_not_a_duplicate_still_raises():
    """Control: ignoring every error in the new loop would turn it into a silent
    swallow worse than the one it replaced."""
    body = _fn("materialize_recurring")
    loop = body[body.index("for row in new_rows"):]

    assert "raise" in loop


# ─── B10: the average is over three months ──────────────────────────────────

def test_the_average_divides_by_the_window_and_not_by_what_it_found():
    """A month with no expense in the category is ₪0, not a month that didn't happen."""
    # The division itself moved to the function shared by the separate query and
    # `get_settings_page`
    assert "category_averages_from_totals(totals)" in _fn("category_monthly_averages")
    body = _fn("category_averages_from_totals")

    assert "/ _HISTORY_MONTHS" in body, "the average is still divided by the number of months found"
    assert "/ len(past)" not in body


def test_the_window_is_one_constant_shared_by_the_query_and_the_maths():
    """Fetching 3 months and dividing by 4 (or vice versa) is a wrong number you
    can't see is wrong."""
    assert "_HISTORY_MONTHS = 3" in _SRC
    assert "month - _HISTORY_MONTHS" in _SRC


# ─── B10: the chart shows no future ─────────────────────────────────────────
# Now tested by behaviour: tests/test_row_cap.py::test_the_trend_shows_no_future_months


# ─── B10: a category must have a name and a type ────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from backend.app import app
    from tests._fake_db import FakeSupabase

    app.config["TESTING"] = True
    fake = FakeSupabase(categories=[])
    monkeypatch.setattr(db, "get_client", lambda: fake)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = "me"
            sess["family_id"] = "fam"
        yield c, fake


@pytest.mark.parametrize("name", ["", "   ", None])
def test_a_category_without_a_name_is_refused(client, name):
    """`TEXT NOT NULL` accepts an empty string: a row in the breakdown that can't
    be identified."""
    c, fake = client
    body = {"type": "expense"}
    if name is not None:
        body["name"] = name

    res = c.post("/api/categories", json=body)

    assert res.status_code == 422
    assert fake.rows("categories") == []


@pytest.mark.parametrize("type_", ["transfer", "", "EXPENSE"])
def test_a_category_with_an_unknown_type_is_refused(client, type_):
    """Stopped only by the database CHECK and came back as a 500, while every other
    route validates."""
    c, fake = client

    res = c.post("/api/categories", json={"name": "מכולת", "type": type_})

    assert res.status_code == 422
    assert fake.rows("categories") == []


# ─── B7: the columns that lock the manager role ─────────────────────────────

def test_the_family_table_only_grants_the_columns_the_app_writes():
    """Every `_require_manager` check relies on `manager_id`. Without a column
    restriction, RLS lets any member write to it directly, and then the check
    protects only itself. Verified against the database: `name` and `settings`
    pass, `manager_id` and `invite_code` are blocked."""
    sql = (_ROOT / "backend/supabase/migrations"
           / "20260922150000_lock_family_columns.sql").read_text(encoding="utf-8")

    assert "revoke update on public.families from authenticated" in sql
    assert re.search(r"grant\s+update\s*\(\s*name,\s*settings\s*\)", sql)
    assert "manager_id" not in sql.split("grant  update")[1]
