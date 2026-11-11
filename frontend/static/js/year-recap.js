/* "Your 2026": the yearly summary screen (Matan, 1.11). Opens on tapping any
 * button with `data-year-recap="<year>"`, and only whoever tapped sees it. The
 * comparison with the previous year comes from the server only when the
 * previous year is full; otherwise it simply isn't there. */
(function () {
    'use strict';
    const overlay = document.getElementById('yearRecap');
    if (!overlay) return;
    const body = document.getElementById('yrBody');
    // The same abbreviations as the comparison page (`months.js`), with ׳
    const SHORT = ['ינו׳', 'פבר׳', 'מרץ', 'אפר׳', 'מאי', 'יוני', 'יולי', 'אוג׳', 'ספט׳', 'אוק׳', 'נוב׳', 'דצמ׳'];
    const MONTHS = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני', 'יולי',
                    'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר'];
    const esc = window.escapeHtml;
    const money = n => '₪' + Math.round(Math.abs(n)).toLocaleString('he-IL');
    let lastFocus = null;

    function section(title, inner) {
        return '<section class="yr-card"><h3 class="yr-card-title">' + title + '</h3>' + inner + '</section>';
    }
    // No emoji in the rows (Matan, 1.11)

    function line(html) {
        return '<li>' + html + '</li>';
    }

    function render(d) {
        // During the year, a checkpoint, "2026 עד עכשיו"; at the end of the
        // year, "2026 שלכם" (Matan, 1.11)

        const now = new Date();
        document.getElementById('yrKicker').textContent = d.partial ? 'איפה אתם עומדים השנה' : 'הסיכום השנתי';
        document.getElementById('yrTitle').textContent = d.year + (d.partial ? ' עד עכשיו' : ' שלכם');
        document.getElementById('yrSub').textContent = (d.partial ? 'נכון ל-' + now.getDate() + '.' + (now.getMonth() + 1) + ' · ' : '')
            + d.count.toLocaleString('he-IL') + ' עסקאות נרשמו השנה';
        let html = '<div class="yr-figures">'
            + '<div class="yr-fig income"><span>הכנסתם</span><b>' + money(d.income) + '</b></div>'
            + '<div class="yr-fig expense"><span>הוצאתם</span><b>' + money(d.expense) + '</b></div>'
            + '<div class="yr-fig savings"><span>חסכתם</span><b>' + money(d.savings) + '</b></div>'
            + '</div>';
        if (d.savings_rate) html += '<p class="yr-rate">חסכתם <b>' + d.savings_rate + '%</b> מההכנסות</p>';

        // The months: an expense bar per month, with the extremes marked

        const max = Math.max.apply(null, d.months.map(m => m.expense)) || 1;
        let bars = '<div class="yr-bars" aria-hidden="true" style="grid-template-columns:repeat(' + d.through + ',1fr)">';
        d.months.filter(m => m.month <= d.through).forEach(function (m) {
            const cls = m.month === d.costliest ? ' is-high' : m.month === d.best ? ' is-best' : '';
            bars += '<div class="yr-bar' + cls + '"><i style="height:' + Math.max(4, Math.round(m.expense / max * 100)) + '%"></i>'
                  + '<span>' + SHORT[m.month - 1] + '</span></div>';
        });
        bars += '</div>';
        let lines = '';
        if (d.best) {
            const b = d.months[d.best - 1];
            lines += line('החודש הכי חסכוני' + (d.partial ? ' עד עכשיו' : '') + ': <b>' + MONTHS[d.best - 1] + '</b> — נשארו ' + money(b.balance));
        }
        if (d.costliest) {
            const c = d.months[d.costliest - 1];
            lines += line('החודש הכי יקר' + (d.partial ? ' עד עכשיו' : '') + ': <b>' + MONTHS[d.costliest - 1] + '</b> — ' + money(c.expense) + ' הוצאות');
        }
        html += section('החודשים', bars + '<ul class="yr-lines">' + lines + '</ul>');

        if (d.top.length) {
            let cats = '<ul class="yr-cats">';
            d.top.forEach(function (c) {
                cats += '<li><span class="yr-cat-name">' + esc(c.icon) + ' ' + esc(c.name) + '</span>'
                      + '<span class="yr-cat-bar"><i style="width:' + c.share + '%"></i></span>'
                      + '<span class="yr-cat-amount">' + money(c.total) + ' · ' + c.share + '%</span></li>';
            });
            html += section('לאן הלך הכסף', cats + '</ul>');
        }

        if (d.compare) {
            const k = d.compare;
            let cmp = line('הוצאתם <b class="' + (k.expense_diff <= 0 ? 'good' : 'bad') + '">'
                           + money(k.expense_diff) + ' ' + (k.expense_diff <= 0 ? 'פחות' : 'יותר') + '</b>' + (d.partial ? '' : ' מ-' + k.year));
            if (k.savings_diff) cmp += line('חסכתם <b class="' + (k.savings_diff > 0 ? 'good' : 'bad') + '">'
                           + money(k.savings_diff) + ' ' + (k.savings_diff > 0 ? 'יותר' : 'פחות') + '</b>');
            if (k.up) cmp += line('<b>' + esc(k.up.name) + '</b> עלה ב-' + k.up.pct + '%');
            if (k.down) cmp += line('<b>' + esc(k.down.name) + '</b> ירד ב-' + Math.abs(k.down.pct) + '%');
            html += section(d.partial ? 'מול אותה תקופה ב-' + k.year : 'מול ' + k.year, '<ul class="yr-lines">' + cmp + '</ul>');
        }

        if (d.projects.length) {
            let pr = '<ul class="yr-projects">';
            d.projects.forEach(function (p) {
                pr += '<li><span>' + esc(p.icon) + ' ' + esc(p.name) + '</span><b>' + money(p.total) + '</b></li>';
            });
            html += section('הפרויקטים של השנה', pr + '</ul>');
        }
        html += '<button type="button" class="yr-done" data-yr-close>סגירה</button>';
        body.innerHTML = html;
    }

    function open(year, trigger) {
        lastFocus = trigger || document.activeElement;
        document.getElementById('yrTitle').textContent = year;
        document.getElementById('yrSub').textContent = '';
        body.innerHTML = '<p class="yr-loading">מכינים את הסיכום…</p>';
        overlay.hidden = false;
        document.body.style.overflow = 'hidden';
        requestAnimationFrame(function () { overlay.classList.add('open'); document.getElementById('yrClose').focus(); });
        fetch('/api/year-recap?year=' + encodeURIComponent(year))
            .then(r => r.json().then(d => { if (!r.ok) throw new Error(d.error || 'לא הצלחנו לטעון את הסיכום'); return d; }))
            .then(render)
            .catch(function (e) { body.innerHTML = '<p class="yr-loading">' + esc(e.message) + '</p>'; });
    }

    function close() {
        overlay.classList.remove('open');
        document.body.style.overflow = '';
        setTimeout(function () { overlay.hidden = true; }, 250);
        if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
    }

    document.addEventListener('click', function (e) {
        const t = e.target.closest && e.target.closest('[data-year-recap]');
        if (t) { open(t.dataset.yearRecap, t); return; }
        if (!overlay.hidden && (e.target === overlay || e.target.closest('#yrClose, [data-yr-close]'))) close();
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !overlay.hidden) close();
    });
})();
