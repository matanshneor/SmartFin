"""
Tests for the run configuration.

The server ran on two synchronous workers, i.e. two requests in parallel at most.
Receipt scanning blocks a worker for the whole call to OpenAI (2 to 6 seconds),
so two simultaneous scans take the site down for every other user. Not slow:
down.

The second test here matters more than the first. The obvious extension is
`--threads` or gevent, and it would cause data to leak between families: the
Supabase client is a process-level singleton, and set_auth_token changes its auth
header globally on every request. With a synchronous worker this is safe because
it handles one request at a time; with threads, one user's request runs with
another's token.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_PROCFILE = (_ROOT / "Procfile").read_text(encoding="utf-8")
_WEB = next(l for l in _PROCFILE.split("\n") if l.startswith("web:"))


def test_there_is_room_for_more_than_two_concurrent_requests():
    m = re.search(r"--workers\s+(\d+)", _WEB)

    assert m, "no explicit --workers"
    assert int(m.group(1)) >= 4, f"{m.group(1)} workers: two receipt scans take the site down"


def test_the_workers_are_processes_and_not_threads():
    """The central test. This extension looks obvious and would make one family
    see another's finances."""
    assert "--threads" not in _WEB, (
        "threads on a shared Supabase client = one user's request runs with another's token"
    )
    for klass in ("gevent", "eventlet", "gthread", "tornado"):
        assert klass not in _WEB, f"an asynchronous worker ({klass}): the same problem"


def test_the_reason_is_written_down_where_someone_would_change_it():
    """A note in another file won't be read by whoever comes to increase throughput."""
    assert "--threads" in _PROCFILE and "gevent" in _PROCFILE, "the warning isn't written"
    assert "supabase" in _PROCFILE.lower()
    assert "flask.g" in _PROCFILE, "it isn't said what has to happen for threads to become safe"


def test_the_app_is_loaded_once_and_shared():
    """Without preload every worker loads the app again (~60MB each), and four
    would cost almost double two."""
    assert "--preload" in _WEB


def test_the_singleton_that_makes_threads_unsafe_still_exists():
    """If the client ever becomes per-request, the warning is void, and this test
    will fail and remind you to update it, instead of leaving a wrong warning
    forever."""
    db = (_ROOT / "backend/supabase_config.py").read_text(encoding="utf-8")

    assert re.search(r"^_client\s*=\s*None", db, re.M), (
        "the client is no longer a singleton: threads can be considered, and the note in the Procfile updated"
    )
    assert "def set_auth_token" in db
