"""Categories: a signup wizard that doesn't confuse, and restoring the defaults
(Matan, 31.10).

· Two people deselected all the categories in the wizard instead of choosing
  them. They all start selected, and the heading said "choose", so "choosing"
  means tapping, and that deselects.
· "For whoever deleted by mistake or wants the default categories": a button in
  settings that adds what is missing, and touches nothing existing.
"""
from pathlib import Path

import pytest

from backend import app as app_module
from backend import supabase_config as db
from backend.app import app, limiter, _DEFAULT_CATEGORIES

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_FAM = "11111111-1111-1111-1111-111111111111"
_ME = "22222222-2222-2222-2222-222222222222"


# ─── The wizard ─────────────────────────────────────────────────────────────

_WIZARD = (_ROOT / "frontend/templates/onboarding.html").read_text(encoding="utf-8")
_WIZARD_JS = (_ROOT / "frontend/static/js/onboarding.js").read_text(encoding="utf-8")


def test_the_wizard_does_not_ask_to_choose_what_is_already_chosen():
    import re
    step = _WIZARD[_WIZARD.index('id="step3"'):]
    step = re.sub(r"\{#.*?#\}", "", step[:step.index("</section>")], flags=re.S)   # no notes
    assert "בחרו את הקטגוריות" not in step
    assert "כל המסומנות כבר נבחרו" in step and "כדי להוריד" in step


def test_every_chip_says_whether_it_is_in():
    assert 'class="cat-btn onboarding-cat-btn active" aria-pressed="true"' in _WIZARD
    assert "aria-pressed" in _WIZARD_JS and "updateCount" in _WIZARD_JS
    assert 'class="onboarding-cat-count"' in _WIZARD


# ─── Restoring the defaults ─────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_limits():
    limiter.reset()
    yield
    limiter.reset()


def _client(monkeypatch, existing):
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "get_categories", lambda *a, **k: existing)
    calls = []

    def _bulk(family_id, cats, start=None):
        calls.append({"family_id": family_id, "cats": cats, "start": start})
        return len(cats), None
    monkeypatch.setattr(app_module.db, "bulk_add_categories", _bulk)
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"], sess["family_id"] = _ME, _FAM
    return c, calls


def _cat(name, type_="expense", order=1, family=_FAM):
    return {"id": name, "name": name, "type": type_, "sort_order": order, "family_id": family}


def test_only_what_is_missing_is_offered_and_added(monkeypatch):
    have = [_cat("סופר ומזון", order=3), _cat("רכב  ותחבורה", order=7),   # a double space: still the same one
            _cat("משכורת", "income", order=2), _cat("קטגוריה שלי", order=9)]
    client, calls = _client(monkeypatch, have)

    missing = client.get("/api/categories/defaults").get_json()["missing"]
    names = {(c["name"], c["type"]) for c in missing}
    assert ("סופר ומזון", "expense") not in names
    assert ("רכב ותחבורה", "expense") not in names
    assert ("משכורת", "income") not in names
    assert ("בריאות", "expense") in names
    assert len(missing) == len(_DEFAULT_CATEGORIES) - 3

    res = client.post("/api/categories/defaults")
    assert res.status_code == 201 and res.get_json()["added"] == len(missing)
    # The new ones go at the end of each department, after the highest order
    # already taken
    assert calls[0]["start"] == {"expense": 9, "income": 2}
    assert calls[0]["family_id"] == _FAM


def test_nothing_missing_writes_nothing(monkeypatch):
    have = [_cat(c["name"], c["type"]) for c in _DEFAULT_CATEGORIES]
    client, calls = _client(monkeypatch, have)
    assert client.get("/api/categories/defaults").get_json()["missing"] == []
    assert client.post("/api/categories/defaults").get_json() == {"status": "ok", "added": 0}
    assert calls == []


def test_a_same_name_in_another_class_is_not_the_same_category(monkeypatch):
    client, _ = _client(monkeypatch, [_cat("השקעות", "expense")])
    names = {(c["name"], c["type"]) for c in client.get("/api/categories/defaults").get_json()["missing"]}
    assert ("השקעות", "savings") in names


def test_global_seed_categories_do_not_count_as_the_familys(monkeypatch):
    client, _ = _client(monkeypatch, [_cat("בריאות", family=None)])
    names = {c["name"] for c in client.get("/api/categories/defaults").get_json()["missing"]}
    assert "בריאות" in names


def test_a_failed_insert_says_so(monkeypatch):
    client, _ = _client(monkeypatch, [])
    monkeypatch.setattr(app_module.db, "bulk_add_categories", lambda *a, **k: (0, "boom"))
    res = client.post("/api/categories/defaults")
    assert res.status_code == 500 and "נכשלה" in res.get_json()["error"]


def test_bulk_add_continues_the_order_after_the_existing():
    class _Insert:
        def __init__(self): self.rows = None
        def table(self, name): return self
        def insert(self, rows): self.rows = rows; return self
        def execute(self):
            class R: data = self.rows
            return R()
    fake = _Insert()
    import pytest as _p
    with _p.MonkeyPatch.context() as m:
        m.setattr(db, "get_client", lambda: fake)
        n, err = db.bulk_add_categories(_FAM, [{"name": "א", "type": "expense"},
                                               {"name": "ב", "type": "expense"},
                                               {"name": "ג", "type": "income"}],
                                        start={"expense": 9})
    assert (n, err) == (3, None)
    assert [r["sort_order"] for r in fake.rows] == [10, 11, 1]


def test_the_button_lives_in_the_categories_manage_mode():
    html = (_ROOT / "frontend/templates/settings.html").read_text(encoding="utf-8")
    area = html[html.index('id="categoriesArea"'):]
    area = area[:area.index("</section>")]
    assert 'id="restoreDefaultCatsBtn"' in area
    css = (_ROOT / "frontend/static/css/style.css").read_text(encoding="utf-8")
    assert "#categoriesArea:not(.managing) #restoreDefaultsRow" in css
