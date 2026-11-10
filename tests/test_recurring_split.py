""""עדכן להבא" splits the series, and doesn't rewrite the first month.

Until now "עדכן להבא" updated the template row, and the template is also the
transaction of the first month. Rent from January at 5,000, edited in September to
5,500, turned January into 5,500 too.

The split lives in SQL (split_recurring_series), so the tests here run against the
real database as the test family (not against anyone's account) and clean up
after themselves.

The series: monthly on the 1st from January 2026. April and August were skipped
(deleted in the past), so there is a skip before the split point and a skip after
it. We split in June: a month followed by occurrences that were already created
(July, September), because that is exactly where a duplicate would be born.
"""
import uuid

import pytest

from backend import supabase_config as db
from tests.conftest import category_of

_MONTHS_WITH_INSTANCES = ["02", "03", "05", "06", "07", "09"]


def _table(name):
    return db.get_client().table(name)


@pytest.fixture
def series(family_a, monkeypatch):
    # The clock is fixed: the series is written by hand up to September 2026, and
    # on the real clock the engine rightly creates October, and the test broke by
    # itself on 1.10.
    import datetime as _dt
    from backend import clock as _clock
    monkeypatch.setattr(_clock, "today", lambda: _dt.date(2026, 9, 30))
    monkeypatch.setattr(_clock, "now", lambda: _dt.datetime(2026, 9, 30, 12, 0))
    db.set_auth_token(family_a["token"])
    fid = family_a["family_id"]
    marker = f"SPLIT-{uuid.uuid4().hex[:8]}"
    ids = {}
    cat = category_of(fid)
    try:
        template = _table("transactions").insert({
            "family_id": fid, "amount": 5000, "type": "expense",
            "date": "2026-01-01", "description": marker, "category_id": cat,
            "is_recurring": True, "recurring_frequency": "monthly_1",
            "recurring_skips": ["2026-04-01", "2026-08-01"],
        }).execute().data[0]
        ids["template"] = template["id"]
        for mm in _MONTHS_WITH_INSTANCES:
            row = _table("transactions").insert({
                "family_id": fid, "amount": 5000, "type": "expense",
                "date": f"2026-{mm}-01", "description": marker, "category_id": cat,
                "is_recurring": False, "recurring_parent_id": template["id"],
                "recurring_frequency": "monthly_1",
            }).execute().data[0]
            ids[mm] = row["id"]
        yield {"fid": fid, "marker": marker, "ids": ids, "family": family_a}
    finally:
        db.set_auth_token(family_a["token"])
        _table("transactions").delete().eq("family_id", fid).eq("description", marker).execute()


def _rows(s):
    db.set_auth_token(s["family"]["token"])
    rows = _table("transactions").select("*").eq("family_id", s["fid"]) \
        .eq("description", s["marker"]).execute().data
    return {r["date"][5:7]: r for r in rows}


def _edit_june_and_split(s):
    """What the browser does: first saves June at the new amount, then "עדכן להבא"."""
    db.set_auth_token(s["family"]["token"])
    _table("transactions").update({"amount": 5500}).eq("id", s["ids"]["06"]).execute()
    return db.split_recurring_series(s["ids"]["template"], s["ids"]["06"], s["fid"])


def test_the_first_month_is_not_rewritten(series):
    """The bug itself."""
    new_id, err = _edit_june_and_split(series)

    assert err is None and new_id == series["ids"]["06"]
    rows = _rows(series)
    assert float(rows["01"]["amount"]) == 5000, "January was rewritten"


def test_the_old_series_ends_the_day_before(series):
    _edit_june_and_split(series)
    old = _rows(series)["01"]

    assert old["is_recurring"] is True
    assert old["recurring_end_date"] == "2026-05-31"
    assert old["recurring_skips"] == ["2026-04-01"], "a skip after the split stayed in the old series"


def test_the_edited_month_starts_the_new_series(series):
    _edit_june_and_split(series)
    june = _rows(series)["06"]

    assert june["is_recurring"] is True
    assert june["recurring_parent_id"] is None
    assert june["recurring_frequency"] == "monthly_1"
    assert june["recurring_end_date"] is None
    assert float(june["amount"]) == 5500
    assert june["recurring_skips"] == ["2026-08-01"], "August's skip didn't move to the new series"


def test_history_keeps_its_amounts_and_its_series(series):
    """What came before the split belongs to the old one; what came after moved to
    the new one, without its amount changing."""
    _edit_june_and_split(series)
    rows = _rows(series)
    template, june = series["ids"]["template"], series["ids"]["06"]

    for mm in ("02", "03", "05"):
        assert rows[mm]["recurring_parent_id"] == template, f"{mm} left the old series"
    for mm in ("07", "09"):
        assert rows[mm]["recurring_parent_id"] == june, f"{mm} didn't move to the new series"
        assert float(rows[mm]["amount"]) == 5000, f"{mm} was rewritten"


def test_the_engine_creates_no_duplicates_afterwards(series):
    """The reason the later occurrences must move: a new series that doesn't see July
    and September would create them again. And the old one, ending in May,
    shouldn't create anything: April was skipped."""
    _edit_june_and_split(series)
    before = len(_rows(series))

    db.set_auth_token(series["family"]["token"])
    _, ok = db.materialize_recurring(series["fid"])

    assert ok
    rows = _table("transactions").select("date").eq("family_id", series["fid"]) \
        .eq("description", series["marker"]).execute().data
    assert len(rows) == before, sorted(r["date"] for r in rows)


def test_another_family_cannot_split(series, family_b):
    """The function bypasses RLS; the check inside it is the only protection."""
    db.set_auth_token(family_b["token"])
    new_id, err = db.split_recurring_series(
        series["ids"]["template"], series["ids"]["06"], series["fid"])

    assert (new_id, err) == (None, None)
    assert _rows(series)["06"]["is_recurring"] is False, "another family split"


def test_an_instance_of_a_different_series_is_refused(series):
    """The UI sends both ids; a mismatched combination mustn't touch anything."""
    db.set_auth_token(series["family"]["token"])
    new_id, _ = db.split_recurring_series(
        series["ids"]["06"], series["ids"]["07"], series["fid"])

    assert new_id is None
    assert _rows(series)["01"]["recurring_end_date"] is None
