"""The "השבוע" card on the home page (Matan, 30.9).

Household expenses only: without projects and without recurring transactions (one
day of rent would dwarf the whole week). Sunday to Saturday. In the current week
the total of last week is shown, complete, as a fact: without "less/more" (Matan,
11.10).
"""
import datetime

import pytest

from backend import supabase_config as db
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_WED = datetime.date(2026, 10, 7)          # Wednesday; the week: 4.10–10.10


def _tx(amount, date, desc="", cat="food", recurring=False, parent=None, project=None, type_="expense"):
    return {"id": f"{amount}-{date}-{desc}", "family_id": _FAM, "amount": amount, "type": type_,
            "date": date, "description": desc, "category_id": cat, "project_id": project,
            "is_recurring": recurring, "recurring_parent_id": parent,
            "categories": {"name": "סופר" if cat == "food" else "דלק", "icon": "🛒" if cat == "food" else "⛽"}}


def _week(rows, monkeypatch, today=_WED):
    monkeypatch.setattr(db, "get_client", lambda: FakeSupabase(transactions=rows))
    return db.week_spending(_FAM, today=today)


def test_seven_days_sunday_first_with_today_and_future(monkeypatch):
    w = _week([], monkeypatch)
    assert [d["date"] for d in w["days"]] == [f"2026-10-{d:02d}" for d in range(4, 11)]
    assert [d["label"] for d in w["days"]] == ["א׳", "ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳"]
    assert [d["today"] for d in w["days"]].index(True) == 3
    assert [d["future"] for d in w["days"]] == [False] * 4 + [True] * 3


def test_totals_per_day_and_for_the_week(monkeypatch):
    w = _week([_tx(180, "2026-10-04", "מסעדה"), _tx(412, "2026-10-05"), _tx(250, "2026-10-05", cat="fuel"),
               _tx(32, "2026-10-07")], monkeypatch)
    assert [d["total"] for d in w["days"][:4]] == [180, 662, 0, 32]
    assert w["total"] == 874
    monday = w["days"][1]["transactions"]
    assert {(t["icon"], t["name"], t["amount"]) for t in monday} == {("🛒", "סופר", 412), ("⛽", "דלק", 250)}


def test_recurring_projects_and_income_are_not_counted(monkeypatch):
    w = _week([_tx(100, "2026-10-05"),
               _tx(5500, "2026-10-04", "שכר דירה", recurring=True),
               _tx(120, "2026-10-05", "סלולר", parent="tpl-1"),
               _tx(4000, "2026-10-06", "קבלן", project="p1"),
               _tx(9000, "2026-10-04", "משכורת", type_="income")], monkeypatch)
    assert w["total"] == 100


# ─── Recurring transactions: shown and not counted (Matan, 11.10) ───────────


def test_recurring_shows_on_its_day_but_stays_out_of_bars_total_and_comparison(monkeypatch):
    w = _week([_tx(100, "2026-10-05"),
               _tx(5500, "2026-10-04", "שכר דירה", recurring=True),
               _tx(120, "2026-10-05", "סלולר", parent="tpl-1"),
               _tx(200, "2026-09-28"), _tx(3000, "2026-09-28", "ביטוח", parent="tpl-2")], monkeypatch)
    sunday, monday = w["days"][0], w["days"][1]
    assert sunday["total"] == 0 and [t["description"] for t in sunday["recurring"]] == ["שכר דירה"]
    assert monday["total"] == 100 and [t["description"] for t in monday["recurring"]] == ["סלולר"]
    assert [t["amount"] for t in monday["transactions"]] == [100]
    assert w["total"] == 100 and w["max"] == 100 and w["recurring_total"] == 5620
    assert w["last_week"] == 200        # last week's insurance isn't included in the comparison


def test_recurring_from_a_project_or_an_income_is_not_shown(monkeypatch):
    w = _week([_tx(800, "2026-10-05", "שיפוץ", recurring=True, project="p1"),
               _tx(9000, "2026-10-05", "משכורת", recurring=True, type_="income")], monkeypatch)
    assert w["recurring_total"] == 0 and w["days"][1]["recurring"] == []


def test_the_card_marks_recurring_and_adds_the_line_under_the_total(monkeypatch):
    from pathlib import Path
    from backend.app import app
    tpl = (Path(__file__).resolve().parent.parent / "frontend/templates/_week_card.html").read_text(encoding="utf-8")
    w = _week([_tx(100, "2026-10-05", "קפה"), _tx(5500, "2026-10-04", "שכר דירה", recurring=True),
               _tx(120, "2026-10-09", "סלולר", recurring=True)], monkeypatch)
    with app.test_request_context():
        html = app.jinja_env.from_string(tpl).render(week=w)
    assert "+ ₪5,620 בעסקאות קבועות" in html
    sunday = html.split('class="week-detail" data-day="0"')[1].split("</div>")[0]
    assert '<li class="is-fixed"><span>🛒 שכר דירה <span class="week-fixed-tag">קבועה</span>' in sunday
    assert "לא בוצעו עסקאות" not in sunday and "₪0" not in sunday   # a day with a recurring one only
    monday = html.split('class="week-detail" data-day="1"')[1].split("</div>")[0]
    assert "is-fixed" not in monday and "קפה" in monday
    # a future day with only a recurring transaction opens, like a day with a
    # transaction entered ahead
    bars = {b.split('data-day="')[1][0]: b[:b.index(">")] for b in html.split('<button type="button" class="week-day')[1:]}
    assert "disabled" in bars["4"] and "disabled" not in bars["5"] and 'class="week-detail" data-day="5"' in html


def test_a_week_with_only_recurring_still_shows_the_card():
    from pathlib import Path
    tpl = (Path(__file__).resolve().parent.parent / "frontend/templates/index.html").read_text(encoding="utf-8")
    assert "{% if week and (week.total or week.recurring_total or week.last_week or week.has_older) %}" in tpl


def test_this_week_shows_all_of_last_week_without_more_or_less(monkeypatch):
    """On Wednesday: all of last week, including Thursday to Saturday, and without diff."""
    w = _week([_tx(300, "2026-10-05"),
               _tx(500, "2026-09-28"), _tx(200, "2026-09-30"),
               _tx(900, "2026-10-01"), _tx(100, "2026-10-03"),
               _tx(777, "2026-09-27"),                         # last Sunday: included
               _tx(666, "2026-09-26")], monkeypatch)           # the Saturday before it: not
    assert w["last_week"] == 2477 and w["diff"] is None


def test_no_comparison_without_last_week_data(monkeypatch):
    assert _week([_tx(300, "2026-10-05")], monkeypatch)["last_week"] is None


def test_saturday_closes_the_week_and_sunday_opens_a_new_one(monkeypatch):
    sat = _week([_tx(50, "2026-10-10")], monkeypatch, today=datetime.date(2026, 10, 10))
    assert sat["days"][-1]["today"] and sat["total"] == 50
    sun = _week([_tx(50, "2026-10-10")], monkeypatch, today=datetime.date(2026, 10, 11))
    # on Sunday morning: ₪0, and next to it all of last week
    assert sun["days"][0]["date"] == "2026-10-11" and sun["total"] == 0 and sun["last_week"] == 50


def test_the_card_is_placed_under_the_three_cards_and_days_can_be_tapped():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    tpl = (root / "frontend/templates/index.html").read_text(encoding="utf-8")
    assert tpl.index('class="summary-cards"') < tpl.index('{% include "_week_card.html" %}') < tpl.index("<h2>עסקאות אחרונות</h2>")
    js = (root / "frontend/static/js/core.js").read_text(encoding="utf-8")
    assert "d.hidden = d.dataset.day !== btn.dataset.day;" in js


def test_an_empty_day_says_so_in_matans_words():
    """A past day with no transactions: "ביום זה"; today itself: "היום" (Matan, 1.10)."""
    from pathlib import Path
    import jinja2
    tpl = (Path(__file__).resolve().parent.parent / "frontend/templates/_week_card.html").read_text(encoding="utf-8")
    start = tpl.index("{% for d in week.days %}{% if not (d.future and not d.total and not d.recurring) %}")
    end = "{% endif %}{% endfor %}"
    snippet = tpl[start:tpl.index(end, tpl.index('class="week-empty"')) + len(end)]
    env = jinja2.Environment()
    env.filters["money"] = lambda v: v
    day = {"label": "ה׳", "day": 1, "month": 10, "total": 0, "transactions": [], "future": False}
    html = env.from_string(snippet).render(week={"days": [dict(day, today=False, selected=False),
                                                          dict(day, today=True, selected=True)]})
    assert html.count('<p class="week-empty">לא בוצעו עסקאות ביום זה</p>') == 1
    assert html.count('<p class="week-empty">לא בוצעו עסקאות היום</p>') == 1



# ─── A transaction entered ahead for a day that hasn't come yet (Matan, 4.10) ─


def test_a_transaction_entered_ahead_shows_on_its_day_and_counts(monkeypatch):
    w = _week([_tx(100, "2026-10-05"), _tx(450, "2026-10-09", "חוג")], monkeypatch)
    friday = w["days"][5]
    assert friday["future"] and friday["total"] == 450
    assert [(t["description"], t["amount"]) for t in friday["transactions"]] == [("חוג", 450)]
    assert w["total"] == 550 and w["max"] == 450


def test_the_card_states_last_week_as_a_fact_and_a_past_week_compares(monkeypatch):
    from pathlib import Path
    from backend.app import app
    tpl = (Path(__file__).resolve().parent.parent / "frontend/templates/_week_card.html").read_text(encoding="utf-8")
    rows = [_tx(519, "2026-10-04"), _tx(2313, "2026-10-05"), _tx(2419, "2026-09-28")]
    sunday = _week(rows, monkeypatch, today=datetime.date(2026, 10, 11))
    past = db.week_spending(_FAM, today=datetime.date(2026, 10, 11), offset=1)
    with app.test_request_context():
        now_html = app.jinja_env.from_string(tpl).render(week=sunday)
        past_html = app.jinja_env.from_string(tpl).render(week=past)
    # Sunday morning, no expenses yet: the short row of an empty week, with last
    # week's total as a fact
    assert '<p class="week-empty-sub">בשבוע שעבר: ₪2,832</p>' in now_html
    assert "פחות" not in now_html and "יותר" not in now_html
    assert '<span class="week-cmp up">₪413 יותר מהשבוע שלפניו</span>' in past_html


def test_a_recurring_occurrence_ahead_still_stays_out(monkeypatch):
    w = _week([_tx(120, "2026-10-09", "סלולר", parent="tpl-1")], monkeypatch)
    assert w["total"] == 0 and w["days"][5]["total"] == 0


def test_a_day_ahead_with_an_entry_opens_and_an_empty_one_stays_locked(monkeypatch):
    from pathlib import Path
    from backend.app import app
    tpl = (Path(__file__).resolve().parent.parent / "frontend/templates/_week_card.html").read_text(encoding="utf-8")
    w = _week([_tx(450, "2026-10-09", "חוג")], monkeypatch)       # Thursday empty, Friday with a transaction
    with app.test_request_context():
        html = app.jinja_env.from_string(tpl).render(week=w)
    bars = {b.split('data-day="')[1][0]: b[:b.index(">")] for b in html.split('<button type="button" class="week-day')[1:]}
    assert "disabled" in bars["4"] and "future" in bars["4"]
    assert "disabled" not in bars["5"] and "future" not in bars["5"]
    details = [d.split('"')[0] for d in html.split('class="week-detail" data-day="')[1:]]
    # Wednesday (today) and Friday, and Friday by its day number, not in fourth place
    # in the list
    assert details == ["0", "1", "2", "3", "5"]
    assert "חוג" in html.split('class="week-detail" data-day="5"')[1]


# ─── Paging to previous weeks (Matan, 2.10) ───────────────────────────────

def _past(rows, monkeypatch, offset):
    monkeypatch.setattr(db, "get_client", lambda: FakeSupabase(transactions=rows))
    return db.week_spending(_FAM, today=_WED, offset=offset)


def test_last_week_is_shown_whole_and_compared_with_the_week_before(monkeypatch):
    # this week: 4.10–10.10. Last week: 27.9–3.10. Before it: 20.9–26.9
    w = _past([_tx(100, "2026-10-03"), _tx(40, "2026-09-28"), _tx(60, "2026-09-22")], monkeypatch, 1)
    assert [d["date"] for d in w["days"]] == ["2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30",
                                              "2026-10-01", "2026-10-02", "2026-10-03"]
    assert not any(d["future"] or d["today"] for d in w["days"]), "last week complete"
    assert w["total"] == 140 and w["last_week"] == 60 and w["diff"] == 80
    assert w["offset"] == 1


def test_a_past_week_opens_on_its_last_day_with_spending(monkeypatch):
    w = _past([_tx(40, "2026-09-28"), _tx(30, "2026-09-30")], monkeypatch, 1)
    assert [d["selected"] for d in w["days"]].index(True) == 3        # Wednesday 30.9
    empty = _past([], monkeypatch, 1)
    assert [d["selected"] for d in empty["days"]].index(True) == 6    # no expenses: Saturday


def test_this_week_opens_on_today(monkeypatch):
    w = _week([_tx(40, "2026-10-04")], monkeypatch)
    assert [d["selected"] for d in w["days"]].index(True) == 3


def test_older_is_offered_only_when_there_is_something_older(monkeypatch):
    assert _past([_tx(40, "2026-09-28")], monkeypatch, 0)["has_older"] is True
    assert _past([_tx(40, "2026-09-28")], monkeypatch, 1)["has_older"] is False
    # a recurring transaction and a project aren't counted, as in the card itself
    rows = [_tx(5000, "2026-09-01", recurring=True), _tx(90, "2026-09-02", project="p1")]
    assert _past(rows, monkeypatch, 0)["has_older"] is False


def test_the_card_title_and_arrows_follow_the_offset():
    from pathlib import Path
    from backend.app import app
    tpl = (Path(__file__).resolve().parent.parent / "frontend/templates/_week_card.html").read_text(encoding="utf-8")
    day = {"label": "א׳", "day": 1, "month": 10, "total": 0, "transactions": [], "future": False,
           "today": False, "selected": False}
    def render(offset, has_older):
        with app.test_request_context():
            return app.jinja_env.from_string(tpl).render(week={
                "days": [dict(day)] * 7, "total": 0, "diff": None, "max": 0,
                "offset": offset, "has_older": has_older})
    now, last, old = render(0, True), render(1, True), render(3, False)
    assert ">השבוע</h2>" in now and ">שבוע שעבר</h2>" in last and ">לפני 3 שבועות</h2>" in old
    assert 'data-week-go="-1" aria-label="שבוע הבא"\n                    disabled' in now, "no next week after this week"
    assert 'data-week-go="4" aria-label="שבוע קודם"\n                    disabled' in old, "nothing older"


def test_the_week_route_returns_the_card_and_refuses_nonsense(monkeypatch):
    from backend import app as app_module
    from backend.app import app, limiter
    limiter.reset()
    app.config["TESTING"] = True
    seen = []
    real = db.week_spending
    monkeypatch.setattr(db, "get_client", lambda: FakeSupabase(transactions=[_tx(40, "2026-09-28")]))
    monkeypatch.setattr(app_module.db, "week_spending",
                        lambda fid, offset=0: seen.append(offset) or real(fid, today=_WED, offset=offset))
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = "22222222-2222-2222-2222-222222222222"
        sess["family_id"] = _FAM
    res = c.get("/api/week?offset=1")
    assert res.status_code == 200 and ">שבוע שעבר</h2>" in res.get_data(as_text=True)
    assert seen == [1]
    for bad in ("-1", "abc", "5000"):
        assert c.get(f"/api/week?offset={bad}").status_code == 422, bad


def test_a_sideways_swipe_locks_the_page_and_a_scroll_does_not():
    """Matan (2.10): "if I scroll there, it shouldn't scroll the screen up and down".
    The direction is decided once after 8px; sideways: preventDefault (hence
    passive: false). In the browser: tested with diagonal, vertical and short
    movement."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "frontend/static/js/core.js").read_text(encoding="utf-8")
    part = js[js.index("let sx = null, sy = 0, axis = null, drag = null, x = 0, stopSpring = null;"):]
    move = part[part.index("document.addEventListener('touchmove'"):]
    move = move[:move.index("}, { passive: false });")]
    assert "axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';" in move
    assert "if (axis !== 'x') return;" in move and "e.preventDefault();" in move
    assert move.index("if (axis !== 'x') return;") < move.index("e.preventDefault();"), "normal scrolling is blocked"


# ─── An empty week: one row instead of ₪0 and seven empty bars (Matan, 18.10) ─


def _render_week(w):
    from pathlib import Path
    from backend.app import app
    tpl = (Path(__file__).resolve().parent.parent / "frontend/templates/_week_card.html").read_text(encoding="utf-8")
    with app.test_request_context():
        return app.jinja_env.from_string(tpl).render(week=w)


def test_an_empty_week_shows_one_short_row_instead_of_bars(monkeypatch):
    w = _week([_tx(300, "2026-09-29")], monkeypatch, today=datetime.date(2026, 10, 6))
    html = _render_week(w)
    assert '<p class="week-empty-title">עדיין אין הוצאות השבוע</p>' in html
    assert 'class="week-empty-add"' in html
    assert 'class="week-bars"' not in html and 'class="week-sum"' not in html
    # the heading and the paging arrows remain
    assert 'class="week-nav-btn"' in html


def test_a_week_with_spending_keeps_the_chart(monkeypatch):
    html = _render_week(_week([_tx(100, "2026-10-05")], monkeypatch))
    assert 'class="week-bars"' in html and 'class="week-empty-state"' not in html


def test_an_empty_past_week_says_so_without_an_add_button(monkeypatch):
    _week([], monkeypatch, today=datetime.date(2026, 10, 11))
    past = db.week_spending(_FAM, today=datetime.date(2026, 10, 11), offset=1)
    html = _render_week(past)
    assert '<p class="week-empty-title">לא היו הוצאות בשבוע הזה</p>' in html
    assert 'class="week-empty-add"' not in html
