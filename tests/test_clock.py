"""
Tests for the app's clock.

The server runs on Railway in UTC and the users are in Israel. The browser stamps
a new transaction in local time, while the server decided by its own clock which
month to show, and between midnight and 3am the two disagree.

The scenario that hurt: at 00:30 on September 1st, Israel time. The server in UTC
is still on August 31st, so it serves an August dashboard. The user adds a ₪500
purchase, the browser stamps it 2026-09-01, and it simply doesn't appear, until
it "pops up out of nowhere" three hours later. This repeats at every month change.
"""
from datetime import datetime, timezone

import pytest

from backend import clock

pytestmark = pytest.mark.unit


def _at_utc(monkeypatch, iso_utc: str):
    """Freezes the clock at a given moment in UTC, as the production server sees it."""
    frozen = datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)

    monkeypatch.setattr(clock, "datetime", _DT)


# ─── The scenario that hurt ─────────────────────────────────────────────────

def test_just_after_midnight_the_server_is_in_the_same_month_as_the_phone(monkeypatch):
    """00:30 on September 1st in Israel = 21:30 on August 31st in UTC.
    Before the fix the server served August while the phone was already in
    September."""
    _at_utc(monkeypatch, "2026-08-31T21:30:00")

    assert clock.today().month == 9, "the server is still in the previous month: transactions will vanish"
    assert clock.today().day == 1


def test_late_at_night_the_server_is_on_the_same_day_as_the_phone(monkeypatch):
    """Not just a month change: every night between midnight and 3am the server's
    'today' lagged by a day."""
    _at_utc(monkeypatch, "2026-09-15T22:10:00")   # 01:10 on 16/09 in Israel

    assert clock.today().day == 16


# ─── Daylight saving changes ────────────────────────────────────────────────
#
# In Israel the change is on the last Friday of March and the last Sunday of
# October: not like Europe and not like the US. So the offset is derived from
# ZoneInfo and not hard-coded.

@pytest.mark.parametrize("utc_moment,expected_hour", [
    ("2026-03-26T12:00:00", 14),   # before the change: winter, UTC+2
    ("2026-03-28T12:00:00", 15),   # after the change: summer, UTC+3
    ("2026-10-24T12:00:00", 15),   # still summer
    ("2026-10-26T12:00:00", 14),   # back to winter
])
def test_the_offset_follows_israeli_daylight_saving(monkeypatch, utc_moment, expected_hour):
    _at_utc(monkeypatch, utc_moment)

    assert clock.now().hour == expected_hour


# ─── That the clock is really used ──────────────────────────────────────────

def test_no_server_code_still_asks_the_container_for_the_time():
    """A spot fix is worth nothing here: one forgotten place is enough for a month
    to be computed on the wrong clock. The test scans the whole codebase."""
    from pathlib import Path
    import re

    backend = Path(__file__).resolve().parent.parent / "backend"
    offenders = []
    for f in backend.glob("*.py"):
        if f.name == "clock.py":
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            if re.search(r"\bdatetime\.now\(\)|\bdate\.today\(\)|utcnow\(", line):
                offenders.append(f"{f.name}:{n}")

    assert not offenders, f"the container's clock is still used in: {offenders}"


def test_the_timezone_is_pinned_to_israel():
    assert str(clock.ISRAEL) == "Asia/Jerusalem"


def test_the_clock_returns_naive_values(monkeypatch):
    """The existing code compares naive datetimes; mixing with aware raises
    TypeError mid-page. The choice is deliberate and so it is tested."""
    _at_utc(monkeypatch, "2026-09-16T08:00:00")

    assert clock.now().tzinfo is None
