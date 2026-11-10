"""Small fixes from the logs (Matan, 13.10)."""
import json
import re

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"


@pytest.mark.parametrize("path", ["/favicon.ico", "/apple-touch-icon.png",
                                  "/apple-touch-icon-precomposed.png"])
def test_the_icon_is_where_whatsapp_and_the_iphone_look_for_it(path):
    """Without them a link to the app appeared without the logo (404 in the logs)."""
    res = app.test_client().get(path)

    assert res.status_code == 200
    assert res.mimetype == "image/png"


def test_robots_closes_only_the_api_and_the_receipts():
    body = app.test_client().get("/robots.txt").get_data(as_text=True)

    assert "Disallow: /api/" in body and "Disallow: /receipts/" in body
    # the landing page and invite links need a share preview
    assert "Disallow: /\n" not in body


def _page_data(html):
    raw = re.search(r'<script type="application/json" id="sf-page-data">(.*?)</script>', html, re.S)
    return json.loads(raw.group(1))


def test_the_add_window_lists_come_with_the_page(monkeypatch):
    """Without two extra requests on every page: what the page already fetched."""
    app.config["TESTING"] = True
    monkeypatch.setattr(db, "set_auth_token", lambda t: None)
    monkeypatch.setattr(db, "materialize_recurring", lambda fid: (0, True))
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    cats = [{"id": "c1", "name": "סופר", "type": "expense", "icon": "🛒"}]
    monkeypatch.setattr(db, "fetch_home_page", lambda *a, **k: {
        "family": {"id": _FAM}, "settings": dict(db.DEFAULT_FAMILY_SETTINGS),
        "members": [{"id": _ME, "name": "מתן", "full_name": "מתן שניאור", "email": "secret@x.com"}],
        "categories": cats, "rows": [], "summary": db.summary_from_rows([]), "recent": [],
        "week": None, "is_new": False})
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        data = _page_data(c.get("/").get_data(as_text=True))

    assert data["lists"]["categories"] == cats
    # like /api/family/members: id and name only, not email
    assert data["lists"]["members"] == [{"id": _ME, "name": "מתן"}]


def test_nothing_is_fetched_just_for_the_lists(monkeypatch):
    """A page that didn't fetch categories: don't fetch for it; the window will ask as
    usual."""
    with app.test_request_context("/"):
        from flask import session
        session["family_id"] = _FAM
        monkeypatch.setattr(db, "_fetch_categories", lambda *a: pytest.fail("fetch"))
        lists = app_module._client_lists()["client_lists"]()
    assert lists == {}


def test_the_js_takes_them_from_the_page():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
    assert "if (Array.isArray(pageLists.categories)) categoriesCache = pageLists.categories;" in js
    assert "if (Array.isArray(pageLists.members)) membersCache = pageLists.members;" in js


# ─── A warm connection after a break ─────────────────────────────────────────

class _Session:
    def __init__(self):
        self.calls = []

    def post(self, path, **kw):
        self.calls.append((path, kw.get("headers")))


class _Client:
    def __init__(self):
        self.postgrest = type("P", (), {})()
        self.postgrest.session = _Session()


@pytest.fixture
def warm(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(db.time, "monotonic", lambda: clock["t"])
    monkeypatch.setitem(db._warm_state, "busy", False)
    monkeypatch.setitem(db._warm_state, "last", 1000.0)
    return clock


def test_a_quiet_worker_pings_and_a_busy_one_does_not(warm, monkeypatch):
    monkeypatch.setenv("SUPABASE_KEY", "anon-key")
    c = _Client()
    assert db._warm_ping_once(c) is False, "there was activity just now"
    warm["t"] += db.WARM_IDLE_SECONDS + 1
    db.request_started()
    assert db._warm_ping_once(c) is False, "in the middle of a request: not allowed"
    db.request_finished()
    warm["t"] += db.WARM_IDLE_SECONDS + 1
    assert db._warm_ping_once(c) is True
    # a small function, not the API root (the whole schema description). And with
    # the anon key, not with the token left on the client: it expired, and every
    # touch was rejected (security check 10.11)
    assert c.postgrest.session.calls == [("rpc/get_my_family_id",
                                          {"Authorization": "Bearer anon-key"})]


def test_a_failed_ping_is_harmless(warm):
    c = _Client()
    c.postgrest.session.post = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    warm["t"] += db.WARM_IDLE_SECONDS + 1
    assert db._warm_ping_once(c) is True


def test_every_request_marks_the_worker_busy():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "backend/app.py").read_text(encoding="utf-8")
    first_hook = src.index("@app.before_request")
    assert src[first_hook:first_hook + 80].count("_db_request_started") == 1, \
        "must be the first before_request, before any fetch"
    assert "@app.teardown_request\ndef _db_request_finished" in src


# ─── The email in settings: at most once every 10 minutes ────────────────────

def test_the_settings_email_check_is_cached_unless_a_change_is_pending(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "get_auth_email", lambda t: (calls.append(1) or ("a@x.com", None)))
    with app.test_request_context("/settings"):
        from flask import session
        session["user_email"] = "a@x.com"
        assert app_module._current_email(cached=True) == ("a@x.com", None)
        assert app_module._current_email(cached=True) == ("a@x.com", None)
        assert len(calls) == 1, "a second check within 10 minutes: from the session"
        session["pending_email"] = "b@x.com"
        app_module._current_email(cached=True)
        assert len(calls) == 2, "email pending: check every time, so the confirmation appears immediately"
        assert "pending_email" not in session
        app_module._current_email()
        assert len(calls) == 3, "actions that check a password: always fresh"


def test_a_gzipped_static_file_has_the_same_weak_etag_every_time():
    """Code review (14.10): from memory it came back with a "strong" ETag of the
    uncompressed file."""
    c = app.test_client()
    first = c.get("/static/css/style.css", headers={"Accept-Encoding": "gzip"})
    second = c.get("/static/css/style.css", headers={"Accept-Encoding": "gzip"})
    for res in (first, second):
        assert res.headers["Content-Encoding"] == "gzip"
        assert res.headers["ETag"].startswith("W/")
        assert "Accept-Ranges" not in res.headers
    assert first.headers["ETag"] == second.headers["ETag"]


def test_the_warm_ping_gives_up_before_a_request_would():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "backend/supabase_config.py").read_text(encoding="utf-8")
    assert "_warm_lock.acquire(timeout=3)" in src
    ping = src[src.index("def _warm_ping_once"):src.index("def _start_warm_pings")]
    assert "timeout=2)" in ping
