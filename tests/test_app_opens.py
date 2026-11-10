"""App opens (Matan, 10.11.2026).

Every time a signed-in user opens the app or comes back to it, one row in
`app_opens`. The browser calls `/api/app-open` in the background, the server
calls `log_app_open()` with the user's token, and the database records the caller,
skipping a repeat within 5 minutes and deleting what is older than 90 days.

Unit: the route, and the browser code run in node. Against the real database:
the function records the caller once, anon can't call it, and nobody reads the
table directly.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter

_ROOT = Path(__file__).resolve().parent.parent
_CORE = (_ROOT / "frontend/static/js/core.js").read_text(encoding="utf-8")


# ─── The route ───────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app.config["TESTING"] = True
    limiter.reset()
    yield app.test_client()
    limiter.reset()


@pytest.mark.unit
def test_a_signed_in_open_is_recorded(client, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module.db, "log_app_open", lambda: calls.append(1) or True)
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda t: None)
    with client.session_transaction() as s:
        s["user_id"], s["access_token"] = "u1", "tok"
    res = client.post("/api/app-open", headers={"Accept": "application/json"})
    assert res.status_code == 204
    assert calls == [1]


@pytest.mark.unit
def test_without_a_session_nothing_is_recorded(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "log_app_open",
                        lambda: pytest.fail("recorded without a user"))
    res = client.post("/api/app-open", headers={"Accept": "application/json"})
    assert res.status_code == 401


@pytest.mark.unit
def test_a_database_failure_is_not_the_users_problem(client, monkeypatch):
    """The page shows nothing either way, so the answer doesn't change either."""
    monkeypatch.setattr(app_module.db, "log_app_open", lambda: False)
    monkeypatch.setattr(app_module.db, "set_auth_token", lambda t: None)
    with client.session_transaction() as s:
        s["user_id"], s["access_token"] = "u1", "tok"
    assert client.post("/api/app-open", headers={"Accept": "application/json"}).status_code == 204


@pytest.mark.unit
def test_the_db_call_swallows_errors(monkeypatch):
    class _Broken:
        def rpc(self, *a, **k):
            raise RuntimeError("down")
    monkeypatch.setattr(db, "get_client", lambda: _Broken())
    assert db.log_app_open() is False


# ─── The browser ─────────────────────────────────────────────────────────────

def _block():
    start = _CORE.index("// ── App opens")
    return _CORE[start:]


@pytest.mark.unit
@pytest.mark.skipif(not shutil.which("node"), reason="node isn't installed")
def test_called_on_load_and_on_coming_back_but_not_on_leaving():
    """On load: one call. Going to another app (hidden): none. Coming back
    (visible): one more. A failed call: console.warn only, never a toast."""
    probe = """
var calls = 0, warned = 0, listeners = {};
var document = { visibilityState: 'visible',
  addEventListener: function (t, f) { listeners[t] = f; } };
var console = { warn: function () { warned++; }, log: function () {} };
var window = { showToast: function () { throw new Error('toast shown'); } };
function fetch(url, opts) {
  if (url !== '/api/app-open' || opts.method !== 'POST') throw new Error('wrong call');
  calls++;
  return calls === 2 ? Promise.reject(new Error('offline'))
                     : Promise.resolve({ ok: true, status: 204 });
}
""" + _block() + """
var afterLoad = calls;
document.visibilityState = 'hidden'; listeners.visibilitychange();
var afterHide = calls;
document.visibilityState = 'visible'; listeners.visibilitychange();
setTimeout(function () {
  process.stdout.write(JSON.stringify([afterLoad, afterHide, calls, warned]));
}, 0);
"""
    out = subprocess.run(["node", "-e", probe], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-600:]
    assert json.loads(out.stdout) == [1, 1, 2, 1]


@pytest.mark.unit
def test_nothing_waits_for_it():
    """Fire and forget: no await, nothing chained after it that touches the page."""
    block = _block()
    assert "await" not in block and "showToast" not in block


# ─── Against the real database ───────────────────────────────────────────────

def _count(user_id):
    from tests._test_accounts import privileged_sql
    out = privileged_sql(
        f"select count(*) as n from public.app_opens where user_id = '{user_id}'")
    rows = json.loads(out[out.index("{"):])["rows"]
    return int(rows[0]["n"])


def test_the_function_records_the_caller_once_per_five_minutes(family_a):
    from tests._test_accounts import privileged_sql
    privileged_sql(f"delete from public.app_opens where user_id = '{family_a['user_id']}'")
    db.set_auth_token(family_a["token"])
    assert db.log_app_open() is True
    assert db.log_app_open() is True
    assert _count(family_a["user_id"]) == 1, "a repeat within 5 minutes was recorded"


def test_anon_cannot_call_it(family_a):
    from postgrest.exceptions import APIError
    try:
        db.set_auth_token("")
        with pytest.raises(APIError):
            db.get_client().rpc("log_app_open", {}).execute()
    finally:
        db.set_auth_token(family_a["token"])


def test_a_user_cannot_read_the_table(family_a):
    """Not even their own rows: only the owner looks at this, through the database."""
    from postgrest.exceptions import APIError
    db.set_auth_token(family_a["token"])
    with pytest.raises(APIError):
        db.get_client().table("app_opens").select("*").limit(1).execute()
