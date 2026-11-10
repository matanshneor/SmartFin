"""The guard at the entrance (Matan, 10.11.2026: security check, item 3).

anon (whoever isn't signed in) has no privilege at all on any table. Until now it
got Supabase's default: everything, including deleting and emptying a whole table,
and all the protection relied on RLS alone. RLS still locks every row; this is a
second layer, so one RLS rule written wrong in the future won't be enough to
expose data.

They run against the real database (skipped in CI, like all database tests).
"""
import os

import httpx
import pytest

from backend import supabase_config as db

_TABLES = ["admin_check_log", "categories", "families", "login_events", "owner_archive",
           "profiles", "project_categories", "projects", "push_log", "push_subscriptions",
           "receipt_scans", "transactions", "web_sessions"]


@pytest.fixture
def anon_rest():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not url or not key:
        pytest.skip("No SUPABASE_URL: the tests against the real database run locally only")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=url.rstrip("/") + "/rest/v1/", headers=headers,
                      timeout=10) as c:
        yield c


# An existing column to filter on, so that even if there were a privilege, an
# update/delete wouldn't touch any row. Without an existing filter, PostgREST fails
# on the column before it checks the privilege.
_KEY = {"push_log": "kind", "push_subscriptions": "endpoint", "web_sessions": "sid_hash"}


@pytest.mark.parametrize("table", _TABLES)
def test_anon_cannot_touch_any_table(anon_rest, table):
    nothing = {_KEY.get(table, "id"): "is.null"}
    for method, kw in (("GET", {"params": {"select": "*", "limit": "1"}}),
                       ("POST", {"json": {}}),
                       ("PATCH", {"params": nothing, "json": {}}),
                       ("DELETE", {"params": nothing})):
        r = anon_rest.request(method, table, **kw)
        assert r.status_code in (401, 403), (table, method, r.status_code, r.text[:200])
        assert r.json().get("code") == "42501", (table, method, r.text[:200])


def test_the_list_covers_every_table():
    """A new table not in the list wouldn't be checked. _TABLES against the database
    itself."""
    if not os.environ.get("SUPABASE_URL"):
        pytest.skip("No SUPABASE_URL")
    import json
    import subprocess
    out = subprocess.run(
        ["supabase", "db", "query",
         "select tablename from pg_tables where schemaname='public' order by 1", "--linked"],
        cwd=os.path.join(os.path.dirname(__file__), "..", "backend"),
        capture_output=True, text=True, timeout=60).stdout
    rows = json.loads(out[out.index("{"):out.rindex("}") + 1])["rows"]
    assert sorted(r["tablename"] for r in rows) == sorted(_TABLES)


def test_the_health_check_still_reaches_the_database(anon_rest):
    """The health check runs as anon. After every table was closed it goes through a
    function."""
    assert db.ping() == (True, "ok")


def test_the_warm_ping_is_accepted_even_with_a_stale_token(anon_rest, monkeypatch):
    """Item 4: the touch that keeps the connection warm was rejected about 3,800 times
    a day, because it carried the expired token of the last user. Now, even with
    such a token on the client, the response is 200, and the logs are clean for
    real rejections."""
    client = db.get_client()
    session = client.postgrest.session
    statuses = []
    real_post = session.post

    def spy(*a, **k):
        r = real_post(*a, **k)
        statuses.append(r.status_code)
        return r
    monkeypatch.setattr(session, "post", spy)
    monkeypatch.setitem(db._warm_state, "busy", False)
    monkeypatch.setitem(db._warm_state, "last", 0.0)
    db.set_auth_token("eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjF9.stale")
    try:
        assert db._warm_ping_once(client) is True
    finally:
        db.set_auth_token("")
    assert statuses == [200]
