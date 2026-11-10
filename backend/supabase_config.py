import hashlib
import os
import re
import threading
import time

from dotenv import load_dotenv
from gotrue.errors import AuthApiError, AuthRetryableError
from postgrest.exceptions import APIError

from . import clock
from .money import format_money
from .wording import HEBREW_MONTHS as _FULL_MONTHS
from . import logs

logger = logs.get("smartfin.db")


class DataUnavailable(Exception):
    """The query failed: raised instead of returning an empty value that looks
    like an answer.

    This was the most dangerous pattern in this code. A function that failed
    returned 0, an empty list or `False`, and the caller couldn't tell "no
    data" from "couldn't check". The result is a confident, wrong answer on
    screen:

      · The monthly summary fails  → the dashboard shows ₪0 for the whole
        budget. No sign, no message. The user looks at a zeroed budget and
        believes it. That is the August 2026 incident; the specific cause was
        fixed then, the behaviour wasn't.
      · Fetching the categories fails → the dashboard concludes "new family"
        and sends it to the signup wizard; there the "already done?" check
        fails too and returns "no", and the family is stuck between two screens.
      · Counting transactions fails → the leave-family confirmation is
        silently skipped.
      · Family settings fail → the attribution defaults come back, and the
        transaction is recorded under another family member.
      · The scan quota fails → the limit on the OpenAI account stops working.

    An ordinary bug shows on screen. This one doesn't, and so nobody reports
    it. A visible failure, which can be retried, is better than a false
    number. And as a bonus: an uncaught exception reaches Sentry, while print
    is swallowed in Railway's logs.
    """

load_dotenv()

_client = None


def get_client():
    global _client
    if _client is not None:
        return _client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        logger.warning("SUPABASE_URL or SUPABASE_KEY not set — running without database")
        return None

    try:
        from supabase import create_client
        from supabase.lib.client_options import ClientOptions
        # No auto-refresh and no in-memory session (Matan, 13.10: "why does it
        # keep sending me to the login screen?"). The library default: after every
        # sign-in/refresh on the shared client, a background timer refreshes the
        # token of **whoever signed in last** ten seconds before it expires, again
        # and again, for as long as the worker lives. Each such refresh "uses up"
        # the refresh token in the user's cookie, and when they come back an hour
        # later: "Invalid Refresh Token: Already Used" → the login screen.
        # The new tokens stayed in server memory only, and `web_sessions` never
        # knew about them. Our refresh happens only in `inject_auth`.
        # `ClientOptions(...)` directly and not `.replace`: there, `False or True`
        # returns `True`.
        _client = create_client(url, key, options=ClientOptions(
            auto_refresh_token=False, persist_session=False))
        _client.auth._auto_refresh_token = False
        _keep_connection_warm(_client)
        _start_warm_pings(_client)
        return _client
    except Exception:
        logger.exception("Failed to connect to Supabase")
        return None


KEEPALIVE_SECONDS = 60.0


def _keep_connection_warm(client):
    """One open connection to the database, not a new one after every pause
    (Matan, 13.10).

    In the logs the first query of a request often took `400-570ms` instead
    of `30-60`: the time to open a new secure connection. Two causes, both in
    the library:
    1. `httpx` closes a connection idle for 5 seconds. A user reading the
       screen and moving to the next page is already past 5 seconds.
    2. Every sign-in and token refresh (`refresh_session`, roughly once an
       hour per user) on the shared client fires a `TOKEN_REFRESHED` event,
       and the client throws away its PostgREST, so the next query opens a
       new connection. Its header is reset on every request anyway
       (`set_auth_token`), so there is nothing to lose by keeping it."""
    try:
        import httpx
        pg = client.postgrest
        old = pg.session
        pg.session = httpx.Client(
            base_url=old.base_url, headers=old.headers, timeout=old.timeout,
            follow_redirects=True, http2=True,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20,
                                keepalive_expiry=KEEPALIVE_SECONDS),
        )
        old.close()
        for sub in list(client.auth._state_change_emitters.values()):
            def keep(event, session, _cb=sub.callback):
                kept = client._postgrest
                _cb(event, session)
                if kept is not None:
                    client._postgrest = kept
            sub.callback = keep
    except Exception:
        # Speed only; without it everything works as before
        logger.exception("_keep_connection_warm")


# ─── A warm connection even after a pause (Matan, 13.10) ────────────────────
#
# After `_keep_connection_warm` the home page loads in `70-130ms` while the app
# is in use. The first open after a pause of more than a minute still took
# `530-710ms`: the connection had closed (`KEEPALIVE_SECONDS`) and was reopened
# on the first query. So each worker sends a light request of its own once it
# has been idle `WARM_IDLE_SECONDS`, and the connection never gets to close.
#
# The client is shared, and a request running in parallel with another one
# once caused "Server disconnected" (see `_run_queries` in app.py). Hence a
# lock: the ping runs only when there is no request, and a request arriving
# right then waits for it (tens of milliseconds); they never run together.
WARM_IDLE_SECONDS = 40.0
_warm_lock = threading.Lock()
_warm_state = {"busy": False, "last": 0.0, "started": False}


def request_started():
    """From `before_request`: no warm-up pings from here until `request_finished`."""
    got = _warm_lock.acquire(timeout=3)      # A ping in flight: wait for it, no more than 3 seconds
    try:
        _warm_state["busy"] = True
        _warm_state["last"] = time.monotonic()
    finally:
        if got:
            _warm_lock.release()


def request_finished():
    _warm_state["busy"] = False
    _warm_state["last"] = time.monotonic()


def _warm_ping_once(client) -> bool:
    """One ping if the worker has been idle long enough. `True` if it was sent."""
    if _warm_state["busy"] or time.monotonic() - _warm_state["last"] < WARM_IDLE_SECONDS:
        return False
    with _warm_lock:
        if _warm_state["busy"]:
            return False
        try:
            # The goal is the connection, not the data, but with the anon key
            # and not the token left on the client by the last request. That
            # token expires after an hour, and every ping was rejected with 401:
            # ~3,800 a day, hiding every real rejection in the logs (Matan,
            # 10.11.2026, security review, item 4). Same function as `ping`:
            # anon has no grant on any table, and this reaches Postgres without one.
            key = os.environ.get("SUPABASE_KEY", "")
            client.postgrest.session.post("rpc/get_my_family_id", json={},
                                         headers={"Authorization": f"Bearer {key}"},
                                         # Shorter than the 3 seconds a request waits for the lock, so they never overlap
                                         timeout=2)
        except Exception:
            pass
        _warm_state["last"] = time.monotonic()
        return True


def _start_warm_pings(client):
    """One background thread per worker. Called from `get_client`, i.e. after
    gunicorn's fork (with `--preload`, a thread started before the fork doesn't
    exist in the workers)."""
    if _warm_state["started"] or os.environ.get("SF_NO_WARM_PINGS"):
        return
    _warm_state["started"] = True
    _warm_state["last"] = time.monotonic()

    def loop():
        while True:
            time.sleep(10)
            _warm_ping_once(client)

    threading.Thread(target=loop, name="sf-warm", daemon=True).start()


def ping(timeout: float = 2.0) -> tuple:
    """A liveness check for the database: one round trip to Postgres and back.
    Returns `(ok, detail)`.

    Doesn't go through the shared client, on purpose. The client is a
    process-level singleton whose auth header is replaced on every request
    (see set_auth_token), and the health check is an unauthenticated request:
    a query through it would run with the token left over from the previous
    request and fail the moment it expired. Such a failure looks exactly like
    "the database is down", and it isn't. A separate request with the anon
    key checks what is being asked.

    The check is `get_my_family_id` rather than the API root, so a good answer
    proves Postgres itself responded (the function reads `profiles`), not just
    that the API gateway is up. For the anon key it returns null, and that is
    perfectly fine: what is checked is that the answer arrived, not what was
    in it.

    Until 10.11.2026 this was a read of `families`. Since then anon has no
    grant on any table (security review, `tighten_table_grants`), so the check
    goes through a function, via POST, the usual way to call a function in
    PostgREST.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return False, "not configured"
    try:
        import httpx
        r = httpx.post(
            f"{url.rstrip('/')}/rest/v1/rpc/get_my_family_id",
            json={},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
    except Exception as e:
        # No logger.exception: during a real outage this is called every
        # minute, and it would flood Sentry with a thousand copies of one failure.
        return False, type(e).__name__
    if r.status_code == 200:
        return True, "ok"
    return False, f"http {r.status_code}"


def set_auth_token(access_token: str):
    """Inject the user's JWT so RLS policies resolve auth.uid() correctly."""
    # `_client` and not `get_client()`: a reset must not **create** a client.
    # An anonymous request in a process that hasn't talked to the database
    # carries no token anyway, and creating a client for it is a wasted
    # connection (and, in tests, a real call to Supabase from a unit test).
    client = _client if not access_token else get_client()
    if not client:
        return
    if not access_token:
        # **Must** reset, not return silently. The client is a process-level
        # singleton, and `inject_auth` returns early when there is no session,
        # so an anonymous request lands on a worker still carrying the previous
        # user's JWT.
        #
        # Not reachable today: no anonymous route calls PostgREST (`ping`
        # bypasses the singleton on purpose, and sign-in/reset use httpx
        # directly). But it is a trap waiting for the next public route, and
        # exactly the kind of cross-family leak the Procfile warns about for threads.
        try:
            client.postgrest.auth(os.environ.get("SUPABASE_KEY", ""))
        except Exception:
            logger.exception("set_auth_token: reset")
        return

    try:
        client.postgrest.auth(access_token)
    except Exception:
        logger.exception("set_auth_token")


def _request_cache(key: str, loader):
    """A per-request cache on flask.g: data that is stable within one request
    (categories, family members, family) is fetched once instead of 3-4 times.
    Outside a Flask context (tests/scripts) it just calls the loader directly,
    with no cache."""
    try:
        from flask import g, has_app_context
        if not has_app_context():
            return loader()
    except ImportError:
        return loader()
    cache = getattr(g, "_sf_cache", None)
    if cache is None:
        cache = g._sf_cache = {}
    if key not in cache:
        cache[key] = loader()
    return cache[key]


def _invalidate_family_cache(family_id: str):
    """Clears the per-request cache after a write to the family row.

    Without this, a read made in the same request *after* the write gets the
    value fetched before it. update_family_settings fetches the existing
    settings to merge into them, and that alone fills the cache with the old
    value, so the response sent back to the browser is the settings from
    before the change.

    The damage didn't stop at display: settings.js stores that response and
    uses it to decide whether to show the family-member picker in the
    transaction modal. A user who turned on "expense attribution" got a
    response saying it was off, the modal showed no picker, and the server,
    reading the fresh value on the next request, attributed every
    transaction to whoever was signed in. Silent, wrong attribution of money,
    until the next refresh.

    Two separate caches hold the value: this one, and g.family_settings,
    which app.py keeps. Both are cleared here; otherwise the eviction is
    partial and useless."""
    try:
        from flask import g, has_app_context
        if not has_app_context():
            return
    except ImportError:
        return
    cache = getattr(g, "_sf_cache", None)
    if cache:
        cache.pop(f"family:{family_id}", None)
    g.pop("family_settings", None)


# ─── Auth ─────────────────────────────────────────────────────────────────────

def get_email_by_phone(normalized_phone: str):
    """Finds the email for a normalised phone number (digits only) before the
    user is signed in, through the DB function email_for_phone (SECURITY
    DEFINER, available to anon). Returns None if not found.

    The database function returns an email only with `LOGIN_LOOKUP_SECRET`
    (Matan, 10.11.2026): without it, anyone holding the anon key could collect
    users' emails by phone number. Without the secret there is no point
    asking, and phone sign-in won't work, so this is logged as an error rather
    than swallowed."""
    client = get_client()
    if not client or not normalized_phone:
        return None
    secret = os.environ.get("LOGIN_LOOKUP_SECRET", "")
    if not secret:
        logger.error("get_email_by_phone: LOGIN_LOOKUP_SECRET is missing; phone sign-in will not work")
        return None
    try:
        result = client.rpc("email_for_phone",
                            {"p_phone": normalized_phone, "p_secret": secret}).execute()
        return result.data or None
    except Exception:
        logger.exception("get_email_by_phone")
        return None


def _forget_auth_session(client):
    """Removes from the shared client the session the last action left on it,
    and cancels its timer. The tokens go back to the caller and are kept in
    the cookie and in `web_sessions`, not here."""
    try:
        client.auth._remove_session()
    except Exception:
        logger.exception("_forget_auth_session")


def sign_in(email: str, password: str):
    """Returns (user_data, error_message)."""
    client = get_client()
    if not client:
        return None, "Database not configured"
    try:
        response = client.auth.sign_in_with_password({"email": email, "password": password})
        return response, None
    except Exception as e:
        return None, str(e)
    finally:
        _forget_auth_session(client)


def log_login_event(event: str = "login"):
    """Records a sign-in in the internal login_events table (site owner only).
    Non-blocking: a failure to record doesn't interfere with the sign-in itself."""
    client = get_client()
    if not client:
        return
    try:
        client.rpc("log_login_event", {"p_event": event}).execute()
    except Exception:
        logger.exception("log_login_event")


def reset_transactions(family_id: str, only_user_id: str = None):
    """Transaction reset: deletes all of the family's transactions (including
    recurring templates), or, if only_user_id is given, only the transactions
    attributed to that user. Every deleted row is archived automatically in
    owner_archive by the trigger. Returns (deleted, err)."""
    client = get_client()
    if not client:
        return False, "Database not configured"
    try:
        query = client.table("transactions").delete().eq("family_id", family_id)
        if only_user_id:
            query = query.eq("user_id", only_user_id)
        # The number of rows actually deleted, not a constant `True`: this is the
        # most destructive action in the app, and the user needs to see how many
        # were really deleted.
        return len(query.execute().data or []), None
    except Exception as e:
        logger.exception("reset_transactions")
        return False, str(e)


def delete_my_account():
    """Permanently deletes the signed-in user's account (through a DB function
    with internal archiving). Returns (ok, error_message)."""
    client = get_client()
    if not client:
        return False, "Database not configured"
    try:
        client.rpc("delete_my_account", {}).execute()
        return True, None
    except Exception as e:
        logger.exception("delete_my_account")
        return False, str(e)


def sign_up(email: str, password: str, name: str, phone: str = None):
    """Returns (user_data, error_message)."""
    client = get_client()
    if not client:
        return None, "Database not configured"
    try:
        response = client.auth.sign_up({
            "email": email,
            "password": password,
            "options": {"data": {"name": name, "phone": phone}}
        })
        return response, None
    except Exception as e:
        return None, str(e)
    finally:
        _forget_auth_session(client)


def refresh_session(refresh_token: str):
    """Exchanges a refresh token for a fresh access token. Returns (response,
    error, fatal).

    `fatal` is the point here: it tells "the server was briefly unavailable"
    apart from "the token was rejected". Without that distinction every network
    blip would sign the user out, which breaks the promise that you stay
    signed in until you sign out yourself."""
    client = get_client()
    if not client:
        return None, "Database not configured", False
    try:
        response = client.auth.refresh_session(refresh_token)
        _forget_auth_session(client)
        return response, None, False
    except AuthRetryableError as e:
        # A transient failure (network, timeout): keep the session and retry on
        # the next request
        return None, str(e), False
    except AuthApiError as e:
        # 5xx is a failure on their side, not a bad token. Only a real rejection
        # justifies signing out.
        transient = (e.status or 0) >= 500
        return None, str(e), not transient
    except Exception as e:
        # Unrecognised: assume transient. A wrong sign-out is worse than one
        # degraded request.
        return None, str(e), False


# ─── A copy of the tokens in the database (`web_sessions`, Matan 13.10) ─────
#
# The refresh token rotates on every refresh. When the response carrying the new
# token never reaches the phone (the app went to the background mid-request),
# the cookie is left with a dead token, and that used to mean a sign-out. Now the
# latest token is also stored here, keyed by the `sha256` of the session id in
# the cookie.
#
# The calls are made without a user token (`anon`): at this point the user's
# access token has usually expired, and PostgREST rejects an expired token
# before even reaching the function.
# Every failure here is swallowed with a log entry: this is a safety net, not a
# condition for getting in.

def _sid_hash(sid: str) -> str:
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()


def _anon_rpc(name: str, params: dict):
    client = get_client()
    if not client:
        logger.warning("%s: no client", name)
        return None
    client.postgrest.auth(os.environ.get("SUPABASE_KEY", ""))
    return client.rpc(name, params).execute()


def web_session_open(sid, user_id, access_token, refresh_token, expires_at):
    try:
        _anon_rpc("web_session_open", {
            "p_sid_hash": _sid_hash(sid), "p_user_id": user_id, "p_access": access_token,
            "p_refresh": refresh_token, "p_expires": int(expires_at or 0)})
    except Exception:
        logger.exception("web_session_open")


def web_session_get(sid):
    """The session's latest tokens, or `None` (no row, or a failure)."""
    try:
        result = _anon_rpc("web_session_get", {"p_sid_hash": _sid_hash(sid)})
    except Exception:
        logger.exception("web_session_get")
        return None
    rows = (result.data if result else None) or []
    if not rows:
        logger.info("web_session_get: no row")
        return None
    return rows[0]


def web_session_rotate(sid, old_refresh, access_token, refresh_token, expires_at):
    try:
        _anon_rpc("web_session_rotate", {
            "p_sid_hash": _sid_hash(sid), "p_old_refresh": old_refresh, "p_access": access_token,
            "p_refresh": refresh_token, "p_expires": int(expires_at or 0)})
    except Exception:
        logger.exception("web_session_rotate")


def web_session_close(sid):
    try:
        _anon_rpc("web_session_close", {"p_sid_hash": _sid_hash(sid)})
    except Exception:
        logger.exception("web_session_close")


# ─── Profile ──────────────────────────────────────────────────────────────────

# PostgREST returns this code when the query succeeded but returned no rows.
# That is a valid answer, not a failure, and the distinction is the whole
# point of fetch_profile.
_NO_ROWS = "PGRST116"


def fetch_profile(user_id: str):
    """Returns `(profile, ok)`. `ok=False` means **the query failed**, not that
    there is no profile.

    The distinction looks pedantic and isn't. Until now every exception came
    back as None, which didn't tell "the user has no profile" from "the server
    didn't answer for two seconds". ensure_family concluded from None that the
    user had no family, created a new one, and overwrote the existing
    membership, so a passing network failure during sign-in cut them off for
    good from their whole history, with no way back from the UI, while their
    partner stayed in the old family. From then on the two record into two
    separate budgets without knowing."""
    client = get_client()
    if not client:
        return None, False
    try:
        # The relationship name is spelled out. Since families.manager_id was
        # added there are two relationships between the tables
        # (profiles.family_id→families and families.manager_id→profiles), and
        # PostgREST refuses to guess which one is meant (PGRST201). Without this
        # the query fails, and since it runs at sign-in, nobody can get in.
        result = client.table("profiles") \
            .select("*, families!profiles_family_id_fkey(name)") \
            .eq("id", user_id).single().execute()
        return result.data, True
    except APIError as e:
        if (e.json() or {}).get("code") == _NO_ROWS:
            return None, True          # No profile: an answer, not a failure
        logger.exception("fetch_profile(%s)", user_id)
        return None, False
    except Exception:
        logger.exception("fetch_profile(%s)", user_id)
        return None, False


# PostgREST's `max_rows` (config.toml, and the dashboard in production): every
# response is cut at a thousand rows, with no error and no sign anything is missing.
_PAGE_SIZE = 1000


def _fetch_all(make_query) -> list:
    """All rows, in pages of `_PAGE_SIZE`, until a short page.

    `make_query` builds the query afresh for every page (PostgREST's query
    builder is mutable), and it **must** order by a unique column: without a
    fixed order, pages can overlap or skip rows."""
    rows, start = [], 0
    while True:
        page = make_query().range(start, start + _PAGE_SIZE - 1).execute().data or []
        rows.extend(page)
        if len(page) < _PAGE_SIZE:
            return rows
        start += _PAGE_SIZE


def export_account_data(family_id: str, user_id: str) -> dict:
    """Everything the app holds about the user and their family, in one structure.

    `/month.csv` was the only export: a single month, from the month page, with
    no profile, no projects and no categories. So section 20 (portability)
    wasn't actually provided: there was no way to take the data and leave
    without exporting month by month by hand.

    Another family member's personal project is not included, just like
    everywhere else in the app: an export doesn't bypass privacy within the
    family.
    """
    client = get_client()
    if not client or not family_id:
        raise DataUnavailable("export_account_data: no client")

    try:
        # In pages: at 55 transactions a month, a thousand is a year and a half,
        # and past that the "full" file held only the newest thousand and looked
        # complete.
        transactions = _fetch_all(lambda: client.table("transactions")
                                  .select("*, categories(name), projects(name)")
                                  .eq("family_id", family_id)
                                  .order("date", desc=True)
                                  .order("id"))

        # Other people's personal projects are hidden here too
        visible_projects = {p["id"] for p in get_projects(family_id, user_id)}
        transactions = [
            t for t in transactions
            if not t.get("project_id") or t["project_id"] in visible_projects
        ]

        return {
            "exported_at": clock.now().isoformat(),
            "profile":     get_profile(user_id) or {},
            "family":      get_family(family_id) or {},
            "members":     get_family_members(family_id),
            "categories":  get_categories(family_id),
            "projects":    get_projects(family_id, user_id),
            "transactions": transactions,
        }
    except DataUnavailable:
        raise
    except Exception as e:
        logger.exception("export_account_data")
        raise DataUnavailable("export_account_data") from e


def get_profile(user_id: str):
    """The convenient form, for callers to whom "none" and "failed" are equivalent
    (showing a name, an avatar). Anyone deciding on the absence of a profile
    must use fetch_profile."""
    profile, _ = fetch_profile(user_id)
    return profile


def update_profile(user_id: str, name: str, phone: str = None, workplace: str = None):
    """Updates the current user's display name, phone and workplace.
    Returns (ok, error_message)."""
    client = get_client()
    if not client:
        return False, "Database not configured"
    try:
        client.table("profiles").update(
            {"name": name, "phone": phone, "workplace": workplace}
        ).eq("id", user_id).execute()
        return True, None
    except Exception as e:
        logger.exception("update_profile")
        if "duplicate" in str(e).lower() and "phone" in str(e).lower():
            return False, "מספר הטלפון כבר רשום בחשבון אחר"
        return False, None


def update_phone(user_id: str, phone: str):
    """Changes only the phone (Matan, 1.10: "account" in settings). Returns (ok, error)."""
    client = get_client()
    if not client:
        return False, "Database not configured"
    try:
        client.table("profiles").update({"phone": phone}).eq("id", user_id).execute()
        return True, None
    except Exception as e:
        logger.exception("update_phone")
        if "duplicate" in str(e).lower() and "phone" in str(e).lower():
            return False, "מספר הטלפון כבר רשום בחשבון אחר"
        return False, None


def _auth_user_request(method: str, access_token: str, **kwargs):
    """A call to `/auth/v1/user` with the user's own token, like `update_password`,
    and not through the shared client, whose "current session" isn't safe
    across users."""
    import httpx

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key or not access_token:
        return None
    return getattr(httpx, method)(
        f"{url}/auth/v1/user",
        headers={"apikey": key, "Authorization": f"Bearer {access_token}",
                 "Content-Type": "application/json"},
        timeout=10, **kwargs)


def get_auth_email(access_token: str):
    """The current email, and an email pending confirmation if any: `(email, new_email)`.

    The email in the session is stored at sign-in and doesn't update by itself:
    after a new email was confirmed it stayed the old one, settings showed it,
    and "current password" was checked against it and failed.
    `(None, None)` when it can't be known; the caller falls back to the
    session's email."""
    import httpx
    try:
        r = _auth_user_request("get", access_token)
        if r is None or r.status_code >= 400:
            return None, None
        data = r.json()
        return data.get("email"), data.get("new_email")
    except (httpx.HTTPError, ValueError):
        logger.exception("get_auth_email")
        return None, None


def request_email_change(access_token: str, new_email: str, redirect_to: str):
    """Requests an email change. Supabase sends a confirmation link to the new
    address (and, if "secure email change" is on, to the current one too), and
    the email changes only after confirmation. Until then sign-in continues
    with the old one. Returns (ok, error)."""
    import httpx
    try:
        r = _auth_user_request("put", access_token, params={"redirect_to": redirect_to},
                               json={"email": new_email})
        if r is None:
            return False, "Database not configured"
        if r.status_code >= 400:
            body = r.json() if r.content else {}
            code = (body.get("error_code") or body.get("code") or "")
            text = (body.get("msg") or body.get("message") or "").lower()
            if code == "email_exists" or "already been registered" in text:
                return False, "המייל הזה כבר רשום בחשבון אחר"
            if "rate" in str(code) or "rate limit" in text:
                return False, "נשלחו יותר מדי מיילים — נסו שוב בעוד כמה דקות"
            logger.warning("request_email_change: %s %s", r.status_code, body)
            return False, None
        return True, None
    except (httpx.HTTPError, ValueError):
        logger.exception("request_email_change")
        return False, None


def update_workplace_history(user_id: str, family_id: str, new_workplace: str,
                             old_workplace: str, apply_to_all: bool):
    """Applies a workplace change to the user's existing salary (income)
    transactions, so a future change doesn't silently rewrite what is already
    shown on history.

    apply_to_all=True: every salary transaction (past and future) gets the new value.
    apply_to_all=False: only transactions from the current month on (by
    transaction date) get the new value; older ones with no frozen value yet
    (workplace is null) are frozen to the old value, so they keep showing what
    they showed until now."""
    client = get_client()
    if not client:
        return
    try:
        salary_cat_ids = [
            c["id"] for c in get_categories(family_id)
            if c.get("type") == "income" and "משכורת" in c.get("name", "")
        ]
        if not salary_cat_ids:
            return

        if apply_to_all:
            client.table("transactions").update({"workplace": new_workplace}) \
                .eq("user_id", user_id).eq("type", "income") \
                .in_("category_id", salary_cat_ids).execute()
            return

        month_start = clock.today().replace(day=1).isoformat()

        client.table("transactions").update({"workplace": old_workplace}) \
            .eq("user_id", user_id).eq("type", "income") \
            .in_("category_id", salary_cat_ids) \
            .is_("workplace", "null").lt("date", month_start).execute()

        client.table("transactions").update({"workplace": new_workplace}) \
            .eq("user_id", user_id).eq("type", "income") \
            .in_("category_id", salary_cat_ids) \
            .gte("date", month_start).execute()
    except Exception:
        logger.exception("update_workplace_history")


def update_password(access_token: str, new_password: str):
    """Updates the currently authenticated user's password.

    Calls the GoTrue REST API directly with the user's own access token
    rather than relying on the shared module-level client's implicit
    "current session" (which isn't safe to use for password changes in a
    multi-user server process — see set_auth_token for the same reasoning).
    """
    import httpx

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return False, "Database not configured"
    try:
        response = httpx.put(
            f"{url}/auth/v1/user",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"password": new_password},
            timeout=10,
        )
        if response.status_code >= 400:
            return False, response.json().get("msg", "עדכון הסיסמה נכשל")

        # Sign out every **other** device. Without this a password change did
        # nothing to anyone already signed in: the Flask session lives 90 days
        # and renews on every request, and Supabase's refresh token keeps
        # rotating itself, so a stolen phone, a leaked cookie or a device left
        # with someone stays signed in, and the user has no action that signs
        # them out.
        #
        # `scope=others` and not `global`: whoever changed the password should
        # not find themselves signed out of the device they did it from.
        try:
            httpx.post(
                f"{url}/auth/v1/logout",
                params={"scope": "others"},
                headers={"apikey": key, "Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
        except Exception:
            # The password has already been changed; that is the part that must
            # succeed. A failed sign-out is logged and doesn't undo it.
            logger.exception("update_password: revoke other sessions")

        return True, None
    except Exception as e:
        return False, str(e)


def send_reset_email(email: str, redirect_to: str):
    """Sends a password-reset email with a link back to our reset-password page."""
    import httpx

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return False, "Database not configured"
    try:
        response = httpx.post(
            f"{url}/auth/v1/recover",
            params={"redirect_to": redirect_to},
            headers={"apikey": key, "Content-Type": "application/json"},
            json={"email": email},
            timeout=10,
        )
        if response.status_code >= 400:
            return False, response.json().get("msg", "שליחת המייל נכשלה")
        return True, None
    except Exception as e:
        return False, str(e)


def ensure_family(user_id: str, family_name: str = "המשפחה שלי"):
    """Creates a family and attaches the user to it, if they don't have one yet.

    Creation goes through the `create_own_family` RPC and not a direct write,
    because a direct write to `profiles.family_id` is now blocked at the grant
    level: it was the way an authenticated user could move themselves into
    another family and read all its finances (see migration 20260916100000).
    The function derives from auth.uid() and refuses to replace an existing
    family, so it solves the problem it exists for without reopening it.

    It also solves the chicken-and-egg problem that was here: the read policy
    on families allows reading a family only after the profile already points
    at it, so creation and attachment must happen together, and here they
    really are atomic.
    """
    client = get_client()
    if not client:
        return None

    profile, ok = fetch_profile(user_id)
    if not ok:
        # The query failed. Never conclude from that there is no family:
        # creating a family here overwrites the existing membership and cuts the
        # user off from their whole history for good. A visible failure, which
        # the next attempt can recover from, is far better.
        logger.error("ensure_family(%s): profile read failed — refusing to create a family", user_id)
        return None
    if profile and profile.get("family_id"):
        return profile["family_id"]

    try:
        result = client.rpc("create_own_family", {"p_name": family_name}).execute()
        return result.data or None
    except Exception:
        logger.exception("ensure_family")
        return None


# ─── Family settings ────────────────────────────────────────────────────────

# The defaults = the site's historical behaviour. A family with empty settings
# gets exactly what it always had; only what the family changed is stored in
# the DB.
DEFAULT_FAMILY_SETTINGS = {
    "owner_attribution": {"expense": True, "income": True, "savings": False},
    # A target budget per category: `{"<category_id>": {"amount": 2000, "alert": true}}`
    # A category not listed here simply has no budget, and that is the default:
    # no category gets a budget unless the family set one.
    #
    # Two separate decisions on purpose: some families want to see "₪1,800 of
    # ₪2,000" without the app nagging them about it.
    "limits": {},
}


def category_budget(settings: dict, category_id: str) -> dict:
    """A category's budget, or None if it has none.

    Returns `{"amount": float, "alert": bool}`."""
    entry = ((settings or {}).get("limits") or {}).get(str(category_id))
    if not isinstance(entry, dict):
        return None
    try:
        amount = float(entry.get("amount") or 0)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return {"amount": amount, "alert": bool(entry.get("alert", True))}


def category_month_spent(family_id: str, category_id: str, year: int, month: int) -> float:
    """How much went to a category this month: household expenses only (no
    projects), like the budget bar on the month page. For "you went over the
    budget" when adding (idea 17). Raises DataUnavailable."""
    import calendar
    client = get_client()
    if not client:
        raise DataUnavailable("category_month_spent: no client")
    first = f"{year:04d}-{month:02d}-01"
    last = f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
    try:
        rows = client.table("transactions").select("amount") \
            .eq("family_id", family_id).eq("type", "expense").eq("category_id", category_id) \
            .is_("project_id", "null").gte("date", first).lte("date", last).execute().data or []
    except Exception as e:
        raise DataUnavailable("category_month_spent") from e
    return round(sum(float(r["amount"]) for r in rows), 2)


def apply_budgets(breakdown: list, settings: dict) -> list:
    """Adds each breakdown row's budget status, if it has one.

    `budget` = the amount, `budget_pct` = how much was used (can exceed 100),
    `budget_left` = how much is left (negative when over), `budget_over` =
    whether it went over.

    The existing bar measures the category's share of the month's total
    expenses, i.e. "groceries are 35% of expenses", not "₪200 left". With a
    budget it measures against the family's decision, and that is what people
    actually ask."""
    out = []
    for row in breakdown:
        budget = category_budget(settings, row.get("category_id"))
        row = dict(row)
        if budget:
            spent = float(row.get("total") or 0)
            row["budget"]       = budget["amount"]
            row["budget_alert"] = budget["alert"]
            row["budget_pct"]   = min(round(spent / budget["amount"] * 100), 100)
            row["budget_left"]  = round(budget["amount"] - spent, 2)
            row["budget_over"]  = spent > budget["amount"]
            # The overage itself, so the view doesn't compute it again
            row["budget_excess"] = round(max(spent - budget["amount"], 0), 2)
        out.append(row)
    return out


# Keys whose value is a **whole map**, and so are replaced rather than merged.
#
# `limits` is keyed by category id, and when a budget is removed it is simply
# absent from the new map. A `.update()` merge can only add or overwrite keys,
# never delete them, so the removal was never saved: the screen showed the
# budget off while the server kept it. The other nested keys
# (owner_attribution) are records with fixed fields and are right to keep merging.
# `member_colors` (round 6, item 13): the full map is always sent, and whoever
# left drops out of it
_WHOLE_MAP_KEYS = frozenset({"limits", "member_colors"})


def _merge_settings(base: dict, patch: dict) -> dict:
    """A one-level-deep merge: nested keys (owner_attribution) merge instead of
    being dropped when only some of them are updated. See `_WHOLE_MAP_KEYS`
    for the exceptions that are replaced whole."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (patch or {}).items():
        if k in _WHOLE_MAP_KEYS:
            out[k] = dict(v) if isinstance(v, dict) else v
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def get_family_settings(family_id: str) -> dict:
    """A family's effective settings: defaults + what is stored in the DB."""
    stored = (get_family(family_id) or {}).get("settings") or {}
    return _merge_settings(DEFAULT_FAMILY_SETTINGS, stored)


def update_family_settings(family_id: str, patch: dict) -> bool:
    """Merges a partial update into the family's stored settings.

    The merge happens **in the database**, in one statement with `for update`,
    not here. It used to be read-merge-write with no lock at all: four workers,
    one row, and two family members saving settings in the same second
    overwrote each other. Whoever set a budget and got "saved" saw it vanish on
    the next refresh, with no sign that anything happened.

    The semantics are identical to `_merge_settings` (one level +
    `_WHOLE_MAP_KEYS`), and they are passed to the function so the two
    implementations can't silently drift apart."""
    client = get_client()
    if not client or not family_id:
        return False
    try:
        client.rpc("merge_family_settings", {
            "p_family_id":  family_id,
            "p_patch":      patch or {},
            "p_whole_keys": sorted(_WHOLE_MAP_KEYS),
        }).execute()
        _invalidate_family_cache(family_id)
        return True
    except Exception:
        logger.exception("update_family_settings")
        return False


# ─── Receipt scanning ───────────────────────────────────────────────────────

RECEIPT_MONTHLY_LIMIT = 100

# A cap on the **total** scans of all families together.
#
# The quota above is per family, and signup is open: every new account gets a
# family, and with it a fresh quota. So there was no cap at all on the total,
# and this route is the only one in the app that costs real money.
#
# There is also a billing cap in the OpenAI dashboard (set 20.9.2026), and it is
# the last safety net. The cap here exists because it fails differently: in
# Hebrew, offering manual entry, instead of the key being blocked and an opaque
# API error that looks like a bug.
#
# The number: a very active family uses ~50 a month, so 2,000 is about 40
# heavily-using families: far above any real use at the current scale, and
# still a brake.
RECEIPT_GLOBAL_MONTHLY_LIMIT = int(os.environ.get("RECEIPT_GLOBAL_MONTHLY_LIMIT", "2000"))

_RECEIPT_MEDIA_TYPES = ("image/jpeg", "image/png", "image/webp")


def _month_start() -> str:
    """The start of the month in Israel time. Shared by both counts, so both
    reset at the same moment; the server itself runs in UTC.

    With the time zone explicit: `"2026-10-01"` alone was read by the database
    (UTC) as UTC midnight, which is 03:00 in Israel in summer, so a scan at
    01:30 on the 1st was counted in the previous month."""
    from datetime import datetime
    today = clock.today()
    return datetime(today.year, today.month, 1, tzinfo=clock.ISRAEL).isoformat()


def receipt_scans_globally_this_month() -> int:
    """How many scans were made this month across all families together.

    Through an RPC and not a query: RLS on `receipt_scans` limits every user to
    their own family, so a plain count would return only theirs, i.e. a cap
    that is never crossed. The function returns one number and nothing else."""
    client = get_client()
    if not client:
        raise DataUnavailable("receipt_scans_globally_this_month: no client")
    try:
        return client.rpc("receipt_scans_global_since",
                          {"p_since": _month_start()}).execute().data or 0
    except Exception as e:
        # As with the per-family count: 0 means "nothing used", i.e. the limit
        # is off
        raise DataUnavailable("receipt_scans_globally_this_month") from e


def receipt_scans_this_month(family_id: str) -> int:
    """How many successful scans were made this month; failed scans are neither
    recorded nor counted."""
    client = get_client()
    if not client or not family_id:
        return 0
    try:
        result = client.table("receipt_scans").select("id", count="exact") \
            .eq("family_id", family_id).gte("created_at", _month_start()).execute()
        return result.count or 0
    except Exception as e:
        # 0 means "no quota used", i.e. the spending limit stops working
        raise DataUnavailable("receipt_scans_this_month") from e


def log_app_open() -> bool:
    """Records that the signed-in user opened the app (Matan, 10.11.2026).

    Runs with the user's token, so the database records the caller and nobody
    else; it skips a repeat within 5 minutes by itself. A failure is a warning,
    not an error: a missed open costs one row of statistics, nothing the user
    sees."""
    client = get_client()
    if not client:
        return False
    try:
        client.rpc("log_app_open", {}).execute()
        return True
    except Exception as e:
        logger.warning("log_app_open failed: %s", type(e).__name__)
        return False


def record_receipt_scan(family_id: str, user_id: str):
    """Records a successful scan for the RECEIPT_MONTHLY_LIMIT quota."""
    client = get_client()
    if not client:
        return
    try:
        client.table("receipt_scans").insert(
            {"family_id": family_id, "user_id": user_id}, returning="minimal"
        ).execute()
    except Exception:
        logger.exception("record_receipt_scan")


def upload_receipt(access_token: str, family_id: str, image_bytes: bytes, content_type: str):
    """Uploads a receipt image to the family's folder in the private 'receipts'
    bucket. Done with the user's JWT (not the service key) so RLS checks it
    against their family. Returns (storage_path, error)."""
    import httpx, uuid as _uuid

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return None, "Database not configured"

    ext  = "jpg" if content_type == "image/jpeg" else content_type.split("/")[-1]
    path = f"{family_id}/{_uuid.uuid4()}.{ext}"
    try:
        response = httpx.post(
            f"{url}/storage/v1/object/receipts/{path}",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": content_type,
            },
            content=image_bytes,
            timeout=20,
        )
        if response.status_code >= 400:
            return None, "העלאת הקבלה נכשלה"
        return path, None
    except Exception as e:
        return None, str(e)


def get_receipt_signed_url(access_token: str, path: str):
    """A temporary (5 minute) link for viewing a receipt image. Returns (url, error)."""
    import httpx

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return None, "Database not configured"
    try:
        response = httpx.post(
            f"{url}/storage/v1/object/sign/receipts/{path}",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"expiresIn": 300},
            timeout=10,
        )
        if response.status_code >= 400:
            return None, "לא ניתן להציג את הקבלה"
        signed = response.json().get("signedURL")
        return f"{url}/storage/v1{signed}", None
    except Exception as e:
        return None, str(e)


def delete_receipt(access_token: str, path: str):
    """Deletes a receipt file from storage (e.g. when the attached transaction is deleted)."""
    delete_receipts(access_token, [path])


def delete_receipts(access_token: str, paths) -> None:
    """Deletes several receipt files in one request, as the user (storage policy:
    only members of the folder's family).

    There is no nightly cleanup to sweep up after it: `purge_orphan_receipts`
    failed on every run (Supabase blocks direct deletes from the storage
    tables) and was removed in 20260929150000. So deleting transactions must
    take their files with it here, at the moment of deletion."""
    import httpx

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    paths = sorted({p for p in (paths or []) if p})
    if not url or not key or not paths:
        return
    for i in range(0, len(paths), 100):
        try:
            httpx.request(
                "DELETE",
                f"{url}/storage/v1/object/receipts",
                headers={
                    "apikey": key,
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"prefixes": paths[i:i + 100]},
                timeout=10,
            )
        except Exception:
            logger.exception("delete_receipts")


def receipt_paths(family_id: str, project_ids=None, user_id: str = None) -> list:
    """The receipt paths of transactions about to be deleted: in these projects,
    and/or of this user. Collected **before** the delete; afterwards no row
    points at the file."""
    client = get_client()
    if not client or not (project_ids or user_id):
        return []
    try:
        out = []
        if project_ids:
            out += _fetch_all(lambda: client.table("transactions").select("id, receipt_path")
                              .eq("family_id", family_id).in_("project_id", list(project_ids))
                              .not_.is_("receipt_path", "null").order("id"))
        if user_id:
            out += _fetch_all(lambda: client.table("transactions").select("id, receipt_path")
                              .eq("family_id", family_id).eq("user_id", user_id)
                              .not_.is_("receipt_path", "null").order("id"))
        return sorted({r["receipt_path"] for r in out if r.get("receipt_path")})
    except Exception:
        # Without the paths there is nothing to delete, but the deletion itself
        # isn't stopped over files
        logger.exception("receipt_paths")
        return []


def personal_project_ids(family_id: str, owner_id: str) -> list:
    """A family member's personal projects, to know what will be deleted when
    they leave."""
    client = get_client()
    if not client:
        raise DataUnavailable("personal_project_ids: no client")
    try:
        return [r["id"] for r in client.table("projects").select("id")
                .eq("family_id", family_id).eq("owner_id", owner_id).execute().data or []]
    except Exception as e:
        raise DataUnavailable("personal_project_ids") from e


def receipts_in_use(family_id: str, paths) -> set:
    """Which of these files some transaction still points at. Called **after**
    deleting transactions: whatever comes back here is not deleted. Raises,
    rather than returning "none"."""
    paths = sorted({p for p in (paths or []) if p})
    if not paths:
        return set()
    client = get_client()
    if not client:
        raise DataUnavailable("receipts_in_use: no client")
    try:
        rows = client.table("transactions").select("receipt_path") \
            .eq("family_id", family_id).in_("receipt_path", paths).execute().data
        return {r["receipt_path"] for r in rows}
    except Exception as e:
        raise DataUnavailable("receipts_in_use") from e


def receipt_in_use(path: str, family_id: str) -> bool:
    """Whether any transaction points at the file. Raises rather than saying
    "no": deleting a file in use leaves a transaction with a broken receipt."""
    client = get_client()
    if not client:
        raise DataUnavailable("receipt_in_use: no client")
    try:
        return bool(client.table("transactions").select("id")
                    .eq("family_id", family_id).eq("receipt_path", path)
                    .limit(1).execute().data)
    except Exception as e:
        raise DataUnavailable("receipt_in_use") from e


def transaction_links(tx_id: str, family_id: str):
    """An existing transaction's links (owner and project), or `None` if it
    doesn't exist.

    For editing: the server checks on owner and project were meant for a
    **new** choice, and also rejected a value that hadn't changed (a
    transaction of someone who left, or in a project that stopped tracking the
    type). The form "solved" that by silently replacing the value."""
    # The values themselves too, for "undo" after an edit (`if_match` in app.py)
    row = _tx_row(tx_id, family_id)
    if not row:
        return None
    return {k: row.get(k) for k in ("user_id", "project_id", "amount", "type", "date",
                                     "description", "category_id", "project_category_id")}


def transaction_meta(tx_id: str, family_id: str):
    """Who entered it and when, for the line at the bottom of the edit window
    (Matan, 3.10, idea 22). `None` if the transaction wasn't found. Raises
    DataUnavailable."""
    row = _tx_row(tx_id, family_id)
    if not row:
        return None
    return {k: row.get(k) for k in ("created_by", "created_at", "recurring_parent_id")}


def get_transaction_receipt_path(transaction_id: str, family_id: str):
    client = get_client()
    if not client:
        return None
    try:
        result = client.table("transactions").select("receipt_path") \
            .eq("id", transaction_id).eq("family_id", family_id).single().execute()
        return (result.data or {}).get("receipt_path")
    except Exception:
        # None means "no receipt", and a failed fetch looks exactly like a
        # transaction without a receipt. The direction is safe (nothing deleted,
        # nothing shown), but without this log entry there is no trace that
        # anything went wrong.
        logger.exception("get_transaction_receipt_path")
        return None


def scan_receipt(image_bytes: bytes, content_type: str, category_names: list):
    """Sends a receipt image to OpenAI (a vision model) and extracts amount,
    business, date and a suggested category, from the family's expense
    categories only. Returns (data_dict, error)."""
    import base64
    import json

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None, "סריקת קבלות אינה מוגדרת בשרת"

    try:
        from openai import OpenAI
    except ImportError:
        return None, "סריקת קבלות אינה זמינה כרגע"

    media_type = content_type if content_type in _RECEIPT_MEDIA_TYPES else "image/jpeg"
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    category_prop = {"type": "string", "description": "Leave empty if there is no clear match."}
    if category_names:
        category_prop["enum"] = category_names

    tool = {
        "type": "function",
        "function": {
            "name": "extract_receipt",
            "description": "Extracts the amount, business name, date and matching category from a photo of an Israeli receipt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "The total amount paid, a number only. If the image is not a readable receipt, return 0.",
                    },
                    "merchant": {
                        "type": "string",
                        "description": "The business name as it appears on the receipt.",
                    },
                    "date": {
                        "type": "string",
                        "description": "The transaction date in YYYY-MM-DD format. Leave empty if unclear.",
                    },
                    "category_name": category_prop,
                },
                "required": ["amount", "merchant"],
            },
        },
    }

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=300,
            tools=[tool],
            tool_choice={"type": "function", "function": {"name": "extract_receipt"}},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "This is a photo of an Israeli receipt. Extract its data using the tool."},
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                ],
            }],
        )
        tool_calls = response.choices[0].message.tool_calls
        if tool_calls:
            try:
                data = json.loads(tool_calls[0].function.arguments or "{}")
            except (TypeError, ValueError):
                data = {}
            try:
                amount = float(data.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0
            if amount <= 0:
                return None, "לא הצלחנו לקרוא את הקבלה — נסו שוב או הזינו ידנית"
            return {
                "amount":         amount,
                "merchant":       (data.get("merchant") or "").strip(),
                "date":           (data.get("date") or "").strip() or None,
                "category_name":  (data.get("category_name") or "").strip() or None,
            }, None
        return None, "לא הצלחנו לקרוא את הקבלה — נסו שוב או הזינו ידנית"
    except Exception:
        logger.exception("scan_receipt")
        return None, "שגיאה בסריקת הקבלה — נסו שוב"


# ─── Transactions ─────────────────────────────────────────────────────────────

def get_monthly_summary(family_id: str, year: int, month: int) -> dict:
    """Returns totals for income, expense, savings for a given month."""
    client = get_client()
    if not client:
        return _empty_summary()
    try:
        # Transactions attached to a project are excluded from the monthly balance:
        # a project is a one-off/capital expense or income that distorts the
        # picture of a "normal month" (shown separately).
        result = client.table("transactions") \
            .select("type, amount") \
            .eq("family_id", family_id) \
            .is_("project_id", "null") \
            .gte("date", f"{year}-{month:02d}-01") \
            .lt("date", _next_month(year, month)) \
            .execute()

        # Exactly the same calculation as the month page. Here it was a copy
        # without rounding, and floating-point fractions turned zero into
        # `-1.1e-13`, so the home page showed "-₪0" in red.
        return summary_from_rows(result.data)
    except Exception as e:
        # Never return zeros: a ₪0 dashboard looks like an empty budget, not a failure
        raise DataUnavailable("get_monthly_summary") from e


_WEEK_LABELS = ("א׳", "ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳")


def week_spending(family_id: str, today=None, offset: int = 0) -> dict:
    """The "this week" card on the home page (Matan, 30.9): expenses Sunday to
    Saturday, day by day.

    Household expenses only: no projects, and recurring transactions
    (template or occurrence) aren't counted: ₪5,500 of rent on one day dwarfed
    everything else, and the week is meant to show running expenses. Recurring
    ones do appear in their day's breakdown, marked, and on a separate line
    under the total (`recurring_total`), Matan, 11.10.

    `last_week`: the previous week's total, whole; `None` when it had no
    expenses. In the current week it is shown as a fact ("last week: ₪X") with
    no `diff`: the previous comparison, Sunday–Wednesday against
    Sunday–Wednesday, was correct but not understandable; on Sunday morning it
    showed "₪519 less than last week" after an expensive week (Matan, 11.10).
    For a finished week, `diff` is a whole week against a whole week.

    A transaction entered ahead of time for a day that hasn't come yet appears
    on its day and counts in the week's total (Matan, 4.10), just as it counts
    in its month."""
    today = today or clock.today()
    prev_start, start, end = week_bounds(today, offset)
    empty = {"days": [], "total": 0.0, "recurring_total": 0.0, "last_week": None, "diff": None,
             "offset": offset, "has_older": False}
    client = get_client()
    if not client:
        return empty
    try:
        rows = client.table("transactions") \
            .select("amount, date, description, category_id, is_recurring, recurring_parent_id, "
                    "project_id, categories(name, icon)") \
            .eq("family_id", family_id).eq("type", "expense").is_("project_id", "null") \
            .gte("date", prev_start.isoformat()) \
            .lte("date", end.isoformat()).execute().data or []
    except Exception as e:
        raise DataUnavailable("week_spending") from e
    # Whether there is anywhere to page back to: any running expense before the
    # start of this week
    try:
        older = client.table("transactions").select("date") \
            .eq("family_id", family_id).eq("type", "expense").is_("project_id", "null") \
            .eq("is_recurring", False).is_("recurring_parent_id", "null") \
            .lt("date", start.isoformat()).order("date", desc=True).limit(1).execute().data or []
    except Exception as e:
        raise DataUnavailable("week_spending: older") from e
    return week_from_rows(rows, bool(older), today, offset)


def week_bounds(today, offset: int = 0):
    """`(Sunday of the previous week, Sunday of the shown week, its Saturday)`.

    `offset`: how many weeks back (Matan, 2.10: paging to earlier weeks). A
    past week is shown whole, and compared with the whole week before it."""
    from datetime import timedelta
    start = today - timedelta(days=(today.weekday() + 1) % 7) - timedelta(days=7 * offset)
    return start - timedelta(days=7), start, start + timedelta(days=6)


def week_from_rows(rows: list, has_older: bool, today, offset: int = 0) -> dict:
    """The "this week" card from rows already fetched, from `week_spending` or
    from `get_home_page`, which brings them along with the rest of the home page."""
    from datetime import timedelta
    prev_start, start, _ = week_bounds(today, offset)
    rows = [r for r in rows if not r.get("project_id")]
    # A recurring transaction (template or occurrence) is shown on its day,
    # marked, and not counted in the bars, the total or the comparison (Matan,
    # 11.10); when it disappeared entirely it looked like a bug
    fixed = [r for r in rows if r.get("is_recurring") or r.get("recurring_parent_id")]
    rows = [r for r in rows if not r.get("is_recurring") and not r.get("recurring_parent_id")]

    def _items(txs):
        return [{"icon": (r.get("categories") or {}).get("icon") or "📦",
                 "name": (r.get("categories") or {}).get("name") or _NO_CATEGORY,
                 "description": r.get("description") or "",
                 "amount": float(r["amount"])} for r in txs]

    days = []
    for i in range(7):
        d = start + timedelta(days=i)
        txs = [r for r in rows if str(r["date"])[:10] == d.isoformat()]
        recurring = [r for r in fixed if str(r["date"])[:10] == d.isoformat()]
        days.append({
            "date": d.isoformat(), "label": _WEEK_LABELS[i], "day": d.day, "month": d.month,
            "today": d == today, "future": d > today,
            "total": round(sum(float(r["amount"]) for r in txs), 2),
            "transactions": _items(txs),
            "recurring": _items(recurring),
        })
    total = round(sum(d["total"] for d in days), 2)
    recurring_total = round(sum(t["amount"] for d in days for t in d["recurring"]), 2)
    prev = [r for r in rows if prev_start.isoformat() <= str(r["date"])[:10] < start.isoformat()]
    last = round(sum(float(r["amount"]) for r in prev), 2) if prev else None
    # The day whose breakdown is open: today itself in the current week; in
    # past weeks, the last day with expenses (and if there were none, Saturday)
    shown = [i for i, d in enumerate(days) if d["today"]] or \
            [i for i, d in enumerate(days) if d["total"]][-1:] or [6]
    for i, d in enumerate(days):
        d["selected"] = i == shown[0]
    return {"days": days, "total": total, "recurring_total": recurring_total, "last_week": last,
            "diff": round(total - last, 2) if last is not None and offset else None,
            "max": max([d["total"] for d in days] + [0]),
            "offset": offset, "has_older": bool(has_older)}


def _filter_hidden_personal_projects(rows: list, viewer_user_id: str) -> list:
    """Filters out transaction rows belonging to another family member's personal
    project, for privacy: only the personal project's owner sees its individual
    transactions. Monthly summaries aren't affected anyway: they exclude every
    project transaction (see _household_rows)."""
    if not viewer_user_id:
        return rows
    return [
        row for row in rows
        if not row.get("projects") or not row["projects"].get("owner_id")
           or row["projects"]["owner_id"] == viewer_user_id
    ]


def recent_descriptions(family_id: str, viewer_user_id: str, today=None, limit: int = 300) -> list:
    """Descriptions already written, for autocomplete in the "short description"
    field (Matan, 3.10, idea 41). The whole family's, from the last 6 months,
    most common first. Without another family member's personal project, the
    same rule as every list (`_filter_hidden_personal_projects`). Raises
    DataUnavailable."""
    from datetime import timedelta
    from collections import Counter
    today = today or clock.today()
    client = get_client()
    if not client:
        raise DataUnavailable("recent_descriptions: no client")
    try:
        rows = _fetch_all(lambda: client.table("transactions")
                          .select("id, description, projects(owner_id)")
                          .eq("family_id", family_id)
                          .gte("date", (today - timedelta(days=183)).isoformat())
                          .order("id"))
    except Exception as e:
        raise DataUnavailable("recent_descriptions") from e
    rows = _filter_hidden_personal_projects(rows, viewer_user_id)
    counts, spelled = Counter(), {}
    for r in rows:
        text = " ".join((r.get("description") or "").split())
        if len(text) < 2:
            continue
        key = text.lower()
        counts[key] += 1
        spelled.setdefault(key, text)
    return [spelled[k] for k, _ in counts.most_common(limit)]


def search_transactions(family_id: str, viewer_user_id: str, q: str,
                        settings: dict = None, limit: int = 100) -> dict:
    """Search across all months (Matan, 30.9, round 6, item 2).

    Searches the description, category name, workplace and project name. A
    number ("690") also finds transactions of exactly that amount. Matching is
    done in Python and not PostgREST: the category and project names live in
    other tables, and `or` on a linked table's columns isn't supported. At a
    family's scale (thousands of rows) this is one light query.

    `count` and `totals` cover everything found; `results` up to `limit`,
    newest first."""
    empty = {"results": [], "count": 0, "truncated": False,
             "totals": {"expense": 0.0, "income": 0.0, "savings": 0.0}}
    needle = " ".join((q or "").split()).lower()
    amount = None
    try:
        amount = float(needle.replace(",", "").replace("₪", ""))
    except ValueError:
        pass
    if len(needle) < 2 and amount is None:
        return empty
    client = get_client()
    if not client:
        return empty
    try:
        rows = _fetch_all(lambda: client.table("transactions")
                          .select("*, categories(name, icon), project_categories(name, icon), "
                                  "profiles(name, workplace), projects(owner_id, name, icon)")
                          .eq("family_id", family_id)
                          .order("id"))
    except Exception as e:
        raise DataUnavailable("search_transactions") from e

    def hit(r):
        if amount is not None and abs(float(r.get("amount") or 0) - amount) < 0.005:
            return True
        cat = (r.get("project_categories") if r.get("project_category_id") else r.get("categories")) or {}
        text = " ".join(str(x) for x in (
            r.get("description"), r.get("workplace"), cat.get("name"),
            (r.get("projects") or {}).get("name")) if x).lower()
        return needle in text

    found = [r for r in _filter_hidden_personal_projects(rows, viewer_user_id) if hit(r)]
    found.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("created_at") or "")), reverse=True)
    totals = {"expense": 0.0, "income": 0.0, "savings": 0.0}
    for r in found:
        if r.get("type") in totals:
            totals[r["type"]] += float(r.get("amount") or 0)
    return {"results": _format_transactions(found[:limit], settings),
            "count": len(found), "truncated": len(found) > limit,
            "totals": {k: round(v, 2) for k, v in totals.items()}}


def precheck_transaction(family_id: str, viewer_user_id: str, body: dict, today=None) -> dict:
    """What to ask before saving a new transaction (Matan, 30.9, round 6, items 6–7).

    `duplicate`: a transaction identical in everything: type, category, amount,
    date (exactly the same day), note, project and assignee. A change in one
    field is enough not to warn. Most common: both partners enter the same
    purchase. `by`: only when someone else entered it.

    `unusual`: the amount is 5 times or more the largest in the category over
    the last half year, when there are at least 5 earlier ones to compare
    with. Not for income: a large bonus isn't a mistake.

    Both are questions, not blocks, so any failure here returns "nothing to
    ask": better to save without a warning than not to save at all."""
    from datetime import date, datetime, timedelta
    none = {"duplicate": None, "unusual": None}
    today = today or clock.today()
    try:
        amount = float(body.get("amount"))
        type_ = str(body.get("type") or "")
        day = date.fromisoformat(str(body.get("date"))[:10])
    except (TypeError, ValueError):
        return none
    cat_key = "project_category_id" if body.get("project_category_id") else "category_id"
    cat = body.get(cat_key)
    if not cat or amount <= 0 or type_ not in ("expense", "income", "savings"):
        return none
    client = get_client()
    if not client:
        return none

    def same(r):
        return str(r.get(cat_key) or "") == str(cat)

    def _norm_text(v):
        return str(v or "").strip().casefold()

    def _norm_owner(v):
        return None if (not v or v == "shared") else str(v)

    want_desc = _norm_text(body.get("description"))
    want_project = str(body.get("project_id") or "")
    want_owner = _norm_owner(body.get("owner"))

    try:
        near = client.table("transactions").select("*, projects(owner_id)") \
            .eq("family_id", family_id).eq("type", type_) \
            .eq("date", day.isoformat()).execute().data or []
        near = [r for r in _filter_hidden_personal_projects(near, viewer_user_id)
                if same(r) and abs(float(r.get("amount") or 0) - amount) < 0.005
                and _norm_text(r.get("description")) == want_desc
                and str(r.get("project_id") or "") == want_project
                and _norm_owner(r.get("user_id")) == want_owner]
        duplicate = None
        if near:
            r = sorted(near, key=lambda x: str(x.get("created_at") or ""), reverse=True)[0]
            by = None
            if r.get("created_by") and r["created_by"] != viewer_user_id:
                by = next((m["name"] for m in get_family_members(family_id)
                           if m["id"] == r["created_by"]), None)
            time_ = None
            try:
                time_ = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00")) \
                    .astimezone(clock.ISRAEL).strftime("%H:%M")
            except (KeyError, TypeError, ValueError):
                pass
            duplicate = {"amount": float(r["amount"]), "date": str(r["date"])[:10],
                         "description": r.get("description") or "", "by": by, "time": time_}

        unusual = None
        if type_ != "income":
            past = client.table("transactions").select("*, projects(owner_id)") \
                .eq("family_id", family_id).eq("type", type_) \
                .gte("date", (today - timedelta(days=183)).isoformat()).execute().data or []
            amounts = [float(r.get("amount") or 0)
                       for r in _filter_hidden_personal_projects(past, viewer_user_id) if same(r)]
            if len(amounts) >= 5 and amount >= 5 * max(amounts):
                unusual = {"min": round(min(amounts), 2), "max": round(max(amounts), 2)}
        return {"duplicate": duplicate, "unusual": unusual}
    except Exception:
        logger.exception("precheck_transaction")
        return none


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


def personal_project_owner(tx_id: str, family_id: str):
    """The owner of the personal project the transaction sits in, or `None`
    (household, a shared project, or the transaction doesn't exist, in which
    case the route itself returns 404).

    The basis of `tx_visible_required` in app.py. RLS doesn't help here: it
    separates families, and a personal project is privacy *within* a family."""
    # An id that isn't a UUID won't match any row, and the route returns 404 for
    # it itself. Without this check PostgREST rejects it with an error, which
    # would turn into a 503.
    if not _UUID_RE.match(str(tx_id)):
        return None
    # A failure here must stop, not pass: "couldn't check" is not "allowed"
    # (`_tx_row` raises)
    row = _tx_row(tx_id, family_id)
    if not row:
        return None
    return (row.get("projects") or {}).get("owner_id")


_TX_ROW_FIELDS = ("user_id, project_id, amount, type, date, description, category_id, "
                  "project_category_id, created_by, created_at, recurring_parent_id, "
                  "projects(owner_id)")


def _tx_row(tx_id: str, family_id: str):
    """The transaction row, once per request (Matan, 13.10, editing speed).

    The check "someone else's personal project?" (`tx_visible_required`),
    "what are the values now" (`transaction_links`) and "who entered it and
    when" (`transaction_meta`) each read the same row separately: three trips
    to the database before the window opened or the save started. Called only
    before a write, so the cache doesn't go stale within the request."""
    def load():
        client = get_client()
        if not client:
            raise DataUnavailable("_tx_row: no client")
        try:
            rows = client.table("transactions").select(_TX_ROW_FIELDS) \
                .eq("id", tx_id).eq("family_id", family_id).limit(1).execute().data or []
        except Exception as e:
            raise DataUnavailable("_tx_row") from e
        return rows[0] if rows else None
    return _request_cache(f"tx:{family_id}:{tx_id}", load)


def get_recent_transactions(family_id: str, limit: int = 5, settings: dict = None,
                            viewer_user_id: str = None) -> list:
    """Returns the most recent transactions with category and user info.

    Transactions attached to a project are excluded: they belong to the project
    only, and appear on the project page and in the "projects this month"
    section. Consistent with the rest of the month's data, all of which filter
    them (summary, category breakdown, split between family members, month
    comparison)."""
    client = get_client()
    if not client:
        return []
    try:
        # Safety margin: if private rows are filtered out, we still want to reach
        # `limit` visible rows
        fetch_limit = limit * 3 if viewer_user_id else limit
        # Sorted by insertion order (created_at), not by transaction date, so a
        # transaction entered most recently appears first even if it is dated in
        # the past (Matan's request)
        result = client.table("transactions") \
            .select("*, categories(name, icon), project_categories(name, icon), profiles(name, workplace), projects(owner_id, name, icon)") \
            .eq("family_id", family_id) \
            .is_("project_id", "null") \
            .or_(_recent_series_filter()) \
            .order("created_at", desc=True) \
            .limit(fetch_limit) \
            .execute()
        return recent_from_rows(result.data, limit, settings, viewer_user_id)
    except Exception as e:
        logger.exception("get_recent_transactions")
        raise DataUnavailable("get_recent_transactions") from e


def recent_from_rows(rows: list, limit: int, settings: dict = None,
                     viewer_user_id: str = None) -> list:
    """"Recent transactions" from rows already fetched (also from `get_home_page`)."""
    rows = _filter_hidden_personal_projects(rows or [], viewer_user_id)[:limit]
    return _format_transactions(rows, settings)


def _recent_series_filter(today=None) -> str:
    """"Recent transactions" without recurring transactions from months already
    past (Matan, 5.10).

    A recurring transaction added with a start date in the past (rent "from
    July") immediately creates the copies for every past month, and all of them
    were just created, so they all jumped to the top of the list and pushed out
    what was really entered just now. What remains: every regular transaction,
    and every transaction from a recurring series from the start of the current
    month. In the query and not after it; otherwise a year-long series would
    take up every fetched row.

    `not.is.true` and not `eq.false`: the column allows `NULL`, and such a row
    is a regular transaction."""
    month_start = (today or clock.today()).replace(day=1).isoformat()
    return f"and(is_recurring.not.is.true,recurring_parent_id.is.null),date.gte.{month_start}"


def get_month_transactions(family_id: str, year: int, month: int, settings: dict = None,
                           viewer_user_id: str = None) -> list:
    """Returns all transactions for a given month (excluding transactions in
    another family member's personal project, for privacy)."""
    client = get_client()
    if not client:
        return []
    try:
        result = client.table("transactions") \
            .select("*, categories(name, icon), project_categories(name, icon), profiles(name, workplace), projects(owner_id, name, icon)") \
            .eq("family_id", family_id) \
            .gte("date", f"{year}-{month:02d}-01") \
            .lt("date", _next_month(year, month)) \
            .order("date", desc=True) \
            .execute()
        rows = _filter_hidden_personal_projects(result.data, viewer_user_id)
        return _format_transactions(rows, settings)
    except Exception as e:
        logger.exception("get_month_transactions")
        raise DataUnavailable("get_month_transactions") from e


def add_transaction(data: dict):
    """Inserts a transaction. data must include: amount, type, family_id, date."""
    client = get_client()
    if not client:
        return None, "Database not configured"
    try:
        result = client.table("transactions").insert(data).execute()
        return result.data[0] if result.data else None, None
    except Exception as e:
        return None, str(e)


def add_transactions(rows: list):
    """Inserts several transactions in one request (card import). All or
    nothing: PostgREST inserts an array in one transaction, so an import that
    fails halfway doesn't leave half a month in the database. Returns
    `(count, error)`."""
    client = get_client()
    if not client:
        return 0, "Database not configured"
    if not rows:
        return 0, None
    try:
        client.table("transactions").insert(rows, returning="minimal").execute()
        return len(rows), None
    except Exception as e:
        logger.exception("add_transactions")
        return 0, str(e)


def import_existing_expenses(family_id: str, viewer_user_id: str, start: str, end: str) -> list:
    """The family's expenses in the range, against which card-import duplicates
    are checked. Includes projects (a purchase on a trip is charged to the same
    card), without another family member's personal project: its description
    would be shown under "maybe already entered", and that is a leak."""
    client = get_client()
    if not client:
        raise DataUnavailable("import_existing_expenses: no client")
    try:
        rows = _fetch_all(lambda: client.table("transactions")
                          .select("id, date, amount, description, project_id, projects(owner_id)")
                          .eq("family_id", family_id).eq("type", "expense")
                          .gte("date", start).lte("date", end).order("id"))
    except Exception as e:
        raise DataUnavailable("import_existing_expenses") from e
    return _filter_hidden_personal_projects(rows, viewer_user_id)


def import_category_history(family_id: str, since: str, limit: int = 3000) -> list:
    """The latest household expenses with a description and category, newest
    first: the "memory" of business → category
    (`card_import.learned_categories`). Empty on failure: without history the
    import still works, it just guesses less."""
    client = get_client()
    if not client:
        return []
    try:
        return client.table("transactions") \
            .select("description, category_id, date") \
            .eq("family_id", family_id).eq("type", "expense") \
            .is_("project_id", "null").not_.is_("category_id", "null") \
            .gte("date", since).order("date", desc=True).order("created_at", desc=True) \
            .limit(limit).execute().data or []
    except Exception:
        logger.exception("import_category_history")
        return []


# ─── Phone notifications (Matan, 31.10); see `backend/push.py` ──────────────

def push_subscribe(endpoint: str, p256dh: str, auth: str) -> bool:
    client = get_client()
    if not client:
        return False
    try:
        client.rpc("push_subscribe", {"p_endpoint": endpoint, "p_p256dh": p256dh,
                                      "p_auth": auth}).execute()
        return True
    except Exception:
        logger.exception("push_subscribe")
        return False


def push_unsubscribe(endpoint: str) -> None:
    """Only the signed-in user's device (RLS); someone else's endpoint simply
    isn't found."""
    client = get_client()
    if not client or not endpoint:
        return
    try:
        client.table("push_subscriptions").delete().eq("endpoint", endpoint).execute()
    except Exception:
        logger.exception("push_unsubscribe")


def push_family_targets(kind: str = None, period: str = None) -> list:
    """The devices of the signed-in user's other family members. With
    `kind/period`: only the first time for the period (the database records
    it). Empty on any failure: a notification is an extra."""
    client = get_client()
    if not client:
        return []
    try:
        return client.rpc("push_family_targets", {"p_kind": kind, "p_period": period}).execute().data or []
    except Exception:
        logger.exception("push_family_targets")
        return []


def push_daily(secret: str, today: str) -> list:
    client = get_client()
    if not client:
        return []
    try:
        return client.rpc("push_daily", {"p_secret": secret, "p_today": today}).execute().data or []
    except Exception:
        logger.exception("push_daily")
        return []


def push_forget(secret: str, endpoints: list) -> None:
    client = get_client()
    if not client or not endpoints:
        return
    try:
        client.rpc("push_forget", {"p_secret": secret, "p_endpoints": list(endpoints)}).execute()
    except Exception:
        logger.exception("push_forget")


def get_recurring_transactions(family_id: str, viewer_user_id: str, settings: dict = None) -> list:
    """All the recurring templates the viewer may see.

    This was the only caller without `_filter_hidden_personal_projects`: a
    recurring payment in a personal project appeared to the whole family in
    settings and on the month page (description, amount and id, and from the id
    it could be edited and deleted). `viewer_user_id` is required on purpose,
    because the filter is open when it is missing."""
    client = get_client()
    if not client:
        return []
    try:
        result = client.table("transactions") \
            .select("*, categories(name, icon), project_categories(name, icon), "
                    "profiles(name, workplace), projects(owner_id, name, icon)") \
            .eq("family_id", family_id) \
            .eq("is_recurring", True) \
            .order("amount", desc=True) \
            .execute()
        return _format_transactions(
            _filter_hidden_personal_projects(result.data, viewer_user_id), settings)
    except Exception as e:
        logger.exception("get_recurring_transactions")
        raise DataUnavailable("get_recurring_transactions") from e


def is_active_template(row: dict, today=None) -> bool:
    """A template whose end date has passed is no longer recurring.

    One rule for both screens that show "recurring transactions". Until now
    only the month page filtered, and settings also showed series that had
    ended, under the heading "N active recurring transactions". After "update
    from now on" it showed the rent twice."""
    end = row.get("recurring_end_date")
    return not (end and str(end) < (today or clock.today()).isoformat())



_MAX_RECURRING_TEMPLATES = 200


def materialize_recurring(family_id: str) -> int:
    """Fills in missing occurrences of recurring transactions up to today
    (retroactively too).

    Every transaction marked recurring serves as a "template": the first
    occurrence is the transaction itself, and from it regular occurrences
    (is_recurring=False) are created by frequency, up to today or the end date.
    The function is idempotent: an occurrence that already exists won't be
    created again. Returns `(created, ok)`. `ok=False` means the run failed,
    and that matters, because the caller marks "synced to today" and won't
    retry until tomorrow. A failure that looks like success leaves a month
    without salary or standing orders until the next day."""
    from datetime import date

    client = get_client()
    if not client or not family_id:
        return 0, False
    try:
        # `limit` on the templates. The cap of 500 occurrences is **per
        # template**, and nobody limited how many templates there are: 1,000
        # templates × 500 occurrences is a single `insert` of half a million rows
        # inside one worker, then OOM or one of four workers stuck. A real family
        # has tens.
        templates = client.table("transactions").select("*") \
            .eq("family_id", family_id).eq("is_recurring", True) \
            .limit(_MAX_RECURRING_TEMPLATES).execute().data
        if not templates:
            _remember_next_due(family_id, [], clock.today())
            return 0, True

        # In pages: the list covers the whole family. Past a thousand the
        # engine stopped seeing some of the occurrences and created them again.
        existing = _fetch_all(lambda: client.table("transactions")
                              .select("recurring_parent_id, date")
                              .eq("family_id", family_id)
                              .not_.is_("recurring_parent_id", "null")
                              .order("id"))
        # The dates of the existing occurrences per template. Not a set of
        # (template, date) pairs: the comparison is by period and not by exact
        # date, otherwise changing a template's date produces a new series that
        # is entirely "missing" and duplicates months back.
        have: dict = {}
        for r in existing:
            have.setdefault(r["recurring_parent_id"], set()).add(
                date.fromisoformat(str(r["date"])))

        # A cache of salary categories and current workplace per owner: avoids
        # refetching for every occurrence, and lets the workplace be frozen on
        # recurring-salary occurrences exactly as on manual entry (add_transaction)
        salary_cat_ids = {
            c["id"] for c in get_categories(family_id)
            if c.get("type") == "income" and "משכורת" in c.get("name", "")
        }
        workplace_by_user: dict = {}

        def _owner_workplace(uid):
            if uid not in workplace_by_user:
                profile = get_profile(uid) if uid else None
                workplace_by_user[uid] = (profile or {}).get("workplace")
            return workplace_by_user[uid]

        today = clock.today()
        new_rows = []
        for t in templates:
            is_salary = t.get("type") == "income" and t.get("category_id") in salary_cat_ids
            freq = t.get("recurring_frequency") or "monthly_1"
            seen = have.setdefault(t["id"], set())
            # The template row itself is the first occurrence (see the docs
            # above), but it has no recurring_parent_id so it wasn't fetched with
            # the occurrences. Without this a template opened on January 5th and
            # moved to "the 15th of the month" gets a second January occurrence
            # next to the original row.
            seen.add(date.fromisoformat(str(t["date"])))
            # Occurrences the user deleted on purpose. Without them the engine
            # sees a gap and fills it, so deleting a single occurrence undoes
            # itself the next day, and whoever cancelled a subscription for one
            # month sees it come back with no explanation.
            for skipped in (t.get("recurring_skips") or []):
                seen.add(date.fromisoformat(str(skipped)))
            for d in _recurring_occurrences(t, today):
                if _already_materialized(freq, d, seen):
                    continue
                # The occurrence just created takes its period, so two occurrences
                # of the same template in the same period aren't created in the
                # same run
                seen.add(d)
                # Installments: the template is the first one (with the rounding);
                # all the rest are the regular installment, numbered by the months
                # since the first
                inst = {}
                if t.get("installments") and t.get("installment_total"):
                    plan = installment_plan(t["installment_total"], int(t["installments"]), str(t["date"]))
                    start = date.fromisoformat(str(t["date"])[:10])
                    if plan:
                        inst = {"amount": plan["base"], "installments": plan["count"],
                                "installment_total": plan["total"],
                                "installment_no": (d.year - start.year) * 12 + d.month - start.month + 1}
                new_rows.append({
                    "amount":              inst.pop("amount", t["amount"]),
                    "type":                t["type"],
                    "date":                d.isoformat(),
                    "description":         t.get("description") or "",
                    "category_id":         t.get("category_id"),
                    # Without these, from the second month every installment
                    # inside a project landed in household expenses as
                    # "uncategorised" (a project transaction has no family category)
                    "project_id":          t.get("project_id"),
                    "project_category_id": t.get("project_category_id"),
                    # Whoever recorded the series, not whoever happened to open
                    # the app today (without the field, the database default is
                    # the run's `auth.uid()`)
                    "created_by":          t.get("created_by"),
                    "user_id":             t.get("user_id"),
                    "family_id":           family_id,
                    "is_recurring":        False,
                    "recurring_parent_id": t["id"],
                    # Stored on the occurrence too (not just the template) so the
                    # view can say "recurring, every X" without fetching the
                    # template separately
                    "recurring_frequency": t.get("recurring_frequency"),
                    "workplace":           _owner_workplace(t.get("user_id")) if is_salary else None,
                    **inst,
                })

        created = 0
        if new_rows:
            try:
                client.table("transactions").insert(new_rows, returning="minimal").execute()
                created = len(new_rows)
            except Exception as e:
                # A uniqueness failure means a parallel request already created
                # **some** of the occurrences.
                #
                # A single `insert` is one statement, so one conflicting row rolls
                # back all of them. Assuming the only cause is an identical batch
                # is right only on full overlap: if another request managed to
                # create the salary but not the rent, both are dropped, and
                # `_sync_recurring` marks "synced to today", so nobody retries
                # until tomorrow. A whole month without rent, with no sign at all.
                #
                # So fall back row by row: whatever exists is skipped, the rest
                # goes in.
                if "uq_tx_recurring_occurrence" not in str(e) and "23505" not in str(e):
                    raise
                for row in new_rows:
                    try:
                        client.table("transactions").insert(row, returning="minimal").execute()
                        created += 1
                    except Exception as one:
                        if "uq_tx_recurring_occurrence" in str(one) or "23505" in str(one):
                            continue    # Someone else already created exactly this
                        raise
        _remember_next_due(family_id, templates, today)
        # `created` and not `len(new_rows)`: after a partial failure the numbers
        # differ, and the caller uses this to decide whether anything was
        # really created.
        return created, True
    except Exception:
        logger.exception("materialize_recurring")
        return 0, False


# Speed (Matan, 1.11, idea 5): the recurring check on the day's first load took
# `0.25-0.9` seconds on the home page, and most days there is nothing to do;
# rent and salary are once a month. After every run the date of the next
# recurring charge is stored, and until then the check is skipped. Every
# template change (add, edit, split, import) runs the engine and recomputes.
RECURRING_RECHECK_DAYS = 7


def next_due_date(templates: list, today):
    """The next recurring charge after today, across all templates. `None`: nothing
    is expected."""
    from datetime import timedelta
    tomorrow = today + timedelta(days=1)
    dates = [d for d in (next_recurring_date(t, tomorrow) for t in templates) if d]
    return min(dates) if dates else None


def _remember_next_due(family_id: str, templates: list, today) -> None:
    due = next_due_date(templates, today)
    update_family_settings(family_id, {"recurring_next": {
        "due": due.isoformat() if due else None, "checked": today.isoformat()}})


def _iso_date(value):
    from datetime import date
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        logger.warning("recurring_next: invalid date %r; checking as usual", value)
        return None


def recurring_can_wait(nxt: dict, today) -> bool:
    """Whether today's recurring check can be skipped. `nxt` is
    `settings.recurring_next`: checked less than a week ago, and no recurring
    charge is expected up to and including today."""
    nxt = nxt if isinstance(nxt, dict) else {}
    checked = _iso_date(nxt.get("checked"))
    if checked is None or not 0 <= (today - checked).days < RECURRING_RECHECK_DAYS:
        return False
    if nxt.get("due") is None:
        return True
    due = _iso_date(nxt.get("due"))
    return due is not None and today < due


def projected_recurring_rows(family_id: str, viewer_user_id: str, year: int, month: int) -> list:
    """A future month (Matan, 1.11): the occurrences recurring transactions will
    create in it, with the same dates and amounts the engine will create on the
    day itself (`_recurring_occurrences`, installments, cancelled occurrences),
    until the series ends. Counted in the month's totals and marked
    `projected`: they aren't a transaction yet, so there is nothing to edit.

    (From 4.10 to 1.11 a future month didn't show them at all; Matan decided to
    bring them back, and as part of the total.) Empty on failure: a future
    month without a forecast beats a page that doesn't load."""
    import calendar
    from datetime import date

    client = get_client()
    if not client:
        return []
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    try:
        templates = client.table("transactions") \
            .select("*, categories(name, icon), project_categories(name, icon), "
                    "profiles(name, workplace), projects(owner_id, name, icon)") \
            .eq("family_id", family_id).eq("is_recurring", True) \
            .limit(_MAX_RECURRING_TEMPLATES).execute().data or []
    except Exception:
        logger.exception("projected_recurring_rows")
        return []
    out = []
    for t in _filter_hidden_personal_projects(templates, viewer_user_id):
        skips = {str(x)[:10] for x in (t.get("recurring_skips") or [])}
        plan = None
        if t.get("installments") and t.get("installment_total"):
            plan = installment_plan(t["installment_total"], int(t["installments"]), str(t["date"]))
        first = date.fromisoformat(str(t["date"])[:10])
        for d in _recurring_occurrences(t, end):
            if d < start or d.isoformat() in skips:
                continue
            row = dict(t, id=f"projected-{t['id']}-{d.isoformat()}", date=d.isoformat(),
                       is_recurring=False, recurring_parent_id=t["id"], receipt_path=None,
                       projected=True)
            if plan:
                row.update(amount=plan["base"], installments=plan["count"],
                           installment_no=(d.year - first.year) * 12 + d.month - first.month + 1)
            out.append(row)
    return out


def _occurrence_period(freq: str, d):
    """An occurrence's period key. Two occurrences of the same template in the
    same period are the same transaction, even if their dates differ.

    Without this distinction the dedup compared exact dates, so changing a
    template's date (e.g. a salary moving from the 5th to the 10th of the
    month) produced a new series of dates none of which was recognised, and
    every month back was created again. A ₪14,000 salary was doubled across
    eight months at once, on every screen in the app.

    For a monthly frequency the period is the calendar month. Weekly and
    biweekly have no natural "period", so a distance of up to half a step
    counts as the same occurrence, just moved."""
    if freq in ("weekly", "biweekly"):
        return None                      # handled by distance, see _already_materialized
    return (d.year, d.month)


def existing_occurrence_dates(tx_id: str, family_id: str):
    """The dates the series has already "taken": the occurrences created and the
    skips. `None` if the transaction isn't a recurring template yet, in which
    case none of its occurrences exist.

    For the backfill warning when editing: it counted every occurrence since
    the start of the series, including those that already exist, so fixing the
    description of a salary from March got "the series will create 6
    transactions back". The engine skips a period that is already taken
    (`_already_materialized`), so the count must too."""
    from datetime import date
    client = get_client()
    if not client:
        raise DataUnavailable("existing_occurrence_dates: no client")
    try:
        rows = client.table("transactions").select("is_recurring, recurring_skips") \
            .eq("id", tx_id).eq("family_id", family_id).limit(1).execute().data or []
        if not rows or not rows[0].get("is_recurring"):
            return None
        instances = client.table("transactions").select("date") \
            .eq("recurring_parent_id", tx_id).eq("family_id", family_id) \
            .execute().data or []
    except Exception as e:
        raise DataUnavailable("existing_occurrence_dates") from e
    return [date.fromisoformat(str(r["date"])[:10]) for r in instances] + \
           [date.fromisoformat(str(d)[:10]) for d in (rows[0].get("recurring_skips") or [])]


def _already_materialized(freq: str, d, existing_dates) -> bool:
    """Whether this occurrence already exists, not by exact date but by period."""
    if freq in ("weekly", "biweekly"):
        tolerance = 3 if freq == "weekly" else 7
        return any(abs((d - e).days) <= tolerance for e in existing_dates)
    period = _occurrence_period(freq, d)
    return any(_occurrence_period(freq, e) == period for e in existing_dates)


def _recurring_occurrences(template: dict, until) -> list:
    """The occurrence dates of a recurring template: after the source date, up to
    and including 'until'."""
    import calendar
    from datetime import date, timedelta

    start = date.fromisoformat(str(template["date"]))
    end_raw = template.get("recurring_end_date")
    end = date.fromisoformat(str(end_raw)) if end_raw else None
    freq = template.get("recurring_frequency") or "monthly_1"

    out = []
    if freq in ("monthly_1", "monthly_15", "monthly_same"):
        # monthly_same repeats every month on the day-of-month of the source
        # date (e.g. the 23rd); monthly_1/15 are fixed to the 1st or the 15th
        target_day = {"monthly_1": 1, "monthly_15": 15}.get(freq, start.day)
        # Start from the start month itself: an occurrence in the same month
        # after the start date counts
        y, m = start.year, start.month
        while len(out) < 500:
            # Clamp to the last day of the month (e.g. February 31st → 28/29)
            day = min(target_day, calendar.monthrange(y, m)[1])
            d = date(y, m, day)
            if d > until or (end and d > end):
                break
            if d > start:
                out.append(d)
            m += 1
            if m > 12:
                m, y = 1, y + 1
    else:
        step = timedelta(days=7 if freq == "weekly" else 14)
        d = start + step
        while d <= until and (not end or d <= end) and len(out) < 500:
            out.append(d)
            d += step
    return out


def _add_months(d, n: int):
    """The same day of the month, `n` months ahead, clamped to the end of a short
    month (31 → 28/30)."""
    import calendar
    from datetime import date
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def installment_plan(total: float, count: int, start):
    """Splitting into installments (Matan, 12.10): a regular installment in whole
    agorot, and the remainder in the first one, as card companies do.
    ₪1,000 / 3 → 333.34, 333.33, 333.33.
    `start` is the first installment's date (`date` or string). `None`: the
    amount is too small."""
    from datetime import date
    if isinstance(start, str):
        start = date.fromisoformat(start[:10])
    cents = round(float(total) * 100)
    base = cents // count
    if base <= 0:
        logger.warning("installment_plan: %s in %s payments is too small", total, count)
        return None
    first = cents - base * (count - 1)
    return {"total": round(cents / 100, 2), "count": count, "base": round(base / 100, 2),
            "first": round(first / 100, 2), "end_date": _add_months(start, count - 1).isoformat()}


def installment_label(row: dict) -> str:
    """"תשלום 3 מתוך 12" for a row in an installment series, otherwise empty."""
    n, k = row.get("installments"), row.get("installment_no")
    return f"תשלום {k} מתוך {n}" if n and k else ""


def next_recurring_date(template: dict, today=None):
    """The next charge of a recurring transaction, today or later (Matan, 12.10:
    "הבא: 1.11" in the recurring list in settings). The template itself is the
    first occurrence, so a template whose date hasn't come yet is the next
    one. Ended (`recurring_end_date` passed): `None`."""
    from datetime import date, timedelta
    today = today or clock.today()
    try:
        start = date.fromisoformat(str(template["date"])[:10])
    except (KeyError, ValueError):
        logger.warning("next_recurring_date: bad template date %r", template.get("date"))
        return None
    if start >= today:
        return start
    for d in _recurring_occurrences(template, today + timedelta(days=400)):
        if d >= today:
            return d
    return None


def update_transaction(transaction_id: str, family_id: str, data: dict):
    """Updates a transaction. Returns (updated_row, error)."""
    client = get_client()
    if not client:
        return None, "Database not configured"
    try:
        result = client.table("transactions") \
            .update(data) \
            .eq("id", transaction_id) \
            .eq("family_id", family_id) \
            .execute()
        return (result.data[0] if result.data else None), None
    except Exception as e:
        return None, str(e)


def split_recurring_series(template_id: str, instance_id: str, family_id: str):
    """"Update from now on": splits the series at the edited occurrence.
    Returns (new_template_id, error).

    Until now this updated the template row, but the template is also the
    first month's transaction, so "5,500 from next month" turned January into
    5,500 too. The split itself, and why it looks this way, is documented in
    migration `20260927120000_split_recurring_series`.

    `(None, None)` = nothing to split (the occurrence wasn't found, isn't of
    this template or is another family's, or the series already ended before
    it). The route returns 404 for that."""
    client = get_client()
    if not client:
        return None, "Database not configured"
    try:
        new_id = client.rpc("split_recurring_series", {
            "p_template_id": template_id,
            "p_instance_id": instance_id,
            "p_family_id":   family_id,
        }).execute().data
        return (new_id or None), None
    except Exception as e:
        logger.exception("split_recurring_series")
        return None, str(e)


def recurring_price_changes(family_id: str, templates: list) -> dict:
    """"₪5,000 ← ₪5,500 מאוקטובר" next to a recurring transaction in settings
    (Matan, 3.10, idea 40).

    Changing an amount "from the current month" splits the series
    (`split_recurring_series`): the old one ends a day before the new one
    starts. There is no link between them in the database, so it is
    identified like this: same department, ended exactly a day before the new
    one starts, and the same category or the same description (editing can
    change one of them). The old amount is that of its last occurrence, since
    a single month could have been edited. Only when the amount really differs.

    Returns `{template_id: {"old": amount, "since": "YYYY-MM-DD"}}`.
    Decoration: a failure returns an empty dict."""
    client = get_client()
    if not client or not templates:
        return {}
    try:
        ended = client.table("transactions") \
            .select("id, amount, type, category_id, description, recurring_end_date") \
            .eq("family_id", family_id).eq("is_recurring", True) \
            .not_.is_("recurring_end_date", "null").execute().data or []
        if not ended:
            return {}
        last = client.table("transactions").select("recurring_parent_id, amount, date") \
            .eq("family_id", family_id).in_("recurring_parent_id", [e["id"] for e in ended]) \
            .order("date", desc=True).execute().data or []
    except Exception:
        logger.exception("recurring_price_changes")
        return {}
    last_amount = {}
    for r in last:                                   # newest first: the first per template is its last occurrence
        last_amount.setdefault(r["recurring_parent_id"], float(r["amount"]))
    return price_changes_from(templates, ended, last_amount)


def price_changes_from(templates: list, ended: list, last_amount: dict) -> dict:
    """The part of `recurring_price_changes` after the query, also used by
    `get_settings_page`. `last_amount`: `{ended template id: amount of its last
    occurrence}`."""
    from datetime import date, timedelta
    last_amount = {str(k): float(v) for k, v in (last_amount or {}).items()}
    out = {}
    for t in templates:
        start = date.fromisoformat(str(t["date"])[:10])
        day_before = (start - timedelta(days=1)).isoformat()
        for e in ended:
            if e["type"] != t.get("type") or str(e["recurring_end_date"])[:10] != day_before:
                continue
            if e.get("category_id") != t.get("category_id") and \
                    (e.get("description") or "") != (t.get("description") or ""):
                continue
            old = last_amount.get(str(e["id"]), float(e["amount"]))
            if round(old, 2) != round(float(t["amount"]), 2):
                out[t["id"]] = {"old": old, "since": start.isoformat()}
            break
    return out


def series_pivot(template_id: str, family_id: str, today=None):
    """Editing a recurring transaction from "recurring transactions" in settings
    applies from the current month (Matan, 2.10). Returns where the change
    starts:

    `("template", None)`: the series started in the current month or later:
        no past to protect, edit the template as usual.
    `("instance", (id, date))`: the last occurrence already created in the
        current month.
    `("future", date)`: this month's occurrence hasn't been created yet
        (salary on the 10th, today is the 2nd): its next date.
    `("none", None)`: no more occurrences (the series ended, or the template
        wasn't found).

    The template is also the first month's transaction, so editing it
    rewrote January. It is the same bug "update from now on" fixed
    (`split_recurring_series`), just through the other door. Raises
    DataUnavailable."""
    from datetime import date, timedelta
    client = get_client()
    if not client:
        raise DataUnavailable("series_pivot: no client")
    today = today or clock.today()
    month_start = today.replace(day=1)
    try:
        tpl = _maybe_one(client.table("transactions").select("*")
                         .eq("id", template_id).eq("family_id", family_id).eq("is_recurring", True))
        if not tpl:
            return "none", None
        if date.fromisoformat(str(tpl["date"])[:10]) >= month_start:
            return "template", None
        rows = client.table("transactions").select("id, date") \
            .eq("family_id", family_id).eq("recurring_parent_id", template_id) \
            .gte("date", month_start.isoformat()).order("date", desc=True).limit(1).execute().data or []
    except Exception as e:
        raise DataUnavailable("series_pivot") from e
    if rows:
        return "instance", (rows[0]["id"], str(rows[0]["date"])[:10])
    skips = {str(d)[:10] for d in (tpl.get("recurring_skips") or [])}
    for d in _recurring_occurrences(tpl, today + timedelta(days=400)):
        if d > today and d.isoformat() not in skips:
            return "future", d.isoformat()
    return "none", None


def create_occurrence(template_id: str, family_id: str, on_date: str):
    """Creates an occurrence of a template on a given date, as the engine would
    (the same fields as in `materialize_recurring`). For a "from now on" edit
    when this month's occurrence hasn't been created yet: it is created now,
    and the series splits at it. When the date arrives the engine sees the
    period is taken and doesn't duplicate. Returns (id, error)."""
    client = get_client()
    if not client:
        return None, "Database not configured"
    try:
        t = _maybe_one(client.table("transactions").select("*")
                       .eq("id", template_id).eq("family_id", family_id).eq("is_recurring", True))
        if not t:
            return None, None
        row = client.table("transactions").insert({
            "amount":              t["amount"],
            "type":                t["type"],
            "date":                on_date,
            "description":         t.get("description") or "",
            "category_id":         t.get("category_id"),
            "project_id":          t.get("project_id"),
            "project_category_id": t.get("project_category_id"),
            "created_by":          t.get("created_by"),
            "user_id":             t.get("user_id"),
            "family_id":           family_id,
            "is_recurring":        False,
            "recurring_parent_id": t["id"],
            "recurring_frequency": t.get("recurring_frequency"),
            "workplace":           t.get("workplace"),
        }).execute().data
        return (row[0]["id"] if row else None), None
    except Exception as e:
        logger.exception("create_occurrence")
        return None, str(e)


def project_choice_needed(err) -> int:
    """How many personal projects await a decision, from the removal/leave error;
    0 for any other error. Only the database can count them: they are
    personal, and so hidden from the manager doing the removal (migration
    `20260928120000`)."""
    m = re.search(r"needs_project_choice:(\d+)", err or "")
    return int(m.group(1)) if m else 0


def remove_family_member(user_id: str, keep_transactions: bool = True, projects: str = "ask"):
    """Removes a family member. Family manager only. Returns (ok, error).

    The rules are enforced in the DB, not here: the function derives from
    auth.uid(), checks that the caller is the manager and that the target is
    really in their family, and refuses to remove the manager themselves;
    otherwise two members could remove each other until nobody was left."""
    client = get_client()
    if not client:
        return False, "Database not configured"
    try:
        client.rpc("remove_family_member",
                   {"p_user_id": user_id, "p_keep_transactions": keep_transactions,
                    "p_projects": projects}).execute()
        return True, None
    except Exception as e:
        logger.exception("remove_family_member")
        return False, str(e)


def leave_family(keep_transactions: bool = True, projects: str = "ask"):
    """Leaves the current family and opens a new, empty one.
    Returns (new_family_id, error).

    `projects` (also in `remove_family_member`): what to do with the leaving
    member's personal projects: `share` / `delete` / `ask`. With `ask`, when
    there are any, the error is `needs_project_choice:N` (see
    `project_choice_needed`) and the screen asks."""
    client = get_client()
    if not client:
        return None, "Database not configured"
    try:
        result = client.rpc("leave_family",
                            {"p_keep_transactions": keep_transactions,
                             "p_projects": projects}).execute()
        return (result.data or None), None
    except Exception as e:
        logger.exception("leave_family")
        return None, str(e)


def is_family_manager() -> bool:
    """Whether the signed-in user is their family's manager.

    Through an RPC and not a read of families: the function derives from
    `auth.uid()` rather than a parameter, so it can't be asked about someone
    else. It returns a boolean only: the manager's id isn't exposed to anyone
    who shouldn't see it.

    Raises `DataUnavailable` on failure: "unknown" must be read as "not a
    manager" by the caller, and that direction must be explicit rather than
    the result of a silent `False`."""
    client = get_client()
    if not client:
        raise DataUnavailable("is_family_manager: no client")
    try:
        return bool(client.rpc("is_family_manager", {}).execute().data)
    except Exception as e:
        raise DataUnavailable("is_family_manager") from e


def renew_expired_invite_code():
    """Renews the invite code **only if it is missing or expired**, and returns the
    valid code. Any family member may; it can't cancel a valid code (migration
    20260929120000). Returns (code, error)."""
    client = get_client()
    if not client:
        return None, "Database not configured"
    try:
        code = client.rpc("renew_expired_invite_code", {}).execute().data
        _invalidate_family_cache(get_my_family_id() or "")
        return (code or None), None
    except Exception as e:
        logger.exception("renew_expired_invite_code")
        return None, str(e)


def rotate_invite_code():
    """Replaces the invite code even when it is valid, i.e. cancels invitations
    already sent. Family manager only (enforced in the database). Returns
    (new_code, error)."""
    client = get_client()
    if not client:
        return None, "Database not configured"
    try:
        result = client.rpc("rotate_invite_code", {}).execute()
        _invalidate_family_cache(get_my_family_id() or "")
        return (result.data or None), None
    except Exception as e:
        logger.exception("rotate_invite_code")
        return None, str(e)


def get_my_family_id():
    """The signed-in user's family id, per the DB and not the session."""
    client = get_client()
    if not client:
        return None
    try:
        return client.rpc("get_my_family_id", {}).execute().data
    except Exception:
        logger.exception("get_my_family_id")
        return None


def stop_recurring(transaction_id: str, family_id: str):
    """Stops a recurring series without deleting money. Returns (ok, error).

    "Remove the recurring transaction" in settings promises the user: "new
    occurrences will stop being created. Occurrences already created will
    stay." In practice it ran a full delete of the template row, and the
    template row is a real transaction in every respect, the first occurrence
    of the series, counted in the monthly summary. So the first month's rent
    silently vanished from history, the exact opposite of what the dialog said.

    On top of that, the delete detached every occurrence from the template
    (`on delete set null`), so the dedup stopped seeing them, and whoever
    created the same recurring transaction again got every month a second time.

    Turning the flag off solves both: the row stays as a regular transaction,
    the link survives, and no new occurrences are created."""
    client = get_client()
    if not client:
        return False, "Database not configured"
    try:
        result = client.table("transactions").update({
            "is_recurring":         False,
            "recurring_frequency":  None,
            "recurring_end_date":   None,
        }).eq("id", transaction_id).eq("family_id", family_id).execute()
        if not result.data:
            return False, "not found"
        return True, None
    except Exception as e:
        logger.exception("stop_recurring")
        return False, str(e)


def _maybe_one(query):
    """One row or `None`.

    In our postgrest-py (0.16), `.maybe_single().execute()` returns `None` (not
    a response with `.data = None`) when there is no row. `.execute().data`
    then raised `AttributeError`, which became `DataUnavailable`: deleting a
    transaction another family member had just deleted answered "couldn't
    check whether this is a recurring transaction, try again", and no retry
    helped."""
    result = query.maybe_single().execute()
    return result.data if result is not None else None


def recurring_occurrence(transaction_id: str, family_id: str):
    """What this transaction is within a recurring series. Returns `(info, ok)`
    or raises.

    `info` is `None` for a regular transaction, or `{"template_id", "date",
    "later"}`: the template id, this occurrence's date, and how many
    occurrences there are from it on (inclusive).

    There is no distinction here between "template" and "occurrence", and that
    is the decision: the distinction is entirely internal; the template row is
    a regular transaction in every respect that also happens to define the
    series. A user deleting March's rent shouldn't have to care whether March
    happens to be the month the series was opened."""
    client = get_client()
    if not client:
        raise DataUnavailable("recurring_occurrence: no client")
    try:
        row = _maybe_one(client.table("transactions") \
            .select("id, date, is_recurring, recurring_parent_id") \
            .eq("id", transaction_id).eq("family_id", family_id))
        if not row:
            return None, True
        template_id = row["id"] if row.get("is_recurring") else row.get("recurring_parent_id")
        if not template_id:
            return None, True

        # How many occurrences from this date on, including the template row
        # itself, which is the first occurrence and has no recurring_parent_id
        later = client.table("transactions").select("id", count="exact") \
            .eq("family_id", family_id) \
            .eq("recurring_parent_id", template_id) \
            .gte("date", str(row["date"])).execute().count or 0
        template = client.table("transactions").select("id", count="exact") \
            .eq("family_id", family_id).eq("id", template_id) \
            .gte("date", str(row["date"])).execute().count or 0
        return {"template_id": template_id, "date": str(row["date"]),
                "later": later + template}, True
    except Exception as e:
        raise DataUnavailable("recurring_occurrence") from e


def is_recurring_instance(transaction_id: str, family_id: str) -> bool:
    """Whether the transaction is an occurrence created from a recurring series
    (and not a template in its own right).

    Used for one block only: marking an existing occurrence "recurring" would
    create a second template running alongside the first, forever. Deletion,
    by contrast, makes no distinction between template and occurrence; see
    `recurring_occurrence`."""
    client = get_client()
    if not client:
        raise DataUnavailable("is_recurring_instance: no client")
    try:
        row = _maybe_one(client.table("transactions").select("recurring_parent_id") \
            .eq("id", transaction_id).eq("family_id", family_id))
        return bool(row and row.get("recurring_parent_id"))
    except Exception as e:
        raise DataUnavailable("is_recurring_instance") from e


def delete_one_occurrence(transaction_id: str, template_id: str,
                          occurrence_date: str, family_id: str):
    """Deletes a single occurrence from a recurring series. Returns `(ok, error)`.

    It all happens in one database function, not three calls from here, for
    two reasons:

    **The series barely survived.** When the deleted row is the template
    itself, `recurring_parent_id` (which is `on delete set null`) orphaned all
    the occurrences created from it at once. The function hands the template
    role to the next occurrence instead of killing the series.

    **`recurring_skips` was read-modify-write.** Two family members deleting
    two occurrences in the same second lost one skip, and the transaction came
    back the next day: exactly the bug the column was added to prevent. In the
    database it is one atomic update.

    `template_id` and `occurrence_date` stay in the signature for the callers'
    sake, but the function derives from the row itself, so there is no way it
    acts on one row and records a skip for another.
    """
    client = get_client()
    if not client:
        return False, "Database not configured"
    try:
        result = client.rpc("delete_recurring_occurrence", {
            "p_tx_id":     transaction_id,
            "p_family_id": family_id,
        }).execute()
        deleted = int(result.data or 0)
        return (deleted > 0), (None if deleted else "not found")
    except Exception as e:
        logger.exception("delete_one_occurrence")
        return False, str(e)

def skip_recurring_date(template_id: str, family_id: str, on_date: str, skip: bool = True):
    """Skips a future occurrence in a series (or undoes the skip). Returns
    `(ok, error)`. All in `skip_recurring_date` in the database: one atomic
    update (Matan, 1.11)."""
    client = get_client()
    if not client:
        return False, "Database not configured"
    try:
        result = client.rpc("skip_recurring_date", {
            "p_template_id": template_id, "p_family_id": family_id,
            "p_date": on_date, "p_skip": bool(skip),
        }).execute()
        return (int(result.data or 0) > 0), (None if result.data else "not found")
    except Exception as e:
        logger.exception("skip_recurring_date")
        return False, str(e)

def delete_occurrences_from(template_id: str, occurrence_date: str, family_id: str):
    """Deletes this occurrence and every later one, and stops the series there.
    Returns (deleted, error).

    `recurring_end_date` is set to the day before, and the flag isn't turned
    off: that way the history before the date stays a recognised series, with
    its links and its duplicate protection, instead of becoming a collection
    of orphan rows."""
    from datetime import date, timedelta

    client = get_client()
    if not client:
        return 0, "Database not configured"
    try:
        removed = client.table("transactions").delete() \
            .eq("family_id", family_id) \
            .eq("recurring_parent_id", template_id) \
            .gte("date", occurrence_date).execute().data or []

        cutoff = date.fromisoformat(occurrence_date) - timedelta(days=1)
        template = _maybe_one(client.table("transactions").select("id, date") \
            .eq("id", template_id).eq("family_id", family_id))
        if not template:
            return len(removed), None

        if str(template["date"]) >= occurrence_date:
            # The series is deleted from its start; nothing to keep
            removed += client.table("transactions").delete() \
                .eq("id", template_id).eq("family_id", family_id) \
                .execute().data or []
        else:
            client.table("transactions") \
                .update({"recurring_end_date": cutoff.isoformat()}) \
                .eq("id", template_id).eq("family_id", family_id).execute()
        return len(removed), None
    except Exception as e:
        logger.exception("delete_occurrences_from")
        return 0, str(e)


def delete_transaction(transaction_id: str, family_id: str):
    """Returns `(ok, err)`: `(True, None)`, or `(False, "not found")` when the
    transaction no longer exists (another family member deleted it a moment
    earlier), or `(False, "error")`. The distinction decides what the user sees:
    "already deleted" and the row leaves the screen, versus "delete failed" and
    the row stays."""
    client = get_client()
    if not client:
        return False, "error"
    try:
        # `.data` returns the rows actually deleted (`returning=representation`
        # is the default). Without this check the function returned `True` even
        # when no row was touched (a transaction another family member deleted
        # a second earlier, or another family's id), and the user got "deleted".
        result = client.table("transactions") \
            .delete() \
            .eq("id", transaction_id) \
            .eq("family_id", family_id) \
            .execute()
        return (True, None) if result.data else (False, "not found")
    except Exception:
        # The user does see "delete failed", so it isn't a silent failure, but
        # without the log entry there is no answering "why".
        logger.exception("delete_transaction")
        return False, "error"


# ─── Categories ───────────────────────────────────────────────────────────────

def family_needs_onboarding(family_id: str) -> bool:
    """A family with zero categories hasn't finished onboarding yet
    (brand-new families start with none — see /onboarding)."""
    client = get_client()
    if not client or not family_id:
        return False
    try:
        result = client.table("categories").select("id", count="exact") \
            .eq("family_id", family_id).limit(1).execute()
        return (result.count or 0) == 0
    except Exception as e:
        raise DataUnavailable("family_needs_onboarding") from e


def family_has_no_transactions(family_id: str) -> bool:
    """True if the family never added a transaction. Used to show a friendly
    welcome on the dashboard instead of a wall of ₪0, and not checked against
    the current month (an established family that hasn't entered anything this
    month shouldn't count as 'new')."""
    client = get_client()
    if not client or not family_id:
        return False
    try:
        result = client.table("transactions").select("id", count="exact") \
            .eq("family_id", family_id).limit(1).execute()
        return (result.count or 0) == 0
    except Exception as e:
        raise DataUnavailable("family_has_no_transactions") from e


_MAX_CATEGORIES_PER_FAMILY = 200


def bulk_add_categories(family_id: str, categories: list, start: dict = None) -> tuple:
    """Inserts multiple categories at once for a family's onboarding.
    `categories` is a list of {name, icon, type} dicts. Returns (count, error).
    sort_order follows the order of the input list (within each type
    separately), so the initial order matches what _DEFAULT_CATEGORIES defines."""
    client = get_client()
    if not client:
        return 0, "Database not configured"
    # A cap. The list came from the request body with no limit at all, and with
    # the 8MB cap that is ~100,000 rows in one `insert`; on top of that
    # `_validated_category` scans this list linearly on every transaction write,
    # so every future transaction pays for it.
    #
    # `_DEFAULT_CATEGORIES` is a few dozen; 200 is far above any real wizard.
    if len(categories or []) > _MAX_CATEGORIES_PER_FAMILY:
        return 0, "יותר מדי קטגוריות בבת אחת"

    # `start`: the last order already taken per department: when adding to an
    # existing family (restoring the defaults), the new ones go at the end of
    # the list and don't collide with the first ones
    counters = {"income": 0, "expense": 0, "savings": 0}
    counters.update({k: v for k, v in (start or {}).items() if k in counters})
    rows = []
    for c in categories:
        if not c.get("name") or c.get("type") not in counters:
            continue
        counters[c["type"]] += 1
        rows.append({
            "name": c["name"], "icon": c.get("icon", "📦"), "type": c["type"],
            "family_id": family_id, "is_custom": True, "sort_order": counters[c["type"]],
        })
    if not rows:
        return 0, "No valid categories provided"
    try:
        result = client.table("categories").insert(rows).execute()
        return len(result.data or []), None
    except Exception as e:
        return 0, str(e)


def _fetch_categories(family_id: str = None) -> list:
    client = get_client()
    if not client:
        return []
    try:
        query = client.table("categories").select("*")
        if family_id:
            query = query.or_(f"family_id.is.null,family_id.eq.{family_id}")
        else:
            query = query.is_("family_id", "null")
        return query.order("sort_order", nullsfirst=False).order("name").execute().data
    except Exception as e:
        # An empty list is read by the dashboard as a "new family" and redirects
        # to the signup wizard
        raise DataUnavailable("categories") from e


def get_categories(family_id: str = None) -> list:
    """Cached per request: called 3-4 times on heavy pages (breakdown by category
    ×3), so the result is kept on flask.g for the length of the request."""
    return _request_cache(f"categories:{family_id}", lambda: _fetch_categories(family_id))


def update_category(cat_id: str, family_id: str, name: str, icon: str):
    """Updates a family category's name and icon."""
    client = get_client()
    if not client:
        return False
    try:
        result = client.table("categories") \
            .update({"name": name, "icon": icon}) \
            .eq("id", cat_id) \
            .eq("family_id", family_id) \
            .execute()
        # `.data` returns the rows actually touched. Without this check "saved"
        # was reported even when no row matched: for example when another family
        # member deleted the item a second earlier, or when the id belongs to
        # another family.
        return bool(result.data)
    except Exception:
        logger.exception("update_category")
        return False


def add_custom_category(family_id: str, name: str, icon: str, type_: str):
    client = get_client()
    if not client:
        return None, "Database not configured"
    try:
        existing = client.table("categories").select("sort_order") \
            .eq("family_id", family_id).eq("type", type_).execute().data or []
        next_order = max([c.get("sort_order") or 0 for c in existing], default=0) + 1
        result = client.table("categories").insert({
            "name": name, "icon": icon, "type": type_,
            "family_id": family_id, "is_custom": True, "sort_order": next_order,
        }).execute()
        return result.data[0] if result.data else None, None
    except Exception as e:
        return None, str(e)


def reorder_categories(family_id: str, type_: str, ordered_ids: list) -> bool:
    """Updates sort_order of categories of a given type to the order received."""
    client = get_client()
    if not client:
        return False
    try:
        for i, cat_id in enumerate(ordered_ids, start=1):
            client.table("categories").update({"sort_order": i}) \
                .eq("id", cat_id).eq("family_id", family_id).eq("type", type_).execute()
        return True
    except Exception:
        logger.exception("reorder_categories")
        return False


# ─── Projects (project budgets: shared, or personal to one family member) ───

def project_last_activity(family_id: str) -> dict:
    """Project → the date of its latest transaction (Matan, 12.10: the project in
    progress goes on top). One query, newest first; the first per project is
    the latest. On failure, empty (the order falls back to creation order;
    nothing breaks over ordering)."""
    client = get_client()
    if not client or not family_id:
        return {}
    try:
        rows = client.table("transactions").select("project_id, date") \
            .eq("family_id", family_id).not_.is_("project_id", "null") \
            .order("date", desc=True).limit(1000).execute().data or []
    except Exception:
        logger.exception("project_last_activity")
        return {}
    out = {}
    for r in rows:
        out.setdefault(r["project_id"], str(r.get("date") or "")[:10])
    return out


def get_projects(family_id: str, viewer_user_id: str, archived=False) -> list:
    """Projects visible to the current viewer: all shared projects + the personal
    projects that belong to them. Another family member's personal project
    isn't included here at all: that is the requested privacy (not just hidden
    in the view, but never fetched).

    `archived`: `False` for the active ones (the default, every ordinary list);
    `True` for "finished projects"; `None` for all, marked (the transaction
    form, for an old transaction)."""
    client = get_client()
    if not client or not family_id:
        return []
    try:
        query = client.table("projects").select("*").eq("family_id", family_id)
        if archived is not None:
            query = query.eq("archived", bool(archived))
        projects = query.order("created_at", desc=True).execute().data
        return projects_from_rows(projects, _project_totals(family_id), viewer_user_id)
    except Exception as e:
        logger.exception("get_projects")
        raise DataUnavailable("get_projects") from e


def projects_from_rows(projects: list, totals: dict, viewer_user_id: str) -> list:
    """The project list from rows and totals already fetched, also used by
    `get_projects_page`. Another family member's personal project isn't
    included (privacy is within the family)."""
    visible = [p for p in projects if not p.get("owner_id") or p["owner_id"] == viewer_user_id]
    out = []
    for p in visible:
        t = totals.get(p["id"], {"expense": 0.0, "income": 0.0, "savings": 0.0})
        # The "current amount" shown in the list: net, income+savings minus expenses
        net = t["income"] + t["savings"] - t["expense"]
        budget = p.get("budget_target")
        out.append({
            "id": p["id"], "name": p["name"],
            "description": p.get("description"),
            "icon": p.get("icon"),
            "is_personal": bool(p.get("owner_id")),
            "owner_id": p.get("owner_id"),
            "budget_target": float(budget) if budget is not None else None,
            "amount": round(net, 2),
            # For the card in the list (Matan, 12.10): "₪X left of ₪Y", the same
            # calculation as on the project page (`spent` = expenses only)
            "spent": round(t["expense"], 2),
            "saved": round(t["savings"], 2),
            "track_expense": p.get("track_expense", True),
            "track_income": p.get("track_income", False),
            "track_savings": p.get("track_savings", False),
            "archived": bool(p.get("archived")),
        })
    return out


def _project_totals(family_id: str) -> dict:
    """The all-time total per project, broken down by transaction type
    (expense/income/savings).

    Computed in the database (RPC `project_totals`), not in Python. The
    previous version pulled *every* project transaction of the family, ever, on
    every load of the settings and projects pages, and summed them here. For a
    family with one trip that is already 56 rows pulled to get one number, and
    the number grows forever.

    The RPC is `security invoker`, so RLS still applies and no door was opened
    here to see another family's projects."""
    client = get_client()
    if not client:
        return {}
    rows = client.rpc("project_totals", {"p_family_id": family_id}).execute().data or []
    return {
        r["project_id"]: {
            "expense": float(r["expense"] or 0),
            "income":  float(r["income"] or 0),
            "savings": float(r["savings"] or 0),
        }
        for r in rows
    }


def add_project(family_id: str, name: str, created_by: str, budget_target: float = None,
                owner_id: str = None, description: str = None, icon: str = None,
                track_expense: bool = True, track_income: bool = False, track_savings: bool = False):
    client = get_client()
    if not client:
        return None, "Database not configured"
    try:
        result = client.table("projects").insert({
            "family_id": family_id, "name": name, "budget_target": budget_target,
            "owner_id": owner_id, "created_by": created_by, "description": description,
            "icon": icon, "track_expense": track_expense,
            "track_income": track_income, "track_savings": track_savings,
        }).execute()
        project = result.data[0] if result.data else None
        if project:
            types = [t for t, on in (("expense", track_expense), ("income", track_income),
                                     ("savings", track_savings)) if on]
            _seed_project_categories(project["id"], family_id, types)
        return project, None
    except Exception as e:
        return None, str(e)


def set_project_archived(project_id: str, family_id: str, archived: bool) -> bool:
    """"Project finished" / "reopen" (Matan, 30.9). The transactions aren't
    touched, only the flag, which takes the project out of the lists and the
    add form. `False` when no row was touched (deleted meanwhile, or not the
    family's)."""
    client = get_client()
    if not client:
        return False
    try:
        res = client.table("projects").update({"archived": bool(archived)}) \
            .eq("id", project_id).eq("family_id", family_id).execute()
        return bool(res.data)
    except Exception:
        logger.exception("set_project_archived")
        return False


def update_project(project_id: str, family_id: str, name: str, budget_target: float = None,
                   description: str = None, icon: str = None, track_expense: bool = True,
                   track_income: bool = False, track_savings: bool = False):
    """Updates name/target/tracked types only. Changing ownership
    (personal/shared) happens only through the dedicated
    share_project/unshare_project, not here."""
    client = get_client()
    if not client:
        return False
    try:
        rows = client.table("projects") \
            .select("track_expense, track_income, track_savings") \
            .eq("id", project_id).eq("family_id", family_id).limit(1).execute().data
        if not rows:
            return None                 # Deleted meanwhile: the route says so (404)
        existing = rows[0]
        # Types just switched on for the first time; seed starter categories for them
        newly_enabled = [
            t for t, before, after in (
                ("expense", existing.get("track_expense"), track_expense),
                ("income", existing.get("track_income"), track_income),
                ("savings", existing.get("track_savings"), track_savings),
            ) if after and not before
        ]

        result = client.table("projects").update({
            "name": name, "budget_target": budget_target, "description": description,
            "icon": icon, "track_expense": track_expense, "track_income": track_income,
            "track_savings": track_savings,
        }).eq("id", project_id).eq("family_id", family_id).execute()
        # See update_category
        if not result.data:
            return False

        if newly_enabled:
            _seed_project_categories(project_id, family_id, newly_enabled)
        return True
    except Exception:
        logger.exception("update_project")
        return False


def share_project(project_id: str, family_id: str, user_id: str):
    """Turns a personal project into a shared one: opened to every family member,
    and every transaction already attached to it becomes shared (user_id=NULL),
    per Matan's request that moving to shared carries the expenses/income
    themselves too. Only the current owner may do this."""
    client = get_client()
    if not client:
        return False, "Database not configured"
    try:
        # Not `.single()`: on a project deleted a moment earlier it raised, and
        # the user got a generic error instead of "deleted meanwhile" (review of 1.10)
        rows = client.table("projects").select("owner_id") \
            .eq("id", project_id).eq("family_id", family_id).limit(1).execute().data
        proj = rows[0] if rows else None
        if not proj:
            return False, "הפרויקט לא נמצא — ייתכן שנמחק בינתיים"
        if proj.get("owner_id") != user_id:
            return False, "רק הבעלים של הפרויקט יכול להפוך אותו למשותף"
        client.table("projects").update({"owner_id": None}) \
            .eq("id", project_id).eq("family_id", family_id).execute()
        client.table("transactions").update({"user_id": None}) \
            .eq("project_id", project_id).eq("family_id", family_id).execute()
        return True, None
    except Exception as e:
        return False, str(e)


def unshare_project(project_id: str, family_id: str, user_id: str):
    """Turns a shared project back into a personal one: only whoever originally
    created the project (created_by) may do this, even if it is currently
    shared and everyone has access to it."""
    client = get_client()
    if not client:
        return False, "Database not configured"
    try:
        rows = client.table("projects").select("owner_id, created_by") \
            .eq("id", project_id).eq("family_id", family_id).limit(1).execute().data
        proj = rows[0] if rows else None
        if not proj:
            return False, "הפרויקט לא נמצא — ייתכן שנמחק בינתיים"
        if proj.get("owner_id"):
            return False, "הפרויקט כבר אישי"
        if proj.get("created_by") != user_id:
            return False, "רק מי שיצר את הפרויקט יכול להחזיר אותו להיות אישי"
        client.table("projects").update({"owner_id": user_id}) \
            .eq("id", project_id).eq("family_id", family_id).execute()
        return True, None
    except Exception as e:
        return False, str(e)


def delete_project(project_id: str, family_id: str):
    """Deletes the project, its categories (ON DELETE CASCADE), and the
    transactions in it.

    There used to be a choice to "keep the transactions", and the screen
    promised they would "return to their regular category". A project
    transaction has no regular category: it landed in household expenses as
    "uncategorised" (ON DELETE SET NULL), and the months it was recorded in
    became more expensive after the fact: a ₪40,000 renovation over three
    months. Matan decided (28.9.2026): deleting a project deletes its
    transactions. Project money isn't counted in household expenses anyway, so
    no household amount moves. Every deleted transaction goes through the
    trigger into the internal archive."""
    client = get_client()
    if not client:
        return False, 0
    try:
        # First make sure the project still exists: otherwise "not found" would
        # come back after the transactions were already deleted, exactly when
        # another family member deleted it a second earlier.
        exists = client.table("projects").select("id") \
            .eq("id", project_id).eq("family_id", family_id).limit(1).execute().data
        if not exists:
            return False, 0
        gone = client.table("transactions").delete() \
            .eq("project_id", project_id).eq("family_id", family_id).execute()
        wiped = len(gone.data or [])
        result = client.table("projects").delete() \
            .eq("id", project_id).eq("family_id", family_id).execute()
        # `(ok, wiped)` and not `True`: this is the most destructive action any
        # member can take without a password or manager rights, and the user
        # needs to see how many transactions really vanished, just like the
        # transaction reset.
        return bool(result.data), wiped
    except Exception:
        logger.exception("delete_project")
        return False, 0


def get_project_for_transaction(project_id: str, family_id: str):
    """The minimal fields needed to validate and enforce when adding/updating a
    transaction attached to a project: owner_id (to enforce automatic
    attribution) and the tracking flags (to check the type is supported)."""
    client = get_client()
    if not client:
        return None
    try:
        return client.table("projects") \
            .select("owner_id, track_expense, track_income, track_savings") \
            .eq("id", project_id).eq("family_id", family_id).single().execute().data
    except Exception:
        # Both callers read None as "project not found" and refuse: the safe
        # direction. But to the user it looks as if an existing project vanished,
        # and that is exactly the report that can't be investigated without a
        # traceback.
        logger.exception("get_project_for_transaction")
        return None


def get_project_detail(project_id: str, family_id: str, viewer_user_id: str) -> dict:
    """Project details + all its transactions, across all months together. If it
    is a personal project belonging to another family member, returns None
    (full access block, not just hiding)."""
    client = get_client()
    if not client or not family_id:
        return None
    # A malformed id (a truncated link): PostgREST would reject it with an
    # error, and that is a 503
    if not _UUID_RE.match(str(project_id)):
        return None
    try:
        # Not `.single()`: it raises on zero rows, which turned a deleted project
        # into an error page ("try refreshing") instead of "doesn't exist"
        found = client.table("projects").select("*") \
            .eq("id", project_id).eq("family_id", family_id).limit(1).execute().data
        if not found:
            return None
        proj = found[0]
        if proj.get("owner_id") and proj["owner_id"] != viewer_user_id:
            return None

        result = client.table("transactions") \
            .select("*, categories(name, icon), project_categories(name, icon), profiles(name, workplace)") \
            .eq("family_id", family_id).eq("project_id", project_id) \
            .order("date", desc=True).execute()
        transactions = _format_transactions(result.data)

        totals = {"expense": 0.0, "income": 0.0, "savings": 0.0}
        # Breakdown by category per type, computed from the transactions already
        # fetched (no extra query)
        grouped = {"expense": {}, "income": {}, "savings": {}}
        for t in transactions:
            typ = t["type"]
            if typ not in totals:
                continue
            totals[typ] += t["amount"]
            key = t.get("category_name") or "אחר"
            entry = grouped[typ].setdefault(
                key, {"name": key, "icon": t.get("category_icon") or "📦", "total": 0.0})
            entry["total"] += t["amount"]

        breakdown = {}
        for typ, cats in grouped.items():
            items = [c for c in cats.values() if c["total"] > 0]
            tot = sum(c["total"] for c in items) or 1
            for c in items:
                c["pct"] = round(c["total"] / tot * 100)
                c["total"] = round(c["total"], 2)
            items.sort(key=lambda c: c["total"], reverse=True)
            breakdown[typ] = items

        budget = proj.get("budget_target")
        spent = totals["expense"]
        return {
            "id": proj["id"], "name": proj["name"],
            "description": proj.get("description"),
            "icon": proj.get("icon"),
            "is_personal": bool(proj.get("owner_id")),
            "owner_id": proj.get("owner_id"),
            "created_by": proj.get("created_by"),
            "track_expense": proj["track_expense"], "track_income": proj["track_income"],
            "track_savings": proj["track_savings"],
            "archived": bool(proj.get("archived")),
            "budget_target": float(budget) if budget is not None else None,
            "spent": round(spent, 2),
            "income": round(totals["income"], 2),
            "savings": round(totals["savings"], 2),
            "remaining": round(float(budget) - spent, 2) if budget is not None else None,
            "breakdown": breakdown,
            "transactions": transactions,
        }
    except Exception as e:
        logger.exception("get_project_detail")
        raise DataUnavailable("get_project_detail") from e


# ─── Project categories (per project, separate from the family's categories) ─

def _seed_project_categories(project_id: str, family_id: str, types: list):
    """Seeds starter categories for a project: a copy of the family's regular
    categories of the same types, as a default that can be edited, deleted or
    added to without affecting the family's original categories."""
    if not types:
        return
    client = get_client()
    if not client:
        return
    try:
        # Turning tracking off doesn't delete the categories, so turning it back
        # on seeded them again: "salary" twice. Whatever the project already has
        # (name+type) is not copied.
        existing = {(c["name"], c["type"]) for c in client.table("project_categories")
                    .select("name, type").eq("project_id", project_id)
                    .eq("family_id", family_id).execute().data}
        family_cats = [c for c in get_categories(family_id) if c.get("type") in types
                       and (c["name"], c["type"]) not in existing]
        if not family_cats:
            return
        rows = [{
            "project_id": project_id, "family_id": family_id,
            "name": c["name"], "icon": c.get("icon", "📦"), "type": c["type"],
        } for c in family_cats]
        client.table("project_categories").insert(rows).execute()
    except Exception:
        logger.exception("_seed_project_categories")


def get_project_categories(project_id: str, family_id: str, type_: str = None) -> list:
    client = get_client()
    if not client:
        return []
    try:
        query = client.table("project_categories").select("*") \
            .eq("project_id", project_id).eq("family_id", family_id)
        if type_:
            query = query.eq("type", type_)
        return query.order("name").execute().data
    except Exception as e:
        logger.exception("get_project_categories")
        raise DataUnavailable("get_project_categories") from e


def add_project_category(project_id: str, family_id: str, name: str, icon: str, type_: str):
    client = get_client()
    if not client:
        return None, "Database not configured"
    try:
        result = client.table("project_categories").insert({
            "project_id": project_id, "family_id": family_id,
            "name": name, "icon": icon, "type": type_,
        }).execute()
        return (result.data[0] if result.data else None), None
    except Exception as e:
        return None, str(e)


# ─── Deleting a category: no transaction is left uncategorised ──────────────
#
# A plain delete left the category's transactions "uncategorised" (the FK is
# `ON DELETE SET NULL`). Now they are moved to another category **before** the
# delete, and the `transactions_category_required` constraint in the database
# catches the race: a transaction added to the category between the move and
# the delete makes the delete fail instead of being orphaned.

# (the categories table, the column in transactions that points at it)
_CATEGORY_KINDS = {
    "family":  ("categories", "category_id"),
    "project": ("project_categories", "project_category_id"),
}


def category_deletion_plan(kind: str, cat_id: str, family_id: str, project_id: str = None):
    """What happens if the category is deleted: `{"category", "count",
    "alternatives"}`, or `None` if it doesn't exist (or belongs to another
    family/project).

    `alternatives`: the categories of the same type, in the same place, that
    the transactions can be moved to."""
    table, column = _CATEGORY_KINDS[kind]
    client = get_client()
    if not client:
        raise DataUnavailable("category_deletion_plan")
    try:
        def scoped(q):
            q = q.eq("family_id", family_id)
            return q.eq("project_id", project_id) if kind == "project" else q

        found = scoped(client.table(table).select("*").eq("id", cat_id)).execute().data
        if not found:
            return None
        category = found[0]
        siblings = scoped(client.table(table).select("id, name, icon, type")
                          .eq("type", category.get("type"))).execute().data
        used = client.table("transactions").select("id", count="exact") \
            .eq("family_id", family_id).eq(column, cat_id).limit(1).execute()
        return {
            "category": category,
            "count": used.count or 0,
            "alternatives": [{"id": c["id"], "name": c["name"], "icon": c.get("icon") or "📦"}
                             for c in siblings if c["id"] != cat_id],
        }
    except Exception as e:
        logger.exception("category_deletion_plan")
        raise DataUnavailable("category_deletion_plan") from e


def delete_category_moving(kind: str, cat_id: str, family_id: str,
                           move_to: str = None, project_id: str = None) -> bool:
    """Moves the category's transactions to `move_to` and then deletes it.
    Checking that the target is valid is the caller's job
    (`category_deletion_plan`)."""
    table, column = _CATEGORY_KINDS[kind]
    client = get_client()
    if not client:
        return False
    try:
        if move_to:
            client.table("transactions").update({column: move_to}) \
                .eq("family_id", family_id).eq(column, cat_id).execute()
        q = client.table(table).delete().eq("id", cat_id).eq("family_id", family_id)
        q = q.eq("project_id", project_id) if kind == "project" else q.eq("is_custom", True)
        return bool(q.execute().data)
    except Exception:
        logger.exception("delete_category_moving")
        return False

def update_project_category(cat_id: str, project_id: str, family_id: str, name: str, icon: str):
    client = get_client()
    if not client:
        return False
    try:
        result = client.table("project_categories").update({"name": name, "icon": icon}) \
            .eq("id", cat_id).eq("project_id", project_id).eq("family_id", family_id).execute()
        # `.data` returns the rows actually touched. Without this check "saved"
        # was reported even when no row matched: for example when another family
        # member deleted the item a second earlier, or when the id belongs to
        # another family.
        return bool(result.data)
    except Exception:
        logger.exception("update_project_category")
        return False


def delete_project_category(cat_id: str, project_id: str, family_id: str) -> bool:
    client = get_client()
    if not client:
        return False
    try:
        result = client.table("project_categories").delete() \
            .eq("id", cat_id).eq("project_id", project_id).eq("family_id", family_id).execute()
        # `.data` returns the rows actually touched. Without this check "saved"
        # was reported even when no row matched: for example when another family
        # member deleted the item a second earlier, or when the id belongs to
        # another family.
        return bool(result.data)
    except Exception:
        logger.exception("delete_project_category")
        return False


# ─── Analytics ───────────────────────────────────────────────────────────────

# ─── One month, one query ───────────────────────────────────────────────────
#
# The month page made 12 database calls, and seven of them read exactly the
# same rows: the summary, three breakdowns by category and two by family
# member, all subsets of the transactions already fetched for the "all
# transactions" list.
#
# The time (about 180 milliseconds) isn't the main point. The main point is
# that each of the seven re-derived the same two rules for itself: excluding
# project transactions, and hiding another family member's personal project.
# The second rule was already forgotten once, and that is recorded in a code
# comment: a project transaction showed up inside a monthly category without
# being counted in its total.
#
# One query with clearly named derivations makes this kind of bug impossible:
# there is nowhere to forget the rule, because it is applied once.

def fetch_month_page(family_id: str, year: int, month: int) -> dict:
    """Everything the month page needs, in one trip to the database.

    Replaces five queries that ran in sequence (settings, members, categories,
    the month's rows, archive); see the note in `_run_queries` in app.py on why
    they run in sequence and not in parallel, and migration
    `20260923120000_month_page_fn.sql` on why the answer is fewer trips rather
    than parallel ones.

    The processing stays here and wasn't moved to SQL on purpose:
    `_merge_settings` and name shortening are the same code the rest of the app
    goes through, and duplicating them in the database would create two sources
    of truth that could silently drift apart.

    `null` from the function = the family isn't the caller's, or the query
    failed. Both are `DataUnavailable`: neither is "a family with no data"."""
    client = get_client()
    if not client:
        raise DataUnavailable("fetch_month_page: no client")
    try:
        data = client.rpc("get_month_page", {
            "p_family_id": family_id,
            "p_year":      year,
            "p_month":     month,
        }).execute().data
    except Exception as e:
        raise DataUnavailable("fetch_month_page") from e

    if not data:
        raise DataUnavailable("fetch_month_page: empty")

    # Same as `_fetch_family_members`: the templates show a first name, and the
    # full name is kept next to it because some places show it in full.
    members = data.get("members") or []
    for m in members:
        m["full_name"] = m.get("name", "")
        m["name"] = first_name(m.get("name", ""))

    family = data.get("family") or {}
    return {
        "family":     family,
        "settings":   _merge_settings(DEFAULT_FAMILY_SETTINGS, family.get("settings") or {}),
        "members":    members,
        "categories": data.get("categories") or [],
        "rows":       data.get("rows") or [],
        "archive":    data.get("archive") or [],
    }


def fetch_home_page(family_id: str, today, recent_limit: int, viewer_user_id: str) -> dict:
    """Everything the home page needs, in one trip to the database (Matan, 13.10).

    Replaces nine queries that ran in sequence; see migration
    `20261013120000_home_page_fn.sql`. Like `fetch_month_page`: only the
    transport changed; the calculations are the same functions
    (`summary_from_rows`, `week_from_rows`, `recent_from_rows`) the separate
    queries go through.

    `null` = the family isn't the caller's, or the query failed:
    `DataUnavailable`."""
    client = get_client()
    if not client:
        raise DataUnavailable("fetch_home_page: no client")
    week_from, week_start, week_to = week_bounds(today)
    # Safety margin as in `get_recent_transactions`: private rows get filtered out
    fetch_limit = recent_limit * 3 if viewer_user_id else recent_limit
    try:
        data = client.rpc("get_home_page", {
            "p_family_id":    family_id,
            "p_year":         today.year,
            "p_month":        today.month,
            "p_recent_from":  today.replace(day=1).isoformat(),
            "p_recent_limit": fetch_limit,
            "p_week_from":    week_from.isoformat(),
            "p_week_start":   week_start.isoformat(),
            "p_week_to":      week_to.isoformat(),
        }).execute().data
    except Exception as e:
        raise DataUnavailable("fetch_home_page") from e
    if not data:
        raise DataUnavailable("fetch_home_page: empty")

    members = data.get("members") or []
    for m in members:
        m["full_name"] = m.get("name", "")
        m["name"] = first_name(m.get("name", ""))
    family = data.get("family") or {}
    settings = _merge_settings(DEFAULT_FAMILY_SETTINGS, family.get("settings") or {})
    rows = data.get("rows") or []
    return {
        "family":     family,
        "settings":   settings,
        "members":    members,
        "categories": data.get("categories") or [],
        "rows":       rows,
        # Same as `get_monthly_summary`: no project transactions
        "summary":    summary_from_rows([r for r in rows if not r.get("project_id")]),
        "recent":     data.get("recent") or [],
        "week":       week_from_rows(data.get("week_rows") or [],
                                     data.get("week_has_older"), today),
        "is_new":     not data.get("has_transactions"),
    }


def fetch_settings_page(family_id: str, today, viewer_user_id: str) -> dict:
    """Everything the settings page needs from the database, in one trip (Matan,
    13.10); see `20261013120100_settings_page_fn.sql`. The calculations are the
    same functions (`category_averages_from_totals`, `price_changes_from`,
    `_format_transactions`).

    `null` = the family isn't the caller's, or the query failed:
    `DataUnavailable`."""
    client = get_client()
    if not client:
        raise DataUnavailable("fetch_settings_page: no client")
    avg_from, avg_to = averages_window(today)
    try:
        data = client.rpc("get_settings_page", {
            "p_family_id": family_id, "p_avg_from": avg_from, "p_avg_to": avg_to,
        }).execute().data
    except Exception as e:
        raise DataUnavailable("fetch_settings_page") from e
    if not data:
        raise DataUnavailable("fetch_settings_page: empty")

    members = data.get("members") or []
    for m in members:
        m["full_name"] = m.get("name", "")
        m["name"] = first_name(m.get("name", ""))
    family = data.get("family") or {}
    settings = _merge_settings(DEFAULT_FAMILY_SETTINGS, family.get("settings") or {})
    return {
        "family":       family,
        "settings":     settings,
        "members":      members,
        "categories":   data.get("categories") or [],
        "cat_averages": category_averages_from_totals(data.get("category_totals")),
        # Same as `get_recurring_transactions`
        "recurring":    _format_transactions(
            _filter_hidden_personal_projects(data.get("recurring") or [], viewer_user_id), settings),
        "ended":        data.get("ended") or [],
        "ended_last":   data.get("ended_last") or {},
        "tx_count":     int(data.get("transaction_count") or 0),
    }


def _members_first_names(members: list) -> list:
    """Same as `_fetch_family_members`: a first name for display, and the full
    one next to it."""
    for m in members:
        m["full_name"] = m.get("name", "")
        m["name"] = first_name(m.get("name", ""))
    return members


def fetch_projects_page(family_id: str, viewer_user_id: str) -> dict:
    """The projects page in one trip (Matan, 13.10); see
    `20261013130000_projects_and_months_page_fn.sql`. `DataUnavailable` on failure."""
    client = get_client()
    if not client:
        raise DataUnavailable("fetch_projects_page: no client")
    try:
        data = client.rpc("get_projects_page", {"p_family_id": family_id}).execute().data
    except Exception as e:
        raise DataUnavailable("fetch_projects_page") from e
    if not data:
        raise DataUnavailable("fetch_projects_page: empty")
    family = data.get("family") or {}
    # Same as `_project_totals`
    totals = {pid: {k: float((t or {}).get(k) or 0) for k in ("expense", "income", "savings")}
              for pid, t in (data.get("totals") or {}).items()}
    rows = data.get("projects") or []
    return {
        "family":   family,
        "settings": _merge_settings(DEFAULT_FAMILY_SETTINGS, family.get("settings") or {}),
        "members":  _members_first_names(data.get("members") or []),
        "active":   projects_from_rows([p for p in rows if not p.get("archived")], totals, viewer_user_id),
        "finished": projects_from_rows([p for p in rows if p.get("archived")], totals, viewer_user_id),
        "last_activity": {pid: str(d or "")[:10] for pid, d in (data.get("last_activity") or {}).items()},
    }


def months_window(today) -> tuple:
    """`(from, until-exclusive)`: the 12 months up to the current one, the window
    of `monthly_trend`."""
    last = today.year * 12 + today.month - 1
    first = last - 11
    nxt = last + 1
    return (f"{first // 12:04d}-{first % 12 + 1:02d}-01", f"{nxt // 12:04d}-{nxt % 12 + 1:02d}-01")


def fetch_months_page(family_id: str, today) -> dict:
    """The comparison page in one trip (Matan, 13.10). The window's expenses are
    fetched once and feed both "by category" and "by family member".
    `DataUnavailable` on failure."""
    client = get_client()
    if not client:
        raise DataUnavailable("fetch_months_page: no client")
    start, end = months_window(today)
    try:
        data = client.rpc("get_months_page", {
            "p_family_id": family_id, "p_from": start, "p_to": end,
        }).execute().data
    except Exception as e:
        raise DataUnavailable("fetch_months_page") from e
    if not data:
        raise DataUnavailable("fetch_months_page: empty")
    family = data.get("family") or {}
    return {
        "family":       family,
        "settings":     _merge_settings(DEFAULT_FAMILY_SETTINGS, family.get("settings") or {}),
        "members":      _members_first_names(data.get("members") or []),
        "categories":   data.get("categories") or [],
        "archive":      data.get("archive") or [],
        "expense_rows": data.get("expense_rows") or [],
    }


def fetch_month_rows(family_id: str, year: int, month: int) -> list:
    """All the month's rows, including project transactions, with all their links.

    This is the query the whole month page is derived from. It also returns
    project transactions; the derivations below decide which ignore them and
    which show them."""
    client = get_client()
    if not client:
        raise DataUnavailable("fetch_month_rows: no client")
    try:
        return client.table("transactions") \
            .select("*, categories(name, icon), project_categories(name, icon), "
                    "profiles(name, workplace), projects(owner_id, name, icon)") \
            .eq("family_id", family_id) \
            .gte("date", f"{year}-{month:02d}-01") \
            .lt("date", _next_month(year, month)) \
            .order("date", desc=True) \
            .execute().data or []
    except Exception as e:
        raise DataUnavailable("fetch_month_rows") from e


def _household_rows(rows: list) -> list:
    """Only transactions not attached to a project.

    This is the rule all the monthly summaries share: a project is a one-off
    expense that distorts the picture of a "normal month", so it is shown
    separately. Applied here once instead of in seven queries."""
    return [r for r in rows if not r.get("project_id")]


def summary_from_rows(rows: list) -> dict:
    """The same summary as get_monthly_summary, from rows already in hand."""
    summary = _empty_summary()
    for row in _household_rows(rows):
        t = row["type"]
        if t in ("income", "expense", "savings"):
            summary[t] += float(row["amount"])
    for k in ("income", "expense", "savings"):
        summary[k] = round(summary[k], 2)
    summary["balance"]   = round(summary["income"] - summary["expense"], 2)
    summary["remaining"] = round(summary["balance"] - summary["savings"], 2)
    total = summary["income"] or 1
    summary["expense_pct"] = round(summary["expense"] / total * 100)
    return summary


# The bucket for transactions that lost their category (the category was
# deleted: `ON DELETE SET NULL`). They used to merge silently into the family's
# "other" category, because grouping was by name; now they are a separate
# bucket and it is visible that some money needs assigning.
_NO_CATEGORY = "ללא קטגוריה"


def category_breakdown_from_rows(rows: list, categories: list, type_: str) -> list:
    """The total per category of the requested type.

    Grouped by the category's **id**, not its name. A name is not a key: the
    database has no uniqueness constraint on category names, and two different
    categories with the same name would merge into one row nobody asked for.
    That is also what silenced the category budgets: they are stored by id,
    and the rows coming out of here didn't carry it at all, so
    `apply_budgets` searched and **never** found anything.

    All of the type's categories always appear, even in a month with no data
    for them; otherwise a category vanishes from the page exactly in the month
    you spent nothing on it, and it looks as if it was deleted."""
    buckets = {c["id"]: {"category_id": c["id"], "name": c["name"],
                         "icon": c.get("icon", "📦"), "total": 0.0}
               for c in categories if c.get("type") == type_ and c.get("id")}

    for row in _household_rows(rows):
        if row["type"] != type_:
            continue
        cat = row.get("categories") or {}
        key = row.get("category_id")
        if key not in buckets:
            # A category not in the list (deleted, or belonging to another family
            # through old data): use the name stamped on the row itself
            buckets[key] = {
                "category_id": key,
                "name": cat.get("name") or _NO_CATEGORY,
                "icon": cat.get("icon") or "📦",
                "total": 0.0,
            }
        buckets[key]["total"] += float(row["amount"])

    grand = sum(b["total"] for b in buckets.values()) or 1
    out = [{**b, "total": round(b["total"], 2),
            "pct": round(b["total"] / grand * 100)} for b in buckets.values()]
    out.sort(key=lambda x: (-x["total"], x["name"]))
    return out


def _month_before(year: int, month: int) -> tuple:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def month_recap(family_id: str, today, categories: list):
    """The "September closed" card on the home page (Matan, 13.10).

    Only on the first day of the month ("the next day it goes away by itself").
    Returns `None` on any other day, and for a past month with no transactions
    at all (nothing to sum up). Household expenses only, no projects, like
    every monthly summary. Raises `DataUnavailable`.

    `diff`: last month's expenses minus the month before (negative = you spent
    less); `None` when the month before had no expenses, because "₪14,950 more
    than August" against an empty August is not a comparison."""
    if today.day != 1:
        return None
    year, month = _month_before(today.year, today.month)
    rows = fetch_month_rows(family_id, year, month)
    if not _household_rows(rows):
        return None
    summary = summary_from_rows(rows)
    before = get_monthly_summary(family_id, *_month_before(year, month))
    top = next((c for c in category_breakdown_from_rows(rows, categories, "expense")
                if c["total"] > 0), None)
    return {
        "year": year, "month": month,
        "income": summary["income"], "expense": summary["expense"],
        "balance": summary["balance"], "savings": summary["savings"],
        "diff": round(summary["expense"] - before["expense"], 2) if before["expense"] else None,
        "top": top,
    }


# A salary category is recognised by name, like the default choice in the form
# (`transactions.js`)
_SALARY_WORD = "משכורת"


def income_sources(transactions: list) -> list:
    """"Income: where it came from" on the month page (Matan, 30.9): a row per
    salary by whoever it is recorded under ("משכורת מתן", "משכורת אור"), and one
    row "other income" for everything else. Salaries first, largest to
    smallest; the other income last.

    Takes the month's household transactions (no projects, like
    `month_transactions`). A salary with no owner (attribution off, or shared)
    is just "salary", with no name."""
    salaries, extra = {}, None
    for t in transactions:
        if t.get("type") != "income":
            continue
        if _SALARY_WORD in (t.get("category_name") or ""):
            key = t.get("user_id") or ""
            src = salaries.setdefault(key, {
                "kind": _SALARY_WORD, "who": t.get("user_name") if t.get("user_id") else "",
                "icon": t.get("category_icon") or "💼", "total": 0.0, "transactions": []})
        else:
            if extra is None:
                extra = {"kind": "הכנסות נוספות", "who": "", "icon": "➕",
                         "total": 0.0, "transactions": []}
            src = extra
        src["total"] += float(t["amount"])
        src["transactions"].append(t)
    out = sorted(salaries.values(), key=lambda s: -s["total"])
    if extra:
        out.append(extra)
    for src in out:
        src["total"] = round(src["total"], 2)
        src["label"] = f'{src["kind"]} {src["who"]}'.strip()
    return out


def member_breakdown_from_rows(rows: list, type_: str) -> list:
    """The total per family member. Derived only for the types the family turned
    attribution on for.

    Grouped by user_id and not by name, so each family member's colour stays
    fixed even if two share a first name. Transactions with no owner are
    gathered together under "shared"."""
    members: dict = {}
    for row in _household_rows(rows):
        if row["type"] != type_:
            continue
        uid = row.get("user_id")
        profile = row.get("profiles")
        name = first_name(profile["name"]) if profile and profile.get("name") else "משותפת"
        key = uid or "__shared__"
        if key not in members:
            members[key] = {"user_id": uid, "name": name, "expense": 0.0}
        members[key]["expense"] += float(row["amount"])
    return sorted(members.values(), key=lambda x: x["expense"], reverse=True)


def project_breakdown_from_rows(rows: list) -> list:
    """Groups the month's project transactions by project.

    The section used to show a flat list of all project transactions together,
    so in a month with a renovation and a trip the eye couldn't separate them,
    and that is exactly the question ("how much did the renovation cost me this
    month?"). Here each project is one row.

    Grouped by **id** and not by name: two projects can have the same name,
    and grouping by name would merge their money, exactly the bug the category
    breakdown had.

    Each type is kept separately and not netted into one amount: a project with
    a refund would show as "cost less", and ₪0 on a project that both spent
    and received is a number that hides both sides. Ordering is by what was
    actually spent.
    """
    by_project = {}
    for row in rows:
        key = row.get("project_id")
        if not key:
            continue
        entry = by_project.setdefault(key, {
            "project_id":   key,
            "name":         row.get("project_name") or "פרויקט",
            "icon":         row.get("project_icon") or "🎯",
            "expense": 0.0, "income": 0.0, "savings": 0.0,
            "transactions": [],
        })
        if row["type"] in ("expense", "income", "savings"):
            entry[row["type"]] += float(row["amount"])
        entry["transactions"].append(row)

    out = []
    for entry in by_project.values():
        for kind in ("expense", "income", "savings"):
            entry[kind] = round(entry[kind], 2)
        entry["outflow"] = round(entry["expense"] + entry["savings"], 2)
        out.append(entry)

    out.sort(key=lambda e: (e["outflow"], e["income"]), reverse=True)
    return out


def category_filter_chips(transactions: list) -> dict:
    """The category row that opens under "expenses"/"income"/"savings" in "all
    transactions" (Matan, 30.9, option A): for each type, the categories it
    had this month with their totals, largest first.

    Every transaction gets a `cat_key`: the key the browser filters by. By id
    and not name, like every breakdown on the page; a project category gets a
    prefix so it doesn't mix with a regular category."""
    groups = {}
    for tx in transactions:
        if tx.get("category_id"):
            key = str(tx["category_id"])
        elif tx.get("project_category_id"):
            key = f"pc-{tx['project_category_id']}"
        else:
            key = "none"
        tx["cat_key"] = key
        chip = groups.setdefault(tx.get("type"), {}).setdefault(key, {
            "key": key, "name": tx.get("category_name") or _NO_CATEGORY,
            "icon": tx.get("category_icon") or "📦", "total": 0.0})
        chip["total"] += float(tx.get("amount") or 0)
    return {t: sorted(({**c, "total": round(c["total"], 2)} for c in by.values()),
                      key=lambda c: (-c["total"], c["name"]))
            for t, by in groups.items()}


def month_transactions_from_rows(rows: list, settings: dict = None,
                                 viewer_user_id: str = None) -> list:
    """The transaction list for display: includes projects, hides another
    member's personal project."""
    return _format_transactions(
        _filter_hidden_personal_projects(rows, viewer_user_id), settings)


def monthly_trend(archive: list, num_months: int = 12, today=None) -> list:
    """The trend chart on the comparison page, derived from the archive table
    under it rather than fetched separately.

    It was fetched separately until now, and three times it agreed with the
    table only roughly: once it forgot to exclude projects (July ₪31,400 in the
    chart, ₪9,400 in the table), once it stopped at "today" and left out the
    rent due on the 30th of the current month, and once it fetched thousands of
    transactions that got cut at `max_rows`. Now it is the same source, so they
    can't drift apart.

    Only the 12 months up to the current one: a payment made in advance for
    November doesn't create a bar in "the last 12 months". Oldest to newest, as
    the chart draws."""
    hebrew_months = ["", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
                     "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
    today = today or clock.today()
    last = today.year * 12 + today.month - 1
    first = last - num_months + 1

    trend = []
    for m in archive or []:
        y, mo = int(m["year"]), int(m["month"])
        if not first <= y * 12 + mo - 1 <= last:
            continue
        trend.append({
            "key":        f"{y}-{mo:02d}",
            "year":       y,
            "month":      mo,
            "month_name": hebrew_months[mo],
            **{k: round(float(m.get(k) or 0), 2) for k in ("income", "expense", "savings")},
        })
    trend.sort(key=lambda t: t["key"])
    return trend


def trend_verdict(trend: list, today=None):
    """The takeaway line above the comparison chart (Matan, 12.10): the last
    finished month against the average of up to 3 months before it, in words,
    without decoding bars.

    The current month isn't judged: mid-month every month looks "thrifty". No
    earlier month to compare with, or nothing was spent in it: `None`, and no
    line."""
    today = today or clock.today()
    done = [t for t in trend or [] if (t["year"], t["month"]) < (today.year, today.month)]
    if len(done) < 2 or done[-1]["expense"] <= 0:
        return None
    last = done[-1]
    before = [t["expense"] for t in done[-4:-1] if t["expense"] > 0]
    if not before:
        return None
    avg = sum(before) / len(before)
    pct = round((last["expense"] - avg) / avg * 100)
    # In whole shekels: "average ₪11,066.67" is precision nobody needs in a sentence
    return {"month_name": last["month_name"], "expense": round(last["expense"]), "avg": round(avg),
            "count": len(before), "pct": abs(pct),
            "direction": "same" if abs(pct) < 3 else ("up" if pct > 0 else "down")}


_SHORT_MONTHS = ["", "ינו׳", "פבר׳", "מרץ", "אפר׳", "מאי", "יוני",
                 "יולי", "אוג׳", "ספט׳", "אוק׳", "נוב׳", "דצמ׳"]


def category_trend(family_id: str, months: list, categories: list, today=None) -> dict:
    """The "by category" card on the comparison page (Matan, 30.9): how much went
    to each category in each of `months`, the same months as the chart above,
    oldest to newest.

    Household expenses only (no projects), like every monthly summary. The
    average and the most expensive month come from finished months: the
    current month isn't over yet, and it would pull the average down and look
    "thrifty" in every mid-month. A category without a single shekel in the
    window is left out."""
    today = today or clock.today()
    if not months:
        return {"months": [], "categories": []}
    client = get_client()
    if not client:
        return {"months": [], "categories": []}
    (y0, m0), (y1, m1) = months[0], months[-1]
    try:
        rows = _fetch_all(lambda: client.table("transactions")
                          .select("id, amount, category_id, date, description")
                          .eq("family_id", family_id)
                          .eq("type", "expense")
                          .is_("project_id", "null")
                          .gte("date", f"{y0}-{m0:02d}-01")
                          .lt("date", _next_month(y1, m1))
                          .order("id"))
    except Exception as e:
        raise DataUnavailable("category_trend") from e
    return category_trend_from_rows(rows, months, categories, today)


def category_trend_from_rows(rows: list, months: list, categories: list, today=None) -> dict:
    """The part of `category_trend` after the query, also used by
    `get_months_page`. Rows outside `months` are dropped, so a wider window
    doesn't change the result."""
    today = today or clock.today()
    if not months:
        return {"months": [], "categories": []}
    index = {(y, m): i for i, (y, m) in enumerate(months)}
    names = {c["id"]: c for c in categories}
    totals, items = {}, {}
    for r in rows:
        d = str(r["date"])
        i = index.get((int(d[:4]), int(d[5:7])))
        if i is None:
            continue
        key = r.get("category_id") or "none"
        totals.setdefault(key, [0.0] * len(months))[i] += float(r["amount"])
        # The transactions themselves, for the window that opens on tapping a
        # month's bar (Matan, 30.9)
        items.setdefault(key, [[] for _ in months])[i].append(
            {"date": d[:10], "description": r.get("description") or "", "amount": float(r["amount"])})

    finished = [i for i, (y, m) in enumerate(months) if (y, m) < (today.year, today.month)]
    out = []
    for key, vals in totals.items():
        vals = [round(v, 2) for v in vals]
        cat = names.get(key) or {}
        done = [vals[i] for i in finished]
        top = max(finished, key=lambda i: vals[i]) if finished else None
        out.append({
            "key": key,
            "name": cat.get("name") or _NO_CATEGORY,
            "icon": cat.get("icon") or "📦",
            "values": vals,
            "avg": round(sum(done) / len(done), 2) if done else None,
            "max": vals[top] if top is not None else None,
            "max_label": _FULL_MONTHS[months[top][1]] if top is not None else None,
            # "This month" only when the last month in the window really is the
            # current month
            "current": vals[-1] if months[-1] == (today.year, today.month) else None,
            "total": round(sum(vals), 2),
            "items": [sorted(m, key=lambda x: x["date"], reverse=True) for m in items[key]],
        })
    out.sort(key=lambda c: (-c["total"], c["name"]))
    return {"months": [{"year": y, "month": m, "label": _SHORT_MONTHS[m], "name": _FULL_MONTHS[m]}
                       for y, m in months],
            "categories": out}


def member_trend(family_id: str, months: list) -> list:
    """"Expenses by family member" over the months, on the comparison page
    (Matan, 3.10, idea 15).

    Household expenses only (no projects), like the chart on the month page
    (`member_breakdown_from_rows`): by `user_id`, and without an owner,
    "shared". Returns `[{"user_id", "name", "values": [...]}]`, a value per
    month in `months`, largest first. Raises DataUnavailable."""
    if not months:
        return []
    client = get_client()
    if not client:
        raise DataUnavailable("member_trend: no client")
    (y0, m0), (y1, m1) = months[0], months[-1]
    try:
        rows = _fetch_all(lambda: client.table("transactions")
                          .select("id, amount, date, user_id, profiles(name)")
                          .eq("family_id", family_id).eq("type", "expense").is_("project_id", "null")
                          .gte("date", f"{y0}-{m0:02d}-01").lt("date", _next_month(y1, m1))
                          .order("id"))
    except Exception as e:
        raise DataUnavailable("member_trend") from e
    return member_trend_from_rows(rows, months)


def member_trend_from_rows(rows: list, months: list) -> list:
    """The part of `member_trend` after the query, also used by `get_months_page`."""
    if not months:
        return []
    index = {(y, m): i for i, (y, m) in enumerate(months)}
    out: dict = {}
    for r in rows:
        d = str(r["date"])
        i = index.get((int(d[:4]), int(d[5:7])))
        if i is None:
            continue
        uid = r.get("user_id")
        profile = r.get("profiles")
        key = uid or "__shared__"
        if key not in out:
            out[key] = {"user_id": uid,
                        "name": first_name(profile["name"]) if uid and profile and profile.get("name") else "משותפת",
                        "values": [0.0] * len(months)}
        out[key]["values"][i] += float(r["amount"])
    for m in out.values():
        m["values"] = [round(v, 2) for v in m["values"]]
    return sorted(out.values(), key=lambda m: -sum(m["values"]))


def category_monthly_averages(family_id: str, today=None) -> dict:
    """The monthly average per category, for every department, for the line
    "₪1,850 a month on average" in settings (Matan, 3.10, idea 38). The last
    three **whole** months, divided by 3, without projects. A category with no
    expense at all in the window isn't in the dict. Raises DataUnavailable."""
    start, end = averages_window(today or clock.today())
    client = get_client()
    if not client:
        raise DataUnavailable("category_monthly_averages: no client")
    try:
        rows = _fetch_all(lambda: client.table("transactions").select("amount, category_id")
                          .eq("family_id", family_id).is_("project_id", "null")
                          .gte("date", start).lt("date", end).order("id"))
    except Exception as e:
        raise DataUnavailable("category_monthly_averages") from e
    totals: dict = {}
    for r in rows:
        if r.get("category_id"):
            totals[str(r["category_id"])] = totals.get(str(r["category_id"]), 0.0) + float(r["amount"])
    return category_averages_from_totals(totals)


def averages_window(today) -> tuple:
    """`(from, until-exclusive)`: the last three whole months, without the
    current month."""
    end = f"{today.year:04d}-{today.month:02d}-01"
    sm, sy = today.month - _HISTORY_MONTHS, today.year
    while sm <= 0:
        sm += 12
        sy -= 1
    return f"{sy:04d}-{sm:02d}-01", end


def category_averages_from_totals(totals: dict) -> dict:
    """The window total per category → a rounded monthly average. Without
    categories that spent nothing."""
    return {str(cid): round(float(t) / _HISTORY_MONTHS)
            for cid, t in (totals or {}).items() if float(t) > 0}


# How many months back go into the average. The constant lives here because
# both the query and the calculation must agree on it; otherwise "the
# three-month average" is computed over a different window than was fetched.
_HISTORY_MONTHS = 3


def budget_alerts(breakdown: list) -> list:
    """Alerts for categories that went over budget.

    `budget_alert` used to be a decision separate from "has a budget", with its
    own checkbox. In practice it was a distinction without a difference: anyone
    who bothered to set a budget wants to know when they went over it,
    otherwise it is just a number on the screen, and the checkbox was dropped.
    The field stays in the data, defaulting to `True` (see `category_budget`),
    so budgets saved in the old shape keep behaving correctly."""
    out = []
    for row in breakdown:
        if not (row.get("budget_over") and row.get("budget_alert")):
            continue
        out.append({
            "severity": "warning",
            "text": (f'{row.get("icon") or "📦"} {row["name"]}: '
                     f'₪{format_money(row["total"])} מתוך תקציב של ₪{format_money(row["budget"])} '
                     f'— חריגה של ₪{format_money(row["budget_excess"])}'),
            "target": category_anchor(row.get("category_id")),
        })
    return out


def category_anchor(category_id) -> str:
    """The anchor of an expense category on the month page. A transaction with no
    category has no row of its own with an id, so it links to the whole
    expense breakdown."""
    return f"cat-{category_id}" if category_id else "expense-breakdown"


def month_alerts(expense_breakdown: list) -> list:
    """The month's alerts, for the home page: only a category going over budget
    (Matan, 11.10). "Higher than average" and the balance alerts (expenses
    above income, negative balance) were removed: whoever wants alerts sets a
    budget, and the balance is already red at the top of the home page.

    `expense_breakdown` after `apply_budgets`."""
    return budget_alerts(expense_breakdown)


# ─── Family members ───────────────────────────────────────────────────────────

def _fetch_family_members(family_id: str) -> list:
    client = get_client()
    if not client:
        return []
    try:
        result = client.rpc("get_family_members", {"p_family_id": family_id}).execute()
        members = result.data or []
        for m in members:
            m["full_name"] = m.get("name", "")
            m["name"] = first_name(m.get("name", ""))
        return members
    except Exception as e:
        logger.exception("get_family_members")
        raise DataUnavailable("_fetch_family_members") from e


def get_family_members(family_id: str) -> list:
    """Cached per request (see _request_cache): called several times on one page."""
    return _request_cache(f"members:{family_id}", lambda: _fetch_family_members(family_id))


def update_family_name(family_id: str, name: str):
    client = get_client()
    if not client:
        return False
    try:
        result = client.table("families").update({"name": name}).eq("id", family_id).execute()
        _invalidate_family_cache(family_id)
        # See update_category: "saved" on no row is a small lie that adds up.
        return bool(result.data)
    except Exception:
        logger.exception("update_family_name")
        return False


def _fetch_family(family_id: str) -> dict:
    client = get_client()
    if not client:
        return {}
    try:
        result = client.table("families").select("*").eq("id", family_id).single().execute()
        return result.data or {}
    except Exception as e:
        # {} merges into the defaults, and the transaction is recorded under
        # another family member
        raise DataUnavailable("family") from e


def get_family(family_id: str) -> dict:
    """Cached per request (see _request_cache): called both directly and through
    the settings."""
    return _request_cache(f"family:{family_id}", lambda: _fetch_family(family_id))


def family_name_for_code(code: str):
    """The family name behind an invite code, or None if the code doesn't exist,
    for a preview ("מצטרפים למשפחת כהן?") before the user confirms."""
    client = get_client()
    if not client or not code:
        return None
    try:
        result = client.rpc("family_name_for_code", {"p_code": code}).execute()
        return result.data or None
    except Exception:
        logger.exception("family_name_for_code")
        return None


def family_transaction_count(family_id: str) -> int:
    """How many transactions the family has. Used to warn before moving to another
    family: the transactions stay tied to the old family through
    transactions.family_id, so whoever leaves a family with history loses
    access to it."""
    client = get_client()
    if not client:
        return 0
    try:
        result = client.table("transactions") \
            .select("id", count="exact") \
            .eq("family_id", family_id) \
            .limit(1).execute()
        return result.count or 0
    except Exception as e:
        # 0 reads as "nothing to lose" and skips the leave-family confirmation
        raise DataUnavailable("family_transaction_count") from e


def join_family_by_code(code: str):
    """Joins the signed-in user to a family by invite code.

    Goes through an RPC with security definer and not a direct SELECT: the RLS
    policy families_member_read allows reading a family only to an existing
    member, and a new joiner by definition isn't a member yet, so a direct
    check always fails. See migration 20260914120000_family_invite_code.sql.

    Returns (family_id, error). error is None on success, and a Hebrew string
    otherwise, so the caller can tell "joined" from "wrong code" instead of
    swallowing it."""
    client = get_client()
    if not client:
        return None, "בסיס הנתונים אינו זמין כרגע"
    if not code or not code.strip():
        return None, "לא הוזן קוד הזמנה"
    try:
        result = client.rpc("join_family_by_code", {"p_code": code}).execute()
        family_id = result.data
        if not family_id:
        # "Or expired": a code is valid for a week, and someone who got an old
        # code checked again and again that they copied it right. Without telling
        # the two apart: "expired" would confirm to a stranger who guessed that
        # the code was once real.
            return None, "הקוד לא נמצא או שפג תוקפו — בקשו ממנהל המשפחה קוד חדש"
        return family_id, None
    except Exception:
        logger.exception("join_family_by_code")
        return None, "ההצטרפות נכשלה — נסו שוב בעוד כמה רגעים"


# ─── Archive (months list) ────────────────────────────────────────────────────

def get_months_archive(family_id: str) -> list:
    """Returns a list of {year, month, income, expense, savings, balance} dicts."""
    client = get_client()
    if not client:
        return []
    try:
        result = client.rpc("get_months_archive", {"p_family_id": family_id}).execute()
        return result.data or []
    except Exception as e:
        logger.exception("get_months_archive")
        raise DataUnavailable("get_months_archive") from e


# ─── Helpers ──────────────────────────────────────────────────────────────────

def first_name(full_name: str) -> str:
    """Family members share a surname, so the UI shows first names only."""
    return (full_name or "").strip().split(" ")[0]


def _empty_summary():
    return {"income": 0.0, "expense": 0.0, "savings": 0.0, "balance": 0.0,
            "remaining": 0.0, "expense_pct": 0}


def _next_month(year: int, month: int) -> str:
    if month == 12:
        return f"{year + 1}-01-01"
    return f"{year}-{month + 1:02d}-01"


def _format_transactions(rows: list, settings: dict = None) -> list:
    cfg = settings or DEFAULT_FAMILY_SETTINGS
    # Workplace relies on attributing income to a family member; without income
    # attribution there is no one to show it for. No setting of its own since
    # 11.10 (Matan): when income attribution is on, it is shown.
    show_workplace = cfg.get("owner_attribution", {}).get("income", True)
    out = []
    for row in rows:
        # A transaction attached to a project uses that project's own category
        # (project_categories), not the family's regular categories
        if row.get("project_category_id"):
            cat = row.get("project_categories") or {}
        else:
            cat = row.get("categories") or {}
        user = row.get("profiles") or {}
        proj = row.get("projects") or {}
        out.append({
            "id":                   row["id"],
            "type":                 row["type"],
            "amount":               float(row["amount"]),
            "description":          row.get("description") or "",
            "date":                 str(row["date"]),
            "category_id":          row.get("category_id"),
            "project_category_id":  row.get("project_category_id"),
            "category_name":        cat.get("name", "אחר"),
            "category_icon":        cat.get("icon", "📦"),
            "user_id":              row.get("user_id"),
            "user_name":            first_name(user.get("name", "")) if row.get("user_id") else "משותפת",
            # Workplace is shown only on salary income.
            # The value frozen on the transaction itself (row.workplace) is
            # preferred, so a future workplace change doesn't accidentally rewrite
            # history; NULL (old transactions from before the feature) falls back
            # to a live lookup from the profile as before.
            "workplace":            ((row.get("workplace") or user.get("workplace"))
                                     if show_workplace and row["type"] == "income"
                                        and "משכורת" in cat.get("name", "")
                                     else None),
            "is_recurring":         row.get("is_recurring", False),
            "recurring_frequency":  row.get("recurring_frequency"),
            # "תשלום 3 מתוך 12" (Matan, 12.10)
            "installments":         row.get("installments"),
            "installment_no":       row.get("installment_no"),
            "recurring_end_date":   str(row["recurring_end_date"]) if row.get("recurring_end_date") else None,
            # The id of the recurring template that created this occurrence (None
            # for a regular transaction or a template itself). Used for smart
            # sync: offering to update the template too when an occurrence's
            # amount changes.
            "recurring_parent_id":  row.get("recurring_parent_id"),
            "project_id":           row.get("project_id"),
            # Who *recorded* the transaction (not whose money it is; that's
            # `user_id`). `None` = unknown.
            "created_by":           row.get("created_by"),
            "project_name":         proj.get("name"),
            "project_icon":         proj.get("icon"),
            "has_receipt":          bool(row.get("receipt_path")),
            # A projected occurrence of a recurring transaction in a future month;
            # not a transaction yet (`projected_recurring_rows`)
            "projected":            bool(row.get("projected")),
        })
    return out


# ─── Year in review (Matan, 1.11) ───────────────────────────────────────────

# Change in a category against the previous year, only when at least this much
# went to it last year. ₪40 → ₪90 is a "+125%" that says nothing.
_YEAR_COMPARE_MIN = 500


def year_recap_due(today):
    """Which year the home-page card sums up: January 1–15, otherwise `None`. Not
    from December: "the year isn't over yet" (Matan, 1.11)."""
    if today.month == 1 and today.day <= 15:
        return today.year - 1
    return None


def year_recap_partial(year: int, today) -> bool:
    """"2026 so far" (Matan, 1.11, option B): the current year is always a
    checkpoint halfway through, even in December. The full summary only once
    the year is over."""
    return year == today.year


def year_recap(family_id: str, viewer_user_id: str, year: int, today=None) -> dict:
    """"Your 2026": a year summary that opens on tap (Matan, 1.11).

    The household's income, expenses and savings (no projects, like every
    summary), the thriftiest and the most expensive month, the biggest
    categories, the year's projects, and against the previous year **only when
    the previous year is full** (it has a January). One query for both years,
    in pages. Another family member's personal project isn't included. Raises
    `DataUnavailable`."""
    client = get_client()
    if not client:
        raise DataUnavailable("year_recap: no client")
    try:
        rows = _fetch_all(lambda: client.table("transactions")
                          .select("id, amount, type, date, category_id, project_id, "
                                  "categories(name, icon), projects(owner_id, name, icon)")
                          .eq("family_id", family_id)
                          .gte("date", f"{year - 1}-01-01").lt("date", f"{year + 1}-01-01")
                          .order("id"))
    except Exception as e:
        raise DataUnavailable("year_recap") from e
    rows = _filter_hidden_personal_projects(rows, viewer_user_id)
    today = today or clock.today()
    partial = year_recap_partial(year, today)
    cur = [r for r in rows if str(r["date"]).startswith(str(year))]
    prev = [r for r in rows if not str(r["date"]).startswith(str(year))]
    if partial:
        # Up to today, and against **the same period** last year, not a whole year
        upto = today.isoformat()[5:]
        cur = [r for r in cur if str(r["date"])[5:10] <= upto]
        prev = [r for r in prev if str(r["date"])[5:10] <= upto]

    months = []
    for m in range(1, 13):
        in_month = [r for r in cur if int(str(r["date"])[5:7]) == m]
        s = summary_from_rows(in_month)
        months.append({"month": m, "income": s["income"], "expense": s["expense"],
                       "savings": s["savings"], "balance": s["balance"],
                       "has_data": bool(_household_rows(in_month))})
    active = [m for m in months if m["has_data"]]
    totals = summary_from_rows(cur)

    def by_category(rs):
        out = {}
        for r in _household_rows(rs):
            if r["type"] != "expense":
                continue
            cat = r.get("categories") or {}
            key = r.get("category_id") or ""
            c = out.setdefault(key, {"name": cat.get("name") or "ללא קטגוריה",
                                     "icon": cat.get("icon") or "📦", "total": 0.0})
            c["total"] += float(r["amount"])
        return out

    cats = by_category(cur)
    top = sorted(cats.values(), key=lambda c: -c["total"])[:3]
    for c in top:
        c["total"] = round(c["total"], 2)
        c["share"] = round(c["total"] / totals["expense"] * 100) if totals["expense"] else 0

    compare = None
    if any(str(r["date"])[5:7] == "01" for r in _household_rows(prev)):
        before = summary_from_rows(prev)
        old = by_category(prev)
        changes = []
        for key, c in old.items():
            if c["total"] < _YEAR_COMPARE_MIN:
                continue
            now_total = cats.get(key, {}).get("total", 0.0)
            changes.append({"name": c["name"], "icon": c["icon"],
                            "pct": round((now_total - c["total"]) / c["total"] * 100)})
        up = max(changes, key=lambda c: c["pct"], default=None)
        down = min(changes, key=lambda c: c["pct"], default=None)
        compare = {"year": year - 1,
                   "expense_diff": round(totals["expense"] - before["expense"], 2),
                   "savings_diff": round(totals["savings"] - before["savings"], 2),
                   "up": up if up and up["pct"] > 0 else None,
                   "down": down if down and down["pct"] < 0 else None}

    projects = {}
    for r in cur:
        if not r.get("project_id") or r["type"] != "expense":
            continue
        p = r.get("projects") or {}
        e = projects.setdefault(r["project_id"], {"name": p.get("name") or "פרויקט",
                                                  "icon": p.get("icon") or "🎯", "total": 0.0})
        e["total"] += float(r["amount"])
    projects = sorted(({**p, "total": round(p["total"], 2)} for p in projects.values()),
                      key=lambda p: -p["total"])

    return {
        "year": year,
        "partial": partial,
        "through": today.month if partial else 12,
        "income": totals["income"], "expense": totals["expense"], "savings": totals["savings"],
        "savings_rate": round(totals["savings"] / totals["income"] * 100) if totals["income"] else None,
        "count": len(cur),
        "months": months,
        "best": max(active, key=lambda m: m["balance"])["month"] if active else None,
        "costliest": max(active, key=lambda m: m["expense"])["month"] if active else None,
        "top": top,
        "projects": projects,
        "compare": compare,
    }
