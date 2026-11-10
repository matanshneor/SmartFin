"""How a money amount is shown: one rule for the whole app.

Until 30.9.2026 every template rounded to whole shekels (`"{:,.0f}"`) while
input accepted agorot. A 12.50 coffee showed as ₪13, and three 10.40
purchases showed as ₪10 each and ₪31 together: totals that don't add up on
screen. Matan's choice: show agorot when there are agorot.

The exact same rule lives in the browser (`sfMoney` in core.js); change one,
change both.
"""


def format_money(value) -> str:
    """`12.5 → "12.50"`, `180 → "180"`, `1234.4 → "1,234.40"`. No ₪ and no sign:
    the sign and currency are the caller's decision (`-₪300`, `+₪50`)."""
    try:
        amount = round(float(value or 0), 2)
    except (TypeError, ValueError):
        amount = 0.0
    if amount == int(amount):
        return f"{int(amount):,}"
    return f"{amount:,.2f}"
