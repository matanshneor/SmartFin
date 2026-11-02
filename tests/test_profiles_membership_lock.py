"""
השיוך למשפחה (‎profiles.family_id‎) נקבע רק דרך פונקציות ‎SECURITY DEFINER‎.

הנעילה הראשונה (‎20260916100000‎) הסירה את ‎UPDATE‎ — אבל ‎DELETE‎ על
הפרופיל ו-‎INSERT‎ עם ‎family_id‎ נשארו, וביחד הם אותה תקיפה בשני צעדים:
למחוק את הפרופיל שלי וליצור אותו מחדש עם משפחה אחרת.

הבדיקה עוברת על המיגרציות לפי הסדר ומוודאת שבסוף המשתמש המאומת לא מקבל
בחזרה אף אחת משלוש ההרשאות.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MIGRATIONS = Path(__file__).resolve().parent.parent / "backend/supabase/migrations"


def _final_profile_grants():
    """מה נשאר ל-‎authenticated‎ על ‎public.profiles‎ אחרי כל המיגרציות.

    מפשט בכוונה: רק ‎grant/revoke <privs> on public.profiles‎ — ‎grant‎
    ברמת עמודות (‎update (name, …)‎) נספר כהרשאה מוגבלת ולא כמלאה."""
    full = {"insert", "update", "delete"}   # ברירת המחדל של Supabase
    pattern = re.compile(
        r"\b(grant|revoke)\s+([a-z ,()_]+?)\s+on\s+(?:table\s+)?public\.profiles\s+(?:to|from)\s+([a-z_, ]+)",
        re.I)
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        sql = re.sub(r"--[^\n]*|/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        for verb, privs, roles in pattern.findall(sql):
            if "authenticated" not in roles.lower():
                continue
            privs = privs.lower()
            if "(" in privs:          # הרשאה לעמודות מסוימות — לא לכל הטבלה
                continue
            names = {p.strip() for p in privs.split(",")}
            if "all" in names:
                names = {"insert", "update", "delete"}
            if verb.lower() == "revoke":
                full -= names
            else:
                full |= names & {"insert", "update", "delete"}
    return full


def test_authenticated_cannot_write_profiles_directly():
    assert _final_profile_grants() == set()
