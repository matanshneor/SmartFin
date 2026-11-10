"""Entrance on scroll (Matan, 11.10): "what comes in should start coming in
slowly with the scroll".

A card below the screen comes in when you scroll to it, tied to the scroll
position: not a time-based animation that runs on load when nobody is looking. In
the browser (Chrome, sample data): opacity rises gradually with the scroll (0.30 →
0.51 → 0.73 → 0.94 → 1), at the end of every page no card is faded, and on the
fallback path (without animation-timeline) a card that was skipped over appears
when you scroll to it.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
_MOTION = (_ROOT / "frontend/static/js/motion.js").read_text(encoding="utf-8")


def test_cards_below_the_fold_enter_with_the_scroll():
    block = _CSS[_CSS.index("@supports (animation-timeline: view()) {"):]
    block = block[:block.index("}\n}")]
    assert "animation-timeline: view(block);" in block
    assert "animation-range: entry 0% entry 140px;" in block      # the same distance for every card


def test_the_reveal_moves_with_translate_not_transform():
    """transform overrides the swipes (the "השבוע" card writes transform)."""
    keyframes = _CSS[_CSS.index("@keyframes reveal-in {"):]
    keyframes = keyframes[:keyframes.index("}\n}")]
    assert "translate:" in keyframes and "transform" not in keyframes


def test_only_cards_below_the_fold_wait_for_the_scroll():
    assert "if (!belowTheFold(el)) return;" in _MOTION
    assert "const below = all.filter(belowTheFold);" in _MOTION


def test_older_browsers_get_a_soft_entrance_instead():
    assert "CSS.supports('animation-timeline: view()')" in _MOTION
    assert "new IntersectionObserver(" in _MOTION
    assert ".reveal-fallback.is-in { opacity: 1; translate: 0 0; }" in _CSS


def test_reduced_motion_shows_everything_at_once():
    assert "if (reduceMotion) return;" in _MOTION[_MOTION.index("function setupReveal"):]
    rm = _CSS[_CSS.rindex("@media (prefers-reduced-motion: reduce) {", 0, _CSS.index(".reveal-fallback.is-in")+400):]
    assert ".reveal-scroll { animation: none; }" in rm


def test_new_content_after_a_soft_swap_also_enters_with_the_scroll():
    hook = _MOTION[_MOTION.index("window.addEventListener('sf:refreshed'"):]
    assert "setupReveal(" in hook[:hook.index("});")]
