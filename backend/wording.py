"""Wording for "number + noun": one rule for the whole app.

"1 חברים", "1 פרויקטים", "1 עסקאות יימחקו": in Hebrew that is "חבר אחד",
"פרויקט אחד", "עסקה אחת". The exact same rule lives in the browser (`sfCount`
in core.js).
"""

# Month names, indexed 1–12 (0 empty). One list for the server and templates.
HEBREW_MONTHS = ["", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
                 "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]


def count_of(n, one: str, many: str) -> str:
    """`count_of(1, "חבר אחד", "חברים") → "חבר אחד"`; `count_of(3, …) → "3 חברים"`.
    `one` is the full singular phrase, because "אחד/אחת" depends on the noun's gender."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    return one if n == 1 else f"{n} {many}"


def share_map(items) -> dict:
    """A whole-number percentage per item of the total, summing to exactly 100
    (largest-remainder method). Plain rounding of 62.5 and 37.5 gave 63% and
    38%: 101%, a number anyone checking would notice. Takes a list of
    `{"name", "total"}` and returns `{name: percent}`."""
    items = [i for i in items if float(i.get("total") or 0) > 0]
    whole = sum(float(i["total"]) for i in items)
    if not whole:
        return {}
    raw = [(i["name"], float(i["total"]) / whole * 100) for i in items]
    floors = {name: int(p) for name, p in raw}
    short = 100 - sum(floors.values())
    for name, p in sorted(raw, key=lambda x: x[1] - int(x[1]), reverse=True)[:short]:
        floors[name] += 1
    return floors


# `date.weekday()`: Monday = 0 … Sunday = 6
_WEEKDAYS = ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון")


def day_label(date_str, today) -> str:
    """Day header in "Recent transactions" (Matan, 30.9): "היום", "אתמול", and
    otherwise "ראשון, 27.9", with the year only when it isn't the current one."""
    import datetime
    try:
        d = datetime.date.fromisoformat(str(date_str)[:10])
    except (TypeError, ValueError):
        return ""
    if d == today:
        return "היום"
    if d == today - datetime.timedelta(days=1):
        return "אתמול"
    label = f"{_WEEKDAYS[d.weekday()]}, {d.day}.{d.month}"
    return label if d.year == today.year else f"{label}.{d.year}"


def greeting(hour: int) -> str:
    """The greeting at the top of the home screen, by Israel time (Matan, 12.10, instead of "שלום")."""
    if 5 <= hour < 12:
        return "בוקר טוב"
    if 12 <= hour < 17:
        return "צהריים טובים"
    if 17 <= hour < 22:
        return "ערב טוב"
    return "לילה טוב"


def next_charge_label(d, today) -> str:
    """"הבא: היום" / "הבא: מחר" / "הבא: 1.11": no year unless it is a different one."""
    if d is None:
        return ""
    days = (d - today).days
    if days == 0:
        return "הבא: היום"
    if days == 1:
        return "הבא: מחר"
    text = f"{d.day}.{d.month}"
    if d.year != today.year:
        text += f".{str(d.year)[2:]}"
    return "הבא: " + text
