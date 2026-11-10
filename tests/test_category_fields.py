"""A category's name and icon are checked on the server, not only on the screen.

The screen has a `maxlength`, the server had nothing: a family member calling
the API directly could save an icon like `<style>…</style>`, and it went into the
`innerHTML` of the in-between row on the home page for the whole family. And
`{"name": 5}` crashed the route with a 500.
"""
import pytest

from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME  = "22222222-2222-2222-2222-222222222222"

# The four routes that write a name and icon
_ROUTES = [
    ("post", "/api/categories",                  {"type": "expense"}),
    ("put",  "/api/categories/c1",               {}),
    ("post", "/api/projects/p1/categories",      {"type": "expense"}),
    ("put",  "/api/projects/p1/categories/c1",   {}),
]


@pytest.fixture
def client(monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True
    wrote = []
    def fake(name):
        def write(*a, **k):
            wrote.append(name)
            return ({"id": "c1"}, None) if name.startswith("add") else True
        return write
    for fn in ("add_custom_category", "update_category",
               "add_project_category", "update_project_category"):
        monkeypatch.setattr(db, fn, fake(fn))
    monkeypatch.setattr(db, "get_project_for_transaction",
                        lambda pid, fid: {"id": pid, "owner_id": None})
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        yield c, wrote


@pytest.mark.parametrize("method,url,extra", _ROUTES, ids=[r[1] + ":" + r[0] for r in _ROUTES])
@pytest.mark.parametrize("fields", [
    {"name": "סופר", "icon": "<style>body{display:none}</style>"},
    # Short enough to pass the length limit: caught only on the characters themselves
    {"name": "סופר", "icon": "<img>"},
    {"name": "סופר", "icon": "🛒" * 20},
    {"name": "x" * 31, "icon": "🛒"},
    {"name": 5, "icon": "🛒"},
    {"name": "סופר", "icon": ["🛒"]},
], ids=["html-icon", "short-html-icon", "long-icon", "long-name", "number-name", "list-icon"])
def test_bad_fields_are_refused_before_anything_is_written(client, method, url, extra, fields):
    c, wrote = client

    res = getattr(c, method)(url, json={**extra, **fields})

    assert res.status_code == 422
    assert wrote == []


@pytest.mark.parametrize("method,url,extra", _ROUTES, ids=[r[1] + ":" + r[0] for r in _ROUTES])
@pytest.mark.parametrize("icon", ["🛒", "👨‍👩‍👧‍👦", "🇮🇱", ""])
def test_real_emoji_still_pass(client, method, url, extra, icon):
    """Control: a compound emoji (family, flag) is longer than one character, and
    still an icon."""
    c, wrote = client

    res = getattr(c, method)(url, json={**extra, "name": "סופר", "icon": icon})

    assert res.status_code in (200, 201), res.get_json()
    assert len(wrote) == 1
