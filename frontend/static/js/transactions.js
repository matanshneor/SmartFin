// The core of entering transactions: the modal, the FAB button, receipt
// scanning, editing and deleting.

(function () {
    const overlay      = document.getElementById('modalOverlay');
    const fabBtn       = document.getElementById('fabBtn');
    const closeBtn     = document.getElementById('modalClose');
    const scanBtn      = document.getElementById('scanBtn');
    const scanWrapper  = document.getElementById('scannerWrapper');
    const receiptInput = document.getElementById('receiptInput');
    const txReceiptPath = document.getElementById('txReceiptPath');
    const scannerThumbnail = document.getElementById('scannerThumbnail');
    const AMOUNT_PLACEHOLDER_DEFAULT = '0';
    const DESCRIPTION_PLACEHOLDER_DEFAULT = 'למשל: שופרסל, משכורת…';
    const toggleExp    = document.getElementById('toggleExpense');
    const toggleInc    = document.getElementById('toggleIncome');
    const toggleSav    = document.getElementById('toggleSavings');
    const submitLabel  = document.getElementById('submitLabel');
    const submitBtn    = document.getElementById('submitBtn');
    const deleteBtn    = document.getElementById('deleteTxBtn');
    const editModeActions = document.getElementById('editModeActions');
    const duplicateBtn = document.getElementById('duplicateTxBtn');
    const categoryGrid = document.getElementById('categoryGrid');
    const projectGroup = document.getElementById('projectGroup');
    const categoryLabel = document.getElementById('categoryLabel');
    const txProject    = document.getElementById('txProject');
    const ownerGroup   = document.getElementById('ownerGroup');
    const ownerToggle  = document.getElementById('ownerToggle');
    const txOwner      = document.getElementById('txOwner');
    const recurringCb  = document.getElementById('txRecurring');
    const recurFields  = document.getElementById('recurringFields');
    const txDate       = document.getElementById('txDate');
    const txEndDate    = document.getElementById('txEndDate');
    const txFrequency  = document.getElementById('txFrequency');
    const txForm       = document.getElementById('txForm');
    const txCategory   = document.getElementById('txCategory');
    const txProjectCategory = document.getElementById('txProjectCategory');
    const txAmount     = document.getElementById('txAmount');
    const txDescription = document.getElementById('txDescription');
    const modalTitle   = document.getElementById('modalTitle');

    // An action noun and not an imperative, like most buttons in the app ("פתיחת
    // חשבון", "יצירת פרויקט חדש", "הצטרפות"). It also sidesteps the singular/plural
    // question: the text around it speaks in the plural ("לחצו", "הזינו"), and a
    // masculine-singular imperative button next to it read like two different
    // writers.

    const TYPE_LABELS = { expense: 'הוספת הוצאה', income: 'הוספת הכנסה', savings: 'הוספת חיסכון' };
    // The family settings: for which transaction types the "whose?" picker is
    // shown (on window so the settings page updates the modal right away when a
    // setting changes)

    window.SF_ATTRIBUTION = window.SF_PAGE_DATA.attribution || {};
    const OWNER_LABELS = { expense: 'של מי ההוצאה?', income: 'של מי ההכנסה?', savings: 'של מי החיסכון?' };

    let currentType   = 'expense';
    let editId        = null;   // null = adding a new transaction, otherwise editing this id
    let editingRecurringParentId = null; // when editing an occurrence created from a recurring template: the template id
    // Opened from "recurring transactions" in settings: the change applies from
    // the current month (Matan, 2.10)

    let editingSeries = false;
    let editingOriginal = null;          // the transaction as it was before the edit, for "undo" (round 6, item 10)
    let originalAmount = null;  // the amount loaded for editing, to compare for "you changed the amount"
    let categoriesCache = null;

    /* Receipt scanning takes a few seconds, and meanwhile the form can be closed
     * and another transaction opened. The response was written into whichever
     * form was open **when it came back**, i.e. into an existing transaction
     * opened for a fix: amount, description, date and category were replaced,
     * and the receipt attached.
     *
     * `scanSeq` goes up on every scan and every close/open of the form; a
     * response whose number is no longer current touches nothing.
     * `claimedReceipt` is the receipt a save already took, so closing the sheet
     * after "save" (which on the home page happens before the server answered)
     * doesn't delete it. */
    let scanSeq = 0;
    let claimedReceipt = null;

    /* On the home page the sheet closes on "save" before the server answered, so
     * adding feels instant. Someone adding several transactions in a row is
     * already opening + and typing the next when the response comes back, and
     * it closed **their** sheet, and what they typed was wiped. `formSeq` goes up
     * on every open of the form; a save whose form was already replaced doesn't
     * touch the current form. */
    let formSeq = 0;

    // An image uploaded for scanning and not saved with a transaction. The
    // server deletes only what no transaction points at; a failure here doesn't
    // concern the user: at worst a file is left.

    function discardReceipt(path) {
        if (!path || path === claimedReceipt) return;
        fetch('/api/receipts/discard', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ path: path }),
        }).catch(function () {});
    }
    let membersCache  = null;
    let projectsCache = null;
    let projectCategoriesCache = {}; // key: "<projectId>:<type>"
    let categoryGridIsProject = false; // whether the current category grid shows project or family categories

    // A date by the local clock (not UTC) with an optional offset in days;
    // otherwise between midnight and ~03:00 in Israel it came out as yesterday.

    function dateStr(offsetDays) {
        const d = new Date();
        if (offsetDays) d.setDate(d.getDate() + offsetDays);
        return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    }
    function todayStr() { return dateStr(0); }

    // The start date of a new transaction: today, or, on another month's page,
    // the same day within that month (February 31st is clamped to the month's
    // end). See month.html.

    function defaultTxDate() {
        const main = document.querySelector('main[data-default-month]');
        const ym = main && main.dataset.defaultMonth;
        if (!ym || !/^\d{4}-\d{2}$/.test(ym)) return todayStr();
        const year = parseInt(ym.slice(0, 4), 10), month = parseInt(ym.slice(5, 7), 10);
        const lastDay = new Date(year, month, 0).getDate();
        const day = Math.min(new Date().getDate(), lastDay);
        return ym + '-' + String(day).padStart(2, '0');
    }

    // "Today"/"yesterday" chips: a shortcut for picking the common date without a
    // picker.
    // `.date-quick-chips` and not every `.date-chip`: the filter buttons on the
    // month page wear the same style, and tapping them changed the date in the
    // form and marked them active.
    // `.is-edit` on the sheet: when editing there is no today/yesterday/day
    // before, only the date (Matan, 5.10)

    const modalSheetEl = overlay.querySelector('.modal-sheet');
    const datePick     = document.querySelector('.date-pick');
    const datePickText = document.getElementById('datePickText');
    const HE_LONG_DATE = new Intl.DateTimeFormat('he-IL', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

    function setEditLook(isEdit) {
        if (modalSheetEl) modalSheetEl.classList.toggle('is-edit', isEdit);
        syncDateChips();
    }

    function syncDateChips() {
        let quick = false;
        document.querySelectorAll('.date-quick-chips .date-chip').forEach(function (chip) {
            const ds = dateStr(parseInt(chip.dataset.days, 10));
            const on = ds === txDate.value;
            chip.classList.toggle('active', on);
            quick = quick || on;
            // The date itself under "today"/"yesterday": no need to think what
            // the date is (Matan, 1.11)

            const sub = chip.querySelector('.date-sub');
            if (sub) sub.textContent = parseInt(ds.slice(8, 10), 10) + '.' + parseInt(ds.slice(5, 7), 10);
        });
        if (!datePickText) return;
        const d = /^\d{4}-\d{2}-\d{2}$/.test(txDate.value) ? new Date(txDate.value + 'T00:00:00') : null;
        const editing = modalSheetEl && modalSheetEl.classList.contains('is-edit');
        // When editing, the full date. When adding, "other", or the date itself
        // when one was picked

        datePickText.textContent = !d ? (editing ? 'תאריך אחר' : 'אחר')
            : editing ? HE_LONG_DATE.format(d)
            : quick ? 'אחר' : d.getDate() + '.' + (d.getMonth() + 1);
        if (datePick) datePick.classList.toggle('active', !editing && !!d && !quick);
    }
    // On desktop, tapping the transparent field doesn't open the picker (only
    // its arrow): open it by hand

    txDate.addEventListener('click', function () {
        try { if (txDate.showPicker) txDate.showPicker(); } catch (e) { /* the regular picker */ }
    });
    document.querySelectorAll('.date-quick-chips .date-chip').forEach(function (chip) {
        chip.addEventListener('click', function () {
            txDate.value = dateStr(parseInt(chip.dataset.days, 10));
            syncDateChips();
        });
    });
    txDate.addEventListener('change', syncDateChips);

    /* Settings and the project edit page change categories without leaving the
     * page. The cache here didn't know about it: a category added didn't appear
     * in +, and one deleted did appear, and failed on save. */
    // What the page already fetched on the server arrives inside it
    // (`client_lists` in app.py), with no two extra requests on every page
    // (Matan, 13.10)

    const pageLists = (window.SF_PAGE_DATA && window.SF_PAGE_DATA.lists) || {};
    if (Array.isArray(pageLists.categories)) categoriesCache = pageLists.categories;
    if (Array.isArray(pageLists.members)) membersCache = pageLists.members;

    window.sfForgetCategories = function () {
        categoriesCache = null;
        projectCategoriesCache = {};
    };

    function loadCategories() {
        if (categoriesCache) return Promise.resolve(categoriesCache);
        return window.sfFetchList('/api/categories')
            .then(function (cats) { categoriesCache = cats; return cats; });
    }

    function loadMembers() {
        if (membersCache) return Promise.resolve(membersCache);
        return window.sfFetchList('/api/family/members')
            .then(function (members) { membersCache = members; return members; });
    }

    function loadProjects() {
        if (projectsCache) return Promise.resolve(projectsCache);
        // Finished projects too, marked: an old transaction in one that is
        // being edited stays in it

        return window.sfFetchList('/api/projects?include_archived=1')
            .then(function (projects) { projectsCache = projects; return projects; });
    }

    function loadProjectCategories(projectId, type) {
        const key = projectId + ':' + type;
        if (projectCategoriesCache[key]) return Promise.resolve(projectCategoriesCache[key]);
        return window.sfFetchList('/api/projects/' + projectId + '/categories?type=' + type)
            .then(function (cats) { projectCategoriesCache[key] = cats; return cats; });
    }

    // The category source depends on the project choice: if a project is chosen,
    // its own categories; otherwise the family's regular categories for the
    // current transaction type.

    function refreshCategoryGrid(selectedId, keepOriginal) {
        const projectId = txProject.value;
        // Where the categories come from: the project choice sits far below

        const chosen = projectId && txProject.options[txProject.selectedIndex];
        categoryLabel.textContent = chosen ? 'קטגוריה בפרויקט "' + chosen.textContent + '"' : 'קטגוריה';
        if (projectId) {
            const forType = currentType;
            loadProjectCategories(projectId, forType).then(function (cats) {
                // If the user switched project/type during loading, don't
                // overwrite the grid with the result of an old request
                // (out-of-order race).

                if (txProject.value !== projectId || currentType !== forType) return;
                renderCategoryGrid(cats, selectedId, true, keepOriginal);
            });
        } else {
            const cats = (categoriesCache || []).filter(c => c.type === currentType);
            renderCategoryGrid(cats, selectedId, false, keepOriginal);
        }
    }

    function renderCategoryGrid(cats, selectedId, isProject, keepOriginal) {
        categoryGridIsProject = isProject;
        categoryGrid.innerHTML = '';
        // The current category isn't in the grid (of a type the project no
        // longer tracks): a button of its own, selected, instead of choosing the
        // first one for it. "Uncategorised" is no longer an option: no transaction
        // without a category (test_category_required).

        const keepCurrent = keepOriginal && !!selectedId && !cats.some(c => c.id === selectedId);
        if (keepCurrent) {
            cats = [{ id: selectedId, type: currentType, icon: '•',
                      name: 'הקטגוריה הנוכחית' }].concat(cats);
        }
        if (!cats.length) {
            const empty = document.createElement('p');
            empty.className = 'cat-grid-empty';
            empty.textContent = isProject
                ? 'לפרויקט אין עדיין קטגוריות במחלקה הזאת — אפשר להוסיף בעריכת הפרויקט'
                : 'אין עדיין קטגוריות במחלקה הזאת — אפשר להוסיף בהגדרות';
            categoryGrid.appendChild(empty);
        }
        // The order is set on the server (sort_order), including where "אחר"
        // goes, which can be moved in settings

        cats.forEach(function (cat) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'cat-btn';
            btn.dataset.value = cat.id;
            btn.dataset.type = cat.type;
            btn.innerHTML = `<span class="cat-emoji">${escapeHtml(cat.icon)}</span><span>${escapeHtml(cat.name)}</span>`;
            btn.setAttribute('role', 'radio');
            btn.setAttribute('aria-checked', 'false');
            categoryGrid.appendChild(btn);
        });
        // Default: for income, "משכורת" is chosen if it exists (regardless of
        // display order); in every other case, the first category.

        let fallbackBtn = categoryGrid.querySelector('.cat-btn');
        if (!selectedId && currentType === 'income') {
            const salary = cats.find(c => c.name && c.name.indexOf('משכורת') !== -1);
            const salaryBtn = salary && categoryGrid.querySelector(`[data-value="${salary.id}"]`);
            if (salaryBtn) fallbackBtn = salaryBtn;
        }
        const toSelect = keepCurrent
            ? categoryGrid.querySelector('.cat-btn')
            : ((selectedId && categoryGrid.querySelector(`[data-value="${selectedId}"]`)) || fallbackBtn);
        txCategory.value = '';
        txProjectCategory.value = '';
        if (toSelect) {
            toSelect.classList.add('active');
            toSelect.setAttribute('aria-checked', 'true');
            if (isProject) txProjectCategory.value = toSelect.dataset.value;
            else txCategory.value = toSelect.dataset.value;
        }
    }

    function buildOwnerToggle(selectedOwner, keepOriginal) {
        // "Shared" first in the list so it appears on the right (RTL: the first
        // item in the DOM is shown on the right side)

        const options = [{ value: 'shared', label: 'משותפת' }]
            .concat((membersCache || []).map(m => ({ value: m.id, label: m.name })));
        // Someone who moved to another family is no longer in the list, and
        // without this their transaction became "shared" on every edit. The
        // server accepts an owner that didn't change.

        if (keepOriginal && selectedOwner && !options.some(o => o.value === selectedOwner)) {
            options.push({ value: selectedOwner, label: 'בן משפחה לשעבר' });
        }
        ownerToggle.innerHTML = '';
        options.forEach(function (opt) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'owner-btn';
            btn.dataset.value = opt.value;
            btn.textContent = opt.label;
            btn.setAttribute('role', 'radio');
            btn.setAttribute('aria-checked', 'false');
            ownerToggle.appendChild(btn);
        });
        // Default: "shared" (relevant when adding a new transaction with no
        // existing attribution to keep), not the first family member in the list

        const toSelect = (selectedOwner && ownerToggle.querySelector(`[data-value="${selectedOwner}"]`))
            || ownerToggle.querySelector('[data-value="shared"]')
            || ownerToggle.querySelector('.owner-btn');
        if (toSelect) {
            toSelect.classList.add('active');
            toSelect.setAttribute('aria-checked', 'true');
            txOwner.value = toSelect.dataset.value;
        }
    }

    function buildProjectSelect(selectedProjectId, keepOriginal) {
        const trackKey = { expense: 'track_expense', income: 'track_income', savings: 'track_savings' }[currentType];
        // A project that stopped tracking the type isn't offered for a new
        // transaction, but a transaction already in it stays in it. Otherwise
        // editing its amount moved it out to household expenses.
        // A finished project isn't offered for a new transaction; only a
        // transaction already in it stays in it

        const projects = (projectsCache || []).filter(p => (p[trackKey] && !p.archived)
            || (keepOriginal && p.id === selectedProjectId));

        // The field is hidden when there is nothing to attach to.
        //
        // It sat in **second** place in the modal, right after the amount, and a
        // family with no projects saw a dropdown with one option: "none". This is
        // the action people do most in the app, and the first field they met in
        // it was a field irrelevant to them.
        //
        // The "whose?" picker right below already does exactly this (see
        // `ownerGroup` in `setType`): the pattern existed and wasn't applied here.

        projectGroup.style.display = projects.length ? '' : 'none';
        if (!projects.length) { txProject.value = ''; return; }

        txProject.innerHTML = '<option value="">ללא</option>' +
            projects.map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.name)}</option>`).join('');
        txProject.value = projects.some(p => p.id === selectedProjectId) ? selectedProjectId : '';
    }

    let modalLastFocused = null;
    let resetSheetDrag = function () {};   // set with the pull-to-close, below

    function openModal() {
        resetSheetDrag();
        loadDescriptions();
        hideSuggest();
        modalLastFocused = document.activeElement;
        if (typeof syncInstallments === 'function') syncInstallments();   // installments: only when adding an expense
        overlay.classList.add('open');
        document.body.style.overflow = 'hidden';
        // On the next frame: the modal is hidden with visibility when closed (so
        // it isn't in the tab order or the screen reader), and an element inside
        // a hidden parent can't be focused. Focus on the same frame the class was
        // added was silently swallowed.
        // On a phone, no keyboard on open (Matan, 12.10: "the keyboard hides the
        // whole sheet"). See the whole sheet first, and tap the amount when ready.
        // Focus moves to the sheet itself, so the screen reader and Tab start
        // from it and not from the + behind it.

        requestAnimationFrame(function () {
            if (window.matchMedia && window.matchMedia('(pointer: coarse)').matches) {
                modalSheetEl.focus({ preventScroll: true });
            } else {
                txAmount.focus();
            }
        });
    }

    // Every focusable element inside the currently open modal, recomputed each
    // time because the content (fields, delete button) changes between adding
    // and editing

    function getModalFocusables() {
        return Array.prototype.slice.call(
            // [tabindex] is included because the save button is a
            // <label role="button">; tabindex="-1"/aria-hidden are filtered out so
            // the focus trap doesn't land on the hidden controls (the haptic
            // switch, the backup submit button)

            overlay.querySelectorAll('button, input, select, textarea, a[href], [tabindex]')
        ).filter(function (el) {
            return el.offsetParent !== null
                && !el.disabled
                && el.getAttribute('tabindex') !== '-1'
                && el.getAttribute('aria-hidden') !== 'true';
        });
    }

    /* The amount in the centre, with the ₪ next to it (Matan, 11.10): the field is
     * as wide as what is written in it. `field-sizing` isn't in every browser yet,
     * so width in characters, after every change. The text is aligned to the ₪
     * side, so the extra space (a period is narrower than a character) stays on
     * the other side. */
    /* ── Commas in the amount while typing (Matan, 12.10: "48000": easy to miss a
     * zero) ──
     * The field is text; the numeric value always goes through `amountValue()`.
     * `formatAmount` keeps digits and one period, up to two digits after the
     * period, and inserts thousands separators, keeping the caret after the same
     * digit. */
    function amountValue() {
        return parseFloat(String(txAmount.value).replace(/,/g, ''));
    }
    function groupThousands(intPart) {
        return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    }
    function formatAmount(keepCaret) {
        const raw = String(txAmount.value);
        const caret = keepCaret ? txAmount.selectionStart : null;
        // How many "significant characters" (digit / period) are before the caret

        const before = caret == null ? 0 : raw.slice(0, caret).replace(/[^\d.]/g, '').length;
        let clean = raw.replace(/[^\d.]/g, '');
        const dot = clean.indexOf('.');
        if (dot !== -1) clean = clean.slice(0, dot + 1) + clean.slice(dot + 1).replace(/\./g, '').slice(0, 2);
        let [intPart, dec] = clean.split('.');
        intPart = intPart.replace(/^0+(?=\d)/, '');
        const out = groupThousands(intPart) + (dec !== undefined ? '.' + dec : '');
        if (out === raw) return;
        txAmount.value = out;
        if (caret != null) {
            let seen = 0, pos = 0;
            while (pos < out.length && seen < before) { if (/[\d.]/.test(out[pos])) seen++; pos++; }
            try { txAmount.setSelectionRange(pos, pos); } catch (e) { /* not focused */ }
        }
    }

    function fitAmount() {
        formatAmount(false);        // a value written by code (edit, scan) gets commas too
        const text = txAmount.value || txAmount.placeholder || '0';
        txAmount.style.width = (text.length + 0.3) + 'ch';
    }

    function markFromReceipt(fields) {
        clearFromReceipt();
        fields.forEach(function (el) { if (el) el.classList.add('from-receipt'); });
        const wrap = txAmount.closest('.amount-input-wrap');
        if (wrap && !wrap.querySelector('.from-receipt-tag')) {
            const tag = document.createElement('span');
            tag.className = 'from-receipt-tag';
            tag.textContent = 'מהקבלה';
            wrap.appendChild(tag);
        }
    }
    function clearFromReceipt(el) {
        const scope = el ? [el] : document.querySelectorAll('#modalOverlay .from-receipt');
        Array.prototype.forEach.call(scope, function (x) { x.classList.remove('from-receipt'); });
        if (!el || el === txAmount) {
            const tag = document.querySelector('#modalOverlay .from-receipt-tag');
            if (tag) tag.remove();
        }
    }
    ['input', 'change'].forEach(function (ev) {
        txForm.addEventListener(ev, function (e) {
            const t = e.target.closest && (e.target.closest('.date-pick') || e.target);
            if (t && t.classList && t.classList.contains('from-receipt')) clearFromReceipt(t);
        });
    });

    function resetScanUI() {
        // `txReceiptPath` **must** be reset here and not only in `resetForm`.
        // `closeModal` calls only here, and `resetForm` runs only from
        // `openAddModal`, so an abandoned scan (✕ instead of save) left the path
        // in the field, and the next edit of an **existing transaction** sent it
        // and got someone else's receipt. Worse: two rows pointing at the same
        // file, and deleting one deletes the other's file.

        txReceiptPath.value = '';
        scanWrapper.classList.remove('active');
        scanBtn.disabled = false;
        scanBtn.querySelector('.scan-btn-text').textContent = 'סריקת קבלה';
        receiptInput.value = '';
        if (scannerThumbnail.src) {
            URL.revokeObjectURL(scannerThumbnail.src);
            scannerThumbnail.removeAttribute('src');
        }
        txAmount.placeholder = AMOUNT_PLACEHOLDER_DEFAULT;
        txDescription.placeholder = DESCRIPTION_PLACEHOLDER_DEFAULT;
        clearFromReceipt();
        fitAmount();
    }

    function closeModal() {
        overlay.classList.remove('open');
        document.body.style.overflow = '';
        scanSeq++;                              // a scan still running: no longer any form's
        discardReceipt(txReceiptPath.value);    // scanned and not saved
        resetScanUI();
        formError.textContent = '';
        if (modalLastFocused) { modalLastFocused.focus(); modalLastFocused = null; }
        refreshIfPending();
    }

    function resetForm() {
        txForm.reset();
        fitAmount();
        txDate.value = defaultTxDate();
        txReceiptPath.value = '';
        formError.textContent = '';
        recurFields.classList.remove('visible');
        resetScanUI();
        syncDateChips();
        if (typeof syncInstallments === 'function') requestAnimationFrame(syncInstallments);
    }

    /* ── "Save changes" only when something changed (Matan, 12.10) ──
     * A snapshot of the form at the moment editing opened; as long as the form
     * matches it, the button is grey (same label), and a tap doesn't save. Going
     * back to the original value: grey again. */
    let editSnapshot = null;
    function formSnapshot() {
        return JSON.stringify([amountValue() || 0, currentType, txCategory.value, txProjectCategory.value,
            txDescription.value.trim(), txDate.value, txOwner ? txOwner.value : '', recurringCb.checked,
            recurringCb.checked ? txFrequency.value : '', recurringCb.checked ? txEndDate.value : '',
            txReceiptPath.value, txProject.value]);
    }
    function isUnchanged() {
        return !!editId && editSnapshot !== null && formSnapshot() === editSnapshot;
    }

    function updateSubmitLabel() {
        const same = isUnchanged();
        submitBtn.classList.toggle('is-unchanged', same);
        if (same) submitBtn.setAttribute('aria-disabled', 'true');
        else submitBtn.removeAttribute('aria-disabled');
        // Always the same label, only disabled or enabled (Matan, 13.10: "don't
        // have it say no changes")

        if (editId) { submitLabel.textContent = 'שמירת השינויים'; return; }
        submitLabel.textContent = TYPE_LABELS[currentType];
    }

    /* `keepOriginal`: the first fill of an edit form. A value already on the
     * transaction is kept even when it isn't in the list of options: a
     * transaction without a category, of someone who moved to another family, or
     * in a project that stopped tracking the type. Until now the form chose for
     * it (the first category, "shared", "none"), and saving a change to the
     * amount silently changed them too. When the user switches type themselves,
     * `keepOriginal` is off. */
    function setType(type, selectedCategoryId, selectedOwner, selectedProjectId, keepOriginal) {
        currentType = type;
        toggleExp.classList.toggle('active', type === 'expense');
        toggleInc.classList.toggle('active', type === 'income');
        toggleSav.classList.toggle('active', type === 'savings');
        toggleExp.setAttribute('aria-pressed', type === 'expense');
        toggleInc.setAttribute('aria-pressed', type === 'income');
        toggleSav.setAttribute('aria-pressed', type === 'savings');
        const modalSheet = document.querySelector('.modal-sheet');
        modalSheet.classList.toggle('income-mode', type === 'income');
        modalSheet.classList.toggle('expense-mode', type === 'expense');
        modalSheet.classList.toggle('savings-mode', type === 'savings');
        buildProjectSelect(selectedProjectId, keepOriginal);
        refreshCategoryGrid(selectedCategoryId, keepOriginal);
        // The "whose?" picker appears only for types the family turned
        // attribution on for (family settings), and not when the family is one
        // person (Matan, 5.10). Then it is only hidden: the choice is built and
        // sent as always; without it the server would record a transaction that
        // was shared under the signed-in user.

        const hasOwner = !!window.SF_ATTRIBUTION[type];
        ownerGroup.style.display = (hasOwner && !window.SF_PAGE_DATA.singleMember) ? '' : 'none';
        if (hasOwner) {
            document.getElementById('ownerLabel').textContent = OWNER_LABELS[type];
            buildOwnerToggle(selectedOwner, keepOriginal);
        }
        // Receipt scanning exists only when adding a new expense: not when
        // editing and not for income/savings

        scanBtn.style.display = (!editId && type === 'expense') ? '' : 'none';
        updateSubmitLabel();
    }

    // Shows soft loading feedback on the element that opened the modal (FAB /
    // transaction row); without this, on a slow connection it looks as if the
    // tap didn't respond until the modal opened

    function withLoadingTrigger(el, promise) {
        if (el) el.classList.add('is-loading-trigger');
        return promise.finally(function () {
            if (el) el.classList.remove('is-loading-trigger');
        });
    }

    /* Locks the "recurring transaction" checkbox on an occurrence that already
     * belongs to a series, and explains why. Without this the checkbox looks
     * fully available, and ticking it (a reasonable reading of "make it
     * recurring from now on") would create a duplicate series. */
    function setRecurringLock(locked) {
        recurringCb.disabled = locked;
        const group = recurringCb.closest('.recurring-group');
        if (group) group.classList.toggle('is-locked', locked);
        let note = document.getElementById('recurringLockNote');
        if (locked && !note && group) {
            note = document.createElement('p');
            note.id = 'recurringLockNote';
            note.className = 'field-hint';
            note.textContent = 'זו עסקה קבועה. '
                             + 'לשינוי שלה מהחודש הנוכחי והלאה — הגדרות ← עסקאות קבועות.';
            group.appendChild(note);
        } else if (!locked && note) {
            note.remove();
        }
    }

    // `presetType`: a type the sheet opens on: "+ add" on an empty row on the
    // month page (Matan, 11.10). From the FAB the tap event arrives here, so only
    // a string counts.

    function openAddModal(presetType) {
        const preset = typeof presetType === 'string' ? presetType : null;
        editSnapshot = null;
        const seriesLink = document.getElementById('txSeriesLink');
        if (seriesLink) seriesLink.hidden = true;
        const receiptLink = document.getElementById('txReceiptLink');
        if (receiptLink) receiptLink.hidden = true;
        scanSeq++;
        formSeq++;
        setSubmitBusy(false);                   // a new form isn't "mid-save"; see `finish`
        editId = null;
        editingRecurringParentId = null;
        editingSeries = false;
        originalAmount = null;
        modalTitle.textContent = 'הוספת עסקה';
        setEditLook(false);
        editModeActions.style.display = 'none';
        if (enteredMeta) enteredMeta.hidden = true;
        setRecurringLock(false);
        resetForm();
        const mySeq = formSeq;
        withLoadingTrigger(fabBtn, Promise.all([loadCategories(), loadMembers(), loadProjects()]))
            .then(function () {
                if (mySeq !== formSeq) return;      // a transaction was tapped meanwhile: it decides
                // On a project page: the project is already selected, and the
                // type is one it tracks: an income-only project doesn't open on
                // "expense" (idea 8)

                const here = document.getElementById('sfPageProject');
                const project = here && (projectsCache || [])
                    .find(function (p) { return p.id === here.dataset.projectId && !p.archived; });
                const type = preset ? preset
                           : !project || project.track_expense ? 'expense'
                           : project.track_income ? 'income' : 'savings';
                setType(type, undefined, undefined, project ? project.id : undefined);
                openModal();
            })
            .catch(function () {
                if (mySeq === formSeq) window.showToast(window.sfNetError(), 'error');
            });
    }

    // ── "הוזנה ע״י אור · אתמול 18:32" at the bottom of the edit sheet (Matan, 3.10) ──

    const enteredMeta = document.getElementById('txEnteredMeta');
    function loadEnteredMeta(id) {
        if (!enteredMeta) return;
        enteredMeta.hidden = true;
        enteredMeta.textContent = '';
        const mySeq = formSeq;
        fetch('/api/transactions/' + encodeURIComponent(id) + '/meta', { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                // Another form opened meanwhile: the line isn't its

                if (mySeq !== formSeq || !d || !d.text) return;
                enteredMeta.textContent = d.text;
                enteredMeta.hidden = false;
            })
            .catch(function () { /* an info line: without it the sheet works as usual */ });
    }

    function openEditModal(tx, triggerEl) {
        scanSeq++;
        formSeq++;
        setSubmitBusy(false);                   // a new form isn't "mid-save"; see `finish`
        txReceiptPath.value = '';               // don't inherit a receipt from a previous form
        editId = tx.id;
        editingRecurringParentId = tx.recurringParentId || null;
        editingSeries = !!(triggerEl && triggerEl.closest && triggerEl.closest('#recurringList'));
        // From "recurring transactions" the button stops the series and doesn't
        // delete a row; see `deleteBtn` below. The name says what it does.

        deleteBtn.textContent = editingSeries ? 'הסרת העסקה הקבועה' : 'מחיקת העסקה';
        // A recurring transaction has no "duplicate" (Matan, 12.10): it already
        // records itself every month. Both the template (from settings) and an
        // occurrence of a series.

        duplicateBtn.hidden = !!(tx.isRecurring || tx.recurringParentId || editingSeries);
        // "Part of a series": on an occurrence only, not on the template itself

        const seriesLink = document.getElementById('txSeriesLink');
        if (seriesLink) {
            seriesLink.hidden = !(tx.recurringParentId && !editingSeries);
            if (tx.recurringParentId) seriesLink.href = '/settings?series=' + encodeURIComponent(tx.recurringParentId);
        }
        // The row's receipt, if any (`.receipt-badge` inside it)

        const receiptLink = document.getElementById('txReceiptLink');
        if (receiptLink) {
            const row = triggerEl && triggerEl.closest && triggerEl.closest('[data-id]');
            const badge = row && row.querySelector('.receipt-badge[data-href]');
            receiptLink.hidden = !badge;
            if (badge) receiptLink.href = badge.dataset.href;
        }
        editingOriginal = tx;
        originalAmount = parseFloat(tx.amount);
        modalTitle.textContent = 'עריכת עסקה';
        setEditLook(true);
        editModeActions.style.display = 'flex';
        loadEnteredMeta(tx.id);
        formError.textContent = '';

        /* The last tap decides. The form fills only when the lists arrive, and
         * `editId` is set right away, so tapping A and immediately B on a slow
         * network filled the form with A's data and saved it onto B. And a
         * transaction then immediately + filled the "new transaction" form with
         * its data, and saving created a copy. */
        const mySeq = formSeq;
        withLoadingTrigger(triggerEl, Promise.all([loadCategories(), loadMembers(), loadProjects()]))
            .then(function () {
                if (mySeq !== formSeq) return;
                const selectedCategoryId = tx.projectId ? tx.projectCategoryId : tx.categoryId;
                setType(tx.type, selectedCategoryId, tx.userId || 'shared', tx.projectId, true);

                txAmount.value       = plainAmount(tx.amount);
                fitAmount();
                txDescription.value  = tx.description || '';
                txDate.value         = tx.date;
                syncDateChips();
                recurringCb.checked  = tx.isRecurring;
                recurFields.classList.toggle('visible', tx.isRecurring);
                if (tx.isRecurring) {
                    txFrequency.value = tx.recurringFrequency || 'monthly_1';
                    txEndDate.value   = tx.recurringEndDate || '';
                }
                // An occurrence already created from a series: marking it
                // "recurring" would create a second template running alongside
                // the first. The server blocks it anyway; here we just don't
                // offer an action that would fail.

                setRecurringLock(!!tx.recurringParentId);
                editSnapshot = formSnapshot();
                updateSubmitLabel();
                openModal();
            })
            .catch(function () {
                if (mySeq === formSeq) window.showToast(window.sfNetError(), 'error');
            });
    }

    fabBtn.addEventListener('click', openAddModal);
    // "+ add" on an empty row on the month page: the sheet opens on the row's
    // type. Delegated from document: a soft refresh replaces the row.

    document.addEventListener('click', function (e) {
        const btn = e.target.closest('[data-add-type]');
        if (btn) openAddModal(btn.dataset.addType);
    });

    // Warming the cache on the home page: the categories and family members are
    // fetched in the background after the page settled, so even the first open
    // of the sheet is instant.

    if (document.querySelector('.transactions-list')) {
        const warm = function () { loadCategories(); loadMembers(); };
        if ('requestIdleCallback' in window) requestIdleCallback(warm, { timeout: 2500 });
        else setTimeout(warm, 1200);
    }

    // Auto-opening the add modal through the PWA shortcut "add transaction"
    // (`/?add=1`). The parameter is removed from the URL so a refresh doesn't
    // open the modal again.

    if (new URLSearchParams(window.location.search).get('add') === '1') {
        history.replaceState(null, '', window.location.pathname);
        openAddModal();
    }

    closeBtn.addEventListener('click', closeModal);
    overlay.addEventListener('click', function (e) {
        if (e.target === overlay) closeModal();
    });

    // Escape closes the modal, Tab/Shift+Tab stay inside it (focus trap)

    overlay.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { closeModal(); return; }
        if (e.key !== 'Tab') return;
        const focusables = getModalFocusables();
        if (!focusables.length) return;
        const first = focusables[0];
        const last  = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
            e.preventDefault();
            last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
        }
    });

    // Shared physical motion: in core.js

    const sfSpring = window.sfSpring, sfProject = window.sfProject,
          sfRubber = window.sfRubber, sfVelocity = window.sfVelocity;

    /* ── Pulling down closes the sheet (Matan, 7.10, Apple-style design, item 1) ──
     *
     * The grabber at the top of the sheet did nothing, and on iPhone every such
     * sheet closes with a pull. The sheet moves with the finger one to one. On
     * release the decision is by **where the motion is heading** and not only
     * where it stopped: the velocity is projected forward (Apple's scrolling
     * formula), so a short fast flick closes, and a long slow drag that comes
     * back up doesn't. Above its place it resists gradually instead of stopping
     * all at once.
     *
     * The animation after release is a spring continuing at the finger's
     * velocity, with no seam between the drag and the motion. And touching the
     * sheet mid-return catches it from where it really is. */
    (function () {
        const sheet = modalSheetEl;
        if (!sheet) return;
        const SCRIM = 0.6;                       // the dark backdrop's opacity, as in `.modal-overlay`
        const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        let y = 0, anim = null, cleanup = 0, g = null;

        function paint(v) {
            y = v;
            sheet.style.transform = 'translateY(' + v + 'px)';
            const p = Math.min(Math.max(v / sheet.offsetHeight, 0), 1);
            overlay.style.backgroundColor = 'rgba(20, 16, 7, ' + (SCRIM * (1 - p)).toFixed(3) + ')';
        }
        function clearInline() {
            sheet.style.transition = sheet.style.transform = '';
            overlay.style.backgroundColor = '';
            y = 0;
        }
        function stop() {
            if (anim) { anim(); anim = null; }
            clearTimeout(cleanup); cleanup = 0;
        }
        // A sheet reopened starts clean, even if the previous close hasn't
        // finished cleaning up

        resetSheetDrag = function () { stop(); clearInline(); };

        function spring(to, v0, response, damping, done, until) {
            stop();
            anim = sfSpring(y, to, v0, response, damping, paint,
                            function () { anim = null; done(); }, until);
        }

        function snapBack(v) {
            if (calm) { stop(); clearInline(); return; }
            spring(0, v, 0.3, 0.8, clearInline);
        }
        function dismiss(v) {
            const h = sheet.offsetHeight;
            function offScreen() {
                closeModal();
                // The backdrop fades now; the sheet is already off screen. After
                // the fade, quietly put it back into the CSS closed state, with no
                // visible motion.

                cleanup = setTimeout(function () {
                    cleanup = 0;
                    clearInline();
                    sheet.style.transition = 'none';
                    void sheet.offsetHeight;          // set without a transition, and only then the transition returns
                    sheet.style.transition = '';
                }, 300);
            }
            if (calm) { offScreen(); return; }
            spring(h + 40, Math.max(v, 600), 0.3, 1, offScreen, function (x) { return x >= h; });
        }

        overlay.addEventListener('touchstart', function (e) {
            if (!overlay.classList.contains('open') || e.touches.length !== 1) return;
            if (!sheet.contains(e.target)) return;
            const t = e.touches[0];
            g = { x0: t.clientX, y0: t.clientY, from: 0, axis: null, samples: [] };
            if (anim) {
                // Caught mid-motion: continue from where the sheet really is

                stop();
                g.axis = 'y';
                g.from = y;
                sheet.style.transition = 'none';
            }
            g.samples.push({ t: e.timeStamp, p: t.clientY });
        }, { passive: true });

        overlay.addEventListener('touchmove', function (e) {
            if (!g) return;
            const t = e.touches[0];
            const dx = t.clientX - g.x0, dy = t.clientY - g.y0;
            if (!g.axis) {
                if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
                // Sideways (the category strips), or scrolling the sheet's
                // content that still has somewhere to scroll: ordinary scrolling,
                // not a pull. Down: only from the top of the content; up: only when
                // the content is already at its end: then the sheet stretches up
                // with resistance and springs back (Matan, 11.10; until then it
                // didn't move at all).

                const atEnd = sheet.scrollTop + sheet.clientHeight >= sheet.scrollHeight - 1;
                if (Math.abs(dx) > Math.abs(dy) || (dy > 0 && sheet.scrollTop > 0) || (dy < 0 && !atEnd)) {
                    g = null; return;
                }
                g.axis = 'y';
                g.y0 += dy > 0 ? 8 : -8;            // without the 8-pixel jump of the decision
                sheet.style.transition = 'none';
            }
            e.preventDefault();                    // the sheet moves, not the content inside it
            const raw = g.from + t.clientY - g.y0;
            paint(raw >= 0 ? raw : sfRubber(raw, sheet.offsetHeight));
            g.samples.push({ t: e.timeStamp, p: t.clientY });
            if (g.samples.length > 6) g.samples.shift();
        }, { passive: false });

        function release(e, cancelled) {
            if (!g) return;
            const dragged = g.axis === 'y';
            const s = g.samples;
            g = null;
            if (!dragged) return;
            if (cancelled) { snapBack(0); return; }
            const v = sfVelocity(s, e.timeStamp);
            const h = sheet.offsetHeight;
            if (v >= 0 && y + sfProject(v) > Math.min(h / 2, 240)) dismiss(v);
            else snapBack(v);
        }
        overlay.addEventListener('touchend', function (e) { release(e, false); }, { passive: true });
        overlay.addEventListener('touchcancel', function (e) { release(e, true); }, { passive: true });
    })();

    // Switching type keeps the chosen project, if it tracks the new type too
    // (`buildProjectSelect` drops it if not). Before, every switch reset it.

    toggleExp.addEventListener('click', () => setType('expense', undefined, undefined, txProject.value));
    toggleInc.addEventListener('click', () => setType('income', undefined, undefined, txProject.value));
    toggleSav.addEventListener('click', () => setType('savings', undefined, undefined, txProject.value));

    // Owner toggle
    ownerToggle.addEventListener('click', function (e) {
        const btn = e.target.closest('.owner-btn');
        if (!btn) return;
        ownerToggle.querySelectorAll('.owner-btn').forEach(function (b) {
            b.classList.remove('active');
            b.setAttribute('aria-checked', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-checked', 'true');
        txOwner.value = btn.dataset.value;
    });

    // Category grid
    categoryGrid.addEventListener('click', function (e) {
        const btn = e.target.closest('.cat-btn');
        if (!btn) return;
        categoryGrid.querySelectorAll('.cat-btn').forEach(function (b) {
            b.classList.remove('active');
            b.setAttribute('aria-checked', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-checked', 'true');
        if (categoryGridIsProject) {
            txProjectCategory.value = btn.dataset.value;
            txCategory.value = '';
        } else {
            txCategory.value = btn.dataset.value;
            txProjectCategory.value = '';
        }
    });

    // Changing the project choice refreshes the category source (project or
    // family categories).
    // The field is at the bottom of the form, and the categories above it switch
    // to the project's categories, so take the user to them, instead of leaving
    // them with a household category no longer selected and a new grid they
    // don't see

    txProject.addEventListener('change', function () {
        refreshCategoryGrid();
        const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        categoryLabel.scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'start' });
        categoryGrid.classList.remove('flash');
        void categoryGrid.offsetWidth;
        categoryGrid.classList.add('flash');
    });

    // Recurring
    recurringCb.addEventListener('change', function () {
        recurFields.classList.toggle('visible', this.checked);
        if (this.checked && instCb && instCb.checked) { instCb.checked = false; syncInstallments(); }
    });

    // ── Installments (Matan, 12.10) ──
    // The amount is the full price; the server splits it (`db.installment_plan`):
    // the same calculation here only to show in advance: "₪400 a month · until
    // September 2027". Installments and a recurring transaction not together:
    // installments already are a recurring transaction with an end.

    const instGroup = document.getElementById('installmentsGroup');
    const instCb    = document.getElementById('txInstallments');
    const instCount = document.getElementById('txInstallmentCount');
    const instHint  = document.getElementById('installmentsHint');
    const instFields = document.getElementById('installmentsFields');
    const MONTH_NAMES = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני', 'יולי',
                         'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר'];
    function installmentCount() {
        const n = parseInt(instCount.value, 10);
        return n >= 2 && n <= 60 ? n : null;
    }
    function syncInstallments() {
        if (!instGroup) return;
        // Only when adding an expense

        instGroup.hidden = !!editId || currentType !== 'expense';
        if (instGroup.hidden && instCb.checked) instCb.checked = false;
        instFields.hidden = !instCb.checked;
        if (!instCb.checked) { instHint.textContent = ''; return; }
        if (recurringCb.checked) { recurringCb.checked = false; recurFields.classList.remove('visible'); }
        const n = installmentCount(), total = amountValue();
        if (!n || !total) { instHint.textContent = n ? '' : 'בין 2 ל-60 תשלומים'; return; }
        const cents = Math.round(total * 100), base = Math.floor(cents / n), first = cents - base * (n - 1);
        if (base <= 0) { instHint.textContent = 'הסכום קטן מדי לחלוקה'; return; }
        const start = txDate.value ? new Date(txDate.value + 'T00:00:00') : new Date();
        const end = new Date(start.getFullYear(), start.getMonth() + n - 1, 1);
        const money = c => '₪' + window.sfMoney(c / 100);
        instHint.textContent = (first !== base ? money(first) + ' ואז ' : '') + money(base) + ' בחודש · עד '
            + MONTH_NAMES[end.getMonth()] + ' ' + end.getFullYear();
    }
    if (instGroup) {
        [instCb, instCount].forEach(el => el.addEventListener('input', syncInstallments));
        instCb.addEventListener('change', syncInstallments);
        txDate.addEventListener('change', syncInstallments);
        txForm.addEventListener('click', function (e) {
            if (e.target.closest('.type-toggle, .date-chip')) requestAnimationFrame(syncInstallments);
        });
    }

    // ── Receipt scanning: a tap opens the camera/file picker ──

    scanBtn.addEventListener('click', function () {
        receiptInput.click();
    });

    // Compress on the client before uploading: a smaller file is cheaper and
    // faster. WebP at 80% quality gives a much smaller file than JPEG; old
    // browsers that don't support toBlob('image/webp') return an empty blob, and
    // then fall back to JPEG at the same quality.

    function compressImage(file) {
        return new Promise(function (resolve) {
            const img = new Image();
            const reader = new FileReader();
            reader.onload = function () { img.src = reader.result; };
            img.onload = function () {
                const MAX = 1200;
                let w = img.width, h = img.height;
                if (w > MAX || h > MAX) {
                    const scale = MAX / Math.max(w, h);
                    w = Math.round(w * scale);
                    h = Math.round(h * scale);
                }
                const canvas = document.createElement('canvas');
                canvas.width = w;
                canvas.height = h;
                canvas.getContext('2d').drawImage(img, 0, 0, w, h);
                canvas.toBlob(function (webpBlob) {
                    if (webpBlob && webpBlob.size > 0) {
                        resolve({ blob: webpBlob, ext: 'webp' });
                    } else {
                        canvas.toBlob(function (jpegBlob) {
                            resolve({ blob: jpegBlob || file, ext: 'jpg' });
                        }, 'image/jpeg', 0.8);
                    }
                }, 'image/webp', 0.8);
            };
            img.onerror = function () { resolve({ blob: file, ext: 'jpg' }); };
            reader.readAsDataURL(file);
        });
    }

    receiptInput.addEventListener('change', function () {
        const file = this.files[0];
        if (!file) return;

        // Show an instant preview and "reading data…" in the fields, without
        // blocking the form: the user can keep typing by hand while the server works

        scannerThumbnail.src = URL.createObjectURL(file);
        scanWrapper.classList.add('active');
        scanBtn.disabled = true;
        scanBtn.querySelector('.scan-btn-text').textContent = 'סורק…';
        txAmount.placeholder = 'מפענח נתונים…';
        fitAmount();
        txDescription.placeholder = 'מפענח נתונים…';
        formError.textContent = '';

        const mySeq = ++scanSeq;
        compressImage(file).then(function (result) {
            const formData = new FormData();
            formData.append('image', result.blob, 'receipt.' + result.ext);
            return fetch('/api/receipts/scan', { method: 'POST', body: formData });
        })
        .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
        .then(function (result) {
            // The form was closed or replaced during reading: don't touch it (not
            // even its scan UI), and the uploaded image is deleted.

            if (mySeq !== scanSeq) {
                if (result.ok && result.data) discardReceipt(result.data.receipt_path);
                return;
            }
            // A second scan in the same form: the previous receipt won't be saved

            const previous = txReceiptPath.value;
            resetScanUI();
            if (!result.ok || result.data.error) {
                formError.textContent = (result.data && result.data.error) || 'לא הצלחנו לקרוא את הקבלה — נסו שוב או הזינו ידנית';
                return;
            }
            const data = result.data;
            txAmount.value = data.amount;
            fitAmount();
            if (data.merchant) txDescription.value = data.merchant;
            if (data.date) txDate.value = data.date;
            if (previous && previous !== data.receipt_path) discardReceipt(previous);
            txReceiptPath.value = data.receipt_path || '';
            if (data.category_id) {
                const catBtn = categoryGrid.querySelector(`[data-value="${data.category_id}"]`);
                if (catBtn) {
                    categoryGrid.querySelectorAll('.cat-btn').forEach(b => b.classList.remove('active'));
                    catBtn.classList.add('active');
                    txCategory.value = catBtn.dataset.value;
                }
            }
            scanBtn.querySelector('.scan-btn-text').textContent = '✓ סריקה הושלמה';
            // "From the receipt" (Matan, 12.10): a brief gold frame on what the
            // scan filled, and a label next to the amount, so it is clear what to
            // check. Goes away when the field is changed.

            markFromReceipt([txAmount, data.merchant && txDescription, data.date && document.querySelector('#modalOverlay .date-pick')]);
            window.showToast('הנתונים פוענחו מהקבלה — בדקו את מה שמסומן');
        })
        .catch(function () {
            if (mySeq !== scanSeq) return;
            resetScanUI();
            formError.textContent = window.sfNetError() + '. אפשר גם להזין ידנית.';
        });
    });

    // Submit → POST (add) or PUT (edit)
    // Builds a temporary "recent transaction" row on the home page, before the
    // server even answered. Improves the feeling of speed when adding only (not
    // editing): the exact details (coloured attribution etc.) arrive with the
    // full refresh that happens on success anyway, so this isn't a real state
    // update, just a fast partial preview.

    function buildPlaceholderTxRow(amount) {
        const li = document.createElement('li');
        // No data-id: keeps the temporary row non-interactive (not tappable or
        // swipeable) as long as it hasn't become a real transaction with an id
        // from the server: prevents an edit/delete attempt on a "pending" that
        // doesn't exist in the DB

        li.className = 'transaction-item tx-pending';

        const activeCatBtn = categoryGrid.querySelector('.cat-btn.active');
        const icon = activeCatBtn ? (activeCatBtn.querySelector('.cat-emoji') || {}).textContent : '';
        const nameSpan = activeCatBtn ? activeCatBtn.querySelectorAll('span')[1] : null;
        const name = nameSpan ? nameSpan.textContent : 'אחר';

        const sign = currentType === 'expense' ? '-' : (currentType === 'income' ? '+' : '');
        const desc = txDescription.value.trim();

        li.innerHTML =
            '<div class="tx-head">' +
                '<div class="tx-icon ' + currentType + '"><span class="tx-emoji">' + escapeHtml(icon || '📦') + '</span></div>' +
                '<div class="tx-details">' +
                    '<p class="tx-name"></p>' +
                    '<p class="tx-meta"></p>' +
                '</div>' +
                '<div class="tx-side"><p class="tx-amount ' + currentType + '"></p></div>' +
            '</div>';
        li.querySelector('.tx-name').textContent = name;
        // The day inside the row, as in rows from the server (Matan, 30.9)

        const day = txDate.value === todayStr() ? 'היום'
                  : txDate.value === dateStr(-1) ? 'אתמול'
                  : (txDate.value || '').slice(8, 10).replace(/^0/, '') + '.' + (txDate.value || '').slice(5, 7).replace(/^0/, '');
        li.querySelector('.tx-meta').textContent = day + (desc ? ' · ' + desc : '');
        li.querySelector('.tx-amount').textContent = sign + '₪' + window.sfMoney(amount);
        return li;
    }

    // Clearing the amount field's error mark the moment it is fixed (accessibility)

    txAmount.addEventListener('input', function () {
        txAmount.removeAttribute('aria-invalid');
        formatAmount(true);
        fitAmount();
        if (typeof syncInstallments === 'function') syncInstallments();   // "₪400 a month"
    });

    // The save button is a <label>, not a <button>; see the note next to the
    // markup. The cost: no real disabled, so double taps are blocked with a
    // flag, and the visual disabling is done with a class.

    let isSubmitting = false;

    function setSubmitBusy(busy) {
        isSubmitting = busy;
        submitBtn.classList.toggle('is-busy', busy);
        submitBtn.setAttribute('aria-disabled', busy ? 'true' : 'false');
    }

    // ── "Undo" after an edit (Matan, 30.9, round 6, item 10) ──
    // Undo is another edit with the previous values, and with `if_match`: what
    // our edit left. If someone changed the transaction since, the server
    // returns 409 and doesn't overwrite.
    // Kept in sessionStorage because most pages do a full reload after an edit.

    const UNDO_KEY = 'sf_undo_edit';
    function undoSpec(before, after, message) {
        return {
            id: before.id,
            message: message,
            body: {
                amount: parseFloat(before.amount), type: before.type,
                category_id: before.projectId ? null : (before.categoryId || null),
                project_category_id: before.projectId ? (before.projectCategoryId || null) : null,
                description: before.description || '', date: before.date,
                owner: before.userId || 'shared',
                is_recurring: !!before.isRecurring,
                recurring_frequency: before.isRecurring ? (before.recurringFrequency || null) : null,
                recurring_end_date: before.isRecurring ? (before.recurringEndDate || null) : null,
                project_id: before.projectId || null,
            },
            if_match: {
                amount: after.amount, type: after.type, date: after.date,
                description: after.description || '', category_id: after.category_id || null,
                project_category_id: after.project_category_id || null, user_id: after.user_id || null,
            },
        };
    }
    function rememberUndo(spec) {
        try { sessionStorage.setItem(UNDO_KEY, JSON.stringify(spec)); }
        catch (e) { window.sfToastAfterReload(spec.message); }
    }
    function runUndo(spec) {
        fetch('/api/transactions/' + spec.id, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Object.assign({}, spec.body, { if_match: spec.if_match })),
        })
            .then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (d) {
                if (d.error) { window.showToast(d.error, 'error'); return; }
                if (document.querySelector('main[data-soft-reload]')) {
                    window.softReload(null, 'השינוי בוטל').then(function (how) {
                        if (how !== 'reloaded') window.showToast('השינוי בוטל');
                    });
                } else {
                    window.sfToastAfterReload('השינוי בוטל');
                    window.location.reload();
                }
            })
            .catch(function () { window.showToast(window.sfNetError(), 'error'); });
    }
    function showPendingUndo() {
        let spec = null;
        try {
            spec = JSON.parse(sessionStorage.getItem(UNDO_KEY) || 'null');
            sessionStorage.removeItem(UNDO_KEY);
        } catch (e) { return; }
        if (!spec || !spec.id) return;
        window.showToast(spec.message, undefined, { label: 'ביטול', onClick: function () { runUndo(spec); } });
    }
    showPendingUndo();          // after a full reload, the message with "undo" waits here

    function requestSubmit() {
        if (typeof txForm.requestSubmit === 'function') txForm.requestSubmit();
        else txForm.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
    }

    // Touching the button toggles the switch (that is what produces the tap on
    // iOS), and from there we continue to the ordinary submit. The submit
    // depends on the switch changing and not the tap itself, because a label has
    // no "click" event that can be relied on in every browser.

    const hapticSubmit = document.getElementById('hapticSubmit');
    if (hapticSubmit) {
        hapticSubmit.addEventListener('change', function () { requestSubmit(); });
    }

    // Keyboard: a label with role="button" isn't submitted by itself on Enter/space

    submitBtn.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        requestSubmit();
    });

    // ── Before saving a new transaction: a duplicate? an unusual amount? (Matan,
    // 30.9, round 6, 6–7) ──
    // Questions, not blocks. A network failure here doesn't stop a save: better
    // to save without a warning. After confirming, the submit runs again, and
    // `precheckPassed` skips the check for exactly the same data.

    let precheckPassed = null;
    function precheckKey(p) {
        return [formSeq, p.amount, p.type, p.category_id, p.project_category_id, p.date].join('|');
    }
    function chosenCategoryName() {
        const btn = categoryGrid.querySelector('.cat-btn.active');
        const span = btn ? btn.querySelectorAll('span')[1] : null;
        return span ? span.textContent : 'הקטגוריה';
    }
    function dayWord(iso) {
        if (iso === dateStr(0)) return 'היום';
        if (iso === dateStr(-1)) return 'אתמול';
        return iso.slice(8, 10) + '.' + iso.slice(5, 7);
    }
    function runPrecheck(payload) {
        return fetch('/api/transactions/precheck', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
            .then(function (r) { return r.ok ? r.json() : {}; })
            .catch(function () { return {}; })
            .then(function (res) {
                const name = chosenCategoryName();
                let chain = Promise.resolve(true);
                const d = res && res.duplicate;
                if (d) {
                    chain = chain.then(function () {
                        return window.appConfirm({
                            title: 'נראה שהעסקה הזאת כבר קיימת',
                            message: name + ' · ₪' + window.sfMoney(d.amount) + ' · ' + dayWord(d.date)
                                + (d.description ? ' · ' + d.description : '')
                                + (d.by ? '\nהוזנה ע״י ' + d.by + (d.time ? ' ב-' + d.time : '') : ''),
                            confirmText: 'להוסיף בכל זאת',
                            cancelText: 'ביטול',
                            danger: false,
                        }).then(function (ok) { return !!ok; });
                    });
                }
                const u = res && res.unusual;
                if (u) {
                    chain = chain.then(function (ok) {
                        if (!ok) return false;
                        return window.appConfirm({
                            title: '₪' + window.sfMoney(payload.amount) + ' על ' + name + '?',
                            message: 'בדרך כלל אתם ' + (payload.type === 'savings' ? 'מפרישים' : 'מוציאים')
                                + ' שם בין ₪' + window.sfMoney(u.min) + ' ל-₪' + window.sfMoney(u.max),
                            confirmText: 'כן, זה נכון',
                            cancelText: 'לתקן',
                            danger: false,
                        }).then(function (ok2) {
                            if (!ok2) { txAmount.focus(); txAmount.select(); }
                            return !!ok2;
                        });
                    });
                }
                return chain;
            });
    }

    // Every change in the form (including the buttons for type, category, date,
    // whose, which update a hidden field on tap) updates the button. On the next
    // frame: after the button's handler has run.

    ['input', 'change', 'click'].forEach(function (ev) {
        txForm.addEventListener(ev, function () {
            if (editId) requestAnimationFrame(updateSubmitLabel);
        });
    });

    txForm.addEventListener('submit', function (e) {
        e.preventDefault();
        if (isSubmitting) return;
        if (isUnchanged()) return;              // the button is disabled: nothing to save
        formError.textContent = '';
        txAmount.removeAttribute('aria-invalid');

        const amount = amountValue();
        if (!amount || amount <= 0 || !isFinite(amount)) {
            formError.textContent = 'נא להזין סכום תקין';
            txAmount.setAttribute('aria-invalid', 'true');
            txAmount.setAttribute('aria-describedby', 'formError');
            txAmount.focus();
            return;
        }

        // The server rejects a missing category anyway; here we just say so
        // before the request

        if (!(txProject.value ? txProjectCategory.value : txCategory.value)) {
            formError.textContent = 'נא לבחור קטגוריה';
            return;
        }

        const payload = {
            amount:              amount,
            type:                currentType,
            category_id:         txCategory.value || null,
            project_category_id: txProjectCategory.value || null,
            description:         txDescription.value.trim(),
            date:                txDate.value,
            owner:               window.SF_ATTRIBUTION[currentType] ? (txOwner.value || null) : null,
            is_recurring:        recurringCb.checked,
            recurring_frequency: recurringCb.checked ? txFrequency.value : null,
            recurring_end_date:  recurringCb.checked ? (txEndDate.value || null) : null,
            receipt_path:        currentType === 'expense' ? (txReceiptPath.value || null) : null,
            project_id:          txProject.value || null,
        };
        if (!editId && instCb && instCb.checked && currentType === 'expense') {
            const n = installmentCount();
            if (!n) { formError.textContent = 'מספר התשלומים — בין 2 ל-60'; instCount.focus(); return; }
            payload.installments = n;
            payload.is_recurring = false;          // the server turns it into a recurring transaction with an end
            payload.recurring_frequency = null;
            payload.recurring_end_date = null;
        }

        if (!editId && precheckPassed !== precheckKey(payload)) {
            const key = precheckKey(payload);
            const mySeq = formSeq;
            setSubmitBusy(true);
            submitLabel.textContent = 'בודק…';
            runPrecheck(payload).then(function (ok) {
                if (mySeq !== formSeq) return;          // the form was replaced meanwhile
                setSubmitBusy(false);
                updateSubmitLabel();
                // The form was closed during "checking…": the user gave up.
                // Without this the transaction was saved anyway, with a receipt
                // the close had already deleted.

                if (!overlay.classList.contains('open')) return;
                if (!ok) return;
                precheckPassed = key;
                requestSubmit();
            });
            return;
        }
        precheckPassed = null;

        setSubmitBusy(true);
        submitLabel.textContent = 'שומר…';
        // From now on the receipt belongs to this save; see `claimedReceipt`.
        // Any failure below releases it, so closing without retrying does
        // delete it.

        claimedReceipt = payload.receipt_path;

        // What belongs to **this** save, not to whatever is open when the
        // response comes back

        const myForm   = formSeq;
        const myEditId = editId;
        const myOriginal = editId ? editingOriginal : null;
        const mySeries = !!(editId && editingSeries);
        const myLabel  = '₪' + amount + (payload.description ? ' (' + payload.description + ')' : '');
        // Whether this save's form is no longer on screen, because another form
        // opened since

        function formWasReplaced() { return formSeq !== myForm; }

        // Optimistic UI: only for a new add (not edit) and only on the home page,
        // where there is a known "recent transactions" list a temporary row can
        // be added to right away, before the server even answered. The rest of
        // the page (summary cards, charts) keeps updating only with the full
        // refresh that happens on success anyway.
        // A transaction attached to a project isn't shown on the home page at
        // all, so it gets no temporary row; otherwise it would flash for a
        // moment and vanish on refresh

        let placeholderRow = null;
        if (!editId && !txProject.value && window.location.pathname === '/') {
            const list = document.querySelector('.transactions-list');
            // `closeModal` must be **inside** the condition. On an empty
            // dashboard there is no list at all (the template renders it only
            // when there are transactions), so no temporary row was created, but
            // the sheet closed anyway, and every error message was written into
            // a closed sheet that had just been cleared. The user's very first
            // transaction failed with no sign on screen.

            if (list) {
                placeholderRow = buildPlaceholderTxRow(amount);
                // At the top of the list: the list is in entry order, newest on top

                list.insertBefore(placeholderRow, list.firstChild);
                closeModal();
                setSubmitBusy(false);
                updateSubmitLabel();
            }
        }

        // The optimistic close cleared the receipt field (`closeModal` →
        // `resetScanUI`), so on reopening after a failure it comes back;
        // otherwise the next attempt is saved without it.

        function reopenAfterFailure() {
            openModal();
            txReceiptPath.value = payload.receipt_path || '';
        }

        // The save failed after a new form was already opened: don't open the
        // old one over it and don't write an error into it; say which
        // transaction wasn't saved, so it can be typed again. Its receipt won't
        // be saved anymore.

        function failedBehindAnotherForm(reason) {
            claimedReceipt = null;
            discardReceipt(payload.receipt_path);
            window.showToast('העסקה של ' + myLabel + ' לא נשמרה — ' + reason, 'error');
        }

        const url    = editId ? ('/api/transactions/' + editId) : '/api/transactions';
        const method = editId ? 'PUT' : 'POST';

        function send(confirmed) {
            const query = [mySeries ? 'from_now=1' : '', confirmed ? 'confirm=1' : ''].filter(Boolean).join('&');
            return fetch(url + (query ? '?' + query : ''), {
                method:  method,
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify(payload),
            }).then(function (r) {
                // A non-JSON response (an error page from the server or Railway:
                // 502/504, a deploy midway) turned into "network error, try
                // again". But the request had already reached the server, and the
                // save may have been written before it fell over: "try again"
                // created a copy. So say what is really known.

                return r.json().then(
                    function (d) { return { code: r.status, d: d }; },
                    function () {
                        return { code: r.status, d: { error: myEditId
                            ? 'השרת לא ענה כמו שצריך — ייתכן שהשינוי כבר נשמר. רעננו ובדקו לפני שמנסים שוב.'
                            : 'השרת לא ענה כמו שצריך — ייתכן שהעסקה כבר נשמרה. בדקו ברשימה לפני שמנסים שוב.' } };
                    });
            });
        }

        send(false)
        .then(function (res) {
            // A recurring series that would create dozens of rows back is almost
            // always a date typo. The server answers 409 with the real number,
            // and we ask.

            if (res.code === 409 && res.d.needs_confirm) {
                if (placeholderRow && formWasReplaced()) {
                    placeholderRow.remove(); placeholderRow = null;
                    failedBehindAnotherForm(res.d.error);
                    return null;
                }
                if (placeholderRow) { placeholderRow.remove(); placeholderRow = null; reopenAfterFailure(); }
                return window.appConfirm({
                    title: 'ליצור ' + res.d.will_create + ' עסקאות אחורה?',
                    message: res.d.error,
                    confirmText: 'כן, ליצור',
                }).then(function (ok) {
                    if (!ok) {
                        claimedReceipt = null;
                        setSubmitBusy(false);
                        updateSubmitLabel();
                        return null;
                    }
                    return send(true);
                });
            }
            return res;
        })
        .then(function (res) {
            if (!res) return;                       // the user cancelled
            const data = res.d;
            if (data.error && placeholderRow && formWasReplaced()) {
                placeholderRow.remove();
                placeholderRow = null;
                failedBehindAnotherForm(data.error);
                return;
            }
            if (data.error) {
                // On a **server** failure the sheet stayed closed, and the fields
                // (still filled, because `closeModal` doesn't reset them) became
                // unreachable: the only way back is the FAB, and `openAddModal`
                // clears them. The toast vanished after 2.6 seconds, and the user
                // typed everything again and failed again for the same reason,
                // which was no longer on screen. This is the same fix made for a
                // network failure, in the branch that was missed.

                if (placeholderRow) {
                    placeholderRow.remove();
                    placeholderRow = null;
                    reopenAfterFailure();
                }
                formError.textContent = data.error;
                claimedReceipt = null;
                setSubmitBusy(false);
                updateSubmitLabel();
                return;
            }

            // A new transaction went in: a sound and a tap. Must happen here,
            // within the user's tap chain, because iOS allows audio only from an
            // interaction.

            const isNew = !myEditId;
            if (isNew) window.appFeedback();

            // What a follow-up action (below) wants the user to know, instead of
            // the usual message

            let followUp = null;
            // A recurring transaction edited from settings: from which date the
            // change applies. No "undo": the series split, and there is no single
            // transaction to restore.
            // A new expense that crossed its category's budget (Matan, 3.10, idea 17)

            if (isNew && data.budget_note) {
                followUp = { text: 'העסקה נוספה. ' + data.budget_note };
            }
            if (data.series_from) {
                const d = data.series_from;
                followUp = { text: 'העסקה הקבועה עודכנה — מ-' + d.slice(8, 10) + '.' + d.slice(5, 7)
                                   + ' והלאה. החודשים הקודמים לא השתנו', series: true };
            }

            function finish() {
                // If a new form opened meanwhile, it is the user's, not ours

                if (!formWasReplaced()) {
                    // The button stayed "saving…" after success. On a page that
                    // refreshes softly (home) the JS isn't reloaded, so the next
                    // edit met a stuck button: the tap did nothing, with no
                    // message at all. Matan: "sometimes it doesn't let me tap
                    // save changes".

                    setSubmitBusy(false);
                    updateSubmitLabel();
                    closeModal();
                }
                // Soft refresh: replaces the page content with no navigation, no
                // re-parsing of the CSS and JS, and without losing the scroll
                // position. The 380ms pause was there only so the sound could
                // finish before the page disappeared; now it doesn't disappear,
                // so it is redundant.

                const message = followUp ? followUp.text : (myEditId ? 'העסקה עודכנה' : 'העסקה נוספה');
                // "Undo" after an edit: not when the series was changed from
                // now on, and not after a failure

                const undo = (myOriginal && data.transaction && !(followUp && (followUp.series || followUp.error)))
                    ? undoSpec(myOriginal, data.transaction, message) : null;
                if (document.querySelector('main[data-soft-reload]')) {
                    if (undo) rememberUndo(undo);
                    window.softReload(null, undo ? null : message).then(function (how) {
                        if (how === 'reloaded') return;   // shown after the full load
                        if (undo) { showPendingUndo(); return; }
                        window.showToast(message, followUp && followUp.error ? 'error' : undefined);
                    });
                } else {
                    // A page with charts or direct listeners: a full reload, as before

                    if (undo) rememberUndo(undo);
                    else window.sfToastAfterReload(message);
                    setTimeout(function () { window.location.reload(); }, isNew ? 380 : 0);
                }
            }

            const sideEffects = [];

            // Smart sync: when editing an occurrence created from a recurring
            // transaction and the amount changed, offer to update the template
            // too, so future occurrences use it.
            //
            // "From now on" splits the series at this occurrence (see
            // `split_recurring_series`): the previous months don't change. Both
            // ids are captured before the confirmation question and not read
            // after it: opening another transaction replaces them, and the
            // question waits for the user.

            if (editingRecurringParentId && amount !== originalAmount) {
                const templateId = editingRecurringParentId;
                const instanceId = editId;
                sideEffects.push(function () {
                    return window.appConfirm({
                        title: 'לעדכן גם את החודשים הבאים?',
                        message: 'שיניתם את הסכום. להמשיך איתו גם בחודשים הבאים? החודשים שכבר עברו יישארו כמו שהם.',
                        confirmText: 'עדכון להבא',
                        danger: false,
                    }).then(function (ok) {
                        if (!ok) return;
                        const failed = function (why) {
                            followUp = { error: true,
                                         text: 'העסקה עודכנה, אבל הסכום לחודשים הבאים לא השתנה — ' + why };
                        };
                        return fetch('/api/recurring/' + templateId + '/sync', {
                            method:  'PUT',
                            headers: { 'Content-Type': 'application/json' },
                            body:    JSON.stringify({ instance_id: instanceId }),
                        }).then(function (r) {
                            if (r.ok) {
                                followUp = { text: 'העסקה עודכנה, והסכום החדש ימשיך מהחודש הזה', series: true };
                                return;
                            }
                            return r.json().then(function (d) { failed((d && d.error) || 'נסו שוב'); },
                                                 function () { failed('נסו שוב'); });
                        }, function () {
                            // Here a failure was silently swallowed until now:
                            // the user answered "update from now on", heard "the
                            // transaction was updated", and next month was
                            // created at the old amount.

                            failed(window.sfNetError());
                        });
                    });
                });
            }

            sideEffects
                .reduce(function (chain, fn) { return chain.then(fn); }, Promise.resolve())
                .then(finish);
        })
        .catch(function () {
            if (placeholderRow && formWasReplaced()) {
                placeholderRow.remove();
                failedBehindAnotherForm(window.sfNetError());
                return;
            }
            if (placeholderRow) {
                placeholderRow.remove();
                // The sheet was already closed optimistically, but what was
                // typed is still in the fields (`closeModal` doesn't reset them).
                // Reopen it instead of leaving the user with an error message and
                // an empty form: in a garage or a car park, "try again" meant
                // typing everything again, with no network, exactly when they
                // least want to.

                reopenAfterFailure();
            }
            formError.textContent = window.sfNetError();
            claimedReceipt = null;
            setSubmitBusy(false);
            updateSubmitLabel();
        });
    });

    // Actually deletes a transaction (after confirmation): used both by the
    // delete button in the modal and by the swipe action on a transaction row,
    // so the same logic isn't duplicated.
    // Deletion with undo: delete on the server right away and remove the row
    // from the DOM (no immediate reload that collapses open lists), and show an
    // "undo" toast for 6 seconds. On "undo", restore by re-POSTing from the
    // row's data (an attached receipt, if any, isn't restored). If not undone,
    // reload at the end of the window to refresh the totals (balance/KPI).

    let pendingDeleteReload = null;

    /* The refresh at the end of the undo window exists to update the totals
     * (balance, KPI) after the row was removed. But it was a forced
     * `location.reload()`, and if meanwhile the user opened the modal and
     * started typing, the page reloaded mid-word and everything was lost. That
     * is exactly the sequence people do: delete a wrong transaction, and
     * immediately type the fix.
     *
     * So: when something is open for editing, defer. And when possible, use the
     * soft refresh, which doesn't touch the modal anyway (it sits outside main). */
    let refreshWhenEditingEnds = false;

    function somethingIsBeingEdited() {
        return overlay.classList.contains('open');
    }

    function refreshAfterDelete() {
        if (somethingIsBeingEdited()) {
            refreshWhenEditingEnds = true;
            return;
        }
        refreshWhenEditingEnds = false;
        if (document.querySelector('main[data-soft-reload]')) {
            window.softReload();
        } else {
            window.location.reload();
        }
    }

    // Called when the modal closes, to complete a deferred refresh

    function refreshIfPending() {
        if (refreshWhenEditingEnds && !somethingIsBeingEdited()) refreshAfterDelete();
    }

    /* ── Deleting a transaction that belongs to a recurring series ──
     *
     * Exactly a calendar's question: "only this one" or "this and all
     * following". There is no distinction here between "template" and
     * occurrence: it is entirely internal, and whoever deletes March's rent
     * shouldn't have to care whether March happens to be the month the series
     * was opened.
     *
     * The safe one is the confirm button; the destructive one goes through a
     * second confirmation with the number. A retreat (Escape or a tap outside)
     * returns `null` and cancels, at both stages.
     */
    /* Every row of a transaction on the page, not just the one the delete started
     * from. On the month page the same transaction appears in "all
     * transactions", in its category and in the split by family member, and the
     * other copies stayed tappable until the refresh, on a transaction that no
     * longer exists. */
    function removeTransactionRows(id, row) {
        if (row) leaveRow(row);
        if (!id) return;
        document.querySelectorAll('.transaction-item, .cat-tx-row, .recurring-row')
            .forEach(function (el) { if (el.dataset.id === id) leaveRow(el); });
    }

    /* A deleted row leaves gently and doesn't vanish in an instant (Matan, 7.10,
     * motion round, item 1).
     *
     * `row.remove()` alone made it disappear, and all the rows below it jumped
     * up at once. Now: a fade and a slight shrink (120ms), then the height closes
     * (180ms) and the rows below rise in sequence. The list's spacing (`gap`)
     * closes too; otherwise it jumped at the end.
     *
     * The row stops being a transaction **right away**: without `data-id` it
     * can't be tapped, swiped or found again while it leaves. With reduced
     * motion, the general rule in style.css zeroes all transitions, and the rows
     * close immediately. */
    function leaveRow(el) {
        if (!el.isConnected || el.dataset.leaving) return;
        if (el.offsetParent === null) { el.remove(); return; }   // hidden: nothing to see
        el.dataset.leaving = '1';
        el.removeAttribute('data-id');
        el.setAttribute('aria-hidden', 'true');
        el.style.pointerEvents = 'none';
        const parent = el.parentElement;
        const gap = parent ? parseFloat(getComputedStyle(parent).rowGap) || 0 : 0;
        el.style.boxSizing = 'border-box';
        el.style.height = el.offsetHeight + 'px';
        el.style.overflow = 'hidden';
        el.style.transition = 'opacity 120ms ease-out, transform 120ms ease-out';
        el.style.opacity = '0';
        el.style.transform = 'scale(0.98)';
        setTimeout(function () {
            const ease = '180ms cubic-bezier(0.22, 1, 0.36, 1)';
            el.style.transition = ['height', 'margin', 'padding', 'border-width']
                .map(function (p) { return p + ' ' + ease; }).join(', ');
            el.style.height = '0px';
            el.style.paddingTop = el.style.paddingBottom = '0px';
            el.style.borderTopWidth = el.style.borderBottomWidth = '0px';
            el.style.marginTop = el.style.marginBottom = '0px';
            // The gap between the row and its neighbour: swallowed by a negative
            // margin, at the same pace

            if (gap) el.style[el.nextElementSibling ? 'marginBottom' : 'marginTop'] = -gap + 'px';
        }, 120);
        setTimeout(function () { el.remove(); }, 320);
    }

    function askAboutSeries(txData, row, later, onFail) {
        return window.appConfirm({
            title:       'זו עסקה קבועה',
            message:     'אפשר למחוק רק את המופע הזה, או אותו וכל הבאים '
                         + 'אחריו (' + later + ' בסך הכל).',
            confirmText: 'רק את זו',
            cancelText:  'את זו וכל הבאות',
            danger:      false,
        }).then(function (onlyThis) {
            if (onlyThis === null) return;                 // retreat
            if (onlyThis) return sendSeriesDelete(txData, row, 'one', 1, onFail);

            return window.appConfirm({
                title:       'למחוק את זו וכל הבאות?',
                message:     window.sfCount(later, 'עסקה אחת תימחק', 'עסקאות יימחקו') + ', והעסקה הקבועה תיעצר כאן. '
                             + 'מה שנרשם בחודשים קודמים יישאר.',
                confirmText: 'מחיקת ' + later,
            }).then(function (sure) {
                if (sure !== true) return;
                return sendSeriesDelete(txData, row, 'later', later, onFail);
            });
        });
    }

    /* Another family member deleted the transaction a moment earlier. That isn't a
     * failure: what the user asked for already happened. Without this they saw
     * "delete failed", the row stayed, and every further attempt failed again. */
    function alreadyGone(txData, row) {
        removeTransactionRows(txData.id, row);
        window.showToast('העסקה כבר נמחקה');
        clearTimeout(pendingDeleteReload);
        pendingDeleteReload = setTimeout(refreshAfterDelete, 2500);
    }

    function sendSeriesDelete(txData, row, mode, total, onFail) {
        return fetch('/api/transactions/' + txData.id + '?mode=' + mode, { method: 'DELETE' })
            .then(r => r.json().then(d => (r.status === 404 ? null : d)))
            .then(function (d) {
                if (d === null) return alreadyGone(txData, row);
                if (d.status !== 'ok') { if (onFail) onFail(d.error || 'המחיקה נכשלה'); return; }
                removeTransactionRows(txData.id, row);
                // No "undo" here: restoring a single occurrence would also bring
                // back the skip recorded for it, and restoring a whole series,
                // dozens of rows.

                const n = d.deleted || total;
                window.showToast(mode === 'one'
                    ? 'העסקה נמחקה. שאר החודשים של העסקה הקבועה נשארו'
                    : window.sfCount(n, 'עסקה אחת נמחקה', 'עסקאות נמחקו') + ', והעסקה הקבועה נעצרה');
                clearTimeout(pendingDeleteReload);
                pendingDeleteReload = setTimeout(refreshAfterDelete, 2500);
            })
            .catch(function () { if (onFail) onFail(window.sfNetError()); });
    }

    /* The confirmation before deleting.
     *
     * For a regular transaction, the usual question. For a transaction that
     * belongs to a recurring series, **no question here at all**, because the
     * server answers 409 and right after it the real question is asked ("only
     * this one / this and all following").
     *
     * Before, both questions appeared one after the other, and the first said
     * "this will remove the transaction from all reports and charts": a sentence
     * true for a single transaction and completely misleading when a whole
     * series is behind it. Matan saw it, and as expected understood it as the
     * only question there would be. */
    function confirmDelete(txData) {
        if (txData && (txData.isRecurring || txData.recurringParentId)) {
            return Promise.resolve(true);
        }
        return window.appConfirm({
            title: 'למחוק את העסקה?',
            message: 'הפעולה תסיר את העסקה מכל הדוחות והגרפים.',
            confirmText: 'מחיקת העסקה',
        });
    }

    function deleteWithUndo(txData, row, onFail) {
        // The receipt image is deleted from storage along with the transaction,
        // so "undo" restores only the transaction: the file no longer exists.
        // This used to happen silently: the user tapped "undo", saw the
        // transaction come back, and the receipt simply wasn't there anymore.
        // Say it upfront, and only when there really was a receipt.

        const hadReceipt = !!(row && row.querySelector('.receipt-badge'));
        fetch('/api/transactions/' + txData.id, { method: 'DELETE' })
            .then(r => r.json().then(d => ({ code: r.status, d: d })))
            .then(function (res) {
                // The server recognises that the transaction belongs to a
                // recurring series and refuses to delete without an explicit
                // choice: "only this one" or "this and all following".

                if (res.code === 409 && res.d.needs_choice) {
                    return askAboutSeries(txData, row, res.d.later, onFail);
                }
                if (res.code === 404) return alreadyGone(txData, row);
                const d = res.d;
                if (d.status !== 'ok') { if (onFail) onFail(d.error || 'מחיקה נכשלה'); return; }
                removeTransactionRows(txData.id, row);
                window.showToast(hadReceipt
                    ? 'העסקה נמחקה. הקבלה המצורפת נמחקה איתה ולא תחזור'
                    : 'העסקה נמחקה', null, {
                    label: 'ביטול',
                    onClick: function () {
                        clearTimeout(pendingDeleteReload);
                        refreshWhenEditingEnds = false;
                        restoreTransaction(Object.assign({}, txData,
                                                         { hadReceipt: hadReceipt }));
                    },
                });
                clearTimeout(pendingDeleteReload);
                pendingDeleteReload = setTimeout(refreshAfterDelete, 6000);
            })
            .catch(function () {
                if (onFail) onFail(window.sfNetError());
            });
    }

    function restoreTransaction(txData) {
        const payload = {
            amount:              txData.amount,
            type:                txData.type,
            category_id:         txData.categoryId || null,
            project_category_id: txData.projectCategoryId || null,
            description:         txData.description || '',
            date:                txData.date,
            owner:               txData.userId || null,
            is_recurring:        txData.isRecurring,
            recurring_frequency: txData.isRecurring ? txData.recurringFrequency : null,
            recurring_end_date:  txData.isRecurring ? txData.recurringEndDate : null,
            receipt_path:        null,
            project_id:          txData.projectId || null,
        };
        fetch('/api/transactions', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(payload),
        })
        .then(r => r.json())
        .then(function (data) {
            if (data.error) { window.showToast(data.error, 'error'); return; }
            // `receipt_path` is sent as `null` on purpose: the file was deleted
            // from storage at the moment of deletion, and a URL to a file that
            // doesn't exist would produce a broken 📎 tag.

            window.sfToastAfterReload(
                txData.hadReceipt ? 'העסקה שוחזרה — בלי הקבלה' : 'העסקה שוחזרה');
            window.location.reload();
        })
        .catch(function () { window.showToast('שחזור נכשל — נסו שוב', 'error'); });
    }

    // "Add again" (duplicate): turns the open edit modal into a new add modal
    // with the same values and today's date, for quickly recording a repeat
    // purchase (coffee, groceries).

    duplicateBtn.addEventListener('click', function () {
        editId = null;
        editingRecurringParentId = null;
        editingSeries = false;
        originalAmount = null;
        modalTitle.textContent = 'הוספת עסקה';
        setEditLook(false);
        editModeActions.style.display = 'none';
        if (enteredMeta) enteredMeta.hidden = true;
        // A duplicate is a one-off transaction: the "recurring" state doesn't
        // carry over

        recurringCb.checked = false;
        recurFields.classList.remove('visible');
        txDate.value = todayStr();
        syncDateChips();
        // `setType` and not just `updateSubmitLabel`: the scan button is hidden
        // in edit mode (`!editId && type === 'expense'`), and duplicating clears
        // `editId` without refreshing it. The duplicated row is a brand new
        // expense, and without this it opened with no way to photograph a
        // receipt, for no visible reason.

        setType(currentType);
        updateSubmitLabel();
        txAmount.focus();
        window.showToast('שכפול — עדכנו ושמרו');
    });

    // Delete (edit mode only)
    deleteBtn.addEventListener('click', function () {
        if (!editId) return;
        // A recurring transaction opened from "recurring transactions" in
        // settings: stop the series, like the ✕ that was on the row (Matan,
        // 11.10; the ✕ was removed). A regular delete here would delete the
        // template row: the first transaction, real money already recorded.

        if (editingSeries) {
            const seriesRow = document.querySelector('#recurringList [data-id="' + editId + '"]');
            if (seriesRow && window.sfStopSeries) {
                closeModal();
                window.sfStopSeries(seriesRow);
            }
            return;
        }
        const row = document.querySelector('[data-id="' + editId + '"]');
        // When the row isn't in the list (editing from the other page, for
        // example), the modal itself already knows whether it is a series

        const txData = row ? buildTxFromRow(row)
                           : { id: editId, isRecurring: recurringCb.checked,
                               recurringParentId: editingRecurringParentId };
        confirmDelete(txData).then(function (ok) {
            if (!ok) return;
            closeModal();
            deleteWithUndo(txData, row, function (msg) {
                window.showToast(msg, 'error');
            });
        });
    });

    /* ── A 📎 tag on a transaction with an attached receipt ──
     * There is no code here, and that is the fix. The previous version fetched a
     * URL from the server and then called window.open, and on iOS that was
     * always blocked: the browser allows opening a window only as a direct
     * result of a tap, and waiting for the server breaks that link. Nothing
     * happened (no window, no error, no hint), and scanned receipts simply
     * weren't reachable from the phone.
     *
     * The icon is now `<a href="/receipts/…" target="_blank">`, and the browser
     * navigates by itself. Navigation resulting from a tap isn't blocked.
     *
     * What does matter: `.receipt-badge` is excluded from the row handlers
     * below; otherwise tapping it would also open the edit modal. */

    // A flag preventing an edit from opening on the synthetic "click" the
    // browser fires after a touch gesture; without it, a swipe dragging the row
    // also opens the edit modal by mistake (a touchend that didn't call
    // preventDefault still produces a following click)

    let justSwiped = false;

    // Builds the tx object for the edit modal from the row's data attributes,
    // used both on an ordinary tap and on the edit action from a swipe.
    // "87.0" from the server is shown as "87"; "87.5" stays "87.5" (Matan, 3.10)

    function plainAmount(v) {
        const n = Number(v);
        return isFinite(n) ? String(n) : v;
    }

    // ── Autocomplete from earlier descriptions (Matan, 3.10, idea 41) ──
    // Loaded once per page load; up to 3 suggestions under the field, by word
    // start. A tap fills only the description; the category isn't touched
    // (Matan gave up on "remembering the category").

    const descSuggest = document.getElementById('descSuggest');
    let pastDescriptions = null;
    function loadDescriptions() {
        if (pastDescriptions || !descSuggest) return;
        pastDescriptions = [];
        fetch('/api/descriptions', { credentials: 'same-origin' })
            .then(r => r.ok ? r.json() : { descriptions: [] })
            .then(d => { pastDescriptions = d.descriptions || []; })
            .catch(() => { pastDescriptions = null; });   // try again on the next open
    }
    function hideSuggest() { if (descSuggest) { descSuggest.hidden = true; descSuggest.innerHTML = ''; } }
    function descMatches(q) {
        const needle = q.trim().toLowerCase();
        if (needle.length < 2 || !pastDescriptions) return [];
        return pastDescriptions.filter(function (d) {
            const low = d.toLowerCase();
            if (low === needle) return false;                     // already typed in full
            return low.split(/\s+/).some(w => w.startsWith(needle)) || low.startsWith(needle);
        }).slice(0, 3);
    }
    if (descSuggest) {
        txDescription.addEventListener('input', function () {
            const found = descMatches(txDescription.value);
            descSuggest.innerHTML = '';
            found.forEach(function (text) {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'desc-suggest-item';
                b.setAttribute('role', 'option');
                b.textContent = text;
                descSuggest.appendChild(b);
            });
            descSuggest.hidden = !found.length;
        });
        // `mousedown` and not `click`: otherwise the field's blur hides the
        // suggestions before the click

        descSuggest.addEventListener('mousedown', function (e) { e.preventDefault(); });
        descSuggest.addEventListener('click', function (e) {
            const b = e.target.closest('.desc-suggest-item');
            if (!b) return;
            txDescription.value = b.textContent;
            hideSuggest();
            txDescription.focus();
        });
        txDescription.addEventListener('blur', function () { setTimeout(hideSuggest, 150); });
    }

    function buildTxFromRow(row) {
        return {
            id:                  row.dataset.id,
            amount:              row.dataset.amount,
            type:                row.dataset.type,
            categoryId:          row.dataset.categoryId,
            projectCategoryId:   row.dataset.projectCategoryId || null,
            description:         row.dataset.description,
            date:                row.dataset.date,
            userId:              row.dataset.userId || '',
            isRecurring:         row.dataset.isRecurring === 'true',
            recurringFrequency:  row.dataset.recurringFrequency,
            recurringEndDate:    row.dataset.recurringEndDate,
            recurringParentId:   row.dataset.recurringParentId || null,
            projectId:           row.dataset.projectId || null,
        };
    }

    // ── Opening an edit by tapping an existing transaction (home page + month
    // page + recurring transactions in settings) ──
    // The same sheet everywhere. Until 5.10 the home page edited inside the
    // card; Matan asked for it to look like the month page.

    document.addEventListener('click', function (e) {
        if (justSwiped) { justSwiped = false; return; }
        if (e.target.closest('.receipt-badge, .swipe-action')) return;
        const row = e.target.closest('.transaction-item, .cat-tx-row, .recurring-row');
        if (!row || !row.dataset.id) return;
        openEditModal(buildTxFromRow(row), row);
    });

    // ── The same edit opening by keyboard (Enter/Space): the rows are marked
    // role="button" tabindex="0" in the templates, exactly like the existing
    // .legend-item[role=button] in month.html ──

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        if (e.target.closest('.receipt-badge, .swipe-action')) return;
        const row = e.target.closest('.transaction-item, .cat-tx-row, .recurring-row');
        if (!row || !row.dataset.id) return;
        e.preventDefault();
        openEditModal(buildTxFromRow(row), row);
    });

    // ── Swipe on a transaction row: right reveals "edit", left reveals "delete" ──
    // In addition to the ordinary tap, not instead of it. Works everywhere there
    // is a transaction row (home page, month page, recurring transactions in
    // settings, project page).

    (function () {
        const ROW_SELECTOR = '.transaction-item, .cat-tx-row, .recurring-row';
        const OPEN_X = 76;    // the open lock distance, matching the width of .swipe-action
        const THRESHOLD = 42; // where the motion is heading (position + momentum); past this it opens
        const calm = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        let drag = null;
        let anim = null;    // { content, cancel }: the spring running now, if any
        let openRow = null; // the row currently open (if any), to close it on a tap elsewhere

        // A recurring transaction row in the settings list: there "remove" stops
        // the series

        function isSettingsSeriesRow(row) {
            return row.classList.contains('recurring-row');
        }

        function ensureSwipeStructure(row) {
            if (row.classList.contains('swipe-ready')) return;
            row.classList.add('swipe-ready');

            const content = document.createElement('div');
            content.className = 'swipe-content';
            while (row.firstChild) content.appendChild(row.firstChild);

            const editAction = document.createElement('div');
            editAction.className = 'swipe-action swipe-action-edit';
            editAction.innerHTML = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg> עריכה';

            const deleteAction = document.createElement('div');
            deleteAction.className = 'swipe-action swipe-action-delete';
            // In settings the swipe stops the series (like the ✕ there) and
            // doesn't delete, so it isn't called "delete" either

            deleteAction.textContent = isSettingsSeriesRow(row) ? '✕ הסרה' : '✕ מחיקה';

            row.appendChild(editAction);
            row.appendChild(deleteAction);
            row.appendChild(content);
        }

        function stopAnim() {
            if (anim) { anim.cancel(); anim = null; }
        }

        // Reach `to` on a spring continuing at the finger's velocity (`v`, px/s).
        // Opening after a flick overshoots a touch (it had momentum); closing
        // stops smoothly.

        function settleTo(content, from, to, v) {
            stopAnim();
            if (calm) { content.style.transition = ''; content.style.transform = to ? 'translateX(' + to + 'px)' : ''; return; }
            content.style.transition = 'none';
            const paint = function (x) { content.style.transform = 'translateX(' + x + 'px)'; };
            anim = { content: content, cancel: sfSpring(from, to, v, 0.3, to ? 0.85 : 1, paint, function () {
                anim = null;
                content.style.transition = '';
                if (!to) content.style.transform = '';
            }) };
        }

        function closeRow(row) {
            const content = row.querySelector('.swipe-content');
            if (anim && anim.content === content) stopAnim();
            if (content) { content.style.transition = ''; content.style.transform = ''; }
            row.classList.remove('swipe-open');
            if (openRow === row) openRow = null;
        }

        // The structure is built **lazily**, on `touchstart` only (below).
        //
        // Before, it was built for every row on load: `ensureSwipeStructure`
        // moves every child into a new div and adds two panels, and on the month
        // page every transaction appears two or three times (breakdown by
        // category, "all transactions", project breakdown). A month of 150
        // transactions was ~350 rows × reparent + three element creations,
        // synchronously, on page load, and for no reason, because `touchstart`
        // builds what is touched anyway.


        document.addEventListener('touchstart', function (e) {
            if (e.target.closest('.receipt-badge, .swipe-action')) return;
            const row = e.target.closest(ROW_SELECTOR);
            if (!row || !row.dataset.id) return;
            ensureSwipeStructure(row);

            if (drag && drag.row !== row) closeRow(drag.row);

            // Touching a row that is already open only closes it (as in most
            // apps): avoids complicated dragging maths from an open state, and
            // also prevents opening an edit by mistake from the synthetic click
            // that will follow this touch

            if (row.classList.contains('swipe-open')) {
                closeRow(row);
                justSwiped = true;
                setTimeout(function () { justSwiped = false; }, 400);
                drag = null;
                return;
            }

            const touch = e.touches[0];
            drag = {
                row: row,
                content: row.querySelector('.swipe-content'),
                startX: touch.clientX,
                startY: touch.clientY,
                deltaX: 0,
                dragging: false,
                samples: [{ t: e.timeStamp, p: touch.clientX }],
            };
        }, { passive: true });

        document.addEventListener('touchmove', function (e) {
            if (!drag) return;
            const touch = e.touches[0];
            const dx = touch.clientX - drag.startX;
            const dy = touch.clientY - drag.startY;
            if (!drag.dragging) {
                if (Math.abs(dx) < 8) return;
                if (Math.abs(dy) > Math.abs(dx)) { drag = null; return; } // vertical scroll: not a swipe
                drag.dragging = true;
                drag.startX += dx > 0 ? 8 : -8;    // without the 8-pixel jump of the decision
                // Without the CSS delay: the row moves with the finger, not 0.2
                // seconds after it

                if (anim && anim.content === drag.content) stopAnim();
                drag.content.style.transition = 'none';
            }
            // Up to the button width, one to one; past it, resistance that grows
            // gradually, instead of a wall at 100 pixels

            const raw = touch.clientX - drag.startX;
            const over = Math.abs(raw) - OPEN_X;
            drag.deltaX = over <= 0 ? raw : Math.sign(raw) * (OPEN_X + sfRubber(over, drag.row.offsetWidth));
            drag.content.style.transform = 'translateX(' + drag.deltaX + 'px)';
            drag.samples.push({ t: e.timeStamp, p: touch.clientX });
            if (drag.samples.length > 6) drag.samples.shift();
            // The page doesn't scroll under the row while it is swiped (Matan,
            // 11.10: "light, comfortable swipes"). Until then the listener was
            // passive, and the screen shook up and down during a sideways swipe.

            if (e.cancelable) e.preventDefault();
        }, { passive: false });

        document.addEventListener('touchend', function (e) {
            if (!drag) return;
            const { row, content, deltaX, dragging, samples } = drag;
            if (dragging) {
                // Prevents opening an edit from the synthetic click the browser
                // may fire after the touch. Browsers usually don't fire a click
                // after a real drag, so the flag is reset after a moment so it
                // doesn't affect later taps unrelated to this drag at all.

                justSwiped = true;
                setTimeout(function () { justSwiped = false; }, 400);
            }
            // The decision by **where the motion is heading**: a short fast flick
            // opens, and a long drag that comes back doesn't. The direction is
            // that of the point it ends up at.

            const v = dragging ? sfVelocity(samples, e.timeStamp) : 0;
            const aim = deltaX + sfProject(v, 0.99);
            // A drag to one side and a flick back to the other: changing your
            // mind: it closes, it doesn't open the other way

            if (dragging && Math.abs(aim) > THRESHOLD && (aim > 0) === (deltaX > 0)) {
                const openDir = aim > 0 ? 'right' : 'left';
                content.dataset.openDir = openDir;
                row.classList.add('swipe-open');
                openRow = row;
                settleTo(content, deltaX, openDir === 'right' ? OPEN_X : -OPEN_X, v);
            } else if (dragging) {
                row.classList.remove('swipe-open');
                if (openRow === row) openRow = null;
                settleTo(content, deltaX, 0, v);
            } else {
                closeRow(row);
            }
            drag = null;
        });

        // The browser took the touch (scrolling, a system sheet): the row goes
        // back into place and doesn't get stuck midway

        document.addEventListener('touchcancel', function () {
            if (!drag) return;
            if (drag.dragging) closeRow(drag.row);
            drag = null;
        }, { passive: true });

        // A tap anywhere outside the open row (including another row) closes it

        document.addEventListener('click', function (e) {
            if (openRow && e.target.closest(ROW_SELECTOR) !== openRow) {
                closeRow(openRow);
            }
        });

        // A tap on a revealed panel: the same existing flows (edit/delete), with
        // no duplicated code

        document.addEventListener('click', function (e) {
            const editBtn = e.target.closest('.swipe-action-edit');
            if (editBtn) {
                const row = editBtn.closest(ROW_SELECTOR);
                closeRow(row);
                openEditModal(buildTxFromRow(row), row);
                return;
            }
            const delBtn = e.target.closest('.swipe-action-delete');
            if (delBtn) {
                const row = delBtn.closest(ROW_SELECTOR);
                // In settings: the same series stop as the ✕ on the same row.
                // Until now the swipe went to `DELETE /api/transactions` and
                // deleted the series' first transaction, or all of it, from a row
                // whose ✕ promises "everything already recorded will stay".

                if (isSettingsSeriesRow(row)) {
                    stopSeriesFromSettings(row, function () { closeRow(row); });
                    return;
                }
                const swiped = buildTxFromRow(row);
                confirmDelete(swiped).then(function (ok) {
                    if (!ok) { closeRow(row); return; }
                    deleteWithUndo(swiped, row, function (msg) {
                        closeRow(row);
                        window.showToast(msg, 'error');
                    });
                });
            }
        });
    })();

    /* Stopping a series from "recurring transactions" in settings: the swipe and
     * the "remove the recurring transaction" button in the edit sheet
     * (`window.sfStopSeries`) are both here. `onCancel`: what to do if the user
     * changed their mind (the swipe closes the row). */
    function stopSeriesFromSettings(row, onCancel) {
        window.appConfirm({
            title: 'להסיר את העסקה הקבועה?',
            message: 'מופעים חדשים יפסיקו להיווצר. כל מה שכבר נרשם — כולל העסקה הראשונה — יישאר בהיסטוריה.',
            confirmText: 'הסרה',
        }).then(function (ok) {
            if (!ok) { if (onCancel) onCancel(); return; }
            // `/api/recurring` and not `/api/transactions`: this stops the series
            // and doesn't delete a row. The template row is the series' first
            // transaction, and deleting it would take real money out of history:
            // exactly what the message above promises won't happen.

            fetch('/api/recurring/' + row.dataset.id, { method: 'DELETE' })
            .then(r => r.json())
            .then(function (d) {
                if (d.status === 'ok') {
                    row.style.transition = 'opacity 0.25s';
                    row.style.opacity = '0';
                    setTimeout(function () { row.remove(); }, 260);
                    window.showToast('העסקה הקבועה הוסרה');
                } else {
                    window.showToast('ההסרה נכשלה', 'error');
                }
            })
            .catch(function () { window.showToast(window.sfNetError(), 'error'); });
        });
    }
    window.sfStopSeries = stopSeriesFromSettings;

})();
