"""
Tests for when the recurring transactions engine runs.

It ran only from the dashboard. Whoever came straight to the month page (from a
bookmark, the bottom menu or a link) saw a month **without the salary and without
the standing orders**, with nothing to explain why. The numbers were fixed only if
they happened to pass through the home page.

And a second bug in the same place: the "synced for today" mark was written even
when creation failed, so the next attempt was only the next day. A failure that
looks like success leaves a whole month missing.
"""
import pytest

from backend import app as app_module
from backend.app import app

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    # the rest of the page isn't of interest here: only when the sync is called
    from backend import supabase_config as _db
    for fn, val in [
        ("get_family_settings", dict(_db.DEFAULT_FAMILY_SETTINGS)),
        ("get_monthly_summary", _db._empty_summary()),
        ("get_categories", []), ("get_family_members", []),
        ("family_has_no_transactions", False), ("get_recent_transactions", []),
        ("get_months_archive", []),
        ("fetch_month_rows", []), ("get_recurring_transactions", []),
        # The month page moved to one trip (get_month_page); the other pages still
        # call the individual fetchers above, so both are faked here.
        ("fetch_month_page", {"family": {}, "settings": dict(_db.DEFAULT_FAMILY_SETTINGS),
                              "members": [], "categories": [], "rows": [], "archive": []}),
        ("get_month_transactions", []),
    ]:
        # Without raising=True (the default) a renamed name is silently swallowed.
        # Three names in this list no longer existed: the fake did nothing, nobody
        # knew, and the test stayed green over a broken pattern.
        monkeypatch.setattr(app_module.db, fn, lambda *a, _v=val, **k: _v)
    monkeypatch.setattr(app_module, "family_settings",
                        lambda: dict(_db.DEFAULT_FAMILY_SETTINGS))
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]   = "22222222-2222-2222-2222-222222222222"
            sess["family_id"] = _FAM
        yield c


# ─── Which pages it runs from ────────────────────────────────────────────────

@pytest.mark.parametrize("path,why", [
    ("/",       "הדשבורד — היה היחיד"),
    ("/month",  "נפתח ישירות מסימנייה או מהתפריט, ומציג את כל מספרי החודש"),
    ("/months", "משווה חודשים — חודש חסר מעוות את ההשוואה"),
])
def test_opening_a_page_with_monthly_figures_fills_in_the_recurring_ones(
        client, monkeypatch, path, why):
    calls = []
    monkeypatch.setattr(app_module.db, "materialize_recurring",
                        lambda fid: (calls.append(fid) or (0, True)))

    client.get(path)

    assert calls == [_FAM], f"{path} doesn't sync: {why}"


def test_it_runs_once_a_day_and_not_on_every_page_load(client, monkeypatch):
    """Control: this is a write to the database, not a fetch. Running on every load
    would double the cost of every page."""
    calls = []
    monkeypatch.setattr(app_module.db, "materialize_recurring",
                        lambda fid: (calls.append(fid) or (0, True)))

    client.get("/")
    client.get("/month")
    client.get("/months")

    assert len(calls) == 1, f"ran {len(calls)} times on the same day"


# ─── A failure doesn't mark success ──────────────────────────────────────────

def test_a_failed_run_is_retried_on_the_next_page_load(client, monkeypatch):
    """The heart of the second bug: marking "synced" after a failure pushed the next
    attempt to tomorrow, and left a month without the salary until then."""
    calls = []
    monkeypatch.setattr(app_module.db, "materialize_recurring",
                        lambda fid: (calls.append(fid) or (0, False)))

    client.get("/")
    client.get("/month")

    assert len(calls) == 2, "a failure was marked as success: it won't be retried until tomorrow"

    with client.session_transaction() as sess:
        assert "recurring_synced" not in sess


def test_a_successful_run_is_marked(client, monkeypatch):
    """Control: without the mark this would run on every page load."""
    monkeypatch.setattr(app_module.db, "materialize_recurring", lambda fid: (3, True))

    client.get("/")

    with client.session_transaction() as sess:
        assert sess.get("recurring_synced")


def test_a_family_less_account_is_skipped(client, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module.db, "materialize_recurring",
                        lambda fid: (calls.append(fid) or (0, True)))
    with client.session_transaction() as sess:
        sess["family_id"] = None

    client.get("/")

    assert calls == []
