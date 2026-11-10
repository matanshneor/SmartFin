"""The installed app (round 4, E6).

Whoever installed SmartFin to the home screen before signing in saw the marketing
page on first open and not the sign-in: the app installed on iPhone has its own
cookies, and `sf_returning` wasn't in them yet. Opening from the icon is now
marked (`?app=1`).
"""
import json
import re
from pathlib import Path

import pytest

from backend.app import app, limiter

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_MANIFEST = json.loads((_ROOT / "frontend/static/manifest.json").read_text(encoding="utf-8"))


@pytest.fixture
def guest():
    limiter.reset()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_opening_from_the_home_screen_goes_to_login(guest):
    r = guest.get(_MANIFEST["start_url"])

    assert r.status_code == 302 and r.headers["Location"].endswith("/login")


def test_a_link_in_the_browser_still_shows_the_landing_page(guest):
    """Control: the landing page stays for whoever arrives from a link."""
    r = guest.get("/")

    assert r.status_code == 200 and "/login" not in r.headers.get("Location", "")


def test_the_add_shortcut_is_also_an_app_launch(guest):
    r = guest.get(_MANIFEST["shortcuts"][0]["url"])

    assert r.status_code == 302 and r.headers["Location"].endswith("/login")


def test_the_app_keeps_its_identity_when_the_start_url_changes():
    """Without `id`, Android derives the app's identity from `start_url`, and
    changing it would turn the installed one into "another app"."""
    assert _MANIFEST["id"] == "/"


def test_budget_fields_open_the_number_keyboard():
    places = [(_ROOT / "frontend/templates" / f).read_text(encoding="utf-8")
              for f in ("projects.html", "project_edit.html", "settings.html")]
    places.append((_ROOT / "frontend/static/js/settings.js").read_text(encoding="utf-8"))
    for text in places:
        for tag in re.findall(r"<input[^>]*type=\"number\"[^>]*>", text, re.S):
            assert "inputmode=" in tag, tag[:90]


def test_a_new_familys_name_field_starts_empty():
    """"המשפחה שלי" inside the field had to be deleted before typing; now it is a
    grey example."""
    from flask import render_template_string
    src = (_ROOT / "frontend/templates/onboarding.html").read_text(encoding="utf-8")
    tag = src[src.index('id="familyName"'):]
    tag = tag[:tag.index(">")]
    with app.test_request_context():
        assert 'value=""' in render_template_string(tag, family={"name": "המשפחה שלי"})
        assert 'value="משפחת כהן"' in render_template_string(tag, family={"name": "משפחת כהן"})


# ─── The icon ───────────────────────────────────────────────────────────────
#
# The previous icon had rounded corners and a frame of its own, with transparent
# corners. iPhone and Android round it themselves: a double rounding, a clipped
# frame, and transparent corners the phone paints. And "any maskable" on the same
# file cut off the tip of the arrow on Android.

@pytest.mark.parametrize("name", ["icon-180.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png"])
def test_home_screen_icons_have_no_transparent_corners(name):
    data = (_ROOT / "frontend/static/icons" / name).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    color_type = data[25]                      # IHDR: 2 = RGB, 6 = RGBA
    assert color_type == 2, f"{name}: has an alpha channel"


def test_the_maskable_icon_is_its_own_file():
    purposes = {i["src"].rsplit("/", 1)[-1]: i["purpose"] for i in _MANIFEST["icons"]}
    assert purposes["icon-maskable-512.png"] == "maskable"
    assert all(p == "any" for n, p in purposes.items() if n != "icon-maskable-512.png")


def test_the_maskable_arrow_stays_inside_the_android_safe_zone():
    """Android guarantees only a circle with a 40% radius from the centre; what is
    outside it may be cut off."""
    svg = (_ROOT / "frontend/static/icons/icon-maskable.svg").read_text(encoding="utf-8")
    tx, ty, k = map(float, re.search(r"translate\(([\d.]+) ([\d.]+)\) scale\(([\d.]+)\)", svg).groups())
    stroke = float(re.search(r'stroke-width="([\d.]+)"', svg).group(1))
    points = [tuple(map(float, p)) for p in re.findall(r"(\d+)[ ,](\d+)", " ".join(re.findall(r'd="([^"]+)"', svg)))]
    # "H158" and "V100" give the corner (158, 60), already between the points
    for x, y in points:
        px, py = tx + (x - tx) * k, ty + (y - ty) * k
        reach = ((px - 96) ** 2 + (py - 96) ** 2) ** 0.5 + stroke * k / 2
        assert reach <= 0.40 * 192, (x, y, round(reach, 1))
