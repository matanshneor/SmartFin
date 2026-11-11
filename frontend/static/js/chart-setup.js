/* Chart settings, and what happens when the library didn't load.
 *
 * The same eight lines of Chart.defaults were copied into three files, and in
 * each of them they sat at the top of the block that also held everything
 * else: opening a category, searching transactions, the Enter/Escape keys. If
 * Chart.js didn't arrive (a blocker, a bad network, a corrupt cached file, or
 * just a page that doesn't load it because there is nothing to draw), the
 * first line threw a ReferenceError and the rest never ran. The user saw a
 * page that looked perfectly fine and simply didn't respond to taps, and that
 * is the worst kind of confusion: there is nothing to report.
 *
 * Now `window.sfCharts.ready` says whether there is a library. The chart code
 * goes behind the flag, the rest of the page doesn't. A chart is decoration on
 * numbers already written on the page anyway; it is not the source of truth for
 * any amount.
 */
window.sfCharts = (function () {
    // Colours that differ from each other in both hue and lightness (Matan,
    // 18.10): in the previous set dark green vs turquoise, purple vs pink-purple
    // and two blues almost merged. Each colour stayed in the same place and the
    // same family, so a category doesn't "jump" to a foreign colour.

    var COLORS = [
        '#A67C00', '#2E8B6E', '#B83A4B', '#1F5F6B',
        // 5, 6, 8 and 9–11 warmer and quieter (Matan, 11.11, option A from the
        // samples): plum, sage, peach, olive, copper, stone blue instead of the
        // saturated purple, cyan, pink, lime, orange and blue. Same places and
        // families, about as far apart from each other as before.
        '#8B4A6B', '#9CC4B6', '#7A4E2D', '#E0A28F',
        // 12 and not 8 (Matan, 12.10): the ninth came out in the first one's
        // colour. `CHART_COLORS` in app.py

        '#8F9A3C', '#D0703A', '#7E8FB3', '#8C857D'
    ];
    // From the active theme, not constants: in dark mode (round 6, item 4)
    // dark grey on a dark background isn't readable, and grid lines in the ink
    // colour disappear

    var dark  = document.documentElement.getAttribute('data-theme') === 'dark';
    var MUTED = dark ? '#A69D8C' : '#78716C';
    var GRID  = dark ? 'rgba(255,244,214,0.08)' : 'rgba(28,25,23,0.07)';
    // The card colour: the line separating the donut slices

    var SURFACE = dark ? '#211B12' : '#FFFFFF';

    // Chart colours are available to CSS too: some legends are rendered on the
    // server, and the dot next to each category is coloured from these
    // variables. So this happens even without the library; otherwise a whole
    // legend would lose its colours through no fault of its own.

    COLORS.forEach(function (c, i) {
        document.documentElement.style.setProperty('--chart-color-' + i, c);
    });

    if (typeof Chart === 'undefined') {
        // Instead of an empty canvas that looks like a failure: a sentence that
        // explains, and makes clear the numbers themselves are fine.

        document.querySelectorAll('.doughnut-wrap, .line-chart-wrap').forEach(function (wrap) {
            var canvas = wrap.querySelector('canvas');
            if (!canvas) return;
            var note = document.createElement('div');
            note.className = 'chart-empty';
            note.innerHTML =
                '<p>📉</p>' +
                '<p>הגרף לא נטען</p>' +
                '<p>המספרים עצמם מעודכנים ונכונים. רענון העמוד בדרך כלל פותר.</p>';
            canvas.replaceWith(note);
            // The number in the middle of the donut is positioned absolutely
            // over the canvas; without the canvas it has no height to sit in,
            // and it would fall onto the text

            wrap.classList.add('chart-unavailable');
        });
        return { ready: false, colors: COLORS, muted: MUTED, grid: GRID, surface: SURFACE };
    }

    Chart.defaults.font.family = "'Rubik', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    Chart.defaults.color = MUTED;
    Chart.defaults.animation.duration = 900;
    Chart.defaults.animation.easing = 'easeOutQuart';
    // Whoever asked their phone for less motion gets the charts already drawn:
    // count-up and the bars already honour it, the charts didn't (design pass, 11.11)
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        Chart.defaults.animation = false;
    }
    Chart.defaults.plugins.legend.display = false;
    Chart.defaults.plugins.tooltip.backgroundColor = '#1C1917';
    Chart.defaults.plugins.tooltip.titleColor = '#FAF7F0';
    Chart.defaults.plugins.tooltip.bodyColor = '#E7E0D2';

    return { ready: true, colors: COLORS, muted: MUTED, grid: GRID, surface: SURFACE };
})();
