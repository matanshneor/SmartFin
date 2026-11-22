const SF_VIEW = window.sfData('sf-view-data');

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


/* ═══ The charts ═══  (see chart-setup.js) */
(function () {
if (!window.sfCharts.ready) return;

const COLORS    = window.sfCharts.colors;
const breakdown = SF_VIEW.breakdown;
['expense', 'income', 'savings'].forEach(function (typ) {
    const items = breakdown[typ] || [];
    const ctx = document.getElementById('chart-' + typ);
    if (!ctx || !items.length) return;

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
})();

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

