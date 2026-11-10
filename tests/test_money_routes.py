"""The routes that move money, this time really run.

The gap these tests close: `add_transaction`, `update_transaction`,
`delete_transaction`, `reset_transactions` and `update_recurring_template` weren't
mentioned in any test in the suite. Hundreds of tests ran green over the central
money route without touching it: the only coverage was the "not signed in" 401 line.

That means breaking add, edit or delete would have gone live with no test
failing. The safety net was spread under everything except the part that moves
money.

The tests here run the real route and the real data function over an in-memory
double (`tests/_fake_db.py`), and check **what was actually saved**, not what was
returned. The reason: almost every bug in this group showed up exactly in that
gap: a response saying "saved" over a database nobody touched.
"""
import pytest

from backend import supabase_config as db
from backend.app import app, limiter
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_FAM   = "11111111-1111-1111-1111-111111111111"
_OTHER = "99999999-9999-9999-9999-999999999999"
_ME    = "22222222-2222-2222-2222-222222222222"
_SPOUSE = "33333333-3333-3333-3333-333333333333"

_CAT_FOOD   = "aaaaaaaa-0000-0000-0000-000000000001"
_CAT_SALARY = "aaaaaaaa-0000-0000-0000-000000000002"

_CATEGORIES = [
    {"id": _CAT_FOOD,   "name": "מכולת",  "type": "expense", "family_id": _FAM},
    {"id": _CAT_SALARY, "name": "משכורת", "type": "income",  "family_id": _FAM},
]


_CAT_SAVINGS = "aaaaaaaa-0000-0000-0000-000000000003"
_CATEGORIES.append({"id": _CAT_SAVINGS, "name": "פיקדון", "type": "savings", "family_id": _FAM})
_PROJECT_CAT = "pc-1"
_DEFAULT_CATEGORY = {"expense": _CAT_FOOD, "income": _CAT_SALARY, "savings": _CAT_SAVINGS}


def _with_category(body):
    """No transaction without a category (test_category_required). A test not about
    categories gets the one matching the type, and isn't rejected on something it
    didn't check."""
    body = dict(body)
    if body.get("project_id"):
        body.setdefault("project_category_id", _PROJECT_CAT)
    else:
        body.setdefault("category_id", _DEFAULT_CATEGORY.get(body.get("type")))
    return body


class _Money:
    """The double, plus shortcuts that read like the sentence the test checks."""

    def __init__(self, fake, client):
        self.fake = fake
        self.client = client

    @property
    def transactions(self):
        return self.fake.rows("transactions")

    def only(self):
        rows = self.transactions
        assert len(rows) == 1, f"expected one row, there are {len(rows)}: {rows}"
        return rows[0]

    def post(self, **body):
        return self.client.post("/api/transactions", json=_with_category(body))

    def put(self, tx_id, **body):
        return self.client.put(f"/api/transactions/{tx_id}", json=_with_category(body))

    def delete(self, tx_id, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return self.client.delete(f"/api/transactions/{tx_id}" + (f"?{query}" if query else ""))


@pytest.fixture
def money(monkeypatch):
    """A real route → a real data function → an in-memory table."""
    # The clock is fixed: the dates here are written by hand (September 2026), and
    # on the real clock a recurring series from 15.9 "backfills" more and more as
    # time passes, and the tests broke by themselves (found in the review of
    # 1.10: one in November, seven in January).
    import datetime as _dt
    from backend import clock as _clock
    monkeypatch.setattr(_clock, "today", lambda: _dt.date(2026, 9, 30))
    monkeypatch.setattr(_clock, "now", lambda: _dt.datetime(2026, 9, 30, 12, 0))
    limiter.reset()
    app.config["TESTING"] = True

    fake = FakeSupabase(transactions=[])
    monkeypatch.setattr(db, "get_client", lambda: fake)

    # Neighbours that aren't the subject under test. Attribution is off by
    # default, so the transaction is saved as household, and every test that
    # needs attribution turns it on explicitly.
    monkeypatch.setattr(db, "get_family_settings",
                        lambda fid: {"owner_attribution": {"expense": False, "income": False,
                                                           "savings": False}})
    monkeypatch.setattr(db, "get_categories", lambda fid: list(_CATEGORIES))
    monkeypatch.setattr(db, "get_project_categories",
                        lambda pid, fid, type_=None: [{"id": _PROJECT_CAT, "project_id": pid}])
    monkeypatch.setattr(db, "get_family_members",
                        lambda fid: [{"id": _ME, "name": "מתן"},
                                     {"id": _SPOUSE, "name": "אור"}])
    monkeypatch.setattr(db, "get_profile", lambda uid: {"id": uid, "workplace": "מקום"})
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(db, "recurring_occurrence", lambda tx, fid: (None, True))
    monkeypatch.setattr(db, "is_recurring_instance", lambda tx, fid: False)
    monkeypatch.setattr(db, "get_transaction_receipt_path", lambda tx, fid: None)

    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
            sess["user_name"] = "מתן"
            sess["user_email"] = "matan@example.com"
        yield _Money(fake, c)


def _seed(money, **overrides):
    """An existing row in the table, as the database would return it."""
    row = {"id": "tx-1", "family_id": _FAM, "amount": 100.0, "type": "expense",
           "date": "2026-09-10", "description": "קפה", "category_id": _CAT_FOOD,
           "user_id": None, "is_recurring": False}
    row.update(overrides)
    money.fake.tables["transactions"].append(row)
    return row


# ═══ Adding ══════════════════════════════════════════════════════════════════

def test_adding_a_transaction_stores_the_amount_that_was_sent(money):
    """The heart, and this is the test that didn't exist: 250.50 went in, 250.50 was saved."""
    response = money.post(amount="250.50", type="expense", date="2026-09-15",
                          description="מכולת", category_id=_CAT_FOOD)

    assert response.status_code == 201, response.get_json()
    row = money.only()
    assert row["amount"] == 250.50
    assert row["type"] == "expense"
    assert row["date"] == "2026-09-15"
    assert row["description"] == "מכולת"
    assert row["category_id"] == _CAT_FOOD


def test_the_transaction_is_stamped_with_the_callers_family(money):
    """The isolation itself. `family_id` is taken from the session and not the
    request body; otherwise a transaction could be recorded into another family."""
    money.post(amount="80", type="expense", date="2026-09-15",
               family_id=_OTHER, category_id=_CAT_FOOD)

    assert money.only()["family_id"] == _FAM


@pytest.mark.parametrize("amount,why", [
    ("-500",        "סכום שלילי"),
    ("0",           "אפס"),
    ("inf",         "אינסוף"),
    ("nan",         "לא-מספר"),
    ("999999999",   "מעל תקרת NUMERIC(10,2)"),
    ("0.001",       "מתחת לאגורה — מתעגל לאפס"),
    ("abc",         "לא מספר בכלל"),
])
def test_a_bad_amount_is_refused_and_nothing_is_written(money, amount, why):
    """`422` alone isn't enough: the question is whether something was saved before
    the refusal."""
    response = money.post(amount=amount, type="expense", date="2026-09-15")

    assert response.status_code == 422, f"{why} was accepted"
    assert money.transactions == [], f"{why} was saved despite the refusal"


@pytest.mark.parametrize("tx_type", ["transfer", "", "EXPENSE", "הוצאה"])
def test_an_unknown_type_is_refused_and_nothing_is_written(money, tx_type):
    response = money.post(amount="50", type=tx_type, date="2026-09-15")

    assert response.status_code == 422
    assert money.transactions == []


def test_a_category_from_another_family_is_refused(money):
    """The category comes from the client. Without an ownership check a transaction
    can be hung on another family's category, and the monthly breakdown silently
    breaks."""
    response = money.post(amount="50", type="expense", date="2026-09-15",
                          category_id="לא-שלי")

    assert response.status_code == 422
    assert money.transactions == []


def test_a_category_of_the_wrong_type_is_refused(money):
    """A salary category on an expense: the amount would be counted on the wrong side."""
    response = money.post(amount="50", type="expense", date="2026-09-15",
                          category_id=_CAT_SALARY)

    assert response.status_code == 422
    assert money.transactions == []


def test_an_impossible_date_is_refused_and_nothing_is_written(money):
    """A date is what drove the recurring engine to its cap: a recurring
    transaction from the year 1000 created 500 real rows in one call."""
    response = money.post(amount="50", type="expense", date="1000-01-01")

    assert response.status_code == 422
    assert money.transactions == []


def test_a_series_that_ends_before_it_starts_is_refused(money):
    response = money.post(amount="50", type="expense", date="2026-09-15",
                          is_recurring=True, recurring_frequency="monthly_1",
                          recurring_end_date="2026-08-01")

    assert response.status_code == 422
    assert money.transactions == []


def test_an_expense_is_attributed_to_the_caller_when_attribution_is_on(money, monkeypatch):
    monkeypatch.setattr(db, "get_family_settings",
                        lambda fid: {"owner_attribution": {"expense": True}})

    money.post(amount="50", type="expense", date="2026-09-15")

    assert money.only()["user_id"] == _ME


def test_an_expense_cannot_be_hung_on_someone_outside_the_family(money, monkeypatch):
    """Forged attribution: the uuid came from the client, and RLS checks only `family_id`."""
    monkeypatch.setattr(db, "get_family_settings",
                        lambda fid: {"owner_attribution": {"expense": True}})

    response = money.post(amount="50", type="expense", date="2026-09-15",
                          owner="44444444-4444-4444-4444-444444444444")

    assert response.status_code == 422
    assert money.transactions == []


def test_attribution_that_is_off_stores_the_transaction_as_shared(money):
    """Whoever turned attribution off shouldn't get it back through the request body."""
    money.post(amount="50", type="expense", date="2026-09-15", owner=_SPOUSE)

    assert money.only()["user_id"] is None


def test_a_new_recurring_transaction_fills_in_its_occurrences(money, monkeypatch):
    """A recurring transaction added retroactively must fill in what is missing
    right away; otherwise the month looks empty until the next visit."""
    calls = []
    monkeypatch.setattr(db, "materialize_recurring",
                        lambda fid: (calls.append(fid), (0, True))[1])

    money.post(amount="7000", type="expense", date="2026-09-01",
               is_recurring=True, recurring_frequency="monthly_1")

    assert calls == [_FAM]
    assert money.only()["is_recurring"] is True


def test_a_plain_transaction_does_not_run_the_recurring_engine(money, monkeypatch):
    """Control: the engine creates real rows, and must not run for nothing."""
    calls = []
    monkeypatch.setattr(db, "materialize_recurring",
                        lambda fid: (calls.append(fid), (0, True))[1])

    money.post(amount="50", type="expense", date="2026-09-15")

    assert calls == []


# ═══ Editing ═════════════════════════════════════════════════════════════════

def test_editing_a_transaction_changes_what_is_stored(money):
    _seed(money, amount=100.0, description="קפה")

    response = money.put("tx-1", amount="180", type="expense", date="2026-09-11",
                         description="קפה וכריך", category_id=_CAT_FOOD)

    assert response.status_code == 200, response.get_json()
    row = money.only()
    assert row["amount"] == 180.0
    assert row["description"] == "קפה וכריך"
    assert row["date"] == "2026-09-11"


def test_editing_a_transaction_that_is_gone_says_so(money):
    """The original bug: another family member deleted the row a second earlier, and
    the editor got `200 {"status":"ok"}` and believed the edit went through."""
    response = money.put("tx-נעלם", amount="180", type="expense", date="2026-09-11")

    assert response.status_code == 404
    assert response.get_json().get("status") != "ok"


def test_editing_cannot_reach_into_another_family(money):
    """The same route, another family's row: must look like it doesn't exist."""
    _seed(money, id="tx-זר", family_id=_OTHER, amount=100.0)

    response = money.put("tx-זר", amount="999", type="expense", date="2026-09-11")

    assert response.status_code == 404
    assert money.only()["amount"] == 100.0, "another family's transaction was edited"


def test_an_occurrence_cannot_become_a_second_template(money, monkeypatch):
    """Marking an existing occurrence "recurring" created a parallel series: rent
    twice a month, forever, with no way to see why."""
    _seed(money, id="tx-מופע")
    monkeypatch.setattr(db, "is_recurring_instance", lambda tx, fid: True)

    response = money.put("tx-מופע", amount="7000", type="expense",
                         date="2026-09-01", is_recurring=True,
                         recurring_frequency="monthly_1")

    assert response.status_code == 422
    assert money.only()["is_recurring"] is False, "a second series was created"


def test_an_unknown_series_state_refuses_rather_than_guesses(money, monkeypatch):
    """A duplicate series is damage that can't be undone; a retry can."""
    _seed(money, id="tx-מופע")
    def _boom(tx, fid):
        raise db.DataUnavailable("is_recurring_instance")
    monkeypatch.setattr(db, "is_recurring_instance", _boom)

    response = money.put("tx-מופע", amount="7000", type="expense",
                         date="2026-09-01", is_recurring=True,
                         recurring_frequency="monthly_1")

    assert response.status_code == 503
    assert money.only()["is_recurring"] is False


@pytest.mark.parametrize("amount", ["-500", "0", "inf", "999999999"])
def test_a_bad_amount_in_an_edit_leaves_the_old_value_alone(money, amount):
    _seed(money, amount=100.0)

    response = money.put("tx-1", amount=amount, type="expense", date="2026-09-11")

    assert response.status_code == 422
    assert money.only()["amount"] == 100.0


# ═══ Deleting ════════════════════════════════════════════════════════════════

def test_deleting_a_transaction_removes_it(money):
    _seed(money)

    response = money.delete("tx-1")

    assert response.status_code == 200
    assert money.transactions == []


def test_deleting_something_that_is_not_there_does_not_say_ok(money):
    """`delete_transaction` returned `True` even when no row was touched."""
    response = money.delete("tx-נעלם")

    assert response.get_json().get("status") != "ok"
    assert response.status_code == 404
    assert response.get_json()["error"] == "העסקה כבר נמחקה"


def test_deleting_cannot_reach_into_another_family(money):
    _seed(money, id="tx-זר", family_id=_OTHER)

    response = money.delete("tx-זר")

    assert response.get_json().get("status") != "ok"
    assert response.status_code == 404, "the response doesn't confirm the transaction exists for others"
    assert len(money.transactions) == 1, "another family's transaction was deleted"


def test_deleting_a_recurring_transaction_asks_before_it_touches_anything(money, monkeypatch):
    """Without `mode` the question is asked, and nothing is deleted meanwhile."""
    _seed(money, id="tx-קבועה", is_recurring=True)
    monkeypatch.setattr(db, "recurring_occurrence",
                        lambda tx, fid: ({"template_id": "tx-קבועה",
                                          "date": "2026-09-01", "later": 4}, True))

    response = money.delete("tx-קבועה")

    assert response.status_code == 409
    assert response.get_json() == {"needs_choice": True, "later": 4}
    assert len(money.transactions) == 1, "deleted before the question was asked"


def test_an_unknown_series_state_refuses_to_delete_blindly(money, monkeypatch):
    _seed(money, id="tx-קבועה")
    def _boom(tx, fid):
        raise db.DataUnavailable("recurring_occurrence")
    monkeypatch.setattr(db, "recurring_occurrence", _boom)

    response = money.delete("tx-קבועה")

    assert response.status_code == 503
    assert len(money.transactions) == 1


# ═══ Reset: the most destructive action ══════════════════════════════════════

@pytest.fixture
def reset(money, monkeypatch):
    class _Session:
        access_token = "token"
    monkeypatch.setattr(db, "sign_in", lambda e, p: (type("R", (), {"session": _Session})(), None))
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "is_family_manager", lambda: True)
    return money


def test_resetting_the_family_deletes_only_this_family(reset):
    _seed(reset, id="a"); _seed(reset, id="b")
    _seed(reset, id="זר", family_id=_OTHER)

    response = reset.client.post("/api/account/reset",
                                 json={"password": "pw", "scope": "family"})

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["deleted"] == 2
    assert [r["id"] for r in reset.transactions] == ["זר"]


def test_resetting_mine_leaves_everyone_elses_money_alone(reset):
    """The mistake here is one-way: whoever asked to reset their own and also
    deleted their partner's can't undo it."""
    _seed(reset, id="שלי", user_id=_ME)
    _seed(reset, id="שלה", user_id=_SPOUSE)

    response = reset.client.post("/api/account/reset",
                                 json={"password": "pw", "scope": "mine"})

    assert response.get_json()["deleted"] == 1
    assert [r["id"] for r in reset.transactions] == ["שלה"]


def test_a_wrong_password_deletes_nothing(reset, monkeypatch):
    _seed(reset, id="a")
    monkeypatch.setattr(db, "sign_in", lambda e, p: (None, "invalid"))

    response = reset.client.post("/api/account/reset",
                                 json={"password": "לא נכון", "scope": "family"})

    assert response.status_code == 403
    assert len(reset.transactions) == 1


def test_only_the_manager_can_wipe_the_whole_family(reset, monkeypatch):
    """The most destructive action was open to any member with their own password."""
    _seed(reset, id="a")
    monkeypatch.setattr(db, "is_family_manager", lambda: False)

    response = reset.client.post("/api/account/reset",
                                 json={"password": "pw", "scope": "family"})

    assert response.status_code == 403
    assert len(reset.transactions) == 1, "a non-manager deleted the family's transactions"


def test_a_member_who_is_not_the_manager_can_still_reset_their_own(reset, monkeypatch):
    """Control: it is the requester's own money."""
    _seed(reset, id="שלי", user_id=_ME)
    monkeypatch.setattr(db, "is_family_manager", lambda: False)

    response = reset.client.post("/api/account/reset",
                                 json={"password": "pw", "scope": "mine"})

    assert response.status_code == 200
    assert reset.transactions == []


def test_resetting_an_empty_family_is_not_a_failure(reset):
    """Zero rows is a valid result, not an error."""
    response = reset.client.post("/api/account/reset",
                                 json={"password": "pw", "scope": "family"})

    assert response.status_code == 200
    assert response.get_json()["deleted"] == 0


# ═══ Recurring transaction: "update from now on" ════════════════════════════
#
# The route updated the template row (which is also the first month's
# transaction), so "5,500 from next month" rewrote January too. Now it splits the
# series in SQL (`split_recurring_series`); the logic itself is tested against
# the real database in `tests/test_recurring_split.py`. Here: what the route
# passes on, and what it doesn't write.

def test_syncing_splits_the_series_and_writes_nothing_itself(money):
    """The test that replaced "changes only the template", which tested exactly the
    bug: it required the template, i.e. January, to become 7,500."""
    _seed(money, id="תבנית", amount=7000.0, is_recurring=True)
    _seed(money, id="מופע", amount=7500.0, is_recurring=False, recurring_parent_id="תבנית")
    money.fake.rpc_results["split_recurring_series"] = "מופע"

    response = money.client.put("/api/recurring/תבנית/sync",
                                json={"instance_id": "מופע", "amount": "9999"})

    assert response.status_code == 200, response.get_json()
    assert money.fake.rpcs == [("split_recurring_series", {
        "p_template_id": "תבנית", "p_instance_id": "מופע", "p_family_id": _FAM})]
    assert money.fake.written("transactions") == [], "the route wrote by itself"
    rows = {r["id"]: r["amount"] for r in money.transactions}
    assert rows == {"תבנית": 7000.0, "מופע": 7500.0}


@pytest.mark.parametrize("body", [{}, {"instance_id": ""}, {"instance_id": 5}, {"amount": "7500"}])
def test_syncing_without_the_edited_month_is_refused(money, body):
    """Without the occurrence there is nowhere to split. An old tab still sending
    only `amount` gets a clear message, not a write."""
    _seed(money, id="תבנית", amount=7000.0, is_recurring=True)

    response = money.client.put("/api/recurring/תבנית/sync", json=body)

    assert response.status_code == 422
    assert money.fake.rpcs == []


def test_nothing_to_split_is_a_404(money):
    """`null` from the function: not found, not of the template, another family,
    or a series that ended."""
    money.fake.rpc_results["split_recurring_series"] = None

    response = money.client.put("/api/recurring/זרה/sync", json={"instance_id": "מופע"})

    assert response.status_code == 404


def test_a_failed_split_says_so(money):
    """Here this was swallowed until now: the browser swallowed the failure, and the
    user heard "updated"."""
    money.fake.rpc_results["split_recurring_series"] = RuntimeError("db down")

    response = money.client.put("/api/recurring/תבנית/sync", json={"instance_id": "מופע"})

    assert response.status_code == 500
    assert "נכשל" in response.get_json()["error"]


# ═══ The scan counter: the only route that costs real money ═════════════════
#
# `record_receipt_scan` appeared once in the whole suite, as a stub with no
# assert. Deleting the line that calls it cancelled both caps (the family's and
# the global one) and left the suite green. This file was written to guard the
# spending.

_SCAN = {"amount": 42.0, "merchant": "מכולת", "date": "2026-09-15",
         "category_name": "מכולת"}


@pytest.fixture
def scan(money, monkeypatch):
    monkeypatch.setattr(db, "receipt_scans_this_month", lambda fid: 0)
    monkeypatch.setattr(db, "receipt_scans_globally_this_month", lambda: 0)
    monkeypatch.setattr(db, "scan_receipt", lambda b, t, cats: (dict(_SCAN), None))
    monkeypatch.setattr(db, "upload_receipt", lambda tok, fid, b, t: ("path.jpg", None))
    return money


def _image(scan):
    import io
    return scan.client.post(
        "/api/receipts/scan",
        data={"image": (io.BytesIO(b"\xff\xd8\xff-fake-jpeg"), "receipt.jpg", "image/jpeg")},
        content_type="multipart/form-data",
    )


def test_a_successful_scan_is_counted(scan):
    """The heart: without this record the quota doesn't exist, and the API spending
    is wide open."""
    response = _image(scan)

    assert response.status_code == 200, response.get_json()
    counted = scan.fake.rows("receipt_scans")
    assert len(counted) == 1, "the scan wasn't counted: the quota doesn't actually exist"
    assert counted[0]["family_id"] == _FAM
    assert counted[0]["user_id"] == _ME


def test_a_failed_scan_is_not_counted(scan, monkeypatch):
    """Control: the family mustn't be charged quota for a scan that returned nothing."""
    monkeypatch.setattr(db, "scan_receipt", lambda b, t, cats: (None, "לא הצלחנו לקרוא"))

    _image(scan)

    assert scan.fake.rows("receipt_scans") == []


def test_a_scan_that_could_not_be_stored_is_still_counted(scan, monkeypatch):
    """The call to OpenAI was already made and paid for. An upload failure doesn't
    give the money back."""
    monkeypatch.setattr(db, "upload_receipt", lambda tok, fid, b, t: (None, "storage"))

    assert _image(scan).status_code == 200
    assert len(scan.fake.rows("receipt_scans")) == 1


def test_the_family_ceiling_blocks_the_scan_before_it_costs_anything(scan, monkeypatch):
    called = []
    monkeypatch.setattr(db, "receipt_scans_this_month", lambda fid: db.RECEIPT_MONTHLY_LIMIT)
    monkeypatch.setattr(db, "scan_receipt",
                        lambda b, t, cats: (called.append(1), (dict(_SCAN), None))[1])

    response = _image(scan)

    assert response.status_code == 429
    assert called == [], "the scan ran and was paid for even though the quota was used up"
    assert scan.fake.rows("receipt_scans") == []


def test_the_global_ceiling_blocks_everyone(scan, monkeypatch):
    """The global cap is the protection on the OpenAI account itself."""
    called = []
    monkeypatch.setattr(db, "receipt_scans_globally_this_month",
                        lambda: db.RECEIPT_GLOBAL_MONTHLY_LIMIT)
    monkeypatch.setattr(db, "scan_receipt",
                        lambda b, t, cats: (called.append(1), (dict(_SCAN), None))[1])

    response = _image(scan)

    assert response.status_code == 503
    assert called == []


def test_a_ceiling_that_cannot_be_read_refuses_rather_than_allows(scan, monkeypatch):
    """Allowing when the check failed effectively cancels the limit: that is a real
    expense."""
    called = []
    def _boom(fid):
        raise db.DataUnavailable("receipt_scans_this_month")
    monkeypatch.setattr(db, "receipt_scans_this_month", _boom)
    monkeypatch.setattr(db, "scan_receipt",
                        lambda b, t, cats: (called.append(1), (dict(_SCAN), None))[1])

    response = _image(scan)

    assert response.status_code == 503
    assert called == []


def test_a_file_that_is_not_an_image_never_reaches_the_scanner(scan, monkeypatch):
    import io
    called = []
    monkeypatch.setattr(db, "scan_receipt",
                        lambda b, t, cats: (called.append(1), (dict(_SCAN), None))[1])

    response = scan.client.post(
        "/api/receipts/scan",
        data={"image": (io.BytesIO(b"%PDF-1.4"), "x.pdf", "application/pdf")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 422
    assert called == []
    assert scan.fake.rows("receipt_scans") == []


# ═══ Deleting an account ═════════════════════════════════════════════════════

def test_deleting_the_account_requires_the_right_password(money, monkeypatch):
    """The action that can't be undone. A wrong password must stop before the RPC."""
    monkeypatch.setattr(db, "sign_in", lambda e, p: (None, "invalid"))

    response = money.client.delete("/api/account", json={"password": "לא נכון"})

    assert response.status_code == 403
    assert money.fake.rpcs == [], "the account was deleted with a wrong password"


def test_deleting_the_account_without_a_password_stops_first(money):
    response = money.client.delete("/api/account", json={})

    assert response.status_code == 422
    assert money.fake.rpcs == []


def test_deleting_the_account_calls_the_archiving_function_and_ends_the_session(money, monkeypatch):
    class _Session:
        access_token = "token"
    monkeypatch.setattr(db, "sign_in",
                        lambda e, p: (type("R", (), {"session": _Session})(), None))
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)

    response = money.client.delete("/api/account", json={"password": "pw"})

    assert response.status_code == 200
    assert [name for name, _ in money.fake.rpcs] == ["delete_my_account"]
    with money.client.session_transaction() as sess:
        assert "user_id" not in sess, "the session survived the account deletion"


# ═══ Frequency, target budget, and backfill ══════════════════════════════════

@pytest.mark.parametrize("freq", [None, "", "daily", "yearly", "MONTHLY_1"])
def test_a_recurring_transaction_needs_a_frequency_the_engine_knows(money, freq):
    """`NULL` passed the database's CHECK, so `{"is_recurring": true}` with no
    frequency created a live template the engine reads as `monthly_1` and
    produces rows from on the 1st of the month, while the picker in the UI shows
    empty."""
    body = {"amount": "50", "type": "expense", "date": "2026-09-15",
            "is_recurring": True}
    if freq is not None:
        body["recurring_frequency"] = freq

    response = money.client.post("/api/transactions", json=_with_category(body))

    assert response.status_code == 422, f"{freq!r} was accepted"
    assert money.transactions == []


def test_a_known_frequency_is_accepted(money):
    """Control."""
    response = money.post(amount="7000", type="expense", date="2026-09-15",
                          is_recurring=True, recurring_frequency="biweekly")

    assert response.status_code == 201
    assert money.only()["recurring_frequency"] == "biweekly"


def test_a_series_that_would_backfill_months_asks_first(money):
    """A typo in the year (`2016` instead of `2026`) created `~120` real transactions
    in one request, with no confirmation and no number in the response."""
    response = money.post(amount="6000", type="expense", date="2016-03-01",
                          is_recurring=True, recurring_frequency="monthly_1")

    assert response.status_code == 409
    body = response.get_json()
    assert body["needs_confirm"] is True
    assert body["will_create"] > 50, "the number wasn't said, or it is wrong"
    assert money.transactions == [], "saved before the question was asked"


def test_confirming_lets_it_through(money):
    """A deliberate backfill is legitimate use; it just has to be said out loud."""
    response = money.client.post("/api/transactions?confirm=1", json=_with_category({
        "amount": "6000", "type": "expense", "date": "2016-03-01",
        "is_recurring": True, "recurring_frequency": "monthly_1"}))

    assert response.status_code == 201


def test_a_short_backfill_is_not_interrupted(money):
    """"I forgot to enter the last two months" is ordinary use, and doesn't deserve
    a dialog."""
    response = money.post(amount="6000", type="expense", date="2026-08-01",
                          is_recurring=True, recurring_frequency="monthly_1")

    assert response.status_code == 201


@pytest.mark.parametrize("target", ["-5000", "0", "inf", "999999999", "nan"])
def test_a_bad_project_budget_is_refused(money, target):
    """A bare `float()` accepted a negative target and showed `-₪5,000` as the
    target; `1e12` overflowed `numeric(10,2)` and came back as an opaque 500."""
    response = money.client.post("/api/projects",
                                 json={"name": "שיפוץ", "budget_target": target})

    assert response.status_code == 422, f"{target} was accepted"


def test_a_project_without_a_budget_is_fine(money, monkeypatch):
    """An empty target is a valid value: not every project needs one."""
    monkeypatch.setattr(db, "add_project",
                        lambda *a, **k: ({"id": "p1", "name": "שיפוץ"}, None))

    response = money.client.post("/api/projects", json={"name": "שיפוץ"})

    assert response.status_code in (200, 201), response.get_json()


# ═══ Editing an old series: the warning counts only what will really be created ═
#
# "The series will create 6 transactions back" popped up on every edit of an old
# template (even fixing a typo in the description), because every occurrence
# since the start of the series was counted, including those that already exist.
# A frightening and false number, growing with the series' age.

@pytest.fixture
def mid_september(monkeypatch):
    import datetime as _dt
    from backend import clock
    monkeypatch.setattr(clock, "today", lambda: _dt.date(2026, 9, 16))


def _salary_series(money, start="2026-03-05"):
    """A salary on the 5th of the month from March; from April to September the
    occurrences were already created."""
    _seed(money, id="tpl", type="income", amount=14000.0, date=start,
          description="משכורת", is_recurring=True, recurring_frequency="monthly_same",
          recurring_skips=None)
    for m in range(4, 10):
        _seed(money, id=f"i{m}", type="income", amount=14000.0, date=f"2026-{m:02d}-05",
              description="משכורת", is_recurring=False, recurring_parent_id="tpl")


def _edit_template(money, **over):
    body = {"amount": "14000", "type": "income", "date": "2026-03-05",
            "description": "משכורת", "is_recurring": True,
            "recurring_frequency": "monthly_same"}
    body.update(over)
    return money.client.put("/api/transactions/tpl", json=_with_category(body))


def test_fixing_a_typo_in_an_old_series_does_not_warn(money, mid_september):
    _salary_series(money)

    response = _edit_template(money, description="משכורת — עבודה")

    assert response.status_code == 200, response.get_json()


def test_moving_payday_within_the_month_does_not_warn(money, mid_september):
    """From the 5th to the 10th: every month already got its salary, and nothing
    will be created."""
    _salary_series(money)

    response = _edit_template(money, date="2026-03-10")

    assert response.status_code == 200, response.get_json()


def test_moving_the_start_a_year_back_warns_with_the_real_number(money, mid_september):
    """From 3.2025: April 2025 to September 2026 is 18 months, six of which already
    exist. 12 will be created, and that is the number to say, not 18."""
    _salary_series(money)

    response = _edit_template(money, date="2025-03-05")

    assert response.status_code == 409
    assert response.get_json()["will_create"] == 12


def test_a_failed_read_does_not_skip_the_question(money, mid_september, monkeypatch):
    """Unknown how many will be created → don't save silently."""
    _salary_series(money)
    def broken(*a):
        raise db.DataUnavailable("down")
    monkeypatch.setattr(db, "existing_occurrence_dates", broken, raising=False)

    response = _edit_template(money, date="2025-03-05")

    assert response.status_code == 503


# ═══ An edit doesn't change what wasn't touched ═════════════════════════════
#
# The owner and project checks were meant for a **new** choice, and also rejected
# a value that hadn't changed. The form "solved" that by silently replacing it: a
# transaction of someone who moved to another family became "shared", and a
# transaction in a project that stopped tracking the type left it for the
# household.

_GONE = "44444444-4444-4444-4444-444444444444"      # moved to another family


def test_editing_keeps_an_owner_who_is_no_longer_in_the_family(money, monkeypatch):
    monkeypatch.setattr(db, "get_family_settings",
                        lambda fid: {"owner_attribution": {"expense": True}})
    _seed(money, id="tx-1", user_id=_GONE)

    response = money.put("tx-1", amount="250", type="expense", date="2026-09-10", owner=_GONE)

    assert response.status_code == 200, response.get_json()
    assert money.only()["user_id"] == _GONE
    assert money.only()["amount"] == 250.0


def test_but_hanging_a_transaction_on_an_outsider_is_still_refused(money, monkeypatch):
    """Control: "didn't change" isn't a loophole: a new owner from outside is still
    rejected."""
    monkeypatch.setattr(db, "get_family_settings",
                        lambda fid: {"owner_attribution": {"expense": True}})
    _seed(money, id="tx-1", user_id=_ME)

    response = money.put("tx-1", amount="250", type="expense", date="2026-09-10", owner=_GONE)

    assert response.status_code == 422
    assert money.only()["user_id"] == _ME


def _project(track_income):
    return lambda pid, fid: {"id": pid, "owner_id": None, "track_expense": True,
                             "track_income": track_income, "track_savings": False}


def test_editing_keeps_a_project_that_stopped_tracking_the_type(money, monkeypatch):
    monkeypatch.setattr(db, "get_project_for_transaction", _project(track_income=False))
    _seed(money, id="tx-1", type="income", project_id="p1", category_id=None)

    response = money.put("tx-1", amount="900", type="income", date="2026-09-10", project_id="p1")

    assert response.status_code == 200, response.get_json()
    assert money.only()["project_id"] == "p1"


def test_but_adding_to_such_a_project_is_still_refused(money, monkeypatch):
    """Control: "tracks type" still decides what can be added."""
    monkeypatch.setattr(db, "get_project_for_transaction", _project(track_income=False))
    _seed(money, id="tx-1", type="income", project_id=None)

    response = money.put("tx-1", amount="900", type="income", date="2026-09-10", project_id="p1")

    assert response.status_code == 422
    assert money.only()["project_id"] is None
