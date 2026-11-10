"""max_rows = 1000: the database returns up to a thousand rows per request, with no
error and no sign.

Three places asked for more than can arrive:
· the "full" export: a family with 55 transactions a month passes it within a year
  and a half, and from then the file holds only the newest thousand and looks
  complete.
· the recurring transactions engine: the list of existing occurrences. Past a
  thousand it stops seeing some of them and creates them again.
· the trend chart: 12 months of transactions. It is now computed from the archive
  table, grouped in SQL (a row per month), so the two agree by construction.
"""
from datetime import date

import pytest

from backend import clock
from backend import supabase_config as db
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"


def _rows(n, **extra):
    return [{"id": f"t{i:05d}", "family_id": _FAM, "amount": 1.0, "type": "expense",
             "date": f"20{10 + i // 400}-01-01", "project_id": None, **extra}
            for i in range(n)]


def test_the_full_export_is_actually_full(monkeypatch):
    fake = FakeSupabase(max_rows=1000, transactions=_rows(1005))
    monkeypatch.setattr(db, "get_client", lambda: fake)
    for name, value in [("get_projects", []), ("get_profile", {}), ("get_family", {}),
                        ("get_family_members", []), ("get_categories", [])]:
        monkeypatch.setattr(db, name, lambda *a, _v=value: _v)

    exported = db.export_account_data(_FAM, "me")["transactions"]

    assert len(exported) == 1005, f"the export was cut at {len(exported)}"
    assert len({t["id"] for t in exported}) == 1005, "overlapping pages: duplicate rows"


def test_the_engine_sees_every_existing_occurrence(monkeypatch):
    """The list of existing occurrences is of the whole family, not of one series.
    Three weekly series, 400 occurrences each: 1,200 together. Each is missing
    exactly this week's occurrence: three will be created, not two hundred."""
    import datetime as _dt
    start = _dt.date(2019, 1, 1)
    rows = []
    for t in ("a", "b", "c"):
        rows.append({"id": t, "family_id": _FAM, "amount": 10.0, "type": "expense",
                     "date": start.isoformat(), "description": "", "category_id": None,
                     "user_id": None, "is_recurring": True, "recurring_frequency": "weekly",
                     "recurring_end_date": None, "recurring_skips": None})
        rows += [{"id": f"{t}{k}", "family_id": _FAM, "recurring_parent_id": t,
                  "is_recurring": False,
                  "date": (start + _dt.timedelta(weeks=k)).isoformat()} for k in range(1, 401)]
    monkeypatch.setattr(clock, "today", lambda: start + _dt.timedelta(weeks=401))
    fake = FakeSupabase(max_rows=1000, transactions=rows)
    monkeypatch.setattr(db, "get_client", lambda: fake)
    monkeypatch.setattr(db, "get_categories", lambda fid: [])

    created, ok = db.materialize_recurring(_FAM)

    assert ok
    assert created == 3, f"{created} were created: the engine didn't see all the existing occurrences"


# ─── The trend chart ─────────────────────────────────────────────────────────

_ARCHIVE = [  # newest to oldest, as get_months_archive returns
    {"year": 2026, "month": 11, "income": 0, "expense": 999, "savings": 0, "balance": -999},
    {"year": 2026, "month": 9, "income": 10000, "expense": 6200.5, "savings": 500, "balance": 3799.5},
    {"year": 2026, "month": 8, "income": 9000, "expense": 7000, "savings": 0, "balance": 2000},
    {"year": 2025, "month": 10, "income": 1, "expense": 1, "savings": 1, "balance": 0},
    {"year": 2025, "month": 9, "income": 1, "expense": 1, "savings": 1, "balance": 0},
    {"year": 2025, "month": 8, "income": 1, "expense": 1, "savings": 1, "balance": 0},
]


def test_the_trend_is_the_archive_itself():
    """Exactly the same numbers as in the table, including the rent on the 30th of the
    current month, which the old chart omitted (it stopped at "today") and the
    table didn't."""
    trend = db.monthly_trend(_ARCHIVE, 12, today=date(2026, 9, 16))

    by_key = {m["key"]: m for m in trend}
    assert by_key["2026-09"]["expense"] == 6200.5
    assert by_key["2026-08"]["income"] == 9000
    assert by_key["2026-09"]["month_name"] == "ספטמבר"


def test_the_trend_shows_no_future_months():
    """A payment paid ahead for November doesn't create a column in "the last 12
    months"."""
    keys = [m["key"] for m in db.monthly_trend(_ARCHIVE, 12, today=date(2026, 9, 16))]

    assert "2026-11" not in keys


def test_the_trend_window_is_twelve_months_oldest_first():
    """October 2025 to September 2026: October on the boundary is in, September 2025
    isn't."""
    keys = [m["key"] for m in db.monthly_trend(_ARCHIVE, 12, today=date(2026, 9, 16))]

    assert keys == ["2025-10", "2026-08", "2026-09"], keys
