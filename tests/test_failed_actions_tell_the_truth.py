"""
בדיקות לשלושה מקומות שבהם פעולה נכשלה או בוטלה — והמסך לא סיפר את האמת.

25 — מתג העדפות מתהפך בעצמו בלחיצה, לפני שהשרת נשאל. בכישלון הוצגה
     הודעה אדומה לשתי שניות והמתג נשאר במצב החדש. המשתמש ניווט משם
     בביטחון שזה נשמר, וברענון הבא זה חזר לאחור בלי שום קשר גלוי.
     מסך שמראה מצב שלא נשמר גרוע מהודעת שגיאה — הוא מבטיח.

26 — העתקת קוד ההזמנה נכשלה בשקט: ‎.then()‎ בלי ‎.catch‎. הרשאה שנדחתה,
     הקשר לא-מאובטח או דפדפן-בתוך-אפליקציה (קישור שנפתח מוואטסאפ) —
     ולא קרה כלום. זו הפעולה שהופכת משפחה למשפחה.

28 — בשאלה השנייה של מחיקת פרויקט, בריחה מהדיאלוג פורשה כ"השאר את
     העסקאות" והפרויקט נמחק בכל זאת. מי שנבהל ולחץ בחוץ התכוון לסגת,
     ולמחיקת פרויקט אין ביטול.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_JS = _ROOT / "frontend/static/js"


def _read(name):
    return (_JS / name).read_text(encoding="utf-8")


# ─── 25: מתג שלא נשמר חוזר אחורה ─────────────────────────────────────────────

def test_a_failed_preference_save_puts_the_control_back():
    body = _read("settings.js")
    fn = body[body.index("function savePrefs"):][:1400]

    assert "revert" in fn, "אין מסלול החזרה — המתג נשאר במצב שלא נשמר"
    assert "if (revert) revert();" in fn


def test_a_server_error_status_counts_as_failure_too():
    """קודם נבדק רק ‎data.error‎; 500 עם גוף ריק נראה כהצלחה."""
    body = _read("settings.js")
    fn = body[body.index("function savePrefs"):][:1400]

    assert "res.ok" in fn


@pytest.mark.parametrize("control,anchor", [
    ("שיוך הוצאה/הכנסה/חיסכון", "attrSwitches.forEach"),
    # "התראות חריגה" ו"הצגת מקום עבודה" ירדו מההגדרות (מתן, 11.10)
])
def test_every_switch_passes_a_way_back(control, anchor):
    """מתג אחד שנשכח הוא מתג שממשיך לשקר."""
    body = _read("settings.js")
    block = body[body.index(anchor):][:700]

    assert "savePrefs(" in block
    assert "checked = was" in block, f"{control} לא חוזר אחורה בכישלון"


def _strip_comments(js):
    """הערות החוצה לפני כל סריקת קוד.

    הערה שמכילה קריאה לדוגמה נספרה כקריאה אמיתית, ומחפש הסוגריים רץ
    ממנה עד סוף הקובץ ובלע הכול. זה קרה בפועל: הערה שמסבירה באג
    שברה את הבדיקה שאמורה למצוא אותו."""
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)


def _call_args(body, name):
    """הארגומנטים של כל קריאה ל-name, לפי התאמת סוגריים אמיתית.
    רגקס לא מספיק — חלק מהקריאות רב-שורתיות ומכילות סוגריים מקוננים."""
    body = _strip_comments(body)
    out = []
    needle = name + "("
    i = body.find(needle)
    while i != -1:
        j, depth = i + len(needle), 1
        while j < len(body) and depth:
            if body[j] == "(": depth += 1
            elif body[j] == ")": depth -= 1
            j += 1
        out.append(body[i + len(needle):j - 1])
        i = body.find(needle, j)
    return out


def _split_args(args):
    """מפצל רשימת ארגומנטים לפי פסיקים שברמה העליונה בלבד — פסיק בתוך
    אובייקט או בתוך גוף פונקציה אינו מפריד בין ארגומנטים."""
    out, depth, current = [], 0, ""
    for ch in args:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(current.strip()); current = ""
        else:
            current += ch
    if current.strip():
        out.append(current.strip())
    return out


def test_every_call_to_saveprefs_passes_a_way_back():
    """הבדיקה שתתפוס את המתג הבא שיתווסף: כל קריאה, בלי יוצא מן הכלל."""
    body = _read("settings.js")
    # הקריאה הראשונה היא ההגדרה עצמה (‎function savePrefs(patch, revert)‎)
    calls = _call_args(body, "savePrefs")[1:]

    # שיוך ותקציבים. התראות ומקום עבודה ירדו מההגדרות (מתן, 11.10)
    assert len(calls) >= 2, f"נמצאו רק {len(calls)} קריאות — הבדיקה כנראה לא מוצאת אותן"
    for args in calls:
        # ארגומנט שני כלשהו: פונקציה בשורה, או שם של אחת שהועברה פנימה.
        # מה שנבדק הוא שיש מסלול החזרה, לא איך הוא נכתב.
        assert len(_split_args(args)) >= 2, \
            f"קריאה ל-savePrefs בלי מסלול החזרה: {args[:60]}"


# ─── 26: העתקה שנכשלת אומרת זאת ──────────────────────────────────────────────

def test_copying_has_a_failure_path_at_all():
    clip = _read("clipboard.js")

    assert ".catch(" in clip, "עדיין דחייה לא-מטופלת — כישלון שקט"
    assert "!navigator.clipboard" in clip, "לא נבדק שהאובייקט בכלל קיים"


def test_a_failed_copy_leaves_the_user_something_to_do():
    """סימון הטקסט משאיר פעולה אחת (העתק ידני) במקום לתהות."""
    clip = _read("clipboard.js")

    assert "selectNodeContents" in clip
    assert "showToast" in clip


@pytest.mark.parametrize("caller", ["settings.js", "onboarding.js"])
def test_both_copy_buttons_use_the_shared_helper(caller):
    """שני המקומות שמציגים קוד הזמנה — בהגדרות ובאשף ההרשמה."""
    body = _read(caller)

    assert "window.copyToClipboard(" in body
    assert "navigator.clipboard" not in body, "נשארה קריאה ישירה בלי טיפול בכישלון"


def test_the_helper_is_loaded_where_it_is_used():
    """‎onboarding.html‎ לא טוען את core.js — האלמנטים שהוא מצפה להם לא
    קיימים שם — ולכן העוזר יושב בקובץ עצמאי משלו."""
    tpl = Path(__file__).resolve().parent.parent / "frontend/templates"

    for page in ("base.html", "onboarding.html"):
        assert "js/clipboard.js" in (tpl / page).read_text(encoding="utf-8"), page
    assert "getElementById" not in _read("clipboard.js"), \
        "העוזר תלוי באלמנטים — הוא נטען גם בעמוד שאין בהם"


# ─── 28: נסיגה מדיאלוג היא נסיגה ─────────────────────────────────────────────

def test_dismissing_a_dialog_is_distinguishable_from_saying_no():
    core = _read("core.js")

    assert "closeConfirm(null)" in core, "Escape ולחיצה בחוץ עדיין מדווחים כ'לא'"
    assert core.count("closeConfirm(null)") == 2, "חסר אחד ממסלולי הנסיגה"
    assert "closeConfirm(false)" in core, "כפתור הביטול חייב להישאר 'לא'"


def test_existing_callers_are_unaffected():
    """‎null‎ נבחר כי הוא falsy — כל ‎if (!ok) return‎ קיים ממשיך לעבוד."""
    core = _read("core.js")
    assert "falsy" in core[core.index("Three outcomes"):core.index("window.appConfirm")]


# ‎projects.js‎ ירד מהרשימה: מחיקת פרויקט עברה לתוך הפרויקט עצמו, ואין
# בה יותר שתי שאלות ברצף. הבחירה מה יקרה לעסקאות היא בורר בטופס שנפתח
# ומראה כמה עסקאות עומדות על הפרק — כלומר אין "שאלה שנייה" שאפשר
# להיסוג ממנה, ויש טופס שצריך לשלוח במפורש. הבדיקה למטה שומרת על
# התכונה שהחליפה אותה.
@pytest.mark.parametrize("path", ["settings.js"])
def test_backing_out_of_the_second_question_cancels_everything(path):
    body = _read(path)

    assert "=== null" in body, f"{path}: נסיגה עדיין מוחקת"


# "ברירת המחדל שומרת את הכסף" ישבה כאן. הבחירה עצמה הוסרה ב-28.9.2026:
# "להשאיר" נחת בבית כ"ללא קטגוריה" וייקר חודשים שעברו. ההחלטה החדשה
# נבדקת ב-tests/test_project_list_and_deletion.py.


def test_it_says_how_many_transactions_are_at_stake():
    """✕ ברשימה לא נשא שום מספר. בתוך הפרויקט אפשר לומר בדיוק מה יאבד."""
    # המחיקה בתחתית עמוד הפרויקט מאז 9.10
    html = (_ROOT / "frontend/templates/project_detail.html").read_text(encoding="utf-8")
    block = html[html.index("project-danger"):]

    assert "tx_count" in block


def test_backing_out_of_removing_a_family_member_cancels_too():
    """אותה תבנית, אותו תיקון — שתי שאלות ברצף."""
    body = _read("settings.js")
    fn = body[body.index("function askAboutTransactions"):][:1100]

    assert "wipe === null" in fn
