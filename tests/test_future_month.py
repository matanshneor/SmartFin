"""A future month and the current month: what appears in them.

The current month (Matan, 4.10, and still so on 1.11): a recurring transaction
appears only from the day it is charged, when the engine creates it as a real
transaction.

A future month (Matan, 1.11): the recurring transactions expected in it appear
and are counted in the totals, until the series ends, "like a forecast". Marked
"expected" and not opened for editing. (From 4.10 to 1.11 a future month didn't
show them either.)

A transaction entered by hand with a future date is an ordinary row: it appears,
is counted, and opens for editing.
"""
import datetime

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"
_TODAY = datetime.date(2026, 10, 4)


def _row(id_, amount, date, name, icon, recurring=False):
    return {"id": id_, "family_id": _FAM, "amount": amount, "type": "expense", "date": date,
            "description": name, "user_id": None, "category_id": "c1", "project_id": None,
            "project_category_id": None, "is_recurring": recurring, "recurring_parent_id": None,
            "recurring_frequency": "monthly_15" if recurring else None,
            "recurring_end_date": None, "receipt_path": None, "workplace": None,
            "categories": {"name": name, "icon": icon},
            "project_categories": None, "profiles": None, "projects": None}


@pytest.fixture
def month_page(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(db, "get_family", lambda *a, **k: {"id": _FAM})
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    monkeypatch.setattr(app_module.clock, "now", lambda: datetime.datetime(2026, 10, 4, 12, 0))
    monkeypatch.setattr(app_module.clock, "today", lambda: _TODAY)
    monkeypatch.setattr(db, "projected_recurring_rows", lambda *a, **k: [])

    def render(real_rows, url):
        monkeypatch.setattr(db, "fetch_month_page", lambda fid, y, m: {
            "family": {"id": _FAM}, "settings": dict(db.DEFAULT_FAMILY_SETTINGS),
            "members": [{"id": _ME, "name": "מתן"}],
            "categories": [{"id": "c1", "name": "שכר דירה", "icon": "🏠", "type": "expense"}],
            "rows": real_rows, "archive": []})
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = _ME
                sess["family_id"] = _FAM
            res = c.get(url)
            assert res.status_code == 200
            return res.get_data(as_text=True)
    return render


def test_the_current_month_shows_nothing_before_its_date(month_page, monkeypatch):
    """The current month, as before: no question at all about projected occurrences."""
    monkeypatch.setattr(db, "projected_recurring_rows",
                        lambda *a, **k: pytest.fail("the current month doesn't show projected ones"))
    html = month_page([], "/month")

    assert "empty-state" in html


def test_a_future_month_shows_and_counts_what_is_expected(month_page, monkeypatch):
    rent = dict(_row("projected-rent-2026-11-15", 5500, "2026-11-15", "שכר דירה", "🏠"),
                recurring_parent_id="rent", projected=True)
    asked = []
    monkeypatch.setattr(db, "projected_recurring_rows",
                        lambda fid, viewer, y, m: asked.append((fid, viewer, y, m)) or [rent])
    html = month_page([], "/month?year=2026&month=11")

    assert asked == [(_FAM, _ME, 2026, 11)]
    assert "₪5,500" in html and "צפוי" in html
    # not a transaction yet: nothing to open
    assert 'data-id="projected-rent-2026-11-15"' not in html


def test_a_one_time_future_transaction_shows_in_its_month(month_page):
    trip = _row("trip", 1400, "2026-11-20", "טיסה", "✈️")
    html = month_page([trip], "/month?year=2026&month=11")

    assert "₪1,400" in html and "20.11" in html
    # a real transaction: opens for editing
    assert 'data-id="trip"' in html



# ─── The calculation itself ─────────────────────────────────────────────────

class _Templates:
    def __init__(self, rows): self.rows = rows
    def table(self, name): return self
    def select(self, *a, **k): return self
    def eq(self, *a): return self
    def limit(self, n): return self
    def execute(self):
        class R: pass
        r = R(); r.data = self.rows; return r


def _tpl(**over):
    t = {"id": "rent", "family_id": _FAM, "amount": 5500, "type": "expense", "date": "2026-01-15",
         "description": "שכר דירה", "user_id": None, "category_id": "c1", "project_id": None,
         "is_recurring": True, "recurring_frequency": "monthly_same", "recurring_end_date": None,
         "categories": {"name": "שכר דירה", "icon": "🏠"}, "projects": None}
    t.update(over)
    return t


def _projected(monkeypatch, templates, y, m):
    monkeypatch.setattr(db, "get_client", lambda: _Templates(templates))
    return db.projected_recurring_rows(_FAM, _ME, y, m)


def test_a_monthly_series_lands_on_its_day(monkeypatch):
    rows = _projected(monkeypatch, [_tpl()], 2026, 12)
    assert [(r["date"], r["amount"], r["projected"], r["recurring_parent_id"]) for r in rows] == \
        [("2026-12-15", 5500, True, "rent")]


def test_until_the_series_ends(monkeypatch):
    t = _tpl(recurring_end_date="2026-12-31")
    assert len(_projected(monkeypatch, [t], 2026, 12)) == 1
    assert _projected(monkeypatch, [t], 2027, 1) == []


def test_a_skipped_occurrence_stays_skipped(monkeypatch):
    t = _tpl(recurring_skips=["2026-12-15"])
    assert _projected(monkeypatch, [t], 2026, 12) == []


def test_installments_show_the_regular_payment_and_its_number(monkeypatch):
    t = _tpl(id="ace", date="2026-10-12", amount=400.01, installments=3, installment_total=1200.01,
             installment_no=1, recurring_end_date="2026-12-12")
    rows = _projected(monkeypatch, [t], 2026, 12)
    assert [(r["amount"], r["installment_no"]) for r in rows] == [(400.0, 3)]


def test_weekly_shows_every_week(monkeypatch):
    t = _tpl(date="2026-11-30", recurring_frequency="weekly")
    assert [r["date"] for r in _projected(monkeypatch, [t], 2026, 12)] == \
        ["2026-12-07", "2026-12-14", "2026-12-21", "2026-12-28"]


def test_someone_elses_personal_project_stays_hidden(monkeypatch):
    t = _tpl(project_id="p1", projects={"owner_id": "someone-else", "name": "x", "icon": "🔒"})
    assert _projected(monkeypatch, [t], 2026, 12) == []


def test_expected_rows_sit_in_date_order_among_real_ones(month_page, monkeypatch):
    rent = dict(_row("projected-rent-2026-11-10", 5500, "2026-11-10", "שכר דירה", "🏠"),
                recurring_parent_id="rent", projected=True)
    monkeypatch.setattr(db, "projected_recurring_rows", lambda *a, **k: [rent])
    trip = _row("trip", 1400, "2026-11-20", "טיסה", "✈️")
    early = _row("early", 90, "2026-11-02", "מונית", "🚕")
    html = month_page([trip, early], "/month?year=2026&month=11")

    tail = html[html.index('chart-title">כל העסקאות'):]
    assert tail.index("20.11") < tail.index("10.11") < tail.index("02.11")
