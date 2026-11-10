"""
Contrast tests.

SmartFin's gold (#A67C00) gave 3.82:1 on white, below the 4.5 the standard
requires for normal text. It touched the whole app, including the primary
button: white on the light end of the gradient gave 2.75:1, and that is the
button for sign-in, signup and the setup wizard.

The chosen fix: the gold itself stays for icons, borders and lines (where the
standard requires 3:1 and it passes), and a darker token is used for text only,
so the visual identity doesn't move.

The tests compute the ratio from the CSS itself and don't hard-code numbers, so
every future change to the palette is checked again.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_CSS = (Path(__file__).resolve().parent.parent
        / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _token(name):
    m = re.search(rf"--{name}:\s*(#[0-9A-Fa-f]{{6}})", _CSS)
    assert m, f"token --{name} not found"
    return m.group(1)


def _gradient_stops():
    m = re.search(r"--gradient-gold:\s*[^;]*?(#[0-9A-Fa-f]{6})[^;]*?(#[0-9A-Fa-f]{6})", _CSS)
    assert m, "gradient not found"
    return m.group(1), m.group(2)


def _luminance(hexstr):
    channels = (int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))
    def linear(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (linear(c) for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a, b):
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _over(fg, bg, alpha):
    """A translucent colour over an opaque background: what the eye really sees."""
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(round(f[i] * alpha + b[i] * (1 - alpha)) for i in range(3))


WHITE = "#FFFFFF"


# ─── Text: 4.5:1 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("surface,name", [
    (WHITE,                "כרטיס לבן"),
    (None,                 "רקע העמוד"),
])
def test_gold_text_is_readable(surface, name):
    bg = surface or _token("color-bg")
    r = _ratio(_token("color-gold-text"), bg)

    assert r >= 4.5, f"gold text on {name}: {r:.2f}:1"


def test_gold_text_on_a_tinted_badge_is_readable():
    """The hard case, and the one easy to miss: a tag with a gold background at
    10% opacity, over the page background. Computing against white only would
    pass and leave this broken."""
    tinted = _over(_token("color-gold"), _token("color-bg"), 0.10)
    r = _ratio(_token("color-gold-text"), tinted)

    assert r >= 4.5, f"{r:.2f}:1"


@pytest.mark.parametrize("stop", [0, 1])
def test_white_on_the_primary_button_is_readable(stop):
    """A gradient is checked at both ends: the light one is the one that fails."""
    r = _ratio(WHITE, _gradient_stops()[stop])

    assert r >= 4.5, f"stop {stop}: {r:.2f}:1"


def test_muted_text_is_readable():
    r = _ratio(_token("color-text-muted"), WHITE)
    assert r >= 4.5, f"{r:.2f}:1"


# ─── Non-text: 3:1 ──────────────────────────────────────────────────────────

def test_borders_and_icons_keep_the_brand_gold():
    """Control: the original gold stays, and passes the requirement for non-text
    content. Without this 'we fixed accessibility' would mean 'we changed how the
    app looks'."""
    gold = _token("color-gold")

    assert gold == "#A67C00", "the brand tint changed"
    assert _ratio(gold, WHITE) >= 3.0


def test_nothing_still_uses_the_brand_gold_as_text():
    """`color:` is text; borders and fills use other properties."""
    leftovers = re.findall(r"(?<![-\w])color:\s*var\(--color-gold\)", _CSS)

    assert not leftovers, f"{len(leftovers)} uses of the brand gold as text"


# ─── The rest of the palette ────────────────────────────────────────────────

@pytest.mark.parametrize("token,what", [
    ("color-savings", "סכומי חיסכון"),
    ("color-income",  "סכומי הכנסה"),
    ("color-expense", "סכומי הוצאה"),
    ("color-text",    "טקסט רגיל"),
    ("color-text-soft", "טקסט משני"),
])
def test_every_colour_used_for_money_is_readable(token, what):
    """Amounts are the text it matters most to read right in this app."""
    for bg, name in ((WHITE, "כרטיס"), (_token("color-bg"), "רקע העמוד")):
        r = _ratio(_token(token), bg)
        assert r >= 4.5, f"{what} on {name}: {r:.2f}:1"


@pytest.mark.parametrize("token", [
    "color-expense-light", "color-income-light", "color-savings-light",
])
def test_the_light_variants_are_checked_against_the_dark_hero(token):
    """They are used only inside the KPI on the dark background. Comparing against
    white would show a false failure and send you to fix a correct colour."""
    r = _ratio(_token(token), _token("hero-bg"))

    assert r >= 4.5, f"{r:.2f}:1"


def test_the_hero_text_is_checked_against_the_hero():
    r = _ratio(_token("hero-text"), _token("hero-bg"))
    assert r >= 4.5, f"{r:.2f}:1"
