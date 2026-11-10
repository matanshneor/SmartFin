"""
The database couldn't be reproduced from the repo.

get_my_family_id() exists in the database, is called from two pushed migrations
(20260916110000), from supabase_config.py, and from the RLS policy
profiles_family_read, and it had no CREATE in the files. It was created through
the dashboard and never written as a migration.

Building from scratch from the files gave a schema where get_family_members and
get_months_archive fail, and the first swallows the failure and returns an empty
list, that is, a family shown without a single member.

This takes exactly away from the work that brought the migration ledger to 36/36:
the ledger was complete, the content wasn't.

The test here doesn't check the database: it checks that the files are
self-sufficient: every function that SQL in the repo calls must be defined in SQL
in the repo.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MIGRATIONS = Path(__file__).resolve().parent.parent / "backend/supabase/migrations"

# functions of Postgres and of Supabase itself: shouldn't be defined by us
_BUILTIN = {
    "auth", "count", "coalesce", "nullif", "btrim", "lower", "upper", "now",
    "gen_random_uuid", "gen_random_bytes", "date_trunc", "to_jsonb", "jsonb_build_object",
    "jsonb_agg", "array_length", "array_agg", "string_agg", "extract", "substr",
    "length", "floor", "random", "round", "sum", "min", "max", "exists", "set_config",
    "current_setting", "encode", "digest", "crypt", "gen_salt", "format", "concat",
    "trim", "replace", "position", "left", "right", "abs", "greatest", "least",
    "to_char", "age", "coalesce", "unnest", "generate_series", "row_to_json",
    "json_build_object", "make_date", "cast", "nextval", "currval", "setval",
}


def _sql():
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(_MIGRATIONS.glob("*.sql"))}


def _defined(all_sql):
    return set(re.findall(r"create (?:or replace )?function\s+(?:public\.)?([a-z_][a-z0-9_]*)",
                          all_sql, re.I))


def _tables(all_sql):
    return set(re.findall(r"create table (?:if not exists )?(?:public\.)?([a-z_][a-z0-9_]*)",
                          all_sql, re.I))


def _called(all_sql):
    """Calls to public.<name>(, without tables.

    insert into public.transactions (amount, ...) looks exactly like a function
    call if you only look at a name followed by a parenthesis."""
    names = set(re.findall(r"public\.([a-z_][a-z0-9_]*)\s*\(", all_sql, re.I))
    return {n for n in names if n not in _tables(all_sql)}


def test_every_function_the_migrations_call_is_also_defined_there():
    """This is the rule that was broken. A migration calling a function not defined in
    the repo makes the repo insufficient, and the failure shows up only when someone
    builds a database from scratch."""
    all_sql = "\n".join(_sql().values())
    missing = sorted(_called(all_sql) - _defined(all_sql) - _BUILTIN)

    assert not missing, f"called and not defined: {missing}"


def test_the_function_that_was_missing_is_there_now():
    all_sql = "\n".join(_sql().values())

    assert "get_my_family_id" in _defined(all_sql)


def test_it_is_defined_before_the_migrations_that_use_it():
    """File order is run order. A function created after its caller will fail in a
    build from scratch, even if both exist."""
    files = _sql()
    defined_in = next(n for n, s in files.items()
                      if re.search(r"create (?:or replace )?function\s+public\.get_my_family_id", s, re.I))
    callers = [n for n, s in files.items()
               if "public.get_my_family_id(" in s and n != defined_in]

    assert callers, "the test found no callers: the wording probably changed"
    late = [n for n in callers if n < defined_in]
    assert not late, (
        f"{defined_in} is defined after {late}: a build from scratch will fail there, because "
        f"Postgres validates a SQL function's body at creation time")


def test_the_restored_definition_matches_what_the_database_has():
    """Verified in a rollback against the live database before the push: the
    definition and grants are identical, that is, the migration is documentation
    and not a change."""
    sql = _sql()["20260916105900_get_my_family_id.sql"]

    assert "SELECT family_id FROM profiles WHERE id = auth.uid();" in sql
    assert "security definer" in sql.lower()
    assert "set search_path to 'public'" in sql.lower()
    assert "to authenticated, anon, service_role" in sql


def test_the_profiles_policy_is_not_the_recursive_version():
    """The policy in the files was recursive: a SELECT policy on profiles that queries
    profiles. On the server it has long gone through the function, which is
    security definer and so doesn't trigger the policy again, but the fix stayed
    only there, and a build from the repo would produce a broken policy."""
    files = _sql()
    fix = files["20260916105900_get_my_family_id.sql"]

    assert 'create policy "profiles_family_read"' in fix
    assert "public.get_my_family_id()" in fix
    # and it comes after the recursive version, so it overrides it
    recursive = [n for n, s in files.items()
                 if 'CREATE POLICY "profiles_family_read"' in s]
    assert recursive and all(n < "20260916105900" for n in recursive)


# ─── And that a push from scratch actually reaches the end ──────────────────
#
# The tests above make sure the content is complete. They didn't check that the
# files **run**: nine policies were defined twice, and CREATE POLICY has no
# IF NOT EXISTS, so supabase db push on an empty database stopped at 42710 on the
# third file. The ledger was full, so on the linked project it wasn't visible, and
# disaster recovery, a test environment and db reset all silently failed until
# someone needed them.

_POLICY = re.compile(r'create\s+policy\s+"([^"]+)"\s+on\s+([\w.]+)', re.I)
_DROP_POLICY = re.compile(r'drop\s+policy\s+if\s+exists\s+"([^"]+)"\s+on\s+([\w.]+)', re.I)


def test_no_policy_is_created_twice_without_being_dropped_first():
    """The same policy in two files is a hard error on push, not a warning."""
    seen = {}
    offenders = []

    for name in sorted(_sql()):
        sql = _sql()[name]
        dropped = {(m.group(1).lower(), m.group(2).lower().replace("public.", ""))
                   for m in _DROP_POLICY.finditer(sql)}
        for m in _POLICY.finditer(sql):
            key = (m.group(1).lower(), m.group(2).lower().replace("public.", ""))
            if key in seen and key not in dropped:
                offenders.append(f'"{key[0]}" על {key[1]} — גם ב-{seen[key]} וגם ב-{name}')
            seen[key] = name

    assert not offenders, (
        "a policy created twice without a prior drop stops a push from scratch:\n  "
        + "\n  ".join(offenders)
    )


def test_no_function_changes_its_return_type_without_a_drop():
    """create or replace function can't change a return type. On an existing database
    it isn't noticed; on an empty database it stops the push."""
    # The type is taken up to the keyword after it, not to the end of the line:
    # returns table spans several lines, and cutting at the first line compares
    # 'table (' to a full signature and reports a change that didn't happen.
    create = re.compile(
        r"create\s+(?:or\s+replace\s+)?function\s+(?:public\.)?(\w+)\s*\([^)]*\)\s*"
        r"returns\s+(.*?)\s+(?:language|as\s*\$)", re.I | re.S)
    drop = re.compile(r"drop\s+function\s+if\s+exists\s+(?:public\.)?(\w+)", re.I)

    def normalise(t):
        t = re.sub(r"\s+", " ", t).strip().lower()
        t = re.sub(r"\s*\(\s*", "(", t)
        t = re.sub(r"\s*\)", ")", t)
        # int and integer are the same type in Postgres; a spelling difference isn't a change.
        return re.sub(r"\bint\b", "integer", t)

    signatures, offenders = {}, []
    for name in sorted(_sql()):
        sql = _sql()[name]
        dropped = {m.group(1).lower() for m in drop.finditer(sql)}
        for m in create.finditer(sql):
            fn, ret = m.group(1).lower(), normalise(m.group(2))
            if fn in signatures and signatures[fn] != ret and fn not in dropped:
                offenders.append(f"{fn}: {signatures[fn]!r} → {ret!r} ב-{name}")
            signatures[fn] = ret

    assert not offenders, (
        "a function changing its return type without drop stops a push from scratch:\n  "
        + "\n  ".join(offenders)
    )
