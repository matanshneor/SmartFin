"""Stage 5 of the design pass (11.11.2026): wording and icons.

Emoji stay only where the user picks them (categories, projects, the icon
picker); decorative ones became the app's line icons (`backend/icons.py`).
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_DECORATIVE = ("📊", "📉", "📈", "✨", "👋", "🎉", "📌", "🔓", "🎨", "💼", "💰")
# Data the user chooses from, or default category sets
_USER_CHOICE = {"icon-picker.js"}


def _ui_files():
    for p in sorted((_ROOT / "frontend/templates").glob("*.html")):
        yield p
    for p in sorted((_ROOT / "frontend/static/js").glob("*.js")):
        if p.name not in _USER_CHOICE:
            yield p


@pytest.mark.parametrize("path", list(_ui_files()), ids=lambda p: p.name)
def test_no_decorative_emoji(path):
    # The project ideas carry the icon the new project will have (user data)
    text = "\n".join(l for l in path.read_text(encoding="utf-8").split("\n")
                     if "for icon, name in [" not in l)
    found = [e for e in _DECORATIVE if e in text]
    assert not found, f"{path.name}: {found}"


def test_the_new_line_icons_exist():
    from backend.icons import _PATHS
    for name in ("bar-chart", "trend-down", "trend-up", "users", "plus-circle"):
        assert name in _PATHS


_SINGULAR = ("רק לך", "עבורך", "תיפתח לך", "השם שלך", "אליך ", "בעצמך", "החשבון שלך")


@pytest.mark.parametrize("path", list(_ui_files()), ids=lambda p: p.name)
def test_one_form_of_address(path):
    """The app speaks in the plural ("אתם"); a personal matter is worded without
    addressing anyone ("גלוי רק למי שיצר אותו")"""
    text = path.read_text(encoding="utf-8")
    found = [w for w in _SINGULAR if w in text]
    assert not found, f"{path.name}: {found}"


def test_requests_say_na():
    """"נא ל…" in every validation message, as in sixteen places already"""
    for name in ("projects.js", "project-edit.js", "import.js"):
        js = (_ROOT / "frontend/static/js" / name).read_text(encoding="utf-8")
        assert "'יש לבחור" not in js and "'בחרו " not in js, name
    assert '"יש לבחור' not in (_ROOT / "backend/app.py").read_text(encoding="utf-8")


def test_every_failure_message_says_what_to_do():
    """"השמירה נכשלה" left the user with nothing to do; like the import's
    "הייבוא נכשל — נסו שוב", every one now ends with the next step"""
    import re
    for path in sorted((_ROOT / "frontend/static/js").glob("*.js")):
        for m in re.finditer(r"'([^'\n]{0,40}נכשל[הו]?[^'\n]{0,20})'", path.read_text(encoding="utf-8")):
            assert "נסו שוב" in m.group(1), f"{path.name}: {m.group(1)}"
