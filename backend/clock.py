"""The app's clock: always Israel time.

The server runs on Railway in UTC while the users are in Israel, so
datetime.now() on the server lags two hours (winter) or three (summer)
behind what the user sees on their phone.

This is not a nuance. The browser stamps a new transaction in local time
(transactions.js) while the server decides by its own clock which month to
show. At 00:30 on September 1st the server is still in August: the user adds
a ₪500 purchase, it is stamped as September, and the dashboard they see is
August's, so it simply isn't there. It "appears out of nowhere" three hours
later. This happens at every month change, and every night between midnight
and three, "today" lags by a day.

The value comes from ZoneInfo rather than a fixed offset because Israel's
daylight-saving changes (last Friday of March, last Sunday of October) don't
match Europe or the US, and hard-coding them would be wrong twice a year.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

ISRAEL = ZoneInfo("Asia/Jerusalem")


def now() -> datetime:
    """The current time in Israel, as a naive datetime.

    Naive on purpose: all existing code compares and formats naive
    datetimes, and mixing naive with aware raises TypeError. What matters
    here is that the hour, day and month match the user's own clock.
    """
    return datetime.now(ISRAEL).replace(tzinfo=None)


def now_utc() -> datetime:
    """The current time as an **aware** datetime, in UTC.

    `now()` is naive on purpose, because all display code works that way.
    But `timestamptz` columns come back from the database with an offset,
    and comparing naive with aware raises `TypeError`. So any comparison
    against the database uses this function, not `now()` with a manual fix.
    """
    from datetime import timezone
    return datetime.now(timezone.utc)


def today():
    """Today's date in Israel time."""
    return now().date()
