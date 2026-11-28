
/* ═══ The page's interactivity ═══
 * Before the charts and separate from them: opening a category must work even
 * without Chart.js. */
(function () {

// ── Expanding a category: showing all its transactions in the project (like
// the month page) ──

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
});

})();


/* ═══ The charts ═══  (see chart-setup.js)
 * Drawn from the server-built page's data block, or by the view below once its
 * data arrives. A chart drawn before is destroyed first: a redraw after a save
 * builds new cards, and Chart.js would keep the old ones alive. */
function sfProjectCharts(breakdown) {
    if (!window.sfCharts.ready) return;
    const COLORS = window.sfCharts.colors;
    ['expense', 'income', 'savings'].forEach(function (typ) {
        const items = breakdown[typ] || [];
        const ctx = document.getElementById('chart-' + typ);
        if (!ctx || !items.length) return;
        const before = Chart.getChart(ctx);
        if (before) before.destroy();

        new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: items.map(d => d.name),
                datasets: [{
                    data:            items.map(d => d.total),
                    backgroundColor: COLORS.slice(0, items.length),
                    borderColor:     window.sfCharts.surface,
                    borderWidth:     2,
                    borderRadius:    4,
                    spacing:         2,
                    hoverOffset:     6,
                }]
            },
            options: {
                cutout: '70%',
                plugins: {
                    tooltip: {
                        callbacks: {
                            label: c => ` ₪${window.sfMoney(c.parsed)} (${items[c.dataIndex].pct}%)`
                        }
                    }
                }
            }
        });
    });
}
if (!document.getElementById('projectView')) sfProjectCharts(window.sfData('sf-view-data').breakdown);

/* "Show all transactions": in its own block and not inside the charts: their
 * block returns early when there is no Chart.js, and the button died with it.
 * Delegated, because a soft refresh replaces it. */
(function () {
document.addEventListener('click', function (e) {
    const btn = e.target.closest('#showAllProjectTx');
    if (!btn) return;
    const list = document.getElementById('projectTxList');
    if (!list) return;
    const open = list.classList.toggle('show-all');
    btn.setAttribute('aria-expanded', String(open));
    btn.textContent = open ? btn.dataset.less : btn.dataset.more;
});
})();



/* ═══ The page that loads its own data (`project_detail_view.html`) ═══
 *
 * The page arrives with no project in it. This asks `view_url`
 * (`/api/screens/project/<id>`) and builds the hero, the cards and the rows
 * from the page's `<template>`s. Every amount and sentence arrives ready from
 * the server: which tiles a project has, a goal or a ceiling, the marks on a
 * row are decided there.
 *
 * Two moments ask for the data: the page opens, and something was saved
 * (`softReload`), when it is asked again and redrawn in place. A project that
 * is no longer there (404) sends the user to the list, as the server-built
 * page did; a failed load says so and offers another try.
 *
 * Everything is written with `textContent`: names and descriptions are the
 * user's own text. */
(function () {
const VIEW = 'projectView';
if (!document.getElementById(VIEW)) return;
const byId = id => document.getElementById(id);
const slot = (root, name) => root.querySelector('[data-slot="' + name + '"]');
const make = (tag, cls, text) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
};
const clone = id => byId(id).content.cloneNode(true);

function archiveButton(btn, view) {
    btn.dataset.archiveProject = view.id;
    btn.dataset.name = view.name;
}

function fillHero(view) {
    const hero = view.hero;
    document.title = view.title;
    const heading = byId('projectHeading');
    heading.textContent = hero.heading;
    if (hero.personal) heading.append(' ', clone('tplPersonalBadge'));
    byId('projectSubtitle').textContent = hero.subtitle;

    const pin = byId('projectPin');
    pin.hidden = !hero.pin;
    if (hero.pin) {
        pin.dataset.pinProject = view.id;
        pin.dataset.pinned = hero.pin.pinned ? 'true' : 'false';
        pin.setAttribute('aria-pressed', hero.pin.pinned ? 'true' : 'false');
    } else {
        delete pin.dataset.pinProject;
    }
    const edit = byId('projectEdit');
    edit.href = hero.edit_href;
    edit.hidden = false;

    const net = byId('projectNet');
    net.hidden = !hero.net;
    net.className = 'project-net' + (hero.net && hero.net.kind ? ' ' + hero.net.kind : '');
    byId('projectNetValue').textContent = hero.net ? hero.net.text : '';

    const totals = byId('projectTotals');
    totals.style.gridTemplateColumns = 'repeat(' + hero.columns + ', minmax(0, 1fr))';
    totals.replaceChildren.apply(totals, hero.chips.map(function (c) {
        const chip = clone('tplKpiChip');
        slot(chip, 'label').textContent = c.label;
        const value = slot(chip, 'value');
        value.textContent = c.value;
        if (c.kind) value.classList.add(c.kind);
        if (c.countup != null) value.dataset.countup = c.countup;
        return chip;
    }));
    totals.hidden = false;

    const bar = byId('projectBar');
    bar.hidden = !hero.bar;
    if (hero.bar) {
        const track = bar.querySelector('.balance-bar-track');
        track.setAttribute('aria-valuenow', hero.bar.pct);
        track.setAttribute('aria-label', hero.bar.text);
        const fill = bar.querySelector('.balance-bar-fill');
        fill.className = 'balance-bar-fill' + (hero.bar.kind ? ' ' + hero.bar.kind : '');
        fill.style.width = hero.bar.width + '%';
        bar.querySelector('.balance-bar-legend span').textContent = hero.bar.text;
    }
}

/* A `.cat-tx-row` with the same `data-*` as the server-built one: tapping it
 * opens editing through `transactions.js` with no extra code. */
function txRow(r, extra) {
    const node = clone('tplTxRow'), box = node.querySelector('.cat-tx-row');
    if (extra) box.classList.add('tx-extra');
    Object.keys(r.data).forEach(function (k) {
        box.dataset[k.replace(/_([a-z])/g, (m, c) => c.toUpperCase())] = r.data[k];
    });
    slot(node, 'day').textContent = r.day;
    const desc = slot(node, 'desc');
    desc.textContent = r.text;
    if (r.installment) desc.append(make('span', 'installment-tag', r.installment));
    else if (r.recurring) desc.append(' · ', clone('tplRepeatIcon'), ' ' + r.recurring);
    if (r.entered_by) desc.append(' · ', make('span', 'tx-entered-by', r.entered_by));
    const owner = slot(node, 'owner');
    if (r.owner) {
        owner.classList.add('owner-' + r.owner.color);
        owner.textContent = r.owner.name;
    } else owner.remove();
    const receipt = slot(node, 'receipt');
    if (r.receipt) receipt.dataset.href = r.receipt; else receipt.remove();
    const amount = slot(node, 'amount');
    amount.classList.add(r.data.type);
    amount.textContent = r.amount;
    return node;
}

function chartCard(c) {
    const card = clone('tplChartCard');
    slot(card, 'title').textContent = c.title;
    const canvas = slot(card, 'canvas');
    canvas.id = 'chart-' + c.type;
    canvas.setAttribute('aria-label', c.title);
    const total = slot(card, 'total');
    total.classList.add(c.type);
    total.textContent = c.total;
    slot(card, 'label').textContent = c.label;
    const legend = slot(card, 'legend');
    c.items.forEach(function (item) {
        const li = clone('tplLegendItem'), head = li.querySelector('.legend-item');
        slot(li, 'dot').style.background = 'var(--chart-color-' + item.color + ')';
        slot(li, 'icon').textContent = item.icon;
        slot(li, 'name').textContent = item.name;
        slot(li, 'pct').textContent = item.pct + '%';
        slot(li, 'amount').textContent = item.amount;
        const rows = slot(li, 'rows');
        if (item.rows.length) {
            head.classList.add('clickable');
            head.setAttribute('role', 'button');
            head.tabIndex = 0;
            head.setAttribute('aria-expanded', 'false');
            item.rows.forEach(function (r) { rows.appendChild(txRow(r, false)); });
        } else {
            slot(li, 'chevron').remove();
            rows.remove();
        }
        legend.appendChild(li);
    });
    return card;
}

const FIRST_SHOWN = 5;

function fill(view) {
    fillHero(view);
    byId('sfPageProject').dataset.projectId = view.id;

    const archived = byId('projectArchived');
    archived.hidden = !view.archived;
    archiveButton(slot(archived, 'archive'), view);
    const quiet = byId('projectQuietHolder');
    quiet.replaceChildren();
    if (view.quiet_since) {
        const banner = clone('tplQuiet'), box = banner.firstElementChild;
        box.dataset.projectQuiet = view.id;
        slot(banner, 'text').textContent = view.quiet_since;
        archiveButton(slot(banner, 'archive'), view);
        quiet.appendChild(banner);
    }

    const charts = byId('projectCharts');
    charts.replaceChildren.apply(charts, view.charts.map(chartCard));
    if (!window.sfCharts.ready) charts.querySelectorAll('.doughnut-wrap').forEach(window.sfCharts.unavailable);

    byId('projectTxTitle').textContent = view.count_title;
    const list = byId('projectTxList');
    list.classList.remove('show-all');
    list.replaceChildren.apply(list, view.transactions.map(function (r, i) { return txRow(r, i >= FIRST_SHOWN); }));
    list.hidden = !view.transactions.length;
    const more = byId('showAllProjectTx');
    more.hidden = view.transactions.length <= FIRST_SHOWN;
    more.dataset.more = view.show_all;
    more.textContent = view.show_all;
    more.setAttribute('aria-expanded', 'false');
    byId('projectTxEmpty').hidden = !!view.transactions.length;
    byId('projectTxEmptySub').textContent = view.empty_sub;

    const finish = byId('projectFinish');
    finish.hidden = view.archived;
    archiveButton(slot(finish, 'archive'), view);
}

function ask(holder) {
    return fetch(holder.dataset.viewUrl, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
        .then(function (r) {
            // 401: `auth-guard.js` is already sending the user to sign in
            if (r.status === 401) return null;
            // Gone (deleted, another member's personal project): to the list,
            // where the notice the server left says why
            if (r.status === 404) {
                return r.json().catch(function () { return {}; }).then(function (d) {
                    // The list says why (carried in this tab, not in the session)
                    try { if (d.error) window.sfToastAfterReload(d.error); } catch (e) { /* the move matters more */ }
                    window.location.replace(d.redirect || '/projects');
                    return null;
                });
            }
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
}

/* `opening`: the data arrived while the page was opening, so this is its
 * entrance (`motion.js` brings the cards in, the totals count up). Otherwise
 * it is a redraw after a save, which the server-built page announced as
 * `sf:refreshed`. */
function show(holder, view, opening) {
    fill(view);
    byId('projectBody').hidden = false;
    holder.removeAttribute('aria-busy');
    // Shown before drawing: Chart.js measures the canvas, and a hidden one is 0×0
    sfProjectCharts(view.breakdown);
    window.dispatchEvent(opening ? new CustomEvent('sf:view-entered', { detail: { root: holder } })
                                 : new CustomEvent('sf:refreshed'));
}

function load(opening) {
    const holder = byId(VIEW);
    if (!holder || holder.dataset.loading) return;
    holder.dataset.loading = '1';
    byId('projectError').hidden = true;
    ask(holder)
        .then(function (view) { if (view && byId(VIEW) === holder) show(holder, view, opening); })
        .catch(function () {
            if (byId(VIEW) !== holder) return;
            byId('projectErrorText').textContent = window.sfNetError ? window.sfNetError() : 'נסו שוב';
            byId('projectError').hidden = false;
            holder.removeAttribute('aria-busy');
        })
        .finally(function () { delete holder.dataset.loading; });
}

// After a save (`softReload` in core.js): the same page, new numbers. A failure
// here is the caller's to handle: it falls back to a full load.
window.sfViews = window.sfViews || {};
window.sfViews[VIEW] = function () {
    const holder = byId(VIEW);
    if (!holder) return Promise.reject(new Error('no view'));
    return ask(holder).then(function (view) { if (view && byId(VIEW) === holder) show(holder, view, false); });
};

document.addEventListener('click', function (e) {
    if (e.target.closest && e.target.closest('#projectRetry')) load(true);
});
load(true);
})();
