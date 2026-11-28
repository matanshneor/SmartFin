/* ═══ Editing a project: everything is saved at once (Matan, 9.10) ═══
 *
 * Until now only the details waited for the button; a category added, renamed
 * or deleted, and an ownership change, were sent to the server the moment they
 * were tapped, and "cancel" didn't cancel them. Now every change is recorded
 * here and marked on screen, and sent only on "save changes". "Cancel" really
 * cancels.
 *
 * On save the order is fixed: details, ownership, new categories, renames,
 * deletions. New ones before deletions, so the transactions of a category being
 * deleted can move to a category added in the same edit. A failed step stops
 * the rest; whatever was already saved leaves the list, and a second "save"
 * continues from the same point.
 *
 * A function and not a block that runs as the file loads: everything below
 * reads the form as it is when it starts ("nothing changed" is measured from
 * that moment). The server-built page starts it at once; the page that loads
 * its own data (below) starts it once its form is filled. */
function sfStartProjectEdit(detailUrl) {
const projectId = document.getElementById('projectCategoriesArea').dataset.projectId;
const editForm  = document.getElementById('editProjectForm');
const saveError = document.getElementById('projectSaveError');
const saveBtn   = document.querySelector('button[form="editProjectForm"]');

const fields = {
    name:         document.getElementById('editProjectName'),
    icon:         document.getElementById('editProjectIcon'),
    description:  document.getElementById('editProjectDescription'),
    budget:       document.getElementById('editProjectBudget'),
    trackExpense: document.getElementById('editProjectTrackExpense'),
    trackIncome:  document.getElementById('editProjectTrackIncome'),
    trackSavings: document.getElementById('editProjectTrackSavings'),
};
function fieldValues() {
    return Object.keys(fields).map(function (k) {
        const el = fields[k];
        return el.type === 'checkbox' ? el.checked : el.value;
    }).join('\u0001');
}
if (window.sfThousands) window.sfThousands(fields.budget);
const initialFields = fieldValues();

function json(r) {
    return r.json().catch(function () { return {}; })
        .then(function (d) { return { ok: r.ok, d: d }; });
}
function call(method, url, body) {
    const opts = { method: method };
    if (body) {
        opts.headers = { 'Content-Type': 'application/json' };
        opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(json).then(function (res) {
        if (!res.ok || res.d.error) throw new Error(res.d.error || 'השמירה נכשלה — נסו שוב');
        return res.d;
    });
}

// ── Ownership: recorded now, sent on save ──

const shareBtn   = document.getElementById('shareProjectBtn');
const unshareBtn = document.getElementById('unshareProjectBtn');
const ownershipNote = document.getElementById('ownershipPending');
let ownership = null;           // ‎'share'‎ / ‎'unshare'‎ / ‎null‎

function showOwnership() {
    const btn = shareBtn || unshareBtn;
    if (!btn) return;
    btn.textContent = ownership ? 'ביטול השינוי'
                    : (shareBtn ? 'הפיכה למשותף' : 'החזרה לפרויקט אישי');
    ownershipNote.hidden = !ownership;
    ownershipNote.textContent = ownership === 'share'
        ? 'הפרויקט יהפוך למשותף בשמירה'
        : 'הפרויקט יחזור להיות אישי בשמירה';
    syncSave();
}

if (shareBtn) {
    shareBtn.addEventListener('click', function () {
        if (ownership) { ownership = null; showOwnership(); return; }
        window.appConfirm({
            title: 'להפוך את הפרויקט למשותף?',
            message: 'כל בני המשפחה יראו את הפרויקט, וההוצאות/הכנסות שכבר נרשמו בו יהפכו לשיוך משותף. תמיד אפשר להחזיר אותו להיות אישי. השינוי ייכנס לתוקף בשמירה.',
            confirmText: 'הפיכה למשותף',
            danger: false,
        }).then(function (ok) {
            if (!ok) return;
            ownership = 'share';
            showOwnership();
        });
    });
}

if (unshareBtn) {
    unshareBtn.addEventListener('click', function () {
        if (ownership) { ownership = null; showOwnership(); return; }
        // "אור רשם כאן 10 עסקאות": computed on the server. The project will be
        // hidden from them, including what they themselves recorded in it, so it
        // is said before and not discovered after.

        const others = unshareBtn.dataset.othersNote;
        window.appConfirm({
            title: 'להחזיר את הפרויקט להיות אישי?',
            message: (others
                ? others + '. אחרי ההחזרה הפרויקט יהיה גלוי רק למי שיצר אותו — '
                  + 'גם את העסקאות האלה, והן יסומנו "הוזנה ע״י".'
                : 'מעכשיו הפרויקט גלוי רק למי שיצר אותו. עסקאות שכבר נרשמו כמשותפות יישארו כך.')
                + ' השינוי ייכנס לתוקף בשמירה.',
            confirmText: 'החזרה לפרויקט אישי',
            danger: !!others,
        }).then(function (ok) {
            if (!ok) return;
            ownership = 'unshare';
            showOwnership();
        });
    });
}

// ── The project's categories ──
//
// Every category in one list: `{id, type, name, icon, origName, origIcon, isNew,
// deleted}`. `deleted` is `{target}`: where the transactions will move (`null`
// when there are none). A new one has a temporary id until saved.

const catTabs   = document.querySelectorAll('.project-cat-tabs .toggle-btn');
const catList   = document.getElementById('projectCatList');
const addForm   = document.getElementById('addProjectCatForm');
const addError  = document.getElementById('addProjectCatError');
const toggleBtn = document.getElementById('projectCatToggle');
const catBody   = document.getElementById('projectCatBody');
const countEl   = document.getElementById('projectCatCount');
const tracked   = Array.prototype.map.call(catTabs, function (b) { return b.dataset.catTab; });
let currentTab  = tracked[0] || null;
let cats = [];
let loaded = false;
let tmpSeq = 0;

const TYPE_NOUN = { expense: 'הוצאות', income: 'הכנסות', savings: 'חיסכון' };

function find(id) { return cats.find(function (c) { return c.id === id; }); }
function live(type) {
    return cats.filter(function (c) { return c.type === type && !c.deleted; });
}
function isRenamed(c) { return !c.isNew && (c.name !== c.origName || c.icon !== c.origIcon); }
function catsDirty() {
    return cats.some(function (c) { return c.isNew || c.deleted || isRenamed(c); });
}
function isDirty() {
    return fieldValues() !== initialFields || !!ownership || catsDirty();
}

function updateCount() {
    if (!loaded) return;
    const n = cats.filter(function (c) { return tracked.indexOf(c.type) !== -1 && !c.deleted; }).length;
    countEl.textContent = '(' + n + ')';
}

function rowHTML(c) {
    let tag = '';
    if (c.deleted) {
        const dest = c.deleted.target && find(c.deleted.target);
        tag = 'תימחק בשמירה' + (dest ? ' · העסקאות יעברו ל"' + dest.name + '"' : '');
    } else if (c.isNew) {
        tag = 'חדשה';
    } else if (isRenamed(c)) {
        tag = 'שונתה';
    }
    const id = escapeHtml(c.id);
    const actions = c.deleted
        ? `<button type="button" class="btn-sm btn-ghost undo-cat-btn" data-id="${id}">ביטול</button>`
        : `<button type="button" class="edit-cat-btn" data-id="${id}" aria-label="עריכת הקטגוריה">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>
            </svg>
        </button>
        <button type="button" class="delete-cat-btn" data-id="${id}" aria-label="מחיקת הקטגוריה">✕</button>`;
    return `
        <span class="cat-row-icon">${escapeHtml(c.icon)}</span>
        <span class="cat-row-name"><span class="cat-name-text">${escapeHtml(c.name)}</span>${tag ? `<span class="cat-pending-tag">${escapeHtml(tag)}</span>` : ''}</span>
        ${actions}
    `;
}

// "Save changes" is enabled only when something changed: details, ownership or
// categories (Matan, 13.10)

function syncSave() { saveBtn.disabled = !isDirty(); }
editForm.addEventListener('input', syncSave);
editForm.addEventListener('change', syncSave);

// The project's kind (Matan, 22.11): the two cards set the three hidden "what to
// track" checkboxes, and what the amount is called. Disabled once the project
// has transactions (the template), and the server refuses the change then too.
document.querySelectorAll('#editProjectKind .kind-card').forEach(function (card) {
    card.addEventListener('click', function () {
        const kind = card.dataset.kind;
        document.querySelectorAll('#editProjectKind .kind-card').forEach(function (c) {
            c.setAttribute('aria-checked', c === card ? 'true' : 'false');
        });
        fields.trackExpense.checked = kind === 'expense';
        fields.trackIncome.checked  = kind === 'expense';
        fields.trackSavings.checked = kind === 'savings';
        const label = document.getElementById('editProjectBudgetLabel');
        if (label) label.textContent = (kind === 'savings' ? 'יעד חיסכון' : 'תקציב') + ' (אופציונלי)';
        const hint = document.getElementById('budgetTargetHint');
        if (hint && hint.dataset[kind]) hint.textContent = hint.dataset[kind];
        editForm.dispatchEvent(new Event('change', { bubbles: true }));
    });
});

function render() {
    syncSave();
    updateCount();
    if (!loaded) return;
    catList.innerHTML = '';
    cats.filter(function (c) { return c.type === currentTab; }).forEach(function (c) {
        const li = document.createElement('li');
        li.className = 'category-row'
            + (c.deleted ? ' is-deleted' : c.isNew ? ' is-new' : isRenamed(c) ? ' is-renamed' : '');
        li.dataset.id = c.id;
        li.innerHTML = rowHTML(c);
        catList.appendChild(li);
    });
}

function load() {
    catList.innerHTML = '<li class="category-row"><span class="cat-row-name">טוען…</span></li>';
    window.sfFetchList('/api/projects/' + projectId + '/categories')
        .then(function (rows) {
            cats = rows.map(function (r) {
                return { id: r.id, type: r.type, name: r.name, icon: r.icon || '📦',
                         origName: r.name, origIcon: r.icon || '📦', isNew: false, deleted: null };
            });
            loaded = true;
            render();
        })
        .catch(function () {
            catList.innerHTML = '';
            window.showToast(window.sfNetError(), 'error');
        });
}

toggleBtn.addEventListener('click', function () {
    const open = toggleBtn.getAttribute('aria-expanded') !== 'true';
    toggleBtn.setAttribute('aria-expanded', String(open));
    catBody.hidden = !open;
});

catTabs.forEach(function (btn) {
    btn.addEventListener('click', function () {
        catTabs.forEach(function (b) {
            b.classList.remove('active');
            b.setAttribute('aria-selected', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');
        currentTab = btn.dataset.catTab;
        render();
    });
});

if (currentTab) load();

function nameTaken(name, type, exceptId) {
    return live(type).some(function (c) { return c.id !== exceptId && c.name === name; });
}

addForm.addEventListener('submit', function (e) {
    e.preventDefault();
    addError.textContent = '';
    if (!loaded) return;
    const iconEl = document.getElementById('newProjectCatIcon');
    const nameEl = document.getElementById('newProjectCatName');
    const icon = iconEl.value.trim() || '🏷';
    const name = nameEl.value.trim();
    if (!name) { addError.textContent = 'נא להזין שם קטגוריה'; return; }
    if (nameTaken(name, currentTab)) { addError.textContent = 'כבר יש קטגוריה בשם הזה'; return; }
    cats.push({ id: 'new-' + (++tmpSeq), type: currentTab, name: name, icon: icon,
                origName: name, origIcon: icon, isNew: true, deleted: null });
    nameEl.value = '';
    iconEl.value = '🏷';
    render();
});

// A category that another category's transactions are meant to move to isn't
// deleted; otherwise on save they would move to it and then be left with no target

function pendingTargetOf(id) {
    return cats.find(function (c) { return c.deleted && c.deleted.target === id; });
}

document.addEventListener('click', function (e) {
    const btn = e.target.closest('.delete-cat-btn');
    if (!btn) return;
    const c = find(btn.dataset.id);
    if (!c) return;
    const source = pendingTargetOf(c.id);
    if (source) {
        window.showToast('העסקאות של "' + source.name + '" עוברות לכאן. בטלו קודם את המחיקה שלה.', 'error');
        return;
    }
    if (c.isNew) {          // not saved yet: it simply leaves the list
        cats = cats.filter(function (x) { return x !== c; });
        render();
        return;
    }
    // The server knows how many transactions it has; the targets are corrected
    // by what isn't saved yet: without categories marked for deletion, with the
    // new names, and with the new ones added

    window.sfAskCategoryDeletion('/api/projects/' + projectId + '/categories/' + c.id, c.name,
        function (usage) {
            const alternatives = live(c.type)
                .filter(function (x) {
                    return x.id !== c.id && (x.isNew
                        || (usage.alternatives || []).some(function (a) { return a.id === x.id; }));
                })
                .map(function (x) { return { id: x.id, name: x.name, icon: x.icon }; });
            let blocked = null;
            if (!alternatives.length && (usage.blocked || (usage.alternatives || []).length)) {
                blocked = usage.blocked || ('זו קטגוריית ה' + (TYPE_NOUN[c.type] || '')
                    + ' האחרונה — בלעדיה אי אפשר לרשום עסקאות במחלקה הזאת. אפשר לשנות לה את השם במקום למחוק.');
            }
            return { count: usage.count, alternatives: alternatives, blocked: blocked };
        })
        .then(function (choice) {
            if (!choice) return;
            c.deleted = { target: choice.target };
            render();
        });
});

document.addEventListener('click', function (e) {
    const btn = e.target.closest('.undo-cat-btn');
    if (!btn) return;
    const c = find(btn.dataset.id);
    if (c) { c.deleted = null; render(); }
});

// ── Rename/re-icon inside the row: "confirm" updates the list, not the server ──

document.addEventListener('click', function (e) {
    const btn = e.target.closest('.edit-cat-btn');
    if (!btn) return;
    const row = btn.closest('.category-row');
    const c = find(btn.dataset.id);
    if (!row || !c || row.classList.contains('editing')) return;

    row.classList.add('editing');
    row.innerHTML = `
        <div class="cat-edit-form">
            <div class="add-cat-row">
                <input class="form-input emoji-input cat-edit-icon" type="text" maxlength="2" aria-label="אייקון">
                <input class="form-input cat-edit-name" type="text" maxlength="30" aria-label="שם קטגוריה">
            </div>
            <p class="form-error cat-edit-error" role="alert"></p>
            <div class="edit-actions">
                <button type="button" class="btn-sm btn-primary cat-edit-ok" data-id="${escapeHtml(c.id)}">אישור</button>
                <button type="button" class="btn-sm btn-ghost cat-edit-cancel">ביטול</button>
            </div>
        </div>
    `;
    row.querySelector('.cat-edit-icon').value = c.icon;
    row.querySelector('.cat-edit-name').value = c.name;
    row.querySelector('.cat-edit-name').focus();
});

document.addEventListener('click', function (e) {
    if (e.target.closest('.cat-edit-cancel')) { render(); return; }
    const ok = e.target.closest('.cat-edit-ok');
    if (!ok) return;
    const row  = ok.closest('.category-row');
    const c    = find(ok.dataset.id);
    const icon = row.querySelector('.cat-edit-icon').value.trim() || '📦';
    const name = row.querySelector('.cat-edit-name').value.trim();
    const err  = row.querySelector('.cat-edit-error');
    if (!name) { row.querySelector('.cat-edit-name').focus(); return; }
    if (nameTaken(name, c.type, c.id)) { err.textContent = 'כבר יש קטגוריה בשם הזה'; return; }
    c.name = name;
    c.icon = icon;
    if (c.isNew) { c.origName = name; c.origIcon = icon; }
    render();
});

// ── Saving ──

function saveSteps() {
    const steps = [];
    const name = fields.name.value.trim();
    const budgetVal = fields.budget.value.replace(/,/g, '').trim();
    steps.push(function () {
        return call('PUT', '/api/projects/' + projectId, {
            name: name,
            icon: fields.icon.value.trim(),
            description: fields.description.value.trim(),
            budget_target: budgetVal ? parseFloat(budgetVal) : null,
            track_expense: fields.trackExpense.checked,
            track_income: fields.trackIncome.checked,
            track_savings: fields.trackSavings.checked,
        });
    });
    if (ownership) {
        steps.push(function () {
            return call('PUT', '/api/projects/' + projectId + '/' + ownership).then(function () {
                ownership = null;
            });
        });
    }
    cats.filter(function (c) { return c.isNew && !c.deleted; }).forEach(function (c) {
        steps.push(function () {
            return call('POST', '/api/projects/' + projectId + '/categories',
                        { name: c.name, icon: c.icon, type: c.type })
                .then(function (saved) {
                    cats.forEach(function (x) {
                        if (x.deleted && x.deleted.target === c.id) x.deleted.target = saved.id;
                    });
                    c.id = saved.id;
                    c.isNew = false;
                });
        });
    });
    cats.filter(function (c) { return !c.isNew && !c.deleted && isRenamed(c); }).forEach(function (c) {
        steps.push(function () {
            return call('PUT', '/api/projects/' + projectId + '/categories/' + c.id,
                        { name: c.name, icon: c.icon })
                .then(function () { c.origName = c.name; c.origIcon = c.icon; });
        });
    });
    cats.filter(function (c) { return !c.isNew && c.deleted; }).forEach(function (c) {
        steps.push(function () {
            const target = c.deleted.target;
            return call('DELETE', '/api/projects/' + projectId + '/categories/' + c.id
                        + (target ? '?move_to=' + encodeURIComponent(target) : ''))
                .then(function () { cats = cats.filter(function (x) { return x !== c; }); });
        });
    });
    return steps;
}

let leaving = false;
// For the delete below, in its own block: a deleted project has nothing unsaved
window.sfProjectEditLeaving = function () { leaving = true; };
editForm.addEventListener('submit', function (e) {
    e.preventDefault();
    saveError.textContent = '';
    if (!fields.name.value.trim()) { saveError.textContent = 'נא להזין שם לפרויקט'; return; }
    if (!fields.trackExpense.checked && !fields.trackIncome.checked && !fields.trackSavings.checked) {
        saveError.textContent = 'נא לבחור לפחות מחלקה אחת למעקב';
        return;
    }
    if (document.querySelector('.category-row.editing')) {
        saveError.textContent = 'יש קטגוריה בעריכה — אשרו או בטלו אותה קודם';
        return;
    }

    saveBtn.disabled = true;
    const steps = saveSteps();
    let chain = Promise.resolve();
    steps.forEach(function (step) { chain = chain.then(step); });
    chain.then(function () {
        if (window.sfForgetCategories) window.sfForgetCategories();
        leaving = true;
        window.sfToastAfterReload('הפרויקט עודכן');
        window.location.href = detailUrl;
    }).catch(function (err) {
        saveBtn.disabled = false;
        if (window.sfForgetCategories) window.sfForgetCategories();
        render();
        showOwnership();
        saveError.textContent = (err && err.message && !/^Failed to fetch|NetworkError|Load failed/.test(err.message))
            ? err.message : window.sfNetError();
    });
});

// ── Leaving without saving ──

window.addEventListener('beforeunload', function (e) {
    if (leaving || !isDirty()) return;
    e.preventDefault();
    e.returnValue = '';
});

document.addEventListener('click', function (e) {
    const link = e.target.closest('.project-save-cancel, .hero-top a.stats-nav-btn');
    if (!link || !isDirty()) return;
    e.preventDefault();
    window.appConfirm({
        title: 'לצאת בלי לשמור?',
        message: 'השינויים שעשיתם בעמוד הזה לא יישמרו.',
        confirmText: 'יציאה בלי שמירה',
        cancelText: 'המשך עריכה',
    }).then(function (ok) {
        if (!ok) return;
        leaving = true;
        window.location.href = link.href;
    });
});
syncSave();
}
if (!document.getElementById('projectEditView')) sfStartProjectEdit(window.sfData('sf-view-data').detailUrl);


/* ═══ Deleting the project ═══ (on the project page from 9.10 to 23.11; back
 * here, in the project's settings, since: Matan)
 *
 * **No `appConfirm` here**: the area opens, shows how many transactions and
 * how much money are at stake, and asks for an explicit choice. Another dialog
 * on top would be a second question about what is already fully shown.
 * Delegated: a soft refresh replaces the page after a transaction is added. */
(function () {
function parts() {
    return {
        toggle: document.getElementById('toggleDeleteProjectBtn'),
        form:   document.getElementById('deleteProjectForm'),
        error:  document.getElementById('deleteProjectError'),
    };
}

document.addEventListener('click', function (e) {
    const p = parts();
    if (!p.form) return;

    if (e.target.closest('#toggleDeleteProjectBtn')) {
        // `visible` and not `open`: the class `.password-form` already uses

        const open = p.form.classList.toggle('visible');
        p.toggle.setAttribute('aria-expanded', String(open));
        return;
    }
    if (e.target.closest('#cancelDeleteProjectBtn')) {
        p.form.classList.remove('visible');
        p.toggle.setAttribute('aria-expanded', 'false');
        p.error.textContent = '';
        return;
    }
    const go = e.target.closest('#confirmDeleteProjectBtn');
    if (!go || go.disabled) return;
    go.disabled = true;
    p.error.textContent = '';
    fetch('/api/projects/' + go.dataset.id, {
        method:  'DELETE',
        headers: { 'Content-Type': 'application/json' },
    })
    .then(r => r.json().catch(() => ({})).then(d => ({ ok: r.ok, d: d })))
    .then(function (res) {
        if (!res.ok || res.d.status !== 'ok') {
            p.error.textContent = res.d.error || 'המחיקה נכשלה — נסו שוב';
            go.disabled = false;
            return;
        }
        // `deleted` is the number of transactions actually deleted: the server
        // returns it so we don't have to promise a number that wasn't checked.

        const wiped = res.d.deleted || 0;
        try {
            window.sfToastAfterReload(wiped
                ? 'הפרויקט נמחק, ואיתו ' + window.sfCount(wiped, 'עסקה אחת', 'עסקאות')
                : 'הפרויקט נמחק');
        } catch (err) { /* no storage: give up the toast, not the navigation */ }
        window.sfProjectEditLeaving();       // deleted: nothing unsaved to warn about
        window.location.href = '/projects';
    })
    .catch(function () {
        p.error.textContent = window.sfNetError();
        go.disabled = false;
    });
});
})();


/* ═══ The page that loads its own data (`project_edit_view.html`) ═══
 *
 * The form arrives empty and hidden. This asks `view_url`
 * (`/api/screens/project/<id>/edit`), fills every field and the sentences
 * around them (all ready from the server), shows the form, and only then starts
 * the editing above, so "save" is enabled only by a real change. Fields get
 * their values as defaults too (`defaultValue`, `defaultChecked`), as if they
 * had arrived in the HTML.
 *
 * A project that is no longer there (404) sends the user to the list; a failed
 * load says so and offers another try. There is no refresh in place: this page
 * holds unsaved edits, and redrawing it would throw them away. Everything is
 * written with `textContent` or `.value`: the name and description are the
 * user's own text. */
(function () {
const holder = document.getElementById('projectEditView');
if (!holder) return;
const byId = id => document.getElementById(id);

function setValue(el, value) {
    el.value = value;
    el.defaultValue = value;
}
function setChecked(el, on) {
    el.checked = on;
    el.defaultChecked = on;
}

function fill(view) {
    document.title = view.title;
    byId('projectEditSubtitle').textContent = view.name;
    byId('projectBackLink').href = view.detail_href;
    byId('projectCancelLink').href = view.detail_href;

    const f = view.fields;
    setValue(byId('editProjectIcon'), f.icon);
    setValue(byId('editProjectName'), f.name);
    setValue(byId('editProjectDescription'), f.description);
    const budget = byId('editProjectBudget');
    budget.value = f.budget;
    // "12000" → "12,000", as `sfThousands` did to the server-built field; it
    // already ran on this one while it was empty, so it formats on `input`
    budget.dispatchEvent(new Event('input'));
    budget.defaultValue = budget.value;
    setChecked(byId('editProjectTrackExpense'), f.track_expense);
    setChecked(byId('editProjectTrackIncome'), f.track_income);
    setChecked(byId('editProjectTrackSavings'), f.track_savings);

    byId('editProjectBudgetLabel').textContent = (view.kind === 'savings' ? 'יעד חיסכון' : 'תקציב') + ' (אופציונלי)';
    const hint = byId('budgetTargetHint');
    hint.textContent = hint.dataset[view.kind];
    document.querySelectorAll('#editProjectKind .kind-card').forEach(function (card) {
        card.setAttribute('aria-checked', card.dataset.kind === view.kind ? 'true' : 'false');
        card.disabled = view.kind_locked;
    });
    byId('editProjectKindLocked').hidden = !view.kind_locked;

    const own = view.ownership, label = byId('ownershipLabel');
    if (own.personal) label.replaceChildren(byId('tplLock').content.cloneNode(true), ' ' + own.label);
    else label.textContent = own.label;
    byId('ownershipHint').textContent = own.hint;
    // The one button this project has; the script above finds it by its id
    if (own.button !== 'share') byId('shareProjectBtn').remove();
    if (own.button !== 'unshare') byId('unshareProjectBtn').remove();
    else byId('unshareProjectBtn').dataset.othersNote = own.others_note;

    const tabs = byId('projectCatTabs');
    tabs.style.gridTemplateColumns = 'repeat(' + view.tabs.length + ', 1fr)';
    tabs.replaceChildren.apply(tabs, view.tabs.map(function (t, i) {
        const btn = byId('tplCatTab').content.firstElementChild.cloneNode(true);
        if (!i) btn.classList.add('active');
        btn.dataset.catTab = t.type;
        btn.setAttribute('aria-selected', i ? 'false' : 'true');
        btn.textContent = t.label;
        return btn;
    }));
    byId('projectCategoriesArea').dataset.projectId = view.id;

    byId('deleteProjectHint').textContent = view.delete.hint;
    byId('deleteProjectWarning').textContent = view.delete.warning;
    const go = byId('confirmDeleteProjectBtn');
    go.dataset.id = view.id;
    go.dataset.name = view.name;
}

let started = false;
function load() {
    if (started || holder.dataset.loading) return;
    holder.dataset.loading = '1';
    byId('projectEditError').hidden = true;
    fetch(holder.dataset.viewUrl, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
        .then(function (r) {
            // 401: `auth-guard.js` is already sending the user to sign in
            if (r.status === 401) return null;
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
        })
        .then(function (view) {
            if (!view) return;
            fill(view);
            byId('projectEditBody').hidden = false;
            holder.removeAttribute('aria-busy');
            started = true;
            sfStartProjectEdit(view.detail_href);
            window.dispatchEvent(new CustomEvent('sf:view-entered', { detail: { root: holder } }));
        })
        .catch(function () {
            byId('projectEditErrorText').textContent = window.sfNetError ? window.sfNetError() : 'נסו שוב';
            byId('projectEditError').hidden = false;
            holder.removeAttribute('aria-busy');
        })
        .finally(function () { delete holder.dataset.loading; });
}

byId('projectEditRetry').addEventListener('click', load);
load();
})();
