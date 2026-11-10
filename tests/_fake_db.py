"""An in-memory double for PostgREST, for routes that move money.

The money tests must run the real code (the route in `app.py` and the function
in `supabase_config.py` it calls) and check what was **really** saved. So the
fake sits at the lowest layer, in place of `get_client()`, and doesn't replace
the write functions themselves: otherwise the test verifies the double.

The double mimics the three behaviours the code really relies on:

1. `.data` after `update`/`delete` returns the rows actually touched, and an
   empty list when no row matched (`returning=representation` is the
   default). Every "no row was touched" check depends on this.
2. `.eq()` really filters, so another family's id matches nothing, and that is
   what makes an isolation test possible without a database.
3. `insert` returns the row as saved, including what the route computed.
"""
import uuid


class FakeExecute:
    """What `.execute()` returns: `.data` and `.count`."""

    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._filters = []
        self._op = "select"
        self._payload = None
        self._count = None
        self._limit = None
        self._single = None
        self._order = None
        self._range = None

    # ─── Building the query ─────────────────────────────────────────────
    def select(self, *_a, count=None, **_k):
        self._op = "select"
        self._count = count
        return self

    def insert(self, payload, **_k):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload, **_k):
        self._op = "update"
        self._payload = payload
        return self

    def delete(self, **_k):
        self._op = "delete"
        return self

    def eq(self, column, value):
        self._filters.append(("eq", column, value))
        return self

    def neq(self, column, value):
        self._filters.append(("neq", column, value))
        return self

    def gte(self, column, value):
        self._filters.append(("gte", column, value))
        return self

    def lt(self, column, value):
        self._filters.append(("lt", column, value))
        return self

    def lte(self, column, value):
        self._filters.append(("lte", column, value))
        return self

    def in_(self, column, values):
        self._filters.append(("in", column, list(values)))
        return self

    def is_(self, column, value):
        kind = "is_not" if getattr(self, "_negate", False) else "is"
        self._negate = False
        self._filters.append((kind, column, None if value == "null" else value))
        return self

    @property
    def not_(self):
        """`.not_.is_("x", "null")`: "everything that isn't NULL"."""
        self._negate = True
        return self

    def order(self, column, desc=False, **_k):
        self._order = (column, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def single(self):
        self._single = "single"
        return self

    def maybe_single(self):
        self._single = "maybe"
        return self

    # ─── Running it ─────────────────────────────────────────────────────
    def _matches(self, row):
        for kind, column, value in self._filters:
            actual = row.get(column)
            if kind == "eq" and actual != value:
                return False
            if kind == "neq" and actual == value:
                return False
            if kind == "gte" and not (actual is not None and str(actual) >= str(value)):
                return False
            if kind == "lte" and not (actual is not None and str(actual) <= str(value)):
                return False
            if kind == "lt" and not (actual is not None and str(actual) < str(value)):
                return False
            if kind == "in" and actual not in value:
                return False
            if kind == "is" and actual is not value:
                return False
            if kind == "is_not" and actual is value:
                return False
        return True

    def execute(self):
        rows = self._db.tables.setdefault(self._table, [])

        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            created = []
            for item in payload:
                row = dict(item)
                row.setdefault("id", str(uuid.uuid4()))
                rows.append(row)
                created.append(dict(row))
            self._db.writes.append((self._table, "insert", created))
            return FakeExecute(created)

        hit = [r for r in rows if self._matches(r)]

        if self._op == "update":
            for row in hit:
                row.update(self._payload)
            touched = [dict(r) for r in hit]
            self._db.writes.append((self._table, "update", touched))
            return FakeExecute(touched)

        if self._op == "delete":
            removed = [dict(r) for r in hit]
            self._db.tables[self._table] = [r for r in rows if not self._matches(r)]
            self._db.writes.append((self._table, "delete", removed))
            return FakeExecute(removed)

        # select
        self._db.reads.append((self._table, len(hit)))
        if self._order:
            column, desc = self._order
            hit.sort(key=lambda r: (r.get(column) is None, r.get(column)), reverse=desc)
        total = len(hit)
        if self._range is not None:
            hit = hit[self._range[0]:self._range[1] + 1]
        if self._limit is not None:
            hit = hit[:self._limit]
        # PostgREST's `max_rows`: a cap on every response, with no error and no
        # sign. Off by default; a test that turns it on sees what the real
        # database does.
        if self._db.max_rows is not None:
            hit = hit[:self._db.max_rows]
        if self._single == "single":
            if not hit:
                raise LookupError(f"{self._table}: single() found no row")
            return FakeExecute(dict(hit[0]), total)
        if self._single == "maybe":
            # Like postgrest-py 0.16: with no row it returns `None` and not an
            # empty response (see `_maybe_one`); the "polite" double hid a bug in
            # four places
            return FakeExecute(dict(hit[0]), total) if hit else None
        return FakeExecute([dict(r) for r in hit], total)


class FakeSupabase:
    """`FakeSupabase(transactions=[...], families=[...])`: tables in memory.

    `writes` records every write in order, so a test can say "nothing was
    written" and not just "the response was an error". That is the difference
    between catching validation that returns 422 and catching validation that
    returns 422 **after** it already saved.
    """

    def __init__(self, max_rows=None, **tables):
        self.max_rows = max_rows
        self.tables = {name: [dict(r) for r in rows] for name, rows in tables.items()}
        self.writes = []
        self.reads = []
        self.rpcs = []
        self.rpc_results = {}

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params=None):
        self.rpcs.append((name, dict(params or {})))
        return _Rpc(self.rpc_results.get(name))

    # ─── Conveniences for tests ─────────────────────────────────────────
    def rows(self, table):
        return [dict(r) for r in self.tables.get(table, [])]

    def written(self, table=None, op=None):
        return [w for w in self.writes
                if (table is None or w[0] == table) and (op is None or w[1] == op)]


class _Rpc:
    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return FakeExecute(self._result)
