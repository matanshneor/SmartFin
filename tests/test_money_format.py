"""Agorot are shown when there are agorot, on the server and in the browser, by
exactly the same rule.

Until 30.9.2026 every template rounded to whole shekels, while input accepted
agorot: a 12.50 coffee showed as ₪13, and three 10.40 purchases showed as ₪10
each and ₪31 together. Matan chose: agorot when present. The rule lives in two
places (`format_money` for templates and alerts, `sfMoney` for animations,
legends and charts), and the test here is what stops them drifting apart.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.money import format_money
from tests.test_project_only_month import month_page, _row  # noqa: F401 (fixture)

pytestmark = pytest.mark.unit

_CORE = Path(__file__).resolve().parent.parent / "frontend/static/js/core.js"

_CASES = [12.5, 180, 180.0, 1234.4, 1234567.89, 0, 0.1 + 0.2, 10.40 * 3,
          99.999, 99.994, 0.005, 5000.10, -300, -12.5]


@pytest.mark.parametrize("value,expected", [
    (12.5, "12.50"), (180, "180"), (1234.4, "1,234.40"), (0.1 + 0.2, "0.30"),
    (10.40 * 3, "31.20"), (99.999, "100"), (None, "0"), ("abc", "0"),
])
def test_the_rule(value, expected):
    assert format_money(value) == expected


def test_the_browser_says_exactly_what_the_server_says():
    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't installed")
    script = (
        "const g = globalThis; g.window = g;"
        "eval(require('fs').readFileSync(process.argv[1], 'utf8')"
        ".match(/window\\.sfMoney = function[\\s\\S]*?\\n\\};/)[0]);"
        "console.log(JSON.stringify(JSON.parse(process.argv[2]).map(v => g.sfMoney(v))));"
    )
    out = subprocess.run([node, "-e", script, str(_CORE), json.dumps(_CASES)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr

    browser = json.loads(out.stdout)
    server = [format_money(v) for v in _CASES]
    assert browser == server, list(zip(_CASES, server, browser))


def test_the_month_page_keeps_agorot_on_purchases_and_rounds_the_category(month_page):
    """Three purchases of 10.40: each stays ₪10.40. The category's total is a whole
    shekel since 24.11 (Matan: ₪930.66 beside ₪518 made a ragged column, and the
    budget line under it was already whole); until then it showed ₪31.20. It is
    the rounded sum, ₪31, never the sum of rounded purchases."""
    rows = [{**_row(f"קנייה{n}", project=False), "id": f"k{n}", "amount": 10.40} for n in range(3)]

    html = month_page(rows)

    assert html.count("₪10.40") >= 3
    assert '<span class="legend-amount">₪31</span>' in html
    assert '<span class="legend-amount">₪31.20</span>' not in html
    # A single purchase never rounds
    assert "₪10<" not in html
