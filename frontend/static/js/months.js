const SF_VIEW = window.sfData('sf-view-data');

/* ═══ The chart ═══  (see chart-setup.js)
 * A block separate from "show more" below: when the library doesn't load, the
 * button still works. */
(function () {
if (!window.sfCharts.ready) return;

const TEXT_MUTED = window.sfCharts.muted;
const GRID_LINE  = window.sfCharts.grid;

const trendData = SF_VIEW.trend;
// The range of both charts: 3 / 6 / 12 months (idea 24). Each chart registers
// here how it updates, and the buttons call all of them.

let RANGE = 6;
const rangeListeners = [];
const SHORT_MONTHS = ['', 'ינו׳', 'פבר׳', 'מרץ', 'אפר׳', 'מאי', 'יוני', 'יולי', 'אוג׳', 'ספט׳', 'אוק׳', 'נוב׳', 'דצמ׳'];

// ── Month comparison ──

if (trendData.length > 0) {
    let last6 = trendData.slice(-RANGE);      // the name is left over from the "6 months" days; this is the chosen range
    const ctx = document.getElementById('compareChart');
    if (ctx) {
        const compareChart = new Chart(ctx, {
            type: 'bar',
            data: {
                // Short names under the bars: at font size 13 the full ones
                // stuck together ("אוגוסטספטמבר"). The full name is in the
                // tooltip when a bar is tapped.

                labels: last6.map(d => SHORT_MONTHS[d.month] || d.month_name),
                // In reverse order: Chart.js lays out the bars in each month
                // left to right, and this way income comes out on the right, as
                // in the legend and in Hebrew reading

                datasets: [
                    {
                        label:           'חיסכון',
                        data:            last6.map(d => d.savings),
                        backgroundColor: 'rgba(166,124,0,0.9)',
                        borderRadius:    5,
                        categoryPercentage: 0.86,   // wide bars, less space between months
                        barPercentage:      0.92,
                    },
                    {
                        label:           'הוצאות',
                        data:            last6.map(d => d.expense),
                        backgroundColor: 'rgba(160,69,69,0.9)',
                        borderRadius:    5,
                        categoryPercentage: 0.86,   // wide bars, less space between months
                        barPercentage:      0.92,
                    },
                    {
                        label:           'הכנסות',
                        data:            last6.map(d => d.income),
                        backgroundColor: 'rgba(61,107,84,0.9)',
                        borderRadius:    5,
                        categoryPercentage: 0.86,   // wide bars, less space between months
                        barPercentage:      0.92,
                    }
                ]
            },
            options: {
                responsive:          true,
                maintainAspectRatio: false,
                // Tapping a month in the chart opens it, like a table row (Matan,
                // 30.9, idea 10). By the whole column and not just the bar that
                // was hit: ₪0 savings has nothing to hit, and a finger is wider
                // than one bar

                onClick: function (evt, _els, chart) {
                    const hit = chart.getElementsAtEventForMode(evt, 'index', { intersect: false }, true);
                    if (!hit.length) return;
                    const d = last6[hit[0].index];
                    window.location.href = '/month?year=' + d.year + '&month=' + d.month;
                },
                onHover: function (evt, els) {
                    evt.native.target.style.cursor = els.length ? 'pointer' : 'default';
                },
                plugins: {
                    legend: {
                        display:  true,
                        position: 'bottom',
                        labels: { boxWidth: 14, padding: 18, font: { size: 14 }, color: TEXT_MUTED }
                    },
                    tooltip: {
                        callbacks: {
                            title: items => last6[items[0].dataIndex].month_name,
                            label: c => ` ${c.dataset.label}: ₪${window.sfMoney(c.parsed.y)}`
                        }
                    }
                },
                // Right to left (Matan, 30.9): the old month on the right and the
                // new on the left, as Hebrew reads, and the amount scale on the
                // right side

                scales: {
                    x: { reverse: true, grid: { display: false },
                         // Straight names, not slanted: slanted ones get cut at the edge

                         ticks: { font: { size: 13 }, maxRotation: 0, autoSkip: false } },
                    y: {
                        position: 'right',
                        grid: { color: GRID_LINE },
                        ticks: { font: { size: 13 }, callback: v => '₪' + v.toLocaleString('en-US') }
                    }
                }
            }
        });
        rangeListeners.push(function () {
            last6 = trendData.slice(-RANGE);
            compareChart.data.labels = last6.map(d => SHORT_MONTHS[d.month] || d.month_name);
            compareChart.data.datasets[0].data = last6.map(d => d.savings);
            compareChart.data.datasets[1].data = last6.map(d => d.expense);
            compareChart.data.datasets[2].data = last6.map(d => d.income);
            compareChart.update();
        });
    }
}

// ── By category (Matan, 30.9) ──
// The same months as the chart above, right to left like it. The amount above
// each bar, a dashed line at the average, and the current month lighter: it
// isn't over yet.

const catTrend = SF_VIEW.cat_trend;
const catCanvas = document.getElementById('categoryChart');
if (catTrend && catTrend.categories.length && catCanvas) {
    const allMonths = catTrend.months;              // 12; the last ones are shown by the range
    let months = allMonths.slice(-RANGE);
    const money = v => '₪' + window.sfMoney(Math.round(v));
    const isNow = months.length && (function () {
        const d = new Date(), last = allMonths[allMonths.length - 1];
        return last.year === d.getFullYear() && last.month === d.getMonth() + 1;
    })();
    const BAR = 'rgba(160,69,69,0.9)', BAR_NOW = 'rgba(160,69,69,0.35)';
    function barColors() {
        return months.map((m, i) => (isNow && i === months.length - 1) ? BAR_NOW : BAR);
    }

    /* The category in the chosen range: the values and transactions of the months
     * shown, and the average and the most expensive month recomputed from the
     * finished months, as in `category_trend` on the server (the current month
     * isn't over yet and would pull the average down). */
    function inRange(c) {
        const n = months.length, off = allMonths.length - n;
        const values = c.values.slice(off), items = (c.items || []).slice(off);
        const now = new Date(), ty = now.getFullYear(), tm = now.getMonth() + 1;
        const finished = months.map((m, i) => (m.year < ty || (m.year === ty && m.month < tm)) ? i : -1)
                               .filter(i => i >= 0);
        const done = finished.map(i => values[i]);
        const top = finished.length ? finished.reduce((a, b) => values[b] > values[a] ? b : a) : null;
        return {
            key: c.key, name: c.name, icon: c.icon, values: values, items: items,
            avg: done.length ? done.reduce((a, b) => a + b, 0) / done.length : null,
            max: top !== null ? values[top] : null,
            max_label: top !== null ? months[top].name : null,
            current: isNow ? values[n - 1] : null,
        };
    }

    // The amount above each bar, without a plugin: one line that draws after
    // the bars

    const valuesOnTop = {
        id: 'sfValuesOnTop',
        afterDatasetsDraw: function (chart) {
            const ctx = chart.ctx, meta = chart.getDatasetMeta(0);
            ctx.save();
            ctx.fillStyle = TEXT_MUTED;
            ctx.font = '600 12px ' + getComputedStyle(document.body).fontFamily;
            ctx.textAlign = 'center';
            meta.data.forEach(function (bar, i) {
                const v = chart.data.datasets[0].data[i];
                if (v > 0) ctx.fillText(Math.round(v).toLocaleString('en-US'), bar.x, bar.y - 6);
            });
            ctx.restore();
        },
    };

    const chart = new Chart(catCanvas, {
        type: 'bar',
        data: {
            labels: months.map(m => m.label),
            datasets: [
                { label: 'הוצאה', data: [], borderRadius: 5,
                  backgroundColor: barColors(),
                  categoryPercentage: 0.86, barPercentage: 0.8, order: 2 },
                { type: 'line', label: 'ממוצע', data: [], borderColor: TEXT_MUTED, borderWidth: 1.2,
                  borderDash: [4, 4], pointRadius: 0, pointHitRadius: 0, fill: false, order: 1 },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            layout: { padding: { top: 20 } },
            plugins: {
                legend: { display: false },
                // No floating label (Matan, 1.10): the amount is already written
                // above the bar, and tapping the bar opens the sheet.

                tooltip: { enabled: false },
            },
            scales: {
                x: { reverse: true, grid: { display: false },
                     ticks: { font: { size: 13 }, maxRotation: 0, autoSkip: false } },
                y: { display: false, beginAtZero: true, grid: { display: false } },
            },
        },
        plugins: [valuesOnTop],
    });

    // ── Tapping a month's bar: a sheet with the category's expenses in that
    // month (Matan, 30.9) ──

    let sheet = null;
    function closeSheet() {
        if (sheet) { sheet.remove(); sheet = null; }
        catCanvas.focus && catCanvas.focus({ preventScroll: true });
    }
    function openMonth(i) {
        if (!current) return;
        const m = months[i];
        const items = (current.items || [])[i] || [];
        if (sheet) sheet.remove();
        sheet = document.createElement('div');
        sheet.className = 'color-sheet cat-month-sheet';
        sheet.setAttribute('role', 'dialog');
        sheet.setAttribute('aria-modal', 'true');
        sheet.setAttribute('aria-labelledby', 'catMonthTitle');
        const card = document.createElement('div');
        card.className = 'color-sheet-card';
        const title = document.createElement('p');
        title.className = 'color-sheet-title';
        title.id = 'catMonthTitle';
        title.textContent = current.icon + ' ' + current.name + ' · ' + m.name + ' ' + m.year;
        const sub = document.createElement('p');
        sub.className = 'color-sheet-hint';
        sub.textContent = items.length
            ? window.sfCount(items.length, 'עסקה אחת', 'עסקאות') + ' · ' + money(current.values[i])
            : 'לא בוצעו עסקאות בקטגוריה בחודש זה';
        card.append(title, sub);
        if (items.length) {
            const ul = document.createElement('ul');
            ul.className = 'cat-month-list';
            items.forEach(function (t) {
                const li = document.createElement('li');
                const d = document.createElement('span');
                d.className = 'cat-month-date';
                d.textContent = t.date.slice(8, 10) + '.' + t.date.slice(5, 7);
                const n = document.createElement('span');
                n.className = 'cat-month-desc';
                n.textContent = t.description || current.name;
                const a = document.createElement('span');
                a.className = 'cat-month-amount';
                a.textContent = '-' + money(t.amount);
                li.append(d, n, a);
                ul.appendChild(li);
            });
            card.appendChild(ul);
        }
        const actions = document.createElement('div');
        actions.className = 'edit-actions';
        const link = document.createElement('a');
        link.className = 'btn-sm btn-ghost';
        // To the top of the page, not the breakdown (Matan, 1.10): there it
        // opened cut off at the top

        link.href = '/month?year=' + m.year + '&month=' + m.month;
        link.textContent = 'לעמוד החודש';
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'btn-sm btn-primary';
        close.textContent = 'סגירה';
        close.addEventListener('click', closeSheet);
        actions.append(close, link);
        card.appendChild(actions);
        sheet.appendChild(card);
        sheet.addEventListener('click', function (e) { if (e.target === sheet) closeSheet(); });
        document.body.appendChild(sheet);
        close.focus({ preventScroll: true });
    }
    // By the whole column, like in the chart above: a month with no expense
    // opens too and says so

    chart.options.onClick = function (evt) {
        const hit = chart.getElementsAtEventForMode(evt, 'index', { intersect: false }, true);
        if (hit.length) openMonth(hit[0].index);
    };
    chart.options.onHover = function (evt, els) {
        evt.native.target.style.cursor = els.length ? 'pointer' : 'default';
    };
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && sheet) closeSheet();
    });

    const stats = document.getElementById('catTrendStats');
    let current = null;
    function show(key) {
        const c = inRange(catTrend.categories.find(x => x.key === key) || catTrend.categories[0]);
        current = c;
        chart.data.datasets[0].data = c.values;
        // The average line across the whole chart width; without a finished
        // month there is no average

        chart.data.datasets[1].data = c.avg == null ? [] : c.values.map(() => c.avg);
        chart.options.scales.y.suggestedMax = Math.max.apply(null, c.values) * 1.12;
        chart.update();
        const cell = (label, value) => '<div><span>' + label + '</span><b>' + value + '</b></div>';
        stats.innerHTML =
            cell('ממוצע לחודש', c.avg == null ? '—' : '<span class="num">' + money(c.avg) + '</span>') +
            // "החודש הכי יקר - יולי" (Matan, 1.10)

            cell('החודש הכי יקר' + (c.max_label ? ' <span class="nowrap">- ' + c.max_label + '</span>' : ''),
                 c.max_label ? '<span class="num">' + money(c.max) + '</span>' : '—') +
            (c.current == null ? '' : cell('החודש', '<span class="num">' + money(c.current) + '</span>'));
    }
    // Arriving from a category on the month page (`?cat=<id>`, idea 23): it is
    // the selected one. A category with no history here: stay on the first.

    let wanted = null;
    try { wanted = new URLSearchParams(location.search).get('cat'); } catch (err) { /* old browser */ }
    const fromMonth = wanted && catTrend.categories.some(x => x.key === wanted) ? wanted : null;
    show(fromMonth || catTrend.categories[0].key);
    rangeListeners.push(function () {
        months = allMonths.slice(-RANGE);
        chart.data.labels = months.map(m => m.label);
        chart.data.datasets[0].backgroundColor = barColors();
        show(current ? current.key : catTrend.categories[0].key);
    });
    if (fromMonth) {
        document.querySelectorAll('.cat-trend-chips .tx-cat-chip').forEach(function (b) {
            const on = b.dataset.key === fromMonth;
            b.classList.toggle('active', on);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
            if (on && b.scrollIntoView) b.scrollIntoView({ block: 'nearest', inline: 'center' });
        });
    }

    document.addEventListener('click', function (e) {
        const chip = e.target.closest && e.target.closest('.cat-trend-chips .tx-cat-chip');
        if (!chip) return;
        document.querySelectorAll('.cat-trend-chips .tx-cat-chip').forEach(function (b) {
            b.classList.toggle('active', b === chip);
            b.setAttribute('aria-pressed', b === chip ? 'true' : 'false');
        });
        show(chip.dataset.key);
    });
}



// ── Expenses by family member over the months (Matan, 3.10, idea 15) ──
// A bar per family member per month, in their fixed colour; the same range as
// the other charts. Below, a monthly average for each, from the finished months
// (as in the category chart).

const memberSeries = SF_VIEW.members;
const memberCanvas = document.getElementById('memberTrendChart');
if (memberSeries && memberSeries.length > 1 && memberCanvas && SF_VIEW.cat_trend) {
    const allM = SF_VIEW.cat_trend.months;            // the same 12 months that were sent
    const avgsEl = document.getElementById('memberTrendAvgs');
    const shownMonths = () => allM.slice(-RANGE);
    const off = () => allM.length - shownMonths().length;
    function datasets() {
        // In reverse order: within each month Chart.js lays out left to right,
        // so the first in the list comes out on the right

        return memberSeries.slice().reverse().map(m => ({
            label: m.name, data: m.values.slice(off()), backgroundColor: m.color,
            borderRadius: 4, categoryPercentage: 0.86, barPercentage: 0.9,
        }));
    }
    function averages() {
        const now = new Date(), ty = now.getFullYear(), tm = now.getMonth() + 1;
        const ms = shownMonths();
        const done = ms.map((m, i) => (m.year < ty || (m.year === ty && m.month < tm)) ? i : -1).filter(i => i >= 0);
        if (!done.length) { avgsEl.textContent = ''; return; }
        avgsEl.textContent = 'ממוצע לחודש: ' + memberSeries.map(function (m) {
            const vals = m.values.slice(off());
            const avg = done.reduce((a, i) => a + vals[i], 0) / done.length;
            return m.name + ' ₪' + window.sfMoney(Math.round(avg));
        }).join(' · ');
    }
    const memberChart = new Chart(memberCanvas, {
        type: 'bar',
        data: { labels: shownMonths().map(m => m.label), datasets: datasets() },
        options: {
            responsive: true, maintainAspectRatio: false,
            onClick: function (evt, _els, chart) {
                const hit = chart.getElementsAtEventForMode(evt, 'index', { intersect: false }, true);
                if (!hit.length) return;
                const m = shownMonths()[hit[0].index];
                window.location.href = '/month?year=' + m.year + '&month=' + m.month;
            },
            onHover: function (evt, els) { evt.native.target.style.cursor = els.length ? 'pointer' : 'default'; },
            plugins: {
                legend: { display: true, position: 'bottom',
                          labels: { boxWidth: 14, padding: 18, font: { size: 14 }, color: TEXT_MUTED } },
                tooltip: { callbacks: {
                    title: items => shownMonths()[items[0].dataIndex].name,
                    label: c => ' ' + c.dataset.label + ': ₪' + window.sfMoney(c.parsed.y) } },
            },
            scales: {
                x: { reverse: true, grid: { display: false },
                     ticks: { font: { size: 13 }, maxRotation: 0, autoSkip: false } },
                y: { position: 'right', grid: { color: GRID_LINE },
                     ticks: { font: { size: 13 }, callback: v => '₪' + v.toLocaleString('en-US') } },
            },
        },
    });
    averages();
    rangeListeners.push(function () {
        memberChart.data.labels = shownMonths().map(m => m.label);
        memberChart.data.datasets = datasets();
        memberChart.update();
        averages();
    });
}
// ── The range buttons ──

document.addEventListener('click', function (e) {
    const btn = e.target.closest && e.target.closest('.range-chip');
    if (!btn) return;
    const n = Number(btn.dataset.range);
    if (!n || n === RANGE) return;
    RANGE = n;
    document.querySelectorAll('.range-chip').forEach(function (b) {
        const on = b === btn;
        b.classList.toggle('active', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    rangeListeners.forEach(function (fn) { fn(); });
});
})();


/* ═══ The page's interactivity ═══ */
(function () {

// A tap anywhere on the row opens the month. The link itself (the month name) is
// what the keyboard and screen reader meet; the row only enlarges the tap area
// for a finger.

document.addEventListener('click', function (e) {
    const row = e.target.closest('.months-table tr.month-row[data-href]');
    if (!row || e.target.closest('a')) return;
    window.location.href = row.dataset.href;
});
})();
