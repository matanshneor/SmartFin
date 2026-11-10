"""Temporary test accounts for the browser scripts (Matan, 4.10).

Every script imports this module at its top. The import creates two new accounts,
A and B, each with its own family, and puts their details in the environment under
the names the scripts already read (RLS_TEST_EMAIL_A, RLS_TEST_PASSWORD_A etc.). At
the end of the run (even after an exception) they are deleted with everything they
left. The scripts run .venv helpers in child processes, which inherit the
environment and get the same accounts.

The creation and deletion themselves are in tests/_test_accounts.py, and run in
.venv: the scripts run in the system Python (where Playwright is installed), which
doesn't have the Supabase library.
"""
import atexit
import json
import os
import subprocess

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_PY = os.path.join(_ROOT, ".venv", "bin", "python3")
_PRELUDE = ("import sys; sys.path.insert(0, '.'); sys.path.insert(0, 'tests'); "
            "from dotenv import load_dotenv; load_dotenv('.env'); import _test_accounts as t; ")


def _venv(code: str) -> str:
    out = subprocess.run([_PY, "-c", _PRELUDE + code], cwd=_ROOT,
                         capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise RuntimeError(f"חשבונות הבדיקה הזמניים: {out.stderr[-600:]}")
    return out.stdout


if "RLS_TEST_PASSWORD_A" not in os.environ:
    _pair = json.loads(_venv("import json; print(json.dumps(t.create_pair('browser')))")
                       .strip().splitlines()[-1])
    for _key, _acc in _pair.items():
        os.environ[f"RLS_TEST_EMAIL_{_key.upper()}"] = _acc["email"]
        os.environ[f"RLS_TEST_PASSWORD_{_key.upper()}"] = _acc["password"]

    @atexit.register
    def _purge():
        ids = [a["user_id"] for a in _pair.values()]
        _venv(f"t.purge({ids!r})")
