(function () {

// ── אקורדיון: פתיחה/סגירה של קבוצות ──
// תיבה אחת פתוחה בכל רגע (מתן, 1.10): פתיחה סוגרת את מה שהיה פתוח.
const settingsHeaders = document.querySelectorAll('.settings-group-header');
settingsHeaders.forEach(function (header) {
    header.addEventListener('click', function () {
        const group = header.closest('.settings-group');
        const isOpen = group.classList.toggle('open');
        header.setAttribute('aria-expanded', isOpen);
        if (!isOpen) return;
        let closedAbove = false;
        settingsHeaders.forEach(function (other) {
            const otherGroup = other.closest('.settings-group');
            if (otherGroup === group || !otherGroup.classList.contains('open')) return;
            otherGroup.classList.remove('open');
            other.setAttribute('aria-expanded', 'false');
            if (otherGroup.compareDocumentPosition(group) & Node.DOCUMENT_POSITION_FOLLOWING) closedAbove = true;
        });
        // תיבה פתוחה מעליה נסגרה — העמוד קפץ והתיבה החדשה ברחה למעלה.
        if (closedAbove) group.scrollIntoView({ block: 'start', behavior: 'smooth' });
        // נפתחת בהחלקה (סבב תנועה, 7.10). רק הפתיחה: הקבוצה שנסגרת למעלה
        // נסגרת מיד, אחרת הגלילה אל החדשה מחושבת כשהעמוד עוד זז
        const body = group.querySelector && group.querySelector('.settings-group-body');
        if (body && window.sfReveal) window.sfReveal(body, true, function () {});
    });
});

// ── טאבים של סוגי קטגוריות ──
const catTabs   = document.querySelectorAll('.cat-type-tabs .toggle-btn');
const catPanels = document.querySelectorAll('.cat-tab-panel');
const newCatType = document.getElementById('newCatType');

catTabs.forEach(function (btn) {
    btn.addEventListener('click', function () {
        catTabs.forEach(function (b) {
            b.classList.remove('active');
            b.setAttribute('aria-selected', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');
        const tab = btn.dataset.catTab;
        catPanels.forEach(p => { p.style.display = (p.dataset.tabPanel === tab) ? '' : 'none'; });
        newCatType.value = tab;
    });
});

// ── מצב ניהול קטגוריות: חושף עריכה/מחיקה/הוספה ──
const manageBtn = document.getElementById('manageCatsBtn');
const catsArea  = document.getElementById('categoriesArea');

manageBtn.addEventListener('click', function () {
    const managing = catsArea.classList.toggle('managing');
    manageBtn.textContent = managing ? 'סיום' : 'ניהול';
    manageBtn.classList.toggle('btn-primary', managing);
    manageBtn.classList.toggle('btn-ghost', !managing);
});

// ── Family name edit ──
const nameDisplay   = document.getElementById('familyNameDisplay');
const nameEdit      = document.getElementById('familyNameEdit');
const nameInput     = document.getElementById('familyNameInput');
const editBtn       = document.getElementById('editFamilyNameBtn');
const saveBtn       = document.getElementById('saveFamilyNameBtn');
const cancelBtn     = document.getElementById('cancelFamilyNameBtn');

if (editBtn) {
    editBtn.addEventListener('click', function () {
        nameEdit.classList.add('visible');
        nameInput.focus();
    });
}

if (cancelBtn) {
    cancelBtn.addEventListener('click', function () {
        nameEdit.classList.remove('visible');
        nameInput.value = nameDisplay.textContent;
        nameEdit.dispatchEvent(new Event('input'));      // "שמירה" חוזר לכבוי
    });
}

if (saveBtn) {
    saveBtn.addEventListener('click', function () {
        const newName = nameInput.value.trim();
        if (!newName) return;
        saveBtn.disabled = true;
        fetch('/api/family', {
            method:  'PUT',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ name: newName }),
        })
        .then(r => r.json())
        .then(function (d) {
            saveBtn.disabled = false;
            if (d.status === 'ok') {
                nameDisplay.textContent = newName;
                nameInput.defaultValue = newName;           // השם השמור — "שמירה" כבוי עד שינוי הבא
                // גם השם בראש העמוד — בלי זה הוא נשאר הישן עד רענון
                document.querySelectorAll('.settings-family-name').forEach(el => { el.textContent = newName; });
                nameEdit.classList.remove('visible');
            } else {
                window.showToast('השמירה נכשלה', 'error');
            }
        })
        .catch(function () {
            saveBtn.disabled = false;
            window.showToast(window.sfNetError(), 'error');
        });
    });
}

// ── Copy invite code ──
const copyBtn    = document.getElementById('copyCodeBtn');
const inviteCode = document.getElementById('inviteCode');

// "שליחה" כשאפשר לשתף, "העתקה" כשלא (מחשב)
const SHARE_LABEL = navigator.share ? 'שליחה' : 'העתקה';
if (copyBtn && inviteCode) {
    copyBtn.textContent = SHARE_LABEL;
    copyBtn.addEventListener('click', function () {
        const famEl = document.getElementById('familyNameInput');
        const msg = window.sfInviteMessage(inviteCode.textContent.trim(),
                                           famEl && famEl.value.trim());
        // בטלפון — חלון השיתוף של המערכת (וואטסאפ ישירות), כמו בכניסה הראשונה
        // (‎welcome-invite.js‎; מתן, 12.10). במחשב, או אם השיתוף נכשל — העתקה.
        const copy = function () {
            window.copyToClipboard(msg, inviteCode)
                .then(function (copied) {
                    if (!copied) return;      // הודעה כבר הוצגה, והקוד מסומן
                    copyBtn.textContent = '✓ הועתקה הזמנה';
                    setTimeout(function () { copyBtn.textContent = SHARE_LABEL; }, 2000);
                });
        };
        if (navigator.share) {
            navigator.share({ text: msg }).catch(function (err) {
                if (err && err.name === 'AbortError') return;     // סגרו את חלון השיתוף
                copy();
            });
            return;
        }
        copy();
    });
}

// ── Join another family ──
// עד היום רגע ההרשמה היה ההזדמנות היחידה להזין קוד, ומי שנרשם לפני
// שקיבל אותו נשאר תקוע במשפחה משלו בלי דרך חזרה.
const joinBtn   = document.getElementById('joinFamilyBtn');
const joinInput = document.getElementById('joinCodeInput');
const joinError = document.getElementById('joinFamilyError');

if (joinBtn && joinInput) {
    joinBtn.addEventListener('click', function () {
        const code = joinInput.value.trim();
        joinError.textContent = '';
        if (!code) {
            joinError.textContent = 'נא להזין קוד הזמנה';
            return;
        }

        function send(confirmed) {
            return fetch('/api/family/join', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ code: code, confirm: confirmed }),
            }).then(r => r.json().then(d => ({ status: r.status, data: d })));
        }

        joinBtn.disabled = true;
        joinBtn.textContent = 'מצטרפים…';

        send(false)
            .then(function (res) {
                // 409 = למשפחה הנוכחית יש תנועות, והמעבר ינתק אותן.
                // מאשרים מפורשות עם המספר לפני שממשיכים.
                if (res.status === 409 && res.data.needs_confirm) {
                    const n = res.data.transaction_count;
                    // n יכול להיות null כשספירת התנועות עצמה נכשלה. עדיין
                    // מבקשים אישור — דילוג עליו בגלל תקלה הוא בדיוק האובדן
                    // שהוא קיים כדי למנוע — רק בלי להמציא מספר.
                    const howMany = (typeof n === 'number')
                        ? `יש ${n} עסקאות`
                        : 'יש עסקאות קיימות';
                    // ‎appConfirm‎ ולא ‎confirm‎ המקומי. זו הייתה הקריאה
                    // היחידה בכל האפליקציה לדיאלוג של הדפדפן — ודווקא על
                    // השאלה המסוכנת ביותר במוצר. ב-PWA מותקן הוא מרונדר
                    // עם שם המארח מעליו ("smartfin… אומר"), משמאל לימין
                    // ובלי שום עיצוב: הרגע שבו האפליקציה נראית הכי פחות
                    // כמו עצמה הוא הרגע שבו המשתמש הכי צריך לבטוח בה.
                    return window.appConfirm({
                        title: 'לעבור למשפחה אחרת?',
                        message: `למשפחה הנוכחית שלכם ${howMany}. מעבר למשפחה אחרת ` +
                                 'מנתק אתכם מהן — הן יישארו במשפחה הישנה ולא תוכלו ' +
                                 'לראות אותן יותר.',
                        confirmText: 'מעבר למשפחה החדשה',
                    }).then(function (ok) {
                        if (!ok) return null;
                        return send(true);
                    });
                }
                return res;
            })
            .then(function (res) {
                if (res === null) return;
                if (res.status >= 400) {
                    joinError.textContent = res.data.error || 'ההצטרפות נכשלה';
                    return;
                }
                window.location.reload();
            })
            .catch(function () {
                joinError.textContent = window.sfNetError();
            })
            .finally(function () {
                joinBtn.disabled = false;
                joinBtn.textContent = 'הצטרפות';
            });
    });
}

// הסרת עסקה קבועה עברה ל-transactions.js: אותה רשימה מוצגת גם בעמוד
// החודש, ו-settings.js לא נטען שם. עותק שני היה הופך כל תיקון לשניים.

// ── סדר קטגוריות (▲▼) ──
/* שם ואייקון בשורת קטגוריה — דרך ‎textContent‎ ומאפיינים, לא דרך HTML.
 * משותף לשינוי שם ולשורה חדשה שנבנית מה-‎<template>‎ של התבנית. */
function applyCategoryName(row, name, icon) {
    row.dataset.name = name;
    row.dataset.icon = icon;
    row.querySelector('.cat-row-name').textContent = name;
    row.querySelector('.cat-row-icon').textContent = icon;
    const sw = row.querySelector('.budget-enabled');
    if (sw) sw.setAttribute('aria-label', 'קביעת תקציב חודשי ל' + name);
    const amount = row.querySelector('.budget-amount');
    if (amount) amount.setAttribute('aria-label', 'תקציב חודשי ל' + name);
}

function forgetCategories() {
    if (window.sfForgetCategories) window.sfForgetCategories();
}

function updatePanelReorderState(panel) {
    if (!panel) return;
    const rows = Array.from(panel.querySelectorAll(':scope > .category-row'));
    rows.forEach(function (row, i) {
        const up   = row.querySelector('.cat-move-up');
        const down = row.querySelector('.cat-move-down');
        if (up)   up.disabled   = (i === 0);
        if (down) down.disabled = (i === rows.length - 1);
    });
}

function persistCategoryOrder(panel) {
    const type  = panel.dataset.tabPanel;
    const order = Array.from(panel.querySelectorAll(':scope > .category-row')).map(r => r.dataset.id);
    fetch('/api/categories/reorder', {
        method:  'PUT',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ type, order }),
    })
    .then(r => r.json())
    .then(function (d) {
        if (d.error) { window.showToast('שמירת הסדר נכשלה', 'error'); return; }
        forgetCategories();                 // הסדר קובע גם את הסדר ב-+
    })
    .catch(function () { window.showToast(window.sfNetError(), 'error'); });
}

document.querySelectorAll('.cat-tab-panel').forEach(updatePanelReorderState);

document.addEventListener('click', function (e) {
    const btn = e.target.closest('.cat-move-up, .cat-move-down');
    if (!btn) return;
    const row   = btn.closest('.category-row');
    const panel = btn.closest('.cat-tab-panel');
    if (!row || !panel) return;
    const sibling = btn.classList.contains('cat-move-up') ? row.previousElementSibling : row.nextElementSibling;
    if (!sibling || !sibling.classList.contains('category-row')) return;
    if (btn.classList.contains('cat-move-up')) {
        panel.insertBefore(row, sibling);
    } else {
        panel.insertBefore(sibling, row);
    }
    updatePanelReorderState(panel);
    persistCategoryOrder(panel);
});

// ── גרירה לסידור (מתן, 30.9 — סבב 6, פריט 5) ──
// מהידית בלבד. השורה עוברת מקום ברגע שמרכז האצבע חוצה את אמצע השכנה, והסדר
// נשמר פעם אחת בשחרור — אותה שמירה כמו החצים, שנשארים למקלדת ולקורא מסך.
document.addEventListener('pointerdown', function (e) {
    const handle = e.target.closest('.cat-drag-handle');
    if (!handle) return;
    const row   = handle.closest('.category-row');
    const panel = handle.closest('.cat-tab-panel');
    if (!row || !panel || row.classList.contains('editing')) return;
    e.preventDefault();
    const before = Array.from(panel.querySelectorAll(':scope > .category-row')).map(r => r.dataset.id).join();
    row.classList.add('dragging');
    // ההאזנה על document ולא על הידית: הזזת השורה ב-DOM משחררת את
    // ‎setPointerCapture‎, והגרירה נעצרה אחרי צעד אחד — ובלי ‎pointerup‎
    // הסדר גם לא נשמר
    const id = e.pointerId;

    function move(ev) {
        if (ev.pointerId !== id) return;
        const y = ev.clientY;
        const prev = row.previousElementSibling;
        const next = row.nextElementSibling;
        if (prev && prev.classList.contains('category-row')) {
            const r = prev.getBoundingClientRect();
            if (y < r.top + r.height / 2) { panel.insertBefore(row, prev); return; }
        }
        if (next && next.classList.contains('category-row')) {
            const r = next.getBoundingClientRect();
            if (y > r.top + r.height / 2) panel.insertBefore(next, row);
        }
    }
    function end(ev) {
        if (ev.pointerId !== id) return;
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', end);
        document.removeEventListener('pointercancel', end);
        row.classList.remove('dragging');
        updatePanelReorderState(panel);
        const after = Array.from(panel.querySelectorAll(':scope > .category-row')).map(r => r.dataset.id).join();
        if (after !== before) persistCategoryOrder(panel);
    }
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', end);
    document.addEventListener('pointercancel', end);
});

// ── Edit category (inline) ──
document.addEventListener('click', function (e) {
    const btn = e.target.closest('.edit-cat-btn');
    if (!btn) return;
    const row = btn.closest('.category-row');
    if (!row || row.classList.contains('editing')) return;

    const id        = btn.dataset.id;
    const origIcon  = row.dataset.icon || '📦';
    const origName  = row.dataset.name || '';

    row.classList.add('editing');
    row.dataset.origHtml = row.innerHTML;
    row.innerHTML = `
        <div class="cat-edit-form">
            <div class="add-cat-row">
                <input class="form-input emoji-input cat-edit-icon" type="text" maxlength="2">
                <input class="form-input cat-edit-name" type="text" maxlength="30">
            </div>
            <div class="edit-actions">
                <button class="btn-sm btn-primary cat-edit-save" data-id="${escapeHtml(id)}">שמירה</button>
                <button class="btn-sm btn-ghost cat-edit-cancel">ביטול</button>
            </div>
        </div>
    `;
    // ערכי המשתמש נכתבים דרך .value (לא אינטרפולציה) — בטוח מ-attribute-XSS
    row.querySelector('.cat-edit-icon').value = origIcon;
    row.querySelector('.cat-edit-name').value = origName;
    row.querySelector('.cat-edit-icon').defaultValue = origIcon;
    row.querySelector('.cat-edit-name').defaultValue = origName;
    if (window.sfSaveWhenChanged) window.sfSaveWhenChanged(row.querySelector('.cat-edit-form'), row.querySelector('.cat-edit-save'));
    row.querySelector('.cat-edit-name').focus();
});

document.addEventListener('click', function (e) {
    const cancelEditBtn = e.target.closest('.cat-edit-cancel');
    if (cancelEditBtn) {
        const row = cancelEditBtn.closest('.category-row');
        row.innerHTML = row.dataset.origHtml;
        row.classList.remove('editing');
        return;
    }

    const saveEditBtn = e.target.closest('.cat-edit-save');
    if (!saveEditBtn) return;
    const row  = saveEditBtn.closest('.category-row');
    const id   = saveEditBtn.dataset.id;
    const icon = row.querySelector('.cat-edit-icon').value.trim() || '📦';
    const name = row.querySelector('.cat-edit-name').value.trim();
    if (!name) { row.querySelector('.cat-edit-name').focus(); return; }

    saveEditBtn.disabled = true;
    fetch('/api/categories/' + id, {
        method:  'PUT',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ name, icon }),
    })
    .then(r => r.json())
    .then(function (d) {
        if (d.error) { window.showToast(d.error, 'error'); saveEditBtn.disabled = false; return; }
        row.classList.remove('editing');
        // השורה המקורית חוזרת במלואה — עם בקרת התקציב ובלי ✕ למי שאינו
        // מנהל — ורק השם והאייקון מתעדכנים. קודם היא נבנתה מחדש מ-
        // ‎catRowInnerHTML‎, שאין בו תקציב, ו"קביעת תקציב" נעלם עד רענון.
        row.innerHTML = row.dataset.origHtml;
        delete row.dataset.origHtml;
        applyCategoryName(row, name, icon);
        updatePanelReorderState(row.closest('.cat-tab-panel'));
        forgetCategories();
        window.showToast('הקטגוריה עודכנה');
    })
    .catch(function () { window.showToast(window.sfNetError(), 'error'); saveEditBtn.disabled = false; });
});

// ── Delete category ──
document.addEventListener('click', function (e) {
    const btn = e.target.closest('.delete-cat-btn');
    if (!btn) return;
    const id  = btn.dataset.id;
    const row = btn.closest('.category-row');
    if (!id || !row) return;
    const panel = row.closest('.cat-tab-panel');

    const catName = (row.querySelector('.cat-row-name') || {}).textContent || '';
    // שואל לאן להעביר את העסקאות, מוחק ומודיע — ראו ‎sfDeleteCategory‎ ב-core.js
    window.sfDeleteCategory('/api/categories/' + id, catName.trim()).then(function (deleted) {
        if (!deleted) return;
        row.style.opacity = '0';
        row.style.height  = '0';
        row.style.overflow = 'hidden';
        row.style.transition = 'all 0.25s';
        setTimeout(function () { row.remove(); updatePanelReorderState(panel); }, 260);
        forgetCategories();
    });
});

// ── החזרת קטגוריות ברירת המחדל (מתן, 31.10) ──
// קודם שואלים את השרת מה חסר, ומציגים את זה בשמות באישור — "נוסיף 3
// קטגוריות" לבד לא אומר למשתמש אם זה מה שהוא חיפש. רק הוספה, אף פעם לא מחיקה.
const restoreBtn = document.getElementById('restoreDefaultCatsBtn');
if (restoreBtn) {
    restoreBtn.addEventListener('click', function () {
        restoreBtn.disabled = true;
        fetch('/api/categories/defaults')
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (res) {
                if (!res.ok) throw new Error(res.d.error || '');
                const missing = res.d.missing || [];
                if (!missing.length) {
                    restoreBtn.disabled = false;
                    window.showToast('כל קטגוריות ברירת המחדל כבר קיימות');
                    return null;
                }
                const names = missing.map(function (c) { return c.icon + ' ' + c.name; }).join(' · ');
                return window.appConfirm({
                    title: 'להחזיר את קטגוריות ברירת המחדל?',
                    message: 'יתווספו ' + window.sfCount(missing.length, 'קטגוריה אחת', 'קטגוריות') + ': ' + names +
                             '. הקטגוריות הקיימות והעסקאות לא ישתנו.',
                    confirmText: 'הוספה',
                    danger: false,
                }).then(function (ok) {
                    if (!ok) { restoreBtn.disabled = false; return null; }
                    return fetch('/api/categories/defaults', { method: 'POST' })
                        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
                        .then(function (done) {
                            if (!done.ok) throw new Error(done.d.error || '');
                            forgetCategories();
                            window.sfToastAfterReload(window.sfCount(done.d.added, 'נוספה קטגוריה אחת', 'קטגוריות נוספו'));
                            window.location.reload();
                        });
                });
            })
            .catch(function (err) {
                restoreBtn.disabled = false;
                window.showToast((err && err.message) || window.sfNetError(), 'error');
            });
    });
}

// ── Add category (הסוג נקבע לפי הטאב הפעיל) ──
const addForm  = document.getElementById('addCatForm');
const addError = document.getElementById('addCatError');

if (addForm) {
    addForm.addEventListener('submit', function (e) {
        e.preventDefault();
        addError.textContent = '';

        const icon = document.getElementById('newCatIcon').value.trim() || '🏷';
        const name = document.getElementById('newCatName').value.trim();
        const type = newCatType.value;

        if (!name) {
            addError.textContent = 'נא להזין שם קטגוריה';
            return;
        }

        const addBtn = addForm.querySelector('button[type="submit"]');
        addBtn.disabled = true;
        fetch('/api/categories', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ name, icon, type }),
        })
        .then(r => r.json())
        .then(function (cat) {
            addBtn.disabled = false;
            if (cat.error) { addError.textContent = cat.error; return; }

            const listMap = { expense: 'expenseCatList', income: 'incomeCatList', savings: 'savingsCatList' };
            const list    = document.getElementById(listMap[type]);
            const tpl     = document.getElementById('newCategoryRow-' + type);
            if (list && tpl) {
                // אותה שורה בדיוק כמו בטעינה (המאקרו ‎category_row‎ בתבנית),
                // כולל בקרת התקציב — ולא גרסה שנבנתה כאן בנפרד
                const li = tpl.content.querySelector('.category-row').cloneNode(true);
                li.dataset.id = cat.id;
                li.querySelectorAll('[data-id]').forEach(function (el) { el.dataset.id = cat.id; });
                applyCategoryName(li, cat.name, cat.icon);
                list.appendChild(li);
                updatePanelReorderState(list);
            }
            forgetCategories();

            document.getElementById('newCatName').value = '';
            document.getElementById('newCatIcon').value = '🏷';
        })
        .catch(function () {
            addBtn.disabled = false;
            addError.textContent = window.sfNetError();
        });
    });
}

// ── Edit account profile ──
const editProfileBtn  = document.getElementById('editProfileBtn');
const profileEdit     = document.getElementById('profileEdit');
const saveProfileBtn  = document.getElementById('saveProfileBtn');
const cancelProfileBtn = document.getElementById('cancelProfileBtn');
const profileError    = document.getElementById('profileError');
const accountNameDisplay = document.getElementById('accountNameDisplay');

if (editProfileBtn) {
    editProfileBtn.addEventListener('click', function () {
        profileEdit.classList.toggle('visible');
    });
}

if (cancelProfileBtn) {
    cancelProfileBtn.addEventListener('click', function () {
        profileEdit.classList.remove('visible');
        profileError.textContent = '';
        // "ביטול" באמת מבטל — חזרה לשמור, ו"שמירה" כבוי (מתן, 13.10)
        profileEdit.querySelectorAll('input').forEach(function (el) { el.value = el.defaultValue; });
        profileEdit.dispatchEvent(new Event('input'));
    });
}

function submitProfile(workplaceScope) {
    const firstName = document.getElementById('editFirstName').value.trim();
    const lastName  = document.getElementById('editLastName').value.trim();
    const workplace = document.getElementById('editWorkplace').value.trim();

    saveProfileBtn.disabled = true;
    fetch('/api/profile', {
        method:  'PUT',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
            first_name: firstName, last_name: lastName, workplace: workplace,
            workplace_scope: workplaceScope || undefined,
        }),
    })
    .then(r => r.json())
    .then(function (d) {
        saveProfileBtn.disabled = false;
        if (d.error) { profileError.textContent = d.error; return; }
        accountNameDisplay.textContent = d.full_name;
        // והשורה שלי ברשימת בני המשפחה
        document.querySelectorAll('[data-my-name]').forEach(function (el) { el.textContent = d.full_name; });
        // גם בראש העמוד — בלי זה הוא נשאר הישן עד רענון (מתן, 1.10)
        const heroName = document.getElementById('heroProfileName');
        if (heroName) heroName.textContent = d.full_name;

        const workplaceDisplay = document.getElementById('accountWorkplaceDisplay');
        workplaceDisplay.textContent = d.workplace || '';
        workplaceDisplay.style.display = d.workplace ? '' : 'none';
        // הערך השמור הוא עכשיו נקודת ההשוואה. בלי זה "שינית את מקום העבודה"
        // נשאל שוב בכל שמירה הבאה של הפרופיל, גם כשלא נגעו בו.
        document.getElementById('editWorkplace').defaultValue = d.workplace || '';
        document.getElementById('editFirstName').defaultValue = document.getElementById('editFirstName').value;
        document.getElementById('editLastName').defaultValue = document.getElementById('editLastName').value;
        profileEdit.dispatchEvent(new Event('input'));

        profileEdit.classList.remove('visible');
    })
    .catch(function () {
        saveProfileBtn.disabled = false;
        profileError.textContent = window.sfNetError();
    });
}

if (saveProfileBtn) {
    saveProfileBtn.addEventListener('click', function () {
        const firstName = document.getElementById('editFirstName').value.trim();
        const lastName  = document.getElementById('editLastName').value.trim();
        const workplaceInput = document.getElementById('editWorkplace');
        const workplace = workplaceInput.value.trim();
        profileError.textContent = '';

        if (!firstName || !lastName) {
            profileError.textContent = 'נא למלא שם פרטי ושם משפחה';
            return;
        }

        // מקום עבודה השתנה — שואלים אם להחיל גם על היסטוריית משכורות
        // או רק מהחודש הנוכחי ואילך (ראה update_workplace_history בשרת)
        const workplaceChanged = workplace !== workplaceInput.defaultValue.trim();
        if (workplaceChanged) {
            window.appConfirm({
                title: 'לעדכן גם עסקאות משכורת קודמות?',
                message: 'שיניתם את מקום העבודה. אפשר לעדכן אותו על כל היסטוריית המשכורות, או רק מהחודש הנוכחי ואילך (עסקאות ישנות יותר ישמרו את מקום העבודה הקודם).',
                confirmText: 'כל ההיסטוריה',
                cancelText: 'רק מהחודש הזה',
                danger: false,
            }).then(function (updateAll) {
                // ‎null‎ = נסיגה (Escape, לחיצה בחוץ): לא שומרים כלום. עד היום
                // זה נשמר כ"רק מהחודש הזה" — בחירה שאף אחד לא עשה.
                if (updateAll === null) return;
                submitProfile(updateAll ? 'all' : 'future');
            });
        } else {
            submitProfile(null);
        }
    });
}

// ── Toggle password form ──
const togglePasswordBtn = document.getElementById('togglePasswordBtn');
const passwordForm      = document.getElementById('passwordForm');
const cancelPasswordBtn = document.getElementById('cancelPasswordBtn');

function resetPasswordForm() {
    document.getElementById('currentPassword').value = '';
    document.getElementById('newPassword').value = '';
    document.getElementById('newPasswordConfirm').value = '';
    document.getElementById('passwordError').textContent = '';
    document.getElementById('passwordSuccess').textContent = '';
    document.getElementById('passwordForm').dispatchEvent(new Event('input'));
}

if (togglePasswordBtn) {
    togglePasswordBtn.addEventListener('click', function () {
        passwordForm.classList.toggle('visible');
    });
}

if (cancelPasswordBtn) {
    cancelPasswordBtn.addEventListener('click', function () {
        passwordForm.classList.remove('visible');
        resetPasswordForm();
    });
}

// ── Change password ──
const changePasswordBtn = document.getElementById('changePasswordBtn');
if (changePasswordBtn) {
    changePasswordBtn.addEventListener('click', function () {
        const currentPassword = document.getElementById('currentPassword').value;
        const password        = document.getElementById('newPassword').value;
        const passwordConfirm = document.getElementById('newPasswordConfirm').value;
        const pwError   = document.getElementById('passwordError');
        const pwSuccess = document.getElementById('passwordSuccess');
        pwError.textContent = '';
        pwSuccess.textContent = '';

        if (!currentPassword) {
            pwError.textContent = 'נא להזין את הסיסמה הנוכחית';
            return;
        }
        if (password.length < 8) {
            pwError.textContent = 'הסיסמה החדשה חייבת להכיל לפחות 8 תווים';
            return;
        }
        if (password !== passwordConfirm) {
            pwError.textContent = 'הסיסמאות החדשות אינן תואמות';
            return;
        }

        changePasswordBtn.disabled = true;
        changePasswordBtn.textContent = 'מעדכן…';
        fetch('/api/profile/password', {
            method:  'PUT',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({
                current_password: currentPassword,
                password: password,
                password_confirm: passwordConfirm,
            }),
        })
        .then(r => r.json())
        .then(function (d) {
            changePasswordBtn.disabled = false;
            changePasswordBtn.textContent = 'עדכון הסיסמה';
            if (d.error) { pwError.textContent = d.error; return; }
            resetPasswordForm();
            pwSuccess.textContent = 'הסיסמה עודכנה בהצלחה';
        })
        .catch(function () {
            changePasswordBtn.disabled = false;
            changePasswordBtn.textContent = 'עדכון הסיסמה';
            pwError.textContent = window.sfNetError();
        });
    });
}

// ── מייל וטלפון בתיבת "חשבון" (מתן, 1.10) ──
function toggleForm(btnId, formId, onClose) {
    const btn = document.getElementById(btnId);
    const form = document.getElementById(formId);
    if (!btn || !form) return;
    btn.addEventListener('click', function () {
        if (!form.classList.toggle('visible')) onClose();
    });
}

// עזיבה/הצטרפות — חלונית אחת (מתן, 11.10)
(function () {
    const btn  = document.getElementById('toggleFamilyMoveBtn');
    const form = document.getElementById('familyMoveForm');
    if (!btn || !form) return;
    btn.addEventListener('click', function () {
        const open = form.classList.toggle('visible');
        btn.setAttribute('aria-expanded', String(open));
        if (!open) {
            const input = document.getElementById('joinCodeInput');
            const err = document.getElementById('joinFamilyError');
            if (input) input.value = '';
            if (err) err.textContent = '';
        }
    });
})();

function resetEmailForm() {
    document.getElementById('newEmail').value = '';
    document.getElementById('emailCurrentPassword').value = '';
    document.getElementById('emailError').textContent = '';
    document.getElementById('emailForm').dispatchEvent(new Event('input'));
}
function closeEmailForm() { document.getElementById('emailForm').classList.remove('visible'); resetEmailForm(); }
toggleForm('toggleEmailBtn', 'emailForm', resetEmailForm);
const cancelEmailBtn = document.getElementById('cancelEmailBtn');
if (cancelEmailBtn) cancelEmailBtn.addEventListener('click', closeEmailForm);

const saveEmailBtn = document.getElementById('saveEmailBtn');
if (saveEmailBtn) {
    saveEmailBtn.addEventListener('click', function () {
        const email = document.getElementById('newEmail').value.trim();
        const currentPassword = document.getElementById('emailCurrentPassword').value;
        const err = document.getElementById('emailError');
        err.textContent = '';
        if (!email) { err.textContent = 'נא להזין מייל חדש'; return; }
        if (!currentPassword) { err.textContent = 'נא להזין את הסיסמה הנוכחית'; return; }

        saveEmailBtn.disabled = true;
        fetch('/api/profile/email', {
            method:  'PUT',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ email: email, current_password: currentPassword }),
        })
        .then(r => r.json())
        .then(function (d) {
            saveEmailBtn.disabled = false;
            if (d.error) { err.textContent = d.error; return; }
            document.getElementById('emailPendingValue').textContent = d.pending_email;
            document.getElementById('emailPendingNote').hidden = false;
            closeEmailForm();
            window.showToast('נשלח קישור אישור למייל החדש');
        })
        .catch(function () {
            saveEmailBtn.disabled = false;
            err.textContent = window.sfNetError();
        });
    });
}

const phoneInput = document.getElementById('newPhone');
function resetPhoneForm() {
    if (phoneInput) phoneInput.value = phoneInput.defaultValue;
    const err = document.getElementById('phoneError');
    if (err) err.textContent = '';
    document.getElementById('phoneForm').dispatchEvent(new Event('input'));
}
function closePhoneForm() { document.getElementById('phoneForm').classList.remove('visible'); resetPhoneForm(); }
toggleForm('togglePhoneBtn', 'phoneForm', resetPhoneForm);
const cancelPhoneBtn = document.getElementById('cancelPhoneBtn');
if (cancelPhoneBtn) cancelPhoneBtn.addEventListener('click', closePhoneForm);

const savePhoneBtn = document.getElementById('savePhoneBtn');
if (savePhoneBtn) {
    savePhoneBtn.addEventListener('click', function () {
        const err = document.getElementById('phoneError');
        err.textContent = '';
        savePhoneBtn.disabled = true;
        fetch('/api/profile/phone', {
            method:  'PUT',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ phone: phoneInput.value.trim() }),
        })
        .then(r => r.json())
        .then(function (d) {
            savePhoneBtn.disabled = false;
            if (d.error) { err.textContent = d.error; return; }
            document.getElementById('accountPhoneValue').textContent = d.phone;
            phoneInput.defaultValue = d.phone;
            // גם השורה שלי ב"המשפחה שלי"
            const row = document.getElementById('accountPhoneDisplay');
            if (row) { row.textContent = d.phone; row.style.display = ''; }
            closePhoneForm();
            window.showToast('הטלפון עודכן');
        })
        .catch(function () {
            savePhoneBtn.disabled = false;
            err.textContent = window.sfNetError();
        });
    });
}

// ── איפוס חשבון: מחיקת כל העסקאות (החשבון נשמר) ──
const toggleResetBtn  = document.getElementById('toggleResetAccountBtn');
const resetForm       = document.getElementById('resetAccountForm');
const cancelResetBtn  = document.getElementById('cancelResetAccountBtn');
const confirmResetBtn = document.getElementById('confirmResetAccountBtn');
const resetPwInput    = document.getElementById('resetAccountPassword');
const resetError      = document.getElementById('resetAccountError');
const scopeFamilyBtn  = document.getElementById('resetScopeFamily');
const scopeMineBtn    = document.getElementById('resetScopeMine');
// מי שאינו מנהל המשפחה לא מקבל את הכפתור "כל עסקאות המשפחה" בכלל,
// אז ברירת המחדל נגזרת ממה שקיים על המסך ולא מקובעת ל-'family'.
let resetScope = scopeFamilyBtn ? 'family' : 'mine';

if (toggleResetBtn) {
    [scopeFamilyBtn, scopeMineBtn].filter(Boolean).forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (scopeFamilyBtn) scopeFamilyBtn.classList.toggle('active', btn === scopeFamilyBtn);
            if (scopeMineBtn)   scopeMineBtn.classList.toggle('active', btn === scopeMineBtn);
            resetScope = btn.dataset.scope;
        });
    });

    toggleResetBtn.addEventListener('click', function () {
        resetForm.classList.toggle('visible');
    });
    cancelResetBtn.addEventListener('click', function () {
        resetForm.classList.remove('visible');
        resetPwInput.value = '';
        resetError.textContent = '';
    });

    confirmResetBtn.addEventListener('click', function () {
        resetError.textContent = '';
        if (!resetPwInput.value) {
            resetError.textContent = 'נא להזין את הסיסמה הנוכחית';
            return;
        }
        // מספר הוא מה שגורם לעצור, לא המשפט "פעולה סופית". הספירה
        // מגיעה מהשרת עם העמוד; אם היא לא הצליחה, לא ממציאים מספר.
        const txCount = parseInt((resetForm.dataset.txCount || ''), 10);
        const count   = (resetScope === 'family' && !isNaN(txCount))
            ? ' ' + window.sfCount(txCount, 'עסקה אחת תימחק.', 'עסקאות יימחקו.')
            : '';
        window.appConfirm({
            title: resetScope === 'family'
                ? 'לאפס את כל עסקאות המשפחה?'
                : 'למחוק את כל העסקאות שלך?',
            message: count + ' פעולה זו סופית ולא ניתנת לביטול.',
            confirmText: 'איפוס העסקאות',
        }).then(function (ok) {
            if (!ok) return;
            confirmResetBtn.disabled = true;
            confirmResetBtn.textContent = 'מאפס…';
            fetch('/api/account/reset', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ password: resetPwInput.value, scope: resetScope }),
            })
            .then(r => r.json())
            .then(function (d) {
                if (d.error) {
                    confirmResetBtn.disabled = false;
                    confirmResetBtn.textContent = 'איפוס העסקאות';
                    resetError.textContent = d.error;
                    return;
                }
                window.sfToastAfterReload('כל העסקאות נמחקו');
                window.location.href = '/';
            })
            .catch(function () {
                confirmResetBtn.disabled = false;
                confirmResetBtn.textContent = 'איפוס העסקאות';
                resetError.textContent = window.sfNetError();
            });
        });
    });
}

// ── מחיקת חשבון לצמיתות ──
const toggleDeleteBtn  = document.getElementById('toggleDeleteAccountBtn');
const deleteForm       = document.getElementById('deleteAccountForm');
const cancelDeleteBtn  = document.getElementById('cancelDeleteAccountBtn');
const confirmDeleteBtn = document.getElementById('confirmDeleteAccountBtn');
const deletePwInput    = document.getElementById('deleteAccountPassword');
const deleteError      = document.getElementById('deleteAccountError');

if (toggleDeleteBtn) {
    toggleDeleteBtn.addEventListener('click', function () {
        deleteForm.classList.toggle('visible');
    });
    cancelDeleteBtn.addEventListener('click', function () {
        deleteForm.classList.remove('visible');
        deletePwInput.value = '';
        deleteError.textContent = '';
    });

    confirmDeleteBtn.addEventListener('click', function () {
        deleteError.textContent = '';
        if (!deletePwInput.value) {
            deleteError.textContent = 'נא להזין את הסיסמה הנוכחית';
            return;
        }
        window.appConfirm({
            title: 'למחוק את החשבון לצמיתות?',
            message: 'פעולה זו סופית ולא ניתנת לביטול — כל הנתונים שלך יימחקו.',
            confirmText: 'מחיקה לצמיתות',
        }).then(function (ok) {
            if (!ok) return;
            confirmDeleteBtn.disabled = true;
            confirmDeleteBtn.textContent = 'מוחק…';
            fetch('/api/account', {
                method:  'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({ password: deletePwInput.value }),
            })
            .then(r => r.json())
            .then(function (d) {
                if (d.error) {
                    confirmDeleteBtn.disabled = false;
                    confirmDeleteBtn.textContent = 'מחיקה לצמיתות';
                    deleteError.textContent = d.error;
                    return;
                }
                window.location.href = '/login';
            })
            .catch(function () {
                confirmDeleteBtn.disabled = false;
                confirmDeleteBtn.textContent = 'מחיקה לצמיתות';
                deleteError.textContent = window.sfNetError();
            });
        });
    });
}

})();

/* ‎savePrefs‎ יושב כאן, ברמת הקובץ, ולא בתוך אחד הבלוקים.
 *
 * הוא היה מוגדר בתוך הבלוק של "העדפות משפחה", וקוד תקציבי הקטגוריות
 * יושב בבלוק אחר לגמרי — שני תחומים נפרדים. כלומר כל ניסיון לשמור
 * תקציב זרק ‎ReferenceError: savePrefs is not defined‎, בתוך ‎setTimeout‎
 * שאף אחד לא צופה בו: בלי הודעה, בלי שגיאה על המסך, בלי כלום. שדה
 * התקציב פשוט לא עשה שום דבר, אף פעם.
 *
 * הבדיקות לא תפסו את זה כי הן חיפשו את המחרוזת ‎savePrefs({ limits:‎
 * בקוד המקור — והיא הייתה שם. נוכחות בטקסט אינה נגישות בתחום.
 */
/* ‎revert‎ הוא הלב כאן. המתג מתהפך בעצמו בלחיצה, לפני שהשרת בכלל נשאל.
 * כשהשמירה נכשלה הוצגה הודעה אדומה ל-2.6 שניות — והמתג נשאר במצב החדש.
 * המשתמש ראה הבהוב, המסך הראה "מופעל", והוא ניווט משם בביטחון שזה נשמר.
 * ברענון הבא זה חזר לאחור, בלי שום קשר גלוי למה שקרה.
 *
 * מסך שמראה מצב שלא נשמר גרוע מהודעת שגיאה: הוא לא רק לא מודיע, הוא
 * מבטיח. */
function savePrefs(patch, revert) {
    function failed(message) {
        if (revert) revert();
        window.showToast(message, 'error');
    }

    fetch('/api/family/settings', {
        method:  'PUT',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(patch),
    })
    .then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, data: d }; });
    })
    .then(function (res) {
        if (!res.ok || res.data.error) {
            failed(res.data.error || 'שמירת ההעדפות נכשלה');
            return;
        }
        // המודאל הגלובלי קורא מכאן — בורר "של מי?" מתעדכן בלי רענון
        window.SF_ATTRIBUTION = res.data.settings.owner_attribution;
        window.showToast('ההעדפות נשמרו');
    })
    .catch(function () { failed(window.sfNetError()); });
}


// ── שיוך עסקאות: שמירה מיידית של כל שינוי ──
// התראות על חריגות ו"מקום עבודה על שורות משכורת" ירדו מכאן (מתן, 11.10):
// מי שרוצה התראות קובע תקציב לקטגוריה, ומקום העבודה מוצג תמיד.
(function () {
const attrSwitches = document.querySelectorAll('[data-pref-type]');

attrSwitches.forEach(function (sw) {
    sw.addEventListener('change', function () {
        const box = this, was = !box.checked;
        const patch = {};
        patch[box.dataset.prefType] = box.checked;
        savePrefs({ owner_attribution: patch }, function () { box.checked = was; });
    });
});
})();

/* ─── ניהול חברי המשפחה ──────────────────────────────────────────────────────
 *
 * עד עכשיו מי שהצטרף למשפחה — בטעות, או עם קוד שדלף בוואטסאפ — נשאר בה
 * לתמיד: אין הסרה, אין עזיבה, והקוד נוצר פעם אחת ואי אפשר לשנותו.
 */
(function () {
    const KEEP  = 'keep';
    const WIPE  = 'delete';

    /* הבחירה על העסקאות נשאלת ולא מוכרעת בשקט. הכסף יצא מהתקציב המשותף,
     * אז "להשאיר" שומר את הסיכומים החודשיים נכונים והעסקאות מוצגות
     * כ"משותפות"; "למחוק" משנה למפרע כל חודש שבו הן מופיעות.
     *
     * המחיקה היא דווקא כפתור האישור, וזה מכוון: appConfirm מחזיר false גם
     * על Escape, על לחיצה מחוץ לדיאלוג ועל ✕. אילו "להשאיר" היה האישור,
     * כל בריחה מהדיאלוג הייתה מוחקת עסקאות — בדיוק הבאג שקיים היום
     * במחיקת פרויקט. כך כל דרך יציאה מגיעה לאפשרות הבטוחה. */
    function askAboutTransactions(title, message) {
        return window.appConfirm({
            title: title,
            message: message + '\n\nמה לעשות עם העסקאות שכבר נרשמו?',
            confirmText: 'למחוק אותן',
            cancelText:  'להשאיר כמשותפות',
        }).then(function (wipe) {
            if (wipe === null) return null;   // נסיגה — לא מסירים כלל
            return wipe ? WIPE : KEEP;
        });
    }

    function send(url, method, body) {
        return fetch(url, {
            method:  method,
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify(body || {}),
        }).then(function (r) {
            return r.json().then(function (d) { return { ok: r.ok, data: d }; });
        });
    }

    /* פרויקטים אישיים של מי שיוצא. עד היום הם נעלמו: רשומים על מי שכבר
     * לא במשפחה, אז מוסתרים מכולם, והכסף שבהם לא נספר בשום מקום.
     *
     * נשאל רק מי שעוזב בעצמו — אלה הפרויקטים שלו. כשהמנהל מסיר, הם נמחקים
     * תמיד (החלטת מתן), והחלון הראשון אומר את זה מראש. השאלה מגיעה מהשרת
     * (409 עם המספר), כי רק המסד יודע אם יש כאלה.
     *
     * כמו ב-askAboutTransactions: המחיקה היא כפתור האישור, ונסיגה (Escape,
     * לחיצה בחוץ) מבטלת את כל הפעולה. "להשאיר כמשותפים" לא מאבד כלום. */
    function askAboutProjects(title, count) {
        const what = count === 1 ? 'פרויקט אישי אחד' : count + ' פרויקטים אישיים';
        return window.appConfirm({
            title: title,
            message: 'יש ' + what + '. אם תמחקו, יימחקו גם העסקאות שבהם. '
                   + 'אם תשאירו, כל המשפחה תראה אותם.',
            confirmText: 'למחוק אותם',
            cancelText:  'להשאיר כמשותפים',
        }).then(function (wipe) {
            if (wipe === null) return null;
            return wipe ? 'delete' : 'share';
        });
    }

    // שולח; ואם השרת עונה שיש פרויקטים שמחכים להחלטה — שואל ושולח שוב.
    // ‎null‎ = המשתמש נסוג בשאלה, ושום דבר לא נשלח.
    function sendDeparture(url, method, body, projectsTitle) {
        return send(url, method, body).then(function (res) {
            if (res.ok || !res.data || !res.data.needs_choice) return res;
            return askAboutProjects(projectsTitle, res.data.project_count)
                .then(function (projects) {
                    if (!projects) return null;
                    return send(url, method, Object.assign({}, body, { projects: projects }));
                });
        });
    }

    // ── הסרת בן משפחה (מנהל בלבד, מתחת ל"אפשרויות מתקדמות") ──
    // שלוש שאלות לפני שמשהו קורה: אזהרה, מה לעשות עם העסקאות, ואישור אחרון.
    // נסיגה בכל אחת מהן — שום דבר לא נשלח.
    const removeBtn    = document.getElementById('removeMemberBtn');
    const removeSelect = document.getElementById('removeMemberSelect');
    if (removeBtn && removeSelect) {
        removeBtn.addEventListener('click', function () {
            const id  = removeSelect.value;
            const opt = removeSelect.options[removeSelect.selectedIndex];
            const name = (opt && opt.textContent.trim()) || 'בן המשפחה';
            if (!id) return;
            let choice = null;

            window.appConfirm({
                title: 'להסיר את ' + name + ' מהמשפחה?',
                // בלי זכר ונקבה (מתן, 1.10) — זו יכולה להיות אור
                message: 'הגישה של ' + name + ' לתקציב המשפחתי תיפסק מיד, ותיפתח משפחה '
                       + 'חדשה וריקה. אפשר לצרף שוב בקוד הזמנה.\n\n'
                       + 'פרויקטים אישיים של ' + name + ', אם יש, יימחקו יחד עם העסקאות שבהם.',
                confirmText: 'המשך',
            }).then(function (ok) {
                if (!ok) return null;
                return askAboutTransactions('העסקאות של ' + name,
                    'העסקאות של ' + name + ' נרשמו מהתקציב המשותף.');
            }).then(function (c) {
                if (!c) return null;
                choice = c;
                return window.appConfirm({
                    title: 'אישור אחרון',
                    message: 'להסיר את ' + name + ' עכשיו? '
                           + (choice === WIPE ? 'העסקאות של ' + name + ' יימחקו. ' : '')
                           + 'את זה אי אפשר לבטל.',
                    confirmText: 'הסרה',
                });
            }).then(function (ok) {
                if (!ok) return;
                removeBtn.disabled = true;
                return send('/api/family/members/' + id, 'DELETE',
                            { keep_transactions: choice === KEEP })
                    .then(function (res) {
                        if (res.ok) {
                            window.showToast(name + ' כבר לא במשפחה');
                            setTimeout(function () { window.location.reload(); }, 700);
                        } else {
                            removeBtn.disabled = false;
                            window.showToast(res.data.error || 'ההסרה נכשלה', 'error');
                        }
                    })
                    .catch(function () {
                        removeBtn.disabled = false;
                        window.showToast(window.sfNetError(), 'error');
                    });
            });
        });
    }

    // ── עזיבת המשפחה ──
    const leaveBtn = document.getElementById('leaveFamilyBtn');
    if (leaveBtn) {
        leaveBtn.addEventListener('click', function () {
            window.appConfirm({
                title: 'לעזוב את המשפחה?',
                message: 'תאבדו גישה לתקציב המשפחתי. תיפתח לך משפחה חדשה וריקה, '
                       + 'והחשבון שלך נשאר. אפשר לחזור בקוד הזמנה.',
                confirmText: 'המשך',
            }).then(function (ok) {
                if (!ok) return null;
                return askAboutTransactions('העסקאות שלך',
                    'העסקאות שרשמתם נרשמו מהתקציב המשותף ויישארו אצל המשפחה.');
            }).then(function (choice) {
                if (!choice) return;
                leaveBtn.disabled = true;
                return sendDeparture('/api/family/leave', 'POST',
                                     { keep_transactions: choice === KEEP },
                                     'הפרויקטים האישיים שלך')
                    .then(function (res) {
                        if (!res) { leaveBtn.disabled = false; return; }
                        if (res.ok) {
                            window.location.href = '/';
                        } else {
                            leaveBtn.disabled = false;
                            window.showToast(res.data.error || 'העזיבה נכשלה', 'error');
                        }
                    })
                    .catch(function () {
                        leaveBtn.disabled = false;
                        window.showToast(window.sfNetError(), 'error');
                    });
            });
        });
    }

    // ── החלפת קוד ההזמנה ──
    const rotateBtn = document.getElementById('rotateCodeBtn');
    if (rotateBtn) {
        rotateBtn.addEventListener('click', function () {
            window.appConfirm({
                title: 'ליצור קוד הזמנה חדש?',
                message: 'הקוד הנוכחי יפסיק לעבוד מיד. מי שכבר הצטרף נשאר במשפחה.',
                confirmText: 'יצירת קוד חדש',
            }).then(function (ok) {
                if (!ok) return;
                rotateBtn.disabled = true;
                return send('/api/family/invite-code', 'POST')
                    .then(function (res) {
                        rotateBtn.disabled = false;
                        if (res.ok && res.data.invite_code) {
                            document.getElementById('inviteCode').textContent = res.data.invite_code;
                            window.showToast('נוצר קוד חדש');
                        } else {
                            window.showToast(res.data.error || 'ההחלפה נכשלה', 'error');
                        }
                    })
                    .catch(function () {
                        rotateBtn.disabled = false;
                        window.showToast(window.sfNetError(), 'error');
                    });
            });
        });
    }
})();


/* ─── תקציב יעד לקטגוריה ─────────────────────────────────────────────────────
 *
 * שתי החלטות נפרדות, בכוונה: האם יש תקציב, ואם כן — האם להתריע. יש
 * משפחות שרוצות לראות "₪1,800 מתוך ₪2,000" על המסך בלי שהאפליקציה
 * תנדנד להן על זה.
 *
 * הפס שכבר קיים בעמוד החודש מודד כמה הקטגוריה מתוך סך ההוצאות ("מכולת
 * היא 35% מההוצאות"). תקציב מודד מול ההחלטה של המשפחה — וזו השאלה
 * שבאמת שואלים.
 */
(function () {

    function fieldsFor(id) {
        const box = document.querySelector('.budget-enabled[data-id="' + id + '"]');
        return box && box.closest('.cat-budget-edit');
    }

    function amountOf(wrap) {
        const raw = wrap.querySelector('.budget-amount').value.trim();
        const num = Number(raw);
        return (raw && isFinite(num) && num > 0) ? num : null;
    }

    /* כפתור השמירה פעיל רק כשיש מה לשמור. כפתור שנראה זמין ולא עושה
     * כלום מלמד להתעלם ממנו. */
    function syncButton(wrap) {
        const btn = wrap.querySelector('.budget-save');
        if (btn) btn.disabled = amountOf(wrap) === null;
    }

    /* ‎alert‎ לא נשלח יותר, והשרת קובע ‎True‎ כברירת מחדל.
     *
     * הייתה כאן תיבה שנייה, "התרע בחריגה", כהחלטה נפרדת מ"יש תקציב".
     * בפועל זו הבחנה בלי הבדל: מי שטרח להגדיר תקציב רוצה לדעת כשחרג
     * ממנו — אחרת הוא רק מספר על המסך. פחות שאלות, אותה תוצאה. */
    function save(id, revert) {
        const wrap = fieldsFor(id);
        if (!wrap) return;
        const on    = wrap.querySelector('.budget-enabled').checked;
        const value = on ? amountOf(wrap) : null;
        const patch = {};
        patch[id] = value === null ? null : { amount: value };
        savePrefs({ limits: patch }, revert);
    }

    document.addEventListener('change', function (e) {
        const box = e.target.closest('.budget-enabled');
        if (!box) return;
        const wrap = box.closest('.cat-budget-edit');
        wrap.querySelector('.cat-budget-fields').hidden = !box.checked;
        if (box.checked) {
            syncButton(wrap);
            wrap.querySelector('.budget-amount').focus();
            return;                 // אין מה לשמור עד שיוזן סכום ויילחץ "שמור"
        }
        // כיבוי הוא פעולה חד-משמעית — אין מה להקליד, אז נשמר מיד
        const was = true;
        save(box.dataset.id, function () {
            box.checked = was;
            wrap.querySelector('.cat-budget-fields').hidden = !was;
        });
    });

    document.addEventListener('input', function (e) {
        const amount = e.target.closest('.budget-amount');
        if (amount) syncButton(amount.closest('.cat-budget-edit'));
    });

    document.addEventListener('click', function (e) {
        const btn = e.target.closest('.budget-save');
        if (!btn || btn.disabled) return;
        btn.disabled = true;
        const label = btn.textContent;
        btn.textContent = 'שומר…';
        save(btn.dataset.id, function () { /* השגיאה מוצגת ב-toast */ });
        // ‎savePrefs‎ לא מחזיר הבטחה, אז משחררים אחרי שהבקשה יצאה
        setTimeout(function () {
            btn.textContent = label;
            syncButton(btn.closest('.cat-budget-edit'));
        }, 700);
    });

    // Enter בשדה הסכום = לחיצה על "שמור"
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter') return;
        const amount = e.target.closest('.budget-amount');
        if (!amount) return;
        e.preventDefault();
        const btn = amount.closest('.cat-budget-edit').querySelector('.budget-save');
        if (btn && !btn.disabled) btn.click();
    });
})();


// ── התראות: מתג כן/לא (מתן, 1.11) — הלוגיקה ב-‎js/push.js‎ ──
(function () {
    const toggle = document.getElementById('pushToggle');
    const status = document.getElementById('pushStatus');
    const push = window.sfPush;
    if (!toggle || !push) return;
    function say(text) { status.textContent = text || ''; status.hidden = !text; }
    function show(st) {
        toggle.checked = st === 'on';
        toggle.disabled = st === 'blocked' || st === 'unsupported';
        say(st === 'blocked' ? 'חסומות בהגדרות הטלפון'
          : st === 'unsupported' ? (push.iOS && !push.standalone ? 'זמינות מהאפליקציה שבמסך הבית' : 'לא נתמכות בדפדפן הזה')
          : '');
    }
    push.state().then(show);
    toggle.addEventListener('change', function () {
        const on = toggle.checked;
        toggle.disabled = true;
        (on ? push.enable() : push.disable())
            .then(function () { toggle.disabled = false; say(''); })
            .catch(function (err) {
                toggle.disabled = false;
                push.state().then(show);
                say((err && err.message) || window.sfNetError());
            });
    });
})();


// ── תצוגה: בהיר / כהה (מתן, 30.9 — סבב 6, פריט 4) ──
// נשמר בטלפון בלבד. ‎data-theme‎ על ‎<html>‎ מחליף את כל הצבעים מיד — בלי
// רענון; את השאר (גרפים) theme.js קובע בטעינה הבאה של כל עמוד.
(function () {
    const buttons = document.querySelectorAll('.theme-choice');
    if (!buttons.length) return;
    function show(theme) {
        buttons.forEach(function (b) {
            const on = b.dataset.themeChoice === theme;
            b.classList.toggle('active', on);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }
    show(document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light');
    buttons.forEach(function (b) {
        b.addEventListener('click', function () {
            const theme = b.dataset.themeChoice;
            if (theme === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
            else document.documentElement.removeAttribute('data-theme');
            try { localStorage.setItem('sf_theme', theme); } catch (e) { /* לא נזכר — רק לעמוד הזה */ }
            show(theme);
        });
    });
})();


// ── גודל טקסט (מתן, 30.9 — סבב 6, פריט 9) ──
(function () {
    const buttons = document.querySelectorAll('.text-size-choice');
    if (!buttons.length) return;
    function show(size) {
        buttons.forEach(function (b) {
            const on = b.dataset.size === size;
            b.classList.toggle('active', on);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }
    show(document.documentElement.getAttribute('data-text-size') || 'normal');
    buttons.forEach(function (b) {
        b.addEventListener('click', function () {
            const size = b.dataset.size;
            if (size === 'normal') document.documentElement.removeAttribute('data-text-size');
            else document.documentElement.setAttribute('data-text-size', size);
            try { localStorage.setItem('sf_text_size', size); } catch (e) { /* רק לעמוד הזה */ }
            show(size);
        });
    });
})();


// ── בחירת צבע לבן משפחה (מתן, 30.9 — סבב 6, פריט 13) ──
// כפתור "בחירת צבע" בשורה שלי פותח חלון; כל צבע מראה את תג השם שלי בצבע הזה.
(function () {
    const openBtn = document.getElementById('memberColorOpen');
    const sheet   = document.getElementById('memberColorSheet');
    if (!openBtn || !sheet) return;
    // לראש העמוד: בתוך קבוצת ההגדרות ‎position: fixed‎ נכלא מתחת לתפריט התחתון
    document.body.appendChild(sheet);
    function close() { sheet.hidden = true; openBtn.focus({ preventScroll: true }); }
    openBtn.addEventListener('click', function () {
        sheet.hidden = false;
        const cur = sheet.querySelector('.color-option.is-current') || sheet.querySelector('.color-option');
        if (cur) cur.focus({ preventScroll: true });
    });
    document.getElementById('memberColorClose').addEventListener('click', close);
    sheet.addEventListener('click', function (e) { if (e.target === sheet) close(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !sheet.hidden) close(); });

    sheet.addEventListener('click', function (e) {
        const btn = e.target.closest('.color-option');
        if (!btn || btn.disabled || btn.classList.contains('is-current')) return;
        btn.disabled = true;
        fetch('/api/family/member-color', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ member_id: btn.dataset.member, color: parseInt(btn.dataset.color, 10) }),
        })
            .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (res) {
                if (!res.ok) { btn.disabled = false; window.showToast(res.d.error || 'שמירת הצבע נכשלה', 'error'); return; }
                try { window.sfToastAfterReload('הצבע נשמר'); } catch (err) { /* בלי טוסט */ }
                window.location.reload();
            })
            .catch(function () { btn.disabled = false; window.showToast(window.sfNetError(), 'error'); });
    });
})();



// ── הגעה מקישור הזמנה (‎/join/<code>‎ ← ‎/settings?join=…‎, רעיון 43) ──
// פותחים את "המשפחה שלי" וממלאים את הקוד. לא מצטרפים מעצמנו: מעבר
// משפחה הוא החלטה, והכפתור "הצטרפות" שואל לפני שהוא מנתק מהמשפחה הנוכחית.
(function () {
    let code = null;
    try { code = new URLSearchParams(location.search).get('join'); } catch (err) { return; }
    const input = document.getElementById('joinCodeInput');
    if (!code || !input) return;
    input.value = code.replace(/[^A-Za-z0-9]/g, '').slice(0, 12).toUpperCase();
    // הקוד יושב בחלונית "עזיבה/הצטרפות" — פותחים אותה
    const moveForm = document.getElementById('familyMoveForm');
    if (moveForm && !moveForm.classList.contains('visible')) {
        moveForm.classList.add('visible');
        const moveBtn = document.getElementById('toggleFamilyMoveBtn');
        if (moveBtn) moveBtn.setAttribute('aria-expanded', 'true');
    }
    const group = input.closest('.settings-group');
    if (group && !group.classList.contains('open')) {
        const header = group.querySelector('.settings-group-header');
        if (header) header.click();
    }
    requestAnimationFrame(function () {
        const row = document.getElementById('join-family');
        if (row) row.scrollIntoView({ block: 'center' });
        input.focus({ preventScroll: true });
    });
})();

// ── "/settings?series=<id>" — מ"חלק מעסקה קבועה" בחלון העריכה (מתן, 12.10) ──
// פותחים את "עסקאות קבועות", גוללים לסדרה, ופותחים אותה לעריכה — אותה לחיצה
// כמו על השורה עצמה (‎transactions.js‎). הפרמטר יורד מהכתובת: רענון לא פותח שוב.
(function () {
    let id;
    try { id = new URLSearchParams(location.search).get('series'); } catch (e) { return; }
    if (!id) return;
    history.replaceState(null, '', location.pathname + location.hash);
    const row = Array.prototype.find.call(document.querySelectorAll('#recurringList .recurring-row'),
                                          function (r) { return r.dataset.id === id; });
    if (!row) return;
    const group = row.closest('.settings-group');
    if (group && !group.classList.contains('open')) {
        const header = group.querySelector('.settings-group-header');
        if (header) header.click();
    }
    // אחרי ‎load‎: ‎transactions.js‎ (שפותח את חלון העריכה) נטען אחרי הקובץ הזה
    const open = function () {
        row.scrollIntoView({ block: 'center' });
        row.click();
    };
    if (document.readyState === 'complete') requestAnimationFrame(open);
    else window.addEventListener('load', function () { requestAnimationFrame(open); });
})();

// ── אישור לפני התנתקות (מתן, 12.10) ──
// "התנתקות" בתחתית ההגדרות ניתקה בנגיעה אחת. הטופס נשאר POST (הגנה מאתר אחר
// — ראו ההערה בתבנית); רק השליחה מחכה לאישור.
(function () {
    const form = document.querySelector('form.logout-form');
    if (!form) return;
    let confirmed = false;
    form.addEventListener('submit', function (e) {
        if (confirmed) return;
        e.preventDefault();
        window.appConfirm({
            title: 'להתנתק?',
            message: 'להתנתק מ-SmartFin בטלפון הזה?',
            confirmText: 'התנתקות',
            cancelText: 'ביטול',
            danger: false,
        }).then(function (ok) {
            if (!ok) return;
            confirmed = true;
            form.submit();
        });
    });
})();


// ── "שמירה" דולק רק כשמשהו השתנה (מתן, 13.10) ──
// בכל טופס עריכה בהגדרות: כבוי כשהכול כמו השמור, דולק כששינו משהו. הכיתוב
// לא משתנה. ראו ‎sfSaveWhenChanged‎ ב-core.js.
(function () {
    if (!window.sfSaveWhenChanged) return;
    [['familyNameEdit', 'saveFamilyNameBtn'], ['profileEdit', 'saveProfileBtn'],
     ['emailForm', 'saveEmailBtn'], ['phoneForm', 'savePhoneBtn'], ['passwordForm', 'changePasswordBtn']]
        .forEach(function (pair) {
            window.sfSaveWhenChanged(document.getElementById(pair[0]), document.getElementById(pair[1]));
        });
})();
