"""
Tests for removing a recurring transaction.

The dialog in settings promises: "מופעים חדשים יפסיקו להיווצר. מופעים שכבר נוצרו
יישארו." In practice the button ran a full delete of the template row, and the
template row is a real transaction in every respect: it is the first occurrence
in the series, and it is counted in the monthly summary (get_monthly_summary
doesn't filter is_recurring). That is, the first month's rent was silently deleted
from history, in complete contradiction to the promise the user had just read.

And in addition: the delete detached all the occurrences from the template (on
delete set null), so the dedup mechanism stopped seeing them. Whoever recreated the
same recurring transaction got all the months a second time: double rent over ten
months.
"""
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_TX = "11111111-1111-1111-1111-111111111111"
_FAM = "22222222-2222-2222-2222-222222222222"


class _FakeClient:
    """Records what was done (update or delete) and which fields were sent."""

    def __init__(self):
        self.updated = None
        self.deleted = False

    def table(self, _n):            return self
    def update(self, payload, **k): self.updated = payload; return self
    def delete(self, **k):          self.deleted = True; return self
    def eq(self, *a, **k):          return self
    def execute(self):              return self

    @property
    def data(self):                 return [{"id": _TX}]


@pytest.fixture
def client(monkeypatch):
    # the transaction isn't in someone else's personal project (see tx_visible_required)
    monkeypatch.setattr(app_module.db, "personal_project_owner", lambda *a: None)
    # editing checks the current attributions: here a normal family transaction
    monkeypatch.setattr(app_module.db, "transaction_links",
                        lambda *a: {"user_id": None, "project_id": None})
    app.config["TESTING"] = True
    fake = _FakeClient()
    monkeypatch.setattr(db, "get_client", lambda: fake)
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]   = "33333333-3333-3333-3333-333333333333"
            sess["family_id"] = _FAM
        yield c, fake


# ─── The dialog's promise ────────────────────────────────────────────────────

def test_removing_a_recurring_series_does_not_delete_the_transaction(client):
    """The heart: the template row is money that really moved, and it mustn't vanish."""
    c, fake = client

    response = c.delete(f"/api/recurring/{_TX}")

    assert response.status_code == 200
    assert fake.deleted is False, "the transaction was deleted: real money was taken out of history"
    assert fake.updated is not None


def test_removing_a_recurring_series_does_stop_future_occurrences(client):
    """Control: if we didn't turn the flag off, we did nothing."""
    c, fake = client

    c.delete(f"/api/recurring/{_TX}")

    assert fake.updated["is_recurring"] is False
    assert fake.updated["recurring_frequency"] is None


def test_the_dialog_no_longer_promises_something_the_code_does_not_do():
    """The message and the code must agree; they didn't, and that was the whole bug."""
    # The handler moved from settings.js to transactions.js when the same list was
    # added to the month page. The test looks for it wherever it is: pinning the
    # file name is what broke it, not any change in the behaviour it really
    # guards.
    owners = [f for f in (_ROOT / "frontend/static/js").glob("*.js")
              if "להסיר את העסקה הקבועה?" in f.read_text(encoding="utf-8")]
    assert len(owners) == 1, f"the dialog appears in {[f.name for f in owners]}: a duplicate copy or gone"

    js = owners[0].read_text(encoding="utf-8")
    block = js[js.index("להסיר את העסקה הקבועה?"):][:1200]

    assert "/api/recurring/" in block, "still calls the route that deletes the row"
    assert "/api/transactions/" not in block


# ─── Isolation between families ──────────────────────────────────────────────

def test_a_missing_or_foreign_series_is_refused(client, monkeypatch):
    """The route filters by family_id; a row not found must return 404 and not
    'success' on nothing."""
    c, fake = client
    monkeypatch.setattr(type(fake), "data", property(lambda self: []))

    response = c.delete(f"/api/recurring/{_TX}")

    assert response.status_code == 404


def test_the_query_is_scoped_to_the_callers_family():
    """A direct call to the DB layer: the family_id filter must be there."""
    import inspect
    src = inspect.getsource(db.stop_recurring)

    assert '.eq("family_id", family_id)' in src
