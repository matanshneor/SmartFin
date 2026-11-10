""""הוזנה ע״י אור · אתמול 18:32" at the bottom of the edit sheet (Matan, 3.10,
idea 22)."""
import datetime

import pytest

from backend import app as app_module
from backend.app import _entered_text, app, limiter
from backend.clock import ISRAEL

pytestmark = pytest.mark.unit

_ME, _OR = "me-id", "or-id"
_NAMES = {_ME: "מתן", _OR: "אור"}
_NOW = datetime.datetime(2026, 10, 3, 21, 0, tzinfo=ISRAEL)


def _meta(at, by=_OR, parent=None):
    return {"created_at": at, "created_by": by, "recurring_parent_id": parent}


@pytest.mark.parametrize("meta,expected", [
    # The time is stored in UTC and shown in Israel time (`+03:00` in October)
    (_meta("2026-10-03T06:15:00+00:00"), "הוזנה ע״י אור · היום 09:15"),
    (_meta("2026-10-02T15:32:00+00:00"), "הוזנה ע״י אור · אתמול 18:32"),
    (_meta("2026-09-12T08:00:00+00:00"), "הוזנה ע״י אור · 12.9.2026 11:00"),
    (_meta("2026-10-03T06:15:00+00:00", by=_ME), "הוזנה על ידך · היום 09:15"),
    (_meta("2026-10-03T06:15:00+00:00", by="gone"), "הוזנה ע״י בן משפחה לשעבר · היום 09:15"),
    (_meta("2026-09-12T08:00:00+00:00", by=None), "הוזנה ב-12.9.2026 11:00"),
    (_meta("2026-10-01T03:00:00+00:00", parent="tpl"), "נוצרה אוטומטית מעסקה קבועה"),
], ids=["today", "yesterday", "older", "me", "former-member", "unknown-creator", "auto"])
def test_the_line_says_who_and_when(meta, expected):
    assert _entered_text(meta, _ME, _NAMES, now=_NOW) == expected


def test_after_midnight_israel_time_is_today_not_yesterday():
    """23:30 UTC on 2.10 is 02:30 on 3.10 in Israel: "today", not "yesterday"."""
    assert _entered_text(_meta("2026-10-02T23:30:00+00:00"), _ME, _NAMES, now=_NOW) == \
        "הוזנה ע״י אור · היום 02:30"


def test_the_route(monkeypatch):
    limiter.reset()
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "transaction_meta",
                        lambda tx, fid: _meta("2026-10-03T06:15:00+00:00") if tx == "t1" else None)
    monkeypatch.setattr(app_module, "_member_names", lambda fid: _NAMES)
    monkeypatch.setattr(app_module.db, "personal_project_owner", lambda *a: None, raising=False)
    monkeypatch.setattr(app_module.clock, "now", lambda: _NOW)
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["user_id"] = _ME
        sess["family_id"] = "fam"
    assert c.get("/api/transactions/t1/meta").get_json() == {"text": "הוזנה ע״י אור · היום 09:15"}
    assert c.get("/api/transactions/missing/meta").status_code == 404


def test_the_edit_window_loads_it_and_a_new_form_hides_it():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    js = (root / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
    assert "loadEnteredMeta(tx.id);" in js
    assert "if (mySeq !== formSeq || !d || !d.text) return;" in js, "a late response isn't written onto another form"
    assert js.count("if (enteredMeta) enteredMeta.hidden = true;") == 2, "add and duplicate hide it"
    assert 'id="txEnteredMeta"' in (root / "frontend/templates/base.html").read_text(encoding="utf-8")
