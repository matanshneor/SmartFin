const SF_VIEW = window.sfData('sf-view-data');


// Centring the current month in the strip. The "magnet" itself is all CSS; here
// we only place the starting point. Also after a soft refresh (returning to the
// app, adding a transaction): it replaces the hero with a new strip that sits at
// the start; Matan (1.10) saw July-August when the current month was October.

(function () {
function centerStrip() {
    const strip = document.getElementById('monthStrip');
    if (!strip) return;
    const active = strip.querySelector('.month-chip.is-current');
    if (!active) return;
    // No animation: a jump to smooth scrolling on page load looks like a glitch

    const prev = strip.style.scrollBehavior;
    strip.style.scrollBehavior = 'auto';
    strip.scrollLeft += active.getBoundingClientRect().left + active.offsetWidth / 2
                      - (strip.getBoundingClientRect().left + strip.offsetWidth / 2);
    strip.style.scrollBehavior = prev;
}
centerStrip();
// Again once Rubik has loaded: the chips were measured in the fallback font, and
// the real one changes their widths, so the strip settled a few pixels off, and
// sometimes snapped to the oldest month (design pass, 11.11).
if (document.fonts && document.fonts.ready) document.fonts.ready.then(centerStrip);
window.addEventListener('sf:refreshed', centerStrip);
})();


/* ═══ The page's interactivity ═══
 *
 * A block separate from the charts, and before them, on purpose: everything
 * here must work even without Chart.js. Until now it all sat together, and the
 * first chart line took down the rest, including in an empty month, where the
 * template doesn't even load the library.
 */
(function () {

// ── Expanding a category: showing all its transactions in the month ──

/* One category open at a time (Matan, 13.10): opening one closes the others
 * immediately, with no motion, and without the tapped row jumping on screen as
 * what is above it shrinks. */
function closeOtherCategories(trigger, wrap) {
    const before = wrap.getBoundingClientRect().top;
    document.querySelectorAll('.legend-item.clickable[aria-expanded="true"]').forEach(function (other) {
        if (other === trigger) return;
        other.setAttribute('aria-expanded', 'false');
        const w = other.closest('.legend-item-wrap');
        if (!w) return;
        const list = w.querySelector('.cat-tx-list');
        if (list && list.sfAnim) { list.sfAnim.cancel(); list.sfAnim = null; list.style.overflow = ''; }
        w.classList.remove('open');
    });
    const shift = wrap.getBoundingClientRect().top - before;
    if (shift) window.scrollBy(0, shift);
}

function toggleExpand(trigger) {
    const wrap = trigger.closest('.legend-item-wrap');
    if (!wrap) return;
    // State by `aria-expanded` and not the class: on close the class stays
    // until the slide ends

    const isOpen = trigger.getAttribute('aria-expanded') !== 'true';
    if (isOpen) closeOtherCategories(trigger, wrap);
    trigger.setAttribute('aria-expanded', isOpen);
    const list = wrap.querySelector('.cat-tx-list');
    const apply = function (open) { wrap.classList.toggle('open', open); };
    if (list && window.sfReveal) window.sfReveal(list, isOpen, apply); else apply(isOpen);
}

document.addEventListener('click', function (e) {
    const trigger = e.target.closest('.legend-item.clickable');
    if (trigger) toggleExpand(trigger);
});

document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const trigger = e.target.closest('.legend-item.clickable');
    if (trigger) { e.preventDefault(); toggleExpand(trigger); }
    // `.zero-toggle` is marked `role="button" tabindex="0"`, but was handled on
    // `click` only, and an element that isn't a `<button>` doesn't produce a
    // click from Enter. Three Tab stops on every month page announced themselves
    // as buttons and did nothing, and keyboard users simply couldn't reach the
    // hidden categories.

    const zero = e.target.closest('.zero-toggle');
    if (zero) { e.preventDefault(); zero.click(); }
});

// ── Showing/hiding categories with no activity ──

document.addEventListener('click', function (e) {
    const toggle = e.target.closest('.zero-toggle');
    if (!toggle) return;
    const shown = toggle.parentElement.classList.toggle('show-zeros');
    toggle.classList.toggle('open', shown);
    // A screen reader doesn't see the arrow flip: it needs to hear
    // "expanded"/"collapsed"

    toggle.setAttribute('aria-expanded', String(shown));
});


// ── From the tiles (and the home cards) to the breakdown: scroll and flash ──
// `:target` doesn't update on `replaceState`, so the flash is a class. Arriving
// from home is ordinary navigation with `#`: the browser scrolls by itself, and
// only the flash is added here.

const BREAKDOWNS = ['income-breakdown', 'expense-breakdown', 'savings-breakdown'];

function flashBreakdown(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    el.classList.remove('flash');
    void el.offsetWidth;                  // restarts the animation on a repeated tap too
    el.classList.add('flash');
    return el;
}

document.addEventListener('click', function (e) {
    const link = e.target.closest('a.kpi-link');
    if (!link) return;
    const id = (link.getAttribute('href') || '').slice(1);
    const el = flashBreakdown(id);
    if (!el) return;                      // no breakdown: the link behaves as usual
    e.preventDefault();
    const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' });
    try { history.replaceState(null, '', '#' + id); } catch (err) { /* not critical */ }
});

// Guarding `location`: a file-level line that throws takes down all of
// month.js: charts, legend, opening categories (exactly how core.js once fell
// over sessionStorage)

try {
    const arrived = window.location.hash.slice(1);
    if (BREAKDOWNS.includes(arrived)) flashBreakdown(arrived);
    else if (arrived.indexOf('cat-') === 0) showCategory(arrived);
} catch (err) { /* without the flash, not without the page */ }

// ── From a home alert to its target (Matan, 11.10) ──
// An alert about a category (`#cat-<id>`) opens the category in the expense
// breakdown, with its transactions, and flashes it. Arriving from home is
// ordinary navigation with `#`.
// A category not found (deleted meanwhile) falls back to the whole expense
// breakdown.

function showCategory(id) {
    const wrap = document.getElementById(id);
    if (!wrap) {
        const box = flashBreakdown('expense-breakdown');
        if (box) box.scrollIntoView({ behavior: 'auto', block: 'start' });
        return box;
    }
    const trigger = wrap.querySelector('.legend-item.clickable');
    if (trigger && trigger.getAttribute('aria-expanded') !== 'true') toggleExpand(trigger);
    wrap.classList.remove('flash');
    void wrap.offsetWidth;
    wrap.classList.add('flash');
    wrap.addEventListener('animationend', function () { wrap.classList.remove('flash'); }, { once: true });
    return wrap;
}

// ── Search/filter in the "all transactions" list (client-side) ──
//
// Delegated from document rather than listening directly, and without storing
// the rows upfront: a soft refresh replaces the whole `main`, and every
// reference captured on load then points at detached elements. The search just
// stopped responding, with no sign at all.
// Search and the type filter (Matan, 30.9) work together: a row is shown only
// if it matches both. The active type is read from the button itself and not
// kept in a variable, for the same reason: after a soft refresh the buttons are
// new, and it goes back to "all".

function filterAllTx() {
    const rows = document.querySelectorAll('#txScreen .tx-screen-list .cat-tx-row');
    const input = document.getElementById('txSearch');
    const emptyMsg = document.getElementById('txSearchEmpty');
    const count = document.querySelector('.all-tx-count');
    const activeChip = document.querySelector('.tx-type-chip.active');
    const type = activeChip ? activeChip.dataset.type : '';
    // The categories chosen in the open row (empty = "all expenses")

    const cats = new Set();
    document.querySelectorAll('.tx-cat-chips:not([hidden]) .tx-cat-chip.active').forEach(function (c) {
        if (c.dataset.cat) cats.add(c.dataset.cat);
    });
    const q = input ? input.value.trim().toLowerCase() : '';
    let shown = 0, sum = 0;
    rows.forEach(function (row) {
        // The category and project name too (Matan, 5.10): "סופר" finds "רמי לוי"

        const desc = ((row.querySelector('.cat-tx-desc') || {}).textContent || '')
                   + ' ' + (row.dataset.search || '');
        const match = (!q || desc.toLowerCase().indexOf(q) !== -1)
                   && (!type || row.dataset.type === type)
                   && (!cats.size || cats.has(row.dataset.catKey));
        row.style.display = match ? '' : 'none';
        if (match) { shown++; sum += parseFloat(row.dataset.amount) || 0; }
    });
    const filtering = q || type || cats.size;
    txState = { q: input ? input.value : '', type: type, cats: Array.from(cats) };
    if (emptyMsg) emptyMsg.style.display = (filtering && shown === 0) ? 'block' : 'none';
    // When categories are chosen, also how much went on them ("5 · ₪1,070")

    if (count) count.textContent = !filtering ? count.dataset.total
        : (cats.size ? shown + ' · ₪' + window.sfMoney(Math.round(sum)) : shown);
}

// ── The all-transactions screen (Matan, 5.10) ──
// The preview shows the last 5; the full list, with search and filter, on its
// own screen. `#all-tx` in the URL: the phone's back button closes the screen
// and doesn't leave the month.
// Editing from the screen refreshes `main` (soft refresh), and the new screen
// arrives closed, with an empty search. So it is reopened with what was in it
// (`txState`).

let txState = null;

// ── Sorting "all transactions": date / amount (Matan, 12.10) ──
// The original order (by date, from the server) is stored on each row the first
// time, and returned to. Filtering and search keep working: they hide rows,
// they don't move them.

function sortAllTx(by) {
    const list = document.querySelector('#txScreen .tx-screen-list');
    if (!list) return;
    const rows = Array.prototype.slice.call(list.querySelectorAll(':scope > .cat-tx-row'));
    rows.forEach(function (r, i) { if (r.dataset.order === undefined) r.dataset.order = i; });
    rows.sort(by === 'amount'
        ? function (a, b) { return Number(b.dataset.amount) - Number(a.dataset.amount) || a.dataset.order - b.dataset.order; }
        : function (a, b) { return a.dataset.order - b.dataset.order; });
    rows.forEach(function (r) { list.appendChild(r); });
    document.querySelectorAll('#txScreen .tx-sort-chip').forEach(function (c) {
        const on = c.dataset.sort === by;
        c.classList.toggle('active', on);
        c.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
}
document.addEventListener('click', function (e) {
    const chip = e.target.closest('#txScreen .tx-sort-chip');
    if (chip) sortAllTx(chip.dataset.sort);
});

function txScreen() { return document.getElementById('txScreen'); }

// The screen moves to `body` at the moment it opens. Inside the card,
// `position: fixed` doesn't cover the screen: the cards have an entrance
// animation with `transform`, which makes them the containing block. The screen
// was trapped inside the card, and the month's sticky header sat above it.

function openTxScreen(push) {
    const screen = txScreen();
    if (!screen) return;
    if (screen.parentElement !== document.body) document.body.appendChild(screen);
    screen.hidden = false;
    document.body.classList.add('tx-screen-open');
    // Focus into the screen, or keyboard and VoiceOver stayed on the button now
    // hidden behind it (design pass, 11.11)
    const back = document.getElementById('txScreenClose');
    if (back) back.focus({ preventScroll: true });
    if (push) {
        try { history.pushState({ sfTxScreen: true }, '', '#all-tx'); } catch (e) { /* not critical */ }
    }
}

function hideTxScreen() {
    const screen = txScreen();
    const hadFocus = screen && screen.contains(document.activeElement);
    if (screen) screen.hidden = true;
    document.body.classList.remove('tx-screen-open');
    const opener = document.getElementById('txScreenOpen');
    if (hadFocus && opener) opener.focus({ preventScroll: true });
    txState = null;
    sortAllTx('date');                  // the sort isn't kept: next time it opens by date again
}

function closeTxScreen() {
    if (history.state && history.state.sfTxScreen) { history.back(); return; }   // popstate hides it
    hideTxScreen();
    try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { /* not critical */ }
}

function restoreTxState() {
    if (!txState) return;
    const input = document.getElementById('txSearch');
    if (input) input.value = txState.q;
    const typeChip = document.querySelector('.tx-type-chip[data-type="' + txState.type + '"]');
    if (typeChip) {
        document.querySelectorAll('.tx-type-chip').forEach(function (c) { pressChip(c, c === typeChip); });
        document.querySelectorAll('.tx-cat-chips').forEach(function (rowEl) {
            rowEl.hidden = rowEl.dataset.forType !== txState.type;
        });
    }
    const group = document.querySelector('.tx-cat-chips:not([hidden])');
    if (group && txState.cats.length) {
        group.querySelectorAll('.tx-cat-chip').forEach(function (c) {
            pressChip(c, c.dataset.cat ? txState.cats.indexOf(c.dataset.cat) !== -1 : false);
        });
    }
    filterAllTx();
}

document.addEventListener('click', function (e) {
    if (!e.target.closest) return;
    if (e.target.closest('#txScreenOpen')) openTxScreen(true);
    else if (e.target.closest('#txScreenClose')) closeTxScreen();
});
document.addEventListener('keydown', function (e) {
    const screen = txScreen();
    if (e.key === 'Escape' && !e.defaultPrevented && screen && !screen.hidden && !document.querySelector('.modal-overlay.open')) closeTxScreen();
});
window.addEventListener('popstate', function () {
    if (location.hash === '#all-tx') openTxScreen(false); else hideTxScreen();
});
window.addEventListener('sf:refreshed', function () {
    // The refresh brought a new screen inside `main`; the old copy moved to
    // `body` is redundant

    document.querySelectorAll('body > #txScreen').forEach(function (old) {
        if (old !== document.querySelector('main #txScreen')) old.remove();
    });
    if (location.hash !== '#all-tx') return;
    const keep = txState;
    openTxScreen(false);
    txState = keep;
    restoreTxState();
});
try {
    if (location.hash === '#all-tx') openTxScreen(false);
} catch (err) { /* without auto-open, not without the page */ }

function pressChip(chip, on) {
    chip.classList.toggle('active', on);
    chip.setAttribute('aria-pressed', on ? 'true' : 'false');
}

document.addEventListener('input', function (e) {
    if (e.target && e.target.id === 'txSearch') filterAllTx();
});

document.addEventListener('click', function (e) {
    if (!e.target.closest) return;
    const typeChip = e.target.closest('.tx-type-chip');
    const catChip = e.target.closest('.tx-cat-chip');
    if (typeChip) {
        // A new type: open its category row, and every row goes back to "all …"

        document.querySelectorAll('.tx-type-chip').forEach(function (c) { pressChip(c, c === typeChip); });
        document.querySelectorAll('.tx-cat-chips').forEach(function (rowEl) {
            rowEl.hidden = rowEl.dataset.forType !== typeChip.dataset.type;
            rowEl.querySelectorAll('.tx-cat-chip').forEach(function (c) { pressChip(c, !c.dataset.cat); });
        });
    } else if (catChip) {
        const group = catChip.closest('.tx-cat-chips');
        const allChip = group.querySelector('.tx-cat-chip[data-cat=""]');
        if (!catChip.dataset.cat) {
            // "All expenses": clears every selection

            group.querySelectorAll('.tx-cat-chip').forEach(function (c) { pressChip(c, c === allChip); });
        } else {
            // A category: a tap adds, another tap removes

            pressChip(catChip, !catChip.classList.contains('active'));
            const any = group.querySelector('.tx-cat-chip.active:not([data-cat=""])');
            pressChip(allChip, !any);
        }
    } else {
        return;
    }
    filterAllTx();
});

})();


/* ═══ The charts ═══  (see chart-setup.js)
 *
 * Redrawn after a soft refresh. `softReload` replaces the whole `main`, i.e.
 * every canvas is replaced, and the old Chart.js instances stayed attached to
 * detached elements while the new ones were never drawn. The result on screen
 * was centre labels floating over empty squares, which looks like the page
 * crashed.
 */
(function () {
if (!window.sfCharts.ready) return;

const COLORS    = window.sfCharts.colors;

let drawn = [];
const calmMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* Tapping a slice (Matan, 12.10): the same row under the ring opens with its
 * transactions, flashes and scrolls to the middle of the screen. A project:
 * opens its page. The order of the slices is the order of the rows (the same
 * list from the server). */
function openSlice(cardId, els) {
    if (!els || !els.length) return;
    const card = document.getElementById(cardId);
    if (!card) return;
    const wrap = card.querySelectorAll('.legend-list > .legend-item-wrap')[els[0].index];
    if (!wrap) return;
    const trigger = wrap.querySelector('.legend-item.clickable');
    if (trigger && trigger.tagName === 'A') { window.location.href = trigger.href; return; }
    if (trigger && trigger.getAttribute('aria-expanded') !== 'true') trigger.click();   // toggleExpand is in another block; not defined here
    wrap.classList.remove('flash');
    void wrap.offsetWidth;
    wrap.classList.add('flash');
    wrap.addEventListener('animationend', function () { wrap.classList.remove('flash'); }, { once: true });
    wrap.scrollIntoView({ behavior: calmMotion ? 'auto' : 'smooth', block: 'center' });
}

function paint() {
// Previous instances are destroyed before drawing: `new Chart` on a canvas in
// use throws.

drawn.forEach(function (c) { try { c.destroy(); } catch (e) {} });
drawn = [];

// The display data comes from data islands in the HTML, and that HTML was
// replaced, so it is read again rather than relying on what was read on load.

const view = window.sfData('sf-view-data');
const SF_VIEW = view;

// ── 2. Expenses by category ──
// A fixed colour per category (`color_idx` from the server: its place in the
// category list, Matan 12.10), and when switching months the ring "flows": it
// is created with the previous month's values per category, then updated to the
// new ones, so the slices grow and shrink instead of being drawn from zero.

const expenseData = SF_VIEW.expense;
if (expenseData.length > 0) {
    const ctx = document.getElementById('expenseChart');
    if (ctx) {
        const keyOf = d => d.category_id || d.project_id || d.name;
        const target = expenseData.map(d => d.total);
        const prev = window.__sfLastDonut;
        const morph = !!prev && !calmMotion;
        const start = morph ? expenseData.map(d => prev[keyOf(d)] || 0) : target;
        const chart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: expenseData.map(d => d.name),
                datasets: [{
                    data:            start,
                    backgroundColor: expenseData.map((d, i) => COLORS[(d.color_idx != null ? d.color_idx : i) % COLORS.length]),
                    borderColor:     window.sfCharts.surface,
                    borderWidth:     2,
                    borderRadius:    4,
                    spacing:         2,
                    hoverOffset:     6,
                }]
            },
            options: {
                cutout: '70%',
                animation: morph ? false : undefined,
                // Instead of the black tooltip, the tap opens the category's row
                // (Matan, 12.10)

                plugins: { tooltip: { enabled: false } },
                onClick: (evt, els) => openSlice('expense-breakdown', els),
            }
        });
        drawn.push(chart);
        if (morph) {
            chart.options.animation = { duration: 650, easing: 'easeOutCubic' };
            chart.data.datasets[0].data = target;
            chart.update();
        }
        const remember = {};
        expenseData.forEach(d => { remember[keyOf(d)] = d.total; });
        window.__sfLastDonut = remember;
    }
}

// ── 4. Income: the same ring as expenses (Matan, 30.9). Savings has no chart ──
// The colours come from the server: the family member, or grey for "other income".

[['incomeChart', SF_VIEW.income || [], 'color']]
.forEach(function (spec) {
    const ctx = document.getElementById(spec[0]);
    const data = spec[1];
    if (!ctx || !data.length) return;
    drawn.push(new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: data.map(d => d.name),
            datasets: [{
                data:            data.map(d => d.total),
                backgroundColor: spec[2] ? data.map(d => d[spec[2]]) : COLORS.slice(0, data.length),
                borderColor:     window.sfCharts.surface,
                borderWidth:     2,
                borderRadius:    4,
                spacing:         2,
                hoverOffset:     6,
            }]
        },
        options: {
            cutout: '70%',
            plugins: { tooltip: { enabled: false } },
            onClick: (evt, els) => openSlice('income-breakdown', els),
        }
    }));
});

// ── 5. Split between family members: rows in the template now, not a chart
// (design pass, 11.11) ──

}   // paint

paint();

// A soft refresh replaced `main`: the canvases we drew on are no longer in the document.

window.addEventListener('sf:refreshed', paint);

})();



/* ═══ A header that stays at the top while scrolling (Matan, 30.9, round 6,
 * item 12) ═══
 *
 * When the monthly balance leaves the screen, a thin row appears at the top: the
 * month name and the balance. Tapping it returns to the top of the page. Rebuilt
 * after a soft refresh, so the values are fresh. The amount is read from
 * `data-countup` and not the text: mid count-up the text is an intermediate
 * number. */
(function () {
    let bar = null, observer = null;

    function build() {
        if (observer) { observer.disconnect(); observer = null; }
        const net = document.querySelector('.month-net');
        const title = document.querySelector('.hero-title');
        if (!net || !title || !('IntersectionObserver' in window)) { if (bar) bar.hidden = true; return; }
        if (!bar) {
            bar = document.createElement('button');
            bar.type = 'button';
            bar.className = 'month-sticky';
            bar.hidden = true;
            bar.setAttribute('aria-label', 'חזרה לראש החודש');
            bar.addEventListener('click', function () {
                const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
                window.scrollTo({ top: 0, behavior: calm ? 'auto' : 'smooth' });
            });
            document.body.appendChild(bar);
        }
        const valueEl = net.querySelector('.month-net-value');
        let value = valueEl ? valueEl.textContent.trim() : '';
        if (valueEl && valueEl.dataset.countup) {
            value = (valueEl.dataset.prefix || '') + '₪' + window.sfMoney(valueEl.dataset.countup);
        }
        bar.innerHTML = '';
        const t = document.createElement('span');
        t.className = 'month-sticky-title';
        t.textContent = title.textContent.trim();
        const v = document.createElement('span');
        v.className = 'month-sticky-value' + (net.classList.contains('deficit') ? ' deficit'
                                            : net.classList.contains('surplus') ? ' surplus' : '');
        v.textContent = 'מאזן ' + value;
        bar.append(t, v);
        observer = new IntersectionObserver(function (entries) {
            // Appears only when the balance left **upwards**, not when it
            // hasn't reached the screen yet

            const e = entries[0];
            bar.hidden = e.isIntersecting || e.boundingClientRect.top > 0;
        });
        observer.observe(net);
    }
    build();
    window.addEventListener('sf:refreshed', build);
})();


/* ═══ Setting budgets: one sheet for all categories (Matan, 30.9) ═══
 *
 * Instead of "set a budget for the category" after every row, which sent you to
 * settings. Every expense category with how much went on it this month and a
 * field for the monthly budget; empty = no budget. One save sends only what
 * changed. The data is read on **open** (`sf-budgets` inside `main`), so after a
 * soft refresh it is already the new data. */
(function () {
    let sheet = null;

    function budgetMoney(v) { return '₪' + window.sfMoney(v); }

    function buildBudgets(rows) {
        if (sheet) sheet.remove();
        sheet = document.createElement('div');
        sheet.className = 'color-sheet budget-sheet';
        sheet.setAttribute('role', 'dialog');
        sheet.setAttribute('aria-modal', 'true');
        sheet.setAttribute('aria-labelledby', 'budgetSheetTitle');
        const card = document.createElement('div');
        card.className = 'color-sheet-card budget-sheet-card';
        card.innerHTML = '<p class="color-sheet-title" id="budgetSheetTitle">תקציב חודשי לכל קטגוריה</p>'
            + '<p class="color-sheet-hint">סכום לחודש. שדה ריק — בלי תקציב לקטגוריה.</p>';
        const list = document.createElement('div');
        list.className = 'budget-sheet-list';
        rows.forEach(function (r) {
            const row = document.createElement('label');
            row.className = 'budget-sheet-row';
            const info = document.createElement('span');
            info.className = 'budget-sheet-info';
            const name = document.createElement('span');
            name.className = 'budget-sheet-name';
            name.textContent = (r.icon || '📦') + ' ' + r.name;
            const spent = document.createElement('span');
            spent.className = 'budget-sheet-spent';
            spent.textContent = 'החודש: ' + budgetMoney(r.total || 0);
            info.append(name, spent);
            const wrap = document.createElement('span');
            wrap.className = 'amount-input-wrap budget-sheet-amount';
            wrap.innerHTML = '<span class="amount-currency">₪</span>';
            const input = document.createElement('input');
            input.className = 'form-input amount-input';
            input.type = 'number';
            input.inputMode = 'numeric';
            input.min = '0';
            input.step = '1';
            input.placeholder = 'אין';
            input.value = r.budget ? Math.round(r.budget) : '';
            input.defaultValue = input.value;               // "save" is disabled until something changes (Matan, 13.10)
            input.dataset.cat = r.category_id;
            input.dataset.old = r.budget ? String(Math.round(r.budget)) : '';
            input.dataset.alert = r.budget_alert === false ? 'false' : 'true';
            input.setAttribute('aria-label', 'תקציב חודשי ל' + r.name);
            wrap.appendChild(input);
            row.append(info, wrap);
            list.appendChild(row);
        });
        card.appendChild(list);
        const err = document.createElement('p');
        err.className = 'form-error';
        err.setAttribute('role', 'alert');
        card.appendChild(err);
        const actions = document.createElement('div');
        actions.className = 'edit-actions budget-sheet-actions';
        actions.innerHTML = '<button type="button" class="btn-sm btn-primary" data-save>שמירה</button>'
                          + '<button type="button" class="btn-sm btn-ghost" data-cancel>ביטול</button>';
        card.appendChild(actions);
        if (window.sfSaveWhenChanged) window.sfSaveWhenChanged(card, actions.querySelector('[data-save]'));
        sheet.appendChild(card);
        document.body.appendChild(sheet);

        sheet.addEventListener('click', function (e) {
            if (e.target === sheet || e.target.closest('[data-cancel]')) { closeBudgets(); return; }
            if (!e.target.closest('[data-save]')) return;
            const limits = {};
            let bad = false;
            sheet.querySelectorAll('input[data-cat]').forEach(function (inp) {
                const v = inp.value.trim();
                if (v === inp.dataset.old) return;
                if (v === '' || Number(v) === 0) { limits[inp.dataset.cat] = null; return; }
                const n = Number(v);
                if (!isFinite(n) || n < 0) { bad = true; inp.setAttribute('aria-invalid', 'true'); return; }
                limits[inp.dataset.cat] = { amount: Math.round(n), alert: inp.dataset.alert !== 'false' };
            });
            if (bad) { err.textContent = 'נא להזין סכומים חיוביים בלבד'; return; }
            if (!Object.keys(limits).length) { closeBudgets(); return; }
            const save = e.target.closest('[data-save]');
            save.disabled = true;
            save.textContent = 'שומר…';
            fetch('/api/family/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ limits: limits }),
            })
                .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; }); })
                .then(function (res) {
                    if (!res.ok) {
                        err.textContent = res.d.error || 'שמירת התקציבים נכשלה — נסו שוב';
                        save.disabled = false; save.textContent = 'שמירה';
                        return;
                    }
                    closeBudgets();
                    if (window.sfForgetCategories) window.sfForgetCategories();
                    window.softReload(null, 'התקציבים נשמרו').then(function (how) {
                        if (how !== 'reloaded') window.showToast('התקציבים נשמרו');
                    });
                })
                .catch(function () {
                    err.textContent = window.sfNetError();
                    save.disabled = false; save.textContent = 'שמירה';
                });
        });
    }

    function closeBudgets() {
        // First `hidden`: the sheet slides down the same path it came up
        // (style.css, `.color-sheet`), and only after the motion leaves the page.
        // `sheet` is reset right away: a new open builds a new sheet.

        if (sheet) {
            const gone = sheet;
            sheet = null;
            gone.hidden = true;
            setTimeout(function () { gone.remove(); }, 300);
        }
        const btn = document.getElementById('budgetsOpen');
        if (btn) btn.focus({ preventScroll: true });
    }

    document.addEventListener('click', function (e) {
        if (!e.target.closest || !e.target.closest('#budgetsOpen')) return;
        let rows = [];
        try { rows = JSON.parse((document.getElementById('sf-budgets') || {}).textContent || '[]'); }
        catch (err) { rows = []; }
        if (!rows.length) return;
        buildBudgets(rows);
        const first = sheet.querySelector('input');
        if (first) first.focus({ preventScroll: true });
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && sheet) closeBudgets();
    });
})();


/* ═══ Paging between months (Matan, 3.10, idea 35; redone 11.10) ═══
 *
 * A sideways swipe anywhere on the page pages a month. Until 11.10 this worked
 * only on the balance and the tiles, and on the rest of the page the phone read
 * the swipe as "back" (Matan: "it goes back to the home page"). And the switch
 * was a full page load, not smooth.
 *
 * Now the content moves with the finger; on release the current month leaves
 * to one side and the new one enters from the other, with no page load
 * (`window.softNavigate`). The new month is fetched as soon as the swipe starts,
 * so on release it is usually already here. The arrows and the month strip use
 * the same animation too.
 *
 * As in the "this week" card: time flows right to left, so dragging left = the
 * previous month, right = the next. No swiping from what has its own swipe:
 * transaction rows (edit and delete), the month strip and category rows that
 * scroll sideways. A touch right at the edge of the screen stays the phone's
 * (the system's "back"). */
(function () {
    // What moves with the finger: the top of the page without the dark
    // background, and all the content

    const SLIDE = 'header.page-hero .hero-top, header.page-hero .month-strip, '
                + 'header.page-hero .month-net, header.page-hero .kpi-chips, main.main-content';
    const OWN_SWIPE = '.transaction-item, .cat-tx-row, .recurring-row, .month-strip, .tx-cat-chips, '
                    + '.range-chips, input, textarea, select, button.swipe-action, .tx-screen';
    const OPEN_WINDOWS = '.modal-overlay.open, .confirm-overlay.open, .search-screen:not([hidden]), '
                       + '.icon-picker:not([hidden]), .color-sheet:not([hidden]), .tx-screen.open';
    const EDGE = 22;          // near the edge: the system's swipe
    const GO_DIST = 60;       // where the motion is aimed (distance + momentum); past this, switch month
    const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let sx = null, sy = 0, axis = null, pending = null, busy = false, samples = [];
    let x = 0, stopSpring = null;

    function parts() { return Array.prototype.slice.call(document.querySelectorAll(SLIDE)); }
    function link(isOlder) {
        return document.querySelector('.stats-nav-btn[aria-label="' + (isOlder ? 'חודש קודם' : 'חודש הבא') + '"]');
    }
    // The content `v` away from its place, fading slightly as it moves further:
    // a hint that it is about to switch

    function place(v) {
        x = v;
        const fade = v ? String(Math.max(0.6, 1 - Math.abs(v) / (window.innerWidth * 1.6))) : '';
        parts().forEach(function (el) {
            el.style.transition = '';
            el.style.transform = v ? 'translateX(' + v + 'px)' : '';
            el.style.opacity = fade;
        });
    }
    function animate(els, frames, ms, easing) {
        if (calm || !els.length || !els[0].animate) return Promise.resolve();
        return Promise.all(els.map(function (el) {
            return el.animate(frames, { duration: ms, easing: easing, fill: 'forwards' }).finished
                .catch(function () {});
        }));
    }
    function prefetch(url) {
        if (!pending || pending.url !== url) {
            pending = { url: url, html: window.sfFetchPage(url).catch(function () { return null; }) };
        }
        return pending.html;
    }
    // Back into place on a spring that continues the finger's velocity (like
    // the transaction rows and the sheet)

    function settle(v) {
        if (stopSpring) { stopSpring(); stopSpring = null; }
        if (calm || !x) { place(0); return; }
        stopSpring = window.sfSpring(x, 0, v || 0, 0.32, 0.86, place,
                                     function () { stopSpring = null; place(0); });
    }

    /* The switch itself: from the swipe, the arrows and the strip. `fromX`/`v`:
     * where the content stands and how fast the finger left: the exit
     * continues the motion seamlessly. */
    function go(url, isOlder, fromX, v) {
        if (busy) return;
        busy = true;
        if (stopSpring) { stopSpring(); stopSpring = null; }
        const dir = isOlder ? -1 : 1;           // left = previous (time flows right to left)
        const w = window.innerWidth;
        const html = prefetch(url);
        const out = parts();
        const from = fromX || 0;
        const speed = Math.max(Math.abs(v || 0), 1400);                         // px/s
        const ms = Math.max(110, Math.min(220, (w * 0.6 - Math.abs(from)) / speed * 1000));
        const fromOpacity = out[0] ? (parseFloat(out[0].style.opacity) || 1) : 1;
        out.forEach(function (el) { el.style.transform = ''; el.style.opacity = ''; });
        x = 0;
        animate(out, [{ transform: 'translateX(' + from + 'px)', opacity: fromOpacity },
                      { transform: 'translateX(' + (dir * w * 0.6) + 'px)', opacity: 0 }],
                ms, 'cubic-bezier(0.3, 0, 0.8, 0.6)')
            .then(function () { return html; })
            .then(function (text) {
                window.scrollTo(0, 0);
                return window.softNavigate(url, text || undefined);
            })
            .then(function (result) {
                if (result !== 'swapped') return;
                const inn = parts();
                return animate(inn, [{ transform: 'translateX(' + (-dir * w * 0.35) + 'px)', opacity: 0 },
                                     { transform: 'translateX(0)', opacity: 1 }],
                               300, 'cubic-bezier(0.22, 1, 0.36, 1)')
                    .then(function () {
                        inn.forEach(function (el) {
                            if (el.getAnimations) el.getAnimations().forEach(function (a) { a.cancel(); });
                        });
                    });
            })
            .then(function () { busy = false; pending = null; },
                  function () { busy = false; pending = null; });
    }
    // For tests and for anyone who wants to page from code

    window.sfGoMonth = go;

    document.addEventListener('touchstart', function (e) {
        sx = null;
        if (busy || e.touches.length !== 1) return;
        const t = e.touches[0];
        if (t.clientX < EDGE || t.clientX > window.innerWidth - EDGE) return;
        if (e.target.closest && e.target.closest(OWN_SWIPE)) return;
        if (document.querySelector(OPEN_WINDOWS)) return;
        if (!document.querySelector('.stats-nav-btn')) return;   // not on the month page
        if (stopSpring) { stopSpring(); stopSpring = null; }   // caught in the middle of returning
        sx = t.clientX - x; sy = t.clientY; axis = x ? 'x' : null;
        samples = [{ t: e.timeStamp, p: t.clientX }];
    }, { passive: true });

    document.addEventListener('touchmove', function (e) {
        if (sx === null) return;
        const t = e.touches[0];
        const dx = t.clientX - sx, dy = t.clientY - sy;
        if (!axis) {
            if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
            axis = Math.abs(dx) > Math.abs(dy) * 1.2 ? 'x' : 'y';
            if (axis !== 'x') { sx = null; return; }
            sx += dx > 0 ? 8 : -8;              // without the 8-pixel jump of the decision
            const target = link(dx < 0);
            if (target) prefetch(target.href);
        }
        e.preventDefault();                     // the screen doesn't scroll while paging
        samples.push({ t: e.timeStamp, p: t.clientX });
        if (samples.length > 6) samples.shift();
        // One to one with the finger: in real time

        place(t.clientX - sx);
    }, { passive: false });

    document.addEventListener('touchend', function (e) {
        if (sx === null) return;
        sx = null;
        if (axis !== 'x') return;
        axis = null;
        // By **where the motion is heading** (like "this week"): a short fast
        // flick is enough, and a drag to one side that flicks back to the other
        // is changing your mind

        const v = window.sfVelocity(samples, e.timeStamp);
        const aim = x + window.sfProject(v, 0.99);
        const isOlder = aim < 0;
        const target = link(isOlder);
        if (target && Math.abs(aim) >= GO_DIST && (aim < 0) === (x < 0)) {
            go(target.href, isOlder, x, v);
            return;
        }
        settle(v);
    }, { passive: true });

    document.addEventListener('touchcancel', function () {
        if (sx !== null && axis === 'x') settle(0);
        sx = null; axis = null;
    }, { passive: true });

    // The arrows, the month strip and "back to the current month": the same
    // smooth switch. The direction is by the date in the URL: an earlier month
    // enters from the same side as in a swipe.

    function ym(href) {
        try {
            const u = new URL(href, location.href);
            const y = parseInt(u.searchParams.get('year'), 10), m = parseInt(u.searchParams.get('month'), 10);
            return (y && m) ? y * 12 + m : null;
        } catch (err) { return null; }
    }
    document.addEventListener('click', function (e) {
        const a = e.target.closest && e.target.closest('a.stats-nav-btn, a.month-chip, a.back-to-current');
        if (!a || e.metaKey || e.ctrlKey || e.shiftKey || busy) return;
        e.preventDefault();
        if (a.classList.contains('is-current')) return;          // the month already shown
        let isOlder;
        if (a.classList.contains('stats-nav-btn')) isOlder = a.getAttribute('aria-label') === 'חודש קודם';
        else if (a.classList.contains('back-to-current')) isOlder = a.dataset.older === 'true';
        else {
            // In the strip: against the month shown now (`/month` with no
            // parameters is the current one)

            const cur = document.querySelector('.month-chip.is-current');
            const here = cur ? ym(cur.href) : null, there = ym(a.href);
            isOlder = here !== null && there !== null && there < here;
        }
        go(a.href, isOlder);
    });

    // Going back with "back": the browser restores the page as it was,
    // including the offset

    window.addEventListener('pageshow', function () { place(0); busy = false; });
})();
