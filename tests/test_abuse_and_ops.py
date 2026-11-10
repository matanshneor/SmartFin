"""What breaks when strangers arrive, and what breaks without anyone knowing.

Each item here was **right** in an app for one family and stops being right
the moment signup is open:

- 23 write routes with no rate limit at all, and behind them a trigger that
  copies every deletion into `owner_archive` forever: "add-delete-repeat" was a
  loop growing a 500MB database with no code anywhere in the repo cleaning it.
- The limits that do exist depend on one environment variable, and the warning
  about its absence went to `logger.warning`: a breadcrumb in Sentry, in a log
  buffer that gets wiped.
- A 30-bit invite code that never expired, worth read and write access to the
  family's whole financial history.
- The signup form answered with certainty "does this address have an account
  here".
- A ten-year session that a password change didn't sign out.
- And the client is a process-level singleton that wasn't reset between requests.
"""
from pathlib import Path

import pytest

from backend import app as app_module
from backend.app import app

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_APP_SRC = (_ROOT / "backend/app.py").read_text(encoding="utf-8")
_DB_SRC = (_ROOT / "backend/supabase_config.py").read_text(encoding="utf-8")


def _migration(name):
    return (_ROOT / "backend/supabase/migrations" / name).read_text(encoding="utf-8")


# ─── D1: every write route is limited ───────────────────────────────────────

_WRITE_ROUTES = [
    ('/api/transactions', 'POST'),
    ('/api/transactions/<tx_id>', 'PUT'),
    ('/api/transactions/<tx_id>', 'DELETE'),
    ('/api/categories', 'POST'),
    ('/api/categories/<cat_id>', 'PUT'),
    ('/api/categories/<cat_id>', 'DELETE'),
    ('/api/recurring/<template_id>', 'DELETE'),
    ('/api/recurring/<template_id>/sync', 'PUT'),
    ('/api/profile', 'PUT'),
    ('/api/family', 'PUT'),
    ('/api/family/settings', 'PUT'),
]


@pytest.mark.parametrize("path,method", _WRITE_ROUTES, ids=[f"{m} {p}" for p, m in _WRITE_ROUTES])
def test_every_write_route_has_a_ceiling(path, method):
    """No human scenario comes near the limit; it exists so a script can't fill the
    database, and above all not `owner_archive`, which doesn't clean itself."""
    route = f'@app.route("{path}", methods=["{method}"])'
    assert route in _APP_SRC, f"route {method} {path} not found: the test is stale"

    after = _APP_SRC[_APP_SRC.index(route):]
    decorators = after[:after.index("\ndef ")]

    assert "@limiter.limit" in decorators, f"{method} {path} has no rate limit at all"


def test_the_csv_export_is_limited_too():
    """An export is a heavy query over a whole month, and `/month.csv` is a GET
    that can be opened in a loop."""
    after = _APP_SRC[_APP_SRC.index('@app.route("/month.csv")'):]
    assert "@limiter.limit" in after[:after.index("\ndef ")]


# ─── D1: the amplifiers ─────────────────────────────────────────────────────

def test_the_archive_has_a_retention_window():
    """The trigger copies every deletion into `owner_archive`, and nowhere in the
    repo was there any code deleting from it. Both the policy's promise and an
    abuse amplifier."""
    sql = _migration("20260922160000_archive_retention.sql")

    assert "purge_expired_archive" in sql
    assert "interval '30 days'" in sql


def test_the_purge_is_scheduled_and_not_left_to_a_visitor():
    """A cleanup that runs only when someone signs in stops exactly in the case
    that needs it most: an account that was deleted and nobody comes back to."""
    sql = _migration("20260922160000_archive_retention.sql")

    assert "cron.schedule" in sql
    assert "pg_cron" in sql


def test_bulk_category_insert_has_a_ceiling():
    """The list came from the request body with no limit (~100,000 rows under the
    8MB cap), and `_validated_category` scans it linearly on every transaction
    write."""
    assert "_MAX_CATEGORIES_PER_FAMILY" in _DB_SRC

    fn = _DB_SRC[_DB_SRC.index("def bulk_add_categories("):]
    fn = fn[:fn.index("\ndef ")]
    assert "_MAX_CATEGORIES_PER_FAMILY" in fn


def test_the_recurring_engine_bounds_how_many_templates_it_expands():
    """The cap of 500 occurrences is **per template**. 1,000 templates are half a
    million rows in one `insert`, inside one of four workers."""
    fn = _DB_SRC[_DB_SRC.index("def materialize_recurring("):]
    fn = fn[:fn.index("\ndef ")]

    assert "_MAX_RECURRING_TEMPLATES" in fn


# ─── D2: the limits say whether they work at all ───────────────────────────

def test_losing_the_shared_store_is_an_event_and_not_a_breadcrumb():
    """`warning` isn't an event in Sentry, and Railway's log gets wiped. Without
    this the only alert that every limit was weakened fourfold reached nobody."""
    block = _APP_SRC[_APP_SRC.index("RATELIMIT_IS_SHARED"):][:600]

    assert "logger.error(" in block
    assert "logger.warning(" not in block


def test_health_says_whether_the_limits_are_actually_shared():
    """Otherwise there is no way to check except searching the deploy's logs."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        body = c.get("/health").get_json()

    assert "rate_limiting" in body
    assert body["rate_limiting"] in ("shared", "per-worker")


# ─── D4: the invite code expires ────────────────────────────────────────────

def test_the_invite_code_expires():
    """32^6 is about a billion: reasonable for a code read out over the phone, and
    not reasonable when it is valid forever. A valid code is worth read **and
    write** access to the whole history."""
    sql = _migration("20260922180000_invite_code_expiry.sql")

    assert "invite_code_expires_at" in sql
    assert "interval '7 days'" in sql


def test_an_expired_code_looks_exactly_like_a_wrong_one():
    """Otherwise the answer itself reveals that the code was once real: exactly the
    information a guesser is looking for."""
    sql = _migration("20260922180000_invite_code_expiry.sql")
    join = sql[sql.index("function public.join_family_by_code"):]

    assert "invite_code_expires_at is null or invite_code_expires_at > now()" in join
    assert "return null" in join


def test_the_preview_respects_the_expiry_too():
    sql = _migration("20260922180000_invite_code_expiry.sql")
    preview = sql[sql.index("function public.family_name_for_code"):]

    assert "invite_code_expires_at" in preview


def test_settings_shows_when_the_code_stops_working():
    """Without this there is no way to know that the code you are sending on
    WhatsApp won't work anymore."""
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")

    assert "invite_expired" in html
    assert "invite_expires_in" in html


@pytest.mark.parametrize("stamp,expired", [
    ("2020-01-01T00:00:00+00:00", True),
    ("2099-01-01T00:00:00+00:00", False),
    (None, False),
])
def test_the_expiry_check_compares_aware_datetimes(stamp, expired):
    """`clock.now()` is naive on purpose, and `timestamptz` comes back with an
    offset. Comparing them raises `TypeError`, i.e. the settings page would crash."""
    family = {"invite_code_expires_at": stamp} if stamp else {}

    assert app_module._invite_expired(family) is expired


# ─── D5: the singleton is reset between requests ────────────────────────────

def test_an_empty_token_resets_the_header_instead_of_leaving_it():
    """The client is a process-level singleton. `inject_auth` returned early when
    there was no session, so an anonymous request landed on a worker still
    carrying a previous JWT.

    Not reachable today (no anonymous route calls PostgREST), but it is a trap
    waiting for the next public route, and the same cross-family leak the
    Procfile warns about for threads."""
    fn = _DB_SRC[_DB_SRC.index("def set_auth_token("):]
    fn = fn[:fn.index("\ndef ")]

    assert "SUPABASE_KEY" in fn, "an empty token doesn't reset the header"


def test_the_reset_does_not_create_a_client_that_did_not_exist():
    """A reset shouldn't open a connection: an anonymous request in a process that
    hasn't talked to the database carries no token anyway."""
    fn = _DB_SRC[_DB_SRC.index("def set_auth_token("):]
    fn = fn[:fn.index("\ndef ")]

    assert "_client if not access_token else get_client()" in fn


def test_inject_auth_resets_when_there_is_no_session():
    block = _APP_SRC[_APP_SRC.index("def inject_auth():"):][:600]

    assert 'db.set_auth_token("")' in block


# ─── D6: an abandoned session closes, and a password change signs out ───────

def test_logout_is_post_only():
    """With `SameSite=Lax`, `<img src=".../logout">` on another site signed a
    visitor out and deleted their device cookies."""
    rule = next(r for r in app.url_map.iter_rules() if str(r) == "/logout")

    assert "GET" not in rule.methods


# ─── D7: failures that were invisible ───────────────────────────────────────

def test_a_failed_receipt_upload_is_logged():
    """The error was thrown away entirely, and the quota was already counted: the
    user sees a successful scan with no receipt tag, and there is no trace
    explaining why."""
    block = _APP_SRC[_APP_SRC.index("if upload_err:"):][:400]

    assert "logger.error" in block


def test_an_unreachable_database_is_an_event():
    block = _APP_SRC[_APP_SRC.index("health: database unreachable") - 300:][:400]

    assert "logger.error" in block


# ─── D8: dependency locking ─────────────────────────────────────────────────

def test_the_transitive_dependencies_are_pinned():
    """`requirements.txt` pins only what was chosen explicitly. postgrest, gotrue,
    pydantic and httpcore were resolved afresh on every build."""
    lock = (_ROOT / "requirements.lock")

    assert lock.exists(), "no lock: every transitive dependency is resolved afresh on every build"
    pins = [l for l in lock.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]
    assert len(pins) > 30
    assert all("==" in l for l in pins), "there is a line without a pinned version"


def test_ci_scans_the_lockfile_and_not_only_the_direct_list():
    ci = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "pip-audit -r requirements.lock" in ci
