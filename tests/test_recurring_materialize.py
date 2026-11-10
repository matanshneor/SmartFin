"""
Tests for filling in the occurrences themselves: materialize_recurring, and not
only the date generator already covered in test_recurring.py.

The bug: dedup recognised an existing occurrence by the pair (template, exact
date). Changing the date in the template (for example a salary moving from the
5th to the 10th of the month) produces a new series of dates none of which is
recognised, so **all the months back were created again**. A ₪14,000 salary was
doubled over eight months at once, on the dashboard, the month page and the
comparison chart, without the user doing anything except fixing a date.

The unique index in the database doesn't help here: it is keyed on (template,
date), and the dates really are different.

The rule tested: one occurrence per template per period: a calendar month for a
monthly frequency.
"""

import pytest

from backend import clock
from backend import supabase_config as db

pytestmark = pytest.mark.unit


class _FakeClient:
    """A minimal Supabase client: returns templates and existing occurrences, and
    collects what is written."""

    def __init__(self, templates, instances):
        self._templates = templates
        self._instances = instances
        self.inserted = []
        self._select = None

    def table(self, _name):            return self
    def select(self, cols, **k):       self._select = cols; return self
    def eq(self, *a, **k):             return self
    # A ceiling on the number of templates fetched in one run: 500 occurrences per
    # template is a ceiling for each one, and nobody limited how many templates
    # there are.
    def limit(self, *a, **k):          return self
    # existing occurrences are fetched in pages (_fetch_all); here always one short page
    def order(self, *a, **k):          return self
    def range(self, *a, **k):          return self
    def execute(self):                 return self
    def insert(self, rows, **k):       self.inserted.extend(rows); return self

    @property
    def not_(self):                    return self
    def is_(self, *a, **k):            return self

    @property
    def data(self):
        if self._select == "*":
            return self._templates
        return [{"recurring_parent_id": p, "date": d} for p, d in self._instances]


_TEMPLATE_ID = "11111111-1111-1111-1111-111111111111"


def _salary(day, freq="monthly_same"):
    return {
        "id": _TEMPLATE_ID, "amount": 14000, "type": "income",
        "date": f"2026-01-{day:02d}", "description": "משכורת",
        "category_id": None, "user_id": None,
        "recurring_frequency": freq, "recurring_end_date": None,
    }


@pytest.fixture
def frozen_september(monkeypatch):
    """Freezes 'today' at 16/09/2026, so the tests don't change with time.

    The previous version overrode datetime.date.today, and the recurring
    transactions engine doesn't call it at all, but clock.today(), derived from
    datetime.now(ISRAEL). That is, time was never frozen, and the tests ran
    against the real date.

    This didn't stay theoretical: the weekly test relies on another week not
    having passed since 15/09, so it passed for six days and failed in CI on
    22/09, a day after the tested line of code was written, and with no
    connection to it."""
    import datetime as _dt
    monkeypatch.setattr(clock, "today", lambda: _dt.date(2026, 9, 16))
    monkeypatch.setattr(clock, "now", lambda: _dt.datetime(2026, 9, 16, 12, 0))
    yield


def _run(monkeypatch, templates, instances):
    client = _FakeClient(templates, instances)
    monkeypatch.setattr(db, "get_client", lambda: client)
    monkeypatch.setattr(db, "get_categories", lambda fid: [])
    # (created, ok): the second flag was added so a failure doesn't look like
    # success and mark "synced for today" (see tests/test_recurring_sync.py)
    created, ok = db.materialize_recurring("fam-1")
    assert ok, "the run failed: the test below would test the wrong thing"
    return created, client.inserted


# ─── The bug itself ──────────────────────────────────────────────────────────

def test_moving_a_monthly_template_does_not_duplicate_past_months(
        frozen_september, monkeypatch):
    """The heart. The salary was paid on the 5th of the month from February to
    September; the user fixes the template to the 10th. Not a single occurrence
    may be created: every month already got its own."""
    already = [(_TEMPLATE_ID, f"2026-{m:02d}-05") for m in range(2, 10)]

    created, rows = _run(monkeypatch, [_salary(10)], already)

    assert created == 0, (
        f"{created} occurrences were created after a date change: "
        f"income of {len(already)} months was doubled: {[r['date'] for r in rows]}"
    )


def test_switching_to_a_fixed_day_of_month_does_not_duplicate_either(
        frozen_september, monkeypatch):
    """The same bug through a frequency change rather than a date: monthly_same →
    monthly_15."""
    already = [(_TEMPLATE_ID, f"2026-{m:02d}-05") for m in range(2, 10)]

    created, _ = _run(monkeypatch, [_salary(5, freq="monthly_15")], already)

    assert created == 0


# ─── Controls: the caution doesn't cancel the feature ────────────────────────

def test_a_brand_new_template_still_fills_in_every_month(
        frozen_september, monkeypatch):
    """Without this the 'fix' would simply switch off recurring transactions."""
    created, rows = _run(monkeypatch, [_salary(5)], [])

    assert created == 8, f"{created} were created instead of February–September"
    assert [r["date"] for r in rows][0] == "2026-02-05"
    assert all(r["recurring_parent_id"] == _TEMPLATE_ID for r in rows)


def test_a_month_that_is_genuinely_missing_is_still_filled(
        frozen_september, monkeypatch):
    """An occurrence deleted by hand: its month is empty, so it comes back."""
    already = [(_TEMPLATE_ID, f"2026-{m:02d}-05") for m in range(2, 10) if m != 5]

    created, rows = _run(monkeypatch, [_salary(5)], already)

    assert created == 1
    assert rows[0]["date"].startswith("2026-05")


def test_two_templates_do_not_block_each_other(frozen_september, monkeypatch):
    """The period is counted per template separately: rent and salary in the same
    month."""
    rent = dict(_salary(5), id="22222222-2222-2222-2222-222222222222",
                type="expense", description="שכר דירה")
    already = [(_TEMPLATE_ID, f"2026-{m:02d}-05") for m in range(2, 10)]

    created, rows = _run(monkeypatch, [_salary(5), rent], already)

    assert created == 8
    assert {r["recurring_parent_id"] for r in rows} == {rent["id"]}


def test_weekly_templates_use_proximity_not_the_calendar_month(
        frozen_september, monkeypatch):
    """A weekly one has no 'month' as a period: an occurrence that moved by a day or
    two is the same occurrence, but next week is a new and legitimate occurrence."""
    weekly = dict(_salary(5), date="2026-09-01", recurring_frequency="weekly")
    already = [(_TEMPLATE_ID, "2026-09-08"), (_TEMPLATE_ID, "2026-09-15")]

    created, rows = _run(monkeypatch, [weekly], already)

    assert created == 0, f"existing weekly occurrences were created again: {[r['date'] for r in rows]}"


# ─── An occurrence deleted on purpose doesn't come back tomorrow ─────────────

def test_a_deliberately_deleted_occurrence_does_not_come_back(
        frozen_september, monkeypatch):
    """The engine decides what is missing by what exists, and has no way to tell
    "not created yet" from "deleted on purpose": in both cases it sees a hole and
    fills it.

    Whoever cancelled a subscription for one month deleted September, and the next
    day it came back. Until the "רק את זו" option was added this didn't surface,
    because there was no way to delete a single occurrence on purpose; from the
    moment it exists, it is a feature that doesn't work."""
    tpl = dict(_salary(1), date="2026-06-01", recurring_frequency="monthly_1",
               recurring_skips=["2026-09-01"])
    already = [(_TEMPLATE_ID, "2026-07-01"), (_TEMPLATE_ID, "2026-08-01")]

    created, rows = _run(monkeypatch, [tpl], already)

    assert created == 0, f"the skipped occurrence was created again: {[r['date'] for r in rows]}"


def test_a_month_that_was_never_created_is_still_filled_in(
        frozen_september, monkeypatch):
    """Control, and the important one here: the skip must target one date. If it
    stopped the series, all the following months would silently vanish."""
    tpl = dict(_salary(1), date="2026-06-01", recurring_frequency="monthly_1",
               recurring_skips=["2026-08-01"])
    already = [(_TEMPLATE_ID, "2026-07-01")]

    created, rows = _run(monkeypatch, [tpl], already)

    dates = sorted(r["date"] for r in rows)
    assert dates == ["2026-09-01"], f"created {dates}: only September was expected"


def test_a_template_without_skips_behaves_exactly_as_before(
        frozen_september, monkeypatch):
    """Control: the column is new, and in most rows it is empty."""
    tpl = dict(_salary(1), date="2026-07-01", recurring_frequency="monthly_1")
    tpl.pop("recurring_skips", None)

    created, rows = _run(monkeypatch, [tpl], [])

    assert sorted(r["date"] for r in rows) == ["2026-08-01", "2026-09-01"]


# ─── A recurring transaction inside a project ───────────────────────────────
#
# The occurrence copied amount, type, category and user from the template, but not
# the project. And a project transaction is saved without a family category_id.
# That is, from the second month every payment to the contractor landed in the
# household expenses as "no category", and the project got stuck on the first
# payment. And if the project is personal, the payments were exposed to the whole
# family.

_PROJECT = "33333333-3333-3333-3333-333333333333"
_PROJECT_CAT = "44444444-4444-4444-4444-444444444444"


def _contractor():
    return {
        "id": _TEMPLATE_ID, "amount": 2000, "type": "expense",
        "date": "2026-07-01", "description": "קבלן",
        "category_id": None, "user_id": None,
        "project_id": _PROJECT, "project_category_id": _PROJECT_CAT,
        "recurring_frequency": "monthly_1", "recurring_end_date": None,
    }


def test_an_occurrence_stays_in_its_project(frozen_september, monkeypatch):
    created, rows = _run(monkeypatch, [_contractor()], [])

    assert [r["date"] for r in rows] == ["2026-08-01", "2026-09-01"]
    for r in rows:
        assert r["project_id"] == _PROJECT, f"{r['date']} landed in the household expenses"
        assert r["project_category_id"] == _PROJECT_CAT


def test_a_household_occurrence_stays_out_of_projects(frozen_september, monkeypatch):
    """Control: a template without a project doesn't get a project from somewhere."""
    created, rows = _run(monkeypatch, [_salary(5)], [])

    assert rows and all(r.get("project_id") is None for r in rows)
    assert all(r.get("project_category_id") is None for r in rows)


def test_an_occurrence_is_entered_by_whoever_entered_the_series(frozen_september, monkeypatch):
    """Without an explicit copy, the database would record it under auth.uid(): that
    is, whoever happened to open the app that day."""
    template = {**_salary(5), "created_by": "אור"}

    _, rows = _run(monkeypatch, [template], [])

    assert rows and all(r["created_by"] == "אור" for r in rows)
