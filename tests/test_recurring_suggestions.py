"""An expense entered three months in a row is offered as a recurring
transaction (Matan, 22.11)."""
import datetime
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _fake_db import FakeSupabase  # noqa: E402
from backend import supabase_config as db  # noqa: E402

pytestmark = pytest.mark.unit
_FAM, _ME = "fam-1", "u-me"
_TODAY = datetime.date(2026, 11, 22)


def _tx(id_, date, amount, desc, cat="c-subs", **extra):
    row = {"id": id_, "family_id": _FAM, "date": date, "amount": amount, "type": "expense",
           "description": desc, "category_id": cat, "project_id": None, "project_category_id": None,
           "user_id": None, "is_recurring": False, "recurring_parent_id": None,
           "recurring_frequency": None, "recurring_end_date": None, "workplace": None,
           "categories": {"name": "מנויים", "icon": "💳"}, "project_categories": None,
           "profiles": None, "projects": None}
    row.update(extra)
    return row


def _suggest(rows, monkeypatch):
    monkeypatch.setattr(db, "get_client", lambda: FakeSupabase(transactions=rows))
    return db.recurring_suggestions(_FAM, _ME, today=_TODAY)


def test_three_months_in_a_row_is_offered_with_the_latest_row(monkeypatch):
    out = _suggest([_tx("a", "2026-09-10", 55, "נטפליקס"), _tx("b", "2026-10-10", 55, "נטפליקס"),
                    _tx("c", "2026-11-10", 57, "נטפליקס")], monkeypatch)
    assert [(t["id"], t["months"]) for t in out] == [("c", 3)]


def test_ending_last_month_counts_too(monkeypatch):
    out = _suggest([_tx("a", "2026-08-05", 99, "אינטרנט"), _tx("b", "2026-09-05", 99, "אינטרנט"),
                    _tx("c", "2026-10-05", 99, "אינטרנט")], monkeypatch)
    assert [t["id"] for t in out] == ["c"]


@pytest.mark.parametrize("rows", [
    # only two months
    [_tx("a", "2026-10-10", 55, "נטפליקס"), _tx("b", "2026-11-10", 55, "נטפליקס")],
    # a gap in the middle
    [_tx("a", "2026-08-10", 55, "נטפליקס"), _tx("b", "2026-09-10", 55, "נטפליקס"), _tx("c", "2026-11-10", 55, "נטפליקס")],
    # twice in one month: a habit, not a subscription
    [_tx("a", "2026-09-10", 40, "קפה"), _tx("b", "2026-10-10", 40, "קפה"),
     _tx("c", "2026-11-10", 40, "קפה"), _tx("d", "2026-11-12", 40, "קפה")],
    # the amounts are far apart
    [_tx("a", "2026-09-10", 100, "סופר"), _tx("b", "2026-10-10", 180, "סופר"), _tx("c", "2026-11-10", 240, "סופר")],
    # no description to recognise it by
    [_tx("a", "2026-09-10", 55, ""), _tx("b", "2026-10-10", 55, ""), _tx("c", "2026-11-10", 55, "")],
    # in a project
    [_tx("a", "2026-09-10", 55, "נגר", project_id="p1"), _tx("b", "2026-10-10", 55, "נגר", project_id="p1"),
     _tx("c", "2026-11-10", 55, "נגר", project_id="p1")],
], ids=["two-months", "gap", "twice-a-month", "amounts-apart", "no-description", "project"])
def test_what_is_not_offered(rows, monkeypatch):
    assert _suggest(rows, monkeypatch) == []


def test_not_offered_when_it_already_runs_as_recurring(monkeypatch):
    rows = [_tx("a", "2026-09-10", 55, "נטפליקס"), _tx("b", "2026-10-10", 55, "נטפליקס"),
            _tx("c", "2026-11-10", 55, "נטפליקס"),
            _tx("tpl", "2026-11-15", 55, "נטפליקס", is_recurring=True, recurring_frequency="monthly_same")]
    assert _suggest(rows, monkeypatch) == []


def test_the_home_page_asks_and_the_sheet_opens_with_recurring_on():
    root = pathlib.Path(__file__).resolve().parent.parent
    js = (root / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
    assert "fetch('/api/recurring-suggestions'" in js
    assert "}, yes, { makeRecurring: true });" in js
    assert "if (opts && opts.makeRecurring && !tx.isRecurring && !tx.recurringParentId) {" in js
