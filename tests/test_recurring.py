"""
Tests for the recurring transactions engine: _recurring_occurrences.

This is the most bug-prone logic in the system: it runs automatically in the
background, creates financial records, and if it is wrong the user sees wrong
numbers in the budget without understanding why.

The function is pure (template + date in, list of dates out), so these tests
don't touch Supabase at all: no sign-in, no network, and no risk to the 429 limit.
Hence also the @pytest.mark.unit marker on the whole file.
"""
from datetime import date

import pytest

from backend.supabase_config import _recurring_occurrences as occurrences

pytestmark = pytest.mark.unit


def _template(start, freq=None, end=None):
    """A minimal template: only the fields _recurring_occurrences actually reads."""
    tmpl = {"date": start}
    if freq:
        tmpl["recurring_frequency"] = freq
    if end:
        tmpl["recurring_end_date"] = end
    return tmpl


# ─── Monthly on the day of the month (monthly_same) ──────────────────────────

def test_monthly_same_clamps_to_short_month_then_recovers():
    """The classic case: a template from 31 January.

    February must shorten to 28, but March must go back to 31: that is, the
    clamping derives from the current month and doesn't "stick" to the template.
    That is exactly what a naive refactor could break."""
    result = occurrences(_template("2026-01-31", "monthly_same"), date(2026, 7, 1))

    assert result == [
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
    ]


def test_monthly_same_hits_february_29_in_leap_year():
    """2028 is a leap year: the clamping must give 29 and not 28."""
    result = occurrences(_template("2028-01-31", "monthly_same"), date(2028, 4, 1))

    assert result == [date(2028, 2, 29), date(2028, 3, 31)]


def test_monthly_same_excludes_the_template_date_itself():
    """The source date is the transaction the user already entered: a duplicate of it
    must not be produced."""
    result = occurrences(_template("2026-03-10", "monthly_same"), date(2026, 5, 31))

    assert date(2026, 3, 10) not in result
    assert result == [date(2026, 4, 10), date(2026, 5, 10)]


# ─── Monthly on a fixed date (monthly_1 / monthly_15) ────────────────────────

def test_monthly_1_skips_occurrence_before_start_date():
    """A template created on 15 January with frequency "the 1st of the month": 1
    January already passed, so the first occurrence must be 1 February and not 1
    January."""
    result = occurrences(_template("2026-01-15", "monthly_1"), date(2026, 4, 30))

    assert result == [date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)]


def test_monthly_15_starting_exactly_on_the_15th():
    """When the source date is exactly the target day, that month's occurrence is
    skipped (it is the original transaction), and counting starts from the next
    month."""
    result = occurrences(_template("2026-01-15", "monthly_15"), date(2026, 4, 30))

    assert result == [date(2026, 2, 15), date(2026, 3, 15), date(2026, 4, 15)]


def test_missing_frequency_falls_back_to_monthly_1():
    """An old template without recurring_frequency shouldn't blow up: the default in
    the code is monthly_1."""
    result = occurrences(_template("2026-01-10"), date(2026, 4, 1))

    assert result == [date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)]


# ─── Weekly and biweekly ─────────────────────────────────────────────────────

def test_weekly_steps_by_seven_days():
    result = occurrences(_template("2026-01-01", "weekly"), date(2026, 2, 1))

    assert result == [
        date(2026, 1, 8),
        date(2026, 1, 15),
        date(2026, 1, 22),
        date(2026, 1, 29),
    ]


def test_biweekly_crosses_the_year_boundary():
    """A year boundary: a point where manual date calculations tend to break."""
    result = occurrences(_template("2025-12-20", "biweekly"), date(2026, 2, 1))

    assert result == [date(2026, 1, 3), date(2026, 1, 17), date(2026, 1, 31)]


# ─── Boundaries: until and recurring_end_date ────────────────────────────────

def test_end_date_is_inclusive():
    """An occurrence falling exactly on recurring_end_date is included."""
    result = occurrences(
        _template("2026-01-01", "weekly", end="2026-01-22"), date(2026, 3, 1)
    )

    assert result[-1] == date(2026, 1, 22)


def test_until_is_inclusive():
    """The same rule for the upper bound 'until' (usually: today)."""
    result = occurrences(_template("2026-01-01", "weekly"), date(2026, 1, 22))

    assert result[-1] == date(2026, 1, 22)


def test_end_date_before_first_occurrence_yields_nothing():
    result = occurrences(
        _template("2026-01-01", "monthly_same", end="2026-01-15"), date(2026, 6, 1)
    )

    assert result == []


def test_until_before_start_yields_nothing():
    """materialize_recurring runs until today: a future template produces nothing."""
    result = occurrences(_template("2026-05-01", "weekly"), date(2026, 1, 1))

    assert result == []


# ─── The 500-occurrence ceiling ──────────────────────────────────────────────

def test_long_weekly_series_is_silently_truncated_at_500():
    """Documenting existing behaviour, not approving it.

    The code has a hard ceiling of 500 occurrences. A weekly template from 2015
    should produce ~610 occurrences; in practice it stops at 500, meaning the last
    occurrence is in August 2024 and more than two years are missing. No error, no
    log, and the user just sees their recurring transaction stopped appearing
    midway.

    If the ceiling is removed or accompanied by an alert, this test will fail, and
    that is exactly the intent."""
    result = occurrences(_template("2015-01-01", "weekly"), date(2026, 9, 14))

    assert len(result) == 500
    assert result[-1] == date(2024, 8, 1)
    assert result[-1] < date(2026, 9, 14), "the ceiling stops the series far in the past"


def test_monthly_series_stays_under_the_cap():
    """Control: a monthly frequency doesn't reach the ceiling even over decades, so
    the problem is focused on the dense frequencies (weekly and biweekly)."""
    result = occurrences(_template("1990-01-01", "monthly_1"), date(2026, 9, 14))

    assert len(result) < 500
    assert result[-1] == date(2026, 9, 1)
