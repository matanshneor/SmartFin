"""Every action that writes data is rate-limited (review of 1.10).

The README promised this, and 13 actions weren't limited: all of projects,
category reordering, the duplicate check, finishing the wizard. The test goes
over every route, so a new route without a limit is caught immediately.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC = (Path(__file__).resolve().parent.parent / "backend/app.py").read_text(encoding="utf-8")


def _routes():
    for m in re.finditer(r'^@app\.route\("([^"]+)"(?:, methods=\[([^\]]+)\])?\)', _SRC, re.M):
        head = _SRC[m.start():_SRC.index("\ndef ", m.start())]
        yield m.group(1), m.group(2) or '"GET"', head


def test_every_write_route_has_a_rate_limit():
    missing = [path for path, methods, head in _routes()
               if any(w in methods for w in ('"POST"', '"PUT"', '"DELETE"'))
               and "@limiter.limit(" not in head]
    assert not missing, missing
