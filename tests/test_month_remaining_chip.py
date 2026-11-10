""""מאזן חודשי" (formerly "נשאר בעו״ש") on the month page: no red while there is
no income yet, and no "-₪0".

The home page already knew: a month with no income isn't a deficit, because there
is nothing to subtract from yet. The month page, in exactly the same state, showed
"₪-800" in red. And without JS (or before the count-up animation ran) the minus
stood after the ₪.
"""
import re

import pytest

from tests.test_project_only_month import month_page, _row  # noqa: F401 (fixture)

pytestmark = pytest.mark.unit


def _chip(html):
    """The balance's box, from the opening tag (where the deficit marker sits) to the
    amount."""
    i = html.rindex('<div class="month-net', 0, html.index("מאזן החודש"))
    return html[i:html.index("</p>", html.index("month-net-value", i)) + 4]


def _expense(amount, kind="expense"):
    row = _row(f"{kind}-{amount}", project=False)
    return {**row, "id": f"{kind}-{amount}", "amount": float(amount), "type": kind}


def test_spending_before_any_income_is_not_a_deficit(month_page):
    chip = _chip(month_page([_expense(800)]))

    assert "deficit" not in chip, "red deficit before any income came in"
    assert "-" not in re.sub(r"<[^>]+>", "", chip).replace("מאזן החודש", "")


def test_a_real_deficit_is_red_with_the_minus_before_the_shekel(month_page):
    """Control, and in the same touch: -₪300 and not ₪-300."""
    chip = _chip(month_page([_expense(500, "income"), _expense(800)]))

    assert "deficit" in chip
    assert "-₪300" in re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", chip))


def test_forty_agorot_short_is_not_minus_zero(month_page):
    chip = _chip(month_page([_expense(500, "income"), _expense(500.40)]))

    assert "deficit" not in chip
