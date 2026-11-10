"""Importing transactions from a credit-card company's file (Matan, 31.10).

Once a month the user downloads the transaction statement from the Max, Cal or
Isracard site and uploads it here. This module is pure, with no database and
no network (except OpenAI, isolated in two functions at the end), so it can be
tested end to end without any doubles.

Three layers for detecting the columns, cheapest first:
  1. Known words in the header row ("תאריך עסקה", "שם בית העסק", "סכום חיוב").
     This covers all the big companies without knowing their exact layout upfront.
  2. OpenAI: gets only the header row and three sample rows, and says which
     column is what (`ai_map_columns`).
  3. The user picks (`mapping` sent back from the browser).

Left to `app.py`: permissions, duplicates against the database, and the insert itself.
"""
from __future__ import annotations

import calendar
import csv
import io
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 5 * 1024 * 1024
# Raw rows scanned per sheet. A family's monthly statement is tens to
# hundreds; the cap guards against a huge file sent by mistake (or on purpose).
MAX_ROWS_PER_SHEET = 5000
# Transactions per import: a full year of an active family fits comfortably.
MAX_TRANSACTIONS = 500
# How many days apart still count as "the same transaction": manual entry is
# sometimes a day or two after the purchase, and the card company sometimes
# records the posting date.
DUPLICATE_DAYS = 3

FIELDS = ("date", "merchant", "charge", "amount", "notes", "sector")


class ImportError_(Exception):
    """A file that can't be read. The message is in Hebrew and shown to the user."""


# ─── Reading the file into tables ───────────────────────────────────────────

def read_sheets(data: bytes, filename: str = "") -> list:
    """The file as a list of sheets, each sheet a list of rows, each row a list of cells.

    By content, not extension: some companies download a "`.xls`" that is
    really an HTML page with a table, and people rename extensions."""
    if not data:
        raise ImportError_("הקובץ ריק")
    if len(data) > MAX_FILE_BYTES:
        raise ImportError_("הקובץ גדול מדי (עד 5MB)")

    head = data[:8]
    if head.startswith(b"PK"):
        return _read_xlsx(data)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return _read_xls(data)

    if head.startswith(b"%PDF"):
        return _read_pdf(data)

    text = _decode(data)
    sniff = text.lstrip()[:2000].lower()
    if sniff.startswith("<") and ("<table" in sniff or "<html" in sniff or "<tr" in text.lower()):
        return [_read_html(text)]
    return [_read_csv(text)]


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1255"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_xlsx(data: bytes) -> list:
    try:
        import openpyxl
    except ImportError:                     # pragma: no cover (declared dependency)
        raise ImportError_("קריאת קבצי אקסל אינה זמינה כרגע")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        logger.info("card_import: xlsx could not be read", exc_info=True)
        raise ImportError_("לא הצלחנו לפתוח את קובץ האקסל — ודאו שזה הקובץ שהורד מהאתר")
    sheets = []
    try:
        for ws in wb.worksheets:
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= MAX_ROWS_PER_SHEET:
                    break
                rows.append([_clean_cell(c) for c in row])
            sheets.append(rows)
    finally:
        wb.close()
    return sheets


def _read_xls(data: bytes) -> list:
    try:
        import xlrd
    except ImportError:                     # pragma: no cover (declared dependency)
        raise ImportError_("קריאת קבצי אקסל ישנים אינה זמינה כרגע")
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception:
        logger.info("card_import: xls could not be read", exc_info=True)
        raise ImportError_("לא הצלחנו לפתוח את קובץ האקסל — ודאו שזה הקובץ שהורד מהאתר")
    sheets = []
    for sh in book.sheets():
        rows = []
        for r in range(min(sh.nrows, MAX_ROWS_PER_SHEET)):
            cells = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        cells.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                        continue
                    except Exception:
                        pass
                cells.append(_clean_cell(cell.value))
            rows.append(cells)
        sheets.append(rows)
    return sheets


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(_clean_cell("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if len(self.rows) < MAX_ROWS_PER_SHEET:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _read_html(text: str) -> list:
    parser = _TableParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        raise ImportError_("לא הצלחנו לקרוא את הקובץ")
    return parser.rows


def _read_csv(text: str) -> list:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        if i >= MAX_ROWS_PER_SHEET:
            break
        rows.append([_clean_cell(c) for c in row])
    return rows


# ─── PDF: the monthly statement (Matan, 31.10, a Cal sample) ────────────────
#
# A PDF has no table, just words at positions on the page. It is rebuilt like this:
#   1. Words → lines, by height on the page.
#   2. A "data line" is a line with a date and an amount. A run of them is a table.
#   3. The headers (one or two adjacent lines above, "תאריך / העסקה") define the
#      columns by horizontal position; every word in a data line goes to its column.
# Each table comes out as a separate "sheet", and from there it is all like
# Excel: the same header detection.
#
# And in Hebrew: some files store each word in visual order, so "העסק" is stored
# as "קסעה". This is detected from known header words, and reversed.

MAX_PDF_PAGES = 30
_HEB = re.compile(r"[֐-׿]")
_HEB_RUNS = re.compile(r"[֐-׿\"'׳״]+|[^֐-׿\"'׳״]+")
_KNOWN_WORDS = ("תאריך", "סכום", "העסק", "העסקה", "חיוב", "פירוט", "עסקות", "עסקאות")
# How many points below the previous line still count as the same line, and
# the gap between header lines
_LINE_TOLERANCE = 2.0
_HEADER_TO_DATA = 15.0
_HEADER_TO_HEADER = 8.0
_BLOCK_GAP = 40.0
_COLUMN_GAP = 6.0


def _read_pdf(data: bytes) -> list:
    try:
        import pdfplumber
    except ImportError:                     # pragma: no cover (declared dependency)
        raise ImportError_("קריאת PDF אינה זמינה כרגע — הורידו מהאתר את הפירוט כקובץ אקסל")
    pages = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages[:MAX_PDF_PAGES]:
                pages.append([{"text": w["text"], "x0": w["x0"], "x1": w["x1"], "top": w["top"]}
                              for w in page.extract_words()])
    except Exception:
        logger.info("card_import: pdf could not be read", exc_info=True)
        raise ImportError_("לא הצלחנו לפתוח את קובץ ה-PDF — נסו להוריד מהאתר את הפירוט כקובץ אקסל")
    sheets = sheets_from_pdf_words(pages)
    if not sheets:
        raise ImportError_("לא מצאנו טקסט ב-PDF (אולי זה קובץ סרוק). "
                           "הורידו מהאתר את הפירוט כקובץ אקסל, או כ-PDF מקורי")
    return sheets


def _visual_order(pages) -> bool:
    """Whether the Hebrew words are stored reversed. Decided by known header words."""
    straight = flipped = 0
    for words in pages:
        for w in words:
            t = w["text"]
            if not _HEB.search(t):
                continue
            straight += any(k in t for k in _KNOWN_WORDS)
            flipped += any(k in t[::-1] for k in _KNOWN_WORDS)
    return flipped > straight


def _fix_word(text: str, visual: bool) -> str:
    if not visual or not _HEB.search(text):
        return text
    runs = _HEB_RUNS.findall(text)
    return "".join(r[::-1] if _HEB.search(r) else r for r in reversed(runs))


def _lines(words, visual):
    """Words → lines, top to bottom; within each line, words right to left."""
    lines = []
    for w in sorted(words, key=lambda w: w["top"]):
        w = dict(w, text=_fix_word(w["text"], visual))
        if lines and abs(lines[-1]["top"] - w["top"]) <= _LINE_TOLERANCE:
            lines[-1]["words"].append(w)
        else:
            lines.append({"top": w["top"], "words": [w]})
    for line in lines:
        line["words"].sort(key=lambda w: -w["x1"])
        line["text"] = " ".join(w["text"] for w in line["words"])
    return lines


def _is_data_line(line) -> bool:
    texts = [w["text"] for w in line["words"]]
    has_date = any(parse_date(t) for t in texts)
    has_amount = any(parse_date(t) is None and _parse_amount_or_none(t) not in (None, 0)
                     and re.search(r"\d[.,]\d{2}$|^\d+$", t.strip("₪ ")) for t in texts)
    return has_date and has_amount


def _columns(header_lines):
    """Columns from the headers: adjacent words on the same line are one header
    ("שם בית העסק"), and headers on two lines that overlap horizontally are the
    same column ("סכום / חיוב")."""
    cols = []
    for line in header_lines:
        clusters = []
        for w in line["words"]:              # right to left
            if clusters and clusters[-1]["x0"] - w["x1"] <= _COLUMN_GAP:
                c = clusters[-1]
                c["x0"] = min(c["x0"], w["x0"])
                c["parts"].append(w["text"])
            else:
                clusters.append({"x0": w["x0"], "x1": w["x1"], "parts": [w["text"]]})
        for c in clusters:
            same = next((k for k in cols if c["x0"] < k["x1"] and k["x0"] < c["x1"]), None)
            if same:
                same["x0"], same["x1"] = min(same["x0"], c["x0"]), max(same["x1"], c["x1"])
                same["text"] += " " + " ".join(c["parts"])
            else:
                cols.append({"x0": c["x0"], "x1": c["x1"], "text": " ".join(c["parts"])})
    return sorted(cols, key=lambda c: -c["x1"])


def _cells(line, cols):
    """Each word to its nearest column (by the boundary midway between columns)."""
    cells = [[] for _ in cols]
    for w in line["words"]:
        mid = (w["x0"] + w["x1"]) / 2
        best = min(range(len(cols)), key=lambda i: 0 if cols[i]["x0"] <= mid <= cols[i]["x1"]
                   else min(abs(mid - cols[i]["x0"]), abs(mid - cols[i]["x1"])))
        cells[best].append(w["text"])
    return [" ".join(c) if c else None for c in cells]


def sheets_from_pdf_words(pages) -> list:
    """Pages of words (`text, x0, x1, top`) → "sheets" by table.
    Separate from `pdfplumber` so it can be tested without a file."""
    visual = _visual_order(pages)
    sheets = []
    for words in pages:
        lines = _lines(words, visual)
        i = 0
        while i < len(lines):
            if not _is_data_line(lines[i]):
                i += 1
                continue
            # The headers: up to two adjacent lines above the first data line
            header, top = [], lines[i]["top"]
            j = i - 1
            gap = _HEADER_TO_DATA
            while j >= 0 and len(header) < 2 and top - lines[j]["top"] <= gap \
                    and not _is_data_line(lines[j]):
                header.insert(0, lines[j])
                top, gap = lines[j]["top"], _HEADER_TO_HEADER
                j -= 1
            block, last = [], lines[i]["top"]
            while i < len(lines) and lines[i]["top"] - last <= _BLOCK_GAP:
                if _is_data_line(lines[i]):
                    block.append(lines[i])
                    last = lines[i]["top"]
                i += 1
            if not header:
                continue
            cols = _columns(header)
            sheets.append([[c["text"] for c in cols]] + [_cells(l, cols) for l in block])
    return sheets


# Direction marks that Hebrew Excel scatters inside cells: invisible, and they
# break comparisons
_BIDI = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮⁦⁧⁨⁩﻿"), None)


def _clean_cell(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.translate(_BIDI).replace("\xa0", " ").strip()
        return value or None
    return value


# ─── Detecting the columns ──────────────────────────────────────────────────

def _norm_header(value) -> str:
    if not isinstance(value, str):
        return ""
    v = value.translate(_BIDI).lower()
    v = re.sub(r"[\"'״׳`]", "", v)
    v = re.sub(r"[\s_:.\-()]+", " ", v)
    return v.strip()


def _first(headers, used, *preds):
    """The first column matching the first condition that matches anything, so
    "תאריך עסקה" beats "תאריך חיוב" even when it appears after it."""
    for pred in preds:
        for i, h in enumerate(headers):
            if h and i not in used and pred(h):
                return i
    return None


def detect_mapping(header_row) -> dict | None:
    """Field → column index mapping from the header words. `None`: not a header row.

    A date, a business and some amount are required. "סכום חיוב" (in shekels,
    what was actually charged) is preferred over "סכום עסקה" (which, for a
    purchase abroad, is in dollars)."""
    headers = [_norm_header(c) for c in header_row]
    if sum(1 for h in headers if h) < 3:
        return None
    used = set()

    def take(i):
        if i is not None:
            used.add(i)
        return i

    has = lambda *words: (lambda h: all(w in h for w in words))
    not_ = lambda pred, *words: (lambda h: pred(h) and not any(w in h for w in words))

    m = {}
    m["date"] = take(_first(headers, used,
                            lambda h: h in ("תאריך עסקה", "תאריך העסקה", "תאריך רכישה",
                                            "תאריך ביצוע", "תאריך", "date", "transaction date"),
                            not_(has("תאריך"), "חיוב", "קליטה"),
                            has("date"),
                            has("תאריך")))
    m["charge"] = take(_first(headers, used,
                              not_(has("סכום", "חיוב"), "מקורי"),
                              has("חיוב", "שח"),
                              has("charged"),
                              lambda h: h in ("חיוב", "סכום בשח", "סכום בש ח")))
    m["amount"] = take(_first(headers, used,
                              lambda h: h in ("סכום עסקה", "סכום העסקה", "סכום עסקה מקורי",
                                              "סכום מקורי", "סכום", "amount"),
                              not_(has("סכום"), "מטבע", "שער"),
                              has("amount")))
    m["merchant"] = take(_first(headers, used,
                                has("בית", "עסק"),
                                has("שם העסק"),
                                lambda h: h in ("תיאור", "תאור", "פרטים", "שם", "description",
                                                "merchant", "תיאור התנועה", "פרטי העסקה"),
                                not_(has("תיאור"), "נוסף"),
                                has("merchant"), has("description")))
    m["notes"] = take(_first(headers, used,
                             lambda h: h in ("הערות", "הערה", "פירוט נוסף", "סוג עסקה",
                                             "פרטים נוספים", "notes"),
                             has("הער"), has("סוג עסק"), has("פירוט")))
    # The card company's own category ("ענף" at Cal, "קטגוריה" at Max): a hint
    m["sector"] = take(_first(headers, used,
                              lambda h: h in ("ענף", "קטגוריה", "ענף עסקי", "סוג בית עסק",
                                              "category")))
    if m["date"] is None or m["merchant"] is None or (m["charge"] is None and m["amount"] is None):
        return None
    return {k: v for k, v in m.items() if v is not None}


def header_signature(row) -> list:
    return [_norm_header(c) for c in row]


def find_header_candidate(sheets):
    """For a file the words didn't recognise: the row most likely to be the headers,
    and the three rows after it. This is what is sent to the AI and what the user
    sees when picking manually.

    Finds the first data row (it has a date and an amount); the header is the
    nearest row above it with at least two text cells."""
    for rows in sheets:
        for d, data_row in enumerate(rows[:80]):
            if not _row_has_date_and_amount(data_row):
                continue
            for i in range(d - 1, max(d - 6, -1), -1):
                row = rows[i]
                texts = [c for c in row if isinstance(c, str) and parse_date(c) is None
                         and _parse_amount_or_none(c) is None]
                if len(texts) < 2:
                    continue
                following = [r for r in rows[i + 1:i + 8] if any(c is not None for c in r)][:3]
                width = max([len(row)] + [len(r) for r in following])
                return {
                    "signature": header_signature(row),
                    "headers": [_cell_text(c) for c in row] + [""] * (width - len(row)),
                    "samples": [[_cell_text(c) for c in r] + [""] * (width - len(r))
                                for r in following],
                }
            break
    return None


def _row_has_date_and_amount(row) -> bool:
    return any(parse_date(c) for c in row) and any(
        _parse_amount_or_none(c) not in (None, 0) and parse_date(c) is None for c in row)


def _cell_text(c) -> str:
    if c is None:
        return ""
    if isinstance(c, datetime):
        return c.strftime("%d/%m/%Y")
    if isinstance(c, date):
        return c.strftime("%d/%m/%Y")
    if isinstance(c, float) and c.is_integer():
        return str(int(c))
    return str(c)[:80]


def valid_mapping(mapping, width: int) -> dict | None:
    """A mapping that came from outside (AI or browser): integer indexes within
    the table width, no column used twice, and the required fields present."""
    if not isinstance(mapping, dict):
        return None
    out = {}
    for k in FIELDS:
        v = mapping.get(k)
        if v is None or v == "" or v == -1:
            continue
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < max(width, 1):
            return None
        out[k] = v
    if len(set(out.values())) != len(out):
        return None
    if "date" not in out or "merchant" not in out or not ({"charge", "amount"} & out.keys()):
        return None
    return out


# ─── Values ─────────────────────────────────────────────────────────────────

_EXCEL_EPOCH = date(1899, 12, 30)
_DMY = re.compile(r"^(\d{1,2})[./\-](\d{1,2})[./\-](\d{2}|\d{4})(?:\s.*)?$")
_YMD = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\s].*)?$")


def parse_date(value):
    """A date from a cell. Israeli order (day/month/year): no Israeli company
    exports month/day, and a wrong guess would silently move transactions
    between months."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        # An Excel serial number, only within a sane range (1954–2119)
        if 20000 <= value <= 80000:
            return _EXCEL_EPOCH + timedelta(days=int(value))
        return None
    if not isinstance(value, str):
        return None
    s = value.strip()
    m = _DMY.match(s)
    try:
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
            return date(y, mo, d)
        m = _YMD.match(s)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return None


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_amount_or_none(value):
    """An amount from a cell, signed. "₪ 1,234.50", "54.90-", "(54.90)" and "`-54.9`"."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    s = value.translate(_BIDI).strip()
    if not s or not re.search(r"\d", s):
        return None
    # A cell with letters that aren't a currency mark: text, not an amount
    # ("תשלום 3 מתוך 12")
    letters = re.sub(r"(ש\"?ח|ש״ח|nis|ils|usd|eur|₪|\$|€|£)", "", s, flags=re.I)
    if re.search(r"[A-Za-z֐-׿]", letters):
        return None
    if _DMY.match(s) or _YMD.match(s):
        return None
    negative = s.startswith("-") or s.endswith("-") or (s.startswith("(") and s.endswith(")"))
    digits = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
    found = _NUM.search(digits.replace("-", ""))
    if not found:
        return None
    try:
        number = float(found.group(0))
    except ValueError:
        return None
    return -number if negative else number


parse_amount = _parse_amount_or_none

_INSTALLMENT = re.compile(r"תשלום\s*(\d{1,2})\s*(?:מתוך|מ-|מ\s|/|מ)\s*(\d{1,2})")


def parse_installment(cells):
    """"תשלום 3 מתוך 12" in one of the cells → `(3, 12)`, or `None`."""
    for c in cells:
        if isinstance(c, str):
            m = _INSTALLMENT.search(c)
            if m:
                k, n = int(m.group(1)), int(m.group(2))
                if 1 <= k <= n <= 60 and n >= 2:
                    return k, n
    return None


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


# ─── Extracting the transactions ────────────────────────────────────────────

_SUMMARY_WORDS = ("סהכ", "סה כ", "סך הכל", "total", "יתרה")


def extract(sheets, known_layouts=None):
    """All the transactions in the file.

    `known_layouts`: mappings already confirmed (by the AI or the user), as a
    list of `{"signature": [...], "mapping": {...}}`. A row whose headers equal
    a signature gets that mapping.

    Returns `(transactions, layouts_used)`. An empty transaction list with an
    empty `layouts_used` means no header row was found; `app.py` moves to the
    next layer."""
    known = []
    for layout in known_layouts or []:
        if isinstance(layout, dict) and isinstance(layout.get("signature"), list):
            known.append(([str(s) for s in layout["signature"]], layout.get("mapping")))

    out, used = [], []
    for rows in sheets:
        mapping = None
        for row in rows:
            if not row or all(c is None for c in row):
                continue
            sig = header_signature(row)
            new_mapping = None
            for ksig, kmap in known:
                if _same_signature(sig, ksig):
                    new_mapping = valid_mapping(kmap, len(row))
                    break
            if new_mapping is None:
                new_mapping = detect_mapping(row)
            if new_mapping is not None:
                mapping = new_mapping
                entry = {"signature": sig, "mapping": mapping}
                if entry not in used:
                    used.append(entry)
                continue
            if mapping is None:
                continue
            tx = _row_to_tx(row, mapping)
            if tx:
                out.append(tx)
                if len(out) > MAX_TRANSACTIONS:
                    raise ImportError_(f"יש בקובץ יותר מ-{MAX_TRANSACTIONS} עסקאות — "
                                       f"הורידו פירוט של תקופה קצרה יותר")
    return out, used


def _same_signature(a, b) -> bool:
    a = [x for x in a]
    b = [x for x in b]
    while a and not a[-1]:
        a.pop()
    while b and not b[-1]:
        b.pop()
    return a == b and any(a)


def _cell(row, i):
    return row[i] if i is not None and i < len(row) else None


# Standing order (Matan, 1.11): Cal writes "הוראת קבע" in the details and
# `הו"ק` at the end of the business name; Max writes "הוראת קבע" in the
# transaction type. Whole word only: "הוקרה" is not `הו"ק`.
_STANDING = re.compile(r"הוראת\s*קבע|(?<![\u0590-\u05FF])הו[\"״']?ק(?![\u0590-\u05FF])")
_STANDING_SUFFIX = re.compile(r"\s*(?<![\u0590-\u05FF])הו[\"״']?ק\s*$")


def is_standing_order(cells) -> bool:
    return any(isinstance(c, str) and _STANDING.search(c) for c in cells)


def _row_to_tx(row, mapping):
    when = parse_date(_cell(row, mapping.get("date")))
    if when is None:
        return None
    merchant = _cell(row, mapping.get("merchant"))
    if isinstance(merchant, (int, float)) and not isinstance(merchant, bool):
        merchant = _cell_text(merchant)
    if not isinstance(merchant, str) or not merchant.strip():
        return None
    merchant = re.sub(r"\s+", " ", merchant).strip()[:100]
    if any(w in _norm_header(merchant) for w in _SUMMARY_WORDS):
        return None

    charge = parse_amount(_cell(row, mapping.get("charge")))
    amount = parse_amount(_cell(row, mapping.get("amount")))
    # A transaction not yet charged (or abroad, before conversion): the charge
    # amount ("סכום חיוב") is empty or 0
    value = charge if charge not in (None, 0) else amount
    if value in (None, 0):
        return None

    inst = parse_installment([_cell(row, mapping.get("notes"))] + list(row))
    # An installment series is not a standing order: it ends, and it has its
    # own mechanism
    standing = not inst and value > 0 and is_standing_order(row)
    if standing:
        merchant = _STANDING_SUFFIX.sub("", merchant) or merchant
    description = merchant
    if inst:
        k, n = inst
        description = f"{merchant} (תשלום {k} מתוך {n})"
        # With installments, the transaction date is the purchase day on every
        # installment. Installment k is charged k-1 months later; otherwise all
        # 12 installments would land in the same month, and next month's import
        # would be an uncaught duplicate.
        when = _add_months(when, k - 1)

    return {
        "date":        when.isoformat(),
        "merchant":    merchant,
        "description": description[:120],
        "amount":      round(abs(value), 2),
        "refund":      value < 0,
        "installment": list(inst) if inst else None,
        "sector":      _sector(_cell(row, mapping.get("sector"))),
        "standing_order": standing,
    }


def _sector(value):
    return re.sub(r"\s+", " ", value).strip()[:40] if isinstance(value, str) and value.strip() else None


# ─── Business names: matching against history ───────────────────────────────

def merchant_key(text) -> str:
    """A business name for comparison: no digits (branch number), symbols or
    double spaces. "פז חולון 4531" and "פז  חולון" are the same key."""
    if not isinstance(text, str):
        return ""
    t = text.translate(_BIDI).lower()
    t = re.sub(r"\(תשלום[^)]*\)", " ", t)
    t = re.sub(r"[\d]+", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"_", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _related(a: str, b: str) -> bool:
    """The same business: an identical key, or one is the start of the other in
    whole words ("שופרסל" entered manually and "שופרסל דיל" from the file)."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 3 and long_.startswith(short + " ")


def learned_categories(history) -> dict:
    """Business key → the category last chosen for it in the family.

    `history`: rows `{description, category_id, date}`, newest first. This is
    the whole "memory": an imported transaction with a corrected category is
    saved with description = business name, and next month it is the one found
    here. Manually entered transactions teach too."""
    out = {}
    for row in history:
        key = merchant_key(row.get("description"))
        if key and row.get("category_id") and key not in out:
            out[key] = row["category_id"]
    return out


def lookup_learned(key: str, learned: dict):
    if key in learned:
        return learned[key]
    best = None
    for k, cat in learned.items():
        if _related(key, k) and (best is None or len(k) > len(best[0])):
            best = (k, cat)
    return best[1] if best else None


# First-time rules, before there is any history: a word in the business name →
# a word in the category name. By name, not id, because every family has its
# own categories, and a rule applies only when the family has a category whose
# name matches ("סופר ומזון", "מזון").
_RULES = [
    (("שופרסל", "רמי לוי", "ויקטורי", "יוחננוף", "אושר עד", "טיב טעם", "יינות ביתן",
      "חצי חינם", "קרפור", "carrefour", "מגה", "פרש מרקט", "סופר", "מכולת", "מאפי",
      "קצביה", "ירקות", "am pm", "ampm", "שוק"),
     ("מזון", "סופר")),
    (("פז", "דלק", "סונול", "דור אלון", "ten", "yellow", "רב קו", "gett", "moovit",
      "מוביט", "חניון", "חניה", "פנגו", "pango", "cellopark", "סלופארק", "כביש",
      "מוסך", "רכבת", "אגד", "דן"),
     ("רכב", "תחבורה")),
    (("חברת החשמל", "חשמל", "מים", "תאגיד", "ארנונה", "עירית", "עיריית", "בזק", "hot",
      "הוט", "yes", "פרטנר", "partner", "סלקום", "cellcom", "פלאפון", "pelephone",
      "גולן טלקום", "netflix", "spotify", "apple com", "google", "דיסני", "disney", "ביטוח"),
     ("חשבונות", "מנויים")),
    (("סופר פארם", "super pharm", "מכבי", "כללית", "מאוחדת", "לאומית", "בית מרקחת",
      "פארם", "רופא", "שיניים", "אופטיקה"),
     ("בריאות",)),
    (("מסעד", "קפה", "cafe", "coffee", "ארומה", "aroma", "wolt", "וולט", "תן ביס", "10bis",
      "cibus", "סיבוס", "מקדונלד", "mcdonald", "בורגר", "burger", "פיצ", "pizza", "דומינו",
      "סינמה", "cinema", "יס פלאנט", "פאב", "בר"),
     ("מסעדות", "בילויים", "בידור")),
    (("zara", "זארה", "h m", "קסטרו", "castro", "fox", "פוקס", "aliexpress", "amazon",
      "אמזון", "shein", "שיין", "terminal x", "טרמינל", "אדידס", "adidas", "נייקי", "nike",
      "ksp", "באג", "מחסני חשמל"),
     ("שופינג", "קניות", "ביגוד")),
    (("ikea", "איקאה", "ace", "אייס", "הום סנטר", "home center", "ביתילי", "פוקס הום"),
     ("ריהוט", "לבית", "בית")),
    (("אל על", "el al", "booking", "airbnb", "ישראייר", "ארקיע", "hotel", "מלון", "wizz",
      "ryanair", "easyjet"),
     ("חופשות", "טיולים", "נסיעות")),
    (("מספרה", "קוסמטיק", "ציפורניים", "nails", "spa", "ספא"),
     ("טיפוח",)),
]


def rule_category(merchant: str, categories: list):
    """A category by the rules, from the family's expense categories. `None`: none.

    A short word (up to 3 characters: "פז", "בר", "hot") must appear as a whole
    word; otherwise "בר" would match "עבר" and "פז" would match "חפז"."""
    text = (merchant or "").lower().translate(_BIDI)
    text = " " + re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip() + " "
    # The longest word wins: "סופר פארם" is health, not "סופר",
    # and "מחסני חשמל" is shopping, not "חשמל"
    best = None
    for merchant_words, category_words in _RULES:
        hits = [w for w in merchant_words if ((f" {w} " in text) if len(w) <= 3 else (w in text))]
        if not hits:
            continue
        cat = next((c for c in categories
                    if any(cw in (c.get("name") or "") for cw in category_words)), None)
        if cat and (best is None or len(max(hits, key=len)) > best[0]):
            best = (len(max(hits, key=len)), cat["id"])
    return best[1] if best else None


# The sector the card company itself gives ("פנאי בילוי", "רכב ותחבורה") → a
# word in the family's category name. Only when nothing matched by the business
# itself; the sector is more general.
_SECTOR_RULES = [
    (("מזון", "סופר", "מכולת"), ("מזון", "סופר")),
    (("רכב", "תחבור", "דלק", "חניה"), ("רכב", "תחבורה")),
    (("מסעד", "בילוי", "פנאי", "מזון מהיר", "בתי קפה"), ("מסעדות", "בילויים", "בידור")),
    (("תקשורת", "מחשבים", "חשמל ומים", "ביטוח", "שירותים"), ("חשבונות", "מנויים")),
    (("רפואה", "בריאות", "פארם", "אופטיקה"), ("בריאות",)),
    (("אופנה", "ביגוד", "הנעלה", "קניות", "כלבו", "מוצרי און"), ("שופינג", "קניות", "ביגוד")),
    (("ריהוט", "בית ומשרד", "בית וגן", "עיצוב"), ("ריהוט", "לבית", "בית")),
    (("תיירות", "טיסות", "נופש", "מלונות"), ("חופשות", "טיולים", "נסיעות")),
    (("קוסמטיקה", "טיפוח", "יופי"), ("טיפוח",)),
]


def sector_category(sector, categories: list):
    if not sector:
        return None
    for sector_words, category_words in _SECTOR_RULES:
        if any(w in sector for w in sector_words):
            cat = next((c for c in categories
                        if any(cw in (c.get("name") or "") for cw in category_words)), None)
            if cat:
                return cat["id"]
    return None


# ─── Duplicates ─────────────────────────────────────────────────────────────

def mark_duplicates(items: list, existing: list) -> None:
    """Marks every transaction from the file that already exists in the database.
    Mutates `items` in place.

    `exists`: same amount, at most `DUPLICATE_DAYS` days apart, and the same
    business (or the existing transaction has no description). `maybe`: same
    amount and a close date, but a different description: perhaps entered
    manually under another name; not ticked for import, the user decides.

    Each existing row is "consumed" once: two identical coffees on the same day
    in the file, one of them entered manually, so the other is still new."""
    pool = []
    for row in existing:
        try:
            d = date.fromisoformat(str(row.get("date"))[:10])
            amt = round(float(row.get("amount")), 2)
        except (TypeError, ValueError):
            continue
        pool.append({"date": d, "amount": amt, "key": merchant_key(row.get("description")),
                     "description": row.get("description") or "", "used": False})

    for want_exact in (True, False):
        for item in items:
            if item.get("status"):
                continue
            d = date.fromisoformat(item["date"])
            key = merchant_key(item["merchant"])
            best = None
            for p in pool:
                if p["used"] or abs(p["amount"] - item["amount"]) > 0.005:
                    continue
                gap = abs((p["date"] - d).days)
                if gap > DUPLICATE_DAYS:
                    continue
                same = not p["key"] or _related(key, p["key"])
                if want_exact != same:
                    continue
                if best is None or gap < best[0]:
                    best = (gap, p)
            if best:
                p = best[1]
                p["used"] = True
                item["status"] = "exists" if want_exact else "maybe"
                item["match"] = {"date": p["date"].isoformat(), "description": p["description"]}
    for item in items:
        item.setdefault("status", "new")


# ─── OpenAI ─────────────────────────────────────────────────────────────────

def _openai():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:                     # pragma: no cover
        return None
    return OpenAI(api_key=api_key, timeout=25)


def _call_tool(client, tool, prompt, max_tokens):
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=max_tokens,
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": tool["function"]["name"]}},
        messages=[{"role": "user", "content": prompt}],
    )
    calls = response.choices[0].message.tool_calls
    if not calls:
        return None
    try:
        return json.loads(calls[0].function.arguments or "{}")
    except (TypeError, ValueError):
        return None


def ai_map_columns(headers: list, samples: list):
    """Layer 2: which column is what, from the headers and three sample rows only.
    Nothing beyond that is sent. `None`: unavailable or failed."""
    client = _openai()
    if not client:
        return None
    col = {"type": "integer", "description": "Column index (0-based), or -1 if there is none"}
    tool = {"type": "function", "function": {
        "name": "map_columns",
        "description": "Maps the columns of an Israeli credit-card transaction statement to fields.",
        "parameters": {"type": "object", "properties": {
            "date": dict(col, description="Transaction date (not the charge date), 0-based index"),
            "merchant": dict(col, description="Business name / transaction description, 0-based index"),
            "charge": dict(col, description="Amount charged in shekels, 0-based index, or -1"),
            "amount": dict(col, description="Original transaction amount, 0-based index, or -1"),
            "notes": dict(col, description="Notes / transaction type (e.g. installments), or -1"),
        }, "required": ["date", "merchant", "charge", "amount"]},
    }}
    table = "\n".join(" | ".join(f"[{i}] {h}" for i, h in enumerate(r))
                      for r in [headers] + samples)
    try:
        data = _call_tool(client, tool,
                          "These are the headers and first three rows of a credit-card transaction statement. "
                          "Use the tool to return which column each field is.\n\n" + table, 200)
    except Exception:
        logger.exception("ai_map_columns")
        return None
    if not isinstance(data, dict):
        return None
    return valid_mapping({k: data.get(k) for k in FIELDS}, len(headers))


def ai_categorize(merchants: list, category_names: list) -> dict:
    """Business names → a category name from the family's own categories only.
    Only business names are sent: no amounts, dates or card details."""
    if not merchants or not category_names:
        return {}
    client = _openai()
    if not client:
        return {}
    merchants = merchants[:80]
    tool = {"type": "function", "function": {
        "name": "categorize",
        "description": "Assigns each Israeli business to an expense category.",
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "merchant": {"type": "string"},
                "category": {"type": "string", "enum": category_names + [""],
                             "description": "Empty if there is no clear match"},
            }, "required": ["merchant", "category"]}},
        }, "required": ["items"]},
    }}
    try:
        data = _call_tool(client, tool,
                          "Assign each of the following businesses (from an Israeli credit-card statement) to one of "
                          "the categories. If unclear, leave it empty.\n\n" + "\n".join(merchants),
                          60 + 40 * len(merchants))
    except Exception:
        logger.exception("ai_categorize")
        return {}
    out = {}
    allowed = set(category_names)
    for item in (data or {}).get("items") or []:
        if isinstance(item, dict) and item.get("category") in allowed \
                and isinstance(item.get("merchant"), str):
            out[item["merchant"]] = item["category"]
    return out
