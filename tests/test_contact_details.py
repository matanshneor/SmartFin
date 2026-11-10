"""
The privacy policy pointed to contact details that didn't exist.

The section said "you can get in touch through the contact details in the app",
and the app had no `mailto`, no "contact us", no address. That is a GDPR promise
(in the same section that lists the rights of access and erasure) with no
mechanism behind it. And it is also the only channel a stuck user would look for.

The legal pages are open to the whole internet and marked for indexing, so the
address there is assembled in the browser: real text in the DOM for screen
readers and copying, and nothing for a crawler that doesn't run JavaScript. In
settings, behind sign-in, it is visible as usual.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_TPL  = _ROOT / "frontend/templates"

_ADDRESS = "matanshneor1@gmail.com"
_USER, _DOMAIN = _ADDRESS.split("@")


def _read(name):
    return (_TPL / name).read_text(encoding="utf-8")


# ─── The promise is kept ────────────────────────────────────────────────────

def test_the_privacy_policy_no_longer_points_at_nothing():
    privacy = _read("privacy.html")

    assert "פרטי הקשר\n        המופיעים באפליקציה" not in privacy
    assert 'data-contact' in privacy


@pytest.mark.parametrize("page", ["privacy.html", "terms.html"])
def test_both_legal_pages_have_a_way_to_make_contact(page):
    html = _read(page)

    assert "יצירת קשר" in html
    assert f'data-user="{_USER}"' in html
    assert f'data-domain="{_DOMAIN}"' in html


def test_a_signed_in_user_finds_it_where_things_break():
    """Whoever has something not working is in the app, not on the legal page."""
    settings = _read("settings.html")

    assert f"mailto:{_ADDRESS}" in settings


def test_contact_is_not_buried_inside_a_collapsed_group():
    """It was inside "security and account", above changing the password and
    deleting the account: a closed accordion nobody opens to ask for help. The
    test measures this on the structure: the link must sit outside every
    `settings-group-body`."""
    settings = _read("settings.html")
    link_at = settings.index(f"mailto:{_ADDRESS}")

    before = settings[:link_at]
    opened = before.count('class="settings-group-body"')
    closed = before.count("</section>")

    assert opened <= closed, (
        "the link sits inside a collapsible group: someone who needs help won't open it"
    )


# ─── Not exposing it to bots on the open pages ──────────────────────────────

@pytest.mark.parametrize("page", ["privacy.html", "terms.html"])
def test_the_public_pages_never_spell_the_address_out(page):
    """A public page marked for indexing is exactly what gets harvested. A crawler
    looks for `mailto:` and a string with `@`, and there is neither there."""
    html = _read(page)

    assert _ADDRESS not in html, "the address is written out in full"
    assert "mailto:" not in html


@pytest.mark.parametrize("page", ["privacy.html", "terms.html"])
def test_there_is_still_a_way_without_javascript(page):
    """Without JavaScript there is no link, and there must remain a way to get in touch."""
    html = _read(page)
    block = html[html.index("data-contact"):]
    block = block[:block.index("</span>")]

    assert "<noscript>" in block
    assert _USER in block and "gmail" in block


@pytest.mark.parametrize("page", ["privacy.html", "terms.html"])
def test_the_page_actually_loads_the_script(page):
    assert "js/contact.js" in _read(page)


# ─── What is accessible to humans stays accessible ──────────────────────────

def test_the_assembled_address_is_real_text_not_a_picture():
    """A screen reader needs to read it out, and a user needs to select and copy it."""
    js = (_ROOT / "frontend/static/js/contact.js").read_text(encoding="utf-8")

    assert "link.textContent = address" in js
    assert "link.href = 'mailto:' + address" in js


def test_the_noscript_fallback_hides_its_punctuation_from_screen_readers():
    """`[at]` and `[dot]` are meant for the eye, not the ear: a screen reader
    reading them out turns a simple address into a riddle."""
    privacy = _read("privacy.html")
    block = privacy[privacy.index("<noscript>"):]
    block = block[:block.index("</noscript>")]

    assert block.count('aria-hidden="true"') == 2
