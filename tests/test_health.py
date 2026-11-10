"""
The health check said "ok" even when the database was dead.

It returned a fixed string without checking anything, so an external monitor
would report all clear exactly while no family could see its money. The only way
to find out was for someone to bother telling us.

Now there are two: `/health` answers "is the process alive?" (that is Railway's
check, and it mustn't fail because of the database: it would block a deploy at
exactly the worst moment), and `/health/db` answers "can the app really be used?".
"""
import pytest

from backend import app as app_module
from backend.app import app

pytestmark = pytest.mark.unit


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    # The five-second memory is shared by every test in the process: reset it
    monkeypatch.setitem(app_module._health_cache, "at", -app_module._HEALTH_TTL)
    with app.test_client() as c:
        yield c


def _db_says(monkeypatch, ok, detail):
    monkeypatch.setattr(app_module.db, "ping", lambda *a, **k: (ok, detail))


# ─── When the database answers ──────────────────────────────────────────────

def test_both_endpoints_are_green_when_the_database_answers(client, monkeypatch):
    _db_says(monkeypatch, True, "ok")

    for path in ("/health", "/health/db"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.get_json()["database"] == "ok", path


# ─── When the database doesn't answer ───────────────────────────────────────

def test_the_monitor_endpoint_fails_when_the_database_is_down(client, monkeypatch):
    """The heart of the fix. Without this there is no way to know remotely that the
    app is broken."""
    _db_says(monkeypatch, False, "ConnectTimeout")

    r = client.get("/health/db")
    assert r.status_code == 503
    assert r.get_json() == {"status": "degraded", "database": "ConnectTimeout"}


def test_railways_probe_stays_green_so_a_database_blip_cannot_block_a_deploy(
        client, monkeypatch):
    """Control: if this one also failed, a two-minute Supabase outage would block
    the deploy that might be coming to fix it."""
    _db_says(monkeypatch, False, "ConnectTimeout")

    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["database"] == "ConnectTimeout", "answers 200 but doesn't hide it"


# ─── The short memory ───────────────────────────────────────────────────────

def test_a_burst_of_checks_hits_the_database_once(client, monkeypatch):
    """Four workers in total. Without memory, exactly during an outage (when checks
    multiply and the server is loaded) every check would occupy one of them."""
    calls = []
    monkeypatch.setattr(app_module.db, "ping",
                        lambda *a, **k: (calls.append(1) or (True, "ok")))

    for _ in range(10):
        client.get("/health")
        client.get("/health/db")

    assert len(calls) == 1, f"asked {len(calls)} times instead of once"


def test_the_memory_expires(client, monkeypatch):
    """Control: a memory that doesn't expire would freeze the first answer forever."""
    state = {"ok": True}
    monkeypatch.setattr(app_module.db, "ping",
                        lambda *a, **k: (state["ok"], "ok" if state["ok"] else "down"))

    assert client.get("/health/db").status_code == 200

    state["ok"] = False
    assert client.get("/health/db").status_code == 200, "still inside the window"

    # age the record instead of actually waiting
    app_module._health_cache["at"] -= app_module._HEALTH_TTL
    assert client.get("/health/db").status_code == 503


# ─── The check itself ───────────────────────────────────────────────────────

def test_ping_does_not_use_the_shared_client(monkeypatch):
    """The shared client carries the previous request's token. An unauthenticated
    check through it would fail the moment it expired, and that looks exactly like
    "the database is down"."""
    from backend import supabase_config as _db
    monkeypatch.setattr(_db, "get_client",
                        lambda: pytest.fail("ping used the shared client"))
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    ok, detail = _db.ping()
    assert (ok, detail) == (False, "not configured")


def test_a_network_failure_is_reported_and_not_raised(monkeypatch):
    """A health check that crashes is exactly what it is meant to prevent."""
    from backend import supabase_config as _db
    import httpx
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "k")
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectTimeout("x")))

    assert _db.ping() == (False, "ConnectTimeout")


def test_an_error_status_is_not_mistaken_for_health(monkeypatch):
    from backend import supabase_config as _db
    import httpx
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_KEY", "k")
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **k: httpx.Response(503, request=httpx.Request("POST", "https://x")))

    assert _db.ping() == (False, "http 503")
