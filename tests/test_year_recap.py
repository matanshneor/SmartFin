""""2026 שלכם": the year recap (Matan, 1.11): opens on tap, only whoever taps
sees it; it has projects; and without a complete previous year, no comparison."""
import datetime
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
_FAM = "11111111-1111-1111-1111-111111111111"
_ME = "22222222-2222-2222-2222-222222222222"
_n = 0


def _r(date, typ, amount, cat=None, project=None, owner=None):
    global _n
    _n += 1
    names = {"food": ("סופר", "🛒"), "rent": ("דירה", "🏠"), "fun": ("בילויים", "🥂")}
    return {"id": f"r{_n}", "date": date, "type": typ, "amount": amount, "category_id": cat,
            "project_id": project,
            "categories": {"name": names[cat][0], "icon": names[cat][1]} if cat else None,
            "projects": {"owner_id": owner, "name": "חופשה", "icon": "🏖️"} if project else None}


def _recap(monkeypatch, rows, year=2026, today=datetime.date(2027, 1, 5)):
    monkeypatch.setattr(db, "get_client", lambda: object())
    monkeypatch.setattr(db, "_fetch_all", lambda make: rows)
    return db.year_recap(_FAM, _ME, year, today)


def test_the_year_in_numbers(monkeypatch):
    rows = [_r("2026-03-01", "income", 10000), _r("2026-03-05", "expense", 2000, "food"),
            _r("2026-03-06", "savings", 1000),
            _r("2026-08-01", "income", 10000), _r("2026-08-05", "expense", 7000, "rent"),
            _r("2026-08-09", "expense", 3000, None, "p1")]
    d = _recap(monkeypatch, rows)
    assert (d["income"], d["expense"], d["savings"]) == (20000, 9000, 1000)   # without the project
    assert d["savings_rate"] == 5 and d["count"] == 6
    assert d["best"] == 3 and d["costliest"] == 8
    assert [c["name"] for c in d["top"]] == ["דירה", "סופר"]
    assert d["projects"] == [{"name": "חופשה", "icon": "🏖️", "total": 3000.0}]


def test_no_comparison_without_a_full_previous_year(monkeypatch):
    rows = [_r("2025-06-01", "expense", 900, "food"), _r("2026-06-01", "expense", 1000, "food")]
    assert _recap(monkeypatch, rows)["compare"] is None


def test_comparison_with_a_full_previous_year(monkeypatch):
    rows = [_r("2025-01-03", "expense", 1000, "food"), _r("2025-05-03", "expense", 1000, "fun"),
            _r("2026-01-03", "expense", 1500, "food"), _r("2026-05-03", "expense", 500, "fun")]
    k = _recap(monkeypatch, rows)["compare"]
    assert k["year"] == 2025 and k["expense_diff"] == 0
    assert k["up"]["name"] == "סופר" and k["up"]["pct"] == 50
    assert k["down"]["name"] == "בילויים" and k["down"]["pct"] == -50


def test_someone_elses_private_project_is_left_out(monkeypatch):
    rows = [_r("2026-04-01", "expense", 800, None, "p9", owner="someone-else")]
    d = _recap(monkeypatch, rows)
    assert d["projects"] == [] and d["count"] == 0


@pytest.mark.parametrize("today,year", [
    (datetime.date(2027, 1, 1), 2026), (datetime.date(2027, 1, 15), 2026),
    (datetime.date(2026, 12, 31), None), (datetime.date(2026, 12, 20), None),
    (datetime.date(2027, 1, 16), None),
])
def test_when_the_home_card_shows(today, year):
    assert db.year_recap_due(today) == year


def test_the_api_returns_only_for_the_one_who_asks(monkeypatch):
    calls = []
    monkeypatch.setattr(app_module.clock, "today", lambda: datetime.date(2027, 1, 5))
    monkeypatch.setattr(app_module.db, "year_recap",
                        lambda fam, viewer, year, today: calls.append((fam, viewer, year)) or {"year": year})
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["user_id"], s["family_id"] = _ME, _FAM
        assert c.get("/api/year-recap").get_json() == {"year": 2026}
        assert c.get("/api/year-recap?year=2030").status_code == 422
    assert calls == [(_FAM, _ME, 2026)]


def test_the_closed_screen_does_not_block_the_page():
    """display: flex beat hidden: the closed, transparent screen blocked every tap."""
    css = (ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    assert ".yr-overlay[hidden] { display: none; }" in css


# ─── "2026 עד עכשיו": a check during the year (Matan, 1.11: option B) ────────

def test_during_the_year_it_is_up_to_today_against_the_same_period(monkeypatch):
    rows = [_r("2025-01-05", "expense", 1000, "food"), _r("2025-12-01", "expense", 9000, "food"),
            _r("2026-01-05", "expense", 1200, "food"), _r("2026-07-20", "expense", 300, "fun")]
    d = _recap(monkeypatch, rows, today=datetime.date(2026, 7, 10))
    assert d["partial"] is True and d["through"] == 7
    assert d["expense"] == 1200                         # 20.7 hasn't arrived yet
    assert d["compare"]["expense_diff"] == 200          # against January–10.7 of 2025, not against all of 2025


def test_at_the_end_of_the_year_it_is_the_full_summary(monkeypatch):
    d = _recap(monkeypatch, [_r("2026-12-24", "expense", 100, "food")])
    assert d["partial"] is False and d["through"] == 12 and d["expense"] == 100


@pytest.mark.parametrize("today,partial", [
    (datetime.date(2026, 7, 10), True), (datetime.date(2026, 12, 31), True),
    (datetime.date(2027, 1, 1), False),
])
def test_when_it_is_a_checkpoint(today, partial):
    assert db.year_recap_partial(2026, today) is partial


def test_no_emoji_and_not_at_the_top():
    html = (ROOT / "frontend/templates/months.html").read_text(encoding="utf-8")
    assert "{{ recap_year }} עד עכשיו" in html and "🎉" not in html
    assert html.index("data-year-recap") > html.index("{% block content %}")   # not in the hero
    assert "🎉" not in (ROOT / "frontend/templates/index.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend/static/js/year-recap.js").read_text(encoding="utf-8")
    assert not any(e in js for e in ("🏆", "💸", "💪", "👏", "📈", "📉", "🐷"))
