"""Editing a recurring transaction from "עסקאות קבועות" in settings: from the
current month on (Matan, 2.10).

The template is also the first month's transaction, so editing it rewrote January:
rent from January at ₪5,000 edited in October to ₪5,500 turned January into
₪5,500 too, and October (already created) stayed ₪5,000. Now the change is written
on this month's occurrence and the series splits there, like "עדכון להבא".

The split lives in SQL, so the tests run against the real database as the test
family, with a fixed clock, and clean up after themselves.
"""
import datetime
import uuid

import pytest

from backend import app as app_module
from backend import clock
from backend import supabase_config as db
from tests.conftest import category_of


def _table(name):
    return db.get_client().table(name)


def _freeze(monkeypatch, day):
    monkeypatch.setattr(clock, "today", lambda: day)
    monkeypatch.setattr(clock, "now", lambda: datetime.datetime(day.year, day.month, day.day, 12, 0))


@pytest.fixture
def make_series(family_a):
    """A series + the occurrences already "created". Returns a function; cleans up at
    the end."""
    db.set_auth_token(family_a["token"])
    fid = family_a["family_id"]
    marker = f"SEF-{uuid.uuid4().hex[:8]}"

    def build(start, freq, instance_dates):
        cat = category_of(fid)
        tpl = _table("transactions").insert({
            "family_id": fid, "amount": 5000, "type": "expense", "date": start,
            "description": marker, "category_id": cat, "is_recurring": True,
            "recurring_frequency": freq,
        }).execute().data[0]
        for d in instance_dates:
            _table("transactions").insert({
                "family_id": fid, "amount": 5000, "type": "expense", "date": d,
                "description": marker, "category_id": cat, "is_recurring": False,
                "recurring_parent_id": tpl["id"], "recurring_frequency": freq,
            }).execute()
        return tpl

    yield {"fid": fid, "marker": marker, "build": build}
    db.set_auth_token(family_a["token"])
    _table("transactions").delete().eq("family_id", fid).eq("description", marker).execute()
    _table("transactions").delete().eq("family_id", fid).eq("description", marker + "-חדש").execute()


def _payload(tpl, **changes):
    p = {"amount": 5500.0, "type": "expense", "date": tpl["date"], "description": tpl["description"],
         "category_id": tpl["category_id"], "user_id": None, "is_recurring": True,
         "recurring_frequency": tpl["recurring_frequency"], "recurring_end_date": None,
         "project_id": None, "project_category_id": None}
    p.update(changes)
    return p


def _rows(s):
    rows = _table("transactions").select("*").eq("family_id", s["fid"]) \
        .in_("description", [s["marker"], s["marker"] + "-חדש"]).order("date").execute().data
    return {r["date"]: r for r in rows}


def _edit(tpl, s, payload):
    with app_module.app.app_context():
        reply = app_module._edit_series_from_now(tpl["id"], s["fid"], payload)
        return None if reply is None else reply.get_json() if not isinstance(reply, tuple) \
            else reply[0].get_json()


def test_this_months_occurrence_takes_the_change_and_history_stays(make_series, monkeypatch):
    """Matan's example: rent from January, edited in September."""
    _freeze(monkeypatch, datetime.date(2026, 9, 20))
    s = make_series
    months = [f"2026-{m:02d}-01" for m in range(2, 10)]
    tpl = s["build"]("2026-01-01", "monthly_1", months)

    out = _edit(tpl, s, _payload(tpl, description=s["marker"] + "-חדש"))

    assert out["series_from"] == "2026-09-01"
    rows = _rows(s)
    assert float(rows["2026-01-01"]["amount"]) == 5000, "January was rewritten"
    assert rows["2026-01-01"]["description"] == s["marker"], "January's description too"
    for d in months[:-1]:
        assert float(rows[d]["amount"]) == 5000, f"{d} changed"
    sep = rows["2026-09-01"]
    assert float(sep["amount"]) == 5500 and sep["description"] == s["marker"] + "-חדש"
    assert sep["is_recurring"] is True and sep["recurring_parent_id"] is None
    assert rows["2026-01-01"]["recurring_end_date"] == "2026-08-31"


def test_when_this_months_occurrence_is_still_ahead_it_starts_there(make_series, monkeypatch):
    """A salary on the 20th of the month, and today is the 10th: this month's
    occurrence is created now, with the new values, and from there the new series."""
    _freeze(monkeypatch, datetime.date(2026, 9, 10))
    s = make_series
    tpl = s["build"]("2026-06-20", "monthly_same", ["2026-07-20", "2026-08-20"])

    out = _edit(tpl, s, _payload(tpl))

    assert out["series_from"] == "2026-09-20"
    rows = _rows(s)
    assert [float(rows[d]["amount"]) for d in ("2026-06-20", "2026-07-20", "2026-08-20")] == [5000] * 3
    assert float(rows["2026-09-20"]["amount"]) == 5500 and rows["2026-09-20"]["is_recurring"] is True
    assert rows["2026-06-20"]["recurring_end_date"] == "2026-09-19"
    # when the 20th arrives the engine won't duplicate it
    _freeze(monkeypatch, datetime.date(2026, 9, 25))
    db.materialize_recurring(s["fid"])
    assert len([d for d in _rows(s) if d.startswith("2026-09")]) == 1


def test_a_changed_frequency_goes_to_the_new_series(make_series, monkeypatch):
    _freeze(monkeypatch, datetime.date(2026, 9, 20))
    s = make_series
    tpl = s["build"]("2026-07-01", "monthly_1", ["2026-08-01", "2026-09-01"])

    _edit(tpl, s, _payload(tpl, recurring_frequency="monthly_15"))

    rows = _rows(s)
    assert rows["2026-09-01"]["recurring_frequency"] == "monthly_15"
    assert rows["2026-07-01"]["recurring_frequency"] == "monthly_1"


def test_a_series_that_started_this_month_is_edited_as_before(make_series, monkeypatch):
    """There is no past to protect: a normal edit of the template."""
    _freeze(monkeypatch, datetime.date(2026, 9, 20))
    s = make_series
    tpl = s["build"]("2026-09-01", "monthly_1", [])

    assert _edit(tpl, s, _payload(tpl)) is None


def test_an_end_date_before_this_month_is_refused(make_series, monkeypatch):
    _freeze(monkeypatch, datetime.date(2026, 9, 20))
    s = make_series
    tpl = s["build"]("2026-07-01", "monthly_1", ["2026-08-01", "2026-09-01"])

    out = _edit(tpl, s, _payload(tpl, recurring_end_date=datetime.date(2026, 8, 15)))

    assert "תאריך הסיום" in out["error"]
    assert float(_rows(s)["2026-09-01"]["amount"]) == 5000, "written despite the refusal"


# ─── "₪5,000 ← ₪5,500 מאוקטובר" in settings (Matan, 3.10: idea 40) ──────────

def _active(s):
    return [r for r in _rows(s).values() if r["is_recurring"] and not r["recurring_end_date"]]


def test_after_a_change_the_settings_line_knows_the_old_amount(make_series, monkeypatch):
    _freeze(monkeypatch, datetime.date(2026, 9, 20))
    s = make_series
    tpl = s["build"]("2026-07-01", "monthly_1", ["2026-08-01", "2026-09-01"])
    _edit(tpl, s, _payload(tpl))                              # 5,000 → 5,500 from September

    active = _active(s)
    assert len(active) == 1
    changes = db.recurring_price_changes(s["fid"], active)
    assert changes == {active[0]["id"]: {"old": 5000.0, "since": "2026-09-01"}}


def test_a_series_that_never_changed_has_no_line(make_series, monkeypatch):
    _freeze(monkeypatch, datetime.date(2026, 9, 20))
    s = make_series
    s["build"]("2026-07-01", "monthly_1", ["2026-08-01", "2026-09-01"])
    assert db.recurring_price_changes(s["fid"], _active(s)) == {}


def test_a_change_without_a_new_amount_has_no_line(make_series, monkeypatch):
    """A description-only change splits too, but there is no "← ₪" to show."""
    _freeze(monkeypatch, datetime.date(2026, 9, 20))
    s = make_series
    tpl = s["build"]("2026-07-01", "monthly_1", ["2026-08-01", "2026-09-01"])
    _edit(tpl, s, _payload(tpl, amount=5000.0, description=s["marker"] + "-חדש"))
    assert db.recurring_price_changes(s["fid"], _active(s)) == {}
