"""Deduplication of recurring transactions: the logic that prevents double money.

This is the function standing between the family and a doubled salary. It wasn't
covered by any direct test: the only coverage was through a full
materialize_recurring, which needs a database, and so didn't run anywhere.

The original bug it was born to prevent: dedup compared **exact** dates. Whoever
changed the salary day in the template from the 5th to the 10th got a new series
of dates none of which was recognised, and all the months back were created
again. ₪14,000 times eight months, all at once, on every screen of the app.

The functions are pure, so the tests here are values against values.
"""
from datetime import date

import pytest

from backend.supabase_config import (_already_materialized, _occurrence_period,
                                     _recurring_occurrences)

pytestmark = pytest.mark.unit


def _d(s):
    return date.fromisoformat(s)


# ═══ Monthly: the period is the month, not the day ═══════════════════════════

@pytest.mark.parametrize("freq", ["monthly_1", "monthly_15", "monthly_same"])
def test_the_same_month_on_a_different_day_is_the_same_occurrence(freq):
    """The heart. This is exactly the case that doubled the salary: same month,
    different day."""
    assert _already_materialized(freq, _d("2026-09-10"), [_d("2026-09-05")]) is True


@pytest.mark.parametrize("freq", ["monthly_1", "monthly_15", "monthly_same"])
def test_a_different_month_is_a_new_occurrence(freq):
    """Control, and no less important: dedup that is too broad swallows real salaries."""
    assert _already_materialized(freq, _d("2026-10-05"), [_d("2026-09-05")]) is False


def test_the_same_day_number_in_another_year_is_not_the_same_month():
    assert _already_materialized("monthly_1", _d("2027-09-05"), [_d("2026-09-05")]) is False


def test_nothing_existing_means_nothing_was_materialized():
    assert _already_materialized("monthly_1", _d("2026-09-05"), []) is False


def test_the_period_key_is_the_calendar_month():
    assert _occurrence_period("monthly_1", _d("2026-09-30")) == (2026, 9)
    assert _occurrence_period("monthly_1", _d("2026-09-01")) == (2026, 9)
    assert _occurrence_period("monthly_1", _d("2026-10-01")) != (2026, 9)


# ═══ Weekly and biweekly: there is no "period", so distance ══════════════════

@pytest.mark.parametrize("gap,same", [(0, True), (3, True), (4, False), (10, False)])
def test_weekly_treats_a_shift_of_up_to_three_days_as_the_same_occurrence(gap, same):
    """A weekly template that moved by a day or two is the same payment, just moved."""
    from datetime import timedelta
    existing = _d("2026-09-07")
    assert _already_materialized("weekly", existing + timedelta(days=gap), [existing]) is same


@pytest.mark.parametrize("gap,same", [(0, True), (7, True), (8, False), (14, False)])
def test_biweekly_allows_a_wider_shift(gap, same):
    from datetime import timedelta
    existing = _d("2026-09-07")
    assert _already_materialized("biweekly", existing + timedelta(days=gap), [existing]) is same


def test_a_weekly_shift_is_measured_in_both_directions():
    """An occurrence moved earlier is the same occurrence as one moved later."""
    assert _already_materialized("weekly", _d("2026-09-05"), [_d("2026-09-07")]) is True


# ═══ Producing the occurrences themselves ════════════════════════════════════

def _template(**kw):
    row = {"date": "2026-01-15", "recurring_frequency": "monthly_same"}
    row.update(kw)
    return row


def test_occurrences_stop_at_the_end_date():
    """An end date that isn't enforced produces a recurring expense that already ended,
    forever."""
    dates = _recurring_occurrences(
        _template(date="2026-01-15", recurring_end_date="2026-03-31"), _d("2026-12-31"))

    assert max(dates) <= _d("2026-03-31")


def test_occurrences_stop_at_today():
    """Future occurrences aren't a fact: they haven't happened yet."""
    dates = _recurring_occurrences(_template(date="2026-01-15"), _d("2026-04-30"))

    assert max(dates) <= _d("2026-04-30")


def test_a_monthly_series_produces_one_occurrence_per_month():
    dates = _recurring_occurrences(_template(date="2026-01-15"), _d("2026-04-30"))

    assert sorted({(d.year, d.month) for d in dates}) == [(2026, 2), (2026, 3), (2026, 4)]
    assert len(dates) == len(set(dates)), "the same date was created twice"


def test_a_day_that_does_not_exist_in_the_month_is_clamped():
    """The 31st of every month: February has no 31st, and without clamping that is a
    crash."""
    dates = _recurring_occurrences(
        _template(date="2026-01-31", recurring_frequency="monthly_same"), _d("2026-03-31"))

    february = [d for d in dates if d.month == 2]
    assert february == [_d("2026-02-28")], february


def test_the_series_cannot_run_away():
    """The ceiling that stood between a wrong date and 500 real transactions in one
    call. It is still there, but now _parse_date stops before it."""
    dates = _recurring_occurrences(_template(date="1900-01-15"), _d("2026-09-30"))

    assert len(dates) <= 500


def test_the_template_date_itself_is_not_an_occurrence():
    """The first occurrence is the transaction itself. Producing it again is a
    duplicate."""
    dates = _recurring_occurrences(_template(date="2026-01-15"), _d("2026-04-30"))

    assert _d("2026-01-15") not in dates
