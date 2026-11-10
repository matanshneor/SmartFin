"""
Deleting a recurring transaction: the calendar question.

Matan phrased the model: "every recurring transaction I delete should ask me
whether to delete all occurrences from here on or only the current occurrence.
There shouldn't be a difference between an original recurring transaction and an
occurrence of it."

He is right. The distinction between "template" and occurrence is entirely
internal (the template row is a normal transaction that happens to also define
the series) and whoever deletes March's rent shouldn't care whether March happens
to be the month the series started.

The previous version asked "stop or delete everything", and only on the template
row. In Matan's case all five templates are from July, so from the September
dashboard the question couldn't be reached at all: he tried, nothing happened,
and he reported it doesn't work.

"רק את זו" requires the engine to remember the skip: it decides what is missing by
what exists, so a deleted occurrence looks to it like a month not created yet, and
it fills it in the next day.
"""
import pytest

from backend import app as app_module
from backend.app import app

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_TX  = "22222222-2222-2222-2222-222222222222"


_CAT = "55555555-5555-5555-5555-555555555555"


@pytest.fixture
def client(monkeypatch):
    # The clock is fixed: the dates here are written by hand (September 2026), and
    # on the real clock a recurring series from 15.9 "backfills" more and more as
    # time passes, and the tests broke by themselves (found in the review of
    # 1.10: one in November, seven in January).
    import datetime as _dt
    from backend import clock as _clock
    monkeypatch.setattr(_clock, "today", lambda: _dt.date(2026, 9, 30))
    monkeypatch.setattr(_clock, "now", lambda: _dt.datetime(2026, 9, 30, 12, 0))
    # the transaction isn't in someone else's personal project (see tx_visible_required)
    monkeypatch.setattr(app_module.db, "personal_project_owner", lambda *a: None)
    # editing checks the current attributions: here a normal family transaction
    monkeypatch.setattr(app_module.db, "transaction_links",
                        lambda *a: {"user_id": None, "project_id": None})
    # the backfill warning on edit checks what the series already covers; here
    # there is no series
    monkeypatch.setattr(app_module.db, "existing_occurrence_dates", lambda *a: None)
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "get_transaction_receipt_path", lambda *a: None)
    # the edit route validates category and owner before it writes
    monkeypatch.setattr(app_module.db, "get_categories",
                        lambda *a, **k: [{"id": _CAT, "name": "מכולת", "type": "expense"}])
    monkeypatch.setattr(app_module, "family_settings",
                        lambda: dict(app_module.db.DEFAULT_FAMILY_SETTINGS))
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"]   = "33333333-3333-3333-3333-333333333333"
            sess["family_id"] = _FAM
        yield c


def _occurrence(monkeypatch, info):
    monkeypatch.setattr(app_module.db, "recurring_occurrence", lambda *a: (info, True))


_SERIES = {"template_id": "tpl-1", "date": "2026-09-01", "later": 4}


# ─── No difference between "original" and occurrence ────────────────────────

def test_any_recurring_transaction_asks_the_same_question(client, monkeypatch):
    """The heart of what Matan asked. Before, only the template row asked, and all the
    occurrences were deleted silently."""
    _occurrence(monkeypatch, _SERIES)
    monkeypatch.setattr(app_module.db, "delete_transaction",
                        lambda *a: pytest.fail("deleted without asking"))

    res = client.delete(f"/api/transactions/{_TX}")

    assert res.status_code == 409
    assert res.get_json() == {"needs_choice": True, "later": 4}


def test_the_question_carries_the_real_number(client, monkeypatch):
    _occurrence(monkeypatch, dict(_SERIES, later=23))

    assert client.delete(f"/api/transactions/{_TX}").get_json()["later"] == 23


# ─── "רק את זו" ─────────────────────────────────────────────────────────────

def test_deleting_one_occurrence_records_the_skip(client, monkeypatch):
    """Without the record the engine fills this month in again the next day, and the
    delete undoes itself."""
    called = []
    _occurrence(monkeypatch, _SERIES)
    monkeypatch.setattr(app_module.db, "delete_one_occurrence",
                        lambda tx, tpl, d, fam: (called.append((tx, tpl, d)) or (True, None)))

    res = client.delete(f"/api/transactions/{_TX}?mode=one")

    assert res.get_json() == {"status": "ok", "deleted": 1}
    assert called == [(_TX, "tpl-1", "2026-09-01")]


def test_deleting_one_occurrence_does_not_touch_the_rest(client, monkeypatch):
    _occurrence(monkeypatch, _SERIES)
    monkeypatch.setattr(app_module.db, "delete_one_occurrence", lambda *a: (True, None))
    monkeypatch.setattr(app_module.db, "delete_occurrences_from",
                        lambda *a: pytest.fail("the whole series was deleted"))

    assert client.delete(f"/api/transactions/{_TX}?mode=one").status_code == 200


# ─── "את זו וכל הבאות" ──────────────────────────────────────────────────────

def test_deleting_from_here_onward_reports_how_many_went(client, monkeypatch):
    _occurrence(monkeypatch, _SERIES)
    monkeypatch.setattr(app_module.db, "delete_occurrences_from",
                        lambda tpl, d, fam: (4, None))

    res = client.delete(f"/api/transactions/{_TX}?mode=later")

    assert res.get_json() == {"status": "ok", "deleted": 4}


# ─── A normal transaction didn't change ─────────────────────────────────────

def test_an_ordinary_transaction_is_untouched(client, monkeypatch):
    """Control, and the important one here: most deletes are of a normal transaction,
    and they must keep working exactly as before, including "בטל"."""
    _occurrence(monkeypatch, None)
    deleted = []
    monkeypatch.setattr(app_module.db, "delete_transaction",
                        lambda tx, fam: (deleted.append(tx) or (True, None)))

    res = client.delete(f"/api/transactions/{_TX}")

    assert res.status_code == 200
    assert res.get_json() == {"status": "ok"}
    assert deleted == [_TX]


def test_an_unknown_series_state_refuses_rather_than_guessing(client, monkeypatch):
    def _boom(*a):
        raise app_module.db.DataUnavailable("recurring_occurrence")
    monkeypatch.setattr(app_module.db, "recurring_occurrence", _boom)
    monkeypatch.setattr(app_module.db, "delete_transaction",
                        lambda *a: pytest.fail("deleted without knowing whether there is a series"))

    assert client.delete(f"/api/transactions/{_TX}").status_code == 503


# ─── An occurrence can't become a second template ───────────────────────────

def _edit(client, is_recurring):
    return client.put(f"/api/transactions/{_TX}", json={
        "amount": 100, "type": "expense", "date": "2026-09-10", "category_id": _CAT,
        "is_recurring": is_recurring, "recurring_frequency": "monthly_1",
    })


def test_marking_an_instance_as_recurring_is_refused(client, monkeypatch):
    """"Make it recurring from now" on an existing occurrence created a second series
    running in parallel to the first: the same money twice a month, forever."""
    monkeypatch.setattr(app_module.db, "is_recurring_instance", lambda *a: True)
    monkeypatch.setattr(app_module.db, "update_transaction",
                        lambda *a, **k: pytest.fail("a second series was created"))

    res = _edit(client, True)

    assert res.status_code == 422
    assert "זו עסקה קבועה" in res.get_json()["error"]


def test_an_instance_can_still_be_edited_normally(client, monkeypatch):
    """Control: the block is on turning it into a template, not on editing it."""
    monkeypatch.setattr(app_module.db, "is_recurring_instance",
                        lambda *a: pytest.fail("a series was checked on a normal edit"))
    monkeypatch.setattr(app_module.db, "update_transaction",
                        lambda *a, **k: ({"id": _TX}, None))

    assert _edit(client, False).status_code == 200


def test_an_ordinary_transaction_can_still_become_recurring(client, monkeypatch):
    """Control: this is the feature itself: it mustn't be blocked."""
    monkeypatch.setattr(app_module.db, "is_recurring_instance", lambda *a: False)
    monkeypatch.setattr(app_module.db, "update_transaction",
                        lambda *a, **k: ({"id": _TX}, None))
    monkeypatch.setattr(app_module.db, "materialize_recurring", lambda fam: (0, True))

    assert _edit(client, True).status_code == 200


def test_an_unknown_instance_state_refuses(client, monkeypatch):
    def _boom(*a):
        raise app_module.db.DataUnavailable("is_recurring_instance")
    monkeypatch.setattr(app_module.db, "is_recurring_instance", _boom)
    monkeypatch.setattr(app_module.db, "update_transaction",
                        lambda *a, **k: pytest.fail("written without knowing"))

    assert _edit(client, True).status_code == 503


# ─── What the user sees ─────────────────────────────────────────────────────

def _js():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    return (root / "frontend/static/js/transactions.js").read_text(encoding="utf-8")


def _dialog():
    js = _js()
    block = js[js.index("function askAboutSeries("):]
    return block[:block.index("function sendSeriesDelete(")]


def test_the_dialog_asks_the_calendar_question():
    block = _dialog()

    assert "רק את זו" in block
    assert "את זו וכל הבאות" in block
    assert "'זו עסקה קבועה'" in block


def test_the_safe_option_is_the_confirm_button():
    """Deleting one occurrence is the expected action; deleting all the rest goes
    through a second confirmation that shows the number."""
    block = _dialog()

    assert "confirmText: 'רק את זו'" in block
    assert "למחוק את זו וכל הבאות?" in block


def test_backing_out_of_either_dialog_deletes_nothing():
    """Backing out returns null. A second dialog interpreting null as confirmation is
    exactly the bug that was already in project deletion."""
    block = _dialog()

    assert "if (onlyThis === null) return;" in block
    assert "if (sure !== true) return;" in block


def test_no_undo_is_offered_for_either():
    """Restoring a single occurrence would also bring back the skip recorded on it,
    and of a whole series: dozens of rows."""
    js = _js()
    block = js[js.index("function sendSeriesDelete("):]
    block = block[:block.index("function deleteWithUndo(")]

    assert "label: 'ביטול'" not in block
    assert "שאר החודשים של העסקה הקבועה נשארו" in block


# ─── Two questions instead of one ───────────────────────────────────────────

def test_a_recurring_transaction_is_not_asked_the_generic_question_first():
    """Matan saw "למחוק את העסקה? / הפעולה תסיר את העסקה מכל הדוחות והגרפים": a
    sentence that is true for a single transaction and completely misleading when
    a series is behind it.

    The real question comes after it, from the server, but whoever sees a worded,
    absolute question assumes it is the only question, and taps cancel."""
    js = _js()
    block = js[js.index("function confirmDelete("):]
    block = block[:block.index("function deleteWithUndo(")]

    assert "txData.isRecurring || txData.recurringParentId" in block
    assert "Promise.resolve(true)" in block


def test_every_delete_path_goes_through_the_same_gate():
    """The delete paths: the modal and the swipe (the inline editor was removed on
    5.10). Each asked the generic question separately, and each could be left
    behind."""
    js = _js()

    assert js.count("title: 'למחוק את העסקה?'") == 1, "a local generic question remained"
    assert js.count("confirmDelete(") == 3, "not all delete paths go through the gate"


def test_an_ordinary_transaction_still_gets_asked():
    """Control: most deletes are of a normal transaction, and there the question is
    needed."""
    js = _js()
    block = js[js.index("function confirmDelete("):]
    block = block[:block.index("function deleteWithUndo(")]

    assert "הפעולה תסיר את העסקה מכל הדוחות והגרפים" in block
    assert "confirmText: 'מחיקת העסקה'" in block
