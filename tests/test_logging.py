"""
Tests for logging.

The code used print only: 61 calls, 44 of them inside except. The output went to
Railway's log buffer, which gets wiped, and nobody read it. More importantly,
**Sentry didn't see it**. Error monitoring was hooked up, and then the app's most
common failure mode was routed around it: a query that fails and is swallowed.

With logging, Sentry's logging integration catches every record at ERROR and
above and turns it into an event. Same line of code, but now it is visible.
"""
import ast
import logging
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_BACKEND = Path(__file__).resolve().parent.parent / "backend"


def _sources():
    return [f for f in _BACKEND.glob("*.py") if f.name not in ("__init__.py", "logs.py")]


# ─── No failure is written anymore to a place nobody reads ──────────────────

def test_nothing_in_the_backend_prints_any_more():
    offenders = []
    for f in _sources():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "print"):
                offenders.append(f"{f.name}:{node.lineno}")

    assert not offenders, f"print remains in: {offenders}: Sentry won't see it"


def test_every_swallowed_exception_is_logged_with_its_traceback():
    """`logger.exception` and not `logger.error`: it attaches the traceback, which is
    exactly what is missing to know why the query failed."""
    db = ast.parse((_BACKEND / "supabase_config.py").read_text(encoding="utf-8"))

    calls = [n for n in ast.walk(db)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "exception"]
    assert len(calls) >= 40, f"only {len(calls)}: it seems most swallows weren't logged"


def test_no_exception_log_sits_outside_an_except_block():
    """`logger.exception` outside except prints "NoneType: None" instead of a
    traceback, i.e. a worthless record exactly when it is needed."""
    for f in _sources():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        bad = []

        class V(ast.NodeVisitor):
            def __init__(self): self.depth = 0
            def visit_ExceptHandler(self, node):
                self.depth += 1; self.generic_visit(node); self.depth -= 1
            def visit_Call(self, node):
                fn = node.func
                if (isinstance(fn, ast.Attribute) and fn.attr == "exception"
                        and isinstance(fn.value, ast.Name) and fn.value.id == "logger"
                        and self.depth == 0):
                    bad.append(node.lineno)
                self.generic_visit(node)

        V().visit(tree)
        assert not bad, f"{f.name}: logger.exception outside except on lines {bad}"


# ─── That the log really goes out ───────────────────────────────────────────

def test_a_failure_produces_a_record_with_the_traceback(caplog):
    from backend import supabase_config as db

    with caplog.at_level(logging.ERROR):
        try:
            raise RuntimeError("Supabase לא ענה")
        except Exception:
            db.logger.exception("get_monthly_summary")

    assert caplog.records, "no record was created"
    record = caplog.records[0]
    assert record.levelno == logging.ERROR, "a level below ERROR doesn't become an event in Sentry"
    assert record.exc_info is not None, "no traceback in the record"


def test_the_logger_is_configured_towards_stderr():
    """That is where Railway collects. `force=True` because gunicorn already touched
    the config before us, and without it the setup is silently ignored."""
    src = (_BACKEND / "logs.py").read_text(encoding="utf-8")

    assert "stream=sys.stderr" in src
    assert "force=True" in src


def test_noisy_libraries_are_quietened():
    """httpx reports every request at INFO. Without this the log is flooded and
    nobody reads it, i.e. we are back where we started."""
    src = (_BACKEND / "logs.py").read_text(encoding="utf-8")

    assert "httpx" in src and "logging.WARNING" in src


# ─── A silent failure that returns a false value ────────────────────────────

# Two swallows allowed to be silent, and why:
#   _invalidate_family_cache: `except ImportError` around importing flask. No
#     request context, nothing to clear. This is control flow, not a failure.
#   category_budget: `except (TypeError, ValueError)` on converting a value from
#     the settings. "No budget for the category" is the right answer to a corrupt
#     value, not a failure.
_MAY_STAY_SILENT = {"_invalidate_family_cache", "category_budget"}


def _enclosing_function(tree, node):
    best = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.lineno <= node.lineno <= (n.end_lineno or 0):
            if best is None or n.lineno > best.lineno:
                best = n
    return best.name if best else "?"


def test_no_failure_returns_a_falsy_answer_without_leaving_a_trace():
    """This is the most dangerous pattern in this file, and it has already cost
    dearly.

    A function that fails and returns `None`/`False` looks to the caller exactly
    like "no data". Even when the direction is safe (refuse, not allow), the user
    sees something confusing ("project not found", "no receipt"), and the
    developer has no way to investigate it later: the record simply doesn't exist.
    """
    tree = ast.parse((_BACKEND / "supabase_config.py").read_text(encoding="utf-8"))
    falsy = {None, False, 0, "", ()}
    offenders = []

    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        body = ast.unparse(ast.Module(body=handler.body, type_ignores=[]))
        if "logger." in body or "raise" in body:
            continue
        for n in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
            if not isinstance(n, ast.Return):
                continue
            v = n.value
            if v is None or (isinstance(v, ast.Constant) and v.value in falsy):
                name = _enclosing_function(tree, handler)
                if name not in _MAY_STAY_SILENT:
                    offenders.append(f"{name} (שורה {handler.lineno})")

    assert not offenders, (
        "a swallow returning a false value with no log: " + ", ".join(sorted(set(offenders)))
    )
