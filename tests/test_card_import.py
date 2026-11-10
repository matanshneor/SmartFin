"""Importing from the card company (Matan, 31.10).

The file the user uploads is the only thing in the app whose structure isn't
under our control: every company, and every version of its site, produces a
slightly different table. The tests here build files in the shapes the companies
really produce (Hebrew headers, preamble rows, a negative amount for a credit,
"תשלום 3 מתוך 12", HTML pretending to be `.xls`) and check what is read, what is
marked as a duplicate, and what finally goes into the database.
"""
import io
import json
from datetime import date, datetime

import openpyxl
import pytest

from backend import app as app_module
from backend import card_import as ci
from backend import supabase_config as db
from backend.app import app, limiter

pytestmark = pytest.mark.unit

_FAM = "11111111-1111-1111-1111-111111111111"
_ME = "33333333-3333-3333-3333-333333333333"
_FOOD, _CAR, _HOME = "c-food", "c-car", "c-home"
_CATS = [
    {"id": _FOOD, "name": "סופר ומזון", "icon": "🛒", "type": "expense"},
    {"id": _CAR, "name": "רכב ותחבורה", "icon": "🚗", "type": "expense"},
    {"id": _HOME, "name": "ריהוט ואביזרים לבית", "icon": "🪑", "type": "expense"},
    {"id": "c-salary", "name": "משכורת", "icon": "💼", "type": "income"},
]

_MAX_HEADERS = ["תאריך עסקה", "שם בית העסק", "קטגוריה", "4 ספרות אחרונות של כרטיס האשראי",
                "סוג עסקה", "סכום חיוב", "מטבע חיוב", "סכום עסקה מקורי", "מטבע עסקה מקורי",
                "תאריך חיוב", "הערות"]


def _xlsx(rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _max_file() -> bytes:
    return _xlsx([
        ["כל המשתמשים (1)"],
        ["4 עסקאות"],
        [],
        _MAX_HEADERS,
        [datetime(2026, 10, 3), "שופרסל דיל 123", "מזון", "1234", "רגילה", 412.3, "₪", 412.3, "₪", "10-11-2026", None],
        ["05-10-2026", "פז חולון", "דלק", "1234", "רגילה", 250, "₪", 250, "₪", "10-11-2026", None],
        ["12-08-2026", "ACE", "בית", "1234", "תשלומים", 400, "₪", 1200, "₪", "10-11-2026", "תשלום 3 מתוך 3"],
        ["15-10-2026", "NETFLIX.COM", "", "1234", "רגילה", "-54.90", "₪", "54.90", "₪", "10-11-2026", "זיכוי"],
        [None, 'סה"כ', None, None, None, 1007.4],
    ])


# ─── Reading ────────────────────────────────────────────────────────────────

def test_a_max_style_workbook_is_read_without_knowing_the_company():
    txs, used = ci.extract(ci.read_sheets(_max_file(), "x.xlsx"))

    assert [(t["date"], t["merchant"], t["amount"]) for t in txs] == [
        ("2026-10-03", "שופרסל דיל 123", 412.3),
        ("2026-10-05", "פז חולון", 250.0),
        ("2026-10-12", "ACE", 400.0),
        ("2026-10-15", "NETFLIX.COM", 54.9),
    ]
    # The charge amount (400) and not the full transaction amount (1200)
    assert used[0]["mapping"]["charge"] == 5
    assert used[0]["mapping"]["date"] == 0      # "תאריך עסקה", not "תאריך חיוב"


def test_the_total_row_is_not_a_transaction():
    txs, _ = ci.extract(ci.read_sheets(_max_file(), "x.xlsx"))
    assert all("סה" not in t["merchant"] for t in txs)


def test_an_installment_lands_in_the_month_it_is_charged():
    """With installments, "תאריך עסקה" is the purchase day on every one of them.
    Installment 3 is charged two months later; otherwise all the installments
    would land in August."""
    txs, _ = ci.extract(ci.read_sheets(_max_file(), "x.xlsx"))
    ace = next(t for t in txs if t["merchant"] == "ACE")
    assert ace["date"] == "2026-10-12"
    assert ace["installment"] == [3, 3]
    assert ace["description"] == "ACE (תשלום 3 מתוך 3)"


def test_a_negative_charge_is_a_refund():
    txs, _ = ci.extract(ci.read_sheets(_max_file(), "x.xlsx"))
    netflix = next(t for t in txs if t["merchant"] == "NETFLIX.COM")
    assert netflix["refund"] is True and netflix["amount"] == 54.9


def test_html_disguised_as_xls_in_windows_hebrew_is_read():
    html = ('<html><body><table><tr><th>תאריך רכישה</th><th>שם בית עסק</th><th>סכום עסקה</th>'
            '<th>סכום חיוב</th></tr><tr><td>01/10/26</td><td>רמי לוי</td><td>₪ 1,234.50</td>'
            '<td>1,234.50 ש"ח</td></tr></table></body></html>')
    txs, _ = ci.extract(ci.read_sheets(html.encode("cp1255"), "export.xls"))
    assert [(t["date"], t["merchant"], t["amount"]) for t in txs] == [("2026-10-01", "רמי לוי", 1234.5)]


def test_csv_with_semicolons_and_english_headers():
    text = "Date;Description;Amount\n2026-10-01;Wolt;55.5\n02/10/2026;Aroma;(18.00)\n"
    txs, _ = ci.extract(ci.read_sheets(text.encode(), "a.csv"))
    assert [(t["date"], t["merchant"], t["amount"], t["refund"]) for t in txs] == [
        ("2026-10-01", "Wolt", 55.5, False), ("2026-10-02", "Aroma", 18.0, True)]


def test_a_foreign_purchase_without_a_charge_yet_falls_back_to_the_amount():
    rows = [["תאריך עסקה", "שם בית העסק", "סכום חיוב", "סכום עסקה"],
            ["01/10/2026", "AMAZON", None, 99.0]]
    txs, _ = ci.extract([rows])
    assert txs[0]["amount"] == 99.0


@pytest.mark.parametrize("blob,message", [
    (b"", "ריק"),
    (b"%PDF-1.7 ...", "PDF"),
    (b"x" * (ci.MAX_FILE_BYTES + 1), "גדול"),
])
def test_unreadable_files_say_why_in_hebrew(blob, message):
    with pytest.raises(ci.ImportError_) as e:
        ci.read_sheets(blob, "f.pdf")
    assert message in str(e.value)


def test_a_broken_xlsx_is_a_hebrew_error_not_a_crash():
    with pytest.raises(ci.ImportError_):
        ci.read_sheets(b"PK\x03\x04garbage", "f.xlsx")


@pytest.mark.parametrize("raw,expected", [
    ("03/10/2026", date(2026, 10, 3)),     # day/month: the Israeli order
    ("3.10.26", date(2026, 10, 3)),
    ("2026-10-03", date(2026, 10, 3)),
    (datetime(2026, 10, 3, 14, 5), date(2026, 10, 3)),
    (46298, date(2026, 10, 3)),             # an Excel serial number
    ("31/02/2026", None),
    ("שופרסל", None),
    (12.5, None),
])
def test_dates(raw, expected):
    assert ci.parse_date(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("₪ 1,234.50", 1234.5), ("54.90-", -54.9), ("(18.00)", -18.0), ("-7", -7.0),
    ('99 ש"ח', 99.0), (12, 12.0), ("תשלום 3 מתוך 12", None), ("03/10/2026", None), ("", None),
])
def test_amounts(raw, expected):
    assert ci.parse_amount(raw) == expected


# ─── An unknown file ────────────────────────────────────────────────────────

_WEIRD = [["דוח לקוח"], ["יום", "עסק", "כמה"], ["01/10/2026", "קפה", "12"], ["02/10/2026", "דלק", "200"]]


def test_an_unknown_layout_offers_the_header_row_and_samples():
    assert ci.extract([_WEIRD]) == ([], [])
    cand = ci.find_header_candidate([_WEIRD])
    assert cand["headers"] == ["יום", "עסק", "כמה"]
    assert cand["samples"][0] == ["01/10/2026", "קפה", "12"]


def test_a_confirmed_layout_reads_the_same_file_next_time():
    cand = ci.find_header_candidate([_WEIRD])
    layout = {"signature": cand["signature"], "mapping": {"date": 0, "merchant": 1, "amount": 2}}
    txs, used = ci.extract([_WEIRD], [layout])
    assert [(t["merchant"], t["amount"]) for t in txs] == [("קפה", 12.0), ("דלק", 200.0)]
    assert used == [layout]


@pytest.mark.parametrize("mapping", [
    {"date": 0, "merchant": 0, "amount": 2},       # the same column twice
    {"date": 0, "merchant": 1},                    # no amount
    {"date": 0, "merchant": 1, "amount": 9},       # outside the table
    {"date": "0", "merchant": 1, "amount": 2},     # not a number
    {"date": True, "merchant": 1, "amount": 2},
    "date=0",
])
def test_a_layout_from_outside_is_validated(mapping):
    assert ci.valid_mapping(mapping, 3) is None


# ─── Categories ─────────────────────────────────────────────────────────────

def test_the_familys_own_choice_for_a_merchant_wins():
    history = [{"description": "פז חולון", "category_id": _HOME, "date": "2026-09-01"},
               {"description": "פז חולון", "category_id": _CAR, "date": "2026-08-01"}]
    learned = ci.learned_categories(history)
    # A different branch number: the same business; the last one chosen wins
    assert ci.lookup_learned(ci.merchant_key("פז חולון 4531"), learned) == _HOME


def test_a_short_manual_name_teaches_the_long_one():
    learned = ci.learned_categories([{"description": "שופרסל", "category_id": _FOOD}])
    assert ci.lookup_learned(ci.merchant_key("שופרסל דיל"), learned) == _FOOD
    assert ci.lookup_learned(ci.merchant_key("שופרסלון"), learned) is None


@pytest.mark.parametrize("merchant,expected", [
    ("שופרסל דיל 123", _FOOD),
    ("פז חולון", _CAR),
    ("ACE", _HOME),
    ("עבר הירדן", None),        # "בר" as a whole word only
    ("חפז", None),
])
def test_keyword_rules_use_the_familys_category_names(merchant, expected):
    assert ci.rule_category(merchant, _CATS) == expected


def test_the_longest_keyword_wins():
    cats = _CATS + [{"id": "c-health", "name": "בריאות", "type": "expense"}]
    assert ci.rule_category("סופר פארם רמת גן", cats) == "c-health"


# ─── Duplicates ─────────────────────────────────────────────────────────────

def _item(d, merchant, amount):
    return {"date": d, "merchant": merchant, "amount": amount}


def test_an_expense_entered_by_hand_is_marked_as_existing():
    items = [_item("2026-10-07", "שופרסל דיל", 87.5)]
    ci.mark_duplicates(items, [{"date": "2026-10-06", "amount": "87.50", "description": "שופרסל"}])
    assert items[0]["status"] == "exists"
    assert items[0]["match"] == {"date": "2026-10-06", "description": "שופרסל"}


def test_same_amount_different_name_is_only_a_maybe():
    items = [_item("2026-10-07", "שופרסל דיל", 87.5)]
    ci.mark_duplicates(items, [{"date": "2026-10-07", "amount": 87.5, "description": "קניות לשבת"}])
    assert items[0]["status"] == "maybe"


def test_too_far_apart_is_new():
    items = [_item("2026-10-07", "שופרסל", 87.5)]
    ci.mark_duplicates(items, [{"date": "2026-10-01", "amount": 87.5, "description": "שופרסל"}])
    assert items[0]["status"] == "new"


def test_two_identical_coffees_and_one_entered_by_hand():
    """Each existing row is "consumed" once: the second coffee is still new."""
    items = [_item("2026-10-07", "ארומה", 18), _item("2026-10-07", "ארומה", 18)]
    ci.mark_duplicates(items, [{"date": "2026-10-07", "amount": 18, "description": "ארומה"}])
    assert sorted(i["status"] for i in items) == ["exists", "new"]


def test_importing_the_same_file_twice_adds_nothing():
    txs, _ = ci.extract(ci.read_sheets(_max_file(), "x.xlsx"))
    saved = [{"date": t["date"], "amount": t["amount"], "description": t["description"]}
             for t in txs if not t["refund"]]
    again = [t for t in txs if not t["refund"]]
    ci.mark_duplicates(again, saved)
    assert {t["status"] for t in again} == {"exists"}


# ─── The routes ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client(monkeypatch):
    app.config["TESTING"] = True
    monkeypatch.setattr(app_module.db, "get_categories", lambda *a, **k: _CATS)
    monkeypatch.setattr(app_module, "family_settings", lambda: dict(db.DEFAULT_FAMILY_SETTINGS))
    monkeypatch.setattr(app_module.db, "import_existing_expenses", lambda *a, **k: [])
    monkeypatch.setattr(app_module.db, "import_category_history", lambda *a, **k: [])
    monkeypatch.setattr(app_module.db, "get_recurring_transactions", lambda *a, **k: [])
    monkeypatch.setattr(app_module.db, "get_family_members", lambda *a: [{"id": _ME, "name": "מתן"}])
    # No OpenAI in tests: every such call is real money
    monkeypatch.setattr(ci, "ai_map_columns", lambda *a: None)
    monkeypatch.setattr(ci, "ai_categorize", lambda *a: {})
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = _ME
            sess["family_id"] = _FAM
        yield c


def _preview(client, blob, name="x.xlsx", **form):
    data = {"file": (io.BytesIO(blob), name)}
    data.update({k: json.dumps(v) for k, v in form.items()})
    return client.post("/api/import/preview", content_type="multipart/form-data", data=data)


def test_preview_suggests_categories_and_writes_nothing(client, monkeypatch):
    def _no_writes(*a, **k):
        raise AssertionError("preview כתב למסד")
    monkeypatch.setattr(app_module.db, "add_transactions", _no_writes)
    monkeypatch.setattr(app_module.db, "add_transaction", _no_writes)

    res = _preview(client, _max_file())
    body = res.get_json()

    assert res.status_code == 200 and body["status"] == "ok"
    by = {t["merchant"]: t for t in body["transactions"]}
    assert by["שופרסל דיל 123"]["category_id"] == _FOOD
    assert by["פז חולון"]["category_id"] == _CAR
    assert by["NETFLIX.COM"]["status"] == "refund"
    assert body["counts"] == {"new": 3, "exists": 0, "maybe": 0, "refund": 1}


def test_preview_marks_what_is_already_in_the_month(client, monkeypatch):
    seen = {}

    def _existing(family_id, viewer, start, end):
        seen.update(family_id=family_id, start=start, end=end)
        return [{"date": "2026-10-04", "amount": 412.3, "description": "שופרסל"}]
    monkeypatch.setattr(app_module.db, "import_existing_expenses", _existing)

    body = _preview(client, _max_file()).get_json()

    shufersal = next(t for t in body["transactions"] if t["merchant"].startswith("שופרסל"))
    assert shufersal["status"] == "exists"
    # The range is padded with the duplicate days on both sides
    assert seen == {"family_id": _FAM, "start": "2026-09-30", "end": "2026-10-18"}


def test_preview_refuses_when_duplicates_cannot_be_checked(client, monkeypatch):
    def _down(*a, **k):
        raise db.DataUnavailable("x")
    monkeypatch.setattr(app_module.db, "import_existing_expenses", _down)
    assert _preview(client, _max_file()).status_code == 503


def test_history_beats_the_keyword_rules(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "import_category_history",
                        lambda *a, **k: [{"description": "פז חולון", "category_id": _HOME}])
    body = _preview(client, _max_file()).get_json()
    paz = next(t for t in body["transactions"] if t["merchant"] == "פז חולון")
    assert (paz["category_id"], paz["category_source"]) == (_HOME, "history")


def test_ai_only_names_merchants_and_only_family_categories_count(client, monkeypatch):
    asked = {}

    def _ai(merchants, names):
        asked.update(merchants=merchants, names=names)
        return {"קפה גרג": "סופר ומזון", "חנות": "קטגוריה שלא קיימת"}
    monkeypatch.setattr(ci, "ai_categorize", _ai)
    blob = _xlsx([["תאריך עסקה", "שם בית העסק", "סכום חיוב"],
                  ["01/10/2026", "קפה גרג", 20], ["01/10/2026", "חנות", 30]])

    body = _preview(client, blob).get_json()

    assert asked["merchants"] == ["חנות", "קפה גרג"]
    assert "משכורת" not in asked["names"]          # expense categories only
    by = {t["merchant"]: t for t in body["transactions"]}
    assert by["קפה גרג"]["category_id"] == _FOOD
    assert by["חנות"]["category_id"] is None


def test_an_unknown_file_asks_which_column_is_which(client):
    blob = _xlsx(_WEIRD)
    body = _preview(client, blob).get_json()
    assert body["status"] == "needs_mapping"
    assert body["headers"] == ["יום", "עסק", "כמה"]

    sig = body["signature"]
    again = _preview(client, blob, mapping={"signature": sig,
                                            "mapping": {"date": 0, "merchant": 1, "amount": 2}})
    assert [t["merchant"] for t in again.get_json()["transactions"]] == ["קפה", "דלק"]


def test_an_unknown_file_is_mapped_by_ai_when_available(client, monkeypatch):
    monkeypatch.setattr(ci, "ai_map_columns", lambda h, s: {"date": 0, "merchant": 1, "amount": 2})
    body = _preview(client, _xlsx(_WEIRD)).get_json()
    assert body["status"] == "ok" and body["ai_mapped"] is True
    # The layout goes back to the browser, so next time there is no need for AI
    assert body["layouts"][0]["mapping"] == {"date": 0, "merchant": 1, "amount": 2}


def test_a_file_without_any_table_is_a_hebrew_error(client):
    res = _preview(client, b"just some text\nnothing here\n", name="a.csv")
    assert res.status_code == 422 and "טבלת עסקאות" in res.get_json()["error"]


def _commit(client, rows, **extra):
    return client.post("/api/import/commit", json=dict(extra, transactions=rows))


def test_commit_writes_only_the_approved_rows_in_one_request(client, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module.db, "add_transactions",
                        lambda rows: (calls.append(rows), (len(rows), None))[1])
    res = _commit(client, [
        {"date": "2026-10-03", "amount": 412.3, "description": "שופרסל דיל", "category_id": _FOOD},
        {"date": "2026-10-12", "amount": "400", "description": "ACE (תשלום 3 מתוך 3)", "category_id": _HOME},
    ])

    assert res.status_code == 201 and res.get_json()["created"] == 2
    assert len(calls) == 1
    first = calls[0][0]
    assert first == {"amount": 412.3, "type": "expense", "date": "2026-10-03",
                     "description": "שופרסל דיל", "category_id": _FOOD, "user_id": _ME,
                     "family_id": _FAM, "is_recurring": False,
                     "recurring_frequency": None, "recurring_end_date": None}


@pytest.mark.parametrize("row,message", [
    ({"date": "2026-10-03", "amount": 10, "description": "x", "category_id": "c-salary"}, "קטגוריה"),
    ({"date": "2026-10-03", "amount": 10, "description": "x", "category_id": None}, "קטגוריה"),
    ({"date": "2026-10-03", "amount": -10, "description": "x", "category_id": _FOOD}, "חיובי"),
    ({"date": "1900-01-01", "amount": 10, "description": "x", "category_id": _FOOD}, "בעבר"),
    ("not a row", "אינה תקינה"),
])
def test_commit_validates_every_row_and_writes_nothing_on_error(client, monkeypatch, row, message):
    def _no_writes(*a, **k):
        raise AssertionError("נכתב למרות שגיאה")
    monkeypatch.setattr(app_module.db, "add_transactions", _no_writes)
    good = {"date": "2026-10-03", "amount": 10, "description": "ok", "category_id": _FOOD}
    res = _commit(client, [good, row])
    assert res.status_code == 422
    assert "שורה 2" in res.get_json()["error"] and message in res.get_json()["error"]


def test_commit_rejects_an_owner_outside_the_family(client, monkeypatch):
    settings = dict(db.DEFAULT_FAMILY_SETTINGS, owner_attribution={"expense": True})
    monkeypatch.setattr(app_module, "family_settings", lambda: settings)
    monkeypatch.setattr(app_module.db, "get_family_members", lambda *a: [{"id": _ME}])
    monkeypatch.setattr(app_module.db, "add_transactions", lambda rows: (len(rows), None))
    row = {"date": "2026-10-03", "amount": 10, "description": "x", "category_id": _FOOD}

    assert _commit(client, [row], owner="someone-else").status_code == 422
    assert _commit(client, [row]).status_code == 201


def test_commit_can_record_the_card_as_shared(client, monkeypatch):
    calls = []
    monkeypatch.setattr(app_module.db, "add_transactions",
                        lambda rows: (calls.append(rows), (len(rows), None))[1])
    row = {"date": "2026-10-03", "amount": 10, "description": "x", "category_id": _FOOD}
    assert _commit(client, [row], owner="shared").status_code == 201
    assert calls[0][0]["user_id"] is None


def test_commit_caps_the_batch(client):
    row = {"date": "2026-10-03", "amount": 10, "description": "x", "category_id": _FOOD}
    assert _commit(client, [row] * (ci.MAX_TRANSACTIONS + 1)).status_code == 422
    assert _commit(client, []).status_code == 422


def test_a_failed_insert_says_nothing_was_saved(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "add_transactions", lambda rows: (0, "boom"))
    row = {"date": "2026-10-03", "amount": 10, "description": "x", "category_id": _FOOD}
    res = _commit(client, [row])
    assert res.status_code == 500 and "שום עסקה לא נשמרה" in res.get_json()["error"]


def test_the_import_page_renders_with_expense_categories_only(client):
    res = client.get("/import")
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    data = json.loads(html.split('id="importData">')[1].split("</script>")[0])
    assert [c["id"] for c in data["categories"]] == [_FOOD, _CAR, _HOME]


def test_settings_and_month_link_to_the_import():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "frontend/templates"
    for name in ("settings.html", "month.html"):
        assert "url_for('import_page')" in (root / name).read_text(encoding="utf-8"), name


# ─── PDF: a monthly statement ───────────────────────────────────────────────
#
# Based on the Cal statement Matan uploaded (31.10), with no personal detail: the
# same positions on the page, the same two-line headers, and the same Hebrew
# stored in visual order ("העסק" as "קסעה"). Business names and amounts come
# from the sample; they aren't identifying.

def _w(text, x0, x1, top, visual=True):
    if visual and ci._HEB.search(text):
        text = text[::-1]
    return {"text": text, "x0": x0, "x1": x1, "top": top}


def _cal_page(visual=True):
    w = lambda *a: _w(*a, visual=visual)
    return [
        w("פירוט", 518, 538, 336), w("עסקות", 493, 516, 336), w("שנצברו", 465, 491, 336),
        # two-line headers
        w("תאריך", 518, 538, 347.5), w("שם", 488, 499, 347.5), w("בית", 474, 486, 347.5),
        w("העסק", 453, 472, 347.5), w("ענף", 404, 416, 347.5), w("פירוט", 362, 380, 347.5),
        w("כרטיס", 255, 275, 347.5), w("סכום", 230, 246, 347.5), w("סכום", 166, 181, 347.5),
        w("העסקה", 514, 538, 354), w("הוצג", 259, 275, 354), w("העסקה", 222, 246, 354),
        w("חיוב", 168, 181, 354),
        # transactions
        w("10/08/2026", 503, 538, 364.2), w("קוסטה", 481, 499, 364.2), w("ג'ים", 468, 479, 364.2),
        w("פנאי", 403, 416, 364.2), w("הוראת", 364, 380, 364.2), w("קבע", 352, 362, 364.2),
        w("לא", 267, 275, 364.2), w("255.00", 225, 246, 364.2), w("₪", 218, 223, 364.2),
        w("255.00", 161, 181, 364.2), w("₪", 153, 159, 364.2),
        w("20/08/2026", 503, 538, 383.7), w("כביש", 485, 499, 383.7), w("6", 479, 483, 383.7),
        w("רכב", 405, 416, 383.7), w("לא", 267, 275, 383.7), w("149.39", 225, 246, 383.7),
        w("₪", 218, 223, 383.7), w("149.39", 161, 181, 383.7), w("₪", 153, 159, 383.7),
        w("13/09/2026", 503, 538, 393.4), w("Netflix.com", 465, 499, 393.4),
        w("תקשורת", 393, 416, 393.4), w("לא", 267, 275, 393.4), w("-69.90", 225, 246, 393.4),
        w("₪", 222, 227, 393.4), w("-69.90", 161, 181, 393.4), w("₪", 157, 162, 393.4),
        # the description's continuation line: no date, not a transaction
        w("7610", 400, 416, 402),
        # a summary: a date and an amount, but not in the date column
        w('סה"כ', 254, 272, 414), w("לתאריך", 226, 252, 414), w("15/09/26", 190, 224, 414),
        w("514.19", 156, 181, 414), w("₪", 146, 153, 414),
        # the interest table: a date and percentages, no money amount
        w("סוג", 290, 300, 530), w("שיעור", 234, 256, 530), w("מתאריך", 159, 187, 530),
        w("+30", 290, 300, 549), w("13.75%", 234, 256, 549), w("03/09/26", 159, 187, 549),
    ]


@pytest.mark.parametrize("visual", [True, False])
def test_a_cal_statement_pdf_is_read_like_a_spreadsheet(visual):
    sheets = ci.sheets_from_pdf_words([_cal_page(visual)])
    assert sheets[0][0] == ["תאריך העסקה", "שם בית העסק", "ענף", "פירוט", "כרטיס הוצג",
                            "סכום העסקה", "סכום חיוב"]
    txs, _ = ci.extract(sheets)
    assert [(t["date"], t["merchant"], t["amount"], t["refund"]) for t in txs] == [
        ("2026-08-10", "קוסטה ג'ים", 255.0, False),
        ("2026-08-20", "כביש 6", 149.39, False),
        ("2026-09-13", "Netflix.com", 69.9, True),
    ]


def test_a_pdf_without_text_says_it_may_be_scanned(monkeypatch):
    monkeypatch.setattr(ci, "sheets_from_pdf_words", lambda pages: [])

    class _Pdf:
        pages = []
        def __enter__(self): return self
        def __exit__(self, *a): return False
    import pdfplumber
    monkeypatch.setattr(pdfplumber, "open", lambda *a, **k: _Pdf())
    with pytest.raises(ci.ImportError_) as e:
        ci.read_sheets(b"%PDF-1.7 scanned", "s.pdf")
    assert "סרוק" in str(e.value)


def test_the_preview_route_accepts_a_pdf(client, monkeypatch):
    monkeypatch.setattr(ci, "_read_pdf", lambda data: ci.sheets_from_pdf_words([_cal_page()]))
    body = _preview(client, b"%PDF-1.7 ...", name="statement.pdf").get_json()
    assert body["status"] == "ok"
    assert body["counts"] == {"new": 2, "exists": 0, "maybe": 0, "refund": 1}


def test_the_card_companys_own_sector_is_a_fallback_hint():
    """"קוסטה ג'ים" says nothing to the rules, but Cal already wrote "פנאי בילוי"."""
    cats = _CATS + [{"id": "c-fun", "name": "מסעדות ובילויים", "type": "expense"}]
    txs, _ = ci.extract(ci.sheets_from_pdf_words([_cal_page()]))
    gym = txs[0]
    assert gym["sector"] == "פנאי"
    assert ci.rule_category(gym["merchant"], cats) is None
    assert ci.sector_category(gym["sector"], cats) == "c-fun"
    assert ci.sector_category("משהו אחר", cats) is None
    assert ci.sector_category(None, cats) is None


def test_preview_uses_the_sector_only_after_merchant_rules(client):
    blob = _xlsx([["תאריך עסקה", "שם בית העסק", "קטגוריה", "סכום חיוב"],
                  ["01/10/2026", "חנות לא מוכרת", "רכב ותחבורה", 20],
                  ["01/10/2026", "שופרסל", "רכב ותחבורה", 30]])
    by = {t["merchant"]: t for t in _preview(client, blob).get_json()["transactions"]}
    assert (by["חנות לא מוכרת"]["category_id"], by["חנות לא מוכרת"]["category_source"]) == (_CAR, "sector")
    assert by["שופרסל"]["category_id"] == _FOOD


def test_the_settings_link_is_a_quiet_line_not_a_box():
    """Matan (31.10): a once-a-month action, "not a box of its own in settings". A
    text line above "sign out", with no frame and no arrow."""
    from pathlib import Path
    html = (Path(__file__).resolve().parent.parent / "frontend/templates/settings.html").read_text(encoding="utf-8")
    footer = html[html.index('<div class="settings-footer">'):]
    link = footer[:footer.index('class="logout-form')]
    assert "url_for('import_page')" in link
    groups = html[:html.index('<div class="settings-footer">')]
    assert "import_page" not in groups
    assert "←" not in link and "<svg" not in link



# ─── Standing orders (Matan, 1.11) ──────────────────────────────────────────

def test_standing_orders_are_recognised_and_the_suffix_dropped():
    txs, _ = ci.extract(ci.sheets_from_pdf_words([_cal_page()]))
    rows = [["תאריך עסקה", "שם בית העסק", "סכום חיוב", "הערות"],
            ["01/10/2026", 'קוסטה ג\'ים הו"ק', 179, None],
            ["02/10/2026", "NETFLIX", 69.9, "הוראת קבע"],
            ["03/10/2026", "הוקרה לעובד", 50, None],
            ["04/10/2026", "ACE", 400, "תשלום 2 מתוך 3 הוראת קבע"]]
    by = {t["merchant"]: t for t in ci.extract([rows])[0]}
    assert by["קוסטה ג'ים"]["standing_order"] is True          # `הו"ק` was dropped from the name
    assert by["NETFLIX"]["standing_order"] is True
    assert by["הוקרה לעובד"]["standing_order"] is False         # whole word only
    assert by["ACE"]["standing_order"] is False                 # installments: not a recurring series


def test_preview_offers_one_series_per_merchant_at_the_latest_amount(client):
    blob = _xlsx([["תאריך עסקה", "שם בית העסק", "סכום חיוב", "הערות"],
                  ["10/08/2026", 'קוסטה ג\'ים הו"ק', 255, "הוראת קבע"],
                  ["10/09/2026", 'קוסטה ג\'ים הו"ק', 179, "הוראת קבע"],
                  ["11/09/2026", "שופרסל", 80, None]])
    txs = _preview(client, blob).get_json()["transactions"]
    offers = [(t["date"], t["amount"]) for t in txs if t["recurring_offer"]]
    assert offers == [("2026-09-10", 179.0)]
    assert not any(t["recurring_offer"] for t in txs if t["merchant"] == "שופרסל")


def test_an_existing_series_is_not_offered_again(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "get_recurring_transactions",
                        lambda *a, **k: [{"description": "Netflix.com", "is_recurring": True,
                                          "recurring_end_date": None, "date": "2026-01-05"}])
    monkeypatch.setattr(app_module.db, "is_active_template", lambda r: True)
    blob = _xlsx([["תאריך עסקה", "שם בית העסק", "סכום חיוב", "הערות"],
                  ["13/10/2026", "Netflix.com", 69.9, "הוראת קבע"]])
    t = _preview(client, blob).get_json()["transactions"][0]
    assert (t["recurring_offer"], t["already_recurring"]) == (False, True)


def test_commit_turns_an_approved_standing_order_into_a_monthly_series(client, monkeypatch):
    calls, materialized = [], []
    monkeypatch.setattr(app_module.db, "add_transactions",
                        lambda rows: (calls.append(rows), (len(rows), None))[1])
    monkeypatch.setattr(app_module.db, "materialize_recurring", lambda fam: materialized.append(fam))
    res = _commit(client, [
        {"date": "2026-10-13", "amount": 69.9, "description": "Netflix.com", "category_id": _FOOD,
         "recurring": True},
        {"date": "2026-10-14", "amount": 80, "description": "שופרסל", "category_id": _FOOD},
    ])
    assert res.status_code == 201 and res.get_json()["recurring"] == 1
    netflix, super_ = calls[0]
    assert (netflix["is_recurring"], netflix["recurring_frequency"]) == (True, "monthly_same")
    assert (super_["is_recurring"], super_["recurring_frequency"]) == (False, None)
    assert set(netflix) == set(super_)                          # the same keys: one array
    assert materialized == [_FAM]


def test_no_series_means_no_materializing(client, monkeypatch):
    monkeypatch.setattr(app_module.db, "add_transactions", lambda rows: (len(rows), None))
    monkeypatch.setattr(app_module.db, "materialize_recurring", lambda fam: pytest.fail("wasn't needed"))
    row = {"date": "2026-10-03", "amount": 10, "description": "x", "category_id": _FOOD, "recurring": "yes"}
    assert _commit(client, [row]).get_json()["recurring"] == 0   # only a real `True`
