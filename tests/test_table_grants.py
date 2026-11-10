"""השומר בכניסה (מתן, 10.11.2026 — בדיקת אבטחה, נקודה 3).

ל-anon — מי שלא מחובר — אין שום הרשאה על אף טבלה. עד היום הוא קיבל
את ברירת המחדל של Supabase: הכל, כולל מחיקה וריקון של טבלה שלמה, וכל
ההגנה נשענה על RLS בלבד. RLS עדיין נועלת כל שורה; זו שכבה שנייה, כך
שכלל RLS אחד שייכתב לא נכון בעתיד לא יספיק כדי לחשוף נתונים.

רצות מול המסד האמיתי (מדלגות ב-CI, כמו כל בדיקות המסד).
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
        pytest.skip("אין SUPABASE_URL — הבדיקות מול המסד האמיתי רצות רק מקומית")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    with httpx.Client(base_url=url.rstrip("/") + "/rest/v1/", headers=headers,
                      timeout=10) as c:
        yield c


# עמודה קיימת לסינון — כך שגם אילו הייתה הרשאה, עדכון/מחיקה לא היו נוגעים
# בשום שורה. בלי סינון קיים, PostgREST נכשל על העמודה לפני שהוא בודק הרשאה.
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
    """טבלה חדשה שלא ברשימה לא הייתה נבדקת. ‎_TABLES‎ מול המסד עצמו."""
    if not os.environ.get("SUPABASE_URL"):
        pytest.skip("אין SUPABASE_URL")
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
    """בדיקת הבריאות רצה כ-anon. אחרי שנסגרה כל טבלה היא עוברת דרך פונקציה."""
    assert db.ping() == (True, "ok")


def test_the_warm_ping_is_accepted_even_with_a_stale_token(anon_rest, monkeypatch):
    """נקודה 4: הנגיעה שמחזיקה את החיבור חם נדחתה כ-3,800 פעמים ביום, כי
    נשאה את הטוקן שפג של המשתמש האחרון. עכשיו — גם עם טוקן כזה על הלקוח —
    התשובה היא 200, והיומנים נקיים לדחיות אמיתיות."""
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
