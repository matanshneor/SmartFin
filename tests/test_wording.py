"""Uniform wording (round 4, E3).

- "1 חברים" → "חבר אחד": count_of on the server and sfCount in the browser, by the
  same rule.
- Plural address, like most of the app: "נסו שוב" and not "נסה שוב". When Or got
  "נסה שוב" it jarred, and half the messages addressed that way.
"""
import glob
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.wording import count_of

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CASES = [0, 1, 2, 11, "1", "abc", None]


@pytest.mark.parametrize("n,expected", [(1, "חבר אחד"), (2, "2 חברים"), (0, "0 חברים"),
                                         ("1", "חבר אחד"), (None, "0 חברים")])
def test_the_rule(n, expected):
    assert count_of(n, "חבר אחד", "חברים") == expected


def test_the_browser_counts_exactly_like_the_server():
    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't installed")
    script = (
        "const g = globalThis; g.window = g;"
        "eval(require('fs').readFileSync(process.argv[1], 'utf8')"
        ".match(/window\\.sfCount = function[\\s\\S]*?\\n\\};/)[0]);"
        "console.log(JSON.stringify(JSON.parse(process.argv[2]).map(n => g.sfCount(n, 'חבר אחד', 'חברים'))));"
    )
    out = subprocess.run([node, "-e", script, str(_ROOT / "frontend/static/js/core.js"), json.dumps(_CASES)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [count_of(n, "חבר אחד", "חברים") for n in _CASES]


def _visible_strings():
    """Text a user sees: without comments, and in code, only strings."""
    for f in glob.glob(str(_ROOT / "frontend/templates/*.html")) + \
             glob.glob(str(_ROOT / "frontend/static/js/*.js")) + \
             [str(_ROOT / "backend/app.py"), str(_ROOT / "backend/supabase_config.py")]:
        src = re.sub(r"\{#.*?#\}|<!--.*?-->|/\*.*?\*/|\"\"\".*?\"\"\"", "", open(f, encoding="utf-8").read(), flags=re.S)
        for line in src.split("\n"):
            if line.strip().startswith(("#", "//")):
                continue
            yield Path(f).name, line


@pytest.mark.parametrize("masculine", ["נסה שוב", "הזן ידנית", "בדוק גם", "רק אתה", "תמיד תוכל", 'aria-label="בחר '])
def test_messages_address_the_family_not_one_man(masculine):
    # a whole word: "לבדוק גם" (an infinitive, neutral) contains "בדוק גם"
    pattern = re.compile(r"(?<![א-ת])" + re.escape(masculine))
    hits = [name for name, line in _visible_strings() if pattern.search(line)]
    assert not hits, hits


@pytest.mark.parametrize("template,phrase", [
    ("settings.html", "}} חברים"), ("settings.html", "}} פרויקטים"),
    ("settings.html", "}} עסקאות חוזרות"), ("month.html", "}} קטגוריות ללא"),
    ("project_edit.html", "}} עסקאות"), ("settings.html", "}} ימים"),
])
def test_no_bare_number_before_a_plural(template, phrase):
    """"{{ n }} חברים" is "1 חברים" when n=1."""
    html = (_ROOT / "frontend/templates" / template).read_text(encoding="utf-8")
    assert phrase not in re.sub(r"\{#.*?#\}", "", html, flags=re.S)


def test_one_name_for_savings_and_for_shared():
    strings = list(_visible_strings())
    assert not [n for n, l in strings if "חסכונות" in l]
    assert not [n for n, l in strings if re.search(r"['\"]משותף['\"]", l)]


# ─── Arrows in RTL: forward ←, back → ────────────────────────────────────────

@pytest.mark.parametrize("page", ["privacy.html", "terms.html"])
def test_back_points_right(page):
    html = (_ROOT / "frontend/templates" / page).read_text(encoding="utf-8")
    assert ">→ חזרה<" in html and "← חזרה" not in html


def test_a_sequence_of_steps_reads_right_to_left():
    html = (_ROOT / "frontend/templates/project_detail.html").read_text(encoding="utf-8")
    assert '"שייך לפרויקט" ←' in html


@pytest.mark.parametrize("is_future,arrow", [(False, "חזרה לחודש הנוכחי ←"),
                                              (True, "→ חזרה לחודש הנוכחי")],
                         ids=["from-the-past", "from-the-future"])
def test_back_to_this_month_points_the_right_way(is_future, arrow):
    """From November, when September is the current month, returning is backwards in
    time."""
    from jinja2 import Environment
    src = (_ROOT / "frontend/templates/month.html").read_text(encoding="utf-8")
    line = src[src.index("{% if is_current %}"):]
    line = line[:line.index("</p>")]
    html = Environment().from_string(line).render(is_current=False, is_future=is_future,
                                                  url_for=lambda *a, **k: "/month")
    assert arrow in html


def test_the_route_knows_a_future_month():
    import inspect
    from backend import app as app_module
    src = inspect.getsource(app_module.month_view)
    assert "is_future = (year, month) > (now.year, now.month)" in src
    assert src.count("is_future=is_future") == 2


@pytest.mark.parametrize("totals,expected", [
    ({"קרן": 5000, "גמל": 3000}, {"קרן": 63, "גמל": 37}),          # 62.5 + 37.5, not 63 + 38
    ({"א": 1, "ב": 1, "ג": 1}, {"א": 34, "ב": 33, "ג": 33}),
    ({"א": 100}, {"א": 100}),
    ({"א": 0, "ב": 50}, {"ב": 100}),
    ({}, {}),
])
def test_shares_always_add_up_to_a_hundred(totals, expected):
    from backend.wording import share_map
    got = share_map([{"name": n, "total": t} for n, t in totals.items()])
    assert got == expected
    assert not got or sum(got.values()) == 100


def test_the_app_says_monthly_balance_and_never_checking_account():
    """Matan (30.9): "מאזן חודשי" instead of "נשאר בעו״ש": "so there's no נשאר בעוש in
    the app at all". In every spelling, in every text a user sees."""
    forms = re.compile(r'עו״ש|עו\\?"ש|עו&quot;ש|(?<![א-ת])עוש(?![א-ת])')
    hits = [name for name, line in _visible_strings() if forms.search(line)]
    assert not hits, hits
    for page in ("index.html", "month.html", "landing.html"):
        html = re.sub(r"\{#.*?#\}", "", (_ROOT / "frontend/templates" / page).read_text(encoding="utf-8"), flags=re.S)
        # "מאזן החודש" (Matan, 1.10), and no longer "מאזן חודשי", except one sentence
        # in the "איך מצרפים בן משפחה" window, which Matan wrote that way explicitly (5.10)
        html = html.replace("רואים את אותן עסקאות ואותו מאזן חודשי.", "")
        assert "מאזן החודש" in html and "מאזן חודשי" not in html, page


def test_recurring_transactions_have_one_name():
    """Matan (1.10): "עסקאות קבועות" across the app: not "עסקה חוזרת" and not "סדרה".
    In every text a user sees."""
    forms = re.compile(r"עסקה חוזרת|עסקאות חוזרות|העסקה הזאת חוזרת|העסקה חוזרת|סדרה|הסדרה|סדרות")
    hits = [(name, line) for name, line in _visible_strings() if forms.search(line)]
    assert not hits, hits


def test_the_project_edit_page_says_what_it_edits():
    """Matan (1.10): "עריכת פרויקט" and not "עריכת הגדרות", which doesn't say of what,
    and gets confused with the app's settings page."""
    html = (_ROOT / "frontend/templates/project_edit.html").read_text(encoding="utf-8")
    assert '<h1 class="hero-title">עריכת פרויקט</h1>' in html
    assert "עריכת הגדרות" not in html


@pytest.mark.parametrize("singular", [
    "הדבק קוד", "אם קיבלת ", "צור פרויקט כדי", "עדכן ושמור", "שינית את", "תאבד גישה",
    "שרשמת ", "שרצית ", "נרשמת בהצלחה", "שחיפשת ", "מעבירים אותך",
])
def test_verbs_address_the_family_in_plural(singular):
    """Matan (1.10): verbs addressing the user: always plural ("שיניתם", "נסו").
    "שלך" and "לך" on personal things stay (option B)."""
    hits = [(name, line.strip()) for name, line in _visible_strings() if singular in line]
    assert not hits, hits


@pytest.mark.parametrize("imperative", [
    ">שמור<", ">שמור שינויים<", ">מחק<", ">מחק ", ">הוסף<", ">העתק<", ">העתק ", ">התחבר<", ">הירשם<",
    ">התנתק<", ">התקן<", ">סרוק קבלה<", ">צור פרויקט<", ">עדכן סיסמה<", ">אפס ",
    "confirmText: 'מחק", "confirmText: 'הסר'", "confirmText: 'צור", "confirmText: 'עדכן",
    "confirmText: 'עבור", "confirmText: 'הפוך", "confirmText: 'החזר ", "label: 'בטל'",
    "textContent = 'שמור'", "'שלח קישור איפוס'", "'העבר ומחק'",
    'aria-label="ערוך ', 'aria-label="מחק ', 'aria-label="הסר ', 'aria-label="סגור',
    'aria-label="הוסף ', 'aria-label="צפה ', 'aria-label="הזז ',
    "'מתחבר…'", "'נרשם…'", "'מצטרף…'",
])
def test_buttons_name_the_action(imperative):
    """Matan (1.10): buttons as action nouns ("שמירה", "מחיקה", "התחברות"), not
    masculine imperative. And while loading: "מתחברים…", not "מתחבר…"."""
    hits = [(name, line.strip()) for name, line in _visible_strings() if imperative in line]
    assert not hits, hits



@pytest.mark.parametrize("gendered", [
    "יאבד גישה", "ותיפתח לו", "לצרף אותו", "אם יש לו", "העסקאות שלו", "הוסר מהמשפחה",
    "רשם כאן", "נרשם ע״י",
])
def test_messages_about_a_member_have_no_gender(gendered):
    """Matan (1.10): messages about a specific family member: no masculine or feminine,
    because it could be Or. "מנהל המשפחה" stays (option A)."""
    hits = [(name, line.strip()) for name, line in _visible_strings() if gendered in line]
    assert not hits, hits


@pytest.mark.parametrize("other_name", [
    "אימייל", "מיקום עבודה", "תנועות", "דשבורד", "לדף הבית", "לדף הראשי", "כפרטי", "התרעה",
    "למשתמש אחר", "המשתמש אינו", "המאזן החודשי", "במאזן החודשי", "הוצאות קבועות</h3>",
    "לא הצלחתי", 'סה"כ', "הכול", "שישה תווים",
])
def test_one_name_for_each_thing(other_name):
    """Matan (1.10): one name for each thing: email, workplace, transactions, household,
    personal, notification, family member/account, month balance, recurring
    transactions, "לא הצלחנו", total, everything, 6 characters."""
    word = re.compile(r"(?<![\u0590-\u05FF])" + re.escape(other_name) + r"(?![\u0590-\u05FF])")
    hits = [(name, line.strip()) for name, line in _visible_strings() if word.search(line)]
    assert not hits, hits


def test_income_expense_savings_are_called_departments():
    """Matan (1.10): income / expenses / savings are "מחלקות", not "סוגים". What stays
    with "סוג" talks about something else (file type, "מכל סוג")."""
    word = re.compile(r"(?<![֐-׿])(ה|ל|מ|ב|ש|ו)?(סוג|סוגי|סוגים|הסוג)(?![֐-׿])")
    allowed = ("סוג הקובץ", "מכל סוג")
    hits = [(name, line.strip()) for name, line in _visible_strings()
            if word.search(line) and not any(a in line for a in allowed)]
    assert not hits, hits
