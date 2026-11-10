"""סבב 1 מרשימת הרעיונות (מתן, 12.10) — 12 ליטושים קטנים.

ברכה לפי השעה · פסיקים בסכום · בלי ".00" · בלי שכפול בעסקה קבועה · "אין
שינויים" · "חלק מעסקה קבועה" · עין לסיסמה · אישור התנתקות · "שליחה" להזמנה ·
מיון לפי סכום · "הבא: 1.11" · בלי גרף "לפי בן משפחה" של עמודה אחת.
"""
import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest

from backend import supabase_config as db
from backend.wording import greeting, next_charge_label

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS = _ROOT / "frontend/static/js"
_T = _ROOT / "frontend/templates"
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _read(p):
    return p.read_text(encoding="utf-8")


# ─── ברכה לפי השעה ──────────────────────────────────────────────────────

@pytest.mark.parametrize("hour,text", [(5, "בוקר טוב"), (11, "בוקר טוב"), (12, "צהריים טובים"),
                                       (16, "צהריים טובים"), (17, "ערב טוב"), (21, "ערב טוב"),
                                       (22, "לילה טוב"), (0, "לילה טוב"), (4, "לילה טוב")])
def test_the_greeting_follows_the_hour(hour, text):
    assert greeting(hour) == text


def test_the_home_page_greets_by_the_israeli_clock():
    assert '{{ greeting() }}, {{ user.name }}' in _read(_T / "index.html")
    app_src = _read(_ROOT / "backend/app.py")
    assert 'app.jinja_env.globals["greeting"] = lambda: greeting(clock.now().hour)' in app_src


# ─── פסיקים בסכום, בלי ".00" ───────────────────────────────────────────

def _format(value, caret):
    """מריץ את ‎formatAmount‎ האמיתי מ-transactions.js ב-node."""
    src = _read(_JS / "transactions.js")
    fn = src[src.index("    function amountValue() {"):src.index("    function fitAmount() {")]
    script = ("const txAmount={value:%s,selectionStart:%d,setSelectionRange(a){this.sel=a}};\n%s\n"
              "formatAmount(true);console.log(JSON.stringify([txAmount.value, txAmount.sel ?? null, amountValue()]));"
              % (json.dumps(value), caret, fn))
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.mark.parametrize("typed,caret,shown,value", [
    ("48000", 5, "48,000", 48000),
    ("4800.5", 6, "4,800.5", 4800.5),
    ("1234567.891", 11, "1,234,567.89", 1234567.89),   # עד שתי ספרות אחרי הנקודה
    ("0012", 4, "12", 12),
    ("12.3.4", 6, "12.34", 12.34),                      # נקודה אחת
    ("0.5", 3, "0.5", 0.5),
])
def test_the_amount_gets_commas_while_typing(typed, caret, shown, value):
    got, _, num = _format(typed, caret)
    assert got == shown and num == value


def test_the_caret_stays_after_the_same_digit():
    # הסמן אחרי ה-8 ב-"4800" → "4,800": אחרי ה-8, כלומר אחרי "4,8"
    got, sel, _ = _format("4800", 2)
    assert got == "4,800" and sel == 3


def test_the_field_is_text_with_a_number_keyboard_and_no_cents_placeholder():
    base = _read(_T / "base.html")
    field = base[base.index('id="txAmount"'):]
    field = field[:field.index(">")]
    assert 'inputmode="decimal"' in field and 'placeholder="0"' in field
    assert 'type="text" id="txAmount"' in base
    tx = _read(_JS / "transactions.js")
    assert "const AMOUNT_PLACEHOLDER_DEFAULT = '0';" in tx
    assert "const amount = amountValue();" in tx                # השליחה בלי פסיקים
    assert "formatAmount(false);        // a value written by code" in tx  # עריכה וסריקה


# ─── עריכה ──────────────────────────────────────────────────────────────

def test_recurring_transactions_have_no_duplicate_button():
    tx = _read(_JS / "transactions.js")
    assert "duplicateBtn.hidden = !!(tx.isRecurring || tx.recurringParentId || editingSeries);" in tx


def test_save_is_grey_until_something_changes():
    tx = _read(_JS / "transactions.js")
    assert "editSnapshot = formSnapshot();" in tx
    # מאז 13.10 — אותו כיתוב, רק כבוי/פעיל ("שלא יהיה רשום אין שינויים")
    assert "if (editId) { submitLabel.textContent = 'שמירת השינויים'; return; }" in tx
    assert "'אין שינויים'" not in tx
    assert "if (isUnchanged()) return;" in tx
    assert "label.submit-btn.is-unchanged" in _CSS


def test_an_occurrence_links_to_its_recurring_transaction():
    base = _read(_T / "base.html")
    assert 'id="txSeriesLink"' in base and "חלק מעסקה קבועה" in base
    tx = _read(_JS / "transactions.js")
    assert "seriesLink.href = '/settings?series=' + encodeURIComponent(tx.recurringParentId);" in tx
    settings = _read(_JS / "settings.js")
    assert "new URLSearchParams(location.search).get('series')" in settings and "row.click();" in settings


# ─── סיסמה והתנתקות ─────────────────────────────────────────────────────

def test_every_password_page_loads_the_eye():
    for name in ("login.html", "reset_password.html", "settings.html"):
        assert "js/password-eye.js" in _read(_T / name), name
    eye = _read(_JS / "password-eye.js")
    assert "input.type = show ? 'text' : 'password';" in eye
    assert "autocomplete" not in eye.split("*/", 1)[1]           # לא נוגעים במנהל הסיסמאות


def test_logging_out_asks_first():
    settings = _read(_JS / "settings.js")
    block = settings[settings.index("// ── Confirmation before signing out"):]
    assert "window.appConfirm(" in block and "form.submit();" in block
    assert 'method="POST" action="{{ url_for(\'logout\') }}"' in _read(_T / "settings.html")


# ─── הזמנה, מיון, החיוב הבא, בן משפחה אחד ──────────────────────────────

def test_the_invite_is_shared_where_possible():
    settings = _read(_JS / "settings.js")
    block = settings[settings.index("const SHARE_LABEL"):settings.index("// ── Join another family")]
    assert "navigator.share({ text: msg })" in block and "err.name === 'AbortError'" in block
    assert "window.copyToClipboard(msg, inviteCode)" in block


def test_all_transactions_can_be_sorted_by_amount():
    month = _read(_T / "month.html")
    assert 'data-sort="amount"' in month and 'data-sort="date"' in month
    js = _read(_JS / "month.js")
    assert "Number(b.dataset.amount) - Number(a.dataset.amount)" in js
    assert "sortAllTx('date');" in js                            # לא נשמר


_TODAY = dt.date(2026, 10, 12)


def _tpl(date, freq, end=None):
    return {"date": date, "recurring_frequency": freq, "recurring_end_date": end}


@pytest.mark.parametrize("tpl,expected", [
    (_tpl("2026-09-01", "monthly_1"), dt.date(2026, 11, 1)),
    (_tpl("2026-09-15", "monthly_15"), dt.date(2026, 10, 15)),
    (_tpl("2026-08-12", "monthly_same"), dt.date(2026, 10, 12)),         # היום
    (_tpl("2026-01-31", "monthly_same"), dt.date(2026, 10, 31)),
    (_tpl("2026-10-05", "weekly"), dt.date(2026, 10, 12)),
    (_tpl("2026-10-01", "biweekly"), dt.date(2026, 10, 15)),
    (_tpl("2026-11-03", "monthly_same"), dt.date(2026, 11, 3)),          # עוד לא התחילה
    (_tpl("2026-01-01", "monthly_1", end="2026-10-01"), None),          # הסתיימה
])
def test_the_next_charge(tpl, expected):
    assert db.next_recurring_date(tpl, _TODAY) == expected


def test_the_next_charge_label():
    assert next_charge_label(_TODAY, _TODAY) == "הבא: היום"
    assert next_charge_label(dt.date(2026, 10, 13), _TODAY) == "הבא: מחר"
    assert next_charge_label(dt.date(2026, 11, 1), _TODAY) == "הבא: 1.11"
    assert next_charge_label(dt.date(2027, 1, 1), _TODAY) == "הבא: 1.1.27"
    assert next_charge_label(None, _TODAY) == ""


def test_one_member_gets_no_chart():
    import inspect
    from backend import app as app_module
    src = inspect.getsource(app_module.month_view)
    assert "if len(mb) >= 2:" in src
