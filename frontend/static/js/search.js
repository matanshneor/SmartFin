/* Search across all months: the magnifying glass on the home page (Matan, 30.9,
 * round 6, item 2).
 *
 * The results are `.cat-tx-row` with the same `data-*` as on the month page, so
 * tapping them opens editing through `transactions.js` with no extra code. The
 * button sits in the hero, which is replaced on a soft refresh: the listener is
 * delegated from document. */
(function () {
    const screen  = document.getElementById('searchScreen');
    if (!screen) return;
    const input   = document.getElementById('searchInput');
    const summary = document.getElementById('searchSummary');
    const results = document.getElementById('searchResults');
    const HINT = summary.textContent;
    const MONTHS = ['', 'ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני', 'יולי',
                    'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר'];
    let timer = null, seq = 0, lastFocus = null;

    function open() {
        lastFocus = document.activeElement;
        screen.hidden = false;
        document.body.classList.add('search-open');
        requestAnimationFrame(function () { input.focus(); });
    }
    function close() {
        screen.hidden = true;
        document.body.classList.remove('search-open');
        if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
    }

    function row(tx) {
        const li = document.createElement('li');
        li.className = 'cat-tx-row';
        li.setAttribute('role', 'button');
        li.tabIndex = 0;
        const d = li.dataset;
        d.id = tx.id; d.amount = tx.amount; d.type = tx.type;
        d.categoryId = tx.category_id || ''; d.projectCategoryId = tx.project_category_id || '';
        d.description = tx.description || ''; d.date = tx.date; d.userId = tx.user_id || '';
        d.isRecurring = String(!!tx.is_recurring);
        d.recurringFrequency = tx.recurring_frequency || '';
        d.recurringEndDate = tx.recurring_end_date || '';
        d.recurringParentId = tx.recurring_parent_id || '';
        d.projectId = tx.project_id || '';
        const date = document.createElement('span');
        date.className = 'cat-tx-date';
        date.textContent = tx.date.slice(8, 10) + '.' + tx.date.slice(5, 7);
        const desc = document.createElement('span');
        desc.className = 'cat-tx-desc';
        desc.textContent = (tx.category_icon || '') + ' ' + (tx.description || tx.category_name)
            + (tx.description ? ' · ' + tx.category_name : '')
            + (tx.project_name ? ' · ' + (tx.project_icon || '🎯') + ' ' + tx.project_name : '');
        const amt = document.createElement('span');
        amt.className = 'cat-tx-amount ' + tx.type;
        amt.textContent = (tx.type === 'expense' ? '-' : tx.type === 'income' ? '+' : '')
            + '₪' + window.sfMoney(tx.amount);
        li.append(date, desc, amt);
        return li;
    }

    function render(data) {
        results.innerHTML = '';
        if (!data.count) { summary.textContent = 'לא נמצאו עסקאות'; return; }
        const t = data.totals, parts = [];
        if (t.expense) parts.push('הוצאות ₪' + window.sfMoney(t.expense));
        if (t.income)  parts.push('הכנסות ₪' + window.sfMoney(t.income));
        if (t.savings) parts.push('חיסכון ₪' + window.sfMoney(t.savings));
        summary.textContent = (data.count === 1 ? 'נמצאה עסקה אחת' : 'נמצאו ' + data.count + ' עסקאות')
            + (parts.length ? ' · ' + parts.join(' · ') : '')
            + (data.truncated ? ' · מוצגות 100 האחרונות' : '');
        // A group per month, newest first: the server already sorts

        let list = null, key = null;
        data.results.forEach(function (tx) {
            const k = tx.date.slice(0, 7);
            if (k !== key) {
                key = k;
                const h = document.createElement('h3');
                h.className = 'search-month';
                h.textContent = MONTHS[parseInt(k.slice(5, 7), 10)] + ' ' + k.slice(0, 4);
                list = document.createElement('ul');
                list.className = 'cat-tx-list search-list';
                results.append(h, list);
            }
            list.appendChild(row(tx));
        });
    }

    function run() {
        const q = input.value.trim();
        const mine = ++seq;
        if (q.length < 2 && isNaN(parseFloat(q))) {
            results.innerHTML = ''; summary.textContent = HINT; return;
        }
        summary.textContent = 'מחפש…';
        fetch('/api/search?q=' + encodeURIComponent(q))
            .then(function (r) {
                return r.json().then(function (data) {
                    if (!r.ok) throw new Error(data && data.error);
                    return data;
                });
            })
            .then(function (data) { if (mine === seq) render(data); })
            .catch(function () { if (mine === seq) summary.textContent = window.sfNetError(); });
    }

    document.addEventListener('click', function (e) {
        if (e.target.closest && e.target.closest('#searchOpen')) open();
    });
    document.getElementById('searchClose').addEventListener('click', close);
    input.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(run, 300);
    });
    document.addEventListener('keydown', function (e) {
        // Escape closes the search, unless the edit sheet is open above it

        if (e.key === 'Escape' && !screen.hidden
            && !document.querySelector('.modal-overlay.open')) close();
    });
    // After editing or deleting from the results, search again so old values
    // aren't shown

    window.addEventListener('sf:refreshed', function () { if (!screen.hidden) run(); });
})();
