"""Speed (Matan, 1.11: idea 5).

In the Railway logs (a week, ~300 loads): the home page 0.06-0.15 seconds, except
the first load of the day, 0.25-0.9, all of it the recurring transactions check.
And most days there is nothing in it. Now the next recurring charge date is saved,
and until then we skip.
"""
from datetime import date

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME = "22222222-2222-2222-2222-222222222222"
TODAY = date(2026, 11, 3)


def _tpl(start, freq="monthly_same", end=None):
    return {"id": start, "date": start, "is_recurring": True, "recurring_frequency": freq,
            "recurring_end_date": end}


# ─── When is the next one ───────────────────────────────────────────────────

def test_the_next_charge_is_the_earliest_after_today():
    templates = [_tpl("2026-01-10"), _tpl("2026-01-04"), _tpl("2025-01-01", end="2026-06-01")]
    assert db.next_due_date(templates, TODAY) == date(2026, 11, 4)


def test_a_charge_due_today_is_already_done_so_the_next_one_counts():
    assert db.next_due_date([_tpl("2026-01-03")], TODAY) == date(2026, 12, 3)


def test_nothing_due_at_all():
    assert db.next_due_date([], TODAY) is None
    assert db.next_due_date([_tpl("2025-01-01", end="2026-01-01")], TODAY) is None


@pytest.mark.parametrize("nxt,can_wait", [
    ({"checked": "2026-11-01", "due": "2026-11-10"}, True),
    ({"checked": "2026-11-01", "due": "2026-11-03"}, False),     # today
    ({"checked": "2026-11-01", "due": "2026-11-02"}, False),     # past: not done
    ({"checked": "2026-10-20", "due": "2026-12-01"}, False),     # more than a week: check
    ({"checked": "2026-11-02", "due": None}, True),              # nothing expected
    ({"checked": "2026-11-05", "due": "2026-12-01"}, False),     # from the future? don't trust it
    ({"checked": "nope", "due": "2026-12-01"}, False),
    ({}, False), (None, False), ("x", False),
])
def test_when_the_check_can_wait(nxt, can_wait):
    assert db.recurring_can_wait(nxt, TODAY) is can_wait


# ─── The check at the start of the page ─────────────────────────────────────

def _run_sync(monkeypatch, session_next):
    calls = []
    monkeypatch.setattr(app_module.clock, "today", lambda: TODAY)
    monkeypatch.setattr(app_module.db, "materialize_recurring",
                        lambda fam: (calls.append(fam), (0, True))[1])
    with app.test_request_context():
        from flask import session
        if session_next is not None:
            session["recurring_next"] = session_next
        app_module._sync_recurring(_FAM)
        synced = session.get("recurring_synced")
    return calls, synced


def test_a_quiet_day_skips_the_engine_without_any_query(monkeypatch):
    calls, synced = _run_sync(monkeypatch, {"checked": "2026-11-01", "due": "2026-11-10"})
    assert calls == [] and synced == "2026-11-03"


def test_a_due_day_runs_the_engine(monkeypatch):
    calls, synced = _run_sync(monkeypatch, {"checked": "2026-11-01", "due": "2026-11-03"})
    assert calls == [_FAM] and synced == "2026-11-03"


def test_without_a_known_date_it_runs_as_before(monkeypatch):
    calls, _ = _run_sync(monkeypatch, None)
    assert calls == [_FAM]


def test_the_engine_remembers_the_next_charge_for_the_whole_family(monkeypatch):
    patches = []
    monkeypatch.setattr(db, "update_family_settings", lambda fam, patch: patches.append((fam, patch)))
    monkeypatch.setattr(db.clock, "today", lambda: TODAY)
    db._remember_next_due(_FAM, [_tpl("2026-01-20")], TODAY)
    assert patches == [(_FAM, {"recurring_next": {"due": "2026-11-20", "checked": "2026-11-03"}})]


def test_every_page_refreshes_the_sessions_copy(monkeypatch):
    nxt = {"due": "2026-11-20", "checked": "2026-11-03"}
    monkeypatch.setattr(app_module, "family_settings",
                        lambda: dict(db.DEFAULT_FAMILY_SETTINGS, recurring_next=nxt))
    with app.test_request_context():
        from flask import session
        session["user_id"] = _ME
        app_module.inject_family_settings()
        assert session["recurring_next"] == nxt


def test_the_email_check_is_not_every_ten_minutes():
    """0.2-0.4 seconds per check against the auth service, and every 10 minutes."""
    assert app_module._EMAIL_FRESH_SECONDS >= 6 * 3600
