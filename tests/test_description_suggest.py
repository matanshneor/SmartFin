"""Autocomplete from earlier descriptions in the "short description" field
(Matan, 3.10, idea 41)."""
import datetime
import json
import subprocess
from pathlib import Path

import pytest

from backend import supabase_config as db
from tests._fake_db import FakeSupabase

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parent.parent
_FAM, _ME = "fam", "me"
_TODAY = datetime.date(2026, 10, 3)


def _tx(desc, date="2026-09-20", owner=None):
    return {"id": f"{desc}-{date}-{owner}", "family_id": _FAM, "description": desc, "date": date,
            "amount": 10, "type": "expense", "projects": {"owner_id": owner} if owner else None}


def _descs(rows, monkeypatch):
    monkeypatch.setattr(db, "get_client", lambda: FakeSupabase(transactions=rows))
    return db.recent_descriptions(_FAM, _ME, today=_TODAY)


def test_most_used_first_and_spelled_as_written(monkeypatch):
    rows = [_tx("שופרסל"), _tx("רמי לוי"), _tx("רמי  לוי "), _tx("רמי לוי"), _tx("שופרסל"), _tx("פז")]
    assert _descs(rows, monkeypatch) == ["רמי לוי", "שופרסל", "פז"]


def test_only_the_last_six_months(monkeypatch):
    assert _descs([_tx("ישן", date="2026-03-01"), _tx("חדש")], monkeypatch) == ["חדש"]


def test_someone_elses_personal_project_stays_private(monkeypatch):
    rows = [_tx("מתנה לאור", owner="other"), _tx("קבלן", owner=_ME), _tx("סופר")]
    assert sorted(_descs(rows, monkeypatch)) == sorted(["קבלן", "סופר"])


def test_empty_and_one_letter_descriptions_are_skipped(monkeypatch):
    assert _descs([_tx(""), _tx("א"), _tx("ok")], monkeypatch) == ["ok"]


def _matches(q, past):
    js = (_ROOT / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
    fn = js[js.index("function descMatches(q) {"):]
    fn = fn[:fn.index("\n    }") + 6]
    code = "let pastDescriptions = " + json.dumps(past) + ";\n" + fn + \
           "\nconsole.log(JSON.stringify(descMatches(" + json.dumps(q) + ")));"
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def test_matching_is_by_the_start_of_any_word_and_at_most_three():
    past = ["רמי לוי", "רמי לוי שיווק השקמה", "שופרסל", "לוי ביטוח", "רמת גן חניה"]
    assert _matches("רמ", past) == ["רמי לוי", "רמי לוי שיווק השקמה", "רמת גן חניה"]
    assert _matches("לוי", past) == ["רמי לוי", "רמי לוי שיווק השקמה", "לוי ביטוח"]
    assert _matches("ר", past) == [], "one letter: too early"
    assert _matches("שופרסל", past) == [], "already typed in full"


def test_a_suggestion_fills_only_the_description():
    js = (_ROOT / "frontend/static/js/transactions.js").read_text(encoding="utf-8")
    click = js[js.index("descSuggest.addEventListener('click'"):]
    click = click[:click.index("});") + 3]
    assert "txDescription.value = b.textContent;" in click
    assert "category" not in click.lower(), "the category isn't chosen from the suggestion"
