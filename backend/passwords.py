"""מה נדרש מסיסמה חדשה (מתן, 10.11.2026 — בדיקת אבטחה).

שני כללים, לכל סיסמה חדשה: הרשמה, איפוס, והחלפה בהגדרות. סיסמה קיימת
לא נבדקת — מי שכבר רשום ממשיך להתחבר בדיוק כמו קודם.

  · לפחות 8 תווים (עד 10.11 — 6). ההמלצה של Supabase היא 8 לפחות.
  · לא סיסמה שדלפה. רשימות של מאות מיליוני סיסמאות שדלפו מאתרים אחרים
    מסתובבות ברשת, ותוקפים מנסים אותן אחת-אחת על כל אתר. ל-Supabase יש
    בדיקה כזו מובנית, אבל רק בתוכנית בתשלום — אז היא כאן.

הבדיקה מול Pwned Passwords (‎api.pwnedpasswords.com‎) לא שולחת את הסיסמה:
נשלחים רק חמשת התווים הראשונים של ה-SHA-1 שלה, והשירות מחזיר את כל
הסיומות שמתחילות בהם (מאות). ההשוואה נעשית כאן. ‎Add-Padding‎ מוסיף
לתשובה שורות מזויפות עם ספירה 0, כדי שגם גודל התשובה לא יסגיר משהו.

אם השירות לא עונה — הסיסמה מתקבלת. הרשמה שנתקעת בגלל שירות חיצוני
גרועה יותר מסיסמה אחת שלא נבדקה.
"""
import hashlib

import httpx

from . import logs

logger = logs.get("smartfin.passwords")

MIN_LENGTH = 8
_RANGE_URL = "https://api.pwnedpasswords.com/range/"
_TIMEOUT = 3.0

TOO_SHORT = f"הסיסמה חייבת להכיל לפחות {MIN_LENGTH} תווים"
LEAKED = ("הסיסמה הזו הופיעה ברשימות של סיסמאות שדלפו מאתרים אחרים, "
          "ולכן קל לנחש אותה — בחרו סיסמה אחרת")


def _fetch_range(prefix: str) -> str:
    r = httpx.get(_RANGE_URL + prefix, timeout=_TIMEOUT,
                  headers={"Add-Padding": "true", "User-Agent": "SmartFin"})
    r.raise_for_status()
    return r.text


def is_leaked(password: str) -> bool:
    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        body = _fetch_range(prefix)
    except httpx.HTTPError as e:
        logger.warning("pwned passwords unavailable (%s) — password accepted unchecked",
                       type(e).__name__)
        return False
    for line in body.splitlines():
        found, _, count = line.strip().partition(":")
        if found == suffix:
            # שורות ריפוד מגיעות עם ספירה 0 — הן לא סיסמאות אמיתיות
            return count.strip() not in ("", "0")
    return False


def problem(password: str):
    """הודעת השגיאה לסיסמה חדשה, או None אם היא בסדר."""
    if len(password) < MIN_LENGTH:
        return TOO_SHORT
    if is_leaked(password):
        return LEAKED
    return None
