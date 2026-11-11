// Screenshots of the SmartFin harness servers (see serve.py).
//   node shoot.cjs [group ...]     groups: A B C D E S   (default: all)
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

// Output outside git (see .gitignore): SHOTS=<label> picks the folder, default 'latest'
const OUT = path.join(__dirname, 'out', process.env.SHOTS || 'latest');
fs.mkdirSync(OUT, { recursive: true });
const PORT = { rich: 8099, new: 8100, onb: 8101, cards: 8102, stress: 8103 };
const base = (s) => `http://127.0.0.1:${PORT[s]}`;
const UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1';
const want = process.argv.slice(2);
const run = (g) => !want.length || want.includes(g);
const only = process.env.ONLY ? new RegExp(process.env.ONLY) : null;

// The month strip should sit on the current month. null = no strip on this page.
const stripOk = (p) => p.evaluate(() => {
    const s = document.getElementById('monthStrip'); if (!s) return null;
    const a = s.querySelector('.is-current'); if (!a) return null;
    const ar = a.getBoundingClientRect(), sr = s.getBoundingClientRect();
    return Math.abs((ar.left + ar.width / 2) - (sr.left + sr.width / 2)) < 40;
});

const index = [];          // { file, desc, mode, notes[] }
let notes = [];            // console errors / bad responses since the last shot

async function ctx(browser, o = {}) {
    const c = await browser.newContext({
        viewport: { width: o.w || 390, height: o.h || 844 },
        deviceScaleFactor: 2, isMobile: true, hasTouch: true,
        locale: 'he-IL', timezoneId: 'Asia/Jerusalem', serviceWorkers: 'block',
        reducedMotion: o.reduced ? 'reduce' : 'no-preference', userAgent: UA,
    });
    await c.addInitScript((s) => {
        try {
            if (s.theme === 'dark') localStorage.setItem('sf_theme', 'dark');
            if (s.text) localStorage.setItem('sf_text_size', s.text);
            if (!s.freshNews) localStorage.setItem('sf_whats_new', '2026-10-13');
            if (!s.installBar) sessionStorage.setItem('sf_install_hidden', '1');
            if (s.known) for (const k in s.known) localStorage.setItem(k, s.known[k]);
        } catch (e) { /* */ }
    }, { theme: o.theme || 'light', text: o.text || null, freshNews: !!o.freshNews, installBar: !!o.installBar, known: o.known || null });
    c._mode = `${o.w || 390}x${o.h || 844} ${o.theme || 'light'}${o.text ? ' text=' + o.text : ''} motion=${o.reduced ? 'reduced' : 'default'}${o.installBar ? ' install-bar=shown' : ''}`;
    return c;
}

async function page(c, fixedTime) {
    const p = await c.newPage();
    if (fixedTime) await p.clock.setFixedTime(new Date(fixedTime));
    p.on('console', (m) => { if (m.type() === 'error') notes.push('console: ' + m.text().slice(0, 160)); });
    p.on('pageerror', (e) => notes.push('pageerror: ' + String(e).slice(0, 160)));
    p.on('response', (r) => { if (r.status() >= 400 && !r.url().includes('/nope')) notes.push(`HTTP ${r.status()} ${r.url().replace(/^http:\/\/[^/]+/, '')}`); });
    return p;
}

async function settle(p, ms = 1900) {
    await p.evaluate(() => document.fonts.ready);
    await p.waitForTimeout(ms);
    // The month strip scrolls smoothly to the current month: shoot only once it
    // has stopped, or two runs differ by a few pixels there.
    await p.evaluate(async () => {
        const s = document.getElementById('monthStrip');
        if (!s) return;
        let last = -1;
        for (let i = 0; i < 40 && s.scrollLeft !== last; i++) {
            last = s.scrollLeft;
            await new Promise((r) => setTimeout(r, 150));
        }
    });
}

async function go(p, url, ms) {
    await p.goto(url, { waitUntil: 'load' });
    await settle(p, ms);
}

async function shot(p, file, desc, full = false) {
    const name = file + '.png';
    await p.screenshot({ path: path.join(OUT, name), fullPage: full });
    index.push({ file: name, desc, mode: p.context()._mode + (full ? ' full-page' : ' viewport'), notes });
    notes = [];
    console.log('  ✓', name);
}

async function attempt(label, fn) {
    if (only && !only.test(label)) return;
    try { await fn(); } catch (e) {
        index.push({ file: '(not produced) ' + label, desc: 'FAILED: ' + String(e).split('\n')[0].slice(0, 200), mode: '', notes });
        notes = [];
        console.log('  ✗', label, String(e).split('\n')[0].slice(0, 160));
    }
}

async function both(browser, scenario, url, file, desc, o = {}) {
    // viewport in default motion, full page in reduced motion (so scroll-reveal content is not faded)
    await attempt(file, async () => {
        const c1 = await ctx(browser, o);
        const p1 = await page(c1, o.fixedTime);
        if (!o.anon) await p1.goto(base(scenario) + '/__h/login?next=/privacy');
        await go(p1, base(scenario) + url);
        if (o.before) await o.before(p1);
        if ((await stripOk(p1)) === false) {
            await shot(p1, file + '-viewport-STRIP-UNCENTRED', desc + ' — above the fold. Month strip drifted off the current month within 2s of load (intermittent in Chromium, about 1 load in 8; it is centred right after load). Kept as evidence; the normal shot is the retry.');
            for (let i = 0; i < 4 && (await stripOk(p1)) === false; i++) { await go(p1, base(scenario) + url); if (o.before) await o.before(p1); }
        }
        await shot(p1, file + '-viewport', desc + ' — above the fold');
        await c1.close();
        const c2 = await ctx(browser, { ...o, reduced: true });
        const p2 = await page(c2, o.fixedTime);
        if (!o.anon) await p2.goto(base(scenario) + '/__h/login?next=/privacy');
        await go(p2, base(scenario) + url);
        if (o.before) await o.before(p2);
        for (let i = 0; i < 4 && (await stripOk(p2)) === false; i++) { await go(p2, base(scenario) + url); if (o.before) await o.before(p2); }
        await shot(p2, file + '-full', desc + ' — whole page', true);
        await c2.close();
    });
}

async function signedIn(browser, scenario, o = {}) {
    const c = await ctx(browser, o);
    const p = await page(c, o.fixedTime);
    await p.goto(base(scenario) + '/__h/login?next=/privacy');
    return p;
}

async function openAdd(p) {
    await p.click('#fabBtn');
    await p.waitForSelector('.modal-overlay.open');
    await p.waitForTimeout(800);
}
const sheetTo = (p, where) => p.evaluate((w) => {
    const s = document.querySelector('.modal-sheet'); s.scrollTop = w === 'bottom' ? s.scrollHeight : 0;
}, where);

(async () => {
    const browser = await chromium.launch();

    // ───────────────────────── A: every page, rich, light, 390 ─────────────────────────
    if (run('A')) {
        console.log('A');
        const R = 'rich';
        await both(browser, R, '/', 'A-home-390-light', 'Home, rich family');
        await both(browser, R, '/month', 'A-month-390-light', 'Month page, current month');
        await both(browser, R, '/months', 'A-months-390-light', 'Comparison between months');
        await both(browser, R, '/projects', 'A-projects-390-light', 'Projects list');
        let projUrl = null;
        await attempt('project url', async () => {
            const p = await signedIn(browser, R);
            await go(p, base(R) + '/projects', 500);
            projUrl = await p.evaluate(() => {
                const a = [...document.querySelectorAll('a[href^="/projects/"]')].find((x) => /יוון/.test(x.textContent));
                return a ? a.getAttribute('href') : null;
            });
            notes = [];
            await p.context().close();
        });
        if (projUrl) {
            await both(browser, R, projUrl, 'A-project-detail-390-light', 'Project detail (trip, with budget target)');
            await both(browser, R, projUrl + '/edit', 'A-project-edit-390-light', 'Project edit');
        }
        await both(browser, R, '/projects/new', 'A-project-new-390-light', 'New project form');
        await both(browser, R, '/settings', 'A-settings-collapsed-390-light', 'Settings, all groups collapsed');
        let groups = [];
        await attempt('settings group titles', async () => {
            const p = await signedIn(browser, R);
            await go(p, base(R) + '/settings', 500);
            groups = await p.evaluate(() => [...document.querySelectorAll('.settings-group-header')].map((h) => (h.querySelector('.group-title') || h).textContent.trim().replace(/\s+/g, ' ').slice(0, 40)));
            notes = [];
            await p.context().close();
        });
        const slug = ['family', 'categories', 'recurring', 'attribution', 'display', 'notifications', 'account'];
        for (let i = 0; i < groups.length; i++) {
            const key = groups.length === 7 ? slug[i] : (i === groups.length - 1 ? 'account' : slug[i]);
            await both(browser, R, '/settings', `A-settings-group${i + 1}-${key}-390-light`,
                `Settings with group ${i + 1} ("${groups[i]}") expanded`, {
                    before: async (p) => {
                        const h = p.locator('.settings-group-header').nth(i);
                        await h.click();
                        await p.waitForTimeout(900);
                        await h.evaluate((el) => el.scrollIntoView({ block: 'start' }));
                        await p.evaluate(() => window.scrollBy(0, -70));
                        await p.waitForTimeout(400);
                    },
                });
        }
        await both(browser, R, '/import', 'A-import-390-light', 'Credit-card import, first step');
        await both(browser, R, '/nope-not-a-page', 'A-error-404-390-light', 'Error page (404), signed in');
        await both(browser, R, '/login', 'A-login-390-light', 'Login', { anon: true });
        await both(browser, R, '/login', 'A-signup-390-light', 'Signup tab', {
            anon: true, before: async (p) => { await p.click('#tabSignup'); await p.waitForTimeout(600); },
        });
        await both(browser, R, '/?intro=1', 'A-landing-390-light', 'Landing page (signed out)', { anon: true });
        // Onboarding: a family with no categories
        await both(browser, 'onb', '/onboarding', 'A-onboarding-step0-390-light', 'Onboarding: new family or join');
        await attempt('onboarding steps', async () => {
            const p = await signedIn(browser, 'onb');
            await go(p, base('onb') + '/onboarding', 1200);
            await p.click('#chooseCreateBtn'); await p.waitForTimeout(900);
            await shot(p, 'A-onboarding-step1-name-390-light-viewport', 'Onboarding step 1: family name');
            await p.fill('#familyName', 'כהן'); await p.click('#toStep2Btn'); await p.waitForTimeout(1200);
            await shot(p, 'A-onboarding-step2-attribution-390-light-viewport', 'Onboarding step 2: attribution');
            await shot(p, 'A-onboarding-step2-attribution-390-light-full', 'Onboarding step 2: attribution — whole page', true);
            await p.click('#toStep3Btn'); await p.waitForTimeout(1200);
            await shot(p, 'A-onboarding-step3-categories-390-light-viewport', 'Onboarding step 3: categories');
            await shot(p, 'A-onboarding-step3-categories-390-light-full', 'Onboarding step 3: categories — whole page', true);
            await p.context().close();
        });
    }

    // ───────────────────────── B: overlays, rich, light, 390, viewport ─────────────────
    if (run('B')) {
        console.log('B');
        const R = 'rich';
        await attempt('B add sheet', async () => {
            const p = await signedIn(browser, R);
            await go(p, base(R) + '/');
            await openAdd(p);
            await shot(p, 'B-add-sheet-expense-top', 'Add sheet opened from +, expense, top of sheet, untouched');
            await sheetTo(p, 'bottom'); await p.waitForTimeout(300);
            await shot(p, 'B-add-sheet-expense-bottom', 'Same sheet scrolled to its bottom: where Save sits');
            const m = await p.evaluate(() => {
                const s = document.querySelector('.modal-sheet'), b = document.querySelector('#submitBtn').getBoundingClientRect();
                return { sheetScrollHeight: s.scrollHeight, sheetClientHeight: s.clientHeight, viewport: innerHeight,
                         cats: document.querySelectorAll('#categoryGrid .cat-btn').length, saveTopAtBottom: Math.round(b.top) };
            });
            index[index.length - 1].notes.push('metrics: ' + JSON.stringify(m));
            await sheetTo(p, 'top');
            await p.fill('#txAmount', '86.5');
            await p.locator('#categoryGrid .cat-btn').nth(4).click();
            await p.fill('#txDescription', 'מכולת'); await p.waitForTimeout(500);
            await shot(p, 'B-add-sheet-expense-filled', 'Add sheet with amount, category and description filled');
            await p.click('#toggleIncome'); await p.waitForTimeout(600); await sheetTo(p, 'top');
            await shot(p, 'B-add-sheet-income', 'Add sheet in income mode');
            await p.click('#toggleSavings'); await p.waitForTimeout(600); await sheetTo(p, 'top');
            await shot(p, 'B-add-sheet-savings', 'Add sheet in savings mode');
            await p.click('#toggleExpense'); await p.waitForTimeout(400);
            await p.evaluate(() => { document.querySelector('#txRecurring').click(); document.querySelector('#txInstallments').click(); });
            await p.waitForTimeout(600); await sheetTo(p, 'bottom'); await p.waitForTimeout(300);
            await shot(p, 'B-add-sheet-recurring-installments-on', 'Add sheet, recurring and installments switched on, bottom');
            await p.context().close();
        });
        await attempt('B edit sheet + confirm', async () => {
            const p = await signedIn(browser, R);
            await go(p, base(R) + '/');
            await p.locator('.transaction-item .tx-head').first().click();
            await p.waitForTimeout(1200);
            await shot(p, 'B-edit-from-home-row', 'After tapping the first transaction row on home (edit)');
            const del = p.locator('#deleteTxBtn:visible, .tx-inline-delete:visible, [data-action="delete"]:visible').first();
            if (await del.count()) {
                await del.evaluate((el) => el.scrollIntoView({ block: 'center' }));
                await shot(p, 'B-edit-sheet-bottom-actions', 'Edit: the save / duplicate / delete actions');
                await del.click(); await p.waitForTimeout(700);
            } else {
                await p.evaluate(() => window.appConfirm({ title: 'למחוק את העסקה?', message: 'סופר ומזון · ₪86.50', confirmText: 'מחיקת העסקה' }));
                await p.waitForTimeout(700);
            }
            await shot(p, 'B-confirm-dialog', 'Confirm dialog (delete a transaction)');
            await p.context().close();
        });
        await attempt('B toast', async () => {
            const p = await signedIn(browser, R);
            await go(p, base(R) + '/');
            await p.evaluate(() => window.showToast('העסקה נוספה'));
            await p.waitForTimeout(500);
            await shot(p, 'B-toast-plain', 'Toast "העסקה נוספה" on home');
            await p.evaluate(() => window.showToast('העסקה נמחקה', null, { label: 'ביטול', onClick() {} }));
            await p.waitForTimeout(500);
            await shot(p, 'B-toast-with-undo', 'Toast with an undo action');
            await p.evaluate(() => window.showToast('השמירה נכשלה', 'error'));
            await p.waitForTimeout(500);
            await shot(p, 'B-toast-error', 'Error toast');
            await p.context().close();
        });
        await attempt('B search', async () => {
            const p = await signedIn(browser, R);
            await go(p, base(R) + '/');
            await p.click('#searchOpen'); await p.waitForTimeout(800);
            await shot(p, 'B-search-empty', 'Search screen before typing');
            await p.fill('#searchInput', 'סופר'); await p.waitForTimeout(1500);
            await shot(p, 'B-search-results', 'Search screen with results for "סופר"');
            await p.context().close();
        });
        await attempt('B month overlays', async () => {
            const p = await signedIn(browser, R);
            await go(p, base(R) + '/month');
            await p.click('#budgetsOpen'); await p.waitForTimeout(900);
            await shot(p, 'B-month-budgets-sheet', 'Budgets sheet on the month page');
            await p.keyboard.press('Escape'); await p.waitForTimeout(600);
            const row = p.locator('.legend-item.clickable[role="button"]').first();
            await row.evaluate((el) => el.scrollIntoView({ block: 'start' }));
            await p.evaluate(() => window.scrollBy(0, -120));
            await row.click(); await p.waitForTimeout(900);
            await shot(p, 'B-month-category-expanded', 'Month page: a category row expanded to its transactions');
            const open = p.locator('#txScreenOpen');
            await open.evaluate((el) => el.scrollIntoView({ block: 'center' }));
            await p.waitForTimeout(500);
            await shot(p, 'B-month-all-transactions-card', 'Month page: the "all transactions" card in the page');
            await open.click(); await p.waitForTimeout(1000);
            await shot(p, 'B-month-all-transactions-screen', 'All-transactions full screen on the month page');
            await p.context().close();
        });
        await attempt('B month scrolled (sticky header)', async () => {
            const p = await signedIn(browser, R);
            await go(p, base(R) + '/month');
            await p.evaluate(() => window.scrollTo(0, 900)); await p.waitForTimeout(900);
            await shot(p, 'B-month-scrolled-sticky-header', 'Month page scrolled down: sticky header and dock over content');
            await p.context().close();
        });
        await attempt('B colour sheet', async () => {
            const p = await signedIn(browser, R);
            await go(p, base(R) + '/settings', 1200);
            await p.locator('#my-details .settings-group-header').click(); await p.waitForTimeout(900);
            await p.locator('#memberColorOpen').evaluate((el) => el.scrollIntoView({ block: 'center' }));
            await p.click('#memberColorOpen'); await p.waitForTimeout(900);
            await shot(p, 'B-settings-member-colour-sheet', 'Member colour sheet (settings → my account)');
            await p.context().close();
        });
        await attempt('B icon picker + manage mode', async () => {
            const p = await signedIn(browser, R);
            await go(p, base(R) + '/settings', 1200);
            await p.locator('.settings-group-header').nth(1).click(); await p.waitForTimeout(900);
            await p.click('#manageCatsBtn'); await p.waitForTimeout(700);
            await p.locator('#manageCatsBtn').evaluate((el) => el.scrollIntoView({ block: 'start' }));
            await p.evaluate(() => window.scrollBy(0, -80)); await p.waitForTimeout(300);
            await shot(p, 'B-settings-categories-manage-mode', 'Settings → categories with "ניהול" (manage) switched on');
            const icon = p.locator('#newCatIcon');
            await icon.evaluate((el) => el.scrollIntoView({ block: 'center' }));
            await icon.dispatchEvent('pointerdown');
            await p.waitForTimeout(1100);
            if (!(await p.locator('.icon-picker:visible').count())) throw new Error('icon picker did not open');
            await shot(p, 'B-icon-picker', 'Icon picker (from the add-category row)');
            await p.context().close();
        });
        await attempt('B install bar', async () => {
            const p = await signedIn(browser, R, { installBar: true });
            await go(p, base(R) + '/');
            await shot(p, 'B-installbar-home', 'Home as a browser visitor who has not installed: install bar shown');
            await p.evaluate(() => window.showToast('העסקה נמחקה', null, { label: 'ביטול', onClick() {} }));
            await p.waitForTimeout(500);
            await shot(p, 'B-installbar-toast', 'Toast with undo while the install bar is shown');
            await p.waitForTimeout(6200);
            await openAdd(p);
            await shot(p, 'B-installbar-add-sheet-top', 'Add sheet open while the install bar is shown (top)');
            await sheetTo(p, 'bottom'); await p.waitForTimeout(300);
            await shot(p, 'B-installbar-add-sheet-bottom', 'Add sheet scrolled to bottom while the install bar is shown');
            const g = await p.evaluate(() => {
                const r = (s) => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect(); return { top: Math.round(b.top), bottom: Math.round(b.bottom), z: getComputedStyle(e).zIndex, hidden: e.hidden }; };
                return { installBar: r('#installBar'), submit: r('#submitBtn'), sheet: r('.modal-sheet'), overlayZ: getComputedStyle(document.querySelector('#modalOverlay')).zIndex };
            });
            index[index.length - 1].notes.push('geometry: ' + JSON.stringify(g));
            await p.fill('#txAmount', '50'); await p.waitForTimeout(300);
            await p.evaluate(() => window.showToast('נא לבחור קטגוריה', 'error')); await p.waitForTimeout(500);
            await shot(p, 'B-installbar-add-sheet-toast', 'Add sheet + a toast + the install bar together');
            await p.context().close();
        });
    }

    // ───────────────────────── C: home variants ─────────────────────────
    if (run('C')) {
        console.log('C');
        await both(browser, 'new', '/', 'C-home-new-family-390-light', 'Home for a brand-new family (no transactions)');
        await both(browser, 'new', '/month', 'C-month-new-family-390-light', 'Month page for a brand-new family');
        await both(browser, 'new', '/projects', 'C-projects-empty-390-light', 'Projects page with no projects');
        await both(browser, 'new', '/months', 'C-months-empty-390-light', 'Comparison page with no data');
        const T = '2027-01-01T12:30:00+02:00';
        await both(browser, 'cards', '/', 'C-home-all-cards-390-light',
            'Home on 1 January: month recap + year card + budget alert + pinned project + week card', { fixedTime: T });
        await attempt('C everything at once', async () => {
            // fresh device for "what's new"; partner unknown to this phone → "member joined"
            const p0 = await signedIn(browser, 'cards', { fixedTime: T });
            await go(p0, base('cards') + '/', 600);
            const ids = await p0.evaluate(() => { const d = document.querySelector('#sfHomeInfo').dataset; return { fam: d.family, me: d.me }; });
            notes = [];
            await p0.context().close();
            const known = {}; known['sf_known_members_' + ids.fam] = JSON.stringify([ids.me]);
            const p = await signedIn(browser, 'cards', { fixedTime: T, freshNews: true, installBar: true, known });
            await go(p, base('cards') + '/');
            await shot(p, 'C-home-first-open-after-update-viewport', 'Home on 1 January on a phone that has not seen "what\'s new": sheet + install bar + cards');
            const ok = p.locator('.color-sheet button, .whats-new button').last();
            await p.keyboard.press('Escape'); await p.waitForTimeout(300);
            if (await p.locator('.color-sheet:visible').count()) { await ok.click().catch(() => {}); }
            await p.waitForTimeout(700);
            await shot(p, 'C-home-all-cards-plus-joined-installbar-viewport', 'Same after closing "what\'s new": member-joined banner + recap + year card + alert + install bar');
            await shot(p, 'C-home-all-cards-plus-joined-installbar-full', 'Same — whole page', true);
            await p.context().close();
        });
        await attempt('C year recap', async () => {
            const p = await signedIn(browser, 'cards', { fixedTime: T });
            await go(p, base('cards') + '/');
            // One announcement per visit (11.11): close the month recap first, as a user would
            if (await p.locator('.recap-wrap:not([hidden]) .alert-dismiss').count()) {
                await p.locator('.recap-wrap .alert-dismiss').first().click(); await p.waitForTimeout(300);
            }
            await p.locator('[data-year-recap]').first().click(); await p.waitForTimeout(2000);
            await shot(p, 'C-year-recap-sheet', 'Year recap opened from the home card');
            await p.context().close();
        });
        await attempt('C welcome invite', async () => {
            const p = await signedIn(browser, 'new');
            await go(p, base('new') + '/?welcome=1');
            await shot(p, 'C-home-new-family-welcome-invite', 'New family home right after onboarding: the welcome-invite sheet');
            await p.context().close();
        });
    }

    // ───────────────────────── D: dark, 390 ─────────────────────────
    if (run('D')) {
        console.log('D');
        const R = 'rich', o = { theme: 'dark' };
        await both(browser, R, '/', 'D-home-390-dark', 'Home, dark theme', o);
        await both(browser, R, '/month', 'D-month-390-dark', 'Month page, dark theme', o);
        await both(browser, R, '/months', 'D-months-390-dark', 'Comparison, dark theme', o);
        await both(browser, R, '/projects', 'D-projects-390-dark', 'Projects, dark theme', o);
        await both(browser, R, '/settings', 'D-settings-collapsed-390-dark', 'Settings collapsed, dark theme', o);
        await both(browser, R, '/settings', 'D-settings-categories-390-dark', 'Settings → categories expanded, dark theme', {
            ...o, before: async (p) => { await p.locator('.settings-group-header').nth(1).click(); await p.waitForTimeout(900); },
        });
        await attempt('D add sheet', async () => {
            const p = await signedIn(browser, R, o);
            await go(p, base(R) + '/');
            await openAdd(p);
            await shot(p, 'D-add-sheet-top-390-dark', 'Add sheet, dark theme, top');
            await sheetTo(p, 'bottom'); await p.waitForTimeout(300);
            await shot(p, 'D-add-sheet-bottom-390-dark', 'Add sheet, dark theme, bottom');
            await p.context().close();
        });
    }

    // ───────────────────────── E: 320x568, text size xlarge ─────────────────────────
    if (run('E')) {
        console.log('E');
        const R = 'rich', o = { w: 320, h: 568, text: 'xlarge' };
        for (const [url, name, desc] of [['/', 'home', 'Home'], ['/month', 'month', 'Month'], ['/settings', 'settings', 'Settings'],
            ['/months', 'months', 'Comparison'], ['/projects', 'projects', 'Projects']]) {
            await attempt('E ' + name, async () => {
                const p = await signedIn(browser, R, o);
                await go(p, base(R) + url);
                await shot(p, `E-${name}-320-xlarge-viewport`, `${desc} at 320×568 with text size "very large"`);
                const d = await p.evaluate(() => {
                    const nav = document.querySelector('.bottom-nav').getBoundingClientRect();
                    return { docScrollW: document.documentElement.scrollWidth, innerW: innerWidth,
                        nav: { w: Math.round(nav.width), h: Math.round(nav.height) },
                        labels: [...document.querySelectorAll('.nav-item')].map((n) => ({ t: n.querySelector('.nav-label').textContent, item: Math.round(n.getBoundingClientRect().width), label: Math.round(n.querySelector('.nav-label').getBoundingClientRect().width) })) };
                });
                index[index.length - 1].notes.push('dock: ' + JSON.stringify(d));
                await p.context().close();
            });
        }
        await attempt('E add sheet', async () => {
            const p = await signedIn(browser, R, o);
            await go(p, base(R) + '/');
            await openAdd(p);
            await shot(p, 'E-add-sheet-top-320-xlarge-viewport', 'Add sheet at 320×568, text "very large", top');
            await sheetTo(p, 'bottom'); await p.waitForTimeout(300);
            await shot(p, 'E-add-sheet-bottom-320-xlarge-viewport', 'Add sheet at 320×568, text "very large", bottom');
            await p.context().close();
        });
        await attempt('E home 320 normal', async () => {
            const p = await signedIn(browser, R, { w: 320, h: 568 });
            await go(p, base(R) + '/');
            await shot(p, 'E-home-320-normal-viewport', 'Home at 320×568, normal text size (to separate width from text size)');
            await p.context().close();
        });
    }

    // ───────────────────────── S: stress data ─────────────────────────
    if (run('S')) {
        console.log('S');
        await both(browser, 'stress', '/', 'S-home-stress-390-light', 'Home with long names and six-figure amounts with agorot');
        await both(browser, 'stress', '/month', 'S-month-stress-390-light', 'Month page with long names and large amounts');
        await both(browser, 'stress', '/settings', 'S-settings-stress-390-light', 'Settings with a long family name and member names');
    }

    await browser.close();
    const prev = fs.existsSync(path.join(OUT, 'index.json')) ? JSON.parse(fs.readFileSync(path.join(OUT, 'index.json'))) : [];
    const done = new Set(index.map((i) => i.file));
    const merged = prev.filter((i) => !done.has(i.file)).concat(index);
    fs.writeFileSync(path.join(OUT, 'index.json'), JSON.stringify(merged, null, 1));
    console.log('done', index.length);
})();
