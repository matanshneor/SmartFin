/* Importing from the card company (`/import`, Matan 31.10).
 *
 * Four steps on the same page: upload → (unknown file: pick columns) → review
 * and confirm → done. The server reads the file and stores nothing until
 * "import"; here we only display, let categories be fixed and choose what goes in.
 *
 * Confirmed layouts (from the AI or by hand) are kept in localStorage and sent
 * with every upload, so next time the same file is read right away, with no AI
 * and no questions. If storage is blocked, we just ask again. */
(function () {
'use strict';

const data = window.sfData('importData');
const categories = data.categories || [];
const LAYOUTS_KEY = 'sf_import_layouts';

const $ = function (id) { return document.getElementById(id); };
const steps = { upload: $('importUpload'), mapping: $('importMapping'),
                review: $('importReview'), done: $('importDone') };
const fileInput = $('importFile');
const errorEl = $('importError');
const fileNameEl = $('importFileName');
const listEl = $('importList');
const existsEl = $('importExists');
const refundsEl = $('importRefunds');
const commitBtn = $('importCommit');
const commitErr = $('importCommitError');

let currentFile = null;
let candidate = null;     // what the server returned when the file wasn't recognised
let rows = [];            // the transactions from the server, with `checked` and `category_id` set by the user

/* Shows a step. `motion.js` gives every `.chart-card` an entrance animation on
 * load, and a card hidden at that moment stays transparent forever, because
 * the animation of a hidden element doesn't run and doesn't finish. So the
 * step being opened is released from it. */
function reveal(el) {
    el.hidden = false;
    el.classList.remove('anim-rise', 'reveal-fallback', 'reveal-scroll');
    el.style.animationDelay = '';
}

function show(name) {
    Object.keys(steps).forEach(function (k) {
        if (k === name) reveal(steps[k]); else steps[k].hidden = true;
    });
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function loadLayouts() {
    try {
        const v = JSON.parse(localStorage.getItem(LAYOUTS_KEY) || '[]');
        return Array.isArray(v) ? v : [];
    } catch (e) { return []; }
}

function saveLayouts(used) {
    if (!Array.isArray(used) || !used.length) return;
    const all = loadLayouts();
    used.forEach(function (layout) {
        const sig = JSON.stringify(layout.signature);
        const i = all.findIndex(function (l) { return JSON.stringify(l.signature) === sig; });
        if (i >= 0) all.splice(i, 1);
        all.unshift(layout);
    });
    try { localStorage.setItem(LAYOUTS_KEY, JSON.stringify(all.slice(0, 20))); } catch (e) { /* no memory: ask again */ }
}

function busy(on, label) {
    const btn = document.querySelector('.import-file-btn');
    btn.classList.toggle('is-busy', on);
    btn.textContent = on ? (label || 'קורא את הקובץ…') : 'בחירת קובץ';
    fileInput.disabled = on;
}

// ─── Step 1: upload ─────────────────────────────────────────────────────


function preview(mapping) {
    if (!currentFile) return;
    errorEl.textContent = '';
    $('importMapError').textContent = '';
    const form = new FormData();
    form.append('file', currentFile);
    form.append('layouts', JSON.stringify(loadLayouts()));
    if (mapping) form.append('mapping', JSON.stringify(mapping));
    busy(true);
    fetch('/api/import/preview', { method: 'POST', body: form })
        .then(function (r) {
            return r.json().catch(function () { return { error: 'שגיאה בקריאת הקובץ — נסו שוב' }; });
        })
        .then(function (res) {
            busy(false);
            if (res.error) {
                (mapping ? $('importMapError') : errorEl).textContent = res.error;
                return;
            }
            if (res.status === 'needs_mapping') { showMapping(res); return; }
            saveLayouts(res.layouts);
            showReview(res);
        })
        .catch(function () { busy(false); errorEl.textContent = window.sfNetError(); });
}

fileInput.addEventListener('change', function () {
    currentFile = fileInput.files && fileInput.files[0];
    fileNameEl.textContent = currentFile ? currentFile.name : '';
    if (currentFile) preview(null);
});

document.querySelectorAll('[data-import-restart]').forEach(function (btn) {
    btn.addEventListener('click', function () {
        currentFile = null;
        fileInput.value = '';
        fileNameEl.textContent = '';
        errorEl.textContent = '';
        show('upload');
        fileInput.click();
    });
});

// ─── Step 2: picking columns ────────────────────────────────────────────


function showMapping(res) {
    candidate = res;
    const table = $('importSample');
    table.textContent = '';
    const head = table.createTHead().insertRow();
    res.headers.forEach(function (h, i) {
        const th = document.createElement('th');
        th.textContent = h || ('עמודה ' + (i + 1));
        head.appendChild(th);
    });
    const body = table.createTBody();
    res.samples.forEach(function (r) {
        const tr = body.insertRow();
        r.forEach(function (c) { tr.insertCell().textContent = c; });
    });
    document.querySelectorAll('[data-map]').forEach(function (sel) {
        sel.textContent = '';
        sel.add(new Option('—', ''));
        res.headers.forEach(function (h, i) { sel.add(new Option(h || ('עמודה ' + (i + 1)), String(i))); });
    });
    reveal(steps.mapping);
    steps.review.hidden = true;
    steps.mapping.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

$('importMapGo').addEventListener('click', function () {
    const mapping = {};
    document.querySelectorAll('[data-map]').forEach(function (sel) {
        if (sel.value !== '') mapping[sel.dataset.map] = parseInt(sel.value, 10);
    });
    const err = $('importMapError');
    if (mapping.date === undefined || mapping.merchant === undefined) {
        err.textContent = 'נא לבחור לפחות תאריך ובית עסק'; return;
    }
    if (mapping.charge === undefined && mapping.amount === undefined) {
        err.textContent = 'נא לבחור את עמודת הסכום'; return;
    }
    const cols = Object.keys(mapping).map(function (k) { return mapping[k]; });
    if (new Set(cols).size !== cols.length) { err.textContent = 'אותה עמודה נבחרה פעמיים'; return; }
    preview({ signature: candidate.signature, mapping: mapping });
});

// ─── Step 3: review and confirm ─────────────────────────────────────────


function shortDate(iso) {
    const p = iso.split('-');
    return parseInt(p[2], 10) + '.' + parseInt(p[1], 10) + (p[0] !== String(new Date().getFullYear()) ? '.' + p[0].slice(2) : '');
}

function categorySelect(row) {
    const sel = document.createElement('select');
    sel.className = 'form-input import-cat';
    sel.setAttribute('aria-label', 'קטגוריה ל' + row.merchant);
    sel.add(new Option('בחירת קטגוריה…', ''));
    categories.forEach(function (c) {
        sel.add(new Option(((c.icon || '') + ' ' + c.name).trim(), c.id));
    });
    sel.value = row.category_id || '';
    sel.addEventListener('change', function () {
        row.category_id = sel.value || null;
        // The same business on other rows without a category gets the same choice

        if (row.category_id) {
            rows.forEach(function (other) {
                if (other !== row && other.merchant === row.merchant && !other.category_id && other._select) {
                    other.category_id = row.category_id;
                    other._select.value = row.category_id;
                    other._li.classList.remove('needs-cat');
                }
            });
        }
        row._li.classList.toggle('needs-cat', row.checked && !row.category_id);
        refresh();
    });
    row._select = sel;
    return sel;
}

function note(row) {
    if (row.status === 'exists' && row.match)
        return 'כבר קיים: ' + (row.match.description || 'ללא תיאור') + ' ב-' + shortDate(row.match.date);
    if (row.status === 'maybe' && row.match)
        return 'ייתכן שכבר הוזן: "' + (row.match.description || 'ללא תיאור') + '" ב-' + shortDate(row.match.date) + ' באותו סכום';
    return '';
}

function rowElement(row, importable) {
    const li = document.createElement('li');
    li.className = 'import-row';
    row._li = li;

    if (importable) {
        const check = document.createElement('input');
        check.type = 'checkbox';
        check.className = 'import-check';
        check.checked = row.checked;
        check.setAttribute('aria-label', 'לייבא את ' + row.merchant);
        check.addEventListener('change', function () {
            row.checked = check.checked;
            li.classList.toggle('is-off', !row.checked);
            li.classList.toggle('needs-cat', row.checked && !row.category_id);
            refresh();
        });
        row._check = check;
        li.appendChild(check);
    }

    const main = document.createElement('div');
    main.className = 'import-main';
    const line = document.createElement('div');
    line.className = 'import-line';
    const name = document.createElement('span');
    name.className = 'import-merchant';
    name.textContent = row.description;
    const amount = document.createElement('span');
    amount.className = 'import-amount';
    amount.textContent = (row.refund ? '+' : '') + '₪' + window.sfMoney(row.amount);
    line.appendChild(name);
    line.appendChild(amount);
    main.appendChild(line);

    const meta = document.createElement('div');
    meta.className = 'import-meta';
    const when = document.createElement('span');
    when.className = 'import-date';
    when.textContent = shortDate(row.date);
    meta.appendChild(when);
    if (importable) meta.appendChild(categorySelect(row));
    main.appendChild(meta);

    const text = note(row);
    if (text) {
        const p = document.createElement('p');
        p.className = 'import-note' + (row.status === 'maybe' ? ' is-warn' : '');
        p.textContent = text;
        main.appendChild(p);
    }

    // Standing order (Matan, 1.11): make it a recurring transaction, ticked in
    // advance, because that is exactly what a standing order is. Whoever already
    // has a series for the same business is only told.

    if (importable && row.recurring_offer) {
        const label = document.createElement('label');
        label.className = 'import-recurring';
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = row.recurring !== false;
        row.recurring = box.checked;
        box.addEventListener('change', function () { row.recurring = box.checked; });
        const span = document.createElement('span');
        span.textContent = 'הוראת קבע · להוסיף כעסקה קבועה חודשית';
        label.appendChild(box);
        label.appendChild(span);
        main.appendChild(label);
    } else if (importable && row.already_recurring) {
        const p = document.createElement('p');
        p.className = 'import-note';
        p.textContent = 'כבר מוגדרת כעסקה קבועה';
        main.appendChild(p);
    }
    li.appendChild(main);
    li.classList.toggle('is-off', importable && !row.checked);
    return li;
}

function showReview(res) {
    rows = res.transactions.map(function (t) {
        t.checked = t.status === 'new';
        return t;
    });
    listEl.textContent = '';
    existsEl.textContent = '';
    refundsEl.textContent = '';

    const main = rows.filter(function (r) { return r.status === 'new' || r.status === 'maybe'; });
    const exists = rows.filter(function (r) { return r.status === 'exists'; });
    const refunds = rows.filter(function (r) { return r.status === 'refund'; });
    main.forEach(function (r) { listEl.appendChild(rowElement(r, true)); });
    exists.forEach(function (r) { existsEl.appendChild(rowElement(r, true)); });
    refunds.forEach(function (r) { refundsEl.appendChild(rowElement(r, false)); });

    $('importExistsWrap').hidden = !exists.length;
    $('importExistsTitle').textContent = window.sfCount(exists.length, 'עסקה אחת כבר קיימת', 'עסקאות כבר קיימות') + ' — לא ייובאו';
    $('importRefundsWrap').hidden = !refunds.length;
    $('importRefundsTitle').textContent = window.sfCount(refunds.length, 'זיכוי אחד', 'זיכויים');

    const c = res.counts || {};
    const parts = [window.sfCount(c.new || 0, 'עסקה חדשה אחת', 'עסקאות חדשות')];
    if (c.maybe) parts.push(window.sfCount(c.maybe, 'אחת לבדיקה', 'לבדיקה'));
    if (c.exists) parts.push(c.exists + ' כבר קיימות');
    $('importSummary').textContent = parts.join(' · ');
    if (!main.length) {
        const empty = document.createElement('li');
        empty.className = 'chart-empty';
        empty.textContent = 'אין עסקאות חדשות בקובץ — הכל כבר קיים';
        listEl.appendChild(empty);
    }

    commitErr.textContent = '';
    show('review');
    refresh();
}

function selected() {
    return rows.filter(function (r) { return r.checked && r.status !== 'refund'; });
}

function refresh() {
    const chosen = selected();
    const total = chosen.reduce(function (s, r) { return s + r.amount; }, 0);
    commitBtn.disabled = !chosen.length;
    commitBtn.textContent = chosen.length
        ? 'ייבוא ' + window.sfCount(chosen.length, 'עסקה אחת', 'עסקאות') + ' · ₪' + window.sfMoney(total)
        : 'לא נבחרו עסקאות';
}

function setAll(on) {
    rows.forEach(function (r) {
        if (!r._check || r.status === 'exists') return;
        r.checked = on;
        r._check.checked = on;
        r._li.classList.toggle('is-off', !on);
        r._li.classList.toggle('needs-cat', on && !r.category_id);
    });
    refresh();
}
$('importAll').addEventListener('click', function () { setAll(true); });
$('importNone').addEventListener('click', function () { setAll(false); });

commitBtn.addEventListener('click', function () {
    commitErr.textContent = '';
    const chosen = selected();
    const missing = chosen.filter(function (r) { return !r.category_id; });
    if (missing.length) {
        missing.forEach(function (r) { r._li.classList.add('needs-cat'); });
        commitErr.textContent = 'חסרה קטגוריה ל' + window.sfCount(missing.length, 'עסקה אחת', 'עסקאות') + ' — נא לבחור קטגוריה, או להוריד את הסימון';
        missing[0]._li.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
    }
    const body = {
        transactions: chosen.map(function (r) {
            return { date: r.date, amount: r.amount, description: r.description, category_id: r.category_id,
                     recurring: !!(r.recurring_offer && r.recurring) };
        }),
    };
    const owner = $('importOwner');
    if (owner) body.owner = owner.value;

    commitBtn.disabled = true;
    commitBtn.textContent = 'מייבא…';
    fetch('/api/import/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
        .then(function (r) { return r.json().catch(function () { return { error: 'הייבוא נכשל — נסו שוב' }; }); })
        .then(function (res) {
            if (res.error) { commitErr.textContent = res.error; refresh(); return; }
            $('importDoneTitle').textContent = window.sfCount(res.created, 'עסקה אחת נוספה', 'עסקאות נוספו') +
                (res.recurring ? ' · ' + window.sfCount(res.recurring, 'עסקה קבועה אחת', 'עסקאות קבועות') : '');
            // The month of most transactions: where "to the month" leads

            const months = {};
            chosen.forEach(function (r) { const k = r.date.slice(0, 7); months[k] = (months[k] || 0) + 1; });
            const top = Object.keys(months).sort(function (a, b) { return months[b] - months[a]; })[0];
            if (top) {
                const link = $('importDoneMonth');
                link.href = link.pathname + '?year=' + parseInt(top.slice(0, 4), 10) + '&month=' + parseInt(top.slice(5, 7), 10);
            }
            show('done');
        })
        .catch(function () { commitErr.textContent = window.sfNetError(); refresh(); });
});
})();
