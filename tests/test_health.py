"""
בדיקת הבריאות אמרה "ok" גם כשהמסד היה מת.

היא החזירה מחרוזת קבועה בלי לבדוק כלום, אז מוניטור חיצוני היה מדווח
שהכול תקין בדיוק בזמן שאף משפחה לא רואה את הכסף שלה. הדרך היחידה
לגלות הייתה שמישהו יטרח לספר.

עכשיו יש שתיים: ‎/health‎ עונה על "התהליך חי?" (זו הבדיקה של Railway,
ואסור לה להיכשל בגלל המסד — היא הייתה חוסמת פריסה בדיוק ברגע הגרוע),
ו-‎/health/db‎ עונה על "אפשר באמת להשתמש באפליקציה?".
"""
import pytest

from backend import app as app_module
from backend.app import app

pytestmark = pytest.mark.unit


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    # הזיכרון בן חמש השניות משותף לכל הבדיקות בתהליך — מאפסים אותו
    monkeypatch.setitem(app_module._health_cache, "at", -app_module._HEALTH_TTL)
    with app.test_client() as c:
        yield c


def _db_says(monkeypatch, ok, detail):
    monkeypatch.setattr(app_module.db, "ping", lambda *a, **k: (ok, detail))


# ─── כשהמסד עונה ────────────────────────────────────────────────────────────

def test_both_endpoints_are_green_when_the_database_answers(client, monkeypatch):
    _db_says(monkeypatch, True, "ok")

    for path in ("/health", "/health/db"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.get_json()["database"] == "ok", path


# ─── כשהמסד לא עונה ─────────────────────────────────────────────────────────

def test_the_monitor_endpoint_fails_when_the_database_is_down(client, monkeypatch):
    """הלב של התיקון. בלי זה אין שום דרך לדעת מרחוק שהאפליקציה שבורה."""
    _db_says(monkeypatch, False, "ConnectTimeout")

    r = client.get("/health/db")
    assert r.status_code == 503
    assert r.get_json() == {"status": "degraded", "database": "ConnectTimeout"}


def test_railways_probe_stays_green_so_a_database_blip_cannot_block_a_deploy(
        client, monkeypatch):
    """בקרת-נגד: אם גם זו הייתה נכשלת, תקלה של שתי דקות ב-Supabase הייתה
    חוסמת את הפריסה שאולי באה לתקן אותה."""
    _db_says(monkeypatch, False, "ConnectTimeout")

    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["database"] == "ConnectTimeout", "עונה 200 אבל לא מסתיר"


# ─── הזיכרון הקצר ───────────────────────────────────────────────────────────

def test_a_burst_of_checks_hits_the_database_once(client, monkeypatch):
    """ארבעה workers בסך הכול. בלי זיכרון, דווקא בזמן תקלה — כשהבדיקות
    מתרבות והשרת עמוס — כל בדיקה הייתה תופסת אחד מהם."""
    calls = []
    monkeypatch.setattr(app_module.db, "ping",
                        lambda *a, **k: (calls.append(1) or (True, "ok")))

    for _ in range(10):
        client.get("/health")
        client.get("/health/db")

    assert len(calls) == 1, f"נשאל {len(calls)} פעמים במקום פעם אחת"


def test_the_memory_expires(client, monkeypatch):
    """בקרת-נגד: זיכרון שלא פג היה מקפיא את התשובה הראשונה לנצח."""
    state = {"ok": True}
    monkeypatch.setattr(app_module.db, "ping",
                        lambda *a, **k: (state["ok"], "ok" if state["ok"] else "down"))

    assert client.get("/health/db").status_code == 200

    state["ok"] = False
    assert client.get("/health/db").status_code == 200, "עדיין בתוך החלון"

    # מזקינים את הרשומה במקום לחכות בפועל
    app_module._health_cache["at"] -= app_module._HEALTH_TTL
    assert client.get("/health/db").status_code == 503


# ─── הבדיקה עצמה ────────────────────────────────────────────────────────────

def test_ping_does_not_use_the_shared_client(monkeypatch):
    """הלקוח המשותף נושא את הטוקן של הבקשה הקודמת. בדיקה לא-מאומתת דרכו
    הייתה נכשלת ברגע שהוא פג — וזה נראה בדיוק כמו "המסד נפל"."""
    from backend import supabase_config as _db
    monkeypatch.setattr(_db, "get_client",
                        lambda: pytest.fail("ping השתמש בלקוח המשותף"))
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    ok, detail = _db.ping()
    assert (ok, detail) == (False, "not configured")


def test_a_network_failure_is_reported_and_not_raised(monkeypatch):
    """בדיקת בריאות שמתרסקת היא בדיוק מה שהיא אמורה למנוע."""
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
