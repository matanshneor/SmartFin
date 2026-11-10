"""
The link sent on WhatsApp, and the invitation copied with it.

This is what really happens when Matan spreads the app. A link without share
tags shows as a bare line of text (no name, no description and no image), and that
looks suspicious exactly at the moment people are asked to hand over money data.

And sharing the invitation copied six characters and nothing else. Whoever got
"K4F2QX" got six characters with no link and no explanation, while the landing
page sells it as "invite your household with a six-character code".
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT   = Path(__file__).resolve().parent.parent
_TPL    = _ROOT / "frontend/templates"
_JS     = _ROOT / "frontend/static/js"
_PUBLIC = ["landing.html", "login.html", "privacy.html", "terms.html"]


def _read(name):
    return (_TPL / name).read_text(encoding="utf-8")


# ─── A preview when shared ──────────────────────────────────────────────────

@pytest.mark.parametrize("page", _PUBLIC)
def test_every_public_page_previews_properly(page):
    html = _read(page)

    for tag in ("og:title", "og:description", "og:image", "og:url", "og:type"):
        assert f'property="{tag}"' in html, f"{page} is missing {tag}"
    assert 'name="twitter:card"' in html


@pytest.mark.parametrize("page", _PUBLIC)
def test_the_preview_image_is_absolute(page):
    """A relative URL isn't resolved by whoever shows the preview: they fetch the
    page from a completely different server."""
    html = _read(page)
    block = html[html.index('property="og:image"'):]
    block = block[:block.index(">") + 1]

    assert "_external=True" in block


@pytest.mark.parametrize("page", _PUBLIC)
def test_each_page_says_what_it_is(page):
    """One title for the whole site makes every link identical, and that is exactly
    what the tags are meant to prevent."""
    html = _read(page)
    block = html[html.index("{% set og_title"):]
    title = block[:block.index("%}")]

    assert "SmartFin" in title
    assert len(title) > 30, "too generic a title"


def test_the_image_exists_and_is_the_right_shape():
    """1200×630 is what WhatsApp and Facebook expect; another ratio gets cropped."""
    png = _ROOT / "frontend/static/icons/og-image.png"

    assert png.exists(), "no preview image"
    data = png.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    width  = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    assert (width, height) == (1200, 630), f"the size is {width}×{height}"


# ─── The invitation that is copied ──────────────────────────────────────────

_HARNESS = r"""
const fs = require('fs'), g = globalThis;
g.window = { location: { origin: 'https://smartfin.up.railway.app' } };
g.document = { querySelectorAll: () => [] };
(0, eval)(fs.readFileSync(process.argv[2], 'utf8'));
console.log(JSON.stringify({
    named:   g.window.sfInviteMessage('K4F2QX', 'משפחת שניאור'),
    unnamed: g.window.sfInviteMessage('K4F2QX', ''),
}));
"""


def _messages():
    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't installed")
    h = _ROOT / "tests" / "_invite_harness.js"
    h.write_text(_HARNESS, encoding="utf-8")
    try:
        out = subprocess.run([node, str(h), str(_JS / "clipboard.js")],
                             capture_output=True, text=True, timeout=30)
    finally:
        h.unlink(missing_ok=True)
    assert out.returncode == 0, out.stderr[:600]
    return json.loads(out.stdout)


def test_the_invite_carries_a_link_not_just_six_characters():
    """The heart. Without the link, the recipient doesn't know where to go with the code."""
    msg = _messages()["named"]

    assert "K4F2QX" in msg
    # Since 3.10 (idea 43): a link that joins on tap, with the code inside it
    assert "https://smartfin.up.railway.app/join/K4F2QX" in msg


def test_the_invite_says_who_is_inviting():
    msg = _messages()["named"]

    assert "משפחת שניאור" in msg
    assert "SmartFin" in msg


def test_a_family_without_a_name_still_reads_properly():
    """Control: the family name is optional, and a message with a hole in the middle
    is worse than a generic message."""
    msg = _messages()["unnamed"]

    assert '""' not in msg
    assert "המשפחה שלנו" in msg
    assert "K4F2QX" in msg


def test_the_code_sits_on_its_own_line():
    """So it can be selected with a long press without dragging a period along."""
    msg = _messages()["named"]

    assert "K4F2QX" in msg.split("\n"), "the code on its own line"
