"""
Tests for what the Service Worker keeps in cache.

It served cached pages when the network was weak. That is right for a content
page, and not for a page showing money. A user on a poor cellular connection got a
full, styled screen with "נשאר בעו״ש ₪1,270" from the previous visit, maybe from
days ago. The count-up animation even ran on them, so they looked fresh. Nothing
indicated it was stale.

A clear message that there is no connection is better than a false number,
especially when someone might make a financial decision based on that screen.

Tests on the file's content: there is no Service Worker environment here to run in.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SW = (Path(__file__).resolve().parent.parent
       / "frontend/static/sw.js").read_text(encoding="utf-8")


def _cacheable():
    m = re.search(r"CACHEABLE_PAGES\s*=\s*\[([^\]]*)\]", _SW)
    assert m, "the list of cacheable pages wasn't found"
    return set(re.findall(r"'([^']+)'", m.group(1)))


# ─── What isn't cached ───────────────────────────────────────────────────────

@pytest.mark.parametrize("page,what", [
    ("/",          "יתרה ועסקאות אחרונות"),
    ("/month",     "כל מספרי החודש והגרפים"),
    ("/months",    "היסטוריה חודשית"),
    ("/projects",  "סכומי פרויקטים"),
    ("/settings",  "קוד הזמנה, טלפון ומקום עבודה"),
    ("/login",     "המייל השמור בטופס"),
])
def test_pages_with_money_or_personal_data_are_never_served_from_cache(page, what):
    assert page not in _cacheable(), f"{page} is cached: {what}"


def test_only_the_legal_pages_are_cacheable():
    """They are identical for all users and don't change. Everything else isn't."""
    assert _cacheable() == {"/privacy", "/terms"}


def test_api_calls_are_still_never_intercepted():
    """Control: transaction data changes all the time, and silently serving a stale
    response is worse than a clear network error."""
    assert "url.pathname.startsWith('/api/')" in _SW


def test_static_assets_are_still_cached():
    """Control: without this every load downloads 99KB of CSS and 77KB of JS again."""
    assert "url.pathname.startsWith('/static/')" in _SW
    assert "stale-while-revalidate" in _SW or "cache.put(e.request, res.clone())" in _SW


# ─── What is shown instead ───────────────────────────────────────────────────

def test_there_is_an_offline_page_and_it_says_why_there_are_no_numbers():
    """"No connection" alone leaves the user wondering whether their data was lost."""
    assert "function offlinePage()" in _SW
    assert "אין חיבור לאינטרנט" in _SW
    assert "לא מוצגים כאן נתונים ישנים" in _SW


def test_the_offline_page_offers_a_way_forward():
    """An app installed on a phone has no refresh button on screen, so without a
    button the only way forward is guessing."""
    assert "location.reload()" in _SW


def test_the_offline_page_looks_like_the_app():
    """The stylesheet is already cached, so this is free, and the difference is
    between an app screen and a raw browser error."""
    assert "/static/css/style.css" in _SW


def test_the_installed_app_gets_our_offline_page_not_the_browsers():
    """start_url in the manifest is /. When it didn't go through the Service Worker,
    opening the app with no network showed the browser's error screen."""
    import json
    manifest = json.loads((Path(__file__).resolve().parent.parent
                           / "frontend/static/manifest.json").read_text(encoding="utf-8"))

    # ?app=1 marks opening from the icon; the Service Worker looks at pathname only
    assert manifest["start_url"].split("?")[0] == "/"
    assert "/" not in _cacheable()
    # / falls into the fetch-with-fallback path and not into an early return
    # (since 3.10: first a response loaded on touch, if any; otherwise network; and
    # with no network, our screen)
    block = _SW[_SW.index("if (!CACHEABLE_PAGES.includes(url.pathname)) {"):]
    block = block[:block.index("return;")]
    assert ".then(res => res || fetch(e.request))" in block
    assert ".catch(() => offlinePage())" in block


def test_old_caches_are_purged_when_the_version_changes():
    """This is the mechanism that makes the fix reach existing users: without this
    deletion they keep getting what is already cached on their side, and changing
    the version is worth nothing.

    The version itself isn't pinned here on purpose: it is meant to change on every
    deploy touching assets, and a test that needs editing every time is a test
    people stop respecting and start updating automatically without reading."""
    assert re.search(r"const CACHE\s*=\s*'smartfin-v\d+'", _SW), \
        "no cache version constant"
    activate = _SW[_SW.index("'activate'"):_SW.index("'fetch'")]
    assert "keys.filter(k => k !== CACHE)" in activate and "caches.delete" in activate


def test_nothing_is_precached_by_url_any_more():
    """The list was removed on purpose, not out of carelessness.

    Since asset URLs carry a content hash (?v=…) set on the server, a fixed list
    here can't know it. It would download unhashed URLs no page asks for: a double
    download of every file, and a cache nobody touches.

    And it is redundant anyway: a hashed URL is served with a one-year lifetime, so
    the browser caches it by itself from the first visit."""
    assert "addAll" not in _SW
    assert "PRECACHE" not in _SW
