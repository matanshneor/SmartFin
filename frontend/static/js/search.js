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

    // Scrolling the results puts the keyboard away (Matan, 22.11): on iPhone it
    // covers the bottom of the list, and the last results couldn't be reached
    results.addEventListener('touchmove', function () {
        const a = document.activeElement;
        if (a && screen.contains(a) && a.matches('input')) a.blur();
    }, { passive: true });

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
        d.installments = tx.installments || ''; d.installmentNo = tx.installment_no || '';
        const date = document.createElement('span');
        date.className = 'cat-tx-date';
        date.textContent = tx.date.slice(8, 10) + '.' + tx.date.slice(5, 7);
        const desc = document.createElement('span');
        desc.className = 'cat-tx-desc';
        desc.textContent = (tx.category_icon || '') + ' ' + (tx.description || tx.category_name)
            + (tx.description ? ' · ' + tx.category_name : '')
            + (tx.project_name ? ' · ' + (tx.project_icon || '🎯') + ' ' + tx.project_name : '');
        if (tx.installments && tx.installment_no) {     // on a line of its own, never cut off
            const tag = document.createElement('span');
            tag.className = 'installment-tag';
            tag.textContent = 'תשלום ' + tx.installment_no + ' מתוך ' + tx.installments;
            desc.append(tag);
        }
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

    // ── Filters (Matan, 22.11): whose, when, how much ──
    const toggle = document.getElementById('searchFilterToggle');
    const panel  = document.getElementById('searchFilters');
    const clear  = document.getElementById('searchFilterClear');
    const whoBox = document.getElementById('searchWho');
    const whenBox = document.getElementById('searchWhen');
    const minIn = document.getElementById('searchMin'), maxIn = document.getElementById('searchMax');
    const state = { who: '', when: '' };
    let membersLoaded = false;

    function iso(d) {
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    }
    function num(el) { return el.value.replace(/,/g, '').trim(); }
    function filterParams() {
        const p = [];
        if (state.who) p.push('who=' + encodeURIComponent(state.who));
        if (state.when) {
            const now = new Date();
            const from = state.when === 'month' ? new Date(now.getFullYear(), now.getMonth(), 1)
                       : state.when === '3m'    ? new Date(now.getFullYear(), now.getMonth() - 2, 1)
                       :                          new Date(now.getFullYear(), 0, 1);
            p.push('from=' + iso(from));
        }
        if (num(minIn)) p.push('min=' + encodeURIComponent(num(minIn)));
        if (num(maxIn)) p.push('max=' + encodeURIComponent(num(maxIn)));
        return p;
    }
    function markFilters() {
        const n = filterParams().length;
        toggle.textContent = n ? 'סינון · ' + n : 'סינון';
        toggle.classList.toggle('active', n > 0);
        clear.hidden = n === 0;
    }
    function pick(box, attr, value) {
        box.querySelectorAll('.date-chip').forEach(function (c) {
            const on = c.getAttribute(attr) === value;
            c.classList.toggle('active', on);
            c.setAttribute('aria-pressed', on);
        });
    }
    function chip(value, label) {
        const c = document.createElement('button');
        c.type = 'button';
        c.className = 'date-chip' + (value === '' ? ' active' : '');
        c.dataset.who = value;
        c.setAttribute('aria-pressed', value === '');
        c.textContent = label;
        return c;
    }
    function loadMembers() {
        if (membersLoaded || !window.sfFetchList) return;
        membersLoaded = true;
        window.sfFetchList('/api/family/members').then(function (members) {
            if (!members || members.length < 2) return;     // one person: nothing to choose
            whoBox.append(chip('', 'הכל'), chip('shared', 'משותפת'));
            members.forEach(function (m) { whoBox.appendChild(chip(m.id, String(m.name || '').split(' ')[0])); });
            document.getElementById('searchWhoRow').hidden = false;
        }).catch(function () { membersLoaded = false; });
    }
    toggle.addEventListener('click', function () {
        panel.hidden = !panel.hidden;
        toggle.setAttribute('aria-expanded', String(!panel.hidden));
        if (!panel.hidden) loadMembers();
    });
    whoBox.addEventListener('click', function (e) {
        const c = e.target.closest('[data-who]');
        if (!c) return;
        state.who = c.dataset.who; pick(whoBox, 'data-who', state.who); markFilters(); run();
    });
    whenBox.addEventListener('click', function (e) {
        const c = e.target.closest('[data-when]');
        if (!c) return;
        state.when = c.dataset.when; pick(whenBox, 'data-when', state.when); markFilters(); run();
    });
    [minIn, maxIn].forEach(function (el) {
        el.addEventListener('input', function () {
            markFilters();
            clearTimeout(timer);
            timer = setTimeout(run, 400);
        });
    });
    clear.addEventListener('click', function () {
        state.who = ''; state.when = '';
        pick(whoBox, 'data-who', ''); pick(whenBox, 'data-when', '');
        minIn.value = ''; maxIn.value = '';
        markFilters(); run();
    });

    function run() {
        const q = input.value.trim();
        const mine = ++seq;
        const filters = filterParams();
        // A filter alone is a search too: "everything of Daniel's this year"
        if (q.length < 2 && isNaN(parseFloat(q)) && !filters.length) {
            results.innerHTML = ''; summary.textContent = HINT; return;
        }
        summary.textContent = 'מחפש…';
        fetch('/api/search?q=' + encodeURIComponent(q) + (filters.length ? '&' + filters.join('&') : ''))
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
