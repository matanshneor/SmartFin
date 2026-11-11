"""Runs the real SmartFin app over an in-memory dataset, for screenshots.

    python -B serve.py <scenario> <port> [YYYY-MM-DD]

Nothing here touches the network or the repository: the Supabase client is
replaced by tests/_fake_db.FakeSupabase (subclassed below), httpx verbs refuse
to run, the clock is pinned, and the login route is registered from here.
"""
import os
import sys
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.dont_write_bytecode = True
os.environ["SECRET_KEY"] = "visual-harness-not-a-secret"
os.environ["FLASK_ENV"] = "development"
for k in ("SUPABASE_URL", "SUPABASE_KEY", "OPENAI_API_KEY", "SENTRY_DSN", "VAPID_PRIVATE_KEY",
          "RESEND_API_KEY", "RATELIMIT_STORAGE_URI"):
    os.environ.pop(k, None)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))
sys.path.insert(0, HERE)

scenario = sys.argv[1] if len(sys.argv) > 1 else "rich"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8099
today = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else date(2026, 11, 11)
NOW = datetime(today.year, today.month, today.day, 12, 30)

import httpx  # noqa: E402


def _refuse(*a, **k):
    raise AssertionError("visual harness: real network call refused")


for verb in ("request", "get", "post", "put", "patch", "delete"):
    setattr(httpx, verb, _refuse)

from backend import clock  # noqa: E402

clock.now = lambda: NOW
clock.now_utc = lambda: NOW.replace(tzinfo=timezone.utc)
clock.today = lambda: NOW.date()

from backend import supabase_config as db  # noqa: E402
from backend import app as app_module  # noqa: E402
from backend.app import app, limiter  # noqa: E402
from flask import session, redirect, request  # noqa: E402

import _fake_db  # noqa: E402
import demo  # noqa: E402


class Query(_fake_db._Query):
    def __init__(self, fake, table):
        super().__init__(fake, table)
        self._or = None

    def or_(self, expr, **_k):
        self._or = expr
        return self

    def order(self, column, desc=False, **_k):
        if self._order is None:          # several .order() calls: the first is the primary key
            self._order = (column, desc)
        return self

    def gt(self, column, value):
        self._filters.append(("gt", column, value))
        return self

    def ilike(self, column, pattern):
        self._filters.append(("ilike", column, str(pattern).strip("%").lower()))
        return self

    like = ilike

    def _matches(self, row):
        plain = [f for f in self._filters if f[0] not in ("gt", "ilike")]
        extra = [f for f in self._filters if f[0] in ("gt", "ilike")]
        saved, self._filters = self._filters, plain
        try:
            if not super()._matches(row):
                return False
        finally:
            self._filters = saved
        for kind, column, value in extra:
            actual = row.get(column)
            if kind == "gt" and not (actual is not None and str(actual) > str(value)):
                return False
            if kind == "ilike" and value not in str(actual or "").lower():
                return False
        if self._or:
            ok = False
            for part in self._or.split(","):
                col, op, val = part.split(".", 2)
                actual = row.get(col)
                if op == "is" and val == "null" and actual is None:
                    ok = True
                elif op == "eq" and str(actual) == val:
                    ok = True
                elif op == "ilike" and val.strip("%*").lower() in str(actual or "").lower():
                    ok = True
            if not ok:
                return False
        return True

    def __getattr__(self, name):         # any other filter verb: accept and ignore
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: self


class _Rpc:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return _fake_db.FakeExecute(self._result)


class Fake(_fake_db.FakeSupabase):
    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params=None):
        self.rpcs.append((name, dict(params or {})))
        return _Rpc(demo.rpc(self.tables, name, params))


FAKE = Fake(**demo.build(scenario, today))
db.get_client = lambda: FAKE
limiter.enabled = False


def _harness_login():
    me = next(p for p in FAKE.tables["profiles"] if p["id"] == demo.ME)
    session.clear()
    session.permanent = True
    session.update(
        user_id=demo.ME, family_id=demo.FAM, user_name=db.first_name(me["name"]),
        avatar_initial=me["avatar_initial"], user_email=me["email"],
        email_checked_at=__import__("time").time(), recurring_synced=today.isoformat())
    return redirect(request.args.get("next") or "/")


def _harness_logout():
    session.clear()
    return redirect("/?intro=1")


app.add_url_rule("/__h/login", "h_login", _harness_login)
app.add_url_rule("/__h/logout", "h_logout", _harness_logout)

if __name__ == "__main__":
    print(f"harness: scenario={scenario} today={today} port={port} "
          f"tx={len(FAKE.tables['transactions'])}", flush=True)
    app.run(host="127.0.0.1", port=port, use_reloader=False, threaded=False)
