"""Ten fetchers turned "the query failed" into "you have no data".

This is the pattern the docstring at the top of supabase_config.py says was
eliminated, and it was applied to the summary fetchers only. What remained:

- get_months_archive fails → the month strip shrinks to the current month and the
  comparison page renders empty. A family with two years of history sees
  "no history".
- get_recurring_transactions fails → summarise_recurring([]) → the whole panel is
  hidden. ₪11,000 of recurring expenses vanish from the screen.
- _fetch_family_members fails → _resolve_owner rejects **every** owner with
  "בן המשפחה שנבחר אינו במשפחה שלך". The error blames the user.

A fake zero looks exactly like a real zero, and the whole app is numbers.
"""
import pytest

from backend import supabase_config as db

pytestmark = pytest.mark.unit

_READERS = [
    ("get_recent_transactions",  ("fam",)),
    ("get_month_transactions",   ("fam", 2026, 9)),
    ("get_recurring_transactions", ("fam", "viewer")),
    ("get_projects",             ("fam", "viewer")),
    ("get_months_archive",       ("fam",)),
    ("get_project_categories",   ("proj", "fam")),
    ("_fetch_family_members",    ("fam",)),
]


class _Exploding:
    """Every query fails: the state the fetchers swallowed."""

    def table(self, *a, **k):        return self
    def select(self, *a, **k):       return self
    def eq(self, *a, **k):           return self
    def gte(self, *a, **k):          return self
    def lte(self, *a, **k):          return self
    def order(self, *a, **k):        return self
    def limit(self, *a, **k):        return self
    def single(self, *a, **k):       return self
    def maybe_single(self, *a, **k): return self
    def or_(self, *a, **k):          return self
    def rpc(self, *a, **k):          return self
    def execute(self):
        raise RuntimeError("the database is unavailable")


@pytest.fixture
def broken(monkeypatch):
    monkeypatch.setattr(db, "get_client", lambda: _Exploding())


@pytest.mark.parametrize("name,args", _READERS, ids=[r[0] for r in _READERS])
def test_a_failed_read_is_raised_and_not_returned_as_empty(broken, name, args):
    """The heart. An empty list is an answer about reality, and a failed query isn't
    one."""
    fn = getattr(db, name)

    with pytest.raises(db.DataUnavailable):
        fn(*args)


def test_the_app_turns_that_into_a_page_that_says_so():
    """Without a dedicated handler this is a generic 500: right in direction, but it
    doesn't tell the user it's worth simply trying again.

    Tested on the handler itself and not through a request: app.config["TESTING"]
    leaks between tests, and with the flag on Flask raises the exception instead
    of running handlers, so the test would measure the flag and not the
    behaviour."""
    from backend.app import app, data_unavailable

    with app.test_request_context("/month"):
        body, status = data_unavailable(db.DataUnavailable("בדיקה"))

    assert status == 503, "a fetch failure must be 503, not 200 and not 500"
    assert "לא הצלחנו לטעון" in body


def test_an_api_caller_gets_json_and_not_an_html_page():
    """The browser asks for JSON, and an HTML page in reply reaches it as a "network
    error", meaning it will retry again and again a request that won't succeed."""
    from backend.app import app, data_unavailable

    with app.test_request_context("/api/transactions",
                                  headers={"Accept": "application/json"}):
        response, status = data_unavailable(db.DataUnavailable("בדיקה"))

    assert status == 503
    assert response.is_json
    assert "לא הצלחנו לטעון" in response.get_json()["error"]


def test_the_failure_is_logged_at_a_level_that_reaches_sentry(caplog):
    """A warning is a breadcrumb, not an event. A fetch failure in production must be
    heard."""
    import logging
    from backend.app import app, data_unavailable

    with app.test_request_context("/month"), caplog.at_level(logging.ERROR):
        data_unavailable(db.DataUnavailable("בדיקה"))

    assert any(r.levelno >= logging.ERROR for r in caplog.records)
