"""Stage 1 of the design pass (11.11.2026): faults seen in screenshots, not taste.

Each of these was visible in the harness shots (`tests/visual/`):

- The "add to home screen" bar (z-index 950) floated over the transaction
  sheet, covered the row above Save, and hid the "added / undo" toast entirely,
  because both sat at the same height above the dock.
- With 12 categories and an active project, Save sat below the fold of the
  sheet on a 390×844 phone.
- At 320px with extra-large text, the dock labels touched each other and
  "הגדרות" ran under the +.
- The reduced-motion rule shortened endless animations to 0.01ms a cycle
  instead of stopping them, so the scan line and the spinner flickered.
- Placeholder text was 2.2:1 against the input, the "off" switch track 1.5:1.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")


def _rule(selector):
    i = _CSS.index(selector + " {")
    return _CSS[i:_CSS.index("}", i)]


def _luminance(hexstr):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexstr[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _ratio(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _token(name, block=":root {"):
    start = _CSS.index(block)
    m = re.search(rf"--{name}:\s*(#[0-9A-Fa-f]{{6}})", _CSS[start:])
    assert m, name
    return m.group(1)


# ─── The install / notifications bar ──────────────────────────────────

@pytest.mark.parametrize("overlay", [
    ".modal-overlay.open", ".confirm-overlay.open", ".color-sheet:not([hidden])",
    ".icon-picker:not([hidden])", ".search-screen:not([hidden])",
    ".tx-screen:not([hidden])", ".receipt-viewer:not([hidden])", ".yr-overlay:not([hidden])",
])
def test_every_overlay_hides_the_install_bar(overlay):
    m = re.search(r"body:has\(([^{]*)\)\s*\.install-bar\s*\{\s*display:\s*none", _CSS)
    assert m, "no rule hides the bar while an overlay is open"
    assert overlay in m.group(1)


def test_the_toast_rises_above_the_install_bar():
    rule = _rule("body.has-install-bar .toast")
    assert "bottom:" in rule
    # Above the bar: its offset (10px) + its height (~62px) + a gap
    assert "+ 82px" in rule


# ─── Save in the transaction sheet ────────────────────────────────────

def test_save_sticks_to_the_bottom_of_the_sheet():
    rule = _rule("#modalOverlay label.submit-btn")
    assert "position: sticky" in rule
    assert "bottom:" in rule
    # The ring in the sheet's colour hides rows scrolling behind the button
    assert "var(--color-bg-elevated)" in rule


# ─── The dock on a narrow screen with large text ──────────────────────

def test_dock_labels_fit_at_320_with_large_text():
    block = _CSS[_CSS.index("/* The dock on a narrow screen with large text"):]
    block = block[:block.index("\n}\n")]
    assert "@media (max-width: 380px)" in block
    assert 'html[data-text-size="xlarge"] .nav-item .nav-label' in block
    assert 'html[data-text-size="large"] .nav-item .nav-label' in block
    assert ".nav-item { flex: 1 1 auto; }" in block
    assert ".nav-item { min-width: 0; }" in _CSS


def test_dock_labels_are_not_spaced_apart():
    """Hebrew tracking (test_hebrew_tracking): the dock was the last label at +0.02em"""
    assert "letter-spacing" not in _rule(".nav-item .nav-label")


# ─── Reduced motion ───────────────────────────────────────────────────

def test_reduced_motion_stops_endless_animations():
    i = _CSS.index("/* ─── Reduced motion")
    block = _CSS[i:i + 1500]
    assert "animation-iteration-count: 1 !important" in block


def test_charts_respect_reduced_motion():
    js = (_ROOT / "frontend/static/js/chart-setup.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in js
    assert "Chart.defaults.animation = false" in js


# ─── Contrast that was below the standard ─────────────────────────────

def test_placeholder_is_readable_on_inputs():
    rule = _rule(".form-input::placeholder")
    assert "var(--color-text-muted)" in rule
    muted = _token("color-text-muted")
    for bg in ("#FFFFFF", _token("color-input")):
        assert _ratio(muted, bg) >= 4.5, bg


@pytest.mark.parametrize("block,card", [(":root {", "#FFFFFF"),
                                         (':root[data-theme="dark"] {', "#211B12")])
def test_the_off_switch_track_is_visible(block, card):
    assert _ratio(_token("toggle-off", block), card) >= 3


def test_the_income_tab_passes_with_white_text():
    rule = _rule(".modal-sheet.income-mode .toggle-btn.active")
    stops = re.findall(r"#[0-9A-Fa-f]{6}", re.search(r"background:[^;]*", rule).group(0))
    assert stops
    for stop in stops:
        assert _ratio("#FFFFFF", stop) >= 4.5, stop


def test_switch_focus_ring_passes_on_light_surfaces():
    assert re.search(r':root:not\(\[data-theme="dark"\]\)[^{]*focus-visible[^{]*\{[^}]*'
                     r'outline-color:\s*var\(--color-gold-text\)', _CSS)


# ─── The month strip ──────────────────────────────────────────────────

def test_the_month_strip_is_centred_again_once_the_font_loads():
    """Measured in the fallback font, the chips change width when Rubik arrives:
    two loads of the same page settled a few pixels apart in the harness."""
    js = (_ROOT / "frontend/static/js/month.js").read_text(encoding="utf-8")
    assert "document.fonts.ready.then(centerStrip)" in js


# ─── Focus and screen readers ─────────────────────────────────────────

def _js(name):
    return (_ROOT / "frontend/static/js" / name).read_text(encoding="utf-8")


def test_one_focus_trap_covers_every_open_dialog():
    core = _js("core.js")
    i = core.index("Focus stays inside an open dialog")
    block = core[i:]
    assert "[aria-modal=\"true\"]" in block
    # The sheet and the confirm keep their own trap; this one steps aside
    assert "e.defaultPrevented" in block


def test_all_transactions_moves_focus_in_and_back():
    js = _js("month.js")
    assert "back.focus({ preventScroll: true })" in js
    assert "opener.focus({ preventScroll: true })" in js


def test_the_invite_sheet_takes_focus_and_lets_go_of_escape():
    js = _js("welcome-invite.js")
    assert "sendBtn.focus(" in js
    assert "removeEventListener('keydown', onKey)" in js


def test_onboarding_moves_focus_to_the_new_step():
    js = _js("onboarding.js")
    assert "title.focus({ preventScroll: true })" in js
    assert "' מתוך 3'" in js


def test_the_home_row_says_what_the_eye_sees():
    """The label replaced the row's content and dropped income vs expense, the
    day and the description"""
    tpl = (_ROOT / "frontend/templates/index.html").read_text(encoding="utf-8")
    i = tpl.index('aria-label="עריכת {{ tx.category_name }}')
    label = tpl[i:tpl.index('">', i)]
    for part in ("'הוצאה'", "'הכנסה'", "day_label", "tx.description"):
        assert part in label


@pytest.mark.parametrize("js,attr", [
    ("login.js", "tabSignup.setAttribute('aria-pressed'"),
    ("projects.js", "ownerPersonalBtn.setAttribute('aria-pressed'"),
    ("transactions.js", "chip.setAttribute('aria-pressed', on ? 'true' : 'false')"),
])
def test_selected_state_is_announced(js, attr):
    assert attr in _js(js)


def test_missing_focus_rings_are_drawn():
    i = _CSS.index("/* Focus rings that were missing")
    block = _CSS[i:i + 1200]
    for sel in (".search-bar:focus-within", ".checkbox-input:focus-visible + .checkbox-custom",
                ".import-file-btn:has(+ input:focus-visible)"):
        assert sel + " { outline: 2px solid var(--color-gold-text)" in block, sel


@pytest.mark.parametrize("n", [3, 4, 5])
def test_name_tags_pass_for_text(n):
    i = _CSS.index(f"\n.owner-{n} {{")
    rule = _CSS[i:_CSS.index("}", i)]
    color = re.search(r"color:\s*(#[0-9A-Fa-f]{6})", rule).group(1)
    assert _ratio(color, "#FFFFFF") >= 4.5
    # The member chart's bars use the same colours
    app = (_ROOT / "backend/app.py").read_text(encoding="utf-8")
    assert f'{n}: "{color}"' in app


def test_a_soft_refresh_across_a_deploy_reloads_fully():
    """New markup under old CSS: a page left open while a release went out"""
    core = _js("core.js")
    apply = core[core.index("function sfApplyHtml"):]
    apply = apply[:apply.index("pairs.forEach(function (pair) { pair[0].replaceWith")]
    assert "if (assets(doc) !== assets(document)) return 'full';" in apply


@pytest.mark.parametrize("ink,card", [((28, 25, 23), (255, 255, 255)), ((255, 244, 214), (0x21, 0x1B, 0x12))])
def test_the_input_edge_passes_three_to_one(ink, card):
    """Matan chose 3:1 from the side-by-side samples (11.11)"""
    alpha = float(re.search(r"border: 1px solid rgba\(var\(--ink-rgb\), ([0-9.]+)\)", _rule(".form-input")).group(1))
    mix = "#" + "".join(f"{round(alpha * i + (1 - alpha) * c):02X}" for i, c in zip(ink, card))
    assert _ratio(mix, "#%02X%02X%02X" % card) >= 3


def test_the_chart_palette_is_option_a():
    """Matan chose option A from the samples (11.11): warmer and quieter, and
    still about as far apart as the old set (smallest CIE76 distance >= 25)"""
    import itertools, math
    js = _js("chart-setup.js")
    palette = re.findall(r"'(#[0-9A-Fa-f]{6})'", js[js.index("var COLORS = ["):js.index("];")])
    assert palette[4:6] == ["#8B4A6B", "#9CC4B6"] and palette[9:11] == ["#D0703A", "#7E8FB3"]

    def lab(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        x = (c[0] * .4124 + c[1] * .3576 + c[2] * .1805) / .95047
        y = c[0] * .2126 + c[1] * .7152 + c[2] * .0722
        z = (c[0] * .0193 + c[1] * .1192 + c[2] * .9505) / 1.08883
        f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
        return (116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z)))
    assert min(math.dist(lab(a), lab(b)) for a, b in itertools.combinations(palette, 2)) >= 25
