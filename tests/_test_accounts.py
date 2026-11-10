"""Temporary test accounts (Matan, 4.10: "the test families shouldn't be part of
the database").

Every run (of pytest or of a browser script) creates two users for itself, each
with its own family, and deletes both at the end, together with everything they
left behind: transactions, categories, projects, the internal archive
(`owner_archive`: a trigger archives every deleted transaction, and that is
where 14,500 test rows piled up), the sign-in log and receipt scans.

Until 4.10 there were two permanent test users in the production database, and
they were counted on every admin screen.

Creation and deletion run in SQL with owner privileges, through the linked CLI,
like setting up state in `test_family_join`. Not through `sign_up`: Supabase
has a signup and sign-in quota per IP address, and a series of browser scripts
would get stuck on it. The sign-in itself is an ordinary sign-in, like any
user's.

A run interrupted midway (Ctrl+C, a crash) doesn't get to delete. So every
creation starts by cleaning up test accounts left from earlier runs: only those
created more than two hours ago, so as not to delete accounts of a run going
on in parallel right now.
"""
import os
import secrets
import subprocess
import uuid

_BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")

# `.test` is a reserved domain (RFC 2606). But the cleanup does **not** identify a
# test account by email: signup doesn't verify email, so anyone can sign up with
# this domain. The marker is `app_metadata.smartfin_test`, a field only the
# server writes; a client's `sign_up` writes to `user_metadata` only.
DOMAIN = "smartfin.test"
_MARK = "raw_app_meta_data->>'smartfin_test' = 'true'"
_STALE = "2 hours"


def privileged_sql(sql: str) -> str:
    """SQL with owner privileges, through the linked CLI. Raises on failure."""
    out = subprocess.run(["supabase", "db", "query", sql, "--linked"],
                         cwd=_BACKEND, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"SQL with owner privileges failed: {out.stderr[-400:]}")
    return out.stdout


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def create_pair(label: str = "run") -> dict:
    """Two new users, A and B, each with its own family.

    Returns `{"a": {"email", "password", "user_id", "family_id", "token"}, "b": …}`."""
    from backend import supabase_config as db

    sweep_stale()
    run = uuid.uuid4().hex[:8]
    accounts = {}
    for key in ("a", "b"):
        accounts[key] = {"user_id": str(uuid.uuid4()),
                         "email": f"test-{label}-{run}-{key}@{DOMAIN}",
                         "password": secrets.token_urlsafe(18),
                         "name": f"בדיקה {key.upper()}"}
    privileged_sql("".join(f"""
        insert into auth.users (instance_id, id, aud, role, email, encrypted_password,
            email_confirmed_at, raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
            confirmation_token, recovery_token, email_change_token_new, email_change)
        values ('00000000-0000-0000-0000-000000000000', {_q(a['user_id'])}, 'authenticated',
            'authenticated', {_q(a['email'])},
            extensions.crypt({_q(a['password'])}, extensions.gen_salt('bf')), now(),
            '{{"provider":"email","providers":["email"],"smartfin_test":true}}',
            jsonb_build_object('name', {_q(a['name'])}), now(), now(), '', '', '', '');
        insert into auth.identities (provider_id, user_id, identity_data, provider,
            created_at, updated_at, last_sign_in_at)
        values ({_q(a['user_id'])}, {_q(a['user_id'])},
            jsonb_build_object('sub', {_q(a['user_id'])}, 'email', {_q(a['email'])},
                               'email_verified', true),
            'email', now(), now(), now());""" for a in accounts.values()))

    try:
        for key, a in accounts.items():
            # An ordinary first sign-in: `ensure_family` opens the family, as for
            # every new user in the app
            response, err = db.sign_in(a["email"], a["password"])
            if err:
                raise RuntimeError(f"signing in to a new test account failed ({a['email']}): {err}")
            db.set_auth_token(response.session.access_token)
            a["family_id"] = db.ensure_family(a["user_id"], f"משפחת בדיקה {key.upper()}")
            if not a["family_id"]:
                raise RuntimeError(f"no family was opened for test account {a['email']}")
            # And the onboarding wizard, with the default categories: a family
            # with no categories is sent to `/onboarding` from every page, and no
            # transaction can be entered in it
            from backend.app import _DEFAULT_CATEGORIES
            _, err = db.bulk_add_categories(a["family_id"], _DEFAULT_CATEGORIES)
            if err:
                raise RuntimeError(f"categories for test account {a['email']}: {err}")
            a["token"] = response.session.access_token
    except Exception:
        purge([a["user_id"] for a in accounts.values()])
        raise
    return accounts


def _purge_sql(users_where: str, extra_family_ids=()) -> str:
    """Deletes the users matching the condition, their families and every trace.

    The order matters: deleting the family cascades to the transactions, and
    the trigger archives each of them, so the archive is cleaned **after**
    deleting the families. `_tf` is built upfront, because after deleting the
    users there is no way left to know what their families were.

    `extra_family_ids`: temporary families with no members (`test_family_join`)."""
    extra = ", ".join(f"({_q(f)}::uuid)" for f in extra_family_ids)
    return f"""
        begin;
        create temp table _tu on commit drop as
            select id from auth.users where {users_where};
        create temp table _tf on commit drop as
            select distinct family_id as id from public.profiles
            where id in (select id from _tu) and family_id is not null
            {"union select column1 from (values " + extra + ") v" if extra else ""};
        -- משפחה שגם חבר אמיתי שייך אליה לא נמחקת, אף פעם
        delete from _tf where id in (
            select family_id from public.profiles
            where family_id is not null and id not in (select id from _tu));
        delete from public.families where id in (select id from _tf);
        delete from public.owner_archive
            where (kind = 'transaction' and (payload->>'family_id')::uuid in (select id from _tf))
               or (kind = 'family' and (payload->'family'->>'id')::uuid in (select id from _tf))
               or (kind = 'account' and (payload->>'id')::uuid in (select id from _tu));
        delete from public.login_events where user_id in (select id from _tu);
        delete from auth.users where id in (select id from _tu);
        commit;
    """


def purge(user_ids, extra_family_ids=()) -> None:
    """Deletes these test accounts and everything they left. Only accounts created here."""
    ids = ", ".join(f"{_q(u)}::uuid" for u in user_ids)
    if not ids and not extra_family_ids:
        return
    where = f"id in ({ids or 'null'}) and {_MARK}"
    privileged_sql(_purge_sql(where, extra_family_ids))


def sweep_stale() -> None:
    """Test accounts left from an interrupted run: everything created here more
    than two hours ago."""
    privileged_sql(_purge_sql(f"{_MARK} and created_at < now() - interval '{_STALE}'"))
