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

# ─── ניטור שגיאות ────────────────────────────────────────────────────────────
# בלי זה שגיאה שמשתמש נתקל בה מגיעה ל-error.html ושם נעלמת: ה-print ללוגים של
# Railway נמחק ואף אחד לא קורא אותו. עם משתמש אחד זה נסבל כי הוא מגלה לבד;
# עם מאה זה אומר שמשתמש מתוסכל עוזב ואין לנו מושג למה.
# פעיל רק כש-SENTRY_DSN מוגדר, כך שפיתוח מקומי ופריסות קיימות לא מושפעים.
# שמות משתנים ושדות שאסור שיעזבו את השרת. נבדק מול השם בפועל בקוד:
# password/current_password במסלולי האימות, access_token/refresh_token
# ב-session, image_bytes/b64 בסריקת הקבלה.
_SECRET_NAMES = (
    "password", "passwd", "secret", "token", "authorization", "apikey",
    "api_key", "image_bytes", "b64", "encrypted_password",
)


def _looks_secret(name: str) -> bool:
    return any(s in str(name).lower() for s in _SECRET_NAMES)


def _scrub_event(event, hint):
    """מנקה את האירוע לפני שהוא עוזב את השרת.

    האפליקציה מטפלת בנתונים פיננסיים, ומסלול סריקת הקבלה נושא תמונה של
    קבלה אמיתית — שום אחד מאלה לא אמור להגיע לשירות חיצוני.

    הניקוי הקודם נגע רק ב-event["request"], וזה לא היה מספיק: ה-SDK מצרף
    לכל frame ב-stacktrace גם את המשתנים המקומיים שהיו בזיכרון באותו רגע.
    בפועל זה אומר שחריגה כלשהי במסלול התחברות, הרשמה, איפוס סיסמה או
    מחיקת חשבון הייתה שולחת את הסיסמה עצמה; חריגה ב-inject_auth הייתה
    שולחת את טוקני Supabase; וחריגה בסריקת קבלה הייתה שולחת את התמונה.

    include_local_variables=False כבר מכבה את זה ב-SDK, והניקוי כאן הוא
    השכבה השנייה — בדיוק כמו שההערה המקורית התכוונה, אבל במקומות שבהם
    הנתונים באמת נמצאים: extra, breadcrumbs, contexts ו-query_string,
    שאף אחד מהם לא נגע בהם קודם."""
    request_data = event.get("request") or {}
    request_data.pop("data", None)
    request_data.pop("cookies", None)
    # ה-query string נושא ?code=<קוד הזמנה> ב-/api/family/preview
    request_data.pop("query_string", None)
    headers = request_data.get("headers") or {}
    for sensitive in ("Cookie", "Authorization"):
        headers.pop(sensitive, None)

    # משתנים מקומיים בכל frame — הנתיב שהיה פתוח לגמרי
    for value in (event.get("exception") or {}).get("values") or []:
        for frame in (value.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)

    # extra / contexts: ערכים שהקוד שולח במפורש, היום או בעתיד
    for section in ("extra", "contexts"):
        holder = event.get(section)
        if isinstance(holder, dict):
            for key in list(holder):
                if _looks_secret(key):
                    holder[key] = "[scrubbed]"

    # breadcrumbs: ה-SDK מתעד בהן בקשות ושאילתות שקדמו לשגיאה
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
        send_default_pii=False,   # בלי כתובות IP, עוגיות או גוף בקשה
        # ברירת המחדל היא True, והיא מה שצירף סיסמאות וטוקנים לכל דיווח
        include_local_variables=False,
        traces_sample_rate=0.0,   # שגיאות בלבד — מדידות ביצועים עולות כסף ולא נחוצות כאן
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

# מפתח החתימה של ה-sessions חייב להגיע מהסביבה — בלי fallback, אחרת עוגיות
# ניתנות לזיוף עם מפתח ציבורי ידוע. נכשלים בהפעלה במקום להמשיך בשקט.
_secret = os.environ.get("SECRET_KEY")
if not _secret:
    raise RuntimeError("SECRET_KEY environment variable is required — refusing to start without it")
app.secret_key = _secret

# נשארים מחוברים עד יציאה יזומה. העוגייה חיה 90 יום, ו-
# SESSION_REFRESH_EACH_REQUEST (ברירת המחדל של Flask, מפורש כאן) דוחף את
# תאריך התפוגה קדימה בכל בקשה, כך שמשתמש פעיל לעולם לא מגיע אליו.
# מה שמאפשר את זה בפועל הוא רענון ה-refresh token ב-inject_auth: טוקן הגישה
# של Supabase חי כשעה, וההתחברות שורדת כי הוא מוחלף מעצמו.
#
# המספר חל בפועל רק על סשן **נטוש** — מכשיר שנמכר, טלפון שאבד, או דפדפן
# במחשב משותף. פעם זה היה עשר שנים, כלומר שאלה לא נסגרים לעולם.
app.permanent_session_lifetime = timedelta(days=90)

# הקשחת עוגיות: העוגייה נושאת את טוקני Supabase, אז Secure חובה בפרודקשן
# (בפיתוח מקומי על http זה היה שובר את ההתחברות — לכן מותנה).
_IS_DEV = os.environ.get("FLASK_ENV") == "development"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",   # גם הגנת CSRF בסיסית
    SESSION_COOKIE_SECURE=not _IS_DEV,
    SESSION_REFRESH_EACH_REQUEST=True,   # חלון התפוגה נע קדימה עם כל שימוש
    PREFERRED_URL_SCHEME="https" if not _IS_DEV else "http",
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,  # תקרת גודל בקשה — מגן על העלאת קבלות
)

# מאחורי ה-proxy של Railway (TLS termination) — כדי ש-request.host_url יחזיר
# https בקישורי איפוס-סיסמה. לא מזיק בפיתוח מקומי.
#
# x_for=1 חיוני ל-rate limiting ולא רק לנוחות: בלעדיו remote_addr הוא צומת
# הקצה של Railway ולא המשתמש. נמדד בפועל — 12 בקשות ממחשב אחד התפצלו על
# ארבע כתובות proxy שונות, כך שהמגבלה לא נאכפה. גרוע מכך, כל המשתמשים
# חולקים את אותן כתובות, ולכן משתמש אחד רועש היה יכול לנעול את ההתחברות
# לכולם.
#
# הערך 2 נמדד ולא נוחש: Railway מוסיף שתי שכבות, ו-X-Forwarded-For מגיע
# כ-"<לקוח>, <צומת קצה>". ProxyFix סופר מימין, כך ש-1 החזיר את צומת
# הקצה ו-2 מחזיר את הלקוח. הספירה מימין היא גם מה שמונע זיוף: ערך
# ש-הלקוח דוחף בעצמו נדחק שמאלה ולעולם לא נבחר.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=2, x_proto=1, x_host=1)


# ראשון מכל ‎before_request‎: פניית חימום לא תרוץ במקביל לבקשה (‎db.request_started‎)
@app.before_request
def _db_request_started():
    db.request_started()


@app.teardown_request
def _db_request_finished(exc):
    db.request_finished()

# הגבלת קצב על מסלולי האימות — מונע ניחוש סיסמאות וסריקת מספרי טלפון.
#
# האחסון חייב להיות משותף בפרודקשן. עם "memory://" כל worker של gunicorn
# סופר לעצמו, כך שהמגבלה מוכפלת במספר ה-workers, וכל פריסה מאפסת את המונים —
# כלומר ההגנה על התחברות, הרשמה ואיפוס סיסמה רופפת בהרבה ממה שכתוב בקוד.
# זה היה מספיק כשזו הייתה אפליקציה משפחתית; ברגע שהכתובת מופצת זה לא.
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

_RATELIMIT_STORAGE = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")

limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=_RATELIMIT_STORAGE,
    default_limits=[],  # רק המסלולים שמסומנים במפורש מוגבלים
    # אם האחסון המשותף לא זמין — נופלים למונים מקומיים במקום להחזיר שגיאה.
    # תקלה אצל ספק ה-Redis לא אמורה לנעול לאנשים את ההתחברות לאפליקציה:
    # הגבלה רופפת לכמה דקות עדיפה בהרבה על אתר שאי אפשר להיכנס אליו.
    in_memory_fallback_enabled=True,
)

# אם נשארנו על זיכרון מקומי בפרודקשן — אומרים את זה בקול. הכישלון כאן שקט
# מטבעו: הבקשות ממשיכות לעבוד והמגבלה פשוט לא נאכפת, כך שבלי ההתראה הזאת
# אין שום דרך להבחין בין "מוגן" ל"נראה מוגן".
#
# ‎error‎ ולא ‎warning‎: ל-Sentry, ‎warning‎ הוא פירור ולא אירוע, והלוג של
# Railway הוא חוצץ שנמחק ואף אחד לא קורא (ראו backend/logs.py). כלומר
# ההתראה היחידה על כך שכל ההגבלות באפליקציה נחלשו פי 4 ומתאפסות בכל
# פריסה — לא הגיעה לשום מקום שמישהו מסתכל בו.
RATELIMIT_IS_SHARED = not _RATELIMIT_STORAGE.startswith("memory://")

if not RATELIMIT_IS_SHARED and not _IS_DEV:
    logger.error("rate limiting is using in-memory storage in production — "
                 "limits are per-worker and reset on every deploy. "
                 "Set RATELIMIT_STORAGE_URI.")


# ─── נכסים סטטיים: חתימה לפי תוכן ────────────────────────────────────────────
#
# כל קובץ סטטי נשלח עם "no-cache", כלומר הדפדפן חייב לשאול את השרת בכל
# טעינת עמוד אם העותק שלו עדיין תקף. שש שאלות כאלה בכל עמוד, כולן נענות
# "לא השתנה", וכל אחת תופסת אחד משני ה-workers. בפועל שש מתוך שבע הפניות
# בטעינת עמוד רגילה הן שיחה על כלום.
#
# הסיבה היא שלכתובת אין גרסה: /static/css/style.css היא אותה כתובת בין אם
# זה הקובץ של אתמול או של היום, אז לדפדפן אין דרך אחרת לדעת.
#
# הפתרון: ‎?v=<חתימה>‎ שנגזרת מתוכן הקובץ, ותוקף של שנה. הדפדפן לא שואל,
# וכשקובץ משתנה החתימה משתנה איתו — כתובת חדשה, הורדה מחדש, אוטומטית.
#
# החתימה נגזרת מהתוכן ולא ממספר ידני, וזה העיקר: מספר שצריך לזכור לעדכן
# הוא מספר שיישכח, והתוצאה היא דפדפן שמגיש קובץ ישן במשך שנה בלי שום דרך
# לתקן מרחוק. כאן אי אפשר לשכוח.
_ASSET_HASHES: dict = {}


def _asset_version(filename: str) -> str:
    """שמונה תווים מתוך חתימת התוכן של הקובץ. ריק אם הוא לא נמצא."""
    if filename in _ASSET_HASHES:
        return _ASSET_HASHES[filename]

    path = os.path.join(app.static_folder, filename)
    try:
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:8]
    except OSError:
        # קובץ שלא נמצא: לא מפילים את העמוד בגלל נכס חסר. הוא ייכשל
        # בטעינה כמו קודם, וזה מצב שקל לראות.
        digest = ""
    if not _IS_DEV:
        _ASSET_HASHES[filename] = digest
    return digest


# סכום כסף בתבניות: ‎{{ x | money }}‎ — אגורות רק כשיש (ראו backend/money.py)
app.jinja_env.filters["money"] = format_money
app.jinja_env.globals["ui_icon"] = ui_icon
app.jinja_env.globals["empty_illus"] = empty_illus
app.jinja_env.globals["greeting"] = lambda: greeting(clock.now().hour)
app.jinja_env.filters["count_of"] = count_of
# "היום" / "אתמול" / "ראשון, 27.9" — מול היום של השעון המוזרק (בדיקות)
app.jinja_env.filters["day_label"] = lambda d: day_label(d, clock.today())


@app.context_processor
def _versioned_static():
    """דורס את url_for עבור נכסים סטטיים בלבד.

    דריסה ולא פונקציה חדשה, כדי ש-48 הקריאות הקיימות בתבניות לא ישתנו —
    ובעיקר כדי שקריאה שתיכתב מחר תקבל את זה בלי שאיש יזכור."""
    def versioned_url_for(endpoint, **values):
        if endpoint == "static" and "filename" in values and "v" not in values:
            version = _asset_version(values["filename"])
            if version:
                values["v"] = version
        return url_for(endpoint, **values)
    return {"url_for": versioned_url_for}


@app.after_request
def _cache_static_forever(response):
    """שנה של מטמון, אבל רק לכתובת חתומה.

    בלי החתימה זה היה מסוכן: כתובת בלי גרסה שנשמרת לשנה היא קובץ ישן
    שאין דרך לרענן. עם חתימה, שינוי בקובץ מייצר כתובת אחרת ממילא."""
    if request.endpoint == "static" and request.args.get("v"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


_GZIP_TYPES = ("text/html", "text/css", "text/plain", "application/javascript",
               "text/javascript", "application/json", "image/svg+xml",
               "application/manifest+json")
_GZIP_MIN = 1024
_gzip_static = {}   # (נתיב, ETag) → בייטים דחוסים; קבצים סטטיים נדחסים פעם אחת לתהליך


@app.after_request
def _gzip(response):
    """דחיסה לכל מה שנשלח לטלפון (מתן, 13.10 — "הכי מהירה שאפשר").

    עמוד ההגדרות היה ‎117KB‎ וכך ‎13KB‎; ‎style.css‎ — ‎200KB‎ ו-‎45KB‎.
    Railway לא דוחס בעצמו. קבצים סטטיים נדחסים פעם אחת ונשמרים בזיכרון;
    עמודים — בכל בקשה (רמה 6: כמה מילישניות, מול עשרות KB פחות ברשת)."""
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
    """אותו סימון בכל דרך — גם כשהבייטים הדחוסים באו מהזיכרון (בדיקת קוד,
    14.10: שם ה-ETag נשאר "חזק" ודחוס ולא דחוס נשאו את אותו מזהה)."""
    response.headers["Content-Encoding"] = "gzip"
    # ETag של המקור כבר לא מתאר את הבייטים — "חלש" כדי שבדיקת 304 תמשיך לעבוד
    etag = response.headers.get("ETag")
    if etag and not etag.startswith("W/"):
        response.headers["ETag"] = "W/" + etag
    # טווח בייטים של הקובץ הלא דחוס לא מתאים לגרסה הדחוסה
    response.headers.pop("Accept-Ranges", None)
    return response


# ─── Auth helpers ─────────────────────────────────────────────────────────────

def _do_not_rewrite_session_cookie():
    """מסמן שהתגובה הזאת לא תכתוב מחדש את עוגיית ה-session.

    זה חשוב בדיוק כשהרענון נכשל. שני ה-workers של gunicorn מקבלים בקשות
    במקביל, וה-refresh token של Supabase מסובב בכל רענון — כך שבקשה אחת
    יכולה לרענן בהצלחה ולכתוב טוקן חדש לעוגייה, בעוד בקשה מקבילה, שיצאה
    עם הטוקן הישן ונתקעה על רשת סלולרית איטית, מגיעה אחריה ונכשלת. אם
    הבקשה הכושלת תכתוב את העוגייה שלה, היא תדרוס את הטוקן התקין בטוקן
    מת — והמשתמש ינותק בביקור הבא בלי שעשה דבר.

    הביטול עצמו נעשה ב-after_request ולא כאן, וזה לא עניין של סגנון:
    Flask כותב את העוגייה כש-session מסומן permanent או modified, וכל
    כתיבה ל-session בתוך ה-view (למשל recurring_synced בדשבורד) הייתה
    מחזירה את modified לדלוק. גרוע מכך, ביטול permanent מוקדם היה הופך
    את העוגייה שתיכתב לעוגיית-דפדפן שנמחקת בסגירה — כלומר בדיוק הניתוק
    שאנחנו מונעים. ב-after_request כבר לא ירוץ קוד של view."""
    g.sf_skip_session_cookie = True


@app.after_request
def _honour_session_cookie_freeze(response):
    """מקפיא את כתיבת העוגייה — אלא אם ה-session רוקן במכוון.

    התנאי על user_id הוא לא פרט טכני. ההקפאה קיימת כדי לא לדרוס טוקן
    תקין בטוקן מת כשרענון נכשל, וזה רלוונטי רק כל עוד יש התחברות. אבל
    ‎session.clear()‎ — ביציאה יזומה, במחיקת חשבון, ובניתוק אחרי טוקן
    שנדחה — הוא בדיוק הרגע שבו העוגייה **חייבת** להיכתב, כדי שתימחק.

    בלי התנאי הזה מי שלחץ "התנתק" בדיוק כשהרענון נכשל קיבל הודעה שיצא,
    את עוגיות המכשיר נמחקות — ונשאר מחובר לגמרי, כי עוגיית ה-session
    שלו מעולם לא נגעה. הבקשה הבאה הייתה מחזירה אותו לאפליקציה."""
    if g.get("sf_skip_session_cookie") and session.get("user_id"):
        # שני אלה יחד הם מה ש-should_set_cookie של Flask בודק. הערכים
        # שבעוגייה אצל הדפדפן נשארים כפי שהם.
        session.permanent = False
        session.modified  = False
    return response


def _end_session(reason: str):
    """מסיים את ההתחברות ומשאיר עקבות.

    ניתוק היה עד עכשיו שקט לגמרי, ולכן משתמש שהתלונן שהוא "נזרק החוצה"
    לא הותיר שום דבר לחקור אותו. ההבטחה היא להישאר מחובר עד יציאה יזומה,
    אז כל ניתוק אחר הוא אירוע שצריך להיות אפשר לראות."""
    # warning ולא info: ההבטחה היא להישאר מחובר עד יציאה יזומה, אז כל
    # ניתוק אחר הוא אירוע שצריך להיות אפשר לראות
    logger.warning("session ended: %s", reason)
    if _sentry_dsn:
        sentry_sdk.capture_message(f"session ended: {reason}", level="warning")
    session.clear()


@app.before_request
def inject_auth():
    token = session.get("access_token")
    if not token:
        # מאפסים במפורש, ולא יוצאים בשקט. הלקוח הוא סינגלטון ברמת
        # התהליך: בלי האיפוס, בקשה אנונימית ממשיכה לשאת את ה-JWT של
        # המשתמש הקודם שטופל באותו worker.
        db.set_auth_token("")
        return

    # Supabase JWTs expire after ~1 hour; refresh ahead of expiry so a
    # long-lived Flask session keeps working without re-login.
    expires_at = session.get("token_expires_at") or 0
    if session.get("refresh_token") and time.time() > expires_at - 120:
        sid = session.get("sid")
        # התשובה עם הטוקן הקודם אולי לא הגיעה לטלפון (האפליקציה ירדה לרקע
        # באמצע) — אז העוגייה ישנה, והטוקן העדכני מחכה במסד (מתן, 13.10)
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
            session.permanent = True     # כל שימוש מאריך את חלון העוגייה
            if sid:
                db.web_session_rotate(sid, old_refresh, token, response.session.refresh_token,
                                      response.session.expires_at)
        elif fatal and sid and _adopt_newer_tokens(sid):
            # בקשה מקבילה (worker אחר) רעננה רגע לפנינו ושמרה במסד
            token = session["access_token"]
        elif fatal and time.time() >= expires_at:
            # הטוקן נדחה וגם טוקן הגישה כבר פג — ההתחברות מתה באמת ואין
            # מה לשחזר. רק כאן מנתקים.
            _end_session(f"refresh token rejected: {err}")
            return
        else:
            # לא מנתקים. שתי סיבות שונות מגיעות לכאן, ושתיהן בנות-שחזור:
            # תקלה זמנית (רשת, 5xx), או דחייה בזמן שטוקן הגישה עדיין בתוקף —
            # שזה בדיוק מה שקורה כשבקשה מקבילה כבר סובבה את הטוקן לפנינו.
            # הבקשה הזאת ממשיכה עם טוקן הגישה הקיים, והבאה תנסו שוב; אם
            # הדחייה אמיתית, הניתוק יקרה מעצמו כשטוקן הגישה יפוג.
            logger.warning("token refresh failed, keeping session: %s", err)
            _do_not_rewrite_session_cookie()

    db.set_auth_token(token)


def _adopt_newer_tokens(sid) -> bool:
    """מחליף את הטוקנים שבעוגייה בעדכניים מהמסד, אם שם יש חדשים יותר.

    "חדשים יותר" = refresh token אחר מזה שבעוגייה: רק רענון מצליח משנה
    אותו במסד, אז אחר פירושו שהרענון קרה בבקשה שהתשובה שלה לא הגיעה."""
    newer = db.web_session_get(sid)
    if not newer or not newer.get("refresh_token") or newer["refresh_token"] == session.get("refresh_token"):
        return False
    logger.info("session tokens recovered from web_sessions")
    session["access_token"]     = newer["access_token"]
    session["refresh_token"]    = newer["refresh_token"]
    session["token_expires_at"] = newer["expires_at"]
    session.permanent = True
    return True


# ─── זיכרון המכשיר ────────────────────────────────────────────────────────────
#
# דף הנחיתה הוא דף שיווק: הוא נועד למי שלא מכיר את SmartFin. מי שכבר התחבר
# מהמכשיר הזה פעם אחת לא צריך לראות אותו שוב — גם לא כשה-session נגמר או
# כשיצא ביוזמתו. שתי העוגיות כאן הן הזיכרון היחיד ששורד סיום session, ולכן
# הן נפרדות מעוגיית ה-session ומכילות רק את המינימום:
#
#   sf_returning — "כבר התחברת מכאן". קובע לאן הולך השורש.
#   sf_last_id   — המזהה שאיתו התחבר (אימייל או טלפון), כדי למלא מראש את
#                  הטופס. נמחקת ביציאה יזומה: מי שיצא במכוון ביקש שיפסיקו
#                  לזכור אותו, וגם ייתכן שהוא מפנה את המכשיר לבן משפחה אחר.
#
# שתיהן HttpOnly — אין להן שום שימוש ב-JS, וכך גם XSS לא יכול לקרוא את
# האימייל. אף החלטת הרשאה לא נשענת עליהן: ערך מזויף בהן משנה לכל היותר
# איזה דף מוגש למי שלא מחובר, ומה מודפס בשדה טקסט. הסיסמה עדיין נדרשת.
_DEVICE_COOKIE   = "sf_returning"
_LAST_ID_COOKIE  = "sf_last_id"
_DEVICE_COOKIE_MAX_AGE = 10 * 365 * 24 * 60 * 60   # עשר שנים — רק "היה כאן פעם", לא התחברות


def _remember_device(response, identifier: str = ""):
    """מסמן את המכשיר כמוכר, ואם נמסר מזהה — זוכר גם אותו."""
    common = dict(max_age=_DEVICE_COOKIE_MAX_AGE, httponly=True,
                  samesite="Lax", secure=not _IS_DEV)
    response.set_cookie(_DEVICE_COOKIE, "1", **common)
    identifier = (identifier or "").strip()
    if identifier:
        # תקרה וניקוי תווי בקרה — הערך חוזר לדפדפן ונכנס לשדה טופס, ואין
        # שום סיבה שיהיה ארוך או מוזר. Jinja כבר עושה escaping בתבנית.
        safe = re.sub(r"[\x00-\x1f\x7f]", "", identifier)[:120]
        response.set_cookie(_LAST_ID_COOKIE, safe, **common)
    return response


def _remembered_identifier() -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", request.cookies.get(_LAST_ID_COOKIE, ""))[:120]


def _device_is_known() -> bool:
    return request.cookies.get(_DEVICE_COOKIE) == "1"


def _is_api_request() -> bool:
    """האם הבקשה מצפה ל-JSON ולא לעמוד HTML.

    ההפרדה הזאת היא מה שמבדיל בין "המשתמש ניווט לכתובת" לבין "הקוד בדף
    קרא לשרת". fetch עוקב אחרי הפניות בשקט, כך שדף HTML שמוחזר לקריאת
    API חוזר כתשובה תקינה עם קוד 200 — והלקוח מנסה לפרסר אותו כ-JSON
    ונכשל. המשתמש רואה "שגיאת רשת" שלא תיפסק לעולם."""
    return request.path.startswith("/api/")


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            # קריאת API מקבלת 401 שהלקוח יודע לזהות; ניווט רגיל מקבל
            # הפניה לטופס ההתחברות כמו תמיד.
            if _is_api_request():
                return jsonify({"error": "ההתחברות הסתיימה — יש להתחבר מחדש"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# הנתיב ש-‎db.upload_receipt‎ יוצר: ‎<family_id>/<uuid>.<סיומת>‎
_RECEIPT_NAME_RE = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\.[a-z0-9]{2,5}$")


def _receipt_path_ok(path, family_id) -> bool:
    """נתיב הקבלה מגיע מהלקוח. האחסון כבר חוסם תיקייה של משפחה אחרת, אבל
    בתוך המשפחה אפשר היה להצמיד לעסקה קבלה של עסקה אחרת — ואז מחיקת
    אחת מחקה את הקובץ של שתיהן. מקבלים רק את מה שהאפליקציה עצמה יוצרת."""
    if not isinstance(path, str) or not family_id:
        return False
    folder, _, name = path.partition("/")
    return folder == str(family_id) and bool(_RECEIPT_NAME_RE.match(name))


def _delete_unused_receipts(family_id, paths):
    """מוחקת קבצי קבלה — חוץ ממה שעסקה אחרת עדיין מצביעה עליו. נקראת
    אחרי מחיקת העסקאות. כשלא ידוע מה בשימוש, לא מוחקים: קובץ מיותר
    באחסון עדיף על קבלה שבורה של מקרר ביום שצריך את האחריות."""
    paths = [p for p in (paths or []) if p]
    if not paths:
        return
    try:
        in_use = db.receipts_in_use(family_id, paths)
    except db.DataUnavailable:
        logger.error("receipts_in_use נכשל — הקבצים נשארים באחסון")
        return
    db.delete_receipts(session.get("access_token"), [p for p in paths if p not in in_use])


class BadInput(ValueError):
    """ערך מהלקוח שאין טעם לנסות לפרש — ראו ‎_text‎."""


def _text(value) -> str:
    """שדה טקסט מגוף בקשת JSON: מחרוזת נקייה מרווחים, או "" כשהוא חסר.

    כל ערך אחר — מספר, רשימה, אובייקט, בוליאני — הוא ‎BadInput‎, שעונה
    422 "ערך לא תקין". קודם ‎(body.get("x") or "").strip()‎ נפל על מספר
    ב-500, שנרשם אצלנו כתקלה אמיתית. הטפסים תמיד שולחים טקסט; זה
    לבקשות שנשלחות ידנית."""
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
    """העדפות המשפחה המחוברת — נשלף פעם אחת לבקשה (cache על flask.g)."""
    if "family_settings" not in g:
        fid = session.get("family_id")
        g.family_settings = db.get_family_settings(fid) if fid else dict(db.DEFAULT_FAMILY_SETTINGS)
    return g.family_settings


@app.context_processor
def inject_family_settings():
    """family_settings זמין בכל תבנית (התגיות, המודאל וקבוצת ההעדפות תלויים בו).

    זה רץ לפני **כל** render_template, כולל error.html. לכן כאן, ורק כאן,
    כישלון שליפה לא מתפשט: אחרת תקלה במסד הייתה מפילה גם את דף השגיאה
    שנועד לדווח עליה, והמשתמש היה מקבל מסך ריק לגמרי. התבניות כבר יודעות
    להתמודד עם None — זה המצב של מי שלא מחובר.

    זו הקלה בתצוגה בלבד. החלטות שנשענות על ההעדפות — למשל שיוך עסקה לבן
    משפחה ב-_resolve_owner — קוראות ל-family_settings() ישירות וממשיכות
    להיכשל בגלוי, כי שם ערך שגוי נכתב למסד."""
    if "user_id" not in session:
        return {"family_settings": None, "push_key": None}
    try:
        settings = family_settings()
    except db.DataUnavailable:
        return {"family_settings": None, "push_key": push.public_key()}
    # מתי החיוב הקבוע הבא — עותק בסשן, כדי שהבדיקה בתחילת העמוד הבא לא
    # תצטרך לשלוף (‎_sync_recurring‎). משותף למשפחה: כל ריצה של המנוע מעדכנת.
    nxt = (settings or {}).get("recurring_next")
    if nxt and session.get("recurring_next") != nxt:
        session["recurring_next"] = nxt
    # משפחה של אדם אחד (מתן, 5.10): "של מי" אומר כלום — לא התגית ליד כל
    # עסקה, לא הגרף "לפי בן משפחה" ולא השאלה בחלון. רק בתצוגה: ההעדפה
    # עצמה לא משתנה, וכשמצטרף מישהו הכל חוזר. עצל: נשלף רק בעמוד שבאמת
    # מציג "של מי" — לא בדף שגיאה, למשל.
    single = _LazySingleMember()
    # המפתח הציבורי להתראות — בכל עמוד, כי ההרשמה האוטומטית רצה מכל עמוד (‎js/push.js‎)
    return {"family_settings": settings, "single_member": single, "show_owner": _ShowOwner(settings, single),
            "push_key": push.public_key()}


@app.context_processor
def _client_lists():
    """הקטגוריות ובני המשפחה לחלון "הוספת עסקה", בתוך העמוד (מתן, 13.10).

    עד עכשיו כל עמוד שלח אחריו עוד שתי בקשות (‎/api/categories‎,
    ‎/api/family/members‎) רק כדי לחמם את החלון — ובלוגים רבות מהן נקטעו
    באמצע כשעברו לעמוד הבא. רוב העמודים כבר שלפו בדיוק את זה, אז לוקחים
    ממטמון הבקשה בלבד: מה שלא נשלף — לא נשלף בשבילנו, והחלון יבקש כרגיל."""
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
    """‎single_member()‎ בתבנית — האם במשפחה אדם אחד. בספק (שליפה נכשלה) — לא."""
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
    """‎show_owner.get(type)‎ — להציג "של מי" לסוג הזה: השיוך פעיל, ויש יותר מאדם אחד."""
    def __init__(self, settings, single):
        self._attr = (settings or {}).get("owner_attribution") or {}
        self._single = single

    def get(self, tx_type, default=False):
        return bool(self._attr.get(tx_type)) and not self._single()


def assign_member_colors(members: list, chosen: dict) -> dict:
    """מזהה ← מספר צבע (‎_OWNER_HEX‎ ו-‎.owner-N‎).

    מי שבחר צבע (מתן, 30.9 — סבב 6, פריט 13) מקבל אותו. כל השאר — לפי סדר
    ההצטרפות, כמו תמיד, אבל מדלגים על צבע שמישהו כבר בחר: שני אנשים באותו
    צבע, ואי אפשר להבדיל ביניהם. בחירה של מי שכבר לא במשפחה, או ערך שבור,
    פשוט לא נחשבת."""
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
    """צבע לכל בן משפחה — לתגי השם על עסקאות ולגרפים לפי בן משפחה."""
    if not family_id:
        return {}
    members = db.get_family_members(family_id)
    return assign_member_colors(members, (family_settings() or {}).get("member_colors") or {})


def _run_queries(tasks: dict) -> dict:
    """מריץ קבוצת שליפות DB ומחזיר {name: result}.
    היה בעבר מקבילי (ThreadPoolExecutor) אבל הלקוח של Supabase הוא singleton
    עם httpx client משותף — קריאות מקביליות מ-threads על אותו client גרמו ל-
    "Server disconnected" תחת gunicorn (השליפה נכשלה בשקט והחזירה אפסים). מאז
    שהעברנו את השרת ל-EU (אזור europe-west4) כל שליפה עולה ~30ms, אז הרצה
    רצופה מהירה לגמרי (~7 שליפות = ~0.2ש') ובטוחה. רץ ב-thread הראשי, אז גם
    flask.g/_request_cache עובד כרגיל."""
    return {name: _timed(name, fn) for name, fn in tasks.items()}


CHART_COLORS = 12   # ‎COLORS‎ ב-‎chart-setup.js‎ — חייב להישאר מסונכרן


def _distinct_colors(items):
    """אין שתי קטגוריות באותו צבע בעיגול של החודש (מתן, 12.10).

    כל אחת מבקשת את הצבע הקבוע שלה (‎color_idx‎ — המקום ברשימת הקטגוריות).
    הגדולה קודם מקבלת את מה שביקשה; מי שהצבע שלה כבר תפוס — הצבע הפנוי
    הבא. כך "סופר" נשאר זהב בכל החודשים, והקטגוריה התשיעית לא יוצאת זהב
    גם היא. רק כשיש יותר קטגוריות מצבעים בחודש אחד — צבע חוזר."""
    taken = set()
    for c in sorted(items, key=lambda c: -(c.get("total") or 0)):
        want = (c.get("color_idx") or 0) % CHART_COLORS
        if len(taken) < CHART_COLORS:
            while want in taken:
                want = (want + 1) % CHART_COLORS
        taken.add(want)
        c["color_idx"] = want


def _timed(name, fn):
    """מריץ ‎fn‎ ורושם כמה זמן לקח — לשורת "timing" שבסוף הבקשה.

    מהירות הטעינה (מתן, 12.10): קודם למדוד איזו שליפה איטית, ורק אז לתקן."""
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
    """שורה אחת לבקשה שנמדדה: סך הכל, וכל שליפה לפי הסדר."""
    timings = g.get("sf_timings")
    if timings and g.get("sf_request_start") is not None:
        total = (time.perf_counter() - g.sf_request_start) * 1000
        logger.info("timing %s total=%.0fms %s", request.path, total,
                    " ".join(f"{name}={ms:.0f}" for name, ms in timings))
    return response


def _prime_request_cache(family_id, settings=None, categories=None, members=None, family=None):
    """מזריק ל-cache של הבקשה (flask.g) ערכים שכבר נשלפו במקבץ, כדי
    ש-context-processors ו-_member_colors ישתמשו בהם במקום לשלוף שוב באותה
    בקשה. המפתחות חייבים להיות זהים לאלה שב-_request_cache ב-supabase_config."""
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


# צבעי-מילוי מוצקים לעמודות גרף "לפי בן משפחה" — זהים לצבעי תגי-השם
# (.owner-0..5 ב-style.css, חייב להישאר מסונכרן; מספר הצבעים כאן קובע כמה
# בני משפחה מקבלים צבע ייחודי לפני שהמערכת חוזרת מהתחלה). משותפת (ללא
# user_id) מקבלת אפור כמו .owner-shared. צבעים לא-שגרתיים שלא מתנגשים
# עם הסמנטיים (ירוק=הכנסה, אדום=הוצאה, זהב=חיסכון).
_OWNER_HEX  = {
    0: "#2E67A8",  # כחול (מתן)
    1: "#7048B0",  # סגול (אור)
    2: "#A0457C",  # שזיף-מג'נטה
    3: "#A85C3E",  # טרקוטה
    4: "#5E7391",  # כחול-אפור צפחה
    5: "#8A6A52",  # חום-טאופ
    # שניים נוספים — שמונה לבחירה (מתן, 30.9 — סבב 6, פריט 13)
    6: "#4B4FA8",  # אינדיגו
    7: "#2F7385",  # כחול-פטרול
}
_SHARED_HEX = "#78716C"
_EXTRA_INCOME_HEX = "#B8B0A2"   # "הכנסות נוספות" — אפור בהיר, שונה ממשכורת משותפת
# יעדי חיסכון — גוונים של זהב, צבע החיסכון באפליקציה. הפלטה של ההוצאות נתנה
# ליעד השני ירוק, וירוק כאן פירושו הכנסה.
_SAVINGS_HEX = ("#A67C00", "#D6AE34", "#6E5200", "#E8CF7A", "#8C6A12", "#C4A45A")


# ─── Auth routes ──────────────────────────────────────────────────────────────

# ─── בדיקת כתובת מייל ─────────────────────────────────────────────────────────
#
# לא RFC מלא — בדיוק מה שמונע את הטעות שקרתה בפועל: כתובת בלי סיומת.
# לשני הטפסים יש ‎novalidate‎ (בכוונה, כדי שהשגיאות יהיו בעברית ובעיצוב
# שלנו ולא בועית דפדפן באנגלית), ובשרת לא נבדק כלום — אז ‎israel@gmial‎
# או ‎israel@gmail‎ נרשמו בהצלחה.
#
# למה זה חמור יותר משנשמע: המשתמש לא מגלה כלום ברגע ההרשמה. הוא מגלה
# חודש אחר כך, כשהוא מנסה לאפס סיסמה והקישור נשלח לכתובת שלא קיימת.
# אין לו מוצא, ואין שום מסלול באפליקציה לתקן כתובת. במסד יש כבר חשבון
# כזה מיולי.
#
# 254 תווים הוא האורך המרבי של כתובת מייל לפי התקן.
_EMAIL_RE = re.compile(r"^[^@\s]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")


def _looks_like_email(value: str) -> bool:
    value = (value or "").strip()
    return len(value) <= 254 and bool(_EMAIL_RE.match(value))


def _login_error(err: str) -> str:
    """ממפה כשל התחברות להודעה שאומרת מה קרה.

    כל כשל הוצג כ"מייל/טלפון או סיסמה שגויים" — גם כשהסיסמה נכונה
    לגמרי. ‎db.sign_in‎ תופסת כל חריגה ומחזירה את הטקסט שלה, אז הגבלת
    קצב של Supabase, מייל שלא אומת ותקלת חיבור כולם נראו זהים.

    המקרה שהופך את זה מאי-נוחות למלכודת: אם אימות מייל יופעל אי-פעם
    בפרויקט, **כל משתמש חדש ננעל לצמיתות** — הוא נרשם, מקבל "כעת ניתן
    להתחבר", ומקבל "סיסמה שגויה" לנצח. גם "שכחתי סיסמה" לא יעזור, כי
    הסיסמה מעולם לא הייתה הבעיה.

    צד ההרשמה ממפה חמש שגיאות שונות בקפידה; כאן היה אפס."""
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
    """השדות שהוקלדו בטופס ההרשמה, כדי להחזיר אותם עם השגיאה.
    בלי הסיסמאות — אין סיבה שהן ישבו ב-HTML של תשובה."""
    return {
        "first_name":  request.form.get("first_name", "").strip(),
        "last_name":   request.form.get("last_name", "").strip(),
        "email":       request.form.get("email", "").strip(),
        "phone":       request.form.get("phone", "").strip(),
        # או מקישור ההזמנה (‎/join/<code>‎ ← ‎/signup?invite=…‎, רעיון 43)
        "invite_code": request.form.get("invite_code", "").strip() or _clean_invite(request.args.get("invite")),
    }


def _clean_invite(raw) -> str:
    """קוד הזמנה מכתובת: אותיות וספרות בלבד, עד 12, באותיות גדולות. הוא נכנס
    ל-HTML (‎value=‎) ולכתובות — שום דבר אחר לא עובר."""
    return re.sub(r"[^A-Za-z0-9]", "", raw or "")[:12].upper()


def _normalize_phone(raw: str) -> str:
    """מנרמל מספר טלפון להשוואה/שמירה עקבית: ספרות בלבד, בצורה המקומית.

    קידומת ‎+972‎ מומרת ל-‎0‎. בלי זה אותו מספר בדיוק נשמר בשתי צורות:
    מי שנרשם עם ‎+972-54-1234567‎ נשמר כ-‎972541234567‎, ואז ההתחברות
    שלו עם ‎054-1234567‎ לא מוצאת אותו לעולם — ושני אנשים יכלו "לתפוס"
    את אותו מספר, כל אחד בכתיב אחר.
    """
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("972"):
        digits = "0" + digits[3:]
    return digits


# מספר טלפון ישראלי: נייד (‎05X‎ ועוד שבע ספרות), קווי (‎0X‎ ועוד שבע),
# או ‎07X‎ ועוד שבע. הבדיקה קיימת מאותה סיבה כמו זו של המייל — הטפסים
# הם ‎novalidate‎ ובשרת לא נבדק דבר, אז ‎1‎ היה מספר תקין: הוא נשמר, הפך
# למזהה התחברות חלופי חסר משמעות, ובגלל האינדקס הייחודי על הטלפון הוא
# גם חסם את הערך הזה לכל שאר המשתמשים לתמיד.
_PHONE_RE = re.compile(r"^0(5\d|7\d|[2-4689])\d{7}$")


def _looks_like_phone(normalized: str) -> bool:
    return bool(_PHONE_RE.match(normalized or ""))


def _begin_session(user_id, email, access_token, refresh_token, expires_at):
    """פותח את הסשן של Flask אחרי התחברות. מחזיר
    הודעת שגיאה, או ‎None‎ כשהכול הצליח."""
    # Set JWT before querying profiles (RLS requires auth.uid())
    db.set_auth_token(access_token)
    db.log_login_event()  # תיעוד כניסה פנימי (לבעל האתר)
    profile, profile_ok = db.fetch_profile(user_id)

    if not profile_ok:
        # הסיסמה נכונה, אבל הפרופיל לא נקרא. לא נכנסים: המשך מכאן היה
        # מתחיל session בלי family_id, ומשם ensure_family היה יוצר
        # משפחה חדשה ודורס את השיוך הקיים — ניתוק לצמיתות מכל
        # ההיסטוריה בגלל תקלה של שתי שניות. כישלון שאפשר לנסות שוב
        # אחריו הוא התוצאה הנכונה.
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
        # גם היצירה נכשלה. session בלי משפחה הוא אפליקציה שאי אפשר
        # לעשות בה כלום, ובניסיון הבא הוא ינסה ליצור משפחה שוב —
        # אז עדיף לא להיכנס מאשר להיכנס למצב תקוע.
        session.clear()
        return "לא הצלחנו לטעון את הפרטים שלך — נסו שוב בעוד רגע"

    # עותק של הטוקנים במסד, לפי מזהה אקראי בעוגייה (ראו ‎inject_auth‎)
    session["sid"] = secrets.token_urlsafe(32)
    db.web_session_open(session["sid"], user_id, access_token, refresh_token, expires_at)
    db.set_auth_token(access_token)     # הקריאה למסד רצה בלי טוקן משתמש
    return None


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    error = None
    # מה ימלא את שדה "אימייל או טלפון". ב-GET זה המזהה שנשמר מההתחברות
    # הקודמת מהמכשיר הזה; אחרי ניסיון כושל זה מה שהמשתמש הקליד עכשיו,
    # כדי שלא יצטרך להקליד שוב אחרי טעות בסיסמה.
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
                # מכאן והלאה המכשיר מוכר: גם אם ה-session ייגמר יום אחד,
                # השורש יביא אותו לטופס ההתחברות ולא חזרה לדף השיווק.
                return _remember_device(redirect(url_for("dashboard")), identifier)

    # העמוד נושא עכשיו את המזהה שנשמר, כך שהוא תלוי-עוגייה בדיוק כמו השורש
    # ואסור שיישמר במטמון של דפדפן או proxy.
    response = make_response(render_template("login.html", error=error,
                                             active_tab="login",
                                             remembered_identifier=identifier))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Cookie"
    return response


# ─── Face ID ─────────────────────────────────────────────────────────────────
# הוסר (מתן, 13.10): באייפון אתר לא יכול לפתוח Face ID ישירות — אפל מציגה
# קודם חלון "התחברות אל railway.app" ומחכה ללחיצה. "אני רוצה שהאתר פשוט
# יזהה אותי" — ההתחברות נשמרת (‎web_sessions‎), אז נכנסים ישר, בלי סריקה.


@app.route("/join/<code>")
def join_link(code):
    """קישור ההזמנה מוואטסאפ (מתן, 3.10 — רעיון 43). בלי חשבון — טופס ההרשמה
    עם הקוד מלא. עם חשבון — ההגדרות, בשורה "הצטרפות למשפחה אחרת" עם הקוד
    מלא; ההצטרפות עצמה בלחיצה שלו, לא מעצמה."""
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
        elif len(password) < passwords.MIN_LENGTH:
            error = passwords.TOO_SHORT
        # "אימות סיסמה" הוסר מהטופס (מתן, 13.10 — יש עין שמראה מה הוקלד). נבדק
        # רק אם נשלח, כדי שטופס ישן שעוד פתוח בטלפון ימשיך לעבוד
        elif "password_confirm" in request.form and password != password_confirm:
            error = "הסיסמאות אינן תואמות"
        # אחרונה: היא היחידה שפונה לשירות חיצוני, אז רק כשכל השאר תקין
        elif passwords.is_leaked(password):
            error = passwords.LEAKED
        else:
            name = f"{first_name} {last_name}"
            response, err = db.sign_up(email, password, name, phone)
            if err:
                # ממפים שגיאות מוכרות מ-Supabase להודעה בעברית — בעבר כל
                # שגיאה (גם הגבלת קצב, פורמט לא תקין וכו') הוצגה תמיד כ"האימייל
                # כבר קיים" בטעות, מה שהטעה כשהבעיה האמיתית הייתה שונה לגמרי
                err_lower = err.lower()
                if ("already registered" in err_lower or "already exists" in err_lower
                        or ("duplicate" in err_lower and "phone" in err_lower)
                        or "database error saving new user" in err_lower):
                    # הודעה אחת לשני המקרים, בכוונה.
                    #
                    # קודם "האימייל כבר קיים" ו"הטלפון כבר רשום" היו שתי
                    # תשובות שונות — כלומר טופס ההרשמה ענה בוודאות על
                    # "האם הכתובת הזאת רשומה כאן". עם ‎enable_confirmations‎
                    # כבוי זה גם אורקל וגם דרך לתפוס כתובת של מישהו אחר
                    # כך שהוא לא יוכל להירשם לעולם.
                    #
                    # ההודעה מפנה להתחברות ולאיפוס סיסמה, שהם מה שמי
                    # שבאמת שכח שיש לו חשבון צריך ממילא.
                    error = ("הרשמה נכשלה — ייתכן שכבר יש חשבון עם הפרטים האלה. "
                             "נסו להתחבר, או לאפס סיסמה.")
                elif "invalid" in err_lower and "email" in err_lower:
                    error = "הרשמה נכשלה – כתובת המייל אינה תקינה, בדקו שהיא הוזנה נכון"
                elif "rate limit" in err_lower:
                    error = "יותר מדי ניסיונות הרשמה בזמן קצר — נסו שוב בעוד כמה דקות"
                else:
                    # לא מדליפים את השגיאה הפנימית למשתמש — רק ללוג השרת
                    logger.error("signup: %s", err)
                    error = "הרשמה נכשלה — נסו שוב בעוד כמה רגעים"
            else:
                # הצטרפות למשפחה קיימת לפי קוד ההזמנה. כישלון כאן לא מבטל את
                # ההרשמה — המשתמש כבר נוצר ב-Auth ואסור להשאיר אותו בלי דרך
                # להיכנס — אבל הוא כן חייב להיאמר בקול. בעבר הערך המוחזר נזרק,
                # וכל מצטרף קיבל "נרשמת בהצלחה" ואז משפחה חדשה משלו בשקט.
                success = "נרשמתם בהצלחה! כעת ניתן להתחבר."
                join_err = None
                if invite_code and response and response.user:
                    # ה-RPC מזהה את המצטרף דרך auth.uid(), ולכן ה-client חייב
                    # לשאת את הטוקן של המשתמש החדש. לא מסתמכים על כך שה-SDK
                    # יעשה זאת לבד אחרי sign_up — קובעים אותו במפורש, כמו
                    # בכל מסלול אחר באפליקציה.
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

                # נכנסים ישר (מתן, 13.10): עד עכשיו מי שסיים את הטופס קיבל "כעת
                # ניתן להתחבר" והקליד שוב מייל וסיסמה — בדיוק ברגע שהכי קל לאבד
                # משתמש חדש. ‎sign_up‎ מחזירה סשן כשאימות מייל כבוי; כשהוא דלוק
                # (אין סשן) או שהכניסה נכשלה — כמו קודם, למסך ההתחברות.
                session_obj = getattr(response, "session", None) if response else None
                if session_obj and session_obj.access_token and response.user:
                    begin_err = _begin_session(response.user.id, response.user.email,
                                               session_obj.access_token, session_obj.refresh_token,
                                               session_obj.expires_at)
                    if not begin_err:
                        if join_err:
                            session["sf_notice"] = (f"נרשמתם בהצלחה! אבל {join_err}. "
                                                    "אפשר להצטרף למשפחה דרך ההגדרות.")
                        # בלי קטגוריות (משפחה חדשה) הבית מעביר לאשף הפתיחה
                        return _remember_device(redirect(url_for("dashboard")), email)
                    logger.warning("signup auto sign-in failed for %s: %s", email, begin_err)
                # ההרשמה לא מחברת אוטומטית, אבל היא כן הופכת את המכשיר
                # למוכר — מי שהרגע פתח חשבון בוודאי לא צריך לראות שוב את
                # דף השיווק, והמייל שלו כבר ממולא בטופס.
                return _remember_device(
                    make_response(render_template("login.html", active_tab="login",
                                                  success=success,
                                                  remembered_identifier=email)),
                    email)

    # מה שהוקלד חוזר עם השגיאה.
    #
    # עד היום כל שגיאת הרשמה — מייל לא תקין, סיסמאות שלא תואמות, מייל
    # שכבר קיים — מחקה את כל ששת השדות, **כולל קוד ההזמנה**. מי שקיבל
    # קוד בוואטסאפ והקליד סיסמה קצרה מדי נשאר בלי הקוד ובלי הפרטים,
    # וצריך לחזור לשיחה ולחפש. זה ההפך הגמור מצד ההתחברות, ששומר את
    # המזהה במפורש "כדי שלא יצטרך להקליד שוב אחרי טעות בסיסמה".
    #
    # הסיסמאות לא חוזרות, במכוון: אין סיבה שסיסמה תשב ב-HTML של תשובה.
    return render_template("login.html", error=error, active_tab="signup",
                           signup=_signup_form()), 422 if error else 200


# POST בלבד. עם ‎SameSite=Lax‎, ‎<img src=".../logout">‎ באתר אחר הוציא
# את המבקר מהחשבון ומחק לו את עוגיות המכשיר. מטרד ולא פרצה — אבל מטרד
# שמישהו יכול להפעיל מרחוק, ואין שום סיבה ש-GET יבצע פעולה.
@app.route("/logout", methods=["POST"])
@limiter.limit("30 per minute")
def logout():
    """יציאה יזומה מחזירה לדף הנחיתה, ומוחקת את כל מה שנשמר על המכשיר.

    זו הנקודה שבה כל הזיכרון מתאפס — לא רק ה-session אלא גם שתי עוגיות
    המכשיר. הכוונה מדויקת: יציאה יזומה היא הדרך היחידה החוצה, ומי שבחר
    בה מקבל בדיוק את מה שאורח מקבל — דף הנחיתה, ומשם כפתור "התחברות".
    זה גם מה שהופך את סימון המכשיר לאמין: מכאן והלאה, מכשיר שמסומן מוכר
    אבל אין לו session הוא בהכרח מקרה שבו ההתחברות נגמרה מעצמה — וזה
    בדיוק המקרה שבו דף שיווק הוא התשובה הלא נכונה."""
    if session.get("sid"):
        db.web_session_close(session["sid"])
    # טלפון משותף: מי שהתנתק לא ממשיך לקבל את ההתראות של המשפחה (מתן, 31.10)
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
    # פורמט פסול הוא לא מידע על מי רשום, ולכן מותר לומר אותו בקול —
    # בניגוד לתשובה שלמטה, שתמיד "נשלח" כדי לא לחשוף אילו כתובות קיימות.
    # בלי זה מי שהקליד כתובת שבורה קיבל "נשלח קישור" וחיכה לו לשווא.
    if not _looks_like_email(email):
        return jsonify({"error": "כתובת המייל אינה תקינה"}), 422

    redirect_to = request.host_url.rstrip("/") + url_for("reset_password")
    ok, err = db.send_reset_email(email, redirect_to)

    # התוצאה נזרקה לפח. ספק המייל של Supabase מוגבל לשני מיילים בשעה
    # לכל הפרויקט, אז השלישי בשעה קיבל "שלחנו לך קישור" ולא קיבל כלום —
    # ואין לו שום מסלול שחזור אחר, כי גם אימות המייל כבוי. שום דבר לא
    # נרשם, ולא היה שום סימן שזה קורה.
    if not ok:
        # ‎error‎ ולא ‎warning‎: זה אירוע ב-Sentry, ולא פירור שאף אחד
        # לא רואה בחוצץ הלוגים של Railway.
        logger.error("send_reset_email נכשל: %s", err)
        # מגבלת קצב אצל הספק היא מצב זמני, ואומרים אותו. שאר הכשלים
        # נשארים גנריים כדי לא לחשוף אילו כתובות רשומות.
        transient = "rate" in str(err).lower() or "limit" in str(err).lower()
        return jsonify({
            "error": ("יותר מדי בקשות איפוס ברגע זה. נסו שוב בעוד כמה דקות."
                      if transient else
                      "לא הצלחנו לשלוח את הקישור כרגע. נסו שוב בעוד כמה דקות."),
        }), 503

    # תמיד מחזירים הצלחה — לא חושפים אילו אימיילים רשומים
    return jsonify({"status": "ok"})


@app.route("/reset-password")
def reset_password():
    """עמוד קביעת סיסמה חדשה — הטוקן מגיע ב-fragment של הקישור מהמייל."""
    return render_template("reset_password.html")


@app.route("/api/auth/reset", methods=["POST"])
# לא מאומת, ומקבל טוקן שחזור מגוף הבקשה — כלומר פתוח לניחוש טוקנים.
# ‎/api/auth/forgot‎ שלצידו כבר מוגבל ל-3 לדקה; זה נשכח.
@limiter.limit("5 per minute")
def reset_password_submit():
    body             = request.get_json(silent=True) or {}
    access_token     = body.get("access_token", "")
    password         = body.get("password", "")
    password_confirm = body.get("password_confirm", "")

    if not access_token:
        return jsonify({"error": "קישור האיפוס לא תקין או שפג תוקפו"}), 400
    if len(password) < passwords.MIN_LENGTH:
        return jsonify({"error": passwords.TOO_SHORT}), 422
    if password != password_confirm:
        return jsonify({"error": "הסיסמאות אינן תואמות"}), 422
    if passwords.is_leaked(password):
        return jsonify({"error": passwords.LEAKED}), 422

    ok, err = db.update_password(access_token, password)
    if not ok:
        logger.error("reset password: %s", err)
        return jsonify({"error": "האיפוס נכשל — נסו לבקש קישור חדש"}), 400
    return jsonify({"status": "ok"})


# ─── Onboarding (משפחה חדשה בלבד) ──────────────────────────────────────────────

# סט התחלתי גנרי — כל משפחה חדשה יכולה לערוך, למחוק או להוסיף עליו.
# (לא כולל שמות ספציפיים כמו "קופת גמל אנליסט" שרלוונטיים רק למשפחה מסוימת)
_DEFAULT_CATEGORIES = [
    # ההוצאות וההכנסות — בדיוק כמו אצל מתן היום, באותו סדר (מתן, 13.10)
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
    # חיסכון — שתיים (מתן, 13.10)
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

    # מי שהצטרף למשפחה שעוד לא סיימה את ההגדרה נוחת כאן, כי "צריך
    # אשף" נמדד לפי היעדר קטגוריות. עד עכשיו הוא ראה את מסך הפתיחה
    # ("משפחה חדשה או הצטרפות?") — שנראה בדיוק ככישלון, והמוצא
    # המתבקש בו הוא "פותחים משפחה חדשה", כלומר פיצול המשפחה לשתיים.
    #
    # אם יש במשפחה עוד מישהו, ההצטרפות הצליחה ואין מה להגדיר: מי
    # שפתח את המשפחה עוד לא סיים. זה מה שצריך להיאמר.
    #
    # ורק למי שאינו המנהל. מי שפתח את המשפחה ונכנס אליו חבר באמצע
    # האשף חייב להמשיך לראות את האשף — אחרת ההצטרפות נועלת אותו
    # מחוץ להגדרה, ואף אחד מהשניים לא יכול להתקדם.
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

    # שלב "איך תרצו לעקוב?" — שיוך עסקאות לבני משפחה לפי בחירת המשפחה
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
    """מייל לבעל האתר (מתן, 5.10): משפחה סיימה את אשף הפתיחה, או שמישהו
    הצטרף למשפחה. שם המשפחה וכמה חברים יש בה עכשיו. נשלף כאן, בבקשה —
    השליחה עצמה ב-thread בלי גישה למסד (ראו ‎notify.py‎). קישוט: שום
    תקלה כאן לא מפילה הרשמה או הצטרפות.

    ‎person_id‎ — מי שפתח את המשפחה או הצטרף עכשיו: שם, מייל וטלפון (מתן,
    5.10). מתוך רשימת החברים שכבר נשלפה — בלי שליפה נוספת."""
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
            # ולבני המשפחה עצמם — התראה לטלפון (מתן, 31.10). הקריאה רצה בשם
            # מי שהצטרף, שכבר חבר, ומחזירה את המכשירים של כל השאר.
            joiner = (person or {}).get("name") or (person or {}).get("full_name") or ""
            push.send_in_background(db.push_family_targets(), push.member_joined(joiner, name))
    except Exception:
        logger.exception("notify owner about family (%s)", kind)


def _sync_recurring(family_id):
    """משלים מופעים של עסקאות קבועות, פעם ביום לכל משתמש.

    שני דברים תוקנו כאן ביחד.

    ראשית, זה רץ רק מהדשבורד. מי שהגיע ישר לעמוד החודש — מסימנייה,
    מהתפריט התחתון או מקישור — ראה חודש בלי המשכורת ובלי ההוראות
    הקבועות, ולא היה שום דבר שיסביר לו למה. המספרים תוקנו רק אם במקרה
    עבר דרך דף הבית.

    שנית, הסימון "סונכרן להיום" נכתב גם כשהיצירה נכשלה — ואז הניסיון
    הבא רק למחרת. כשל שנראה כהצלחה משאיר חודש שלם חסר.

    מסומן רק אחרי הצלחה, כך שכישלון חולף נפתר בטעינת העמוד הבאה."""
    if not family_id:
        return
    today = clock.today()
    today_str = today.isoformat()
    if session.get("recurring_synced") == today_str:
        return
    # אין היום שום חיוב קבוע — לא בודקים (מתן 1.11). מהעותק שבסשן, שמתעדכן
    # מההגדרות של המשפחה בכל עמוד (‎inject_family_settings‎) — בלי שליפה כאן.
    if db.recurring_can_wait(session.get("recurring_next"), today):
        session["recurring_synced"] = today_str
        return
    _, ok = db.materialize_recurring(family_id)
    if ok:
        session["recurring_synced"] = today_str


# ─── Main pages (5 עמודים: בית · החודש · השוואה · פרויקטים · הגדרות) ────────

def _home_page_separately(family_id, user, now):
    """הדרך הישנה לדף הבית — שליפה לכל חלק. רק כש-‎get_home_page‎ לא זמינה.
    ‎None‎ = אין קטגוריות (צריך את אשף הפתיחה)."""
    # מקבץ השליפות הבלתי-תלויות (רצות בזו אחר זו — ראו _run_queries)
    batch = _run_queries({
        "settings":   lambda: db.get_family_settings(family_id),
        "summary":    lambda: db.get_monthly_summary(family_id, now.year, now.month),
        "categories": lambda: db.get_categories(family_id),
        "members":    lambda: db.get_family_members(family_id),
        "is_new":     lambda: db.family_has_no_transactions(family_id),
    })

    # "צריך onboarding" == אפס קטגוריות — נגזר מהקטגוריות שכבר שלפנו (בלי שליפה נוספת)
    if not batch["categories"]:
        return None

    # "עסקאות אחרונות" תלויות בהעדפות — נשלפות אחרי שיש לנו אותן
    # עשר האחרונות **שהוזנו**, בסדר ההזנה — החדשה למעלה (מתן, 30.9). היום
    # כתוב בתוך כל שורה ("היום · רמי לוי"), כי בסדר הזנה כותרות לפי יום היו
    # חוזרות על עצמן.
    transactions = _timed("recent", lambda: db.get_recent_transactions(family_id, limit=10,
                          settings=batch["settings"], viewer_user_id=user["id"]))
    # "השבוע" (מתן, 30.9) — קישוט: תקלה בו לא מפילה את דף הבית
    try:
        week = _timed("week", lambda: db.week_spending(family_id))
    except db.DataUnavailable:
        week = None
    batch["transactions"] = transactions
    batch["week"] = week
    return batch


@app.route("/")
def dashboard():
    """השורש מגיש שני דברים שונים: דף נחיתה ציבורי למי שלא מחובר, והדשבורד
    למי שכן.

    עד היום הוא היה מוגן ב-login_required והפנה ישר ל-/login, כך שכל מי
    שקיבל קישור נחת על טופס התחברות בלי לדעת מה זה ולמה שימסור נתונים
    פיננסיים. שם הפונקציה נשאר dashboard כדי שכל url_for הקיים ימשיך לעבוד.

    מי שלא מחובר מקבל אחד משניים: מכשיר שמעולם לא התחבר מכאן רואה את דף
    הנחיתה, ומכשיר מוכר מדלג עליו ישר לטופס ההתחברות. דף שיווק הוא התשובה
    הנכונה לאורח, ולא למי שרק רצה להיכנס לתקציב שלו. ‎?intro=1 הוא הדרך
    לראות את דף הנחיתה בכל זאת — משם מגיע כפתור "חזרה" שבדף ההתחברות."""
    if "user_id" not in session:
        # ‎?app=1‎ — נפתח מהאייקון במסך הבית (start_url במניפסט). מי שהתקין
        # את האפליקציה כבר בחר בה; דף שיווק הוא לא מה שהוא צריך לראות. זה
        # קרה למי שהתקין לפני שהתחבר: לאפליקציה המותקנת באייפון יש עוגיות
        # משלה, ו-‎sf_returning‎ עוד לא היה בהן.
        opened_as_app = request.args.get("app") == "1"
        if (_device_is_known() or opened_as_app) and not request.args.get("intro"):
            response = redirect(url_for("login"))
        else:
            # החודש נגזר מהשעון ולא מקודד בתבנית. "ספטמבר 2026" קבוע
            # היה נכון ליום שנכתב ושגוי מ-1 באוקטובר, ותאריך ישן בדף
            # שיווק קורא כמו פרויקט נטוש.
            now = clock.now()
            response = make_response(render_template(
                "landing.html", demo_month=_month_label(now.year, now.month)))
        # התוכן כאן תלוי במצב ההתחברות ובעוגיית המכשיר, ולכן אסור שיישמר
        # במטמון כלשהו — ו-Vary אומר את זה גם ל-proxy שבדרך.
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

    # עלול לכתוב שורות, אז לפני מקבץ השליפות
    _timed("recurring", lambda: _sync_recurring(family_id))

    # נסיעה אחת למסד (מתן, 13.10): ‎get_home_page‎ מביאה את כל מה שהיה תשע
    # שליפות. אם היא לא זמינה (המיגרציה עוד לא רצה, תקלה) — הדרך הישנה.
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

    # מיחזור תוצאות המקבץ ל-context-processors ו-_member_colors (בלי שליפה חוזרת)
    _prime_request_cache(family_id, settings=batch["settings"], categories=batch["categories"],
                         members=batch["members"], family=batch.get("family"))

    # התראות החודש — רק כאן, כשיש (מתן, 11.10): חריגה מתקציב של קטגוריה.
    # לחיצה מובילה לקטגוריה בעמוד החודש. שורות החודש נשלפות רק כשיש
    # תקציבים — בלעדיהם אין מה לבדוק. קישוט: תקלה לא מפילה את דף הבית.
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

    # "ספטמבר נסגר" (מתן, 13.10) — רק ביום הראשון של החודש. קישוט: תקלה לא
    # מפילה את דף הבית.
    try:
        recap = db.month_recap(family_id, clock.today(), batch["categories"])
    except db.DataUnavailable:
        recap = None

    # פרויקט מוצמד (מתן, 12.10): "טיול ליוון: נשארו ₪7,800 מתוך ₪12,000" מתחת
    # למאזן. רק כשמישהו הצמיד — אחרת אין שליפה. פרויקט שהסתיים או שנמחק —
    # הכרטיס לא מופיע. קישוט: תקלה לא מפילה את דף הבית.
    pinned_project = None
    pinned_id = ((batch["settings"] or {}).get("pinned_projects") or {}).get(user["id"])
    if pinned_id:
        try:
            pinned_project = next((p for p in _timed("pinned", lambda: db.get_projects(family_id, user["id"]))
                                   if p["id"] == pinned_id), None)
        except db.DataUnavailable:
            pinned_project = None

    # "איך מצרפים בן משפחה" (מתן, 5.10) — בכניסה הראשונה, אחרי אשף הפתיחה
    # (‎?welcome=1‎ מכפתור "לאפליקציה"), ורק כשעוד אין במשפחה אף אחד אחר
    welcome_invite = None
    if request.args.get("welcome") == "1" and len(batch["members"] or []) == 1:
        try:
            family = _fresh_invite(db.get_family(family_id), user)
        except db.DataUnavailable:
            family = None          # קישוט: תקלה לא מפילה את דף הבית
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
        # "2026 שלכם" (מתן, 1.11) — כרטיס 1–15 בינואר, הסיכום נטען בלחיצה
        year_recap_year=db.year_recap_due(clock.today()),
        hebrew_months=_HEBREW_MONTHS,
        pinned_project=pinned_project,
        # ל"בן משפחה הצטרף" (‎whats-new.js‎): מזהה, שם פרטי וצבע — בלי פרטים נוספים
        home_members=[{"id": m["id"], "name": m.get("name") or "",
                       "color": _member_colors(family_id).get(m["id"])}
                      for m in (batch["members"] or [])],
    )


@app.route("/api/year-recap")
@limiter.limit("30 per minute")
@login_required
def year_recap_api():
    """"2026 שלכם" — סיכום השנה, נפתח בלחיצה (מתן, 1.11). רק מי שלוחץ רואה."""
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
    """עמוד החודש: כל הנתונים והגרפים של חודש נתון (ברירת מחדל: הנוכחי)."""
    user      = get_current_user()
    now       = clock.now()
    # type=int מוודא שזה מספר, לא שזה חודש קיים: ?month=99999999 הפיל את
    # העמוד ב-500 עד שהתגלה. נופלים לחודש הנוכחי במקום להתפוצץ.
    year      = request.args.get("year",  now.year,  type=int)
    month     = request.args.get("month", now.month, type=int)
    if not 1 <= (month or 0) <= 12:
        month = now.month
    if not 1970 <= (year or 0) <= 2100:
        year = now.year
    family_id = user["family_id"]

    # לפני השליפות: בלי זה חודש שנפתח ישירות (סימנייה, תפריט, קישור)
    # מוצג בלי המשכורת ובלי ההוראות הקבועות
    _timed("recurring", lambda: _sync_recurring(family_id))

    is_current = (year == now.year and month == now.month)
    # כיוון החץ של "חזרה לחודש הנוכחי": מחודש עתידי החודש הנוכחי הוא אחורה
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
            # התבנית ניגשת ל-project_month ללא תנאי. המסלול הזה נשכח כשנוספו
            # הפרויקטים, ומשתמש בלי משפחה קיבל 500 במקום העמוד הריק המיועד.
            project_month={"expense": 0, "income": 0, "savings": 0,
                           "transactions": [], "projects": []},
        )

    # שלב 1 — שליפות בלתי-תלויות. שורות החודש נשלפות **פעם אחת**, וכל
    # הסיכומים נגזרים מהן: הסיכום, שלושת הפילוחים לקטגוריה, שני הפילוחים
    # לבן משפחה ורשימת העסקאות היו שבע שאילתות על אותן שורות בדיוק.
    #
    # החיסכון בזמן (כ-180ms) הוא הצד הפחות חשוב. העיקר הוא שכל אחת מהשבע
    # גזרה לעצמה מחדש את אותם שני כללים — החרגת עסקאות פרויקט, והסתרת
    # פרויקט אישי של בן משפחה אחר — והכלל השני כבר נשכח פעם אחת (ראו
    # ההערה על התאמת קטגוריות לפי שם, למטה). עכשיו הוא מיושם פעם אחת.
    # ...וחמש השליפות שנשארו הן עכשיו נסיעה אחת. ‎_run_queries‎ מריצה
    # ברצף (ראו ההערה שם — מקביליות על הלקוח המשותף החזירה אפסים בשקט),
    # אז החמש שילמו זו אחרי זו ~30ms כל אחת. ‎get_month_page‎ מחזירה את
    # חמשתן יחד; ההרכבה נשארה ב-Python ולא ירדה ל-SQL, כדי שלא יהיו שני
    # מקורות אמת ל-‎_merge_settings‎ ולקיצור השמות.
    p1 = _timed("month_page", lambda: db.fetch_month_page(family_id, year, month))
    settings_ = p1["settings"]
    rows      = p1["rows"]
    # חודש עתידי (מתן, 1.11): העסקאות הקבועות הצפויות בו מופיעות ונספרות,
    # עד שהסדרה נגמרת — "כמו תחזית". מסומנות "צפוי" ולא נפתחות לעריכה.
    # בחודש הנוכחי — כמו קודם: עסקה קבועה מופיעה מהיום שהיא יורדת, כשהמנוע
    # יוצר אותה. (4.10–1.11 גם חודש עתידי לא הציג אותן.)
    if is_future:
        rows = list(rows) + db.projected_recurring_rows(family_id, user["id"], year, month)
        # כמו השליפה עצמה — מהחדש לישן, אז הצפויים נכנסים במקומם ולא בסוף
        rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)

    # מוזן כאן ולא לפני ‎render_template‎ כמו קודם: שליפות שלב 2 (חריגות,
    # קצב, הוראות קבועות) פונות בעצמן לקטגוריות ולחברי המשפחה, ועכשיו הן
    # מקבלות אותם מהמקבץ במקום לשלוף שוב באמצע הבקשה.
    _prime_request_cache(family_id, settings=settings_, members=p1["members"],
                         categories=p1["categories"], family=p1["family"])

    summary = db.summary_from_rows(rows)
    # תקציבי הקטגוריות נוספים לפילוח ההוצאות: הפס מודד מול ההחלטה של
    # המשפחה במקום מול סך ההוצאות החודש
    expense_breakdown = db.apply_budgets(
        db.category_breakdown_from_rows(rows, p1["categories"], "expense"), settings_)
    income_breakdown  = db.category_breakdown_from_rows(rows, p1["categories"], "income")
    savings_breakdown = db.category_breakdown_from_rows(rows, p1["categories"], "savings")
    # צבע קבוע לכל קטגוריה (מתן, 12.10): לפי המקום שלה ברשימת הקטגוריות של
    # המשפחה, ולא לפי המקום בפילוח החודש — "סופר" זהב בכל החודשים, והעיגול
    # יכול "לזרום" בין חודשים. פרויקט — לפי המזהה, יציב גם הוא.
    _cat_order = {c["id"]: i for i, c in enumerate(c for c in (p1["categories"] or [])
                                                   if c.get("type") == "expense")}
    for c in expense_breakdown:
        if c.get("is_project"):
            c["color_idx"] = sum(map(ord, str(c.get("project_id") or c.get("name") or "")))
        else:
            c["color_idx"] = _cat_order.get(c.get("category_id"), len(_cat_order))
    _distinct_colors(expense_breakdown)

    # רצועת החודשים: מהישן לחדש (ה-RPC מחזיר מהחדש לישן), ותמיד כוללת את
    # החודש הנצפה — גם אם אין בו עסקאות ולכן הוא לא חוזר מה-RPC
    strip_months = [{"year": m["year"], "month": m["month"]} for m in reversed(p1["archive"] or [])]
    if not any(m["year"] == year and m["month"] == month for m in strip_months):
        strip_months.append({"year": year, "month": month})
        strip_months.sort(key=lambda m: (m["year"], m["month"]))

    # שלב 2 — שליפות שתלויות בהעדפות/בסיכום
    # גרף "לפי בן משפחה" — לא כשבמשפחה אדם אחד: עמודה אחת לא אומרת כלום
    # (מתן, 5.10; כמו בעמוד ההשוואה)
    active_types = [t for t in ("expense", "income", "savings")
                    if settings_["owner_attribution"].get(t) and len(p1["members"] or []) > 1]
    month_transactions = db.month_transactions_from_rows(rows, settings_, user["id"])
    # שורת הקטגוריות מתחת ל"הוצאות" וכו׳ ב"כל העסקאות" (מתן, 30.9)
    tx_cat_chips = db.category_filter_chips(month_transactions)

    # פעילות פרויקטים החודש — מוחרגת מהמאזן/הגרפים, ומוצגת בנפרד. נגזרת
    # מהעסקאות שכבר נשלפו (שכוללות גם עסקאות פרויקט), בלי שליפה נוספת.
    project_txs = [t for t in month_transactions if t.get("project_id")]
    project_month = {
        "expense":      round(sum(t["amount"] for t in project_txs if t["type"] == "expense"), 2),
        "income":       round(sum(t["amount"] for t in project_txs if t["type"] == "income"), 2),
        "savings":      round(sum(t["amount"] for t in project_txs if t["type"] == "savings"), 2),
        "transactions": project_txs,
        "projects":     db.project_breakdown_from_rows(project_txs),
    }

    # ומכאן — רק העסקאות הרגילות. כל שאר נתוני החודש (סיכום, פילוח קטגוריות,
    # חלוקה בין בני משפחה, חריגות) כבר מסננים עסקאות פרויקט בשאילתה עצמה;
    # הרשימה הזו הייתה היחידה שלא, והתבנית מתאימה עסקאות לקטגוריות לפי *שם*.
    # קטגוריית פרויקט ששמה זהה לקטגוריה משפחתית (למשל "אחר", שנזרעת בשתיהן)
    # גרמה לעסקת הפרויקט להופיע בתוך הקטגוריה החודשית — בלי להיספר בסכום שלה.
    month_transactions = [t for t in month_transactions if not t.get("project_id")]

    # קבועות מול משתנות (מתן, 12.10): מה שהיה יוצא בכל מקרה (עסקה קבועה —
    # תבנית או מופע, כולל תשלומים) ומה שתלוי בכם. רק כשיש קבועות — בלי, אין מה לחלק.
    _exp = [t for t in month_transactions if t["type"] == "expense"]
    _fixed = [t for t in _exp if t.get("is_recurring") or t.get("recurring_parent_id")]
    fixed_split = None
    if _fixed:
        fixed_total = round(sum(t["amount"] for t in _fixed), 2)
        exp_total = round(sum(t["amount"] for t in _exp), 2)
        fixed_split = {"fixed": fixed_total, "variable": round(exp_total - fixed_total, 2)}

    # גרף חלוקה בין בני משפחה לכל סוג עסקה שהמשפחה הפעילה בו שיוך
    _type_labels = {"expense": "הוצאות", "income": "הכנסות", "savings": "חיסכון"}
    mcolors = _member_colors(family_id)

    # כרטיסי ההכנסות והחיסכון — גרף עגול כמו ההוצאות (מתן, 30.9). משכורת בצבע
    # של בן המשפחה (אותו צבע כמו התג עם השם), והכנסות נוספות באפור ניטרלי.
    # האחוזים מסתכמים ל-100 בדיוק (‎share_map‎).
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
        # ‎mb‎ ולא ‎rows‎: ‎rows‎ הוא שורות החודש, ודריסה שלו כאן הייתה
        # מרעילה את כל מה שנגזר ממנו בהמשך
        mb = db.member_breakdown_from_rows(rows, t)
        for r in mb:
            idx = mcolors.get(r.get("user_id"))
            r["color"] = _OWNER_HEX.get(idx, _SHARED_HEX) if idx is not None else _SHARED_HEX
        # עמודה אחת לא משווה כלום — בתחילת חודש, כשרק אחד הזין, זה היה גרף
        # בגובה חצי מסך עם עמודה יחידה (מתן, 12.10). "משותפת" נחשבת עמודה.
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
        # רק קטגוריות פעילות (total>0) — כדי שאינדקסי הצבעים בגרף העגול
        # יתאמו למקרא (שגם הוא מסונן ל-active), ובלי פרוסות ברוחב 0.
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
    """ייצוא מלא של הנתונים — סעיף 20 (ניידות) ב-GDPR.

    עד עכשיו הייצוא היחיד היה ‎/month.csv‎: חודש בודד, מעמוד החודש,
    בלי פרופיל, בלי קטגוריות ובלי פרויקטים. כלומר "קחו את הנתונים
    ולכו" היה אפשרי רק חודש-חודש ביד, וזו לא ניידות.

    JSON ולא CSV כי אלה כמה טבלאות עם קשרים ביניהן, וקובץ שטוח אחד היה
    מאבד אותם. ‎/month.csv‎ נשאר לגיליון אלקטרוני.

    ‎5 per hour‎: זו שאילתה שמושכת את כל ההיסטוריה של המשפחה.
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


# תא שמתחיל באחד מאלה הוא נוסחה באקסל (CSV injection)
_CSV_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_text(value) -> str:
    """טקסט חופשי לתא ב-CSV. גרש בהתחלה אומר לאקסל "טקסט, לא נוסחה":
    "‎-50 הנחה‎" לא יהפוך ל-‎#NAME?‎, ותיאור זדוני לא ירוץ אצל מי שפותח."""
    text = str(value or "")
    return "'" + text if text.startswith(_CSV_FORMULA_START) else text


@app.route("/month.csv")
@limiter.limit("10 per minute")
@login_required
def month_csv():
    """ייצוא עסקאות החודש כ-CSV.

    נתונים שאפשר להוציא הם נתונים שאפשר לבטוח בהם, וזו התשובה הזולה
    ביותר ל"מה קורה אם ארצה לעזוב". גם כל שיחה עם רואה חשבון מתחילה כאן.

    שני פרטים שקובעים אם הקובץ באמת נפתח נכון בעברית:
    · BOM בתחילת הקובץ — בלעדיו אקסל מפרש UTF-8 כקידוד מקומי, וכל
      התיאורים והקטגוריות הופכים לג'יבריש. זו התלונה מספר אחת על ייצוא
      CSV בעברית, והיא נראית כמו באג באפליקציה ולא בתוכנה שפותחת.
    · ‎\r\n‎ כסוף שורה — מה ש-Excel מצפה לו."""
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
        # אותה שליפה שמזינה את עמוד החודש, כולל סינון פרויקט אישי של
        # בן משפחה אחר — הייצוא לא אמור לחשוף מה שהמסך מסתיר
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
    """עמוד השוואה: החודש הנוכחי מול חודשים קודמים + כניסה לכל חודש."""
    user      = get_current_user()
    family_id = user["family_id"]
    _sync_recurring(family_id)
    now       = clock.now()
    # נסיעה אחת למסד (מתן, 13.10) — ‎get_months_page‎; אם לא זמינה, כמו קודם
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
    # "לפי קטגוריה" (מתן, 30.9): אותם חודשים כמו הגרף שמעליו. נשלחים 12, והדפדפן
    # מציג 3 / 6 / 12 לפי הטווח שבוחרים (רעיון 24)
    if page is not None:
        cat_trend = db.category_trend_from_rows(page["expense_rows"], months_shown, page["categories"])
    else:
        cat_trend = db.category_trend(family_id, months_shown,
                                      db.get_categories(family_id)) if family_id else None
    # "הוצאות לפי בן משפחה" לאורך החודשים (רעיון 15) — רק כששיוך ההוצאות פעיל
    # ויש יותר מבן משפחה אחד. קישוט: תקלה לא מפילה את העמוד
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
                           # "2026 עד עכשיו" כל השנה, כולל דצמבר; בינואר "סיכום 2026"
                           # (מתן, 1.11 — "השנה עוד לא נגמרה")
                           recap_year=now.year - 1 if now.month == 1 else now.year,
                           recap_partial=now.month > 1,
                           _HEBREW_MONTHS=_HEBREW_MONTHS)


@app.route("/projects")
@login_required
def projects():
    """עמוד פרויקטים: תקציב לפרויקט — טיול, שיפוץ, אירוע ועוד."""
    user      = get_current_user()
    family_id = user["family_id"]
    # נסיעה אחת למסד (מתן, 13.10) — ‎get_projects_page‎; אם לא זמינה, כמו קודם
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
        # "פרויקטים שהסתיימו" — חלק סגור בתחתית (מתן, 30.9)
        finished = db.get_projects(family_id, user["id"], archived=True) if family_id else []
        last = db.project_last_activity(family_id) if len(project_list) > 1 else {}
    # הפרויקט שנוספה לו עסקה לאחרונה — ראשון (מתן, 12.10). בלי עסקאות — אחריהם,
    # בסדר שהגיע (החדש קודם). ‎sorted‎ יציב, אז שני הסדרים נשמרים.
    if len(project_list) > 1:
        project_list = sorted(project_list, key=lambda p: last.get(p["id"]) or "", reverse=True)
    return render_template("projects.html", active_page="projects", user=user,
                           projects=project_list, finished_projects=finished)


def _member_names(family_id) -> dict:
    """מזהה ← שם פרטי, לסימן "נרשמה ע״י". מי שכבר לא במשפחה לא ברשימה."""
    return {m["id"]: m["name"] for m in db.get_family_members(family_id)} if family_id else {}


def _others_contributions(project: dict, viewer_id: str, names: dict) -> str:
    """"10 עסקאות נרשמו כאן על ידי אור" — לאישור של "החזר להיות אישי".

    החזרה לאישי מסתירה את הפרויקט מכל השאר, כולל את מה שהם עצמם רשמו בו
    (החלטת מתן, 28.9.2026). אז זה נאמר מראש, בשמות ובמספרים. עסקאות שלא
    ידוע מי רשם (לפני שהתחלנו לשמור את זה) לא נספרות."""
    from collections import Counter
    counts = Counter(names.get(t["created_by"], "בן משפחה לשעבר")
                     for t in project.get("transactions") or []
                     if t.get("created_by") and t["created_by"] != viewer_id)
    # בלי זכר ונקבה (מתן, 1.10)
    parts = [(f"עסקה אחת נרשמה כאן על ידי {name}" if n == 1
              else f"{n} עסקאות נרשמו כאן על ידי {name}")
             for name, n in counts.most_common()]
    return " · ".join(parts)


def _project_gone():
    """פרויקט שנמחק, או קישור שנחתך: לרשימה, עם הסבר.

    אותה הודעה גם לפרויקט אישי של בן משפחה אחר — "לא נמצא" ולא "אין
    הרשאה", כדי שהתשובה לא תאשר שהוא קיים (ראו ‎project_access_required‎)."""
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
    # "הפרויקט הסתיים?" (מתן, 12.10): פרויקט פעיל שלא נוספה לו עסקה חודשיים
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
    """יצירת פרויקט — מסך משלו, ולא טופס שנפתח בתוך הרשימה (מתן, 5.10).
    ‎?icon=…&name=…‎ — מהרעיונות להתחלה ("✈️ טיול"), שממלאים את הטופס."""
    user = get_current_user()
    return render_template("project_new.html", active_page="projects", user=user,
                           icon=_text(request.args.get("icon"))[:16],
                           name=_text(request.args.get("name"))[:50])


@app.route("/projects/<project_id>/edit")
@login_required
def project_edit(project_id):
    """מסך עריכת הגדרות הפרויקט (שם/תיאור/יעד/מעקב/שיתוף) + ניהול הקטגוריות —
    נפרד מעמוד התצוגה הנקי של הפרויקט."""
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
    """מפענח ומאמת שדות משותפים ליצירה/עדכון של פרויקט (שם/יעד/סוגי מעקב
    בלבד — לא בעלות: זו נקבעת בנפרד ב-add_project_route, ומשתנה אחר כך רק
    דרך share_project_route/unshare_project_route).
    Returns (fields_dict, error) — fields_dict מוכן להעברה ל-db.add/update_project."""
    name = _text(body.get("name"))
    if not name:
        return None, "נא להזין שם לפרויקט"

    # ‎_parse_amount‎ ולא ‎float()‎ חשוף: יעד שלילי נשמר והוצג כ-‎-₪5,000‎,
    # ו-‎1e12‎ הציף את ‎numeric(10,2)‎ וחזר כ-500 סתום — בדיוק המבוי הסתום
    # שהפונקציה הזאת נכתבה כדי למנוע. יעד ריק הוא ערך תקין (אין יעד).
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
    """לפני שמירה של עסקה חדשה: כפילות? סכום חריג? (מתן, 30.9 — סבב 6, 6–7)."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"duplicate": None, "unusual": None})
    return jsonify(db.precheck_transaction(user["family_id"], user["id"],
                                           request.get_json(silent=True) or {}))


@app.route("/api/search", methods=["GET"])
@login_required
def search_route():
    """חיפוש בכל החודשים — הזכוכית המגדלת בדף הבית (מתן, 30.9)."""
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
    """התיאורים שכבר נכתבו, להשלמה בטופס (מתן, 3.10 — רעיון 41). נטען פעם אחת
    כשהטופס נפתח; הסינון לפי מה שמקלידים — בדפדפן."""
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
    """כרטיס "השבוע" לשבוע קודם — הדפדוף בדף הבית (מתן, 2.10). מחזיר את
    הכרטיס כ-HTML מאותה תבנית שדף הבית מרנדר, כדי שלא יהיו שתי גרסאות."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלכם — רעננו את הדף"}), 400
    try:
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        return jsonify({"error": "ערך לא תקין"}), 422
    if not 0 <= offset <= 1040:          # עד 20 שנה אחורה
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
    # אפשר ליצור פרויקט משותף או אישי עבור עצמו בלבד — אף פעם לא אישי
    # עבור בן משפחה אחר (owner_id נקבע כאן מהמשתמש המחובר, לא מהבקשה)
    is_personal = bool(body.get("is_personal"))
    owner_id = user["id"] if is_personal else None
    proj, err = db.add_project(user["family_id"], created_by=user["id"], owner_id=owner_id, **fields)
    if err:
        logger.error("add_project route: %s", err)
        return jsonify({"error": "יצירת הפרויקט נכשלה — נסו שוב"}), 500
    return jsonify(proj), 201


def tx_visible_required(arg: str):
    """עסקה בפרויקט אישי של בן משפחה אחר — כאילו אינה קיימת.

    בכל רשימה היא כבר מוסתרת (‎_filter_hidden_personal_projects‎), אבל
    המסלולים שפועלים על עסקה לפי מזהה בדקו רק את פרויקט *היעד*. מי שהשיג
    מזהה — ורשימת הקבועות בהגדרות מסרה אותו — יכול היה לערוך, למחוק
    ולפתוח את הקבלה. 404 ולא 403: 403 היה מאשר שהעסקה קיימת.

    ‎arg‎ הוא שם הפרמטר בכתובת (‎tx_id‎ / ‎template_id‎). חייב לשבת מתחת
    ל-‎login_required‎."""
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
    """חוסם גישה לפרויקט אישי של בן משפחה אחר.

    הקריאה כבר הייתה מוגנת — ‎get_project_detail‎ ו-‎get_projects‎ מסננות
    פרויקט אישי שאינו של הצופה. הכתיבה לא: כל שמונת המסלולים סיננו לפי
    family_id בלבד. כלומר בן משפחה שניחש או ראה מזהה של פרויקט אישי יכול
    היה לערוך אותו, למחוק אותו על כל עסקאותיו, או לקרוא את הקטגוריות שלו
    — פרויקט שאסור לו אפילו לראות ברשימה.

    "לא נמצא" ולא "אין הרשאה" בכוונה: פרויקט אישי הוא פרטי, והתשובה
    אסור שתאשר שהוא קיים."""
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
    # בלי ‎delete_transactions‎ מהגוף: העסקאות נמחקות תמיד (ראו ‎db.delete_project‎),
    # והשרת לא סומך על לשונית ישנה שעוד שולחת "להשאיר".
    # נאסף לפני: אחרי המחיקה אין עסקה שמצביעה על הקבצים, ושום דבר אחר לא
    # ימחק אותם (ראו ‎db.delete_receipts‎ על הניקוי הלילי שלא עובד).
    receipts = db.receipt_paths(user["family_id"], project_ids=[project_id])
    ok, wiped = db.delete_project(project_id, user["family_id"])
    if ok:
        _delete_unused_receipts(user["family_id"], receipts)
    if not ok:
        # שום שורה לא נגעה: הפרויקט כבר נמחק על ידי בן משפחה אחר, או
        # שהמזהה אינו של המשפחה הזאת. "נמחק" על כלום הוא שקר.
        return jsonify({"error": "הפרויקט לא נמצא — ייתכן שנמחק בינתיים"}), 404
    # ‎deleted‎ מוחזר כדי שהמשתמש יראה כמה עסקאות באמת נעלמו, בדיוק כמו
    # באיפוס העסקאות. "מחק גם עסקאות" לא נשא שום מספר.
    return jsonify({"status": "ok", "deleted": wiped})


@app.route("/api/projects/<project_id>/share", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
@project_access_required
def share_project_route(project_id):
    """הופך פרויקט אישי למשותף. רק הבעלים הנוכחי רשאי (נאכף ב-db.share_project)."""
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
    """מחזיר פרויקט משותף להיות אישי. רק מי שיצר אותו במקור רשאי (נאכף ב-db.unshare_project)."""
    user = get_current_user()
    ok, err = db.unshare_project(project_id, user["family_id"], user["id"])
    if not ok:
        return jsonify({"error": _user_message(err)}), 422
    return jsonify({"status": "ok"})


@app.route("/api/projects", methods=["GET"])
@login_required
def list_projects_route():
    """‎?include_archived=1‎ — טופס העסקה: גם פרויקטים שהסתיימו, מסומנים, כדי
    שעסקה ישנה שנערכת לא תיפול מהפרויקט שלה."""
    user = get_current_user()
    archived = None if request.args.get("include_archived") == "1" else False
    return jsonify(db.get_projects(user["family_id"], user["id"], archived=archived)
                   if user["family_id"] else [])


@app.route("/api/projects/<project_id>/archive", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
@project_access_required
def archive_project_route(project_id):
    """"הפרויקט הסתיים" (‎archived: true‎) / "פתיחה מחדש" (‎false‎)."""
    user = get_current_user()
    archived = (request.get_json(silent=True) or {}).get("archived")
    if not isinstance(archived, bool):
        return jsonify({"error": "ערך לא תקין"}), 422
    if not db.set_project_archived(project_id, user["family_id"], archived):
        return jsonify({"error": "הפרויקט לא נמצא — ייתכן שנמחק בינתיים"}), 404
    return jsonify({"status": "ok", "archived": archived})


@app.route("/api/projects/<project_id>/pin", methods=["PUT"])
@limiter.limit("30 per minute")
@login_required
@project_access_required
def pin_project_route(project_id):
    """"הצמדה לבית" (מתן, 12.10): פרויקט אחד לכל בן משפחה, בהגדרות המשפחה
    תחת ‎pinned_projects[user_id]‎ — ממוזג לפי מפתח, אז שני בני משפחה שמצמידים
    באותו רגע לא דורסים זה את זה. ‎null‎ — בלי הצמדה."""
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
    """נכנסו מהאייקון במסך הבית (מתן, 13.10): מעכשיו בלי הפס "הוסיפו למסך הבית"
    — גם כשהמשתמש הזה נכנס מהדפדפן. נשמר בהגדרות המשפחה תחת
    ‎app_installed[user_id]‎, ממוזג לפי מפתח כמו ‎pinned_projects‎."""
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


_CATEGORY_NAME_MAX = 30     # כמו ה-‎maxlength‎ של המסך
_CATEGORY_ICON_MAX = 16     # אימוג'י מורכב (משפחה, דגל) הוא כמה תווים — עדיין אייקון


def _category_fields(body: dict):
    """שם ואייקון של קטגוריה (משפחתית או של פרויקט). מחזירה (name, icon, error).

    למסך היה ‎maxlength‎, לשרת — כלום. מי שפנה ל-API ישירות יכול היה לשמור
    אייקון ‎<style>…</style>‎, והוא נכנס ל-‎innerHTML‎ אצל כל המשפחה; ו-
    ‎{"name": 5}‎ הפיל את המסלול ב-500 על ‎.strip()‎ של מספר."""
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
    # ‎get_project_for_transaction‎ לא מחזירה ‎id‎ — בלעדיו השאילתה רצה עם "None"
    project = {**(db.get_project_for_transaction(project_id, user["family_id"]) or {}),
               "id": project_id}
    return _delete_category("project", cat_id, user, project=project)


@app.route("/api/projects/<project_id>/categories/<cat_id>/usage", methods=["GET"])
@login_required
@project_access_required
def project_category_usage_route(project_id, cat_id):
    user = get_current_user()
    # ‎get_project_for_transaction‎ לא מחזירה ‎id‎ — בלעדיו השאילתה רצה עם "None"
    project = {**(db.get_project_for_transaction(project_id, user["family_id"]) or {}),
               "id": project_id}
    return _category_usage("project", cat_id, user, project=project)


def _invite_deadline(family):
    """מתי קוד ההזמנה של המשפחה פג, או ‎None‎ אם אין תאריך."""
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
    """בהגדרות של כל בן משפחה תמיד יש קוד הזמנה תקף (בקשת מתן, 29.9.2026).

    הקוד תקף שבוע, ופג בשקט: מי שנכנס כדי לשלוח קוד מצא "הקוד פג". עכשיו
    כניסה להגדרות כשהקוד כבר פג מפיקה חדש — אצל כל בן משפחה, לא רק המנהל.
    **רק כשכבר פג** (‎renew_expired_invite_code‎, ולא ‎rotate‎): קוד שעוד בתוקף
    אולי נשלח למישהו, והחלפה הייתה מבטלת אותו — וזו נשארת הרשאה של המנהל.
    כשל משאיר את "הקוד פג" על המסך — לא נופלים בגללו."""
    if not family:
        return family
    if family.get("invite_code") and not _invite_expired(family):
        return family
    code, _ = db.renew_expired_invite_code()
    return db.get_family(family["id"]) if code else family


def _settings_separately(family_id, user):
    """הדרך הישנה לעמוד ההגדרות — שליפה לכל חלק. רק כש-‎get_settings_page‎
    לא זמינה, או כשאין משפחה."""
    categories = db.get_categories(family_id)
    # "בממוצע ₪1,850 בחודש" ליד כל קטגוריה (רעיון 38) — קישוט: תקלה לא מפילה
    try:
        cat_averages = db.category_monthly_averages(family_id) if family_id else {}
    except db.DataUnavailable:
        cat_averages = {}
    members    = db.get_family_members(family_id)       if family_id else []
    family     = _fresh_invite(db.get_family(family_id), user) if family_id else {}
    # רק סדרות פעילות — ראו ‎db.is_active_template‎
    recurring  = [r for r in db.get_recurring_transactions(family_id, user["id"],
                                                          settings=family_settings())
                  if db.is_active_template(r)] if family_id else []
    # "₪5,000 ← ₪5,500 מאוקטובר" (רעיון 40)
    price_changes = db.recurring_price_changes(family_id, recurring) if recurring else {}
    # כמה עסקאות באמת עומדות להימחק. "פעולה סופית ולא ניתנת לביטול" הוא
    # משפט; מספר הוא עובדה, והוא מה שגורם לעצור.
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
    # נסיעה אחת למסד (מתן, 13.10) — ‎get_settings_page‎. אם היא לא זמינה
    # (המיגרציה עוד לא רצה, תקלה) — שליפה לכל חלק, כמו קודם.
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
    # "הבא: 1.11" לכל עסקה קבועה (מתן, 12.10)
    next_charges = {t["id"]: next_charge_label(db.next_recurring_date(t, today), today)
                    for t in (recurring or [])}
    # הפרופיל שלי כבר נמצא ברשימת החברים שנשלפה למעלה — היא מחזירה שם
    # מלא, אימייל, טלפון ומקום עבודה לכל חבר. שליפה נוספת כאן הייתה
    # פנייה שלמה למסד בשביל נתון שכבר ביד.
    profile = next((m for m in members if m["id"] == user["id"]), None)
    if profile is None:
        # אין משפחה, או שהחבר לא חזר מהרשימה — נופלים לשליפה הישירה
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
        # תוקף קוד ההזמנה. בלי זה אין שום דרך לדעת שהקוד שאתה עומד
        # לשלוח בוואטסאפ כבר לא יעבוד.
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
        # בחירת צבע לבן משפחה (סבב 6, פריט 13)
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
    # ריק מותר כאן (השדה אינו חובה בעריכה), אבל מה שהוקלד חייב להיות מספר
    if phone and not _looks_like_phone(phone):
        return jsonify({"error": "מספר הטלפון אינו תקין — למשל 050-1234567"}), 422

    old_profile = db.get_profile(user["id"]) or {}
    old_workplace = old_profile.get("workplace") or ""
    # הטלפון עבר ל"חשבון" (מתן, 1.10) ולא נשלח מכאן יותר. בלי זה כל שמירה
    # של שם או מקום עבודה הייתה מוחקת אותו — ואיתו את ההתחברות בטלפון.
    if "phone" not in body:
        phone = old_profile.get("phone") or ""

    full_name = f"{first_name} {last_name}"
    ok, err = db.update_profile(user["id"], full_name, phone or None, workplace or None)
    if not ok:
        return jsonify({"error": _user_message(err, "עדכון הפרטים נכשל")}), 500

    # מקום עבודה השתנה בפועל וסופק סקופ — מיישמים על היסטוריית עסקאות המשכורת
    if workplace_scope and workplace != old_workplace and user["family_id"]:
        db.update_workplace_history(
            user["id"], user["family_id"], workplace or None, old_workplace or None,
            apply_to_all=(workplace_scope == "all"),
        )

    # השם הפרטי מוצג בכל האתר (ברכות, עסקאות וכו') — לעדכן גם בסשן
    session["user_name"] = db.first_name(full_name)
    return jsonify({"status": "ok", "full_name": full_name, "first_name": first_name,
                    "last_name": last_name, "phone": phone, "workplace": workplace})


@app.route("/api/profile/password", methods=["PUT"])
@login_required
# מאמת את הסיסמה הנוכחית מול Supabase, כלומר אורקל לניחוש סיסמאות למי
# שהשיג סשן. ההגבלה על /login לא עוזרת כאן — זה מסלול אחר.
@limiter.limit("5 per minute")
def update_password():
    body             = request.get_json(silent=True) or {}
    current_password = body.get("current_password", "")
    password         = body.get("password", "")
    password_confirm = body.get("password_confirm", "")

    if not current_password:
        return jsonify({"error": "נא להזין את הסיסמה הנוכחית"}), 422
    if len(password) < passwords.MIN_LENGTH:
        return jsonify({"error": f"הסיסמה החדשה חייבת להכיל לפחות {passwords.MIN_LENGTH} תווים"}), 422
    if password != password_confirm:
        return jsonify({"error": "הסיסמאות החדשות אינן תואמות"}), 422
    if passwords.is_leaked(password):
        return jsonify({"error": passwords.LEAKED}), 422

    # מוודאים שהסיסמה הנוכחית נכונה לפני שמאפשרים להחליף אותה
    _, err = db.sign_in(_current_email()[0], current_password)
    if err:
        return jsonify({"error": "הסיסמה הנוכחית שגויה"}), 403

    ok, err = db.update_password(session.get("access_token"), password)
    if not ok:
        return jsonify({"error": _user_message(err, "עדכון הסיסמה נכשל")}), 500
    return jsonify({"status": "ok"})


# מתן (13.10): 10 דקות. ביומנים (1.11) הבדיקה עלתה ‎0.2-0.4‎ שניות כל פעם, והמייל
# כמעט לא משתנה — ושינוי מכאן ממילא נבדק בכל טעינה (‎pending_email‎). 12 שעות.
_EMAIL_FRESH_SECONDS = 12 * 3600


def _current_email(cached=False):
    """‎(email, pending_email)‎ מ-Supabase, ומעדכן את הסשן. בלי חיבור — המייל
    שבסשן, ובלי "ממתין". ראו ‎db.get_auth_email‎.

    ‎cached‎ — לעמוד ההגדרות (מתן, 13.10): הבדיקה מול שירות ההתחברות לקחה
    ‎100-230ms‎ מכל טעינה, אז היא נעשית לכל היותר פעם ב-10 דקות. אבל לא כשיש
    מייל שממתין לאישור — אז בודקים כל פעם, כדי שהאישור מהקישור יופיע מיד.
    פעולות שבודקות סיסמה מול המייל תמיד בודקות טרי."""
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
    """שינוי טלפון מתיבת "חשבון" (מתן, 1.10). הטלפון משמש גם להתחברות,
    אז ריק אינו מותר — כמו בהרשמה."""
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
# בודק סיסמה — אותו אורקל כמו שינוי סיסמה, ואותה הגבלה. וכל בקשה שולחת מייל.
@limiter.limit("5 per minute")
def update_email():
    """שינוי מייל (מתן, 1.10): מייל חדש + הסיסמה הנוכחית, ואז קישור אישור.
    בלי הסיסמה, מי שתופס טלפון פתוח מעביר את החשבון למייל שלו ומאפס סיסמה."""
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
    session["pending_email"] = new_email     # מכאן ההגדרות בודקות טרי עד האישור
    return jsonify({"status": "ok", "pending_email": new_email})


@app.route("/api/account/reset", methods=["POST"])
@login_required
# אורקל סיסמה, ובנוסף פעולה הרסנית: מוחקת את כל עסקאות המשפחה.
# מחמיר יותר — אין תרחיש לגיטימי של איפוס חוזר.
@limiter.limit("3 per hour")
def reset_account_route():
    """איפוס עסקאות: מוחק את כל העסקאות (הכנסות/הוצאות/חיסכון, כולל קבועות)
    לפי הבחירה — של כל המשפחה או רק של המשתמש. החשבון, הקטגוריות, הפרויקטים
    וההגדרות נשמרים. דורש אימות סיסמה. הנמחק מארוכב בארכיון הפנימי."""
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

    # איפוס כל המשפחה הוא הפעולה ההרסנית ביותר באפליקציה, והוא היה
    # פתוח לכל חבר עם הסיסמה של עצמו בלבד. "רק שלי" נשאר פתוח — זה
    # הכסף של מי שמבקש.
    if scope == "family":
        denied = _require_manager()
        if denied:
            return denied

    # ‎deleted‎ ולא ‎ok‎: אפס שורות הוא תוצאה תקינה (משפחה בלי עסקאות),
    # אז הכישלון נמדד לפי ‎err‎ בלבד. המספר מוחזר כדי שהמשתמש יראה מה
    # באמת קרה — זו הפעולה ההרסנית ביותר באפליקציה.
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
# אורקל סיסמה, והפעולה הכי הרסנית באפליקציה — מחיקת חשבון לצמיתות.
@limiter.limit("3 per hour")
def delete_account_route():
    """מחיקת חשבון לצמיתות. מבחינת המשתמש הכל נמחק והמייל/טלפון משתחררים;
    בפועל הנתונים מארוכבים לארכיון הפנימי של בעל האתר (ראה מיגרציית
    20260711100000). דורש אימות סיסמה נוכחית."""
    body     = request.get_json(silent=True) or {}
    password = body.get("password", "")

    if not password:
        return jsonify({"error": "נא להזין את הסיסמה הנוכחית"}), 422

    # אימות סיסמה — גם מגן ממחיקה בטעות וגם מרענן את הטוקן שאיתו נמחק
    response, err = db.sign_in(_current_email()[0], password)
    if err:
        return jsonify({"error": "הסיסמה שגויה"}), 403

    db.set_auth_token(response.session.access_token)
    ok, err = db.delete_my_account()
    if not ok:
        return jsonify({"error": "מחיקת החשבון נכשלה"}), 500

    session.clear()
    # מחיקת חשבון היא מחיקה — לא משאירים אחריה את המזהה ולא את סימון
    # המכשיר. מי שיפתח את הכתובת אחר כך הוא שוב אורח שרואה את דף הנחיתה.
    response = jsonify({"status": "ok"})
    response.delete_cookie(_LAST_ID_COOKIE, samesite="Lax", secure=not _IS_DEV)
    response.delete_cookie(_DEVICE_COOKIE,  samesite="Lax", secure=not _IS_DEV)
    return response


def _parse_amount(raw):
    """פרסינג בטוח של סכום עסקה: מספר סופי וחיובי בלבד.
    מחזיר (value, error) — error בעברית מוצג למשתמש כ-422."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, "הסכום חייב להיות מספר"
    if not math.isfinite(value) or value <= 0:
        return None, "הסכום חייב להיות מספר חיובי"
    # העמודה היא NUMERIC(10,2) ומתפוצצת מעל 99,999,999.99. בלי הבדיקה
    # הזאת הקלדה שגויה של תשע ספרות מגיעה ל-Postgres, נכשלת שם, וחוזרת
    # כ"הוספת העסקה נכשלה — נסו שוב" — כך שהמשתמש מנסו שוב את אותו קלט
    # ונכשל שוב, בלי שום רמז למה.
    if value > 99_999_999:
        return None, "הסכום גדול מדי"
    # מתחת לאגורה מתעגל ל-0.00 ואז נופל על ה-CHECK של העמודה, עם אותה
    # הודעה גנרית ואותו מבוי סתום
    if round(value, 2) <= 0:
        return None, "הסכום קטן מדי"
    return value, None


# כמה שנים אחורה וקדימה עסקה עדיין הגיונית. רחב בכוונה — אנשים מזינים
# היסטוריה מהעבר ומקדימים תשלומים לעתיד — וצר מספיק כדי לתפוס אסון.
_DATE_PAST_YEARS   = 10
_DATE_FUTURE_YEARS = 5


def _parse_date(raw):
    """פרסינג בטוח של תאריך עסקה. מחזיר (iso_string, error).

    ‎date‎ עבר עד היום מגוף הבקשה אל המסד בלי שום בדיקה, ולעמודה לא היה
    אילוץ. זה לא נשאר תיאורטי בגלל מנוע העסקאות הקבועות: תבנית עם
    תאריך התחלה בשנת 1000 גורמת לו לייצר מופע לכל חודש מאז, עד תקרת
    500 השורות — 500 עסקאות אמיתיות בקריאה אחת, שנכנסות לסיכומים
    ולהשוואת החודשים לתמיד.

    האילוץ במסד (‎transactions_date_sane‎) הוא הרשת האחרונה והוא רחב,
    כי CHECK חייב להיות immutable ולא יכול לדעת מה היום. כאן אפשר."""
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
    """שער לפעולות הרסניות. מחזיר ‎None‎ אם מותר, או תשובת שגיאה מוכנה.

    התפקיד נוסף כדי שמישהו יהיה אחראי, אבל נאכף רק על הסרת חבר — כך
    שכל מי שהצטרף עם קוד בן שישה תווים יכול היה למחוק את היסטוריית
    כל המשפחה, למחוק קטגוריות ולהחליף את קוד ההזמנה.

    הבדיקה נגזרת מ-‎auth.uid()‎ במסד ולא מהסשן, ולכן אי אפשר לזייף
    אותה מהלקוח. בכישלון שליפה מסרבים — "לא ידוע אם הוא מנהל" הוא
    לא "כן"."""
    try:
        if db.is_family_manager():
            return None
    except db.DataUnavailable:
        return jsonify({"error": "לא הצלחנו לאמת הרשאות — נסו שוב"}), 503
    return jsonify({
        "error": "רק מנהל המשפחה יכול לבצע את הפעולה הזאת.",
    }), 403


def _resolve_owner(body: dict, user: dict, tx_type: str, unchanged=None):
    """מי הבעלים של העסקה — לפי העדפות המשפחה: סוג שהשיוך כבוי בו נשמר
    תמיד כמשפחתי (NULL), גם אם הבקשה ניסתה לשלוח בעלים.
    ערכים: uuid של בן משפחה, "shared" = משותפת (NULL), ובלי owner — המחובר.
    מחזירה (user_id, error)."""
    if not family_settings().get("owner_attribution", {}).get(tx_type, False):
        return None, None
    owner = body.get("owner")
    if owner == "shared":
        return None, None
    # בעריכה: הבעלים שכבר רשום על העסקה נשאר מותר, גם אם כבר לא במשפחה
    # (עבר למשפחה אחרת בקוד הזמנה). אחרת הטופס נאלץ להחליף אותו בשקט.
    if owner and unchanged and owner == unchanged:
        return owner, None
    if owner:
        # ה-uuid הגיע מהלקוח והתקבל עד היום כמו שהוא. RLS בודקת רק את
        # family_id בשורה שנכתבת, לא את user_id, אז אפשר היה לרשום עסקה
        # על שם מי שלא במשפחה — או לתלות הוצאה על בן הזוג. זו לא דליפת
        # מידע אלא זיוף שיוך, וזה מספיק: הפילוח "לפי בן משפחה" מתבסס עליו.
        if owner not in {m["id"] for m in db.get_family_members(user["family_id"])}:
            return None, "בן המשפחה שנבחר אינו במשפחה שלך"
        return owner, None
    return user["id"], None


def _apply_project_assignment(body: dict, user: dict, tx_type: str, current=None):
    """מיישם שיוך לפרויקט (אם body['project_id'] נשלח): מוודא שהפרויקט
    עוקב אחרי סוג העסקה הזה, אוכף בשרת שיוך אוטומטי לבעלים בפרויקט אישי
    (מתעלם מ-body['owner'] במקרה הזה), ומחליף את הקטגוריה הרגילה בקטגוריית
    הפרויקט הייעודית. Returns (project_id, project_category_id, category_id, user_id, error)."""
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

    # פרויקט אישי: רק הבעלים רשאי לכתוב אליו. הקריאה כבר מוסתרת מאחרים
    # (‎_filter_hidden_personal_projects‎), אבל הכתיבה לא נבדקה — כך שבן
    # משפחה יכול היה לרשום עסקאות לתוך פרויקט שאסור לו אפילו לראות.
    if project.get("owner_id") and project["owner_id"] != user["id"]:
        return None, None, None, None, "הפרויקט לא נמצא"

    track_key = {"expense": "track_expense", "income": "track_income", "savings": "track_savings"}[tx_type]
    # "עוקב אחרי סוג" קובע מה אפשר **להוסיף** לפרויקט. עסקה שכבר בו נשארת —
    # אחרת עריכת הסכום שלה הוציאה אותה בשקט מהפרויקט אל הוצאות הבית.
    if not project.get(track_key) and project_id != current.get("project_id"):
        return None, None, None, None, "הפרויקט הזה לא עוקב אחרי המחלקה הזאת"

    owner_id = project.get("owner_id")
    if owner_id:
        user_id, owner_err = owner_id, None
    else:
        user_id, owner_err = _resolve_owner(body, user, tx_type, unchanged=current.get("user_id"))
    if owner_err:
        return None, None, None, None, owner_err

    # קטגוריית הפרויקט חייבת להיות של הפרויקט הזה, לא של אחר
    project_category_id = body.get("project_category_id")
    if not project_category_id:
        return None, None, None, None, "נא לבחור קטגוריה"
    valid = {c["id"] for c in db.get_project_categories(project_id, user["family_id"])}
    if project_category_id not in valid:
        return None, None, None, None, "הקטגוריה אינה שייכת לפרויקט הזה"

    return project_id, project_category_id, None, user_id, None


def _validated_category(category_id, user: dict, tx_type: str):
    """מוודא שהקטגוריה קיימת במשפחה ומתאימה לסוג העסקה.
    מחזירה (category_id, error). אין עסקה בלי קטגוריה — גם לא דרך ה-API."""
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

# כמה מופעים רטרואקטיביים מותר לייצר בלי שהמשתמש יאשר במפורש.
#
# ‎_parse_date‎ מתיר 10 שנים אחורה, ו-‎materialize_recurring‎ רץ מיד אחרי
# כל שמירה של תבנית. טעות הקלדה — ‎2016‎ במקום ‎2026‎ על שכר דירה של
# ₪6,000 — ייצרה ‎~120‎ עסקאות אמיתיות בבקשה אחת, בלי אישור ובלי שום
# מספר בתשובה. הן נכנסו לארכיון החודשים, לגרף המגמה ולייצוא ה-CSV,
# והדרך היחידה החוצה היא "מחק את זו וכל הבאות" מהמוקדמת ביותר — משהו
# שצריך לגלות לבד.
#
# שלושה הוא מספר שמכסה מילוי אחורה סביר ("שכחתי להזין את שלושת החודשים
# האחרונים") ועוצר כל דבר שנראה כמו תאונה.
_RETRO_WITHOUT_CONFIRM = 3


def _retro_occurrences(body, tx_date, already=None):
    """כמה מופעים ייווצרו רטרואקטיבית עבור התבנית שנשמרת עכשיו.

    ‎already‎: התאריכים שהסדרה כבר תפסה (בעריכה — ‎db.existing_occurrence_dates‎).
    תקופה תפוסה לא נספרת, בדיוק כמו שהמנוע לא ייצר אותה."""
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
        # ספירה שנכשלה לא חוסמת שמירה לגיטימית; היא רק לא מגינה.
        logger.exception("_retro_occurrences")
        return 0


# התדירויות שהמנוע יודע לייצר. ‎NULL‎ עבר את ה-CHECK של המסד, אז
# ‎{"is_recurring": true}‎ בלי תדירות יצר תבנית חיה ש-‎_recurring_occurrences‎
# מפרשת כ-‎monthly_1‎ ומייצרת ממנה שורות ב-1 לכל חודש — בזמן שבורר
# התדירות בממשק מציג ריק. מחרוזת לא מוכרת נעצרה רק ב-CHECK וחזרה כ-500.
_FREQUENCIES = ("monthly_same", "monthly_1", "monthly_15", "weekly", "biweekly")


def _validated_frequency(body):
    """מחזירה ‎(frequency, error)‎. רלוונטי רק לעסקה שסומנה קבועה."""
    if not body.get("is_recurring"):
        return None, None
    freq = body.get("recurring_frequency")
    if freq not in _FREQUENCIES:
        return None, "יש לבחור כל כמה זמן העסקה הקבועה נרשמת"
    return freq, None


# אין כאן תרחיש אנושי שמתקרב לגבול; הוא קיים כדי שסקריפט לא יוכל
# למלא את המסד. כל מחיקה מייצרת גם שורת ‎owner_archive‎ דרך טריגר,
# אז "הוסף-מחק-חזור" היה לולאה שמגדילה מסד של 500MB בלי שום ניקוי.
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


    # תשלומים (מתן, 12.10): הסכום הוא המחיר המלא, והשרת מחלק — עסקה קבועה
    # חודשית עם תאריך סיום. ראו ‎db.installment_plan‎.
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

    # ראו ‎_RETRO_WITHOUT_CONFIRM‎: סדרה שתייצר עשרות שורות אחורה היא
    # כמעט תמיד טעות הקלדה בתאריך, ולא בקשה.
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

    # הכנסת משכורת חדשה: מתעדים ("מקפיאים") את מקום העבודה הנוכחי של הבעלים
    # על העסקה עצמה, כדי ששינוי מקום עבודה עתידי לא ישנה בשקט את מה שכבר
    # נוצר — ראה update_workplace_history לזרימה של שינוי מקום עבודה בפועל.
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
    # קבלה מצורפת אפשרית רק בהוצאות; ריק = לא נוגעים בעמודה (לא מוחקים קבלה קיימת בעריכה)
    if tx_type == "expense" and body.get("receipt_path"):
        if not _receipt_path_ok(body["receipt_path"], user["family_id"]):
            return jsonify({"error": "הקבלה המצורפת אינה תקינה — נסו לצלם שוב"}), 422
        payload["receipt_path"] = body["receipt_path"]

    result, err = db.add_transaction(payload)
    if err:
        logger.error("add_transaction route: %s", err)
        return jsonify({"error": "הוספת העסקה נכשלה — נסו שוב"}), 500

    # עסקה קבועה חדשה (גם רטרואקטיבית) — משלימים מיד את כל המופעים עד היום
    if payload["is_recurring"]:
        db.materialize_recurring(user["family_id"])   # (created, ok) — כאן לא נדרש

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
    """"המשכורת נרשמה" (מתן, 31.10) — לשאר בני המשפחה, פעם אחת בחודש של
    העסקה: ‎push_family_targets‎ רושמת את החודש ולא מחזירה כלום בפעם השנייה."""
    try:
        when = date.fromisoformat(str(payload["date"])[:10])
        targets = db.push_family_targets("salary", f"{when.year}-{when.month:02d}")
        push.send_in_background(targets, push.salary(payload["amount"], when.month))
    except Exception:
        logger.exception("push salary")


def _budget_note(family_id, payload):
    """"עברתם את התקציב של סופר ומזון ב-₪120" — אחרי הוספת הוצאה שחוצה את
    התקציב של הקטגוריה שלה בחודש שלה (מתן, 3.10 — רעיון 17). רק הוצאות בית:
    כסף של פרויקט לא נספר בתקציבי הבית. מידע ולא חסימה — תקלה כאן לא
    מכשילה את ההוספה, היא רק מוותרת על ההערה."""
    if payload.get("type") != "expense" or payload.get("project_id") or not payload.get("category_id"):
        return None
    budget = db.category_budget(family_settings(), payload["category_id"])
    if not budget:
        return None
    d = str(payload["date"])[:10]           # ‎_parse_date‎ מחזיר מחרוזת ISO
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
    """השדות שעריכה יכולה לשנות — זהים למה שהלקוח ראה אחרי השמירה שלו?"""
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
    # "בטל" אחרי עריכה (מתן, 30.9 — סבב 6, פריט 10): רק אם העסקה עדיין כמו
    # שהעריכה שלנו השאירה אותה. אחרת מישהו שינה אותה מאז — לא דורסים אותו.
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

    # ראו ‎_RETRO_WITHOUT_CONFIRM‎: סדרה שתייצר עשרות שורות אחורה היא
    # כמעט תמיד טעות הקלדה בתאריך, ולא בקשה. בעריכה נספרות רק תקופות
    # שהסדרה עוד לא תפסה — אחרת כל תיקון בתבנית ותיקה הקפיץ את השאלה.
    already = None
    if body.get("is_recurring") and not request.args.get("confirm"):
        try:
            already = db.existing_occurrence_dates(tx_id, user["family_id"])
        except db.DataUnavailable:
            # לא ידוע כמה ייווצרו. לשמור בלי לשאול זה בדיוק מה שהשאלה מונעת.
            return jsonify({"error": "לא הצלחנו לבדוק את העסקה הקבועה — נסו שוב"}), 503
    retro = _retro_occurrences(body, tx_date, already)
    if retro > _RETRO_WITHOUT_CONFIRM and not request.args.get("confirm"):
        return jsonify({
            "needs_confirm": True,
            "will_create": retro,
            "error": f"העסקה הקבועה הזאת תיצור {retro} עסקאות אחורה, מ-{tx_date}. "
                     f"אם זה מה שרציתם — אשרו.",
        }), 409

    # מופע של סדרה לא יכול להפוך לתבנית בפני עצמה. אין אילוץ במסד שמונע
    # את זה, והתוצאה היא שתי סדרות מקבילות שמייצרות את אותו כסף פעמיים
    # בכל חודש, לתמיד. מי שרוצה לשנות את הסדרה עושה זאת מהתבנית עצמה.
    if bool(body.get("is_recurring", False)):
        try:
            if db.is_recurring_instance(tx_id, user["family_id"]):
                return jsonify({
                    "error": "זו עסקה קבועה. כדי לשנות אותה מהחודש הנוכחי והלאה, "
                             "ערכו אותה דרך ההגדרות ← עסקאות קבועות.",
                }), 422
        except db.DataUnavailable:
            # לא ידוע. סדרה כפולה היא נזק שאי אפשר לבטל; ניסיון חוזר לא.
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
    # קבלה מצורפת אפשרית רק בהוצאות; ריק = לא נוגעים בעמודה (לא מוחקים קבלה קיימת בעריכה)
    if tx_type == "expense" and body.get("receipt_path"):
        if not _receipt_path_ok(body["receipt_path"], user["family_id"]):
            return jsonify({"error": "הקבלה המצורפת אינה תקינה — נסו לצלם שוב"}), 422
        payload["receipt_path"] = body["receipt_path"]

    # עריכה מ"עסקאות קבועות" בהגדרות: מהחודש הנוכחי והלאה (מתן, 2.10).
    # שינוי תאריך ההתחלה הוא תיקון של הסדרה עצמה — הוא נשאר עריכה רגילה.
    if (request.args.get("from_now") and payload["is_recurring"]
            and str(tx_date) == str(current.get("date"))[:10]):
        series_reply = _edit_series_from_now(tx_id, user["family_id"], payload)
        if series_reply is not None:
            return series_reply

    result, err = db.update_transaction(tx_id, user["family_id"], payload)
    if err:
        logger.error("update_transaction route: %s", err)
        return jsonify({"error": "עדכון העסקה נכשל — נסו שוב"}), 500
    # אף שורה לא נגעה: העסקה נמחקה בינתיים על ידי בן משפחה אחר, או
    # שהמזהה שייך למשפחה אחרת. עד היום זה חזר כ-200 "נשמר", והמשתמש
    # האמין שהעריכה שלו נקלטה.
    if not result:
        return jsonify({"error": "העסקה לא נמצאה — ייתכן שנמחקה בינתיים"}), 404

    if payload["is_recurring"]:
        db.materialize_recurring(user["family_id"])   # (created, ok) — כאן לא נדרש

    return jsonify({"status": "ok", "transaction": result})


def _edit_series_from_now(template_id, family_id, payload):
    """עריכת עסקה קבועה "מעכשיו": השינוי נכתב על המופע של החודש הנוכחי,
    והסדרה מתפצלת בו — אותו מנגנון כמו "עדכון להבא" (‎split_recurring_series‎).
    ינואר עד החודש הקודם נשארים כמו שהם.

    ‎None‎ = אין עבר להגן עליו (הסדרה התחילה החודש, או נגמרה) — עורכים את
    התבנית כרגיל. אחרת — התשובה למסלול."""
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

    # 1. המופע מקבל את הערכים החדשים — בלי התאריך שלו ובלי קבלה של התבנית
    occurrence = {k: v for k, v in payload.items()
                  if k not in ("date", "is_recurring", "recurring_frequency",
                               "recurring_end_date", "receipt_path")}
    result, err = db.update_transaction(pivot_id, family_id, occurrence)
    if err or not result:
        logger.error("edit series from now: update occurrence: %s", err)
        return jsonify({"error": "עדכון העסקה הקבועה נכשל — נסו שוב"}), 500
    # 2. הסדרה מתפצלת בו: הישנה נגמרת יום לפני, והוא התבנית של החדשה
    new_id, err = db.split_recurring_series(template_id, pivot_id, family_id)
    if err or not new_id:
        logger.error("edit series from now: split: %s", err)
        return jsonify({"error": "עדכון העסקה הקבועה נכשל — נסו שוב"}), 500
    # 3. ותדירות / תאריך סיום, אם שונו (הפיצול העתיק את של הישנה)
    _, err = db.update_transaction(new_id, family_id, {
        "recurring_frequency": payload["recurring_frequency"],
        "recurring_end_date":  payload["recurring_end_date"],
    })
    if err:
        logger.error("edit series from now: frequency: %s", err)
    db.materialize_recurring(family_id)
    return jsonify({"status": "ok", "transaction": result, "series_from": pivot_date})


def _entered_text(meta: dict, viewer_id: str, names: dict, now=None) -> str:
    """"הוזנה ע״י אור · אתמול 18:32" — ראו ‎/api/transactions/<id>/meta‎.
    עסקה בלשון נקבה ("הוזנה"), כמו "נרשמה ע״י" וחלון הכפילות."""
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
    """מי הזין ומתי — השורה בתחתית חלון העריכה (מתן, 3.10 — רעיון 22). לא
    נכנסת לכל שורה בכל רשימה: נשלפת כשהחלון נפתח."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלכם — רעננו את הדף"}), 400
    try:
        meta = db.transaction_meta(tx_id, user["family_id"])
    except db.DataUnavailable:
        return jsonify({"error": "לא הצלחנו לטעון — נסו שוב"}), 503
    if meta is None:
        return jsonify({"error": "העסקה לא נמצאה"}), 404
    # שמות בני המשפחה רק כשמישהו אחר הזין — ברוב הפעמים זו עסקה שלך
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
    """מחיקת עסקה — ועסקה קבועה היא לא עסקה אחת.

    השאלה היא בדיוק זו של יומן: "רק את זו" או "את זו וכל הבאות". אין
    כאן הבחנה בין "תבנית" למופע — היא פנימית לגמרי, ולמי שמוחק את שכר
    הדירה של מרץ לא אמור להיות אכפת אם מרץ הוא במקרה החודש שבו הסדרה
    נפתחה.

    בלי ‎mode‎ מוחזר 409 עם מספר המופעים שיימחקו ב"כל הבאות", כדי
    שהשאלה תוצג עם המספר האמיתי — אותו דפוס של ‎/api/family/join‎."""
    user = get_current_user()
    mode = request.args.get("mode")

    try:
        occurrence, _ = db.recurring_occurrence(tx_id, user["family_id"])
    except db.DataUnavailable:
        # לא ידוע אם יש סדרה מאחורי השורה. מחיקה עיוורת כאן היא בדיוק
        # הנזק שאי אפשר לבטל, אז מסרבים.
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
    """עוצר סדרה קבועה. במכוון לא מוחק את השורה: היא המופע הראשון בסדרה,
    כלומר כסף שבאמת זז, והדיאלוג בהגדרות מבטיח שמה שכבר נוצר יישאר.
    מי שרוצה למחוק את העסקה עצמה עושה זאת מרשימת העסקאות כמו בכל עסקה."""
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
    """"דלג על החודש הזה" על מופע צפוי בחודש עתידי (מתן, 1.11), ו"בטל"."""
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
    """"עדכן להבא": מפצל את הסדרה במופע שנערך (ראו ‎db.split_recurring_series‎).

    המסלול כבר לא כותב סכום, קטגוריה או תיאור. מסלול העריכה הרגיל שמר
    אותם על המופע רגע קודם — עם כל הבדיקות שלו — והפיצול רק הופך את
    המופע הזה לתבנית. זה גם סוגר את מה שהיה כאן: מסלול כתיבה שני, עם
    ולידציה משלו, לאותם שדות."""
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


# ─── API: Receipt scanning (צילום קבלה) ───────────────────────────────────────

@app.route("/api/receipts/discard", methods=["POST"])
@limiter.limit("30 per minute")
@login_required
def discard_receipt():
    """מוחק תמונה שנסרקה ולא נשמרה עם עסקה.

    הסריקה מעלה את התמונה **לפני** שיש עסקה. סריקה שננטשה — ✕ בלי שמירה,
    סריקה שנייה באותו טופס, או תוצאה שחזרה לטופס שכבר נסגר — השאירה קובץ
    שאף שורה לא מצביעה עליו. הניקוי הלילי שהיה אמור לאסוף אותם נכשל בכל
    ריצה (ראו ‎db.delete_receipts‎), אז הדפדפן מבקש למחוק ברגע הנטישה.

    רק בתיקייה של המשפחה, ורק קובץ שאף עסקה לא מצביעה עליו: אחרת זו
    דרך למחוק קבלה של עסקה קיימת."""
    user = get_current_user()
    path = (request.get_json(silent=True) or {}).get("path")
    if not _receipt_path_ok(path, user["family_id"]):
        return jsonify({"error": "הקובץ לא נמצא"}), 404
    if db.receipt_in_use(path, user["family_id"]):
        return jsonify({"error": "הקבלה מצורפת לעסקה"}), 409
    db.delete_receipt(session.get("access_token"), path)
    return jsonify({"status": "ok"})


@app.route("/api/receipts/scan", methods=["POST"])
# המסלול היחיד כאן שעולה כסף אמיתי, והיחיד מבין 23 המסלולים הכותבים
# שלא הייתה עליו שום הגבלה. סריקה אחת לוקחת 2-6 שניות, אז עשר לדקה
# לא נוגעות באף אדם — הן נוגעות בסקריפט.
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
        # לא ידוע כמה נוצל. הכיוון הבטוח הוא לסרב: אישור כשהבדיקה נכשלה
        # מבטל בפועל את ההגבלה על חשבון ה-OpenAI, וזו הוצאה אמיתית.
        return jsonify({
            "error": "לא הצלחנו לבדוק את מכסת הסריקות — נסו שוב, או הזינו ידנית.",
        }), 503

    # התקרה הגלובלית נבדקת לפני זו של המשפחה: כשהיא נחצית זו לא אשמתו
    # של מי שעומד כאן, וההודעה צריכה לומר את זה ולא להאשים אותו במכסה
    # שהוא לא ניצל.
    if used_globally >= db.RECEIPT_GLOBAL_MONTHLY_LIMIT:
        logger.error("סריקות קבלות: נחצתה התקרה הגלובלית (%s)", used_globally)
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

    # ולידציית סוג הקובץ לפני קריאה/אחסון — אותו whitelist של הסריקה עצמה
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

    # סריקה מוצלחת: נספרת במכסה גם אם העלאת התמונה לאחסון נכשלה
    db.record_receipt_scan(user["family_id"], user["id"])

    receipt_path, upload_err = db.upload_receipt(
        session.get("access_token"), user["family_id"], image_bytes, file.mimetype or "image/jpeg"
    )
    if upload_err:
        # השגיאה נזרקה לפח לגמרי: לא נרשמה, לא הגיעה ל-Sentry, והמכסה
        # כבר נספרה. המשתמש רואה סריקה מוצלחת בלי תג קבלה ואין שום
        # עקבה שמסבירה למה. הקריאה ל-OpenAI כבר שולמה, אז לא מבטלים —
        # רק אומרים.
        logger.error("upload_receipt נכשל אחרי סריקה מוצלחת: %s", upload_err)
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
    """מפנה לקבלה המצורפת לעסקה.

    הפניה ולא JSON, וזו הנקודה כולה: קודם הסמל שלף את הכתובת ב-fetch ואז
    קרא ל-window.open. ב-iOS זה נחסם תמיד — הדפדפן מתיר פתיחת חלון רק
    כתוצאה ישירה מלחיצה, וההמתנה לשרת מבטלת את הקשר. אין חלון, אין שגיאה,
    לא קורה כלום. בפועל הקבלות שנסרקו לא היו נגישות מהטלפון בכלל.

    עכשיו הסמל הוא קישור רגיל, והדפדפן מנווט בעצמו — ניווט שנובע מלחיצה
    לא נחסם. הכתובת החתומה נוצרת כאן ולא נשלחת ללקוח מראש, כך שהיא גם לא
    יושבת ב-HTML של כל שורה עם קבלה.

    ‎/receipts/‎ ולא ‎/api/receipts/‎ כי זה ניווט של הדפדפן: תחת ‎/api/‎ סשן
    שפג היה מחזיר 401 JSON ללשונית חדשה, במקום להעביר להתחברות."""
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
    # הכתובת החתומה קצרת-מועד ואישית; אסור שתישמר במטמון של proxy
    response.headers["Cache-Control"] = "no-store"
    return response


# ─── ייבוא מחברת האשראי (מתן, 31.10) ─────────────────────────────────────────
#
# פעם בחודש: מורידים מהאתר של חברת האשראי את פירוט העסקאות, מעלים אותו כאן,
# רואים טבלה עם קטגוריה מוצעת לכל עסקה וסימון של מה שכבר קיים — ומאשרים.
# שני שלבים ושני מסלולים: ‎preview‎ קורא את הקובץ ולא כותב כלום, ‎commit‎
# מקבל רק את השורות שאושרו. הקובץ עצמו לא נשמר בשום מקום.

def _expense_categories(family_id):
    return [c for c in db.get_categories(family_id) if c.get("type") == "expense"]


def _json_form_field(name, kind):
    """שדה JSON בטופס multipart — או ‎None‎ כשהוא חסר או שבור."""
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
# קורא ל-OpenAI כשהקובץ לא מוכר וכדי לנחש קטגוריות לבתי עסק חדשים — כסף
# אמיתי, כמו סריקת קבלה. ייבוא אמיתי הוא פעם בחודש; עשר לדקה הן לניסיונות
# חוזרים עם קובץ אחר, ושישים ביום — תקרה לסקריפט.
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
    chosen = _json_form_field("mapping", dict)       # בחירה ידנית (שכבה 3)
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
                # שכבה 3: המשתמש בוחר בעצמו איזו עמודה היא מה
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
        # בלי בדיקת כפילויות כל עסקה שכבר הוזנה ידנית הייתה נכנסת פעמיים
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
    """הוראות קבע (מתן, 1.11): מציעים להפוך לעסקה קבועה חודשית — ולא חוזרים
    עליהן בכל ייבוא. רק לחיוב האחרון של כל בית עסק בקובץ (קוסטה ג'ים חויב
    ‎₪255‎ באוגוסט ו-‎₪179‎ בספטמבר — סדרה אחת, בסכום העדכני), ולא כשכבר יש
    במשפחה סדרה פעילה לאותו בית עסק."""
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
            # אותם מפתחות בכל השורות: PostgREST מכניס מערך רק כשלכולן אותן עמודות
            "recurring_frequency": None,
            "recurring_end_date":  None,
        })
        # הוראת קבע שאושרה כעסקה קבועה: תבנית חודשית באותו יום בחודש, כמו
        # עסקה קבועה שמוזנת ידנית. החודשים הבאים נוצרים מעצמם.
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


# ─── התראות לטלפון (מתן, 31.10) ───────────────────────────────────────────────
#
# ‎backend/push.py‎ — ההצפנה והנוסחים. כאן: רישום המכשיר, ביטולו, והריצה
# היומית ש-‎pg_cron‎ מפעיל (חודש נסגר, תשלום אחרון, שבוע בלי רישום).

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
    """‎pg_cron‎ קורא לכאן פעם ביום. בלי הסוד הנכון — 404, כאילו אין כאן כלום.
    הסוד עובר גם למסד: ‎push_daily‎ בודקת אותו מול ה-Vault, כך שגם קריאה
    ישירה ל-RPC בלי הסוד לא מחזירה דבר."""
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
    logger.info("push daily: %s התראות, %s מכשירים שנמחקו", sent, len(dead))
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

    # שני השדות עברו ישירות למסד. ‎name‎ הוא ‎TEXT NOT NULL‎ שמקבל
    # מחרוזת ריקה, אז נוצרה קטגוריה בלי שם — שורה בפילוח החודשי שאי
    # אפשר לזהות ואי אפשר לחפש. ו-‎type‎ לא מוכר נעצר רק ב-CHECK של
    # המסד וחזר כ-500 סתום, אחרי שכל מסלול כתיבה אחר כבר מאמת אותו.
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
    """קטגוריות ברירת המחדל שאין למשפחה (לפי שם ומחלקה), ומה שכבר יש."""
    existing = [c for c in db.get_categories(family_id) if c.get("family_id") == family_id]
    have = {_category_key(c.get("name"), c.get("type")) for c in existing}
    missing = [dict(c) for c in _DEFAULT_CATEGORIES if _category_key(c["name"], c["type"]) not in have]
    return missing, existing


@app.route("/api/categories/defaults", methods=["GET"])
@login_required
def missing_default_categories_route():
    """מה יתווסף בשחזור ברירת המחדל — כדי שהאישור יגיד את זה בשמות."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא מצאנו את המשפחה שלך — רעננו את הדף"}), 400
    missing, _ = _missing_default_categories(user["family_id"])
    return jsonify({"missing": missing})


@app.route("/api/categories/defaults", methods=["POST"])
@limiter.limit("10 per minute")
@login_required
def restore_default_categories_route():
    """מחזיר את קטגוריות ברירת המחדל שחסרות (מתן, 31.10 — "למי שמחק בטעות").

    רק מוסיף: קטגוריה קיימת לא משתנה ולא נמחקת, ועסקאות לא זזות — ולכן
    זה פתוח לכל בן משפחה, כמו הוספת קטגוריה. מה שכבר קיים באותו שם ובאותה
    מחלקה לא נוסף שוב, כך שלחיצה כפולה לא יוצרת כפילויות."""
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
    """התוכנית של ‎db.category_deletion_plan‎ ועוד ‎blocked‎ — למה אי אפשר
    למחוק בכלל, או ‎None‎.

    הקטגוריה האחרונה מסוג מסוים נשארת: בלעדיה אי אפשר לרשום עסקה מהסוג
    הזה, ועסקאות שכבר בה אין לאן להעביר. בפרויקט זה חל רק על סוג שהפרויקט
    עוקב אחריו, או כשיש בה עסקאות — קטגוריית הכנסה ריקה בפרויקט שלא
    עוקב אחרי הכנסות אפשר למחוק."""
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
    """מחיקה עם העברה: עסקאות הקטגוריה עוברות ל-‎?move_to=‎ לפני המחיקה.
    בלי יעד תקין כשיש עסקאות — 409, ושום דבר לא נוגע."""
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
        # גם מירוץ נוחת כאן: עסקה שנוספה לקטגוריה אחרי ההעברה מכשילה את
        # המחיקה באילוץ של המסד, והעסקאות שכבר הועברו נשארות ביעד
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
    """עדכון העדפות המשפחה. מקבל עדכון חלקי וממזג לתוך הקיים."""
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

    # תקציבי קטגוריות. מגיעים מהלקוח, אז נאמתים כמו כל סכום כסף אחר,
    # ונבדקים שהם באמת קטגוריות של המשפחה הזאת.
    if isinstance(body.get("limits"), dict):
        family_categories = {c["id"] for c in db.get_categories(user["family_id"])}
        limits = dict((family_settings().get("limits") or {}))

        for cat_id, entry in body["limits"].items():
            if cat_id not in family_categories:
                return jsonify({"error": "קטגוריה לא נמצאה"}), 422

            # ‎null‎ או סכום 0 = הסרת התקציב. זו הדרך לבטל, ולא מחיקה
            # נפרדת — כך "לא להגדיר תקציב" הוא אותו מסלול כמו "להסיר".
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

        # המפה המלאה, כי הסרה היא היעדרות ממנה. ‎_merge_settings‎ מחליפה
        # את ‎limits‎ במלואה ולא ממזגת אותה — ראו ‎_WHOLE_MAP_KEYS‎. לפני כן
        # היא מוזגה ב-‎.update()‎, שיכולה רק להוסיף, ולכן הסרה לא נשמרה.
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
    """בחירת צבע לבן משפחה (מתן, 30.9 — סבב 6, פריט 13). כל אחד לעצמו בלבד —
    גם מנהל המשפחה לא בוחר לאחרים (מתן). צבע של מישהו אחר — תפוס."""
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
    """מחזיר הודעה שמתאימה להצגה למשתמש.

    שכבת ה-DB מחזירה שני סוגי מחרוזות באותו מקום: הודעות שנכתבו למשתמש
    ("רק הבעלים של הפרויקט יכול…") וסימנים פנימיים שנכתבו למפתח
    ("Database not configured", או ‎str(e)‎ גולמי מ-Supabase). המסלולים
    הציגו את שתיהן כמו שהן.

    הכלל כאן פשוט ובכוונה: הודעה למשתמש נכתבת בעברית. מחרוזת בלי עברית
    היא פנימית — היא נכנסת ללוג ולא למסך. כך גם שגיאה עתידית שתיווסף
    בשכבת ה-DB לא תדלוף לממשק בלי שאף אחד שם לב."""
    if err and re.search(r"[\u0590-\u05FF]", str(err)):
        return str(err)
    if err:
        logger.warning("internal error surfaced to a route: %s", err)
    return fallback


def _family_rpc_message(err: str) -> str:
    """מתרגם את שגיאות ה-RPC להודעה בעברית. ההודעות המקוריות באנגלית
    ומיועדות ללוג, לא למשתמש."""
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
    """"למחוק" או "להשאיר כמשותפים" — ומה שלא נבחר עדיין הוא שאלה."""
    choice = body.get("projects")
    return choice if choice in ("share", "delete") else "ask"


def _needs_project_choice(err):
    """409 עם המספר, כדי שהמסך ישאל — אותו דפוס כמו ‎/api/family/join‎."""
    count = db.project_choice_needed(err)
    if not count:
        return None
    return jsonify({"needs_choice": True, "project_count": count}), 409


@app.route("/api/family/members/<member_id>", methods=["DELETE"])
@login_required
@limiter.limit("10 per minute")
def remove_family_member_route(member_id):
    """הסרת בן משפחה. מנהל המשפחה בלבד — נאכף ב-DB.

    keep_transactions הוא בחירה של המשתמש ולא ברירת מחדל שקטה: הכסף יצא
    מהתקציב המשותף, אז "להשאיר" שומר על הסכומים החודשיים נכונים והעסקה
    מוצגת כ"משותפת". "למחוק" משנה למפרע כל סיכום חודשי שבו הוא מופיע,
    ולכן הוא חייב להיות בחירה מודעת."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא משויכת משפחה לחשבון"}), 400

    body = request.get_json(silent=True) or {}
    keep = body.get("keep_transactions", True) is not False

    # הפרויקטים האישיים של מי שמוסר נמחקים תמיד, עם העסקאות שבהם (החלטת
    # מתן, 28.9.2026): המנהל לא רואה אותם, ואסור שיקבל לידיו דברים פרטיים של
    # מי שהוסר. לא נקרא מהגוף — מי שעוזב בעצמו הוא היחיד שנשאל.
    # הקבלות של כל מה שיימחק — הפרויקטים האישיים שלו תמיד, והעסקאות שלו
    # אם נבחר למחוק אותן. המנהל נשאר במשפחה, אז מותר לו למחוק אחרי.
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
    """עזיבת המשפחה. כל בן משפחה רשאי, כולל המנהל — כשהוא עוזב הניהול
    עובר לוותיק שנשאר, כדי שלא תיוותר משפחה בלי מנהל."""
    user = get_current_user()
    if not user["family_id"]:
        return jsonify({"error": "לא משויכת משפחה לחשבון"}), 400

    body = request.get_json(silent=True) or {}
    keep = body.get("keep_transactions", True) is not False

    choice = _project_choice(body)
    # מי שעוזב מאבד את הרשאת האחסון של המשפחה ברגע שיצא, אז את הקבלות של מה
    # שיימחק מוחקים **לפני** — ולכן קודם צריך לדעת בוודאות מה יימחק. אם יש
    # פרויקטים אישיים ועוד לא נבחר מה לעשות בהם, שואלים לפני שנוגעים במשהו.
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

    # ה-session נושא את המשפחה הישנה; בלי העדכון הזה כל שליפה בבקשה הבאה
    # תסונן לפי משפחה שהמשתמש כבר לא חבר בה, והאפליקציה תיראה ריקה
    session["family_id"] = new_family_id
    return jsonify({"status": "ok"})


@app.route("/api/family/invite-code", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def rotate_invite_code_route():
    """החלפת קוד ההזמנה. הקוד הישן מפסיק לעבוד מיד — זו כל המטרה."""
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
    """תצוגה מקדימה לפני הצטרפות — "מצטרפים למשפחת כהן?". מוגבל בקצב כדי
    שלא ישמש לסריקת קודים."""
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
    """הצטרפות למשפחה קיימת אחרי ההרשמה.

    עד היום רגע ההרשמה היה ההזדמנות היחידה בכל חיי המוצר להזין קוד, ומי
    שנרשם לפני שקיבל אותו נשאר תקוע במשפחה משלו בלי דרך חזרה."""
    user = get_current_user()
    body = request.get_json(silent=True) or {}
    code = _text(body.get("code"))
    if not code:
        return jsonify({"error": "לא הוזן קוד הזמנה"}), 422

    # מעבר למשפחה אחרת נוטש את התנועות הקיימות — הן נשארות קשורות למשפחה
    # הישנה. למשתמש חדש זה לא מזיק; למשתמש עם היסטוריה זה הרסני, ולכן
    # דורשים אישור מפורש ומחזירים את המספר כדי שה-UI יוכל להציג אותו.
    current_family = user.get("family_id")
    if current_family and not body.get("confirm"):
        try:
            existing = db.family_transaction_count(current_family)
        except db.DataUnavailable:
            # לא ידוע כמה היסטוריה תיזנח. דילוג על האישור בגלל תקלה הוא
            # בדיוק האובדן שהאישור קיים כדי למנוע, אז מבקשים אותו בכל מקרה.
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

    # בלי עדכון ה-session המשתמש ימשיך לראות את המשפחה הישנה עד ליציאה וכניסה
    session["family_id"] = family_id
    return jsonify({"status": "ok", "family_id": family_id})


@app.route("/privacy")
def privacy():
    """מדיניות פרטיות — ציבורית בכוונה: מי ששוקל להירשם צריך לקרוא אותה
    לפני שהוא מוסר נתונים, לא אחרי."""
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


# וואטסאפ, האייפון ומנועי חיפוש מבקשים את אלה בכתובת קבועה, בלי לקרוא את
# ה-HTML — ובלעדיהם קישור לאפליקציה הופיע בלי הלוגו (‎404‎ ביומנים, 13.10).
@app.route("/favicon.ico")
@app.route("/apple-touch-icon.png")
@app.route("/apple-touch-icon-precomposed.png")
def touch_icon():
    response = app.send_static_file("icons/icon-180.png")
    response.headers["Cache-Control"] = "public, max-age=604800"
    return response


@app.route("/robots.txt")
def robots_txt():
    """רק ה-API והקבלות סגורים לסורקים. דפי האפליקציה ממילא דורשים התחברות,
    ודף הנחיתה וקישורי ההזמנה צריכים תצוגה מקדימה בשיתוף."""
    return app.response_class("User-agent: *\nDisallow: /api/\nDisallow: /receipts/\n",
                              mimetype="text/plain")


@app.route("/sw.js")
def service_worker():
    """מגיש את ה-Service Worker מהשורש כדי שה-scope שלו יכסה את כל האתר
    (רישום מ-/static/ נחסם על ידי הדפדפן)."""
    return app.send_static_file("sw.js")


# ─── Health checks ────────────────────────────────────────────────────────────
#
# שתי בדיקות, כי נשאלות כאן שתי שאלות שונות:
#
#   /health     — "התהליך חי?"  זו הבדיקה של Railway, והיא מחזירה 200 כל
#                 עוד השרת עונה. אילו היא הייתה נכשלת בגלל תקלה ב-Supabase,
#                 הפריסה הבאה הייתה נחסמת בדיוק ברגע הכי גרוע — תקלה של
#                 שתי דקות במסד הייתה הופכת להשבתה ארוכה בהרבה.
#   /health/db  — "המשתמשים יכולים באמת להשתמש באפליקציה?"  זו הבדיקה
#                 שמוניטור חיצוני צריך לעקוב אחריה, והיא מחזירה 503 כשהמסד
#                 לא עונה.
#
# קודם הייתה כאן רק הראשונה, והיא החזירה "ok" קבוע — כלומר גם כשהמסד היה
# מת. מוניטור היה מדווח שהכול תקין בזמן שאף משפחה לא רואה את הכסף שלה,
# והדרך היחידה לגלות הייתה שמישהו יטרח לספר.

_HEALTH_TTL = 5.0            # שניות
_health_cache = {"at": -_HEALTH_TTL, "ok": False, "detail": ""}


def _database_health():
    """מצב המסד, עם זיכרון של חמש שניות.

    בלי הזיכרון, מוניטור תכוף (או מישהו שמחזיק F5) היה תופס worker
    לכל בדיקה — ובדיוק בזמן תקלה, כשהבדיקות מתרבות והשרת עמוס, יש
    ארבעה workers בסך הכול."""
    now = time.monotonic()
    if now - _health_cache["at"] >= _HEALTH_TTL:
        ok, detail = db.ping()
        _health_cache.update(at=now, ok=ok, detail=detail)
        if not ok:
            # ‎error‎ ולא ‎warning‎: מסד שלא זמין הוא אירוע ב-Sentry. ברמת
            # ‎warning‎ הוא פירור, והדרך היחידה לגלות נפילה הייתה
            # שמשתמש יתלונן.
            logger.error("health: database unreachable (%s)", detail)
    return _health_cache["ok"], _health_cache["detail"]


@app.route("/health")
def health():
    ok, detail = _database_health()
    # תמיד 200 — אבל אומר את האמת בגוף התשובה.
    #
    # ‎rate_limiting‎ כאן כי אי אפשר היה לדעת אחרת: ההגבלה נחלשת בשקט
    # כשמשתנה הסביבה חסר או ש-Redis לא זמין, והבקשות ממשיכות לעבוד.
    # עכשיו זו שאלה של ‎curl‎ אחד.
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

# Content-Security-Policy: ההגנה החזקה ביותר מפני XSS — הדפדפן מסרב להריץ
# קוד שלא הגיע מהמקורות המותרים. אפשר היה להגדיר אותה רק אחרי שכל ה-JS
# הוצא מה-HTML לקבצים: מדיניות שמתירה 'unsafe-inline' מוותרת על רוב הערך,
# כי בדיוק כך נראית הזרקת קוד.
#
# script-src הוא 'self' בלבד — Chart.js הורד מקומית במקום להיטען מ-CDN.
# ל-style-src נדרש 'unsafe-inline' כי התבניות משתמשות ב-style="..." לערכים
# מחושבים (רוחב פסי התקדמות, צבע לכל בן משפחה); זו הרפיה מקובלת ומצומצמת,
# והיא לא מאפשרת הרצת קוד.
_CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    # גוגל הוסר משניהם: הפונט מתארח אצלנו, ומדיניות שמתירה מקורות שלא
    # בשימוש היא רק משטח תקיפה מיותר.
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    # + האחסון של Supabase: הקבלה נפתחת בתוך האפליקציה (‎receipt-viewer.js‎,
    # מתן 12.10), ו-‎/receipts/<id>‎ מפנה לכתובת חתומה שם. רק תמונות — לא סקריפטים.
    "img-src 'self' data: blob:" + (
        " " + re.match(r"https?://[^/]+", os.environ.get("SUPABASE_URL", "")).group(0)
        if re.match(r"https?://[^/]+", os.environ.get("SUPABASE_URL", "")) else ""),
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
])

# מצב הדיווח נשלט בסביבה כדי שאפשר יהיה להריץ קודם ב-Report-Only, לראות מה
# נשבר בפרודקשן האמיתי, ורק אז לאכוף. ברירת המחדל היא אכיפה.
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

    # מסלולי האימות מקבלים את הטופס עצמו בחזרה, עם ההודעה מעליו.
    # error.html הוא מבוי סתום — יש בו רק "חזור לדף הראשי" — ומי שנחסם
    # הגיע לשם בדיוק כי לחץ פעמיים על "התחבר" ולא הבין למה כלום לא קרה.
    # לשלוח אותו משם לדף מת, בלי מה שהקליד, זה להעניש אותו על חוסר סבלנות.
    if request.path in ("/login", "/signup"):
        return render_template(
            "login.html", error=msg,
            active_tab="signup" if request.path == "/signup" else "login",
            remembered_identifier=_remembered_identifier(),
            # גם כאן: מי שנחסם אחרי לחיצה כפולה לא צריך להקליד הכול מחדש
            signup=_signup_form() if request.path == "/signup" else None,
        ), 429

    return render_template("error.html", code=429, message=msg), 429


def _family_changed():
    """מעדכן את הסשן אם המשתמש כבר במשפחה אחרת; מחזיר את שם החדשה, או ‎None‎.

    הסשן זוכר את המשפחה מרגע ההתחברות. מי שהוסר ממנה — או יצא ממנה
    במכשיר אחר — המשיך לשלוח את המזהה הישן, והמסד כבר לא ענה עליו: כמעט
    כל מסך הפך לדף תקלה שמבקש לרענן, ורענון לא עזר עד התנתקות.

    נקרא רק אחרי שליפה שנכשלה, לא בכל בקשה: בשימוש רגיל אין אף שאילתה
    נוספת. כישלון בבדיקה עצמה הוא "לא ידוע", לא "השתנתה"."""
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
    # סנכרון העסקאות הקבועות נעשה פעם ביום **למשפחה** — החדשה עוד לא סונכרנה
    session.pop("recurring_synced", None)
    return ((profile.get("families") or {}).get("name")) or "המשפחה שלי"


@app.errorhandler(db.DataUnavailable)
def data_unavailable(e):
    """שליפה שנכשלה מגיעה לכאן, ולא מוצגת כ"אין לך נתונים".

    עשרה שולפים החזירו ‎[]‎ בכשל. משפחה עם שנתיים היסטוריה ראתה "אין
    היסטוריה", ‎₪11,000‎ של הוצאות קבועות נעלמו מעמוד החודש, ומי
    ש-‎get_family_members‎ נכשל עבורו קיבל "בן המשפחה שנבחר אינו במשפחה
    שלך" על כל ניסיון להוסיף עסקה — שגיאה שמאשימה אותו בתקלה שלנו.

    אפס מדומה נראה בדיוק כמו אפס אמיתי, וכל האפליקציה היא מספרים. אז
    עדיף מסך שאומר "לא הצלחנו לטעון" על מסך שמשקר בשקט."""
    logger.error("DataUnavailable: %s", e)
    # לפני שמכריזים על תקלה: אולי המשתמש כבר במשפחה אחרת (ראו ‎_family_changed‎)
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
        # בלי זה תקלת שרת אמיתית מגיעה למשתמש כ"שגיאת רשת", והוא מנסה
        # שוב ושוב בקשה שלעולם לא תצליח — ואנחנו לא שומעים על התקלה.
        return jsonify({"error": msg}), 500
    return render_template("error.html", code=500, message=msg), 500


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _month_label(year: int, month: int) -> str:
    """שם החודש בעברית. חודש מחוץ לטווח נחתך במקום להיכנס לאינדוקס —
    ‎_HEBREW_MONTHS[13] זרק IndexError והפיל את העמוד, ו-‎_HEBREW_MONTHS[-5]
    החזיר בשקט את אוגוסט, וזה הגרוע מבין השניים."""
    if not 1 <= month <= 12:
        month = clock.now().month
    return f"{_HEBREW_MONTHS[month]} {year}"


if __name__ == "__main__":
    debug = os.environ.get("FLASK_ENV") == "development"
    port  = int(os.environ.get("PORT", 8080))
    app.run(debug=debug, port=port)
