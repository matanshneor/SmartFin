"""The cleanup of the temporary test accounts runs on the production database, so
its rules are kept in tests: it touches only accounts it created itself, and never
a family with a real member (Matan, 4.10)."""
import pytest

import _test_accounts as t

pytestmark = pytest.mark.unit


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(t, "privileged_sql", lambda sql: out.append(sql) or "")
    return out


def test_only_accounts_this_module_marked_are_purged(sent):
    """Not by the domain: sign-up doesn't verify email, and anyone can sign up with
    smartfin.test."""
    t.purge(["11111111-1111-1111-1111-111111111111"])
    t.sweep_stale()

    for sql in sent:
        users = sql[sql.index("select id from auth.users where"):sql.index(";", sql.index("auth.users where"))]
        assert t._MARK in users
        assert "email" not in users


def test_new_accounts_carry_the_mark(sent, monkeypatch):
    monkeypatch.setattr(t, "sweep_stale", lambda: None)
    import backend.supabase_config as db
    monkeypatch.setattr(db, "sign_in", lambda *a: (None, "stop here"))
    monkeypatch.setattr(t, "purge", lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        t.create_pair("x")

    assert '"smartfin_test":true' in sent[0]


def test_a_family_with_a_real_member_is_never_deleted(sent):
    t.purge([], extra_family_ids=["22222222-2222-2222-2222-222222222222"])

    sql = sent[0]
    guard = sql.index("delete from _tf where id in")
    assert "id not in (select id from _tu)" in sql[guard:sql.index(";", guard)]
    assert guard < sql.index("delete from public.families")


def test_the_archive_is_cleaned_after_the_families_it_fills():
    """Deleting a family archives its transactions (a trigger): cleaning the archive
    before that would leave exactly the rows that piled up until 4.10."""
    sql = t._purge_sql("false")
    assert sql.index("delete from public.families") < sql.index("delete from public.owner_archive")


def test_nothing_to_purge_sends_nothing(sent):
    t.purge([])

    assert sent == []
