from flask import (Flask, render_template, request, jsonify, session, redirect,
                   url_for, g, make_response, Response)
from dotenv import load_dotenv
from functools import wraps
from datetime import date, datetime, timedelta
from werkzeug.middleware.proxy_fix import ProxyFix
import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import secrets
import time
from . import supabase_config as db
from .money import format_money
from .icons import ui_icon, empty_illus
from .wording import count_of, day_label, share_map, greeting, next_charge_label, HEBREW_MONTHS as _HEBREW_MONTHS
from . import clock
from . import logs
from . import notify
from . import card_import
from . import push
from . import passwords

logs.setup()
logger = logs.get("smartfin.app")

load_dotenv()

# ─── Error monitoring ───────────────────────────────────────────────────────
# Without this, an error a user hits reaches error.html and disappears there: the
# print to Railway's logs gets wiped and nobody reads it. With one user that is
# tolerable because they find out themselves; with a hundred it means a
# frustrated user leaves and we have no idea why.
# Active only when SENTRY_DSN is set, so local development and existing
# deployments are unaffected.
# Variable and field names that must never leave the server. Checked against
# the actual names in the code: password/current_password in the auth routes,
# access_token/refresh_token in the session, image_bytes/b64 in receipt scanning.
_SECRET_NAMES = (
    "password", "passwd", "secret", "token", "authorization", "apikey",
    "api_key", "image_bytes", "b64", "encrypted_password",
)


def _looks_secret(name: str) -> bool:
    return any(s in str(name).lower() for s in _SECRET_NAMES)


def _scrub_event(event, hint):
    """Scrubs the event before it leaves the server.

    The app handles financial data, and the receipt-scanning route carries a
    photo of a real receipt: none of that should reach an external service.

    The previous scrubbing touched only event["request"], and that wasn't
    enough: the SDK attaches to every frame in the stack trace the local
    variables that were in memory at that moment. In practice, any exception in
    the sign-in, signup, password-reset or account-deletion routes would send the
    password itself; an exception in inject_auth would send Supabase tokens; and
    an exception in receipt scanning would send the image.

    include_local_variables=False already turns that off in the SDK, and the
    scrubbing here is the second layer, exactly as the original comment meant,
    but in the places where the data really is: extra, breadcrumbs, contexts
    and query_string, none of which were touched before."""
    request_data = event.get("request") or {}
    request_data.pop("data", None)
    request_data.pop("cookies", None)
    # The query string carries ?code=<invite code> in /api/family/preview
    request_data.pop("query_string", None)
    headers = request_data.get("headers") or {}
    for sensitive in ("Cookie", "Authorization"):
        headers.pop(sensitive, None)

    # Local variables in every frame: the path that was wide open
    for value in (event.get("exception") or {}).get("values") or []:
        for frame in (value.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)

    # extra / contexts: values the code sends explicitly, today or in the future
    for section in ("extra", "contexts"):
        holder = event.get(section)
        if isinstance(holder, dict):
            for key in list(holder):
                if _looks_secret(key):
                    holder[key] = "[scrubbed]"

    # breadcrumbs: the SDK records the requests and queries that preceded the error
    for crumb in event.get("breadcrumbs") or []:
        if not isinstance(crumb, dict):
            continue
        data = crumb.get("data")
        if isinstance(data, dict):
            for key in list(data):
                if _looks_secret(key):
                    data[key] = "[scrubbed]"

    return event


_sentry_dsn = os.environ.get("SENTRY_DSN")
if _sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration

    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[FlaskIntegration()],
        send_default_pii=False,   # no IP addresses, cookies or request body
        # The default is True, and it is what attached passwords and tokens to
        # every report
        include_local_variables=False,
        traces_sample_rate=0.0,   # errors only: performance tracing costs money and isn't needed here
        environment=os.environ.get("RAILWAY_ENVIRONMENT_NAME", "local"),
        before_send=_scrub_event,
    )


_BASE = os.path.dirname(__file__)
app = Flask(
    __name__,
    template_folder=os.path.join(_BASE, '..', 'frontend', 'templates'),
    static_folder=os.path.join(_BASE, '..', 'frontend', 'static'),
    static_url_path='/static',
)

# The session signing key must come from the environment, with no fallback;
# otherwise cookies can be forged with a known public key. Fail at startup
# instead of carrying on silently.
_secret = os.environ.get("SECRET_KEY")
if not _secret:
    raise RuntimeError("SECRET_KEY environment variable is required — refusing to start without it")
app.secret_key = _secret

# Users stay signed in until they sign out themselves. The cookie lives 90 days,
# and SESSION_REFRESH_EACH_REQUEST (Flask's default, explicit here) pushes the
# expiry forward on every request, so an active user never reaches it.
# What actually makes this work is the refresh-token refresh in inject_auth:
# Supabase's access token lives about an hour, and the session survives
# because it is replaced automatically.
#
# The number really only applies to an **abandoned** session: a sold device, a
# lost phone, or a browser on a shared computer. It used to be ten years, i.e.
# those never got closed.
app.permanent_session_lifetime = timedelta(days=90)

# Cookie hardening: the cookie carries Supabase tokens, so Secure is mandatory
# in production (in local development over http it would break sign-in, hence
# conditional).
_IS_DEV = os.environ.get("FLASK_ENV") == "development"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",   # also basic CSRF protection
    SESSION_COOKIE_SECURE=not _IS_DEV,
    SESSION_REFRESH_EACH_REQUEST=True,   # the expiry window moves forward with every use
    PREFERRED_URL_SCHEME="https" if not _IS_DEV else "http",
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,  # request size cap: protects receipt uploads
)

# Behind Railway's proxy (TLS termination), so request.host_url returns https in
# password-reset links. Harmless in local development.
#
# x_for is essential for rate limiting, not just convenience: without it
# remote_addr is Railway's edge node and not the user. Measured in practice: 12
# requests from one computer were split across four different proxy addresses,
# so the limit wasn't enforced. Worse, all users share the same addresses, so
# one noisy user could lock sign-in for everyone.
#
# The value 2 was measured, not guessed: Railway adds two layers, and
# X-Forwarded-For arrives as "<client>, <edge node>". ProxyFix counts from the
# right, so 1 returned the edge node and 2 returns the client. Counting from the
# right is also what prevents spoofing: a value the client pushes itself is
# pushed left and never chosen.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=2, x_proto=1, x_host=1)


# First of all `before_request` hooks: a warm-up ping won't run in parallel with
# a request (`db.request_started`)
@app.before_request
def _db_request_started():
    db.request_started()


@app.teardown_request
def _db_request_finished(exc):
    db.request_finished()

# Rate limiting on the auth routes: prevents password guessing and phone-number
# scanning.
#
# The storage must be shared in production. With "memory://" each gunicorn worker
# counts for itself, so the limit is multiplied by the number of workers, and
# every deploy resets the counters; i.e. the protection on sign-in, signup and
# password reset is much looser than what the code says. That was enough when
# this was a family app; once the URL is shared, it isn't.
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

_RATELIMIT_STORAGE = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=_RATELIMIT_STORAGE,
    default_limits=[],  # only routes explicitly marked are limited
    # If the shared storage is unavailable, fall back to local counters rather
    # than returning an error. An outage at the Redis provider shouldn't lock
    # people out of the app: a loose limit for a few minutes is far better than
    # a site nobody can get into.
    in_memory_fallback_enabled=True,
)

# If we ended up on local memory in production, say so loudly. The failure here
# is silent by nature: requests keep working and the limit simply isn't
# enforced, so without this alert there is no way to tell "protected" from
# "looks protected".
#
# `error` and not `warning`: to Sentry, `warning` is a breadcrumb, not an
# event, and Railway's log is a buffer that gets wiped and nobody reads (see
# backend/logs.py). So the only alert that every limit in the app was weakened
# fourfold and resets on every deploy reached nowhere anyone looks.
RATELIMIT_IS_SHARED = not _RATELIMIT_STORAGE.startswith("memory://")

if not RATELIMIT_IS_SHARED and not _IS_DEV:
    logger.error("rate limiting is using in-memory storage in production — "
                 "limits are per-worker and reset on every deploy. "
                 "Set RATELIMIT_STORAGE_URI.")


# ─── Static assets: content-based fingerprint ───────────────────────────────
#
# Every static file used to be sent with "no-cache", i.e. the browser must ask
# the server on every page load whether its copy is still valid. Six such
# questions on every page, all answered "not modified", and each one occupies
# one of the two workers. In practice six out of seven requests in an ordinary
# page load were a conversation about nothing.
#
# The reason is that the URL has no version: /static/css/style.css is the same
# URL whether it is yesterday's file or today's, so the browser has no other
# way to know.
#
# The fix: `?v=<fingerprint>` derived from the file's content, and a one-year
# lifetime. The browser doesn't ask, and when a file changes its fingerprint
# changes with it: a new URL, a fresh download, automatically.
#
# The fingerprint is derived from the content and not a manual number, and
# that is the point: a number you have to remember to bump is a number that
# will be forgotten, and the result is a browser serving an old file for a
# year with no way to fix it remotely. Here it can't be forgotten.
_ASSET_HASHES: dict = {}


def _asset_version(filename: str) -> str:
    """Eight characters of the file's content fingerprint. Empty if it isn't found."""
    if filename in _ASSET_HASHES:
        return _ASSET_HASHES[filename]

    path = os.path.join(app.static_folder, filename)
    try:
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:8]
    except OSError:
        # File not found: don't break the page over a missing asset. It will
        # fail to load as before, and that is easy to see.
        digest = ""
    if not _IS_DEV:
        _ASSET_HASHES[filename] = digest
    return digest


# A money amount in templates: `{{ x | money }}`, agorot only when present
# (see backend/money.py)
app.jinja_env.filters["money"] = format_money
app.jinja_env.globals["ui_icon"] = ui_icon
app.jinja_env.globals["empty_illus"] = empty_illus
app.jinja_env.globals["greeting"] = lambda: greeting(clock.now().hour)
app.jinja_env.filters["count_of"] = count_of
# "היום" / "אתמול" / "ראשון, 27.9", against the injected clock's today (tests)
app.jinja_env.filters["day_label"] = lambda d: day_label(d, clock.today())


@app.context_processor
def _versioned_static():
    """Overrides url_for for static assets only.

    An override and not a new function, so the 48 existing calls in templates
    don't change, and above all so a call written tomorrow gets this without
    anyone remembering."""
    def versioned_url_for(endpoint, **values):
        if endpoint == "static" and "filename" in values and "v" not in values:
            version = _asset_version(values["filename"])
            if version:
                values["v"] = version
        return url_for(endpoint, **values)
    return {"url_for": versioned_url_for}


@app.after_request
def _cache_static_forever(response):
    """A year of caching, but only for a fingerprinted URL.

    Without the fingerprint this would be dangerous: an unversioned URL cached
    for a year is an old file with no way to refresh it. With a fingerprint, a
    change to the file produces a different URL anyway."""
    if request.endpoint == "static" and request.args.get("v"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


_GZIP_TYPES = ("text/html", "text/css", "text/plain", "application/javascript",
               "text/javascript", "application/json", "image/svg+xml",
               "application/manifest+json")
_GZIP_MIN = 1024
_gzip_static = {}   # (path, ETag) → compressed bytes; static files are compressed once per process


@app.after_request
def _gzip(response):
    """Compression for everything sent to the phone (Matan, 13.10: "as fast as
    possible").

    The settings page was `117KB` and is now `13KB`; `style.css` went from
    `200KB` to `45KB`. Railway doesn't compress by itself. Static files are
    compressed once and kept in memory; pages on every request (level 6: a few
    milliseconds, against tens of KB less on the network)."""
    if (response.status_code != 200
            or "gzip" not in (request.headers.get("Accept-Encoding") or "").lower()
            or "Content-Encoding" in response.headers
            or response.mimetype not in _GZIP_TYPES):
        return response
    response.vary.add("Accept-Encoding")
    response.direct_passthrough = False
    key = None
    if request.endpoint == "static":
        key = (request.path, response.headers.get("ETag"))
        cached = _gzip_static.get(key)
        if cached is not None:
            response.set_data(cached)
            return _mark_gzipped(response)
    data = response.get_data()
    if len(data) < _GZIP_MIN:
        return response
    packed = gzip.compress(data, compresslevel=6)
    if key is not None and len(_gzip_static) < 300:
        _gzip_static[key] = packed
    response.set_data(packed)
    return _mark_gzipped(response)


def _mark_gzipped(response):
    """The same marking every way, also when the compressed bytes came from memory
    (code review, 14.10: there the ETag stayed "strong", and compressed and
    uncompressed carried the same id)."""
    response.headers["Content-Encoding"] = "gzip"
    # The original's ETag no longer describes the bytes; "weak" so the 304 check
    # keeps working
    etag = response.headers.get("ETag")
    if etag and not etag.startswith("W/"):
        response.headers["ETag"] = "W/" + etag
    # A byte range of the uncompressed file doesn't match the compressed version
    response.headers.pop("Accept-Ranges", None)
    return response


# ─── Auth helpers ─────────────────────────────────────────────────────────────

def _do_not_rewrite_session_cookie():
    """Marks this response as not rewriting the session cookie.

    This matters exactly when the refresh failed. gunicorn's two workers take
    requests in parallel, and Supabase's refresh token rotates on every
    refresh, so one request can refresh successfully and write a new token to
    the cookie while a parallel request, which left with the old token and got
    stuck on a slow cellular network, arrives after it and fails. If the failed
    request writes its cookie, it overwrites the good token with a dead one, and
    the user is signed out on their next visit without having done anything.

    The cancellation itself happens in after_request and not here, and that
    isn't a matter of style: Flask writes the cookie when the session is marked
    permanent or modified, and any write to the session inside the view (e.g.
    recurring_synced on the dashboard) would turn modified back on. Worse,
    cancelling permanent early would make the cookie to be written a browser
    cookie that is deleted on close, exactly the sign-out we are preventing. In
    after_request no view code runs anymore."""
    g.sf_skip_session_cookie = True


@app.after_request
def _honour_session_cookie_freeze(response):
    """Freezes writing the cookie, unless the session was emptied on purpose.

    The condition on user_id is not a technical detail. The freeze exists so a
    good token isn't overwritten with a dead one when a refresh failed, and
    that only matters while there is a session. But `session.clear()` (on an
    explicit sign-out, on account deletion, and on sign-out after a rejected
    token) is exactly the moment the cookie **must** be written, so it gets
    deleted.

    Without this condition, whoever tapped "sign out" exactly when the refresh
    failed got a message that they had signed out, their device cookies were
    deleted, and they stayed fully signed in, because their session cookie was
    never touched. The next request would take them back into the app."""
    if g.get("sf_skip_session_cookie") and session.get("user_id"):
        # These two together are what Flask's should_set_cookie checks. The
        # values in the browser's cookie stay as they are.
        session.permanent = False
        session.modified  = False
    return response


def _end_session(reason: str):
    """Ends the session and leaves a trace.

    A sign-out used to be completely silent, so a user who complained they were
    "kicked out" left nothing to investigate. The promise is to stay signed in
    until you sign out yourself, so any other sign-out is an event that must be
    visible."""
    # warning and not info: the promise is to stay signed in until you sign
    # out, so any other sign-out is an event that must be visible
    logger.warning("session ended: %s", reason)
    if _sentry_dsn:
        sentry_sdk.capture_message(f"session ended: {reason}", level="warning")
    session.clear()


@app.before_request
def inject_auth():
    token = session.get("access_token")
    if not token:
        # Reset explicitly, don't return silently. The client is a
        # process-level singleton: without the reset, an anonymous request
        # keeps carrying the JWT of the previous user handled by that worker.
        db.set_auth_token("")
        return

    # Supabase JWTs expire after ~1 hour; refresh ahead of expiry so a
    # long-lived Flask session keeps working without re-login.
    expires_at = session.get("token_expires_at") or 0
    if session.get("refresh_token") and time.time() > expires_at - 120:
        sid = session.get("sid")
        # The response with the previous token may not have reached the phone
        # (the app went to the background mid-request), so the cookie is stale
        # and the latest token is waiting in the database (Matan, 13.10)
        if sid and _adopt_newer_tokens(sid):
            token, expires_at = session["access_token"], session["token_expires_at"]
    if session.get("refresh_token") and time.time() > expires_at - 120:
        old_refresh = session["refresh_token"]
        response, err, fatal = db.refresh_session(old_refresh)
        if not err and response and response.session:
            token = response.session.access_token
            session["access_token"]     = token
            session["refresh_token"]    = response.session.refresh_token
            session["token_expires_at"] = response.session.expires_at
            session.permanent = True     # every use extends the cookie window
            if sid:
                db.web_session_rotate(sid, old_refresh, token, response.session.refresh_token,
                                      response.session.expires_at)
        elif fatal and sid and _adopt_newer_tokens(sid):
            # A parallel request (another worker) refreshed just before us and
            # stored it in the database
            token = session["access_token"]
        elif fatal and time.time() >= expires_at:
            # The token was rejected and the access token has expired too: the
            # session is really dead and there is nothing to recover. Only here
            # do we sign out.
            _end_session(f"refresh token rejected: {err}")
            return
        else:
            # Don't sign out. Two different causes reach here, and both are
            # recoverable: a transient failure (network, 5xx), or a rejection
            # while the access token is still valid, which is exactly what
            # happens when a parallel request already rotated the token before
            # us. This request continues with the existing access token, and the
            # next one will retry; if the rejection is real, the sign-out
            # happens by itself when the access token expires.
            logger.warning("token refresh failed, keeping session: %s", err)
            _do_not_rewrite_session_cookie()

    db.set_auth_token(token)


def _adopt_newer_tokens(sid) -> bool:
    """Replaces the tokens in the cookie with the latest ones from the database,
    if newer ones are there.

    "Newer" = a refresh token different from the one in the cookie: only a
    successful refresh changes it in the database, so a different one means the
    refresh happened in a request whose response never arrived."""
    newer = db.web_session_get(sid)
    if not newer or not newer.get("refresh_token") or newer["refresh_token"] == session.get("refresh_token"):
        return False
    logger.info("session tokens recovered from web_sessions")
    session["access_token"]     = newer["access_token"]
    session["refresh_token"]    = newer["refresh_token"]
    session["token_expires_at"] = newer["expires_at"]
    session.permanent = True
    return True


# ─── Device memory ──────────────────────────────────────────────────────────
#
# The landing page is a marketing page, meant for people who don't know
# SmartFin. Someone who has signed in from this device once doesn't need to see
# it again, not even when the session ends or they signed out themselves. The
# two cookies here are the only memory that survives the end of a session, so
# they are separate from the session cookie and hold only the minimum:
#
#   sf_returning: "you've signed in from here before". Decides where the root goes.
#   sf_last_id:   the identifier they signed in with (email or phone), to
#                 prefill the form. Deleted on an explicit sign-out: whoever
#                 signed out on purpose asked to stop being remembered, and
#                 may be handing the device to another family member.
#
# Both are HttpOnly: JS has no use for them, and that way XSS can't read the
# email either. No authorisation decision relies on them: a forged value at
# most changes which page is served to someone not signed in, and what is
# printed in a text field. The password is still required.
_DEVICE_COOKIE   = "sf_returning"
_LAST_ID_COOKIE  = "sf_last_id"
_DEVICE_COOKIE_MAX_AGE = 10 * 365 * 24 * 60 * 60   # ten years: only "was here once", not a session


def _remember_device(response, identifier: str = ""):
    """Marks the device as known, and if an identifier is given, remembers it too."""
    common = dict(max_age=_DEVICE_COOKIE_MAX_AGE, httponly=True,
                  samesite="Lax", secure=not _IS_DEV)
    response.set_cookie(_DEVICE_COOKIE, "1", **common)
    identifier = (identifier or "").strip()
    if identifier:
        # A cap and stripping of control characters: the value goes back to the
        # browser into a form field, and there is no reason for it to be long or
        # odd. Jinja already escapes in the template.
        safe = re.sub(r"[\x00-\x1f\x7f]", "", identifier)[:120]
        response.set_cookie(_LAST_ID_COOKIE, safe, **common)
    return response


def _remembered_identifier() -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", request.cookies.get(_LAST_ID_COOKIE, ""))[:120]


def _device_is_known() -> bool:
    return request.cookies.get(_DEVICE_COOKIE) == "1"


def _is_api_request() -> bool:
    """Whether the request expects JSON rather than an HTML page.

    This is what tells "the user navigated to a URL" apart from "the code on the
    page called the server". fetch follows redirects silently, so an HTML page
    returned to an API call comes back as a valid response with status 200, and
    the client tries to parse it as JSON and fails. The user sees a "network
    error" that never stops."""
    return request.path.startswith("/api/")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            # An API call gets a 401 the client knows how to detect; ordinary
            # navigation gets a redirect to the sign-in form as always.
            if _is_api_request():
                return jsonify({"error": "ההתחברות הסתיימה — יש להתחבר מחדש"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# The path `db.upload_receipt` creates: `<family_id>/<uuid>.<extension>`
_RECEIPT_NAME_RE = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\.[a-z0-9]{2,5}$")


def _receipt_path_ok(path, family_id) -> bool:
    """The receipt path comes from the client. Storage already blocks another
    family's folder, but within the family a transaction could be given another
    transaction's receipt, and then deleting one deleted both files. Only what
    the app itself creates is accepted."""
    if not isinstance(path, str) or not family_id:
        return False
    folder, _, name = path.partition("/")
    return folder == str(family_id) and bool(_RECEIPT_NAME_RE.match(name))


def _delete_unused_receipts(family_id, paths):
    """Deletes receipt files, except what another transaction still points at.
    Called after deleting the transactions. When it isn't known what is in use,
    nothing is deleted: a stray file in storage beats a fridge's broken receipt
    on the day the warranty is needed."""
    paths = [p for p in (paths or []) if p]
    if not paths:
        return
    try:
        in_use = db.receipts_in_use(family_id, paths)
    except db.DataUnavailable:
        logger.error("receipts_in_use failed; the files stay in storage")
        return
    db.delete_receipts(session.get("access_token"), [p for p in paths if p not in in_use])


class BadInput(ValueError):
    """A value from the client there is no point trying to interpret; see `_text`."""


def _text(value) -> str:
    """A text field from a JSON request body: a whitespace-stripped string, or ""
    when it is missing.

    Any other value (number, list, object, boolean) is `BadInput`, which answers
    422 "invalid value". Before, `(body.get("x") or "").strip()` crashed on a
    number with a 500, which we logged as a real failure. The forms always send
    text; this is for requests sent by hand."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BadInput(type(value).__name__)
    return value.strip()


@app.errorhandler(BadInput)
def bad_input(e):
    return jsonify({"error": "ערך לא תקין"}), 422


def get_current_user():
    return {
        "id":             session.get("user_id"),
        "name":           session.get("user_name", "משתמש"),
        "family_id":      session.get("family_id"),
        "avatar_initial": session.get("avatar_initial", "מ"),
    }


def family_settings():
    """The signed-in family's settings, fetched once per request (cached on flask.g)."""
    if "family_settings" not in g:
        fid = session.get("family_id")
        g.family_settings = db.get_family_settings(fid) if fid else dict(db.DEFAULT_FAMILY_SETTINGS)
    return g.family_settings


@app.context_processor
def inject_family_settings():
    """family_settings is available in every template (the tags, the modal and the
    settings group depend on it).

    This runs before **every** render_template, including error.html. So here,
    and only here, a failed query doesn't propagate: otherwise a database
    failure would also break the error page meant to report it, and the user
    would get a completely blank screen. The templates already handle None:
    that is the state of someone not signed in.

    This is a display-only relaxation. Decisions that rely on the settings (for
    example attributing a transaction to a family member in _resolve_owner)
    call family_settings() directly and keep failing visibly, because there a
    wrong value is written to the database."""
    if "user_id" not in session:
        return {"family_settings": None, "push_key": None}
    try:
        settings = family_settings()
    except db.DataUnavailable:
        return {"family_settings": None, "push_key": push.public_key()}
    # When the next recurring charge is: a copy in the session, so the check at
    # the start of the next page doesn't have to query (`_sync_recurring`).
    # Shared by the family: every engine run updates it.
    nxt = (settings or {}).get("recurring_next")
    if nxt and session.get("recurring_next") != nxt:
        session["recurring_next"] = nxt
    # A one-person family (Matan, 5.10): "whose" says nothing: not the tag next
    # to each transaction, not the "by family member" chart and not the question
    # in the window. Display only: the preference itself doesn't change, and
    # when someone joins everything comes back. Lazy: fetched only on a page
    # that really shows "whose", not on an error page, for example.
    single = _LazySingleMember()
    # The public key for notifications, on every page, because auto-subscribe
    # runs from every page (`js/push.js`)
    return {"family_settings": settings, "single_member": single, "show_owner": _ShowOwner(settings, single),
            "push_key": push.public_key()}


@app.context_processor
def _client_lists():
    """The categories and family members for the "add transaction" window, inside
    the page (Matan, 13.10).

    Until now every page sent two more requests after it (`/api/categories`,
    `/api/family/members`) just to warm up the window, and in the logs many of
    them were cut off midway when the user moved to the next page. Most pages
    already fetched exactly this, so it is taken from the request cache only:
    what wasn't fetched isn't fetched for our sake, and the window will ask as
    usual."""
    def client_lists() -> dict:
        fid = session.get("family_id")
        cache = g.get("_sf_cache") or {}
        out = {}
        cats = cache.get(f"categories:{fid}") if fid else None
        if cats is not None:
            out["categories"] = cats
        members = cache.get(f"members:{fid}") if fid else None
        if members is not None:
            out["members"] = [{"id": m["id"], "name": m["name"]} for m in members]
        return out
    return {"client_lists": client_lists}


class _LazySingleMember:
    """`single_member()` in templates: whether the family is one person. When in
    doubt (the query failed), no."""
    def __init__(self):
        self._value = None

    def __call__(self) -> bool:
        if self._value is None:
            fid = session.get("family_id")
            try:
                self._value = bool(fid) and len(db.get_family_members(fid)) == 1
            except db.DataUnavailable:
                self._value = False
        return self._value


class _ShowOwner:
    """`show_owner.get(type)`: whether to show "whose" for this type: attribution
    is on, and there is more than one person."""
    def __init__(self, settings, single):
        self._attr = (settings or {}).get("owner_attribution") or {}
        self._single = single

    def get(self, tx_type, default=False):
        return bool(self._attr.get(tx_type)) and not self._single()


def assign_member_colors(members: list, chosen: dict) -> dict:
    """id → colour number (`_OWNER_HEX` and `.owner-N`).

    Whoever picked a colour (Matan, 30.9, round 6, item 13) gets it. Everyone
    else by join order, as always, but skipping a colour someone already picked:
    two people in the same colour can't be told apart. A pick by someone no
    longer in the family, or a broken value, simply doesn't count."""
    ids = [m["id"] for m in members]
    size = len(_OWNER_HEX)
    colors, taken = {}, set()
    for mid, c in (chosen or {}).items():
        if mid in ids and isinstance(c, int) and not isinstance(c, bool) and 0 <= c < size and c not in taken:
            colors[mid] = c
            taken.add(c)
    for i, mid in enumerate(ids):
        if mid in colors:
            continue
        c = i % size
        for step in range(size):
            if (i + step) % size not in taken:
                c = (i + step) % size
                break
        colors[mid] = c
        taken.add(c)
    return colors


def _member_colors(family_id):
    """A colour per family member, for name tags on transactions and per-member charts."""
    if not family_id:
        return {}
    members = db.get_family_members(family_id)
    return assign_member_colors(members, (family_settings() or {}).get("member_colors") or {})


def _run_queries(tasks: dict) -> dict:
    """Runs a group of DB queries and returns {name: result}.
    It used to be parallel (ThreadPoolExecutor), but the Supabase client is a
    singleton with a shared httpx client, and parallel calls from threads on the
    same client caused "Server disconnected" under gunicorn (the query failed
    silently and returned zeros). Since the server moved to the EU
    (europe-west4) every query costs ~30ms, so running in sequence is perfectly
    fast (~7 queries = ~0.2s) and safe. Runs on the main thread, so
    flask.g/_request_cache work as usual too."""
    return {name: _timed(name, fn) for name, fn in tasks.items()}


CHART_COLORS = 12   # `COLORS` in `chart-setup.js`: must stay in sync


def _distinct_colors(items):
    """No two categories in the same colour in the month's donut (Matan, 12.10).

    Each asks for its fixed colour (`color_idx`, its place in the category
    list). The largest first gets what it asked for; one whose colour is
    already taken gets the next free colour. So "סופר" stays gold in every
    month, and the ninth category doesn't come out gold too. Only when a single
    month has more categories than colours does a colour repeat."""
    taken = set()
    for c in sorted(items, key=lambda c: -(c.get("total") or 0)):
        want = (c.get("color_idx") or 0) % CHART_COLORS
        if len(taken) < CHART_COLORS:
            while want in taken:
                want = (want + 1) % CHART_COLORS
        taken.add(want)
        c["color_idx"] = want


def _timed(name, fn):
    """Runs `fn` and records how long it took, for the "timing" line at the end
    of the request.

    Load speed (Matan, 12.10): first measure which query is slow, and only then
    fix."""
    start = time.perf_counter()
    try:
        return fn()
    finally:
        timings = g.setdefault("sf_timings", [])
        timings.append((name, (time.perf_counter() - start) * 1000))


@app.before_request
def _start_request_clock():
    g.sf_request_start = time.perf_counter()


@app.after_request
def _log_query_timings(response):
    """One line per measured request: the total, and every query in order."""
    timings = g.get("sf_timings")
    if timings and g.get("sf_request_start") is not None:
        total = (time.perf_counter() - g.sf_request_start) * 1000
        logger.info("timing %s total=%.0fms %s", request.path, total,
                    " ".join(f"{name}={ms:.0f}" for name, ms in timings))
    return response


def _prime_request_cache(family_id, settings=None, categories=None, members=None, family=None):
    """Injects into the request cache (flask.g) values already fetched in the
    batch, so the context processors and _member_colors use them instead of
    fetching again in the same request. The keys must be identical to those in
    _request_cache in supabase_config."""
    cache = getattr(g, "_sf_cache", None)
    if cache is None:
        cache = g._sf_cache = {}
    if categories is not None:
        cache[f"categories:{family_id}"] = categories
    if members is not None:
        cache[f"members:{family_id}"] = members
    if family is not None:
        cache[f"family:{family_id}"] = family
    if settings is not None:
        g.family_settings = settings


# Solid fill colours for the "by family member" chart bars, identical to the
# name-tag colours (.owner-0..5 in style.css; must stay in sync; the number of
# colours here decides how many family members get a unique colour before it
# wraps around). Shared (no user_id) gets grey like .owner-shared. Unusual
# colours that don't clash with the semantic ones (green=income, red=expense,
# gold=savings).
_OWNER_HEX  = {
    0: "#2E67A8",  # blue (Matan)
    1: "#7048B0",  # purple (Or)
    2: "#A0457C",  # plum-magenta
    3: "#A85C3E",  # terracotta
    4: "#5E7391",  # slate blue-grey
    5: "#8A6A52",  # taupe brown
    # Two more: eight to choose from (Matan, 30.9, round 6, item 13)
    6: "#4B4FA8",  # indigo
    7: "#2F7385",  # petrol blue
}
_SHARED_HEX = "#78716C"
_EXTRA_INCOME_HEX = "#B8B0A2"   # "other income": light grey, distinct from a shared salary
# Savings goals: shades of gold, the app's savings colour. The expense palette
# gave the second goal green, and green here means income.
_SAVINGS_HEX = ("#A67C00", "#D6AE34", "#6E5200", "#E8CF7A", "#8C6A12", "#C4A45A")


# ─── Auth routes ──────────────────────────────────────────────────────────────

# ─── Email address check ────────────────────────────────────────────────────
#
# Not full RFC: exactly what prevents the mistake that actually happened, an
# address with no TLD. Both forms have `novalidate` (on purpose, so errors are
# in Hebrew and our design rather than an English browser bubble), and the
# server checked nothing, so `israel@gmial` or `israel@gmail` signed up
# successfully.
#
# Why this is worse than it sounds: the user notices nothing at signup. They
# find out a month later, when they try to reset a password and the link is
# sent to an address that doesn't exist. They have no way out, and no route in
# the app to fix an address. The database already has such an account from July.
#
# 254 characters is the maximum length of an email address per the standard.
_EMAIL_RE = re.compile(r"^[^@\s]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def _looks_like_email(value: str) -> bool:
    value = (value or "").strip()
    return len(value) <= 254 and bool(_EMAIL_RE.match(value))


def _login_error(err: str) -> str:
    """Maps a sign-in failure to a message that says what happened.

    Every failure used to be shown as "wrong email/phone or password", even
    when the password was entirely correct. `db.sign_in` catches every
    exception and returns its text, so a Supabase rate limit, an unconfirmed
    email and a connection failure all looked the same.

    The case that turns this from an inconvenience into a trap: if email
    confirmation is ever turned on in the project, **every new user is locked
    out for good**: they sign up, get "you can now sign in", and get "wrong
    password" forever. "Forgot password" won't help either, because the
    password was never the problem.

    The signup side carefully maps five different errors; here there were none."""
    text = (err or "").lower()
    if "email not confirmed" in text or "not confirmed" in text:
        return ("החשבון עדיין לא אומת. בדקו את תיבת המייל — נשלח אליכם "
                "קישור אישור, וייתכן שהוא בספאם.")
    if "rate limit" in text or "too many" in text:
        return "יותר מדי ניסיונות בזמן קצר — נסו שוב בעוד כמה דקות"
    if "not configured" in text or "connection" in text or "timeout" in text:
        return "השירות אינו זמין כרגע — נסו שוב בעוד רגע"
    return "מייל/טלפון או סיסמה שגויים"


def _signup_form():
    """The fields typed into the signup form, to return them with the error.
    Without the passwords: there is no reason for them to sit in a response's
    HTML."""
    return {
        "first_name":  request.form.get("first_name", "").strip(),
        "last_name":   request.form.get("last_name", "").strip(),
        "email":       request.form.get("email", "").strip(),
        "phone":       request.form.get("phone", "").strip(),
        # or from the invite link (`/join/<code>` → `/signup?invite=…`, idea 43)
        "invite_code": request.form.get("invite_code", "").strip() or _clean_invite(request.args.get("invite")),
    }


def _clean_invite(raw) -> str:
    """An invite code from a URL: letters and digits only, up to 12, upper-case.
    It goes into HTML (`value=`) and into URLs; nothing else gets through."""
    return re.sub(r"[^A-Za-z0-9]", "", raw or "")[:12].upper()


def _normalize_phone(raw: str) -> str:
    """Normalises a phone number for consistent comparison/storage: digits only,
    in local form.

    A `+972` prefix is converted to `0`. Without this the exact same number was
    stored in two forms: someone who signed up with `+972-54-1234567` was stored
    as `972541234567`, and then signing in with `054-1234567` never found them,
    and two people could "claim" the same number, each in a different spelling.
    """
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("972"):
        digits = "0" + digits[3:]
    return digits


# An Israeli phone number: mobile (`05X` plus seven digits), landline (`0X` plus
# seven), or `07X` plus seven. The check exists for the same reason as the
# email one: the forms are `novalidate` and the server checked nothing, so `1`
# was a valid number: it was saved, became a meaningless alternative sign-in
# identifier, and because of the unique index on phone it also blocked that
# value for every other user forever.
_PHONE_RE = re.compile(r"^0(5\d|7\d|[2-4689])\d{7}$")


def _looks_like_phone(normalized: str) -> bool:
    return bool(_PHONE_RE.match(normalized or ""))


def _begin_session(user_id, email, access_token, refresh_token, expires_at):
    """Opens the Flask session after sign-in. Returns an error message, or
    `None` when everything succeeded."""
    # Set JWT before querying profiles (RLS requires auth.uid())
    db.set_auth_token(access_token)
    db.log_login_event()  # internal sign-in record (for the site owner)
    profile, profile_ok = db.fetch_profile(user_id)

    if not profile_ok:
        # The password is right, but the profile couldn't be read. Don't sign
        # in: continuing from here would start a session with no family_id, and
        # from there ensure_family would create a new family and overwrite the
        # existing membership: cut off for good from all history over a
        # two-second glitch. A failure that can be retried is the right outcome.
        return "לא הצלחנו לטעון את הפרטים שלך — נסו שוב בעוד רגע"

    session.permanent = True  # stay signed in until explicit logout
    session["user_id"]          = user_id
    session["user_email"]       = email
    session["access_token"]     = access_token
    session["refresh_token"]    = refresh_token
    session["token_expires_at"] = expires_at
    session["user_name"]      = db.first_name(profile.get("name", "משתמש")) if profile else "משתמש"
    session["avatar_initial"] = (profile.get("avatar_initial") or "מ") if profile else "מ"
    session["family_id"]      = profile.get("family_id") if profile else None

    # Auto-create family if user doesn't have one
    if not session["family_id"]:
        session["family_id"] = db.ensure_family(user_id)

    if not session["family_id"]:
        # Creation failed too. A session with no family is an app you can't do
        # anything in, and on the next attempt it will try to create a family
        # again, so better not to sign in than to sign into a stuck state.
        session.clear()
        return "לא הצלחנו לטעון את הפרטים שלך — נסו שוב בעוד רגע"

    # A copy of the tokens in the database, keyed by a random id in the cookie
    # (see `inject_auth`)
    session["sid"] = secrets.token_urlsafe(32)
    db.web_session_open(session["sid"], user_id, access_token, refresh_token, expires_at)
    db.set_auth_token(access_token)     # the database call runs without a user token
    return None


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    error = None
    # What fills the "email or phone" field. On GET it is the identifier saved
    # from the previous sign-in on this device; after a failed attempt it is
    # what the user just typed, so they don't have to type it again after a
    # wrong password.
    identifier = _remembered_identifier()
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        password   = request.form.get("password", "")

        if "@" in identifier:
            email = identifier
        else:
            email = db.get_email_by_phone(_normalize_phone(identifier))

        response, err = db.sign_in(email, password) if email else (None, "not found")
        if err:
            error = _login_error(err)
        else:
            error = _begin_session(response.user.id, response.user.email,
                                   response.session.access_token, response.session.refresh_token,
                                   response.session.expires_at)
            if not error:
                # From here on the device is known: even if the session ends one
                # day, the root takes it to the sign-in form and not back to the
                # marketing page.
                return _remember_device(redirect(url_for("dashboard")), identifier)

    # The page now carries the saved identifier, so it depends on the cookie
    # exactly like the root and must not be cached by a browser or proxy.
    response = make_response(render_template("login.html", error=error,
                                             active_tab="login",
                                             remembered_identifier=identifier))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Cookie"
    return response


# ─── Face ID ────────────────────────────────────────────────────────────────
# Removed (Matan, 13.10): on an iPhone a website can't open Face ID directly;
# Apple first shows a "sign in to railway.app" sheet and waits for a tap. "I
# want the site to just recognise me": the session is kept (`web_sessions`), so
# you go straight in, with no scan.


@app.route("/join/<code>")
def join_link(code):
    """The invite link from WhatsApp (Matan, 3.10, idea 43). Without an account:
    the signup form with the code filled in. With an account: settings, at the
    "join another family" row with the code filled in; the join itself is on
    their tap, not automatic."""
    code = _clean_invite(code)
    if "user_id" in session:
        return redirect(url_for("settings", join=code) + "#join-family")
    return redirect(url_for("signup", invite=code))


@app.route("/signup", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        first_name       = request.form.get("first_name", "").strip()
        last_name        = request.form.get("last_name", "").strip()
        email            = request.form.get("email", "").strip()
        phone            = _normalize_phone(request.form.get("phone", ""))
        password         = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")
        invite_code      = request.form.get("invite_code", "").strip()

        if not first_name or not last_name or not email or not password or not phone:
            error = "נא למלא את כל השדות"
        elif not _looks_like_email(email):
            error = "כתובת המייל אינה תקינה — בדקו שהיא מלאה, למשל israel@gmail.com"
        elif not _looks_like_phone(phone):
            error = "מספר הטלפון אינו תקין — למשל 050-1234567"
        elif passwords.problem(password):
            error = passwords.problem(password)
        # "Confirm password" was removed from the form (Matan, 13.10: there is
        # an eye that shows what was typed). Checked only if sent, so an old form
        # still open on a phone keeps working
        elif "password_confirm" in request.form and password != password_confirm:
            error = "הסיסמאות אינן תואמות"
        else:
            name = f"{first_name} {last_name}"
            response, err = db.sign_up(email, password, name, phone)
            if err:
                # Map known Supabase errors to a Hebrew message. In the past every
                # error (rate limit, invalid format, etc.) was always shown as
                # "the email already exists" by mistake, which misled when the
                # real problem was something else entirely
                err_lower = err.lower()
                if ("already registered" in err_lower or "already exists" in err_lower
                        or ("duplicate" in err_lower and "phone" in err_lower)
                        or "database error saving new user" in err_lower):
                    # One message for both cases, on purpose.
                    #
                    # Before, "the email already exists" and "the phone is
                    # already registered" were two different answers, i.e. the
                    # signup form answered with certainty "is this address
                    # registered here". With `enable_confirmations` off it is
                    # both an oracle and a way to grab someone else's address
                    # so they can never sign up.
                    #
                    # The message points to sign-in and password reset, which
                    # is what someone who really forgot they have an account
                    # needs anyway.
                    error = ("הרשמה נכשלה — ייתכן שכבר יש חשבון עם הפרטים האלה. "
                             "נסו להתחבר, או לאפס סיסמה.")
                elif "invalid" in err_lower and "email" in err_lower:
                    error = "הרשמה נכשלה – כתובת המייל אינה תקינה, בדקו שהיא הוזנה נכון"
                elif "rate limit" in err_lower:
                    error = "יותר מדי ניסיונות הרשמה בזמן קצר — נסו שוב בעוד כמה דקות"
                else:
                    # Don't leak the internal error to the user, only to the
                    # server log
                    logger.error("signup: %s", err)
                    error = "הרשמה נכשלה — נסו שוב בעוד כמה רגעים"
            else:
                # Joining an existing family by invite code. A failure here
                # doesn't cancel the signup (the user already exists in Auth
                # and must not be left with no way in), but it must be said out
                # loud. The return value used to be thrown away, and every
                # joiner got "signed up successfully" and then, silently, a new
                # family of their own.
                success = "נרשמתם בהצלחה! כעת ניתן להתחבר."
                join_err = None
                if invite_code and response and response.user:
                    # The RPC identifies the joiner through auth.uid(), so the
                    # client must carry the new user's token. Don't rely on the
                    # SDK doing it by itself after sign_up: set it explicitly, as
                    # in every other route in the app.
                    session_obj = getattr(response, "session", None)
                    if session_obj and session_obj.access_token:
                        db.set_auth_token(session_obj.access_token)
                    joined_id, join_err = db.join_family_by_code(invite_code)
                    if joined_id and not join_err:
                        _notify_owner_about_family("joined", joined_id, person_id=response.user.id)
                    if join_err:
                        logger.warning("signup join failed for %s: %s", email, join_err)
                        success = (f"נרשמתם בהצלחה! אבל {join_err}. "
                                   "אפשר להתחבר ולהצטרף למשפחה דרך ההגדרות.")

                # Sign straight in (Matan, 13.10): until now whoever finished the
                # form got "you can now sign in" and typed email and password
                # again, exactly when it is easiest to lose a new user.
                # `sign_up` returns a session when email confirmation is off;
                # when it is on (no session) or sign-in failed, as before, to
                # the sign-in screen.
                session_obj = getattr(response, "session", None) if response else None
                if session_obj and session_obj.access_token and response.user:
                    begin_err = _begin_session(response.user.id, response.user.email,
                                               session_obj.access_token, session_obj.refresh_token,
                                               session_obj.expires_at)
                    if not begin_err:
                        if join_err:
                            session["sf_notice"] = (f"נרשמתם בהצלחה! אבל {join_err}. "
                                                    "אפשר להצטרף למשפחה דרך ההגדרות.")
                        # Without categories (a new family) home redirects to
                        # the onboarding wizard
                        return _remember_device(redirect(url_for("dashboard")), email)
                    logger.warning("signup auto sign-in failed for %s: %s", email, begin_err)
                # Signup doesn't sign in automatically, but it does make the
                # device known: someone who just opened an account certainly
                # doesn't need to see the marketing page again, and their email
                # is already prefilled in the form.
                return _remember_device(
                    make_response(render_template("login.html", active_tab="login",
                                                  success=success,
                                                  remembered_identifier=email)),
                    email)

    # What was typed comes back with the error.
    #
    # Until now every signup error (invalid email, mismatched passwords, email
    # already exists) wiped all six fields, **including the invite code**.
    # Someone who got a code on WhatsApp and typed a too-short password was left
    # without the code and without their details, and had to go back to the chat
    # and search. The exact opposite of the sign-in side, which keeps the
    # identifier explicitly "so they don't have to type it again after a wrong
    # password".
    #
    # Passwords don't come back, on purpose: there is no reason for a password
    # to sit in a response's HTML.
    return render_template("login.html", error=error, active_tab="signup",
                           signup=_signup_form()), 422 if error else 200


# POST only. With `SameSite=Lax`, `<img src=".../logout">` on another site
# signed the visitor out and deleted their device cookies. A nuisance, not a
# breach, but a nuisance someone can trigger remotely, and there is no reason
# for a GET to perform an action.
@app.route("/logout", methods=["POST"])
@limiter.limit("30 per minute")
def logout():
    """An explicit sign-out goes back to the landing page, and deletes everything
    stored on the device.

    This is the point where all memory resets: not only the session but also
    the two device cookies. The intent is precise: an explicit sign-out is the
    only way out, and whoever chose it gets exactly what a guest gets: the
    landing page, and from there a "sign in" button. It is also what makes the
    device marker reliable: from then on, a device marked known but with no
    session is necessarily a case where the session ended by itself, and that
    is exactly the case where a marketing page is the wrong answer."""
    if session.get("sid"):
        db.web_session_close(session["sid"])
    # A shared phone: whoever signed out stops getting the family's
    # notifications (Matan, 31.10)
    if session.get("push_endpoint"):
        db.push_unsubscribe(session["push_endpoint"])
    session.clear()
    response = redirect(url_for("dashboard"))
    response.delete_cookie(_LAST_ID_COOKIE, samesite="Lax", secure=not _IS_DEV)
    response.delete_cookie(_DEVICE_COOKIE,  samesite="Lax", secure=not _IS_DEV)
    return response


@app.route("/api/auth/forgot", methods=["POST"])
@limiter.limit("3 per minute")
def forgot_password():
    body  = request.get_json(silent=True) or {}
    email = _text(body.get("email"))
    if not email:
        return jsonify({"error": "נא להזין מייל"}), 422
    # An invalid format isn't information about who is registered, so it may be
    # said out loud, unlike the response below, which always says "sent" so as
    # not to reveal which addresses exist. Without this, whoever typed a broken
    # address got "link sent" and waited for it in vain.
    if not _looks_like_email(email):
        return jsonify({"error": "כתובת המייל אינה תקינה"}), 422

    redirect_to = request.host_url.rstrip("/") + url_for("reset_password")
    ok, err = db.send_reset_email(email, redirect_to)

    # The result was thrown away. Supabase's email provider is limited to two
    # emails an hour for the whole project, so the third in an hour got "we sent
    # you a link" and received nothing, with no other recovery route, since
    # email confirmation is off too. Nothing was logged, and there was no sign
    # it was happening.
    if not ok:
        # `error` and not `warning`: this is an event in Sentry, not a
        # breadcrumb nobody sees in Railway's log buffer.
        logger.error("send_reset_email failed: %s", err)
        # A rate limit at the provider is a temporary state, and it is said.
        # Other failures stay generic so as not to reveal which addresses are
        # registered.
        transient = "rate" in str(err).lower() or "limit" in str(err).lower()
        return jsonify({
            "error": ("יותר מדי בקשות איפוס ברגע זה. נסו שוב בעוד כמה דקות."
                      if transient else
                      "לא הצלחנו לשלוח את הקישור כרגע. נסו שוב בעוד כמה דקות."),
        }), 503

    # Always return success: don't reveal which emails are registered
    return jsonify({"status": "ok"})


@app.route("/reset-password")
def reset_password():
    """The set-a-new-password page; the token arrives in the fragment of the
    email link."""
    return render_template("reset_password.html")


@app.route("/api/auth/reset", methods=["POST"])
# Unauthenticated, and takes a recovery token from the request body, i.e. open
# to token guessing. `/api/auth/forgot` next to it was already limited to 3 a
# minute; this one was forgotten.
@limiter.limit("5 per minute")
def reset_password_submit():
    body             = request.get_json(silent=True) or {}
    access_token     = body.get("access_token", "")
    password         = body.get("password", "")
    password_confirm = body.get("password_confirm", "")

    if not access_token:
        return jsonify({"error": "קישור האיפוס לא תקין או שפג תוקפו"}), 400
    if passwords.problem(password):
        return jsonify({"error": passwords.problem(password)}), 422
    if password != password_confirm:
        return jsonify({"error": "הסיסמאות אינן תואמות"}), 422

    ok, err = db.update_password(access_token, password)
    if not ok:
        logger.error("reset password: %s", err)
        return jsonify({"error": "האיפוס נכשל — נסו לבקש קישור חדש"}), 400
    return jsonify({"status": "ok"})


# ─── Onboarding (new families only) ─────────────────────────────────────────

# A generic starter set: every new family can edit, delete or add to it.
# (No specific names like "קופת גמל אנליסט" that are relevant only to one family)
_DEFAULT_CATEGORIES = [
    # Expenses and income: exactly as Matan has them today, in the same order
    # (Matan, 13.10)
    {"name": "משכורת",               "icon": "💼", "type": "income"},
    {"name": "הכנסה נוספת",          "icon": "💵", "type": "income"},
    {"name": "דיור ושכירות",         "icon": "🏠", "type": "expense"},
    {"name": "חשבונות ומנויים",      "icon": "💳", "type": "expense"},
    {"name": "רכב ותחבורה",          "icon": "🚗", "type": "expense"},
    {"name": "בריאות",               "icon": "🏥", "type": "expense"},
    {"name": "סופר ומזון",           "icon": "🛒", "type": "expense"},
    {"name": "מסעדות ובילויים",      "icon": "🥂", "type": "expense"},
    {"name": "שופינג וקניות",        "icon": "🛍️", "type": "expense"},
    {"name": "טיפוח",                "icon": "💅", "type": "expense"},
    {"name": "ריהוט ואביזרים לבית",  "icon": "🪑", "type": "expense"},
    {"name": "חופשות",               "icon": "✈️", "type": "expense"},
    {"name": "אירועים ומתנות",       "icon": "🎁", "type": "expense"},
    {"name": "אחר",                  "icon": "📦", "type": "expense"},
    # Savings: two (Matan, 13.10)
    {"name": "השקעות",               "icon": "📈", "type": "savings"},
    {"name": "פיקדון בנקאי",         "icon": "🏧", "type": "savings"},
]


@app.route("/onboarding")
@login_required
def onboarding():
    user = get_current_user()
    if not user["family_id"] or not db.family_needs_onboarding(user["family_id"]):
        return redirect(url_for("dashboard"))

    family = db.get_family(user["family_id"])

    # Someone who joined a family that hasn't finished setup lands here, because
    # "needs the wizard" is measured by the absence of categories. Until now
    # they saw the welcome screen ("new family or join?"), which looks exactly
    # like a failure, and its obvious way out is "open a new family", i.e.
    # splitting the family in two.
    #
    # If there is someone else in the family, the join succeeded and there is
    # nothing to set up: whoever opened the family hasn't finished yet. That is
    # what needs saying.
    #
    # And only to someone who isn't the manager. Whoever opened the family and
    # had a member join mid-wizard must keep seeing the wizard; otherwise the
    # join locks them out of setup, and neither of the two can move forward.
    try:
        others = [m for m in db.get_family_members(user["family_id"])
                  if m.get("id") != user["id"]]
    except db.DataUnavailable:
        others = []
    if family.get("manager_id") == user["id"]:
        others = []

    return render_template(
        "onboarding.html",
        user=user,
        family=family,
        waiting_for=others[0].get("name") if others else None,
        default_categories=_DEFAULT_CATEGORIES,
    )


@app.route("/api/onboarding/complete", methods=["POST"])
@limiter.limit("10 per minute")
@login_required
def onboarding_complete():
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף, ואם זה חוזר התחברו מחדש"}), 400
    if not db.family_needs_onboarding(user["family_id"]):
        return jsonify({"error": "ההגדרה הראשונית כבר הושלמה"}), 400

    body = request.get_json(silent=True) or {}
    family_name = _text(body.get("family_name"))
    categories  = body.get("categories") or []

    if not categories:
        return jsonify({"error": "נא לבחור לפחות קטגוריה אחת"}), 422

    if family_name:
        db.update_family_name(user["family_id"], family_name)

    # The "how would you like to track?" step: attributing transactions to
    # family members, per the family's choice
    if isinstance(body.get("owner_attribution"), dict):
        oa = body["owner_attribution"]
        db.update_family_settings(user["family_id"], {"owner_attribution": {
            k: bool(oa.get(k, False)) for k in ("expense", "income", "savings")
        }})

    count, err = db.bulk_add_categories(user["family_id"], categories)
    if err:
        logger.error("onboarding bulk_add_categories: %s", err)
        return jsonify({"error": "שמירת הקטגוריות נכשלה — נסו שוב"}), 500

    _notify_owner_about_family("new", user["family_id"], family_name, person_id=user["id"])
    return jsonify({"status": "ok", "categories_created": count})


def _notify_owner_about_family(kind: str, family_id: str, family_name: str = None,
                               person_id: str = None):
    """An email to the site owner (Matan, 5.10): a family finished the onboarding
    wizard, or someone joined a family. The family name and how many members it
    has now. Fetched here, in the request; the sending itself runs on a thread
    with no database access (see `notify.py`). Decoration: no failure here
    breaks a signup or a join.

    `person_id`: whoever opened the family or just joined: name, email and phone
    (Matan, 5.10). From the member list already fetched, with no extra query."""
    try:
        members = db.get_family_members(family_id)
        name = family_name or (db.get_family(family_id) or {}).get("name") or "בלי שם"
        count = len(members)
        when = clock.now().strftime("%d.%m.%Y %H:%M")
        person = next((m for m in members if m.get("id") == person_id), None) if person_id else None
        if kind == "new":
            subject = f"משפחה חדשה ב-SmartFin: {name}"
            lines = [f"משפחה חדשה נרשמה: {name}", f"חברי משפחה: {count}", f"מתי: {when}"]
            who_title = "מי פתח אותה:"
        else:
            subject = f"מישהו הצטרף ל{name} ב-SmartFin"
            lines = [f"בן משפחה חדש הצטרף ל{name}", f"חברי משפחה עכשיו: {count}", f"מתי: {when}"]
            who_title = "מי הצטרף:"
        if person:
            lines += ["", who_title,
                      f"שם: {person.get('full_name') or person.get('name') or '—'}",
                      f"מייל: {person.get('email') or '—'}",
                      f"טלפון: {person.get('phone') or 'לא הוזן'}"]
        notify.notify_owner(subject, lines)
        if kind == "joined":
            # And to the family members themselves: a phone notification (Matan,
            # 31.10). The call runs as the joiner, who is already a member, and
            # returns everyone else's devices.
            joiner = (person or {}).get("name") or (person or {}).get("full_name") or ""
            push.send_in_background(db.push_family_targets(), push.member_joined(joiner, name))
    except Exception:
        logger.exception("notify owner about family (%s)", kind)


def _sync_recurring(family_id):
    """Fills in occurrences of recurring transactions, once a day per user.

    Two things were fixed here together.

    First, it used to run only from the dashboard. Whoever went straight to the
    month page (from a bookmark, the bottom menu or a link) saw a month without
    the salary and the standing orders, and nothing explained why. The numbers
    were fixed only if they happened to pass through the home page.

    Second, the "synced for today" mark was written even when creation failed,
    and then the next attempt was only the next day. A failure that looks like
    success leaves a whole month incomplete.

    Marked only after success, so a passing failure is resolved on the next
    page load."""
    if not family_id:
        return
    today = clock.today()
    today_str = today.isoformat()
    if session.get("recurring_synced") == today_str:
        return
    # No recurring charge today: don't check (Matan 1.11). From the copy in the
    # session, which is refreshed from the family's settings on every page
    # (`inject_family_settings`), with no query here.
    if db.recurring_can_wait(session.get("recurring_next"), today):
        session["recurring_synced"] = today_str
        return
    _, ok = db.materialize_recurring(family_id)
    if ok:
        session["recurring_synced"] = today_str


# ─── Main pages (5 pages: home · month · comparison · projects · settings) ──

def _home_page_separately(family_id, user, now):
    """The old way to the home page: a query per part. Only when `get_home_page`
    is unavailable. `None` = no categories (needs the onboarding wizard)."""
    # The batch of independent queries (run one after another; see _run_queries)
    batch = _run_queries({
        "settings":   lambda: db.get_family_settings(family_id),
        "summary":    lambda: db.get_monthly_summary(family_id, now.year, now.month),
        "categories": lambda: db.get_categories(family_id),
        "members":    lambda: db.get_family_members(family_id),
        "is_new":     lambda: db.family_has_no_transactions(family_id),
    })

    # "Needs onboarding" == zero categories, derived from the categories already
    # fetched (no extra query)
    if not batch["categories"]:
        return None

    # "Recent transactions" depend on the settings, so they are fetched once we
    # have them. The last ten **entered**, in entry order, newest on top (Matan,
    # 30.9). The day is written inside each row ("היום · רמי לוי"), because in
    # entry order per-day headers would repeat themselves.
    transactions = _timed("recent", lambda: db.get_recent_transactions(family_id, limit=10,
                          settings=batch["settings"], viewer_user_id=user["id"]))
    # "This week" (Matan, 30.9): decoration; a failure in it doesn't break the
    # home page
    try:
        week = _timed("week", lambda: db.week_spending(family_id))
    except db.DataUnavailable:
        week = None
    batch["transactions"] = transactions
    batch["week"] = week
    return batch


@app.route("/")
def dashboard():
    """The root serves two different things: a public landing page to whoever
    isn't signed in, and the dashboard to whoever is.

    Until now it was protected by login_required and redirected straight to
    /login, so anyone who got a link landed on a sign-in form without knowing
    what it is or why they should hand over financial data. The function name
    stays dashboard so every existing url_for keeps working.

    Someone not signed in gets one of two: a device that never signed in from
    here sees the landing page, and a known device skips it straight to the
    sign-in form. A marketing page is the right answer for a guest, not for
    someone who just wanted into their budget. `?intro=1` is the way to see the
    landing page anyway; the "back" button on the sign-in page comes from there."""
    if "user_id" not in session:
        # `?app=1`: opened from the home-screen icon (start_url in the manifest).
        # Whoever installed the app already chose it; a marketing page isn't
        # what they need to see. This happened to people who installed before
        # signing in: the installed app on iPhone has its own cookies, and
        # `sf_returning` wasn't in them yet.
        opened_as_app = request.args.get("app") == "1"
        if (_device_is_known() or opened_as_app) and not request.args.get("intro"):
            response = redirect(url_for("login"))
        else:
            # The month is derived from the clock, not hard-coded in the
            # template. A fixed "September 2026" was right on the day it was
            # written and wrong from October 1st, and an old date on a
            # marketing page reads like an abandoned project.
            now = clock.now()
            response = make_response(render_template(
                "landing.html", demo_month=_month_label(now.year, now.month)))
        # The content here depends on sign-in state and the device cookie, so it
        # must not be stored in any cache, and Vary tells that to any proxy on
        # the way too.
        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = "Cookie"
        return response

    user      = get_current_user()
    now       = clock.now()
    family_id = user["family_id"]

    if not family_id:
        return render_template(
            "index.html", active_page="dashboard", user=user,
            summary=db._empty_summary(), transactions=[],
            member_colors={}, month_label=_month_label(now.year, now.month),
            month=now.month, is_new_family=True,
        )

    # May write rows, so before the query batch
    _timed("recurring", lambda: _sync_recurring(family_id))

    # One trip to the database (Matan, 13.10): `get_home_page` brings everything
    # that used to be nine queries. If it is unavailable (the migration hasn't
    # run yet, a failure), the old way.
    month_rows = None
    try:
        batch = _timed("home_page", lambda: db.fetch_home_page(
            family_id, clock.today(), 10, user["id"]))
        month_rows = batch["rows"]
    except db.DataUnavailable:
        logger.warning("home_page rpc unavailable — falling back to separate queries")
        batch = None

    if batch is None:
        batch = _home_page_separately(family_id, user, now)
        if batch is None:
            return redirect(url_for("onboarding"))
    elif not batch["categories"]:
        return redirect(url_for("onboarding"))

    transactions = db.recent_from_rows(batch["recent"], 10, batch["settings"], user["id"]) \
        if "recent" in batch else batch["transactions"]
    week = batch["week"]

    # Recycling the batch results into the context processors and
    # _member_colors (no repeat query)
    _prime_request_cache(family_id, settings=batch["settings"], categories=batch["categories"],
                         members=batch["members"], family=batch.get("family"))

    # The month's alerts, only here, when there are any (Matan, 11.10): a
    # category over budget. A tap leads to the category on the month page. The
    # month's rows are fetched only when there are budgets; without them there
    # is nothing to check. Decoration: a failure doesn't break the home page.
    alerts = []
    if (batch["settings"] or {}).get("limits"):
        try:
            alerts = db.month_alerts(db.apply_budgets(db.category_breakdown_from_rows(
                month_rows if month_rows is not None
                else _timed("alerts", lambda: db.fetch_month_rows(family_id, now.year, now.month)),
                batch["categories"], "expense"),
                batch["settings"]))
        except db.DataUnavailable:
            pass

    # "September closed" (Matan, 13.10), only on the first day of the month.
    # Decoration: a failure doesn't break the home page.
    try:
        recap = db.month_recap(family_id, clock.today(), batch["categories"])
    except db.DataUnavailable:
        recap = None

    # A pinned project (Matan, 12.10): "Greece trip: ₪7,800 left of ₪12,000"
    # under the balance. Only when someone pinned one; otherwise no query. A
    # finished or deleted project: the card doesn't appear. Decoration: a
    # failure doesn't break the home page.
    pinned_project = None
    pinned_id = ((batch["settings"] or {}).get("pinned_projects") or {}).get(user["id"])
    if pinned_id:
        try:
            pinned_project = next((p for p in _timed("pinned", lambda: db.get_projects(family_id, user["id"]))
                                   if p["id"] == pinned_id), None)
        except db.DataUnavailable:
            pinned_project = None

    # "How to add a family member" (Matan, 5.10): on the first visit, after the
    # onboarding wizard (`?welcome=1` from the "to the app" button), and only
    # while there is nobody else in the family yet
    welcome_invite = None
    if request.args.get("welcome") == "1" and len(batch["members"] or []) == 1:
        try:
            family = _fresh_invite(db.get_family(family_id), user)
        except db.DataUnavailable:
            family = None          # decoration: a failure doesn't break the home page
        if family and family.get("invite_code") and not _invite_expired(family):
            welcome_invite = {"code": family["invite_code"], "family_name": family.get("name") or ""}

    return render_template(
        "index.html",
        active_page="dashboard",
        user=user,
        summary=batch["summary"],
        transactions=transactions,
        week=week,
        member_colors=_member_colors(family_id),
        month_label=_month_label(now.year, now.month),
        month=now.month,
        is_new_family=batch["is_new"],
        welcome_invite=welcome_invite,
        alerts=alerts,
        recap=recap,
        # "Your 2026" (Matan, 1.11): a card on January 1–15; the summary loads
        # on tap
        year_recap_year=db.year_recap_due(clock.today()),
        hebrew_months=_HEBREW_MONTHS,
        pinned_project=pinned_project,
        # For "a family member joined" (`whats-new.js`): id, first name and
        # colour, no other details
        home_members=[{"id": m["id"], "name": m.get("name") or "",
                       "color": _member_colors(family_id).get(m["id"])}
                      for m in (batch["members"] or [])],
    )


@app.route("/api/year-recap")
@limiter.limit("30 per minute")
@login_required
def year_recap_api():
    """"Your 2026": the year summary, opens on tap (Matan, 1.11). Only whoever
    taps sees it."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא משויכת משפחה לחשבון"}), 400
    today = clock.today()
    year = request.args.get("year", type=int) or (db.year_recap_due(today) or today.year)
    if not 2000 <= year <= today.year:
        return jsonify({"error": "אין סיכום לשנה הזו"}), 422
    try:
        return jsonify(db.year_recap(user["family_id"], user["id"], year, today))
    except db.DataUnavailable:
        logger.exception("year_recap")
        return jsonify({"error": "לא הצלחנו לטעון את הסיכום — נסו שוב"}), 503


@app.route("/month")
@login_required
def month_view():
    """The month page: all the data and charts for a given month (default: the
    current one)."""
    user      = get_current_user()
    now       = clock.now()
    # type=int checks it is a number, not that it is an existing month:
    # ?month=99999999 crashed the page with a 500 until it was found. Fall back
    # to the current month instead of blowing up.
    year      = request.args.get("year",  now.year,  type=int)
    month     = request.args.get("month", now.month, type=int)
    if not 1 <= (month or 0) <= 12:
        month = now.month
    if not 1970 <= (year or 0) <= 2100:
        year = now.year
    family_id = user["family_id"]

    # Before the queries: without this a month opened directly (bookmark,
    # menu, link) is shown without the salary and the standing orders
    _timed("recurring", lambda: _sync_recurring(family_id))

    is_current = (year == now.year and month == now.month)
    # The direction of the "back to the current month" arrow: from a future
    # month, the current month is backwards
    is_future = (year, month) > (now.year, now.month)

    if not family_id:
        return render_template(
            "month.html", active_page="month", user=user,
            summary=db._empty_summary(), expense_breakdown=[], income_breakdown=[], income_sources=[],
            savings_breakdown=[], member_breakdowns=[], month_transactions=[],
            member_colors={}, month_label=_month_label(year, month), year=year, month=month,
            strip_months=[{"year": year, "month": month}], hebrew_months=_HEBREW_MONTHS,
            is_current=is_current, is_future=is_future, summary_data=db._empty_summary(),
            expense_data=[], members_data=[], income_data=[], savings_data=[], savings_home=[],
            # The template accesses project_month unconditionally. This route
            # was forgotten when projects were added, and a user with no family
            # got a 500 instead of the intended empty page.
            project_month={"expense": 0, "income": 0, "savings": 0,
                           "transactions": [], "projects": []},
        )

    # Stage 1: independent queries. The month's rows are fetched **once**, and
    # every summary is derived from them: the summary, the three category
    # breakdowns, the two family-member breakdowns and the transaction list used
    # to be seven queries over exactly the same rows.
    #
    # The time saved (about 180ms) is the less important side. The main point is
    # that each of the seven re-derived the same two rules for itself (excluding
    # project transactions, and hiding another family member's personal
    # project), and the second rule was already forgotten once (see the note on
    # matching categories by name, below). Now it is applied once.
    # ...and the five queries that remained are now one trip. `_run_queries`
    # runs them in sequence (see the note there: parallelism on the shared
    # client silently returned zeros), so the five paid ~30ms each, one after
    # another. `get_month_page` returns all five together; the assembly stayed
    # in Python and wasn't moved to SQL, so there aren't two sources of truth
    # for `_merge_settings` and name shortening.
    p1 = _timed("month_page", lambda: db.fetch_month_page(family_id, year, month))
    settings_ = p1["settings"]
    rows      = p1["rows"]
    # A future month (Matan, 1.11): the recurring transactions expected in it
    # appear and are counted, until the series ends, "like a forecast". Marked
    # "expected" and not opened for editing. In the current month, as before: a
    # recurring transaction appears from the day it is charged, when the engine
    # creates it. (From 4.10 to 1.11 a future month didn't show them either.)
    if is_future:
        rows = list(rows) + db.projected_recurring_rows(family_id, user["id"], year, month)
        # Like the query itself, newest first, so the projected ones go into
        # their place and not at the end
        rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)

    # Fed here and not right before `render_template` as before: stage 2
    # queries (overages, pace, standing orders) access the categories and
    # family members themselves, and now they get them from the batch instead
    # of querying again mid-request.
    _prime_request_cache(family_id, settings=settings_, members=p1["members"],
                         categories=p1["categories"], family=p1["family"])

    summary = db.summary_from_rows(rows)
    # Category budgets are added to the expense breakdown: the bar measures
    # against the family's decision instead of against the month's total
    # expenses
    expense_breakdown = db.apply_budgets(
        db.category_breakdown_from_rows(rows, p1["categories"], "expense"), settings_)
    income_breakdown  = db.category_breakdown_from_rows(rows, p1["categories"], "income")
    savings_breakdown = db.category_breakdown_from_rows(rows, p1["categories"], "savings")
    # A fixed colour per category (Matan, 12.10): by its place in the family's
    # category list, not its place in the month's breakdown, so "סופר" is gold
    # in every month and the ring can "flow" between months. A project: by id,
    # also stable.
    _cat_order = {c["id"]: i for i, c in enumerate(c for c in (p1["categories"] or [])
                                                   if c.get("type") == "expense")}
    for c in expense_breakdown:
        if c.get("is_project"):
            c["color_idx"] = sum(map(ord, str(c.get("project_id") or c.get("name") or "")))
        else:
            c["color_idx"] = _cat_order.get(c.get("category_id"), len(_cat_order))
    _distinct_colors(expense_breakdown)

    # The month strip: oldest to newest (the RPC returns newest first), and it
    # always includes the viewed month, even if it has no transactions and so
    # doesn't come back from the RPC
    strip_months = [{"year": m["year"], "month": m["month"]} for m in reversed(p1["archive"] or [])]
    if not any(m["year"] == year and m["month"] == month for m in strip_months):
        strip_months.append({"year": year, "month": month})
        strip_months.sort(key=lambda m: (m["year"], m["month"]))

    # Stage 2: queries that depend on the settings/summary.
    # The "by family member" chart, not when the family is one person: one bar
    # says nothing (Matan, 5.10; as on the comparison page)
    active_types = [t for t in ("expense", "income", "savings")
                    if settings_["owner_attribution"].get(t) and len(p1["members"] or []) > 1]
    month_transactions = db.month_transactions_from_rows(rows, settings_, user["id"])
    # The category row under "expenses" etc. in "all transactions" (Matan, 30.9)
    tx_cat_chips = db.category_filter_chips(month_transactions)

    # Project activity this month: excluded from the balance/charts and shown
    # separately. Derived from the transactions already fetched (which include
    # project transactions), with no extra query.
    project_txs = [t for t in month_transactions if t.get("project_id")]
    project_month = {
        "expense":      round(sum(t["amount"] for t in project_txs if t["type"] == "expense"), 2),
        "income":       round(sum(t["amount"] for t in project_txs if t["type"] == "income"), 2),
        "savings":      round(sum(t["amount"] for t in project_txs if t["type"] == "savings"), 2),
        "transactions": project_txs,
        "projects":     db.project_breakdown_from_rows(project_txs),
    }

    # And from here on, only the regular transactions. All the rest of the
    # month's data (summary, category breakdown, split between family members,
    # overages) already filters project transactions in the query itself; this
    # list was the only one that didn't, and the template matches transactions
    # to categories by *name*. A project category with the same name as a family
    # category (e.g. "אחר", seeded in both) made the project transaction appear
    # inside the monthly category, without being counted in its total.
    month_transactions = [t for t in month_transactions if not t.get("project_id")]

    # Fixed versus variable (Matan, 12.10): what would go out anyway (a
    # recurring transaction, template or occurrence, including installments) and
    # what depends on you. Only when there are recurring ones; without them
    # there is nothing to split.
    _exp = [t for t in month_transactions if t["type"] == "expense"]
    _fixed = [t for t in _exp if t.get("is_recurring") or t.get("recurring_parent_id")]
    fixed_split = None
    if _fixed:
        fixed_total = round(sum(t["amount"] for t in _fixed), 2)
        exp_total = round(sum(t["amount"] for t in _exp), 2)
        fixed_split = {"fixed": fixed_total, "variable": round(exp_total - fixed_total, 2)}

    # A split chart between family members for every transaction type the
    # family turned attribution on for
    _type_labels = {"expense": "הוצאות", "income": "הכנסות", "savings": "חיסכון"}
    mcolors = _member_colors(family_id)

    # The income and savings cards: a donut like expenses (Matan, 30.9). A salary
    # in the family member's colour (the same colour as their name tag), and
    # other income in a neutral grey. The percentages sum to exactly 100
    # (`share_map`).
    income_sources = db.income_sources(month_transactions)
    income_pcts = share_map([{"name": s["label"], "total": s["total"]} for s in income_sources])
    for src in income_sources:
        idx = mcolors.get(src["transactions"][0].get("user_id")) if src["who"] else None
        src["color"] = _OWNER_HEX.get(idx, _SHARED_HEX) if idx is not None else (
            _SHARED_HEX if src["kind"] == "משכורת" else _EXTRA_INCOME_HEX)
        src["pct"] = income_pcts.get(src["label"], 0)
    savings_home = [c for c in savings_breakdown if c.get("total", 0) > 0 and not c.get("is_project")]
    savings_pcts = share_map(savings_home)
    for i, c in enumerate(savings_home):
        c["share"] = savings_pcts.get(c["name"], 0)
        c["color"] = _SAVINGS_HEX[i % len(_SAVINGS_HEX)]
    member_breakdowns = []
    for t in active_types:
        # `mb` and not `rows`: `rows` is the month's rows, and overwriting it
        # here would poison everything derived from it later
        mb = db.member_breakdown_from_rows(rows, t)
        for r in mb:
            idx = mcolors.get(r.get("user_id"))
            r["color"] = _OWNER_HEX.get(idx, _SHARED_HEX) if idx is not None else _SHARED_HEX
        # One bar compares nothing: at the start of a month, when only one
        # person had entered anything, it was a half-screen chart with a single
        # bar (Matan, 12.10). "Shared" counts as a bar.
        if len(mb) >= 2:
            member_breakdowns.append({"type": t, "label": _type_labels[t], "rows": mb})

    return render_template(
        "month.html",
        active_page="month",
        user=user,
        summary=summary,
        expense_breakdown=expense_breakdown,
        income_breakdown=income_breakdown,
        income_sources=income_sources,
        savings_home=savings_home,
        savings_breakdown=savings_breakdown,
        member_breakdowns=member_breakdowns,
        fixed_split=fixed_split,
        month_transactions=month_transactions,
        tx_cat_chips=tx_cat_chips,
        project_month=project_month,
        member_colors=_member_colors(family_id),
        month_label=_month_label(year, month),
        year=year,
        month=month,
        strip_months=strip_months,
        hebrew_months=_HEBREW_MONTHS,
        is_current=is_current, is_future=is_future,
        summary_data=summary,
        # Only active categories (total>0), so the colour indexes in the donut
        # match the legend (also filtered to active), and without zero-width slices.
        expense_data=[c for c in expense_breakdown if c.get("total", 0) > 0],
        income_data=[{"name": s["label"], "total": s["total"], "color": s["color"], "pct": s["pct"]}
                     for s in income_sources],
        savings_data=[{"name": c["name"], "total": c["total"], "pct": c["share"], "color": c["color"]}
                      for c in savings_home],
        members_data=member_breakdowns,
    )


@app.route("/account.json")
@login_required
@limiter.limit("5 per hour")
def export_account():
    """A full data export: Article 20 (portability) of the GDPR.

    Until now the only export was `/month.csv`: a single month, from the month
    page, with no profile, no categories and no projects. So "take your data and
    go" was possible only month by month by hand, and that isn't portability.

    JSON and not CSV, because these are several tables with relations between
    them, and one flat file would lose them. `/month.csv` stays for
    spreadsheets.

    `5 per hour`: this is a query that pulls the family's entire history.
    """
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלך"}), 400

    data = db.export_account_data(user["family_id"], user["id"])

    stamp = clock.now().strftime("%Y-%m-%d")
    return Response(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="smartfin-{stamp}.json"'},
    )


# A cell starting with one of these is a formula in Excel (CSV injection)
_CSV_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_text(value) -> str:
    """Free text for a CSV cell. A leading apostrophe tells Excel "text, not a
    formula": "`-50 הנחה`" won't become `#NAME?`, and a malicious description
    won't run for whoever opens it."""
    text = str(value or "")
    return "'" + text if text.startswith(_CSV_FORMULA_START) else text


@app.route("/month.csv")
@limiter.limit("10 per minute")
@login_required
def month_csv():
    """Exports the month's transactions as CSV.

    Data you can take out is data you can trust, and this is the cheapest
    answer to "what happens if I want to leave". Every conversation with an
    accountant starts here too.

    Two details decide whether the file really opens correctly in Hebrew:
    · A BOM at the start of the file: without it Excel reads UTF-8 as a local
      encoding, and every description and category turns into gibberish. That
      is the number one complaint about Hebrew CSV exports, and it looks like a
      bug in the app rather than in the program opening it.
    · `\r\n` as the line ending: what Excel expects."""
    user      = get_current_user()
    now       = clock.now()
    year      = request.args.get("year",  now.year,  type=int)
    month     = request.args.get("month", now.month, type=int)
    if not 1 <= (month or 0) <= 12:
        month = now.month
    if not 1970 <= (year or 0) <= 2100:
        year = now.year

    rows = []
    if user["family_id"]:
        # The same query that feeds the month page, including filtering out
        # another family member's personal project: the export mustn't reveal
        # what the screen hides
        rows = db.get_month_transactions(
            user["family_id"], year, month,
            settings=family_settings(), viewer_user_id=user["id"])

    _TYPE_HE = {"expense": "הוצאה", "income": "הכנסה", "savings": "חיסכון"}

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["תאריך", "מחלקה", "סכום", "קטגוריה", "תיאור",
                     "בן משפחה", "פרויקט", "עסקה קבועה"])
    for tx in rows:
        writer.writerow([
            tx["date"],
            _TYPE_HE.get(tx["type"], tx["type"]),
            f'{tx["amount"]:.2f}',
            _csv_text(tx.get("category_name")),
            _csv_text(tx.get("description")),
            _csv_text(tx.get("user_name")),
            _csv_text(tx.get("project_name")),
            "כן" if tx.get("is_recurring") or tx.get("recurring_parent_id") else "",
        ])

    response = make_response("\ufeff" + buffer.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="smartfin-{year}-{month:02d}.csv"')
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/months")
@login_required
def months():
    """The comparison page: the current month against previous months + entry to
    each month."""
    user      = get_current_user()
    family_id = user["family_id"]
    _sync_recurring(family_id)
    now       = clock.now()
    # One trip to the database (Matan, 13.10): `get_months_page`; if
    # unavailable, as before
    page = None
    if family_id:
        try:
            page = _timed("months_page", lambda: db.fetch_months_page(family_id, clock.today()))
        except db.DataUnavailable:
            logger.warning("months_page rpc unavailable — falling back to separate queries")
    if page is not None:
        _prime_request_cache(family_id, settings=page["settings"], categories=page["categories"],
                             members=page["members"], family=page["family"])
    archive   = (page["archive"] if page is not None
                 else db.get_months_archive(family_id) if family_id else [])
    trend     = db.monthly_trend(archive, 12)
    months_shown = [(t["year"], t["month"]) for t in trend[-12:]]
    # "By category" (Matan, 30.9): the same months as the chart above. 12 are
    # sent, and the browser shows 3 / 6 / 12 by the chosen range (idea 24)
    if page is not None:
        cat_trend = db.category_trend_from_rows(page["expense_rows"], months_shown, page["categories"])
    else:
        cat_trend = db.category_trend(family_id, months_shown,
                                      db.get_categories(family_id)) if family_id else None
    # "Expenses by family member" over the months (idea 15), only when expense
    # attribution is on and there is more than one family member. Decoration: a
    # failure doesn't break the page
    member_series = None
    if family_id and family_settings()["owner_attribution"].get("expense") \
            and len(db.get_family_members(family_id)) > 1:
        try:
            series = (db.member_trend_from_rows(page["expense_rows"], months_shown) if page is not None
                      else db.member_trend(family_id, months_shown))
        except db.DataUnavailable:
            series = []
        mcolors = _member_colors(family_id)
        for m in series:
            idx = mcolors.get(m["user_id"]) if m["user_id"] else None
            m["color"] = _OWNER_HEX.get(idx, _SHARED_HEX) if idx is not None else _SHARED_HEX
        member_series = series if len(series) > 1 else None
    return render_template("months.html", active_page="months", user=user,
                           archive=archive, trend_data=trend, cat_trend=cat_trend,
                           verdict=db.trend_verdict(trend),
                           member_series=member_series,
                           today_year=now.year, today_month=now.month,
                           # "2026 עד עכשיו" all year, including December; in January
                           # "סיכום 2026" (Matan, 1.11: "the year isn't over yet")
                           recap_year=now.year - 1 if now.month == 1 else now.year,
                           recap_partial=now.month > 1,
                           _HEBREW_MONTHS=_HEBREW_MONTHS)


@app.route("/projects")
@login_required
def projects():
    """The projects page: a budget per project (trip, renovation, event and more)."""
    user      = get_current_user()
    family_id = user["family_id"]
    # One trip to the database (Matan, 13.10): `get_projects_page`; if
    # unavailable, as before
    page = None
    if family_id:
        try:
            page = _timed("projects_page", lambda: db.fetch_projects_page(family_id, user["id"]))
        except db.DataUnavailable:
            logger.warning("projects_page rpc unavailable — falling back to separate queries")
    if page is not None:
        _prime_request_cache(family_id, settings=page["settings"], members=page["members"],
                             family=page["family"])
        project_list, finished, last = page["active"], page["finished"], page["last_activity"]
    else:
        project_list = db.get_projects(family_id, user["id"]) if family_id else []
    # "Finished projects": a collapsed section at the bottom (Matan, 30.9)
        finished = db.get_projects(family_id, user["id"], archived=True) if family_id else []
        last = db.project_last_activity(family_id) if len(project_list) > 1 else {}
    # The project with the most recent transaction comes first (Matan, 12.10).
    # Those without transactions come after them, in the order they arrived
    # (newest first). `sorted` is stable, so both orders are kept.
    if len(project_list) > 1:
        project_list = sorted(project_list, key=lambda p: last.get(p["id"]) or "", reverse=True)
    return render_template("projects.html", active_page="projects", user=user,
                           projects=project_list, finished_projects=finished)


def _member_names(family_id) -> dict:
    """id → first name, for the "recorded by" mark. Whoever is no longer in the
    family isn't in the list."""
    return {m["id"]: m["name"] for m in db.get_family_members(family_id)} if family_id else {}


def _others_contributions(project: dict, viewer_id: str, names: dict) -> str:
    """"10 transactions were recorded here by Or": for confirming "make personal
    again".

    Making it personal again hides the project from everyone else, including
    what they themselves recorded in it (Matan's decision, 28.9.2026). So it is
    said upfront, with names and numbers. Transactions where it isn't known who
    recorded them (from before we started storing that) aren't counted."""
    from collections import Counter
    counts = Counter(names.get(t["created_by"], "בן משפחה לשעבר")
                     for t in project.get("transactions") or []
                     if t.get("created_by") and t["created_by"] != viewer_id)
    # Gender-neutral (Matan, 1.10)
    parts = [(f"עסקה אחת נרשמה כאן על ידי {name}" if n == 1
              else f"{n} עסקאות נרשמו כאן על ידי {name}")
             for name, n in counts.most_common()]
    return " · ".join(parts)


def _project_gone():
    """A deleted project, or a truncated link: to the list, with an explanation.

    The same message for another family member's personal project too: "not
    found" and not "no permission", so the answer doesn't confirm it exists
    (see `project_access_required`)."""
    session["sf_notice"] = "הפרויקט הזה כבר לא קיים"
    return redirect(url_for("projects"))


@app.route("/projects/<project_id>")
@login_required
def project_detail(project_id):
    user      = get_current_user()
    family_id = user["family_id"]
    project = db.get_project_detail(project_id, family_id, user["id"]) if family_id else None
    if not project:
        return _project_gone()
    # "Project finished?" (Matan, 12.10): an active project with no new
    # transaction for two months
    quiet_since = None
    if not project.get("archived") and project.get("transactions"):
        last = max(str(t.get("date") or "")[:10] for t in project["transactions"])
        try:
            last_d = date.fromisoformat(last)
            if (clock.today() - last_d).days >= 60:
                quiet_since = _HEBREW_MONTHS[last_d.month] + (f" {last_d.year}" if last_d.year != clock.today().year else "")
        except ValueError:
            logger.warning("project_detail: bad transaction date %r", last)
    pinned_id = ((family_settings().get("pinned_projects") or {}).get(user["id"]))
    return render_template("project_detail.html", active_page="projects", user=user,
                           project=project, quiet_since=quiet_since,
                           is_pinned=(pinned_id == project["id"]),
                           member_colors=_member_colors(family_id),
                           member_names=_member_names(family_id))


@app.route("/projects/new")
@login_required
def project_new():
    """Creating a project: a screen of its own, not a form that opens inside the
    list (Matan, 5.10). `?icon=…&name=…`: from the starter ideas ("✈️ טיול"),
    which prefill the form."""
    user = get_current_user()
    return render_template("project_new.html", active_page="projects", user=user,
                           icon=_text(request.args.get("icon"))[:16],
                           name=_text(request.args.get("name"))[:50])


@app.route("/projects/<project_id>/edit")
@login_required
def project_edit(project_id):
    """The screen for editing the project's settings
    (name/description/target/tracking/sharing) + managing categories, separate
    from the project's clean view page."""
    user      = get_current_user()
    family_id = user["family_id"]
    project = db.get_project_detail(project_id, family_id, user["id"]) if family_id else None
    if not project:
        return _project_gone()
    return render_template("project_edit.html", active_page="projects", user=user,
                           project=project,
                           others_note=_others_contributions(project, user["id"],
                                                             _member_names(family_id)))


def _parse_project_body(body: dict):
    """Parses and validates the fields shared by creating/updating a project
    (name/target/tracked types only, not ownership: that is set separately in
    add_project_route, and changes later only through
    share_project_route/unshare_project_route).
    Returns (fields_dict, error); fields_dict is ready to pass to
    db.add/update_project."""
    name = _text(body.get("name"))
    if not name:
        return None, "נא להזין שם לפרויקט"

    # `_parse_amount` and not a bare `float()`: a negative target was saved and
    # shown as `-₪5,000`, and `1e12` overflowed `numeric(10,2)` and came back as
    # an opaque 500, exactly the dead end this function was written to prevent.
    # An empty target is a valid value (no target).
    budget_target, err = (None, None)
    if body.get("budget_target") not in (None, ""):
        budget_target, err = _parse_amount(body.get("budget_target"))
    if err:
        return None, "תקציב היעד חייב להיות מספר"

    description = _text(body.get("description"))[:200] or None
    icon = _text(body.get("icon"))[:16] or None

    track_expense = bool(body.get("track_expense", True))
    track_income  = bool(body.get("track_income", False))
    track_savings = bool(body.get("track_savings", False))
    if not (track_expense or track_income or track_savings):
        return None, "יש לבחור לפחות מחלקה אחת למעקב"

    return {
        "name": name, "budget_target": budget_target, "description": description,
        "icon": icon,
        "track_expense": track_expense, "track_income": track_income,
        "track_savings": track_savings,
    }, None


@app.route("/api/transactions/precheck", methods=["POST"])
@limiter.limit("60 per minute")
@login_required
def precheck_transaction_route():
    """Before saving a new transaction: a duplicate? an unusual amount? (Matan,
    30.9, round 6, 6–7)."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"duplicate": None, "unusual": None})
    return jsonify(db.precheck_transaction(user["family_id"], user["id"],
                                           request.get_json(silent=True) or {}))


@app.route("/api/search", methods=["GET"])
@login_required
def search_route():
    """Search across all months: the magnifying glass on the home page (Matan, 30.9)."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"results": [], "count": 0, "truncated": False,
                        "totals": {"expense": 0, "income": 0, "savings": 0}})
    q = (request.args.get("q") or "")[:80]
    return jsonify(db.search_transactions(user["family_id"], user["id"], q,
                                          settings=family_settings()))


@app.route("/api/descriptions")
@limiter.limit("60 per minute")
@login_required
def descriptions():
    """Descriptions already written, for autocomplete in the form (Matan, 3.10,
    idea 41). Loaded once when the form opens; filtering by what is typed
    happens in the browser."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"descriptions": []})
    try:
        return jsonify({"descriptions": db.recent_descriptions(user["family_id"], user["id"])})
    except db.DataUnavailable:
        return jsonify({"descriptions": []})


@app.route("/api/week")
@limiter.limit("60 per minute")
@login_required
def week_card():
    """The "this week" card for an earlier week: paging on the home page (Matan,
    2.10). Returns the card as HTML from the same template the home page
    renders, so there aren't two versions."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלכם — רעננו את הדף"}), 400
    try:
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        return jsonify({"error": "ערך לא תקין"}), 422
    if not 0 <= offset <= 1040:          # up to 20 years back
        return jsonify({"error": "ערך לא תקין"}), 422
    try:
        week = db.week_spending(user["family_id"], offset=offset)
    except db.DataUnavailable:
        return jsonify({"error": "לא הצלחנו לטעון את השבוע — נסו שוב"}), 503
    return render_template("_week_card.html", week=week)


@app.route("/api/projects", methods=["POST"])
@limiter.limit("30 per minute")
@login_required
def add_project_route():
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף, ואם זה חוזר התחברו מחדש"}), 400
    body = request.get_json(silent=True) or {}
    fields, err = _parse_project_body(body)
    if err:
        return jsonify({"error": err}), 422
    # You can create a shared project or a personal one for yourself only, never
    # a personal one for another family member (owner_id is set here from the
    # signed-in user, not from the request)
    is_personal = bool(body.get("is_personal"))
    owner_id = user["id"] if is_personal else None
    proj, err = db.add_project(user["family_id"], created_by=user["id"], owner_id=owner_id, **fields)
    if err:
        logger.error("add_project route: %s", err)
        return jsonify({"error": "יצירת הפרויקט נכשלה — נסו שוב"}), 500
    return jsonify(proj), 201


def tx_visible_required(arg: str):
    """A transaction in another family member's personal project: as if it
    doesn't exist.

    In every list it is already hidden (`_filter_hidden_personal_projects`),
    but the routes acting on a transaction by id checked only the *target*
    project. Whoever got hold of an id (and the recurring list in settings
    handed it out) could edit, delete and open the receipt. 404 and not 403:
    403 would confirm the transaction exists.

    `arg` is the name of the URL parameter (`tx_id` / `template_id`). Must sit
    under `login_required`."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            owner = db.personal_project_owner(kwargs[arg], user["family_id"]) \
                if user.get("family_id") else None
            if owner and owner != user["id"]:
                if _is_api_request():
                    return jsonify({"error": "העסקה לא נמצאה"}), 404
                return render_template("error.html", code=404,
                                       message="לא נמצאה קבלה מצורפת לעסקה הזו"), 404
            return f(*args, **kwargs)
        return wrapper
    return decorator


def project_access_required(f):
    """Blocks access to another family member's personal project.

    Reading was already protected: `get_project_detail` and `get_projects`
    filter out a personal project that isn't the viewer's. Writing wasn't: all
    eight routes filtered by family_id only. So a family member who guessed or
    saw a personal project's id could edit it, delete it with all its
    transactions, or read its categories: a project they aren't even allowed
    to see in the list.

    "Not found" and not "no permission" on purpose: a personal project is
    private, and the answer must not confirm it exists."""
    @wraps(f)
    def decorated(project_id, *args, **kwargs):
        user = get_current_user()
        project = db.get_project_for_transaction(project_id, user["family_id"])
        if not project:
            return jsonify({"error": "הפרויקט לא נמצא"}), 404
        if project.get("owner_id") and project["owner_id"] != user["id"]:
            return jsonify({"error": "הפרויקט לא נמצא"}), 404
        return f(project_id, *args, **kwargs)
    return decorated


@app.route("/api/projects/<project_id>", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
@project_access_required
def update_project_route(project_id):
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    fields, err = _parse_project_body(body)
    if err:
        return jsonify({"error": err}), 422
    ok = db.update_project(project_id, user["family_id"], **fields)
    if ok is None:
        return jsonify({"error": "הפרויקט לא נמצא — ייתכן שנמחק בינתיים"}), 404
    if not ok:
        return jsonify({"error": "עדכון נכשל"}), 500
    return jsonify({"status": "ok", **fields})


@app.route("/api/projects/<project_id>", methods=["DELETE"])
@limiter.limit("30 per minute")
@login_required
@project_access_required
def delete_project_route(project_id):
    user = get_current_user()
    # No `delete_transactions` from the body: the transactions are always
    # deleted (see `db.delete_project`), and the server doesn't trust an old tab
    # still sending "keep".
    # Collected first: after the delete no transaction points at the files, and
    # nothing else will delete them (see `db.delete_receipts` on the nightly
    # cleanup that doesn't work).
    receipts = db.receipt_paths(user["family_id"], project_ids=[project_id])
    ok, wiped = db.delete_project(project_id, user["family_id"])
    if ok:
        _delete_unused_receipts(user["family_id"], receipts)
    if not ok:
        # No row was touched: the project was already deleted by another family
        # member, or the id isn't this family's. "Deleted" on nothing is a lie.
        return jsonify({"error": "הפרויקט לא נמצא — ייתכן שנמחק בינתיים"}), 404
    # `deleted` is returned so the user sees how many transactions really
    # vanished, just like the transaction reset. "Also delete transactions"
    # carried no number at all.
    return jsonify({"status": "ok", "deleted": wiped})


@app.route("/api/projects/<project_id>/share", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
@project_access_required
def share_project_route(project_id):
    """Turns a personal project into a shared one. Only the current owner may
    (enforced in db.share_project)."""
    user = get_current_user()
    ok, err = db.share_project(project_id, user["family_id"], user["id"])
    if not ok:
        return jsonify({"error": _user_message(err)}), 422
    return jsonify({"status": "ok"})


@app.route("/api/projects/<project_id>/unshare", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
@project_access_required
def unshare_project_route(project_id):
    """Turns a shared project back into a personal one. Only whoever originally
    created it may (enforced in db.unshare_project)."""
    user = get_current_user()
    ok, err = db.unshare_project(project_id, user["family_id"], user["id"])
    if not ok:
        return jsonify({"error": _user_message(err)}), 422
    return jsonify({"status": "ok"})


@app.route("/api/projects", methods=["GET"])
@login_required
def list_projects_route():
    """`?include_archived=1`: the transaction form: finished projects too, marked,
    so an old transaction being edited doesn't fall out of its project."""
    user = get_current_user()
    archived = None if request.args.get("include_archived") == "1" else False
    return jsonify(db.get_projects(user["family_id"], user["id"], archived=archived)
                   if user["family_id"] else [])


@app.route("/api/projects/<project_id>/archive", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
@project_access_required
def archive_project_route(project_id):
    """"Project finished" (`archived: true`) / "reopen" (`false`)."""
    user = get_current_user()
    archived = (request.get_json(silent=True) or {}).get("archived")
    if not isinstance(archived, bool):
        return jsonify({"error": "ערך לא תקין"}), 422
    if not db.set_project_archived(project_id, user["family_id"], archived):
        return jsonify({"error": "הפרויקט לא נמצא — ייתכן שנמחק בינתיים"}), 404
    return jsonify({"status": "ok", "archived": archived})


@app.route("/api/app-open", methods=["POST"])
@limiter.limit("30 per minute")
@login_required
def app_open_route():
    """The app was opened or came back to the screen (Matan, 10.11.2026).

    Called in the background by `core.js`; the database skips a repeat within 5
    minutes. Always 204: the page doesn't wait for it and shows nothing either
    way, and whether it was recorded is not the browser's business."""
    db.log_app_open()
    return "", 204


@app.route("/api/projects/<project_id>/pin", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
@project_access_required
def pin_project_route(project_id):
    """"Pin to home" (Matan, 12.10): one project per family member, in the
    family settings under `pinned_projects[user_id]`, merged by key, so two
    family members pinning at the same moment don't overwrite each other.
    `null`: no pin."""
    user = get_current_user()
    pinned = (request.get_json(silent=True) or {}).get("pinned")
    if not isinstance(pinned, bool):
        return jsonify({"error": "ערך לא תקין"}), 422
    patch = {"pinned_projects": {user["id"]: project_id if pinned else None}}
    if not db.update_family_settings(user["family_id"], patch):
        return jsonify({"error": "השמירה נכשלה — נסו שוב"}), 500
    return jsonify({"status": "ok", "pinned": pinned})


@app.route("/api/app-installed", methods=["POST"])
@limiter.limit("10 per minute")
@login_required
def app_installed_route():
    """Opened from the home-screen icon (Matan, 13.10): from now on without the
    "add to home screen" bar, even when this user comes in from the browser.
    Stored in the family settings under `app_installed[user_id]`, merged by
    key like `pinned_projects`."""
    user = get_current_user()
    if ((family_settings().get("app_installed") or {}).get(user["id"])):
        return jsonify({"status": "ok"})
    if not db.update_family_settings(user["family_id"], {"app_installed": {user["id"]: clock.today().isoformat()}}):
        return jsonify({"error": "השמירה נכשלה"}), 500
    return jsonify({"status": "ok"})


@app.route("/api/projects/<project_id>/categories", methods=["GET"])
@login_required
@project_access_required
def list_project_categories_route(project_id):
    user = get_current_user()
    type_ = request.args.get("type")
    return jsonify(db.get_project_categories(project_id, user["family_id"], type_))


_CATEGORY_NAME_MAX = 30     # like the screen's `maxlength`
_CATEGORY_ICON_MAX = 16     # a compound emoji (family, flag) is several characters; still one icon


def _category_fields(body: dict):
    """A category's name and icon (family or project). Returns (name, icon, error).

    The screen had a `maxlength`, the server nothing. Whoever called the API
    directly could save an icon `<style>…</style>`, and it went into
    `innerHTML` for the whole family; and `{"name": 5}` crashed the route with
    a 500 on `.strip()` of a number."""
    name, icon = body.get("name"), body.get("icon")
    if name is not None and not isinstance(name, str):
        return None, None, "שם הקטגוריה חייב להיות טקסט"
    if icon is not None and not isinstance(icon, str):
        return None, None, "האייקון חייב להיות טקסט"
    name = (name or "").strip()
    icon = (icon or "").strip() or "📦"
    if not name:
        return None, None, "נא להזין שם קטגוריה"
    if len(name) > _CATEGORY_NAME_MAX:
        return None, None, f"שם הקטגוריה ארוך מדי — עד {_CATEGORY_NAME_MAX} תווים"
    if len(icon) > _CATEGORY_ICON_MAX or any(ch in icon for ch in '<>&"\''):
        return None, None, "האייקון לא תקין — בחרו אימוג'י אחד"
    return name, icon, None


@app.route("/api/projects/<project_id>/categories", methods=["POST"])
@limiter.limit("30 per minute")
@login_required
@project_access_required
def add_project_category_route(project_id):
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    name, icon, field_err = _category_fields(body)
    if field_err:
        return jsonify({"error": field_err}), 422
    if not name:
        return jsonify({"error": "נא להזין שם קטגוריה"}), 422
    type_ = body.get("type")
    if type_ not in ("expense", "income", "savings"):
        return jsonify({"error": "מחלקת הקטגוריה לא תקינה"}), 422
    cat, err = db.add_project_category(project_id, user["family_id"], name,
                                       icon, type_)
    if err:
        logger.error("add_project_category route: %s", err)
        return jsonify({"error": "הוספת הקטגוריה נכשלה — נסו שוב"}), 500
    return jsonify(cat), 201


@app.route("/api/projects/<project_id>/categories/<cat_id>", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
@project_access_required
def update_project_category_route(project_id, cat_id):
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    name, icon, field_err = _category_fields(body)
    if field_err:
        return jsonify({"error": field_err}), 422
    if not name:
        return jsonify({"error": "נא להזין שם קטגוריה"}), 422
    ok = db.update_project_category(cat_id, project_id, user["family_id"], name, icon)
    if not ok:
        return jsonify({"error": "עדכון נכשל"}), 500
    return jsonify({"status": "ok", "name": name, "icon": icon})


@app.route("/api/projects/<project_id>/categories/<cat_id>", methods=["DELETE"])
@limiter.limit("30 per minute")
@login_required
@project_access_required
def delete_project_category_route(project_id, cat_id):
    user = get_current_user()
    # `get_project_for_transaction` doesn't return `id`; without it the query
    # runs with "None"
    project = {**(db.get_project_for_transaction(project_id, user["family_id"]) or {}),
               "id": project_id}
    return _delete_category("project", cat_id, user, project=project)


@app.route("/api/projects/<project_id>/categories/<cat_id>/usage", methods=["GET"])
@login_required
@project_access_required
def project_category_usage_route(project_id, cat_id):
    user = get_current_user()
    # `get_project_for_transaction` doesn't return `id`; without it the query
    # runs with "None"
    project = {**(db.get_project_for_transaction(project_id, user["family_id"]) or {}),
               "id": project_id}
    return _category_usage("project", cat_id, user, project=project)


def _invite_deadline(family):
    """When the family's invite code expires, or `None` if there is no date."""
    raw = (family or {}).get("invite_code_expires_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _invite_expired(family):
    deadline = _invite_deadline(family)
    return bool(deadline and deadline <= clock.now_utc())


def _invite_days_left(family):
    deadline = _invite_deadline(family)
    if not deadline:
        return None
    return max((deadline - clock.now_utc()).days, 0)


def _fresh_invite(family: dict, user: dict) -> dict:
    """Every family member's settings always have a valid invite code (Matan's
    request, 29.9.2026).

    A code is valid for a week, and expires silently: whoever came in to send a
    code found "the code expired". Now opening settings when the code has
    already expired produces a new one, for every family member, not just the
    manager. **Only when already expired** (`renew_expired_invite_code`, not
    `rotate`): a code still valid may have been sent to someone, and replacing
    it would cancel it, and that stays the manager's permission. A failure
    leaves "the code expired" on screen; nothing breaks over it."""
    if not family:
        return family
    if family.get("invite_code") and not _invite_expired(family):
        return family
    code, _ = db.renew_expired_invite_code()
    return db.get_family(family["id"]) if code else family


def _settings_separately(family_id, user):
    """The old way to the settings page: a query per part. Only when
    `get_settings_page` is unavailable, or there is no family."""
    categories = db.get_categories(family_id)
    # "₪1,850 a month on average" next to each category (idea 38): decoration;
    # a failure doesn't break the page
    try:
        cat_averages = db.category_monthly_averages(family_id) if family_id else {}
    except db.DataUnavailable:
        cat_averages = {}
    members    = db.get_family_members(family_id)       if family_id else []
    family     = _fresh_invite(db.get_family(family_id), user) if family_id else {}
    # Active series only; see `db.is_active_template`
    recurring  = [r for r in db.get_recurring_transactions(family_id, user["id"],
                                                          settings=family_settings())
                  if db.is_active_template(r)] if family_id else []
    # "₪5,000 ← ₪5,500 מאוקטובר" (idea 40)
    price_changes = db.recurring_price_changes(family_id, recurring) if recurring else {}
    # How many transactions are really about to be deleted. "A final action
    # that can't be undone" is a sentence; a number is a fact, and it is what
    # makes people stop.
    try:
        family_tx_count = db.family_transaction_count(family_id) if family_id else 0
    except db.DataUnavailable:
        family_tx_count = None
    return categories, cat_averages, members, family, recurring, price_changes, family_tx_count


@app.route("/settings")
@login_required
def settings():
    user       = get_current_user()
    family_id  = user["family_id"]
    today = clock.today()
    # One trip to the database (Matan, 13.10): `get_settings_page`. If it is
    # unavailable (the migration hasn't run yet, a failure), a query per part,
    # as before.
    page = None
    if family_id:
        try:
            page = _timed("settings_page", lambda: db.fetch_settings_page(family_id, today, user["id"]))
        except db.DataUnavailable:
            logger.warning("settings_page rpc unavailable — falling back to separate queries")
    if page is not None:
        _prime_request_cache(family_id, settings=page["settings"], categories=page["categories"],
                             members=page["members"], family=page["family"])
        categories   = page["categories"]
        cat_averages = page["cat_averages"]
        members      = page["members"]
        family       = _fresh_invite(page["family"], user)
        recurring    = [r for r in page["recurring"] if db.is_active_template(r)]
        price_changes = db.price_changes_from(recurring, page["ended"], page["ended_last"]) \
            if recurring else {}
        family_tx_count = page["tx_count"]
    else:
        categories, cat_averages, members, family, recurring, price_changes, family_tx_count = \
            _settings_separately(family_id, user)
    # "הבא: 1.11" for every recurring transaction (Matan, 12.10)
    next_charges = {t["id"]: next_charge_label(db.next_recurring_date(t, today), today)
                    for t in (recurring or [])}
    # My profile is already in the member list fetched above: it returns the
    # full name, email, phone and workplace for every member. Another query here
    # was a whole database round trip for data already in hand.
    profile = next((m for m in members if m["id"] == user["id"]), None)
    if profile is None:
        # No family, or the member didn't come back in the list: fall back to the
        # direct query
        profile = db.get_profile(user["id"]) or {}
    full_name = profile.get("full_name") or profile.get("name") or user["name"]
    name_parts = full_name.split(" ", 1)
    email, pending_email = _current_email(cached=True)
    account = {
        "full_name":  full_name,
        "first_name": name_parts[0] if name_parts else "",
        "last_name":  name_parts[1] if len(name_parts) > 1 else "",
        "email":      email,
        "pending_email": pending_email,
        "phone":      profile.get("phone") or "",
        "workplace":  profile.get("workplace") or "",
    }
    return render_template(
        "settings.html",
        # The invite code's expiry. Without this there is no way to know that
        # the code you are about to send on WhatsApp won't work anymore.
        invite_expired=_invite_expired(family),
        invite_expires_in=_invite_days_left(family),
        family_tx_count=family_tx_count,
        active_page="settings",
        user=user,
        categories=categories,
        cat_averages=cat_averages,
        members=members,
        family=family,
        recurring=recurring,
        price_changes=price_changes,
        next_charges=next_charges,
        hebrew_months=_HEBREW_MONTHS,
        account=account,
        # Picking a colour for a family member (round 6, item 13)
        member_colors=_member_colors(family_id),
        color_hex=_OWNER_HEX,
    )


@app.route("/api/profile", methods=["PUT"])
@limiter.limit("20 per minute")
@login_required
def update_profile():
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    first_name = _text(body.get("first_name"))
    last_name  = _text(body.get("last_name"))
    phone      = _normalize_phone(_text(body.get("phone")))
    workplace  = _text(body.get("workplace"))
    workplace_scope = body.get("workplace_scope")  # 'all' | 'future' | None

    if not first_name or not last_name:
        return jsonify({"error": "נא למלא שם פרטי ושם משפחה"}), 422
    # Empty is allowed here (the field isn't required when editing), but
    # whatever was typed must be a number
    if phone and not _looks_like_phone(phone):
        return jsonify({"error": "מספר הטלפון אינו תקין — למשל 050-1234567"}), 422

    old_profile = db.get_profile(user["id"]) or {}
    old_workplace = old_profile.get("workplace") or ""
    # The phone moved to "account" (Matan, 1.10) and isn't sent from here
    # anymore. Without this, every save of name or workplace would delete it,
    # and with it phone sign-in.
    if "phone" not in body:
        phone = old_profile.get("phone") or ""

    full_name = f"{first_name} {last_name}"
    ok, err = db.update_profile(user["id"], full_name, phone or None, workplace or None)
    if not ok:
        return jsonify({"error": _user_message(err, "עדכון הפרטים נכשל")}), 500

    # The workplace really changed and a scope was given: apply it to the salary
    # transaction history
    if workplace_scope and workplace != old_workplace and user["family_id"]:
        db.update_workplace_history(
            user["id"], user["family_id"], workplace or None, old_workplace or None,
            apply_to_all=(workplace_scope == "all"),
        )

    # The first name is shown all over the site (greetings, transactions etc.),
    # so update it in the session too
    session["user_name"] = db.first_name(full_name)
    return jsonify({"status": "ok", "full_name": full_name, "first_name": first_name,
                    "last_name": last_name, "phone": phone, "workplace": workplace})


@app.route("/api/profile/password", methods=["PUT"])
@login_required
# Checks the current password against Supabase, i.e. a password-guessing
# oracle for anyone who got hold of a session. The limit on /login doesn't help
# here; this is a different route.
@limiter.limit("5 per minute")
def update_password():
    body             = request.get_json(silent=True) or {}
    current_password = body.get("current_password", "")
    password         = body.get("password", "")
    password_confirm = body.get("password_confirm", "")

    if not current_password:
        return jsonify({"error": "נא להזין את הסיסמה הנוכחית"}), 422
    if passwords.problem(password):
        return jsonify({"error": passwords.problem(password)}), 422
    if password != password_confirm:
        return jsonify({"error": "הסיסמאות החדשות אינן תואמות"}), 422

    # Make sure the current password is right before allowing it to be replaced
    _, err = db.sign_in(_current_email()[0], current_password)
    if err:
        return jsonify({"error": "הסיסמה הנוכחית שגויה"}), 403

    ok, err = db.update_password(session.get("access_token"), password)
    if not ok:
        return jsonify({"error": _user_message(err, "עדכון הסיסמה נכשל")}), 500
    return jsonify({"status": "ok"})


# Matan (13.10): 10 minutes. In the logs (1.11) the check cost `0.2-0.4`
# seconds each time, and the email almost never changes, and a change from here
# is checked on every load anyway (`pending_email`). 12 hours.
_EMAIL_FRESH_SECONDS = 12 * 3600


def _current_email(cached=False):
    """`(email, pending_email)` from Supabase, and updates the session. Without a
    connection: the session's email, and no "pending". See `db.get_auth_email`.

    `cached`, for the settings page (Matan, 13.10): the check against the auth
    service took `100-230ms` off every load, so it is done at most once every
    10 minutes. But not when an email is pending confirmation: then it is
    checked every time, so the confirmation from the link shows up right away.
    Actions that check a password against the email always check fresh."""
    if (cached and session.get("user_email") and not session.get("pending_email")
            and time.time() - (session.get("email_checked_at") or 0) < _EMAIL_FRESH_SECONDS):
        return session["user_email"], None
    email, pending = db.get_auth_email(session.get("access_token"))
    if email:
        session["user_email"] = email
        session["email_checked_at"] = time.time()
        if pending:
            session["pending_email"] = pending
        else:
            session.pop("pending_email", None)
    return email or session.get("user_email", ""), pending


@app.route("/api/profile/phone", methods=["PUT"])
@login_required
@limiter.limit("20 per minute")
def update_phone():
    """Changing the phone from the "account" box (Matan, 1.10). The phone is also
    used for sign-in, so empty isn't allowed, as on signup."""
    user  = get_current_user()
    body  = request.get_json(silent=True) or {}
    phone = _normalize_phone(_text(body.get("phone")))
    if not phone or not _looks_like_phone(phone):
        return jsonify({"error": "מספר הטלפון אינו תקין — למשל 050-1234567"}), 422
    ok, err = db.update_phone(user["id"], phone)
    if not ok:
        return jsonify({"error": _user_message(err, "עדכון הטלפון נכשל")}), 500
    return jsonify({"status": "ok", "phone": phone})


@app.route("/api/profile/email", methods=["PUT"])
@login_required
# Checks a password: the same oracle as changing the password, and the same
# limit. And every request sends an email.
@limiter.limit("5 per minute")
def update_email():
    """Changing the email (Matan, 1.10): a new email + the current password, then
    a confirmation link. Without the password, whoever grabs an unlocked phone
    moves the account to their own email and resets the password."""
    body             = request.get_json(silent=True) or {}
    new_email        = _text(body.get("email")).lower()
    current_password = body.get("current_password", "")

    if not _looks_like_email(new_email):
        return jsonify({"error": "כתובת המייל אינה תקינה"}), 422
    if not current_password:
        return jsonify({"error": "נא להזין את הסיסמה הנוכחית"}), 422
    current_email, _ = _current_email()
    if new_email == (current_email or "").lower():
        return jsonify({"error": "זה כבר המייל של החשבון"}), 422

    _, err = db.sign_in(current_email, current_password)
    if err:
        return jsonify({"error": "הסיסמה הנוכחית שגויה"}), 403

    ok, err = db.request_email_change(session.get("access_token"), new_email,
                                      url_for("settings", _external=True))
    if not ok:
        return jsonify({"error": _user_message(err, "שליחת מייל האישור נכשלה")}), 500
    session["pending_email"] = new_email     # from here settings check fresh until confirmation
    return jsonify({"status": "ok", "pending_email": new_email})


@app.route("/api/account/reset", methods=["POST"])
@login_required
# A password oracle, plus a destructive action: deletes all the family's
# transactions. Stricter: there is no legitimate scenario for repeated resets.
@limiter.limit("3 per hour")
def reset_account_route():
    """Transaction reset: deletes all the transactions (income/expenses/savings,
    including recurring ones) per the choice: the whole family's or only the
    user's. The account, categories, projects and settings are kept. Requires
    password confirmation. What is deleted is archived in the internal archive."""
    user     = get_current_user()
    body     = request.get_json(silent=True) or {}
    password = body.get("password", "")
    scope    = body.get("scope", "family")  # 'family' | 'mine'

    if not password:
        return jsonify({"error": "נא להזין את הסיסמה הנוכחית"}), 422
    if scope not in ("family", "mine"):
        return jsonify({"error": "קלט לא תקין"}), 422

    response, err = db.sign_in(_current_email()[0], password)
    if err:
        return jsonify({"error": "הסיסמה שגויה"}), 403

    db.set_auth_token(response.session.access_token)

    # Resetting the whole family is the most destructive action in the app, and
    # it was open to any member with just their own password. "Only mine" stays
    # open: it is the requester's own money.
    if scope == "family":
        denied = _require_manager()
        if denied:
            return denied

    # `deleted` and not `ok`: zero rows is a valid result (a family with no
    # transactions), so failure is measured by `err` alone. The number is
    # returned so the user sees what really happened: this is the most
    # destructive action in the app.
    deleted, err = db.reset_transactions(
        user["family_id"],
        only_user_id=user["id"] if scope == "mine" else None,
    )
    if err:
        logger.error("reset_transactions route: %s", err)
        return jsonify({"error": "האיפוס נכשל"}), 500
    return jsonify({"status": "ok", "deleted": deleted})


@app.route("/api/account", methods=["DELETE"])
@login_required
# A password oracle, and the most destructive action in the app: permanently
# deleting an account.
@limiter.limit("3 per hour")
def delete_account_route():
    """Permanently deletes the account. From the user's point of view everything
    is deleted and the email/phone are freed; in practice the data is archived
    to the site owner's internal archive (see migration 20260711100000).
    Requires confirming the current password."""
    body     = request.get_json(silent=True) or {}
    password = body.get("password", "")

    if not password:
        return jsonify({"error": "נא להזין את הסיסמה הנוכחית"}), 422

    # Password check: both guards against deleting by mistake and refreshes the
    # token the delete runs with
    response, err = db.sign_in(_current_email()[0], password)
    if err:
        return jsonify({"error": "הסיסמה שגויה"}), 403

    db.set_auth_token(response.session.access_token)
    ok, err = db.delete_my_account()
    if not ok:
        return jsonify({"error": "מחיקת החשבון נכשלה"}), 500

    session.clear()
    # Deleting an account is a deletion: don't leave the identifier or the device
    # marker behind. Whoever opens the URL afterwards is a guest again and sees
    # the landing page.
    response = jsonify({"status": "ok"})
    response.delete_cookie(_LAST_ID_COOKIE, samesite="Lax", secure=not _IS_DEV)
    response.delete_cookie(_DEVICE_COOKIE,  samesite="Lax", secure=not _IS_DEV)
    return response


def _parse_amount(raw):
    """Safe parsing of a transaction amount: a finite, positive number only.
    Returns (value, error); the Hebrew error is shown to the user as a 422."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, "הסכום חייב להיות מספר"
    if not math.isfinite(value) or value <= 0:
        return None, "הסכום חייב להיות מספר חיובי"
    # The column is NUMERIC(10,2) and blows up above 99,999,999.99. Without this
    # check a nine-digit typo reaches Postgres, fails there, and comes back as
    # "adding the transaction failed, try again", so the user retries the same
    # input and fails again, with no hint why.
    if value > 99_999_999:
        return None, "הסכום גדול מדי"
    # Below an agora rounds to 0.00 and then hits the column's CHECK, with the
    # same generic message and the same dead end
    if round(value, 2) <= 0:
        return None, "הסכום קטן מדי"
    return value, None


# How many years back and forward a transaction still makes sense. Wide on
# purpose (people enter past history and prepay into the future) and narrow
# enough to catch a disaster.
_DATE_PAST_YEARS   = 10
_DATE_FUTURE_YEARS = 5


def _parse_date(raw):
    """Safe parsing of a transaction date. Returns (iso_string, error).

    Until now `date` passed from the request body to the database with no check
    at all, and the column had no constraint. That didn't stay theoretical
    because of the recurring engine: a template with a start date in the year
    1000 makes it create an occurrence for every month since, up to the cap of
    500 rows: 500 real transactions in one call, going into the summaries and
    the month comparison forever.

    The database constraint (`transactions_date_sane`) is the last net and it
    is wide, because a CHECK must be immutable and can't know what today is.
    Here we can."""
    if not isinstance(raw, str):
        return None, "התאריך חסר או אינו תקין"
    try:
        value = date.fromisoformat(raw.strip())
    except ValueError:
        return None, "התאריך אינו תקין"
    today = clock.today()
    if value.year < today.year - _DATE_PAST_YEARS:
        return None, "התאריך רחוק מדי בעבר"
    if value.year > today.year + _DATE_FUTURE_YEARS:
        return None, "התאריך רחוק מדי בעתיד"
    return value.isoformat(), None


def _require_manager():
    """A gate for destructive actions. Returns `None` if allowed, or a ready
    error response.

    The role was added so someone would be responsible, but it was enforced
    only on removing a member, so anyone who joined with a six-character code
    could delete the whole family's history, delete categories and replace the
    invite code.

    The check derives from `auth.uid()` in the database and not the session, so
    it can't be forged from the client. On a failed query, refuse: "unknown
    whether they are the manager" isn't "yes"."""
    try:
        if db.is_family_manager():
            return None
    except db.DataUnavailable:
        return jsonify({"error": "לא הצלחנו לאמת הרשאות — נסו שוב"}), 503
    return jsonify({
        "error": "רק מנהל המשפחה יכול לבצע את הפעולה הזאת.",
    }), 403


def _resolve_owner(body: dict, user: dict, tx_type: str, unchanged=None):
    """Who owns the transaction, per the family settings: a type with attribution
    off is always saved as household (NULL), even if the request tried to send
    an owner.
    Values: a family member's uuid, "shared" = shared (NULL), and without an
    owner, the signed-in user. Returns (user_id, error)."""
    if not family_settings().get("owner_attribution", {}).get(tx_type, False):
        return None, None
    owner = body.get("owner")
    if owner == "shared":
        return None, None
    # When editing: the owner already on the transaction stays allowed, even if
    # no longer in the family (moved to another family by invite code).
    # Otherwise the form is forced to silently replace it.
    if owner and unchanged and owner == unchanged:
        return owner, None
    if owner:
        # The uuid came from the client and until now was accepted as is. RLS
        # checks only family_id on the row being written, not user_id, so a
        # transaction could be recorded under someone not in the family, or an
        # expense pinned on the partner. Not a data leak but forged attribution,
        # and that is enough: the "by family member" breakdown relies on it.
        if owner not in {m["id"] for m in db.get_family_members(user["family_id"])}:
            return None, "בן המשפחה שנבחר אינו במשפחה שלך"
        return owner, None
    return user["id"], None


def _apply_project_assignment(body: dict, user: dict, tx_type: str, current=None):
    """Applies a project attachment (if body['project_id'] was sent): checks the
    project tracks this transaction type, enforces on the server automatic
    attribution to the owner in a personal project (ignoring body['owner'] in
    that case), and replaces the regular category with the project's own
    category. Returns (project_id, project_category_id, category_id, user_id, error)."""
    current = current or {}
    project_id = body.get("project_id")
    if not project_id:
        owner_id, owner_err = _resolve_owner(body, user, tx_type, unchanged=current.get("user_id"))
        if owner_err:
            return None, None, None, None, owner_err
        category_id, cat_err = _validated_category(body.get("category_id"), user, tx_type)
        if cat_err:
            return None, None, None, None, cat_err
        return None, None, category_id, owner_id, None

    project = db.get_project_for_transaction(project_id, user["family_id"])
    if not project:
        return None, None, None, None, "הפרויקט לא נמצא"

    # A personal project: only the owner may write to it. Reading is already
    # hidden from others (`_filter_hidden_personal_projects`), but writing wasn't
    # checked, so a family member could record transactions into a project they
    # aren't even allowed to see.
    if project.get("owner_id") and project["owner_id"] != user["id"]:
        return None, None, None, None, "הפרויקט לא נמצא"

    track_key = {"expense": "track_expense", "income": "track_income", "savings": "track_savings"}[tx_type]
    # "Tracks type" decides what can be **added** to the project. A transaction
    # already in it stays; otherwise editing its amount silently moved it out of
    # the project into household expenses.
    if not project.get(track_key) and project_id != current.get("project_id"):
        return None, None, None, None, "הפרויקט הזה לא עוקב אחרי המחלקה הזאת"

    owner_id = project.get("owner_id")
    if owner_id:
        user_id, owner_err = owner_id, None
    else:
        user_id, owner_err = _resolve_owner(body, user, tx_type, unchanged=current.get("user_id"))
    if owner_err:
        return None, None, None, None, owner_err

    # The project category must belong to this project, not another
    project_category_id = body.get("project_category_id")
    if not project_category_id:
        return None, None, None, None, "נא לבחור קטגוריה"
    valid = {c["id"] for c in db.get_project_categories(project_id, user["family_id"])}
    if project_category_id not in valid:
        return None, None, None, None, "הקטגוריה אינה שייכת לפרויקט הזה"

    return project_id, project_category_id, None, user_id, None


def _validated_category(category_id, user: dict, tx_type: str):
    """Checks the category exists in the family and matches the transaction type.
    Returns (category_id, error). No transaction without a category, not even
    through the API."""
    if not category_id:
        return None, "נא לבחור קטגוריה"
    for cat in db.get_categories(user["family_id"]):
        if cat["id"] == category_id:
            if cat.get("type") != tx_type:
                return None, "הקטגוריה אינה שייכת למחלקה של העסקה"
            return category_id, None
    return None, "הקטגוריה לא נמצאה"


# ─── API: Transactions ────────────────────────────────────────────────────────

@app.route("/api/family/members", methods=["GET"])
@login_required
def family_members():
    user = get_current_user()
    members = db.get_family_members(user["family_id"]) if user["family_id"] else []
    return jsonify([{"id": m["id"], "name": m["name"]} for m in members])

# How many retroactive occurrences may be created without the user explicitly
# confirming.
#
# `_parse_date` allows 10 years back, and `materialize_recurring` runs right
# after every template save. A typo (`2016` instead of `2026` on a ₪6,000 rent)
# created `~120` real transactions in one request, with no confirmation and no
# number in the response. They went into the month archive, the trend chart
# and the CSV export, and the only way out is "delete this and all later ones"
# from the earliest, something you have to discover by yourself.
#
# Three covers a reasonable backfill ("I forgot to enter the last three
# months") and stops anything that looks like an accident.
_RETRO_WITHOUT_CONFIRM = 3


def _retro_occurrences(body, tx_date, already=None):
    """How many occurrences will be created retroactively for the template being
    saved now.

    `already`: the dates the series has already taken (when editing,
    `db.existing_occurrence_dates`). A taken period isn't counted, just as the
    engine won't create it."""
    if not body.get("is_recurring"):
        return 0
    freq = body.get("recurring_frequency") or "monthly_1"
    template = {
        "date": tx_date,
        "recurring_frequency": freq,
        "recurring_end_date": body.get("recurring_end_date") or None,
    }
    try:
        occurrences = db._recurring_occurrences(template, clock.today())
        if already:
            occurrences = [d for d in occurrences
                           if not db._already_materialized(freq, d, already)]
        return len(occurrences)
    except Exception:
        # A failed count doesn't block a legitimate save; it just doesn't protect.
        logger.exception("_retro_occurrences")
        return 0


# The frequencies the engine knows how to produce. `NULL` passed the database's
# CHECK, so `{"is_recurring": true}` with no frequency created a live template
# that `_recurring_occurrences` reads as `monthly_1` and produces rows from on
# the 1st of every month, while the frequency picker in the UI shows empty. An
# unknown string was stopped only by the CHECK and came back as a 500.
_FREQUENCIES = ("monthly_same", "monthly_1", "monthly_15", "weekly", "biweekly")


def _validated_frequency(body):
    """Returns `(frequency, error)`. Relevant only to a transaction marked recurring."""
    if not body.get("is_recurring"):
        return None, None
    freq = body.get("recurring_frequency")
    if freq not in _FREQUENCIES:
        return None, "יש לבחור כל כמה זמן העסקה הקבועה נרשמת"
    return freq, None


# No human scenario comes near the limit; it exists so a script can't fill the
# database. Every delete also produces an `owner_archive` row through a trigger,
# so "add-delete-repeat" was a loop growing a 500MB database with no cleanup.
@app.route("/api/transactions", methods=["POST"])
@limiter.limit("60 per minute")
@login_required
def add_transaction():
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף, ואם זה חוזר התחברו מחדש"}), 400

    body = request.get_json(silent=True) or {}

    required = ("amount", "type", "date")
    if not all(body.get(k) for k in required):
        return jsonify({"error": "חסרים פרטים: סכום, מחלקה ותאריך הם שדות חובה"}), 422

    tx_type = body["type"]
    if tx_type not in ("expense", "income", "savings"):
        return jsonify({"error": "המחלקה חייבת להיות הוצאה, הכנסה או חיסכון"}), 422

    amount, amount_err = _parse_amount(body["amount"])
    if amount_err:
        return jsonify({"error": amount_err}), 422

    tx_date, date_err = _parse_date(body["date"])
    if date_err:
        return jsonify({"error": date_err}), 422

    recurring_end = body.get("recurring_end_date") or None
    if recurring_end:
        recurring_end, end_err = _parse_date(recurring_end)
        if end_err:
            return jsonify({"error": f"תאריך הסיום: {end_err}"}), 422
        if recurring_end < tx_date:
            return jsonify({"error": "תאריך הסיום של העסקה הקבועה מוקדם מתאריך ההתחלה"}), 422


    # Installments (Matan, 12.10): the amount is the full price, and the server
    # splits it: a monthly recurring transaction with an end date. See
    # `db.installment_plan`.
    installments = body.get("installments")
    plan = None
    if installments not in (None, "", 0, False):
        if tx_type != "expense":
            return jsonify({"error": "תשלומים — רק בהוצאות"}), 422
        if isinstance(installments, bool) or not isinstance(installments, int) or not 2 <= installments <= 60:
            return jsonify({"error": "מספר התשלומים — בין 2 ל-60"}), 422
        plan = db.installment_plan(amount, installments, tx_date)
        if not plan:
            return jsonify({"error": "הסכום קטן מדי לחלוקה לתשלומים"}), 422
        body = dict(body, is_recurring=True, recurring_frequency="monthly_same",
                    recurring_end_date=plan["end_date"])
        recurring_end = plan["end_date"]

    recurring_frequency, freq_err = _validated_frequency(body)
    if freq_err:
        return jsonify({"error": freq_err}), 422

    # See `_RETRO_WITHOUT_CONFIRM`: a series that would create dozens of rows
    # back is almost always a date typo, not a request.
    retro = _retro_occurrences(body, tx_date)
    if retro > _RETRO_WITHOUT_CONFIRM and not request.args.get("confirm"):
        return jsonify({
            "needs_confirm": True,
            "will_create": retro,
            "error": f"העסקה הקבועה הזאת תיצור {retro} עסקאות אחורה, מ-{tx_date}. "
                     f"אם זה מה שרציתם — אשרו.",
        }), 409
    project_id, project_category_id, category_id, owner_user_id, proj_err = \
        _apply_project_assignment(body, user, tx_type)
    if proj_err:
        return jsonify({"error": proj_err}), 422

    # Adding a new salary: record ("freeze") the owner's current workplace on the
    # transaction itself, so a future workplace change doesn't silently rewrite
    # what was already created; see update_workplace_history for the flow of an
    # actual workplace change.
    workplace_snapshot = None
    if tx_type == "income" and owner_user_id and category_id:
        cat = next((c for c in db.get_categories(user["family_id"]) if c["id"] == category_id), None)
        if cat and "משכורת" in cat.get("name", ""):
            owner_profile = db.get_profile(owner_user_id)
            workplace_snapshot = (owner_profile or {}).get("workplace")

    payload = {
        "amount":      amount,
        "type":        tx_type,
        "date":        tx_date,
        "description": _text(body.get("description")),
        "category_id": category_id,
        "user_id":     owner_user_id,
        "family_id":   user["family_id"],
        "is_recurring":         bool(body.get("is_recurring", False)),
        "recurring_frequency":  recurring_frequency,
        "recurring_end_date":   recurring_end,
        "project_id":           project_id,
        "project_category_id":  project_category_id,
        "workplace":            workplace_snapshot,
    }
    if plan:
        payload.update(amount=plan["first"], installments=plan["count"],
                       installment_no=1, installment_total=plan["total"])
    # An attached receipt is possible only on expenses; empty = don't touch the
    # column (don't delete an existing receipt on edit)
    if tx_type == "expense" and body.get("receipt_path"):
        if not _receipt_path_ok(body["receipt_path"], user["family_id"]):
            return jsonify({"error": "הקבלה המצורפת אינה תקינה — נסו לצלם שוב"}), 422
        payload["receipt_path"] = body["receipt_path"]

    result, err = db.add_transaction(payload)
    if err:
        logger.error("add_transaction route: %s", err)
        return jsonify({"error": "הוספת העסקה נכשלה — נסו שוב"}), 500

    # A new recurring transaction (retroactive too): fill in every occurrence up
    # to today right away
    if payload["is_recurring"]:
        db.materialize_recurring(user["family_id"])   # (created, ok): not needed here

    if tx_type == "income" and category_id and _is_salary(category_id, user):
        _push_salary(payload)

    reply = {"status": "ok", "transaction": result}
    note = _budget_note(user["family_id"], payload)
    if note:
        reply["budget_note"] = note
    return jsonify(reply), 201


def _is_salary(category_id, user) -> bool:
    cat = next((c for c in db.get_categories(user["family_id"]) if c["id"] == category_id), None)
    return bool(cat and "משכורת" in (cat.get("name") or ""))


def _push_salary(payload):
    """"The salary was recorded" (Matan, 31.10), to the other family members,
    once in the transaction's month: `push_family_targets` records the month
    and returns nothing the second time."""
    try:
        when = date.fromisoformat(str(payload["date"])[:10])
        targets = db.push_family_targets("salary", f"{when.year}-{when.month:02d}")
        push.send_in_background(targets, push.salary(payload["amount"], when.month))
    except Exception:
        logger.exception("push salary")


def _budget_note(family_id, payload):
    """"עברתם את התקציב של סופר ומזון ב-₪120": after adding an expense that
    crosses its category's budget in its month (Matan, 3.10, idea 17).
    Household expenses only: project money isn't counted in household budgets.
    Information, not a block: a failure here doesn't fail the add, it just
    skips the note."""
    if payload.get("type") != "expense" or payload.get("project_id") or not payload.get("category_id"):
        return None
    budget = db.category_budget(family_settings(), payload["category_id"])
    if not budget:
        return None
    d = str(payload["date"])[:10]           # `_parse_date` returns an ISO string
    try:
        spent = db.category_month_spent(family_id, payload["category_id"], int(d[:4]), int(d[5:7]))
    except db.DataUnavailable:
        return None
    limit = budget["amount"]
    if spent <= limit:
        return None
    name = next((c.get("name") for c in db.get_categories(family_id)
                 if str(c.get("id")) == str(payload["category_id"])), None) or "הקטגוריה"
    over = round(spent - limit)
    if spent - float(payload["amount"]) <= limit:
        return f"עברתם את התקציב של {name} ב-₪{format_money(over)}"
    return f"התקציב של {name} כבר נגמר — ₪{format_money(over)} מעליו"


def _still_matches(current: dict, expected: dict) -> bool:
    """The fields an edit can change: identical to what the client saw after its
    save?"""
    for key in ("type", "date", "description", "category_id", "project_category_id", "user_id"):
        if key in expected and str(current.get(key) or "") != str(expected.get(key) or ""):
            return False
    if "amount" in expected:
        try:
            if abs(float(current.get("amount")) - float(expected["amount"])) > 0.005:
                return False
        except (TypeError, ValueError):
            return False
    return True


@app.route("/api/transactions/<tx_id>", methods=["PUT"])
@limiter.limit("60 per minute")
@login_required
@tx_visible_required("tx_id")
def update_transaction(tx_id):
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף, ואם זה חוזר התחברו מחדש"}), 400

    body = request.get_json(silent=True) or {}

    required = ("amount", "type", "date")
    if not all(body.get(k) for k in required):
        return jsonify({"error": "חסרים פרטים: סכום, מחלקה ותאריך הם שדות חובה"}), 422

    tx_type = body["type"]
    if tx_type not in ("expense", "income", "savings"):
        return jsonify({"error": "המחלקה חייבת להיות הוצאה, הכנסה או חיסכון"}), 422

    amount, amount_err = _parse_amount(body["amount"])
    if amount_err:
        return jsonify({"error": amount_err}), 422

    tx_date, date_err = _parse_date(body["date"])
    if date_err:
        return jsonify({"error": date_err}), 422

    recurring_end = body.get("recurring_end_date") or None
    if recurring_end:
        recurring_end, end_err = _parse_date(recurring_end)
        if end_err:
            return jsonify({"error": f"תאריך הסיום: {end_err}"}), 422
        if recurring_end < tx_date:
            return jsonify({"error": "תאריך הסיום של העסקה הקבועה מוקדם מתאריך ההתחלה"}), 422

    current = db.transaction_links(tx_id, user["family_id"])
    if current is None:
        return jsonify({"error": "העסקה לא נמצאה — ייתכן שנמחקה בינתיים"}), 404
    # "Undo" after an edit (Matan, 30.9, round 6, item 10): only if the
    # transaction is still as our edit left it. Otherwise someone changed it
    # since; don't overwrite them.
    if isinstance(body.get("if_match"), dict) and not _still_matches(current, body["if_match"]):
        return jsonify({"error": "העסקה השתנתה בינתיים, אז לא ביטלנו — כדי לא לדרוס את השינוי",
                        "conflict": True}), 409
    project_id, project_category_id, category_id, owner_user_id, proj_err = \
        _apply_project_assignment(body, user, tx_type, current=current)
    if proj_err:
        return jsonify({"error": proj_err}), 422


    recurring_frequency, freq_err = _validated_frequency(body)
    if freq_err:
        return jsonify({"error": freq_err}), 422

    # See `_RETRO_WITHOUT_CONFIRM`: a series that would create dozens of rows
    # back is almost always a date typo, not a request. When editing, only
    # periods the series hasn't taken yet are counted; otherwise every fix to an
    # old template popped up the question.
    already = None
    if body.get("is_recurring") and not request.args.get("confirm"):
        try:
            already = db.existing_occurrence_dates(tx_id, user["family_id"])
        except db.DataUnavailable:
            # Unknown how many would be created. Saving without asking is exactly
            # what the question prevents.
            return jsonify({"error": "לא הצלחנו לבדוק את העסקה הקבועה — נסו שוב"}), 503
    retro = _retro_occurrences(body, tx_date, already)
    if retro > _RETRO_WITHOUT_CONFIRM and not request.args.get("confirm"):
        return jsonify({
            "needs_confirm": True,
            "will_create": retro,
            "error": f"העסקה הקבועה הזאת תיצור {retro} עסקאות אחורה, מ-{tx_date}. "
                     f"אם זה מה שרציתם — אשרו.",
        }), 409

    # An occurrence of a series can't become a template of its own. No database
    # constraint prevents it, and the result is two parallel series producing the
    # same money twice every month, forever. Whoever wants to change the series
    # does so from the template itself.
    if bool(body.get("is_recurring", False)):
        try:
            if db.is_recurring_instance(tx_id, user["family_id"]):
                return jsonify({
                    "error": "זו עסקה קבועה. כדי לשנות אותה מהחודש הנוכחי והלאה, "
                             "ערכו אותה דרך ההגדרות ← עסקאות קבועות.",
                }), 422
        except db.DataUnavailable:
            # Unknown. A duplicate series is damage that can't be undone; a
            # retry isn't.
            return jsonify({"error": "לא הצלחנו לבדוק את העסקה הקבועה — נסו שוב"}), 503

    payload = {
        "amount":      amount,
        "type":        tx_type,
        "date":        tx_date,
        "description": _text(body.get("description")),
        "category_id": category_id,
        "user_id":     owner_user_id,
        "is_recurring":         bool(body.get("is_recurring", False)),
        "recurring_frequency":  recurring_frequency,
        "recurring_end_date":   recurring_end,
        "project_id":           project_id,
        "project_category_id":  project_category_id,
    }
    # An attached receipt is possible only on expenses; empty = don't touch the
    # column (don't delete an existing receipt on edit)
    if tx_type == "expense" and body.get("receipt_path"):
        if not _receipt_path_ok(body["receipt_path"], user["family_id"]):
            return jsonify({"error": "הקבלה המצורפת אינה תקינה — נסו לצלם שוב"}), 422
        payload["receipt_path"] = body["receipt_path"]

    # Editing from "recurring transactions" in settings: from the current month
    # on (Matan, 2.10). Changing the start date is a correction of the series
    # itself, so it stays an ordinary edit.
    if (request.args.get("from_now") and payload["is_recurring"]
            and str(tx_date) == str(current.get("date"))[:10]):
        series_reply = _edit_series_from_now(tx_id, user["family_id"], payload)
        if series_reply is not None:
            return series_reply

    result, err = db.update_transaction(tx_id, user["family_id"], payload)
    if err:
        logger.error("update_transaction route: %s", err)
        return jsonify({"error": "עדכון העסקה נכשל — נסו שוב"}), 500
    # No row was touched: the transaction was deleted meanwhile by another family
    # member, or the id belongs to another family. Until now this came back as a
    # 200 "saved", and the user believed their edit went through.
    if not result:
        return jsonify({"error": "העסקה לא נמצאה — ייתכן שנמחקה בינתיים"}), 404

    if payload["is_recurring"]:
        db.materialize_recurring(user["family_id"])   # (created, ok): not needed here

    return jsonify({"status": "ok", "transaction": result})


def _edit_series_from_now(template_id, family_id, payload):
    """Editing a recurring transaction "from now on": the change is written on the
    current month's occurrence, and the series splits at it, the same mechanism
    as "update from now on" (`split_recurring_series`). January up to the
    previous month stay as they are.

    `None` = no past to protect (the series started this month, or ended): edit
    the template as usual. Otherwise, the response for the route."""
    try:
        kind, ref = db.series_pivot(template_id, family_id)
    except db.DataUnavailable:
        return jsonify({"error": "לא הצלחנו לבדוק את העסקה הקבועה — נסו שוב"}), 503
    if kind in ("template", "none"):
        return None
    if kind == "future":
        pivot_date = ref
        if payload["recurring_end_date"] and str(payload["recurring_end_date"]) < pivot_date:
            return jsonify({"error": "תאריך הסיום מוקדם מהמופע הבא של העסקה הקבועה"}), 422
        pivot_id, err = db.create_occurrence(template_id, family_id, pivot_date)
        if err or not pivot_id:
            logger.error("edit series from now: create_occurrence: %s", err)
            return jsonify({"error": "עדכון העסקה הקבועה נכשל — נסו שוב"}), 500
    else:
        pivot_id, pivot_date = ref
        if payload["recurring_end_date"] and str(payload["recurring_end_date"]) < pivot_date:
            return jsonify({"error": "תאריך הסיום מוקדם מהחודש הנוכחי"}), 422

    # 1. The occurrence gets the new values, without its date and without the
    #    template's receipt
    occurrence = {k: v for k, v in payload.items()
                  if k not in ("date", "is_recurring", "recurring_frequency",
                               "recurring_end_date", "receipt_path")}
    result, err = db.update_transaction(pivot_id, family_id, occurrence)
    if err or not result:
        logger.error("edit series from now: update occurrence: %s", err)
        return jsonify({"error": "עדכון העסקה הקבועה נכשל — נסו שוב"}), 500
    # 2. The series splits at it: the old one ends a day before, and it is the
    #    template of the new one
    new_id, err = db.split_recurring_series(template_id, pivot_id, family_id)
    if err or not new_id:
        logger.error("edit series from now: split: %s", err)
        return jsonify({"error": "עדכון העסקה הקבועה נכשל — נסו שוב"}), 500
    # 3. And frequency / end date, if changed (the split copied the old one's)
    _, err = db.update_transaction(new_id, family_id, {
        "recurring_frequency": payload["recurring_frequency"],
        "recurring_end_date":  payload["recurring_end_date"],
    })
    if err:
        logger.error("edit series from now: frequency: %s", err)
    db.materialize_recurring(family_id)
    return jsonify({"status": "ok", "transaction": result, "series_from": pivot_date})


def _entered_text(meta: dict, viewer_id: str, names: dict, now=None) -> str:
    """"הוזנה ע״י אור · אתמול 18:32"; see `/api/transactions/<id>/meta`.
    "Transaction" is feminine in Hebrew ("הוזנה"), like "נרשמה ע״י" and the
    duplicate window."""
    if meta.get("recurring_parent_id"):
        return "נוצרה אוטומטית מעסקה קבועה"
    now = now or clock.now()
    when = ""
    raw = meta.get("created_at")
    if raw:
        at = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(clock.ISRAEL)
        day = at.date()
        today = now.date()
        clock_time = at.strftime("%H:%M")
        if day == today:
            when = f"היום {clock_time}"
        elif day == today - timedelta(days=1):
            when = f"אתמול {clock_time}"
        else:
            when = f"{day.day}.{day.month}.{day.year} {clock_time}"
    by = meta.get("created_by")
    if not by:
        return f"הוזנה ב-{when}" if when else ""
    who = "על ידך" if by == viewer_id else "ע״י " + names.get(by, "בן משפחה לשעבר")
    return f"הוזנה {who}" + (f" · {when}" if when else "")


@app.route("/api/transactions/<tx_id>/meta")
@limiter.limit("120 per minute")
@login_required
@tx_visible_required("tx_id")
def transaction_meta(tx_id):
    """Who entered it and when: the line at the bottom of the edit window (Matan,
    3.10, idea 22). Not added to every row of every list: fetched when the
    window opens."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלכם — רעננו את הדף"}), 400
    try:
        meta = db.transaction_meta(tx_id, user["family_id"])
    except db.DataUnavailable:
        return jsonify({"error": "לא הצלחנו לטעון — נסו שוב"}), 503
    if meta is None:
        return jsonify({"error": "העסקה לא נמצאה"}), 404
    # Family members' names only when someone else entered it; most of the time
    # it is your own transaction
    by = meta.get("created_by")
    names = _member_names(user["family_id"]) \
        if by and by != user["id"] and not meta.get("recurring_parent_id") else {}
    text = _entered_text(meta, user["id"], names)
    return jsonify({"text": text})


@app.route("/api/transactions/<tx_id>", methods=["DELETE"])
@limiter.limit("60 per minute")
@login_required
@tx_visible_required("tx_id")
def delete_transaction(tx_id):
    """Deleting a transaction, and a recurring transaction isn't one transaction.

    The question is exactly a calendar's: "only this one" or "this and all
    following". There is no distinction here between "template" and occurrence:
    it is entirely internal, and whoever deletes March's rent shouldn't have to
    care whether March happens to be the month the series was opened.

    Without `mode`, a 409 is returned with the number of occurrences "all
    following" would delete, so the question is shown with the real number:
    the same pattern as `/api/family/join`."""
    user = get_current_user()
    mode = request.args.get("mode")

    try:
        occurrence, _ = db.recurring_occurrence(tx_id, user["family_id"])
    except db.DataUnavailable:
        # Unknown whether there is a series behind the row. A blind delete here
        # is exactly the damage that can't be undone, so refuse.
        return jsonify({"error": "לא הצלחנו לבדוק אם זו עסקה קבועה — נסו שוב"}), 503

    if occurrence and mode not in ("one", "later"):
        return jsonify({"needs_choice": True, "later": occurrence["later"]}), 409

    if occurrence and mode == "one":
        ok, err = db.delete_one_occurrence(
            tx_id, occurrence["template_id"], occurrence["date"], user["family_id"])
        if not ok:
            if err == "not found":
                return jsonify({"error": "העסקה כבר נמחקה"}), 404
            logger.error("delete_one_occurrence route: %s", err)
            return jsonify({"error": "המחיקה נכשלה — נסו שוב"}), 500
        return jsonify({"status": "ok", "deleted": 1})

    if occurrence and mode == "later":
        deleted, err = db.delete_occurrences_from(
            occurrence["template_id"], occurrence["date"], user["family_id"])
        if err:
            logger.error("delete_occurrences_from route: %s", err)
            return jsonify({"error": "המחיקה נכשלה — נסו שוב"}), 500
        return jsonify({"status": "ok", "deleted": deleted})

    receipt_path = db.get_transaction_receipt_path(tx_id, user["family_id"])
    ok, err = db.delete_transaction(tx_id, user["family_id"])
    if err == "not found":
        return jsonify({"error": "העסקה כבר נמחקה"}), 404
    if not ok:
        return jsonify({"error": "המחיקה נכשלה — נסו שוב"}), 500
    _delete_unused_receipts(user["family_id"], [receipt_path])
    return jsonify({"status": "ok"})


@app.route("/api/recurring/<template_id>", methods=["DELETE"])
@limiter.limit("30 per minute")
@login_required
@tx_visible_required("template_id")
def stop_recurring_route(template_id):
    """Stops a recurring series. Deliberately doesn't delete the row: it is the
    first occurrence in the series, i.e. money that really moved, and the
    dialog in settings promises that what was already created stays. Whoever
    wants to delete the transaction itself does it from the transaction list,
    like any transaction."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא משויכת משפחה לחשבון"}), 400

    ok, err = db.stop_recurring(template_id, user["family_id"])
    if not ok:
        if err == "not found":
            return jsonify({"error": "העסקה הקבועה לא נמצאה"}), 404
        logger.error("stop_recurring route: %s", err)
        return jsonify({"error": "ההסרה נכשלה — נסו שוב"}), 500
    return jsonify({"status": "ok"})


@app.route("/api/recurring/<template_id>/skip", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
@tx_visible_required("template_id")
def skip_recurring_route(template_id):
    """"Skip this month" on a projected occurrence in a future month (Matan,
    1.11), and "undo"."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא משויכת משפחה לחשבון"}), 400

    body = request.get_json(silent=True) or {}
    on_date = body.get("date")
    try:
        parsed = date.fromisoformat(on_date) if isinstance(on_date, str) else None
    except ValueError:
        parsed = None
    if not parsed or parsed <= clock.today():
        return jsonify({"error": "אפשר לדלג רק על חודש שעוד לא הגיע"}), 422

    ok, err = db.skip_recurring_date(template_id, user["family_id"], parsed.isoformat(),
                                     body.get("skip", True) is not False)
    if not ok:
        if err == "not found":
            return jsonify({"error": "העסקה הקבועה לא נמצאה"}), 404
        logger.error("skip_recurring route: %s", err)
        return jsonify({"error": "הדילוג נכשל — נסו שוב"}), 500
    return jsonify({"status": "ok"})


@app.route("/api/recurring/<template_id>/sync", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
@tx_visible_required("template_id")
def sync_recurring_template(template_id):
    """"Update from now on": splits the series at the edited occurrence (see
    `db.split_recurring_series`).

    The route no longer writes an amount, category or description. The ordinary
    edit route saved them on the occurrence a moment earlier, with all its
    checks, and the split only turns this occurrence into a template. That also
    closes what used to be here: a second write route, with its own
    validation, for the same fields."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף, ואם זה חוזר התחברו מחדש"}), 400

    body = request.get_json(silent=True) or {}
    instance_id = body.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        return jsonify({"error": "חסר איזה חודש נערך — רעננו את הדף ונסו שוב"}), 422

    new_id, err = db.split_recurring_series(template_id, instance_id, user["family_id"])
    if err:
        return jsonify({"error": "עדכון הסכום הקבוע נכשל — נסו שוב"}), 500
    if not new_id:
        return jsonify({"error": "העסקה הקבועה לא נמצאה"}), 404
    return jsonify({"status": "ok", "template_id": new_id})


# ─── API: Receipt scanning ──────────────────────────────────────────────────

@app.route("/api/receipts/discard", methods=["POST"])
@limiter.limit("30 per minute")
@login_required
def discard_receipt():
    """Deletes an image that was scanned and not saved with a transaction.

    Scanning uploads the image **before** there is a transaction. An abandoned
    scan (✕ without saving, a second scan in the same form, or a result that
    came back to a form already closed) left a file no row points at. The
    nightly cleanup meant to collect them failed on every run (see
    `db.delete_receipts`), so the browser asks to delete at the moment of
    abandonment.

    Only in the family's folder, and only a file no transaction points at:
    otherwise it is a way to delete an existing transaction's receipt."""
    user = get_current_user()
    path = (request.get_json(silent=True) or {}).get("path")
    if not _receipt_path_ok(path, user["family_id"]):
        return jsonify({"error": "הקובץ לא נמצא"}), 404
    if db.receipt_in_use(path, user["family_id"]):
        return jsonify({"error": "הקבלה מצורפת לעסקה"}), 409
    db.delete_receipt(session.get("access_token"), path)
    return jsonify({"status": "ok"})


@app.route("/api/receipts/scan", methods=["POST"])
# The only route here that costs real money, and the only one of the 23 writing
# routes that had no limit at all. One scan takes 2-6 seconds, so ten a minute
# affect no human; they affect a script.
@limiter.limit("10 per minute")
@login_required
def scan_receipt_route():
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף, ואם זה חוזר התחברו מחדש"}), 400

    try:
        used = db.receipt_scans_this_month(user["family_id"])
        used_globally = db.receipt_scans_globally_this_month()
    except db.DataUnavailable:
        # Unknown how much was used. The safe direction is to refuse: allowing
        # when the check failed effectively removes the limit on the OpenAI
        # account, and that is a real expense.
        return jsonify({
            "error": "לא הצלחנו לבדוק את מכסת הסריקות — נסו שוב, או הזינו ידנית.",
        }), 503

    # The global cap is checked before the family's: when it is crossed it isn't
    # the fault of whoever is standing here, and the message should say so rather
    # than blame them for a quota they didn't use.
    if used_globally >= db.RECEIPT_GLOBAL_MONTHLY_LIMIT:
        logger.error("receipt scans: the global cap was crossed (%s)", used_globally)
        return jsonify({
            "error": "סריקת הקבלות אינה זמינה כרגע. ניתן להזין את הפרטים ידנית.",
        }), 503

    if used >= db.RECEIPT_MONTHLY_LIMIT:
        return jsonify({
            "error": f"הגעתם למכסת הסריקות החודשית ({db.RECEIPT_MONTHLY_LIMIT}). ניתן להמשיך ולהזין ידנית.",
        }), 429

    file = request.files.get("image")
    if not file or not file.filename:
        return jsonify({"error": "לא התקבלה תמונה"}), 422

    # File-type validation before reading/storing: the same whitelist as the
    # scan itself
    if (file.mimetype or "") not in db._RECEIPT_MEDIA_TYPES:
        return jsonify({"error": "סוג הקובץ לא נתמך — נא לצלם או לבחור תמונה (JPG/PNG/WebP)"}), 422

    image_bytes = file.read()
    if not image_bytes:
        return jsonify({"error": "לא התקבלה תמונה"}), 422
    if len(image_bytes) > 6 * 1024 * 1024:
        return jsonify({"error": "התמונה גדולה מדי — נסו שוב עם תמונה קטנה יותר"}), 413

    all_categories = db.get_categories(user["family_id"])
    expense_category_names = [c["name"] for c in all_categories if c.get("type") == "expense"]

    data, err = db.scan_receipt(image_bytes, file.mimetype or "image/jpeg", expense_category_names)
    if err:
        return jsonify({"error": err}), 422

    # A successful scan: counted in the quota even if uploading the image to
    # storage failed
    db.record_receipt_scan(user["family_id"], user["id"])

    receipt_path, upload_err = db.upload_receipt(
        session.get("access_token"), user["family_id"], image_bytes, file.mimetype or "image/jpeg"
    )
    if upload_err:
        # The error was thrown away entirely: not logged, never reached Sentry,
        # and the quota was already counted. The user sees a successful scan
        # with no receipt tag and there is no trace explaining why. The OpenAI
        # call was already paid for, so don't cancel, just say it.
        logger.error("upload_receipt failed after a successful scan: %s", upload_err)
        receipt_path = None

    category_id = None
    if data.get("category_name"):
        for c in all_categories:
            if c.get("type") == "expense" and c.get("name") == data["category_name"]:
                category_id = c["id"]
                break

    return jsonify({
        "status":       "ok",
        "amount":       data["amount"],
        "merchant":     data["merchant"],
        "date":         data["date"],
        "category_id":  category_id,
        "receipt_path": receipt_path,
    })


@app.route("/receipts/<tx_id>", methods=["GET"])
@login_required
@tx_visible_required("tx_id")
def view_receipt(tx_id):
    """Redirects to the receipt attached to a transaction.

    A redirect and not JSON, and that is the whole point: before, the icon
    fetched the URL with fetch and then called window.open. On iOS that was
    always blocked: the browser allows opening a window only as a direct result
    of a tap, and waiting for the server breaks that link. No window, no error,
    nothing happens. In practice scanned receipts weren't reachable from the
    phone at all.

    Now the icon is an ordinary link, and the browser navigates by itself:
    navigation resulting from a tap isn't blocked. The signed URL is created
    here and not sent to the client in advance, so it also doesn't sit in the
    HTML of every row with a receipt.

    `/receipts/` and not `/api/receipts/` because this is browser navigation:
    under `/api/`, an expired session would return a 401 JSON to a new tab
    instead of redirecting to sign-in."""
    user = get_current_user()
    path = db.get_transaction_receipt_path(tx_id, user["family_id"])
    if not path:
        return render_template("error.html", code=404,
                               message="לא נמצאה קבלה מצורפת לעסקה הזו"), 404

    url, err = db.get_receipt_signed_url(session.get("access_token"), path)
    if err or not url:
        logger.error("receipt signed url: %s", err)
        return render_template("error.html", code=500,
                               message="טעינת הקבלה נכשלה — נסו שוב בעוד רגע"), 500

    response = redirect(url)
    # The signed URL is short-lived and personal; it must not be cached by a proxy
    response.headers["Cache-Control"] = "no-store"
    return response


# ─── Import from the credit-card company (Matan, 31.10) ─────────────────────
#
# Once a month: download the transaction statement from the card company's
# site, upload it here, see a table with a suggested category per transaction
# and a mark on what already exists, and confirm. Two steps and two routes:
# `preview` reads the file and writes nothing, `commit` takes only the rows
# that were confirmed. The file itself is never stored anywhere.

def _expense_categories(family_id):
    return [c for c in db.get_categories(family_id) if c.get("type") == "expense"]


def _json_form_field(name, kind):
    """A JSON field in a multipart form, or `None` when it is missing or broken."""
    raw = request.form.get(name)
    if not raw or len(raw) > 50_000:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, kind) else None


@app.route("/import")
@login_required
def import_page():
    user = get_current_user()
    if not user["family_id"]:
        return redirect(url_for("onboarding"))
    attribution = (family_settings() or {}).get("owner_attribution", {}).get("expense", False)
    members = db.get_family_members(user["family_id"]) if attribution else []
    return render_template(
        "import.html", active_page="settings", user=user,
        categories=[{"id": c["id"], "name": c.get("name") or "", "icon": c.get("icon") or ""}
                    for c in _expense_categories(user["family_id"])],
        members=[{"id": m["id"], "name": m.get("name") or ""} for m in members],
        show_owner=bool(attribution and len(members) > 1),
    )


@app.route("/api/import/preview", methods=["POST"])
# Calls OpenAI when the file is unknown and to guess categories for new
# businesses: real money, like receipt scanning. A real import is once a month;
# ten a minute is for retries with another file, and sixty a day is a cap for
# a script.
@limiter.limit("10 per minute;60 per day")
@login_required
def import_preview_route():
    user = get_current_user()
    family_id = user["family_id"]
    if not family_id:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף, ואם זה חוזר התחברו מחדש"}), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "לא התקבל קובץ"}), 422
    data = file.read(card_import.MAX_FILE_BYTES + 1)

    layouts = [l for l in (_json_form_field("layouts", list) or [])[:20] if isinstance(l, dict)]
    chosen = _json_form_field("mapping", dict)       # a manual choice (layer 3)
    if chosen:
        layouts.insert(0, chosen)

    try:
        sheets = card_import.read_sheets(data, file.filename)
        txs, used = card_import.extract(sheets, layouts)
        ai_mapped = False
        if not used:
            candidate = card_import.find_header_candidate(sheets)
            if not candidate:
                return jsonify({"error": "לא מצאנו בקובץ טבלת עסקאות. ודאו שזה קובץ פירוט "
                                         "העסקאות שהורד מהאתר של חברת האשראי."}), 422
            mapping = None if chosen else card_import.ai_map_columns(
                candidate["headers"], candidate["samples"])
            if mapping:
                txs, used = card_import.extract(
                    sheets, [{"signature": candidate["signature"], "mapping": mapping}])
                ai_mapped = bool(used)
            if not used:
                # Layer 3: the user picks which column is what
                return jsonify({"status": "needs_mapping", **candidate})
    except card_import.ImportError_ as e:
        return jsonify({"error": str(e)}), 422

    if not txs:
        return jsonify({"error": "לא נמצאו עסקאות בקובץ"}), 422

    categories = _expense_categories(family_id)
    valid_ids = {c["id"] for c in categories}
    by_name = {c["name"]: c["id"] for c in categories}

    days = [date.fromisoformat(t["date"]) for t in txs]
    pad = timedelta(days=card_import.DUPLICATE_DAYS)
    try:
        existing = db.import_existing_expenses(family_id, user["id"], (min(days) - pad).isoformat(),
                                               (max(days) + pad).isoformat())
    except db.DataUnavailable:
        # Without the duplicate check every transaction already entered manually
        # would go in twice
        return jsonify({"error": "לא הצלחנו לבדוק מה כבר קיים — נסו שוב בעוד רגע"}), 503
    history = db.import_category_history(family_id, (clock.today() - timedelta(days=365)).isoformat())
    learned = card_import.learned_categories(history)

    for i, t in enumerate(txs):
        t["id"] = i
        cat = card_import.lookup_learned(card_import.merchant_key(t["merchant"]), learned)
        source = "history"
        if cat not in valid_ids:
            cat, source = card_import.rule_category(t["merchant"], categories), "rule"
        if not cat:
            cat, source = card_import.sector_category(t.get("sector"), categories), "sector"
        t["category_id"], t["category_source"] = (cat, source) if cat else (None, None)
        if t["refund"]:
            t["status"] = "refund"

    card_import.mark_duplicates([t for t in txs if not t["refund"]], existing)
    _offer_recurring(txs, family_id, user["id"])

    unknown = sorted({t["merchant"] for t in txs if not t["category_id"] and t["status"] != "refund"})
    ai_categorized = False
    if unknown:
        guesses = card_import.ai_categorize(unknown, list(by_name))
        for t in txs:
            name = guesses.get(t["merchant"])
            if not t["category_id"] and name in by_name:
                t["category_id"], t["category_source"] = by_name[name], "ai"
                ai_categorized = True

    counts = {s: sum(1 for t in txs if t["status"] == s) for s in ("new", "exists", "maybe", "refund")}
    return jsonify({"status": "ok", "transactions": txs, "layouts": used, "counts": counts,
                    "ai_mapped": ai_mapped, "ai_categorized": ai_categorized})


def _offer_recurring(txs, family_id, viewer_id):
    """Standing orders (Matan, 1.11): offered as a monthly recurring transaction,
    and not repeated on every import. Only for each business's last charge in
    the file (Costa Gym was charged `₪255` in August and `₪179` in September:
    one series, at the current amount), and not when the family already has an
    active series for the same business."""
    try:
        templates = [r for r in db.get_recurring_transactions(family_id, viewer_id)
                     if db.is_active_template(r)]
    except db.DataUnavailable:
        templates = []
    known = [card_import.merchant_key(r.get("description")) for r in templates]
    latest = {}
    for t in txs:
        t["recurring_offer"] = False
        t["already_recurring"] = False
        if not t.get("standing_order") or t["refund"]:
            continue
        key = card_import.merchant_key(t["merchant"])
        if any(card_import._related(key, k) for k in known if k):
            t["already_recurring"] = True
            continue
        if key not in latest or t["date"] >= latest[key]["date"]:
            latest[key] = t
    for t in latest.values():
        t["recurring_offer"] = True


@app.route("/api/import/commit", methods=["POST"])
@limiter.limit("10 per minute")
@login_required
def import_commit_route():
    user = get_current_user()
    family_id = user["family_id"]
    if not family_id:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף, ואם זה חוזר התחברו מחדש"}), 400

    body = request.get_json(silent=True) or {}
    rows = body.get("transactions")
    if not isinstance(rows, list) or not rows:
        return jsonify({"error": "לא נבחרו עסקאות לייבוא"}), 422
    if len(rows) > card_import.MAX_TRANSACTIONS:
        return jsonify({"error": f"אפשר לייבא עד {card_import.MAX_TRANSACTIONS} עסקאות בפעם אחת"}), 422

    owner_id, owner_err = _resolve_owner(body, user, "expense")
    if owner_err:
        return jsonify({"error": owner_err}), 422

    valid_ids = {c["id"] for c in _expense_categories(family_id)}
    payload = []
    for n, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            return jsonify({"error": f"שורה {n} אינה תקינה"}), 422
        amount, err = _parse_amount(row.get("amount"))
        if err:
            return jsonify({"error": f"שורה {n}: {err}"}), 422
        tx_date, err = _parse_date(row.get("date"))
        if err:
            return jsonify({"error": f"שורה {n}: {err}"}), 422
        if row.get("category_id") not in valid_ids:
            return jsonify({"error": f"שורה {n}: נא לבחור קטגוריה"}), 422
        payload.append({
            "amount":       round(amount, 2),
            "type":         "expense",
            "date":         tx_date,
            "description":  _text(row.get("description"))[:120],
            "category_id":  row["category_id"],
            "user_id":      owner_id,
            "family_id":    family_id,
            "is_recurring": False,
            # The same keys on every row: PostgREST inserts an array only when
            # they all have the same columns
            "recurring_frequency": None,
            "recurring_end_date":  None,
        })
        # A standing order confirmed as a recurring transaction: a monthly
        # template on the same day of the month, like a recurring transaction
        # entered manually. The following months are created by themselves.
        if row.get("recurring") is True:
            payload[-1].update(is_recurring=True, recurring_frequency="monthly_same")

    created, err = db.add_transactions(payload)
    if err:
        logger.error("import_commit: %s", err)
        return jsonify({"error": "הייבוא נכשל ושום עסקה לא נשמרה — נסו שוב"}), 500
    recurring = sum(1 for p in payload if p["is_recurring"])
    if recurring:
        db.materialize_recurring(family_id)
    return jsonify({"status": "ok", "created": created, "recurring": recurring}), 201


# ─── Phone notifications (Matan, 31.10) ─────────────────────────────────────
#
# `backend/push.py`: the encryption and the wording. Here: registering the
# device, removing it, and the daily run `pg_cron` triggers (month closed, last
# installment, a week with nothing recorded).

@app.route("/api/push/subscribe", methods=["POST"])
@limiter.limit("20 per hour")
@login_required
def push_subscribe_route():
    if not push.enabled():
        return jsonify({"error": "התראות אינן זמינות כרגע"}), 503
    body = request.get_json(silent=True) or {}
    endpoint = _text(body.get("endpoint"))
    keys = body.get("keys") if isinstance(body.get("keys"), dict) else {}
    p256dh, auth = _text(keys.get("p256dh")), _text(keys.get("auth"))
    if not push._allowed_endpoint(endpoint) or len(endpoint) > 1000 \
            or not 60 <= len(p256dh) <= 200 or not 16 <= len(auth) <= 100:
        return jsonify({"error": "המכשיר הזה לא תומך בהתראות"}), 422
    if not db.push_subscribe(endpoint, p256dh, auth):
        return jsonify({"error": "ההרשמה להתראות נכשלה — נסו שוב"}), 500
    session["push_endpoint"] = endpoint
    return jsonify({"status": "ok"})


@app.route("/api/push/unsubscribe", methods=["POST"])
@limiter.limit("20 per hour")
@login_required
def push_unsubscribe_route():
    endpoint = _text((request.get_json(silent=True) or {}).get("endpoint")) or session.get("push_endpoint")
    if endpoint:
        db.push_unsubscribe(endpoint)
    session.pop("push_endpoint", None)
    return jsonify({"status": "ok"})


def _daily_message(kind: str, data: dict):
    if kind == "month_closed":
        return push.month_closed(int(data.get("month") or 1), data.get("income"),
                                 data.get("expense"), data.get("savings"))
    if kind == "last_installment":
        return push.last_installment(data.get("description"), data.get("amount"))
    if kind == "quiet_week":
        return push.quiet_week(int(data.get("weeks") or 1))
    return None


@app.route("/internal/push/daily", methods=["POST"])
@limiter.limit("10 per hour")
def push_daily_route():
    """`pg_cron` calls here once a day. Without the right secret: 404, as if there
    were nothing here. The secret is passed to the database too: `push_daily`
    checks it against the Vault, so even a direct call to the RPC without the
    secret returns nothing."""
    secret = os.environ.get("PUSH_CRON_SECRET", "")
    given = request.headers.get("X-Cron-Secret", "")
    if not secret or not secrets.compare_digest(secret.encode(), given.encode()):
        return jsonify({"error": "לא נמצא"}), 404
    rows = db.push_daily(secret, clock.today().isoformat())
    groups = {}
    for row in rows:
        key = (row.get("kind"), json.dumps(row.get("data") or {}, sort_keys=True))
        groups.setdefault(key, []).append(row)
    sent, dead = 0, []
    for (kind, _), subs in groups.items():
        message = _daily_message(kind, subs[0].get("data") or {})
        if not message:
            continue
        dead += push.send(subs, message)
        sent += len(subs)
    if dead:
        db.push_forget(secret, dead)
    logger.info("push daily: %s notifications, %s devices removed", sent, len(dead))
    return jsonify({"status": "ok", "sent": sent, "forgotten": len(dead)})


# ─── API: Categories ──────────────────────────────────────────────────────────

@app.route("/api/categories", methods=["GET"])
@login_required
def get_categories():
    user = get_current_user()
    cats = db.get_categories(user["family_id"])
    return jsonify(cats)


@app.route("/api/categories", methods=["POST"])
@limiter.limit("30 per minute")
@login_required
def add_category():
    user = get_current_user()
    body = request.get_json(silent=True) or {}

    # Both fields went straight to the database. `name` is `TEXT NOT NULL`, which
    # accepts an empty string, so a category with no name was created: a row in
    # the monthly breakdown that can't be identified or searched. And an unknown
    # `type` was stopped only by the database CHECK and came back as an opaque
    # 500, while every other write route already validates it.
    name, icon, field_err = _category_fields(body)
    if field_err:
        return jsonify({"error": field_err}), 422

    type_ = body.get("type", "expense")
    if type_ not in ("expense", "income", "savings"):
        return jsonify({"error": "מחלקת הקטגוריה חייבת להיות הוצאה, הכנסה או חיסכון"}), 422

    cat, err = db.add_custom_category(
        family_id=user["family_id"],
        name=name,
        icon=icon,
        type_=type_,
    )
    if err:
        logger.error("add_category route: %s", err)
        return jsonify({"error": "הוספת הקטגוריה נכשלה — נסו שוב"}), 500
    return jsonify(cat), 201


def _category_key(name, type_) -> tuple:
    return (" ".join((name or "").split()), type_)


def _missing_default_categories(family_id) -> tuple:
    """The default categories the family doesn't have (by name and department),
    and what it already has."""
    existing = [c for c in db.get_categories(family_id) if c.get("family_id") == family_id]
    have = {_category_key(c.get("name"), c.get("type")) for c in existing}
    missing = [dict(c) for c in _DEFAULT_CATEGORIES if _category_key(c["name"], c["type"]) not in have]
    return missing, existing


@app.route("/api/categories/defaults", methods=["GET"])
@login_required
def missing_default_categories_route():
    """What restoring the defaults will add, so the confirmation can say it by name."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף"}), 400
    missing, _ = _missing_default_categories(user["family_id"])
    return jsonify({"missing": missing})


@app.route("/api/categories/defaults", methods=["POST"])
@limiter.limit("10 per minute")
@login_required
def restore_default_categories_route():
    """Restores the missing default categories (Matan, 31.10: "for whoever deleted
    by mistake").

    Only adds: an existing category doesn't change and isn't deleted, and
    transactions don't move, so it is open to every family member, like adding
    a category. What already exists with the same name and department isn't
    added again, so a double tap doesn't create duplicates."""
    user = get_current_user()
    family_id = user["family_id"]
    if not family_id:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף"}), 400
    missing, existing = _missing_default_categories(family_id)
    if not missing:
        return jsonify({"status": "ok", "added": 0})
    start = {}
    for c in existing:
        t = c.get("type")
        start[t] = max(start.get(t, 0), c.get("sort_order") or 0)
    added, err = db.bulk_add_categories(family_id, missing, start=start)
    if err:
        logger.error("restore_default_categories: %s", err)
        return jsonify({"error": "ההוספה נכשלה — נסו שוב"}), 500
    return jsonify({"status": "ok", "added": added}), 201


@app.route("/api/categories/<cat_id>", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
def update_category(cat_id):
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    name, icon, field_err = _category_fields(body)
    if field_err:
        return jsonify({"error": field_err}), 422
    if not name:
        return jsonify({"error": "נא להזין שם קטגוריה"}), 422
    ok = db.update_category(cat_id, user["family_id"], name, icon)
    if not ok:
        return jsonify({"error": "עדכון נכשל"}), 500
    return jsonify({"status": "ok", "name": name, "icon": icon})


# ─── API: Categories delete ───────────────────────────────────────────────────

@app.route("/api/categories/<cat_id>", methods=["DELETE"])
@limiter.limit("30 per minute")
@login_required
def delete_category(cat_id):
    user = get_current_user()

    denied = _require_manager()
    if denied:
        return denied
    return _delete_category("family", cat_id, user)


@app.route("/api/categories/<cat_id>/usage", methods=["GET"])
@login_required
def category_usage(cat_id):
    return _category_usage("family", cat_id, get_current_user())


_TYPE_NOUN = {"expense": "הוצאה", "income": "הכנסה", "savings": "חיסכון"}


def _deletion_plan(kind, cat_id, user, project=None):
    """The plan from `db.category_deletion_plan` plus `blocked`: why it can't be
    deleted at all, or `None`.

    The last category of a type stays: without it no transaction of that type
    can be recorded, and the transactions already in it have nowhere to move.
    In a project this applies only to a type the project tracks, or when it has
    transactions: an empty income category in a project that doesn't track
    income can be deleted."""
    plan = db.category_deletion_plan(kind, cat_id, user["family_id"],
                                     project_id=(project or {}).get("id"))
    if plan is None:
        return None
    type_ = plan["category"].get("type")
    needed = kind == "family" or plan["count"] > 0 \
        or bool((project or {}).get(f"track_{type_}"))
    plan["blocked"] = None
    if not plan["alternatives"] and needed:
        plan["blocked"] = (f"זו קטגוריית ה{_TYPE_NOUN.get(type_, '')} האחרונה — "
                           "בלעדיה אי אפשר לרשום עסקאות במחלקה הזאת. "
                           "אפשר לשנות לה את השם במקום למחוק.")
    return plan


def _category_usage(kind, cat_id, user, project=None):
    try:
        plan = _deletion_plan(kind, cat_id, user, project)
    except db.DataUnavailable:
        return jsonify({"error": "השירות אינו זמין כרגע — נסו שוב בעוד רגע"}), 503
    if plan is None:
        return jsonify({"error": "הקטגוריה לא נמצאה"}), 404
    return jsonify({k: plan[k] for k in ("count", "alternatives", "blocked")})


def _delete_category(kind, cat_id, user, project=None):
    """Delete with a move: the category's transactions move to `?move_to=` before
    the delete. Without a valid target when there are transactions: 409, and
    nothing is touched."""
    try:
        plan = _deletion_plan(kind, cat_id, user, project)
    except db.DataUnavailable:
        return jsonify({"error": "השירות אינו זמין כרגע — נסו שוב בעוד רגע"}), 503
    if plan is None:
        return jsonify({"error": "הקטגוריה לא נמצאה"}), 404
    if plan["blocked"]:
        return jsonify({"error": plan["blocked"]}), 409

    move_to = None
    if plan["count"]:
        move_to = request.args.get("move_to")
        if move_to not in {c["id"] for c in plan["alternatives"]}:
            return jsonify({"error": "נא לבחור לאן להעביר את העסקאות של הקטגוריה",
                            "needs_target": True}), 409

    if not db.delete_category_moving(kind, cat_id, user["family_id"], move_to,
                                     project_id=(project or {}).get("id")):
        # A race lands here too: a transaction added to the category after the
        # move makes the delete fail on the database constraint, and the
        # transactions already moved stay at the target
        return jsonify({"error": "מחיקת הקטגוריה נכשלה — נסו שוב"}), 500
    return jsonify({"status": "ok", "moved": plan["count"] if move_to else 0})


@app.route("/api/categories/reorder", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
def reorder_categories_route():
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    type_ = body.get("type")
    order = body.get("order") or []
    if type_ not in ("income", "expense", "savings") or not isinstance(order, list) or not order:
        return jsonify({"error": "קלט לא תקין"}), 422
    ok = db.reorder_categories(user["family_id"], type_, order)
    if not ok:
        return jsonify({"error": "עדכון הסדר נכשל"}), 500
    return jsonify({"status": "ok"})


# ─── API: Family ──────────────────────────────────────────────────────────────

@app.route("/api/family/settings", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
def update_family_settings_route():
    """Updates the family settings. Takes a partial update and merges it into the
    existing ones."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף, ואם זה חוזר התחברו מחדש"}), 400

    body  = request.get_json(silent=True) or {}
    patch = {}

    if isinstance(body.get("owner_attribution"), dict):
        oa = body["owner_attribution"]
        patch["owner_attribution"] = {
            k: bool(oa[k]) for k in ("expense", "income", "savings") if k in oa
        }

    # Category budgets. They come from the client, so they are validated like
    # any other money amount, and checked to really be this family's categories.
    if isinstance(body.get("limits"), dict):
        family_categories = {c["id"] for c in db.get_categories(user["family_id"])}
        limits = dict((family_settings().get("limits") or {}))

        for cat_id, entry in body["limits"].items():
            if cat_id not in family_categories:
                return jsonify({"error": "קטגוריה לא נמצאה"}), 422

            # `null` or an amount of 0 = removing the budget. This is the way to
            # cancel, not a separate delete, so "don't set a budget" is the same
            # route as "remove".
            if entry is None:
                limits.pop(cat_id, None)
                continue
            if not isinstance(entry, dict):
                return jsonify({"error": "מבנה תקציב לא תקין"}), 422

            amount, err = _parse_amount(entry.get("amount"))
            if entry.get("amount") in (None, "", 0, "0"):
                limits.pop(cat_id, None)
                continue
            if err:
                return jsonify({"error": f"תקציב הקטגוריה: {err}"}), 422

            limits[cat_id] = {"amount": amount, "alert": bool(entry.get("alert", True))}

        # The full map, because removal is absence from it. `_merge_settings`
        # replaces `limits` whole rather than merging it; see `_WHOLE_MAP_KEYS`.
        # Before that it was merged with `.update()`, which can only add, so a
        # removal was never saved.
        patch["limits"] = limits

    if not patch:
        return jsonify({"error": "לא התקבלו הגדרות לעדכון"}), 422

    if not db.update_family_settings(user["family_id"], patch):
        return jsonify({"error": "שמירת ההעדפות נכשלה"}), 500
    return jsonify({"status": "ok", "settings": db.get_family_settings(user["family_id"])})


@app.route("/api/family/member-color", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
def set_member_color():
    """Picking a colour for a family member (Matan, 30.9, round 6, item 13). Each
    for themselves only: even the family manager doesn't choose for others
    (Matan). Someone else's colour is taken."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף"}), 400
    body = request.get_json(silent=True) or {}
    member_id, color = body.get("member_id"), body.get("color")
    if not isinstance(color, int) or isinstance(color, bool) or not 0 <= color < len(_OWNER_HEX):
        return jsonify({"error": "צבע לא תקין"}), 422
    members = db.get_family_members(user["family_id"])
    if member_id not in {m["id"] for m in members}:
        return jsonify({"error": "בן המשפחה לא נמצא"}), 404
    if member_id != user["id"]:
        return jsonify({"error": "כל אחד בוחר רק את הצבע של עצמו"}), 403
    chosen = dict((family_settings() or {}).get("member_colors") or {})
    now = assign_member_colors(members, chosen)
    if any(c == color and mid != member_id for mid, c in now.items()):
        return jsonify({"error": "הצבע הזה תפוס — בחרו צבע אחר"}), 409
    chosen[member_id] = color
    if not db.update_family_settings(user["family_id"], {"member_colors": chosen}):
        return jsonify({"error": "שמירת הצבע נכשלה"}), 500
    return jsonify({"status": "ok", "colors": assign_member_colors(members, chosen)})


@app.route("/api/family", methods=["PUT"])
@limiter.limit("20 per minute")
@login_required
def update_family():
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    name = _text(body.get("name"))
    if not name:
        return jsonify({"error": "נא להזין שם"}), 422
    ok = db.update_family_name(user["family_id"], name)
    return jsonify({"status": "ok" if ok else "error"})


def _user_message(err, fallback: str = "הפעולה נכשלה — נסו שוב") -> str:
    """Returns a message fit to show the user.

    The DB layer returns two kinds of strings in the same place: messages
    written for the user ("רק הבעלים של הפרויקט יכול…") and internal markers
    written for the developer ("Database not configured", or a raw `str(e)` from
    Supabase). The routes showed both as they were.

    The rule here is simple on purpose: a message for the user is written in
    Hebrew. A string with no Hebrew is internal: it goes to the log and not to
    the screen. That way a future error added in the DB layer won't leak to the
    UI without anyone noticing."""
    if err and re.search(r"[\u0590-\u05FF]", str(err)):
        return str(err)
    if err:
        logger.warning("internal error surfaced to a route: %s", err)
    return fallback


def _family_rpc_message(err: str) -> str:
    """Translates the RPC errors into a Hebrew message. The original messages are
    in English and meant for the log, not the user."""
    text = (err or "").lower()
    if "only the family manager" in text:
        return "רק מנהל המשפחה יכול להסיר בני משפחה"
    if "manager cannot be removed" in text:
        return "אי אפשר להסיר את מנהל המשפחה"
    if "not a member of your family" in text:
        return "זה לא בן משפחה שלכם"
    if "leave_family to remove yourself" in text:
        return "כדי לצאת מהמשפחה השתמשו ב\"עזיבת המשפחה\""
    return "הפעולה נכשלה — נסו שוב"


def _project_choice(body) -> str:
    """"Delete" or "keep as shared"; whatever hasn't been chosen yet is a question."""
    choice = body.get("projects")
    return choice if choice in ("share", "delete") else "ask"


def _needs_project_choice(err):
    """409 with the number, so the screen asks: the same pattern as
    `/api/family/join`."""
    count = db.project_choice_needed(err)
    if not count:
        return None
    return jsonify({"needs_choice": True, "project_count": count}), 409


@app.route("/api/family/members/<member_id>", methods=["DELETE"])
@login_required
@limiter.limit("10 per minute")
def remove_family_member_route(member_id):
    """Removing a family member. Family manager only, enforced in the DB.

    keep_transactions is the user's choice, not a silent default: the money
    left the shared budget, so "keep" keeps the monthly totals right and the
    transaction is shown as "shared". "Delete" retroactively changes every
    monthly summary it appears in, so it must be a conscious choice."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא משויכת משפחה לחשבון"}), 400

    body = request.get_json(silent=True) or {}
    keep = body.get("keep_transactions", True) is not False

    # The removed member's personal projects are always deleted, with their
    # transactions (Matan's decision, 28.9.2026): the manager can't see them, and
    # must not get hold of the private things of whoever was removed. Not read
    # from the body: someone leaving by themselves is the only one asked.
    # The receipts of everything to be deleted: their personal projects always,
    # and their transactions if it was chosen to delete them. The manager stays
    # in the family, so they may delete afterwards.
    receipts = db.receipt_paths(
        user["family_id"], project_ids=db.personal_project_ids(user["family_id"], member_id),
        user_id=None if keep else member_id)
    ok, err = db.remove_family_member(member_id, keep_transactions=keep, projects="delete")
    if ok:
        _delete_unused_receipts(user["family_id"], receipts)
    if not ok:
        return jsonify({"error": _family_rpc_message(err)}), 403
    return jsonify({"status": "ok"})


@app.route("/api/family/leave", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def leave_family_route():
    """Leaving the family. Any family member may, including the manager: when they
    leave, management passes to the longest-standing member who stays, so no
    family is left without a manager."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא משויכת משפחה לחשבון"}), 400

    body = request.get_json(silent=True) or {}
    keep = body.get("keep_transactions", True) is not False

    choice = _project_choice(body)
    # Whoever leaves loses the family's storage permission the moment they leave,
    # so the receipts of what will be deleted are deleted **before**, and so we
    # first need to know for certain what will be deleted. If there are personal
    # projects and no choice has been made yet, ask before touching anything.
    projects = db.personal_project_ids(user["family_id"], user["id"])
    if projects and choice == "ask":
        return jsonify({"needs_choice": True, "project_count": len(projects)}), 409
    doomed = projects if (choice == "delete" or not keep) else []
    db.delete_receipts(session.get("access_token"),
                       db.receipt_paths(user["family_id"], project_ids=doomed,
                                        user_id=None if keep else user["id"]))
    new_family_id, err = db.leave_family(keep_transactions=keep, projects=choice)
    if err and _needs_project_choice(err):
        return _needs_project_choice(err)
    if err or not new_family_id:
        return jsonify({"error": _family_rpc_message(err)}), 400

    # The session carries the old family; without this update every query in the
    # next request would be filtered by a family the user is no longer in, and
    # the app would look empty
    session["family_id"] = new_family_id
    return jsonify({"status": "ok"})


@app.route("/api/family/invite-code", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def rotate_invite_code_route():
    """Replacing the invite code. The old code stops working immediately: that is
    the whole point."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא משויכת משפחה לחשבון"}), 400

    denied = _require_manager()
    if denied:
        return denied

    code, err = db.rotate_invite_code()
    if err or not code:
        return jsonify({"error": "החלפת הקוד נכשלה — נסו שוב"}), 400
    return jsonify({"status": "ok", "invite_code": code})


@app.route("/api/family/preview", methods=["GET"])
@login_required
@limiter.limit("20 per minute")
def preview_family_code():
    """A preview before joining: "מצטרפים למשפחת כהן?". Rate-limited so it can't
    be used to scan codes."""
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"error": "נא להזין קוד הזמנה"}), 422
    name = db.family_name_for_code(code)
    if not name:
        return jsonify({"found": False}), 404
    return jsonify({"found": True, "name": name})


@app.route("/api/family/join", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def join_family():
    """Joining an existing family after signing up.

    Until now the moment of signup was the only chance in the product's whole
    life to enter a code, and whoever signed up before getting one was stuck in
    a family of their own with no way back."""
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    code = _text(body.get("code"))
    if not code:
        return jsonify({"error": "לא הוזן קוד הזמנה"}), 422

    # Moving to another family abandons the existing transactions: they stay
    # tied to the old family. For a new user that is harmless; for a user with
    # history it is devastating, so explicit confirmation is required and the
    # number is returned so the UI can show it.
    current_family = user.get("family_id")
    if current_family and not body.get("confirm"):
        try:
            existing = db.family_transaction_count(current_family)
        except db.DataUnavailable:
            # Unknown how much history would be abandoned. Skipping the
            # confirmation because of a failure is exactly the loss the
            # confirmation exists to prevent, so ask for it anyway.
            existing = None
        if existing is None or existing:
            return jsonify({
                "needs_confirm": True,
                "transaction_count": existing,
            }), 409

    family_id, err = db.join_family_by_code(code)
    if err:
        return jsonify({"error": err}), 400
    _notify_owner_about_family("joined", family_id, person_id=user["id"])

    # Without updating the session the user would keep seeing the old family
    # until signing out and in
    session["family_id"] = family_id
    return jsonify({"status": "ok", "family_id": family_id})


@app.route("/privacy")
def privacy():
    """The privacy policy, public on purpose: someone considering signing up
    needs to read it before handing over data, not after."""
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


# WhatsApp, the iPhone and search engines request these at a fixed URL without
# reading the HTML, and without them a link to the app showed up with no logo
# (`404` in the logs, 13.10).
@app.route("/favicon.ico")
@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def touch_icon():
    response = app.send_static_file("icons/icon-180.png")
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response


@app.route("/robots.txt")
def robots_txt():
    """Only the API and receipts are closed to crawlers. The app's pages require
    sign-in anyway, and the landing page and invite links need a preview when
    shared."""
    return app.response_class("User-agent: *\nDisallow: /api/\nDisallow: /receipts/\n",
                              mimetype="text/plain")


@app.route("/sw.js")
def service_worker():
    """Serves the Service Worker from the root so its scope covers the whole site
    (registering from /static/ is blocked by the browser)."""
    return app.send_static_file("sw.js")


# ─── Health checks ──────────────────────────────────────────────────────────
#
# Two checks, because two different questions are asked here:
#
#   /health     "Is the process alive?" This is Railway's check, and it returns
#               200 as long as the server answers. If it failed because of a
#               Supabase outage, the next deploy would be blocked at exactly
#               the worst moment: a two-minute database outage would turn into
#               a much longer one.
#   /health/db  "Can users actually use the app?" This is the check an external
#               monitor should follow, and it returns 503 when the database
#               doesn't answer.
#
# Before, only the first one existed, and it returned a constant "ok", i.e.
# even when the database was dead. A monitor would report all clear while no
# family could see their money, and the only way to find out was for someone to
# bother telling us.

_HEALTH_TTL = 5.0            # seconds
_health_cache = {"at": -_HEALTH_TTL, "ok": False, "detail": ""}


def _database_health():
    """The database's state, remembered for five seconds.

    Without the memory, a frequent monitor (or someone holding F5) would occupy
    a worker for every check, and exactly during an outage, when checks
    multiply and the server is loaded, there are four workers in total."""
    now = time.monotonic()
    if now - _health_cache["at"] >= _HEALTH_TTL:
        ok, detail = db.ping()
        _health_cache.update(at=now, ok=ok, detail=detail)
        if not ok:
            # `error` and not `warning`: an unavailable database is an event in
            # Sentry. At `warning` it is a breadcrumb, and the only way to find
            # out about an outage was for a user to complain.
            logger.error("health: database unreachable (%s)", detail)
    return _health_cache["ok"], _health_cache["detail"]


@app.route("/health")
def health():
    ok, detail = _database_health()
    # Always 200, but it tells the truth in the response body.
    #
    # `rate_limiting` is here because there was no other way to know: the limit
    # weakens silently when the environment variable is missing or Redis is
    # unavailable, and requests keep working. Now it is one `curl` away.
    return jsonify({
        "status":        "ok",
        "database":      "ok" if ok else detail,
        "rate_limiting": "shared" if RATELIMIT_IS_SHARED else "per-worker",
    }), 200


@app.route("/health/db")
def health_db():
    ok, detail = _database_health()
    if ok:
        return jsonify({"status": "ok", "database": "ok"}), 200
    return jsonify({"status": "degraded", "database": detail}), 503


# ─── Security headers ─────────────────────────────────────────────────────────

# Content-Security-Policy: the strongest protection against XSS; the browser
# refuses to run code that didn't come from the allowed sources. It could only
# be set after all the JS was moved out of the HTML into files: a policy that
# allows 'unsafe-inline' gives up most of the value, because that is exactly
# what code injection looks like.
#
# script-src is 'self' only: Chart.js was downloaded locally instead of loading
# from a CDN. style-src needs 'unsafe-inline' because the templates use
# style="..." for computed values (progress-bar widths, a colour per family
# member); that is an accepted, narrow relaxation, and it doesn't allow running
# code.
_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    # Google was removed from both: the font is self-hosted, and a policy that
    # allows unused sources is just needless attack surface.
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    # + Supabase storage: the receipt opens inside the app (`receipt-viewer.js`,
    # Matan 12.10), and `/receipts/<id>` redirects to a signed URL there. Images
    # only, not scripts.
    "img-src 'self' data: blob:" + (
        " " + re.match(r"https?://[^/]+", os.environ.get("SUPABASE_URL", "")).group(0)
        if re.match(r"https?://[^/]+", os.environ.get("SUPABASE_URL", "")) else ""),
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
])

# The reporting mode is controlled by the environment so it can first run as
# Report-Only, see what breaks in real production, and only then enforce. The
# default is enforce.
_CSP_REPORT_ONLY = os.environ.get("CSP_REPORT_ONLY") == "1"


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    header = ("Content-Security-Policy-Report-Only" if _CSP_REPORT_ONLY
              else "Content-Security-Policy")
    response.headers.setdefault(header, _CSP)
    if not _IS_DEV:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# ─── Error handlers ───────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    msg = "הדף שחיפשתם לא נמצא"
    if _is_api_request():
        return jsonify({"error": msg}), 404
    return render_template("error.html", code=404, message=msg), 404


@app.errorhandler(413)
def payload_too_large(e):
    return jsonify({"error": "הקובץ או הבקשה גדולים מדי (מקסימום 8MB)"}), 413


@app.errorhandler(429)
def too_many_requests(e):
    msg = "יותר מדי ניסיונות בזמן קצר — נסו שוב בעוד דקה"
    if _is_api_request():
        return jsonify({"error": msg}), 429

    # The auth routes get the form itself back, with the message above it.
    # error.html is a dead end (it only has "back to the home page"), and whoever
    # got blocked ended up there exactly because they double-clicked "sign in"
    # and didn't understand why nothing happened. Sending them from there to a
    # dead page, without what they typed, punishes them for impatience.
    if request.path in ("/login", "/signup"):
        return render_template(
            "login.html", error=msg,
            active_tab="signup" if request.path == "/signup" else "login",
            remembered_identifier=_remembered_identifier(),
            # Here too: whoever got blocked after a double click shouldn't have
            # to type everything again
            signup=_signup_form() if request.path == "/signup" else None,
        ), 429

    return render_template("error.html", code=429, message=msg), 429


def _family_changed():
    """Updates the session if the user is already in another family; returns the
    new family's name, or `None`.

    The session remembers the family from the moment of sign-in. Whoever was
    removed from it (or left it on another device) kept sending the old id, and
    the database no longer answered for it: almost every screen became an error
    page asking to refresh, and refreshing didn't help until signing out.

    Called only after a failed query, not on every request: in normal use there
    isn't a single extra query. A failure of the check itself is "unknown", not
    "changed"."""
    uid = session.get("user_id")
    if not uid:
        return None
    try:
        profile, ok = db.fetch_profile(uid)
    except Exception:
        return None
    new_family = (profile or {}).get("family_id") if ok else None
    if not new_family or new_family == session.get("family_id"):
        return None
    session["family_id"] = new_family
    # Syncing recurring transactions happens once a day **per family**: the new
    # one hasn't been synced yet
    session.pop("recurring_synced", None)
    return ((profile.get("families") or {}).get("name")) or "המשפחה שלי"


@app.errorhandler(db.DataUnavailable)
def data_unavailable(e):
    """A failed query arrives here, and isn't shown as "you have no data".

    Ten fetchers returned `[]` on failure. A family with two years of history
    saw "no history", `₪11,000` of recurring expenses vanished from the month
    page, and whoever `get_family_members` failed for got "the selected family
    member isn't in your family" on every attempt to add a transaction: an
    error blaming them for our failure.

    A fake zero looks exactly like a real zero, and the whole app is numbers.
    So a screen saying "we couldn't load" beats a screen that silently lies."""
    logger.error("DataUnavailable: %s", e)
    # Before declaring a failure: maybe the user is already in another family
    # (see `_family_changed`)
    new_name = _family_changed()
    if new_name:
        if _is_api_request():
            return jsonify({"error": "המשפחה שלך השתנתה — רעננו את הדף",
                            "family_changed": True}), 409
        session["sf_notice"] = f'המשפחה שלך השתנתה — "{new_name}"'
        return redirect(url_for("dashboard"))
    msg = "לא הצלחנו לטעון את הנתונים כרגע. נסו לרענן בעוד רגע."
    if _is_api_request():
        return jsonify({"error": msg}), 503
    return render_template("error.html", code=503, message=msg), 503


@app.errorhandler(500)
def server_error(e):
    msg = "אירעה שגיאה בשרת. נסו שוב בעוד כמה רגעים."
    if _is_api_request():
        # Without this a real server failure reaches the user as a "network
        # error", and they retry again and again a request that will never
        # succeed, and we never hear about the failure.
        return jsonify({"error": msg}), 500
    return render_template("error.html", code=500, message=msg), 500


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _month_label(year: int, month: int) -> str:
    """The month name in Hebrew. A month out of range is clamped instead of being
    used as an index: `_HEBREW_MONTHS[13]` raised IndexError and crashed the
    page, and `_HEBREW_MONTHS[-5]` silently returned August, the worse of the two."""
    if not 1 <= month <= 12:
        month = clock.now().month
    return f"{_HEBREW_MONTHS[month]} {year}"


if __name__ == "__main__":
    debug = os.environ.get("FLASK_ENV") == "development"
    port  = int(os.environ.get("PORT", 8080))
    app.run(debug=debug, port=port)
