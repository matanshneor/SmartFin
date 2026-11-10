"""
Three holes in the same wall: what goes into the database, and what is reported
back.

· /api/recurring/<id>/sync was the **only** writing route that didn't go through
  _parse_amount and didn't validate the category. A bare float() accepted -5000 and
  inf, which were stopped only by the database's CHECK and came back to the user as
  an opaque 500.

· date passed from the request body to the database with no check at all, and the
  column had no constraint. With the recurring transactions engine this isn't
  theoretical: a start date in the year 1000 produces an occurrence for every month
  since, up to the 500-row ceiling, in one call.

· update_transaction and delete_transaction didn't check how many rows were
  affected. Editing a transaction another family member deleted a second earlier
  returned "saved".
"""
import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, _parse_date

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_TX  = "22222222-2222-2222-2222-222222222222"
_CAT = "44444444-4444-4444-4444-444444444444"


@pytest.fixture
def client(monkeypatch):
    # the transaction isn't in someone else's personal project (see tx_visible_required)
    monkeypatch.setattr(app_module.db, "personal_project_owner", lambda *a: None)
    # editing checks the current attributions: here a normal family transaction
    monkeypatch.setattr(app_module.db, "transaction_links",
                        lambda *a: {"user_id": None, "project_id": None})
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "get_categories",
                        lambda *a, **k: [{"id": _CAT, "name": "מכולת", "type": "expense"}])
    monkeypatch.setattr(app_module, "family_settings",
                        lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    monkeypatch.setattr(app_module.db, "is_recurring_instance", lambda *a: False)
    monkeypatch.setattr(app_module.db, "materialize_recurring", lambda fam: (0, True))
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]   = "33333333-3333-3333-3333-333333333333"
            sess["family_id"] = _FAM
        yield c


def _add(client, confirm=False, **over):
    body = {"amount": 100, "type": "expense", "date": "2026-09-21", "category_id": _CAT}
    body.update(over)
    # confirm bypasses the backfill gate (see _RETRO_WITHOUT_CONFIRM). A series
    # starting months back produces real rows, so a question is asked.
    url = "/api/transactions" + ("?confirm=1" if confirm else "")
    return client.post(url, json=body)


# ─── Date ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,why", [
    ("1000-01-01",  "שנת 1000 — 500 שורות בקריאה אחת"),
    ("9999-12-31",  "עתיד רחוק"),
    ("2026-13-01",  "חודש 13"),
    ("2026-02-30",  "30 בפברואר"),
    ("לא תאריך",    "טקסט"),
    ("",            "ריק"),
])
def test_an_impossible_date_never_reaches_the_database(client, monkeypatch, raw, why):
    monkeypatch.setattr(app_module.db, "add_transaction",
                        lambda *a, **k: pytest.fail(f"נכתב תאריך פסול: {why}"))

    assert _add(client, date=raw).status_code == 422, why


def test_ordinary_dates_still_pass():
    """Control, and the important one here: an overly strict check blocks entering
    history."""
    from backend import clock
    y = clock.today().year
    for raw in (f"{y}-01-01", f"{y - 3}-06-15", f"{y + 1}-12-31"):
        assert _parse_date(raw) == (raw, None), raw


def test_a_series_cannot_end_before_it_starts(client, monkeypatch):
    """A "recurring transaction" that ended before it started won't produce a single
    occurrence, and the user believes it is active."""
    monkeypatch.setattr(app_module.db, "add_transaction",
                        lambda *a, **k: pytest.fail("a series ending before it started was saved"))

    res = _add(client, date="2026-05-01", is_recurring=True,
               recurring_frequency="monthly_1", recurring_end_date="2026-01-01")

    assert res.status_code == 422
    assert "מוקדם מתאריך ההתחלה" in res.get_json()["error"]


def test_a_valid_end_date_is_accepted(client, monkeypatch):
    saved = {}
    monkeypatch.setattr(app_module.db, "add_transaction",
                        lambda payload, **k: (saved.update(payload) or ({"id": _TX}, None)))

    res = _add(client, confirm=True, date="2026-05-01", is_recurring=True,
               recurring_frequency="monthly_1", recurring_end_date="2027-05-01")

    assert res.status_code == 201
    assert saved["recurring_end_date"] == "2027-05-01"


def test_the_database_itself_refuses_an_absurd_date():
    """The last net, for anyone bypassing Python. Verified against the live database:
    the year 1000 was blocked, end-before-start was blocked, and a valid date
    passed."""
    from pathlib import Path
    sql = (Path(__file__).resolve().parent.parent
           / "backend/supabase/migrations/20260921200000_transaction_date_bounds.sql"
           ).read_text(encoding="utf-8")

    assert "transactions_date_sane" in sql
    assert "transactions_recurring_end_after_start" in sql


# ─── The sync route ─────────────────────────────────────────────────────────
#
# It was the only writing route that didn't go through _parse_amount and didn't
# validate the category. It no longer writes an amount or category at all: the
# normal edit route saves them, with all its checks, and the sync only splits the
# series (see test_recurring_split.py).

def test_the_sync_route_ignores_amount_and_category(client, monkeypatch):
    """An invalid amount and a foreign category in the request body reach nowhere."""
    seen = []
    monkeypatch.setattr(app_module.db, "split_recurring_series",
                        lambda *a: (seen.append(a) or ("new", None)))

    res = client.put(f"/api/recurring/{_TX}/sync",
                     json={"instance_id": "inst", "amount": -5000,
                           "category_id": "99999999-9999-9999-9999-999999999999"})

    assert res.status_code == 200
    assert seen == [(_TX, "inst", _FAM)]


# ─── A write that touched nothing ───────────────────────────────────────────

def test_editing_a_transaction_that_no_longer_exists_says_so(client, monkeypatch):
    """Another family member deleted it a second ago. Until now this returned 200
    "saved"."""
    monkeypatch.setattr(app_module.db, "update_transaction", lambda *a, **k: (None, None))

    res = client.put(f"/api/transactions/{_TX}", json={
        "amount": 100, "type": "expense", "date": "2026-09-21", "category_id": _CAT})

    assert res.status_code == 404
    assert "ייתכן שנמחקה בינתיים" in res.get_json()["error"]


class _Deleted:
    """A client that reports how many rows were really deleted."""

    def __init__(self, rows):
        self.rows = rows

    def table(self, _n):        return self
    def delete(self, *a, **k):  return self
    def eq(self, *a, **k):      return self

    def execute(self):
        self.data = self.rows
        return self


def test_deleting_a_row_that_matched_nothing_is_not_a_success(monkeypatch):
    """And not just "failed": it no longer exists (see
    test_deleting_what_is_already_gone)."""
    monkeypatch.setattr(db, "get_client", lambda: _Deleted([]))

    assert db.delete_transaction(_TX, _FAM) == (False, "not found")


def test_deleting_a_row_that_did_match_is(monkeypatch):
    monkeypatch.setattr(db, "get_client", lambda: _Deleted([{"id": _TX}]))

    assert db.delete_transaction(_TX, _FAM) == (True, None)


def test_a_failed_delete_is_not_mistaken_for_gone(monkeypatch):
    class Down(_Deleted):
        def execute(self):
            raise ConnectionError("down")
    monkeypatch.setattr(db, "get_client", lambda: Down([]))

    assert db.delete_transaction(_TX, _FAM) == (False, "error")


# ─── Deleting what another family member already deleted ────────────────────
#
# Or deleted "סופר ₪200"; Matan, who still sees it, taps delete. Until now: 500
# "מחיקה נכשלה", and the row stayed on screen, and on every further attempt
# "failed" again.

@pytest.mark.parametrize("result,code", [((False, "not found"), 404), ((False, "error"), 500),
                                         ((True, None), 200)], ids=["gone", "failed", "deleted"])
def test_deleting_what_is_already_gone(client, monkeypatch, result, code):
    monkeypatch.setattr(app_module.db, "recurring_occurrence", lambda *a: (None, True))
    monkeypatch.setattr(app_module.db, "get_transaction_receipt_path", lambda *a: None)
    monkeypatch.setattr(app_module.db, "delete_transaction", lambda *a: result)

    res = client.delete(f"/api/transactions/{_TX}")

    assert res.status_code == code
    if code == 404:
        assert res.get_json()["error"] == "העסקה כבר נמחקה"


def test_a_reset_reports_how_many_rows_it_removed(monkeypatch):
    """The most destructive action in the app returned a constant True, both when
    nothing was deleted and when hundreds were."""
    monkeypatch.setattr(db, "get_client", lambda: _Deleted([{"id": 1}, {"id": 2}, {"id": 3}]))

    assert db.reset_transactions(_FAM) == (3, None)


def test_an_empty_reset_is_not_an_error(monkeypatch):
    """Control: a family with no transactions that resets: zero rows is a valid result,
    not a failure."""
    monkeypatch.setattr(db, "get_client", lambda: _Deleted([]))

    assert db.reset_transactions(_FAM) == (0, None)
