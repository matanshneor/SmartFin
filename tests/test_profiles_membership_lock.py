"""
Family membership (profiles.family_id) is set only through SECURITY DEFINER
functions.

The first lock (20260916100000) removed UPDATE, but DELETE on the profile and
INSERT with family_id remained, and together they are the same attack in two
steps: delete my profile and recreate it with another family.

The test walks the migrations in order and makes sure that in the end the
authenticated user gets none of the three privileges back.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MIGRATIONS = Path(__file__).resolve().parent.parent / "backend/supabase/migrations"


def _final_profile_grants():
    """What authenticated has left on public.profiles after all the migrations.

    Simplifies on purpose: only grant/revoke <privs> on public.profiles; a
    column-level grant (update (name, …)) counts as a limited privilege and not a
    full one."""
    full = {"insert", "update", "delete"}   # Supabase's default
    pattern = re.compile(
        r"\b(grant|revoke)\s+([a-z ,()_]+?)\s+on\s+(?:table\s+)?public\.profiles\s+(?:to|from)\s+([a-z_, ]+)",
        re.I)
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        sql = re.sub(r"--[^\n]*|/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        for verb, privs, roles in pattern.findall(sql):
            if "authenticated" not in roles.lower():
                continue
            privs = privs.lower()
            if "(" in privs:          # a grant on specific columns, not the whole table
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
