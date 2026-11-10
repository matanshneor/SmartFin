""""Expenses by family member" over the months on the comparison page (Matan,
3.10, idea 15)."""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM = "fam"
_MONTHS = [(2026, 8), (2026, 9), (2026, 10)]


def _tx(amount, date, uid=None, name=None, project=None, type_="expense"):
    return {"id": f"{amount}-{date}-{uid}", "family_id": _FAM, "amount": amount, "type": type_, "date": date,
            "user_id": uid, "project_id": project, "profiles": {"name": name} if name else None}


def _trend(rows, monkeypatch):
    monkeypatch.setattr(db, "get_client", lambda: FakeSupabase(transactions=rows))
    return db.member_trend(_FAM, _MONTHS)


def test_a_value_for_every_member_in_every_month(monkeypatch):
    rows = [_tx(300, "2026-08-04", "m", "מתן שניאור"), _tx(200, "2026-09-10", "m", "מתן שניאור"),
            _tx(500, "2026-09-12", "o", "אור שניאור"), _tx(150, "2026-10-01")]
    got = {m["name"]: m["values"] for m in _trend(rows, monkeypatch)}
    assert got == {"מתן": [300, 200, 0], "אור": [0, 500, 0], "משותפת": [0, 0, 150]}


def test_projects_and_income_are_not_counted(monkeypatch):
    rows = [_tx(900, "2026-09-01", "m", "מתן", project="p1"), _tx(5000, "2026-09-01", "m", "מתן", type_="income")]
    assert _trend(rows, monkeypatch) == []


def test_biggest_spender_first(monkeypatch):
    rows = [_tx(100, "2026-09-01", "m", "מתן"), _tx(900, "2026-09-01", "o", "אור")]
    assert [m["name"] for m in _trend(rows, monkeypatch)] == ["אור", "מתן"]


@pytest.mark.parametrize("attribution,members,shown", [
    (True, 2, True), (False, 2, False), (True, 1, False)],
    ids=["shown", "attribution-off", "single-member"])
def test_the_chart_appears_only_when_it_means_something(monkeypatch, attribution, members, shown):
    limiter.reset()
    app.config["TESTING"] = True
    settings = dict(db.DEFAULT_FAMILY_SETTINGS)
    settings["owner_attribution"] = {"expense": attribution, "income": False, "savings": False}
    monkeypatch.setattr(app_module, "family_settings", lambda: settings)
    monkeypatch.setattr(app_module, "_sync_recurring", lambda *a: None)
    monkeypatch.setattr(app_module.db, "get_months_archive",
                        lambda fid: [{"year": 2026, "month": 9, "income": 1, "expense": 1, "savings": 0}])
    monkeypatch.setattr(app_module.db, "category_trend", lambda *a, **k: {"months": [], "categories": []})
    monkeypatch.setattr(app_module.db, "get_categories", lambda *a, **k: [])
    monkeypatch.setattr(app_module.db, "get_family_members", lambda fid: [{"id": f"u{i}"} for i in range(members)])
    monkeypatch.setattr(app_module.db, "member_trend", lambda fid, months: [
        {"user_id": "u0", "name": "מתן", "values": [1]}, {"user_id": None, "name": "משותפת", "values": [2]}])
    monkeypatch.setattr(app_module, "_member_colors", lambda fid: {"u0": 0})
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = "u0"
        sess["family_id"] = _FAM
    html = c.get("/months").get_data(as_text=True)
    assert ('id="memberTrendChart"' in html) == shown


def test_the_chart_follows_the_range_and_its_legend_reads_like_its_bars():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "frontend/static/js/months.js").read_text(encoding="utf-8")
    part = js[js.index("const memberSeries = SF_VIEW.members;"):]
    part = part[:part.index("// ── The range buttons ──")]
    assert "rangeListeners.push(function () {" in part, "the range changes this chart too"
    assert "memberSeries.slice().reverse()" in part, "the first in the list: on the right"
    assert "reverse: true," not in part.split("legend:")[1][:120], "the legend in the same order as the bars"
