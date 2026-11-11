"""Demo dataset for the visual harness. Pure data + the RPC payload builders.

One raw dataset per scenario (rows shaped like PostgREST returns them, with the
embedded relations the app reads), and Python versions of the page RPCs that
aggregate it the same way the SQL functions do.
"""
import copy
import random
import uuid
from datetime import date, datetime, timedelta

NS = uuid.UUID("12345678-1234-5678-1234-567812345678")


def uid(name):
    return str(uuid.uuid5(NS, name))


FAM = uid("family")
ME = uid("me")
PARTNER = uid("partner")

DEFAULT_CATEGORIES = [
    ("משכורת", "💼", "income"), ("הכנסה נוספת", "💵", "income"),
    ("דיור ושכירות", "🏠", "expense"), ("חשבונות ומנויים", "💳", "expense"),
    ("רכב ותחבורה", "🚗", "expense"), ("בריאות", "🏥", "expense"),
    ("סופר ומזון", "🛒", "expense"), ("מסעדות ובילויים", "🥂", "expense"),
    ("שופינג וקניות", "🛍️", "expense"), ("טיפוח", "💅", "expense"),
    ("ריהוט ואביזרים לבית", "🪑", "expense"), ("חופשות", "✈️", "expense"),
    ("אירועים ומתנות", "🎁", "expense"), ("אחר", "📦", "expense"),
    ("השקעות", "📈", "savings"), ("פיקדון בנקאי", "🏧", "savings"),
]


def month_add(d, n):
    idx = d.year * 12 + d.month - 1 + n
    return date(idx // 12, idx % 12 + 1, 1)


def build(scenario, today):
    rnd = random.Random(7)
    single = scenario in ("new", "onb")
    stress = scenario == "stress"

    me_name = "מיכל כהן" if not stress else "מיכל-אלכסנדרה כהן-רוזנבלום"
    partner_name = "דניאל כהן" if not stress else "דניאל-יהונתן כהן-רוזנבלום"
    profiles = [
        {"id": ME, "name": me_name, "family_id": FAM, "avatar_initial": "מ",
         "email": "michal@example.com", "phone": "050-1234567", "workplace": "סטודיו לעיצוב",
         "created_at": "2025-09-01T08:00:00+00:00", "families": {"name": "כהן"}},
    ]
    if not single:
        profiles.append(
            {"id": PARTNER, "name": partner_name, "family_id": FAM, "avatar_initial": "ד",
             "email": "daniel@example.com", "phone": "052-9876543", "workplace": "חברת הייטק",
             "created_at": "2025-09-02T08:00:00+00:00", "families": {"name": "כהן"}})
    prof = {p["id"]: p for p in profiles}

    categories = []
    if scenario != "onb":
        for i, (name, icon, typ) in enumerate(DEFAULT_CATEGORIES):
            categories.append({"id": uid("cat" + name), "family_id": FAM, "name": name, "icon": icon,
                               "type": typ, "is_custom": False, "sort_order": i,
                               "created_at": "2025-09-01T08:00:00+00:00"})
        if stress:
            categories[8]["name"] = "שופינג, קניות אונליין והזמנות מחו״ל"
    cat = {c["name"]: c for c in categories}
    if stress:
        cat["שופינג וקניות"] = categories[8]

    projects, project_categories, txs = [], [], []

    def tx(d, typ, amount, cname=None, desc="", user=None, **extra):
        c = cat.get(cname) if cname else None
        row = {
            "id": uid(f"tx{len(txs)}"), "family_id": FAM, "type": typ, "amount": amount,
            "description": desc, "date": d.isoformat(),
            "category_id": c["id"] if c else None, "project_category_id": None, "project_id": None,
            "user_id": user, "created_by": user or ME,
            "is_recurring": False, "recurring_frequency": None, "recurring_end_date": None,
            "recurring_parent_id": None, "recurring_skips": [],
            "installments": None, "installment_no": None, "installment_total": None,
            "receipt_path": None, "workplace": None,
            "created_at": datetime(d.year, d.month, d.day, rnd.randint(7, 21), rnd.randint(0, 59)).isoformat() + "+00:00",
            "categories": {"name": c["name"], "icon": c["icon"]} if c else None,
            "project_categories": None,
            "profiles": {"name": prof[user]["name"], "workplace": prof[user]["workplace"]} if user in prof else None,
            "projects": None,
        }
        row.update(extra)
        txs.append(row)
        return row

    if scenario in ("new", "onb"):
        return _tables(scenario, today, profiles, categories, projects, project_categories, txs, cat)

    big = 9.03 if stress else 1.0   # stress: six-figure amounts with agorot

    # ── Recurring series: (key, day, type, amount, category, description, owner, frequency)
    series = [
        ("salary_me", 1, "income", round(14200 * big, 2), "משכורת", "משכורת", ME, "monthly_1"),
        ("salary_partner", 10, "income", round(11800 * big, 2), "משכורת", "משכורת", PARTNER, "monthly_same"),
        ("rent", 1, "expense", 5400, "דיור ושכירות", "שכר דירה", None, "monthly_1"),
        ("internet", 5, "expense", 119, "חשבונות ומנויים", "אינטרנט וטלוויזיה", None, "monthly_same"),
        ("streaming", 8, "expense", 54.9, "חשבונות ומנויים", "מנוי סטרימינג", ME, "monthly_same"),
        ("car_ins", 3, "expense", 310, "רכב ותחבורה", "ביטוח רכב", PARTNER, "monthly_same"),
        ("gym", 6, "expense", 229, "בריאות", "מנוי חדר כושר", PARTNER, "monthly_same"),
        ("invest", 2, "savings", 1500, "השקעות", "הוראת קבע לתיק השקעות", None, "monthly_same"),
    ]
    templates = {}
    shops = {
        "סופר ומזון": ["שופרסל", "רמי לוי", "ירקן השכונה", "מכולת", "שוק הכרמל", "מאפייה"],
        "מסעדות ובילויים": ["קפה שכונתי", "פיצה", "מסעדה איטלקית", "בר", "סושי", "קולנוע"],
        "רכב ותחבורה": ["דלק", "חניון", "רב־קו", "שטיפת רכב"],
        "בריאות": ["בית מרקחת", "רופא שיניים", "קופת חולים"],
        "שופינג וקניות": ["בגדים לילדים", "נעליים", "הזמנה מהאינטרנט"],
        "טיפוח": ["מספרה", "קוסמטיקאית"],
        "ריהוט ואביזרים לבית": ["כלי מטבח", "מנורה לסלון"],
        "אירועים ומתנות": ["מתנה ליום הולדת", "חתונה של חברים"],
        "אחר": ["תיקון מכונת כביסה", "דמי חבר"],
    }
    plan = [  # (category, times per month, low, high)
        ("סופר ומזון", 8, 90, 560), ("מסעדות ובילויים", 5, 55, 290), ("רכב ותחבורה", 4, 40, 310),
        ("בריאות", 1, 60, 380), ("שופינג וקניות", 2, 120, 640), ("טיפוח", 1, 90, 260),
        ("ריהוט ואביזרים לבית", 1, 80, 420), ("אירועים ומתנות", 1, 150, 500), ("אחר", 1, 60, 300),
    ]
    owners = [ME, PARTNER, None]

    first_month = month_add(today, -13)
    m = first_month
    k = 0
    while m <= today.replace(day=1):
        last_day = (month_add(m, 1) - timedelta(days=1)).day
        cutoff = today.day if m == today.replace(day=1) else last_day
        # recurring
        for key, day, typ, amount, cname, desc, owner, freq in series:
            if day > cutoff:
                continue
            d = m.replace(day=day)
            wp = prof[owner]["workplace"] if (typ == "income" and owner in prof) else None
            # the internet price went up three months ago, so "price change" shows in settings
            if key == "internet" and m < month_add(today, -3):
                amount = 99
            if key not in templates:
                templates[key] = tx(d, typ, amount, cname, desc, owner, is_recurring=True,
                                    recurring_frequency=freq, workplace=wp)
            else:
                tx(d, typ, amount, cname, desc, owner, recurring_parent_id=templates[key]["id"], workplace=wp)
        # electricity every month, municipal tax every second month
        if cutoff >= 12:
            tx(m.replace(day=12), "expense", rnd.randint(380, 560), "חשבונות ומנויים", "חשבון חשמל", None)
        if k % 2 == 0 and cutoff >= 14:
            tx(m.replace(day=14), "expense", 618, "חשבונות ומנויים", "ארנונה", None)
        if cutoff >= 15:
            tx(m.replace(day=15), "savings", 500, "פיקדון בנקאי", "פיקדון חודשי", None)
        if k % 4 == 1 and cutoff >= 18:
            tx(m.replace(day=18), "income", 850, "הכנסה נוספת", "עבודה פרילנס", ME)
        # variable spending; a quieter and a heavier month so the comparison chart has shape
        factor = [1.0, 0.86, 1.12, 0.94, 1.25, 0.9, 1.05][k % 7]
        for cname, times, lo, hi in plan:
            for _ in range(times):
                day = rnd.randint(1, last_day)
                if day > cutoff:
                    continue
                amount = round(rnd.uniform(lo, hi) * factor, rnd.choice([0, 0, 0, 1, 2]))
                if amount == int(amount):
                    amount = int(amount)
                tx(m.replace(day=day), "expense", amount, cname, rnd.choice(shops[cname]), rnd.choice(owners))
        m = month_add(m, 1)
        k += 1

    cur = today.replace(day=1)
    if today.day == 1:
        # the "everything at once" home: an expense on the 1st that crosses a budget
        tx(today, "expense", 640, "מסעדות ובילויים", "ארוחת ערב חגיגית", ME)
        tx(today, "expense", 212.4, "סופר ומזון", "שופרסל", PARTNER)
    else:
        # an installment purchase, 12 payments, started two months ago
        start = month_add(today, -2).replace(day=4)
        tpl = tx(start, "expense", 400, "ריהוט ואביזרים לבית", "מקרר חדש", None, is_recurring=True,
                 recurring_frequency="monthly_same", installments=12, installment_no=1, installment_total=4800,
                 recurring_end_date=month_add(start, 11).replace(day=4).isoformat())
        for n in (1, 2):
            d = month_add(start, n).replace(day=4)
            if d <= today:
                tx(d, "expense", 400, "ריהוט ואביזרים לבית", "מקרר חדש", None,
                   recurring_parent_id=tpl["id"], installments=12, installment_no=n + 1)
        # recent rows worth looking at: today / yesterday, a receipt, a long description
        tx(today, "expense", 86.5, "סופר ומזון", "מכולת", ME,
           receipt_path=f"{FAM}/{uid('r1')}.jpg")
        tx(today, "expense", 42, "מסעדות ובילויים", "קפה ומאפה", PARTNER)
        tx(today - timedelta(days=1), "expense", 268, "רכב ותחבורה", "דלק", PARTNER)
        tx(today - timedelta(days=1), "expense", 318.9, "מסעדות ובילויים", "ארוחת ערב עם חברים", None)
    if stress:
        tx(today, "expense", 128400.5, "שופינג וקניות",
           "הזמנה גדולה במיוחד מחנות רהיטים ואביזרים לבית עם שם ארוך מאוד", PARTNER)
        tx(today, "expense", 9999.99, "אחר", "Super-Pharm 24/7 · online order #48213", ME)

    # ── Projects
    def project(key, name, icon, desc, owner=None, budget=None, te=True, ti=False, ts=False,
                archived=False, created="2026-06-01"):
        p = {"id": uid("proj" + key), "family_id": FAM, "name": name, "icon": icon, "description": desc,
             "owner_id": owner, "created_by": ME, "budget_target": budget,
             "track_expense": te, "track_income": ti, "track_savings": ts, "archived": archived,
             "created_at": created + "T09:00:00+00:00"}
        projects.append(p)
        return p

    def pcat(p, name, icon, typ="expense"):
        c = {"id": uid("pc" + p["id"] + name), "project_id": p["id"], "family_id": FAM, "name": name,
             "icon": icon, "type": typ, "sort_order": len(project_categories),
             "created_at": p["created_at"]}
        project_categories.append(c)
        return c

    def ptx(p, c, d, amount, desc, user=None, typ="expense"):
        return tx(d, typ, amount, None, desc, user, project_id=p["id"], project_category_id=c["id"],
                  project_categories={"name": c["name"], "icon": c["icon"]},
                  projects={"owner_id": p["owner_id"], "name": p["name"], "icon": p["icon"]})

    trip = project("trip", "חופשה ביוון", "✈️", "שבוע באתונה ובאיים, אוגוסט", budget=12000, created="2026-07-10")
    c_fl, c_ho, c_fo, c_at = (pcat(trip, n, i) for n, i in
                              [("טיסות", "🛫"), ("מלון", "🏨"), ("אוכל", "🍽️"), ("אטרקציות", "🎟️")])
    d0 = cur
    ptx(trip, c_fl, month_add(d0, -2).replace(day=6), 3180, "טיסות הלוך־חזור", ME)
    ptx(trip, c_ho, month_add(d0, -2).replace(day=9), 2640, "מלון באתונה", ME)
    ptx(trip, c_ho, month_add(d0, -1).replace(day=3), 1120, "צימר בנקסוס", PARTNER)
    ptx(trip, c_fo, month_add(d0, -1).replace(day=14), 486.5, "מסעדות", PARTNER)
    ptx(trip, c_at, month_add(d0, -1).replace(day=15), 320, "שיט לאיים", None)
    if today.day >= 4:
        ptx(trip, c_at, d0.replace(day=2), 260, "כרטיסים לאקרופוליס", ME)
        ptx(trip, c_fo, d0.replace(day=4), 194, "ביטוח נסיעות", None)

    reno = project("reno", "שיפוץ המטבח", "🔨", "ארונות, משטח ואינסטלציה", budget=45000, created="2026-04-02")
    c_cab, c_wrk, c_el = (pcat(reno, n, i) for n, i in [("ארונות", "🚪"), ("עבודה", "👷"), ("חשמל ואינסטלציה", "🔌")])
    ptx(reno, c_cab, month_add(d0, -5).replace(day=11), 14500, "מקדמה לנגר", None)
    ptx(reno, c_wrk, month_add(d0, -4).replace(day=20), 7800, "קבלן שיפוצים", None)
    ptx(reno, c_el, month_add(d0, -3).replace(day=7), 3350, "חשמלאי", PARTNER)
    ptx(reno, c_cab, month_add(d0, -1).replace(day=22), 2650, "משטח עבודה", None)

    car = project("car", "קרן לרכב חדש", "🚗", "", owner=ME, budget=60000, te=False, ts=True, created="2026-02-15")
    c_sv = pcat(car, "הפקדות", "💰", "savings")
    for n in range(-8, 1):
        d = month_add(d0, n).replace(day=1)
        if d <= today:
            ptx(car, c_sv, d, 2000, "הפקדה חודשית", ME, typ="savings")

    wed = project("wed", "החתונה של נועה", "🎉", "מתנה ולבוש", budget=3000, archived=True, created="2025-12-01")
    c_g = pcat(wed, "מתנה", "🎁")
    ptx(wed, c_g, month_add(d0, -9).replace(day=18), 1800, "מתנה", None)
    ptx(wed, c_g, month_add(d0, -9).replace(day=12), 940, "חליפה ושמלה", PARTNER)

    return _tables(scenario, today, profiles, categories, projects, project_categories, txs, cat)


def _tables(scenario, today, profiles, categories, projects, project_categories, txs, cat):
    limits, pinned = {}, {}
    if txs:
        limits = {
            cat["סופר ומזון"]["id"]: {"amount": 3200, "alert": True},
            cat["מסעדות ובילויים"]["id"]: {"amount": 600, "alert": True},
            cat["רכב ותחבורה"]["id"]: {"amount": 1500, "alert": True},
        }
        pinned = {ME: projects[0]["id"]} if projects else {}
    family = {
        "id": FAM, "name": "כהן" if scenario != "stress" else "כהן-רוזנבלום ושות׳ המורחבת",
        "invite_code": "K7M2QX",
        "invite_code_expires_at": (datetime(today.year, today.month, today.day, 12) + timedelta(days=5)).isoformat() + "+00:00",
        "manager_id": ME, "created_at": "2025-09-01T08:00:00+00:00",
        "settings": {"limits": limits, "pinned_projects": pinned},
    }
    return {"families": [family], "profiles": profiles, "categories": categories, "projects": projects,
            "project_categories": project_categories, "transactions": txs, "receipt_scans": [],
            "push_subscriptions": []}


# ─── The page RPCs, in Python, over the tables ─────────────────────────────

def _members(t):
    return [{k: p[k] for k in ("id", "name", "avatar_initial", "email", "phone", "workplace")}
            for p in sorted(t["profiles"], key=lambda p: p["created_at"])]


def _cats(t):
    return sorted(t["categories"], key=lambda c: (c.get("sort_order") is None, c.get("sort_order"), c["name"]))


def _family(t):
    return t["families"][0]


def _archive(t):
    agg = {}
    for r in t["transactions"]:
        if r.get("project_id"):
            continue
        key = (int(r["date"][:4]), int(r["date"][5:7]))
        a = agg.setdefault(key, {"year": key[0], "month": key[1], "income": 0, "expense": 0, "savings": 0})
        if r["type"] in a:
            a[r["type"]] += float(r["amount"])
    out = []
    for key in sorted(agg, reverse=True):
        a = agg[key]
        for kk in ("income", "expense", "savings"):
            a[kk] = round(a[kk], 2)
        a["balance"] = round(a["income"] - a["expense"], 2)
        out.append(a)
    return out


def _month_rows(t, year, month):
    pre = f"{year:04d}-{month:02d}"
    return sorted((r for r in t["transactions"] if r["date"].startswith(pre)), key=lambda r: r["date"], reverse=True)


def rpc(t, name, p):
    """Returns the payload for RPC `name` (deep-copied: the app mutates what it gets)."""
    return copy.deepcopy(_rpc(t, name, p or {}))


def _rpc(t, name, p):
    tx = t["transactions"]
    if name == "get_family_members":
        return _members(t)
    if name == "get_my_family_id":
        return FAM
    if name == "is_family_manager":
        return True
    if name == "get_months_archive":
        return _archive(t)
    if name == "project_totals":
        return list(_totals(t).values())
    if name == "get_month_page":
        return {"family": _family(t), "members": _members(t), "categories": _cats(t),
                "rows": _month_rows(t, p["p_year"], p["p_month"]), "archive": _archive(t)}
    if name == "get_home_page":
        recent = [dict(r, projects=None) for r in tx if not r.get("project_id")
                  and ((not r.get("is_recurring") and not r.get("recurring_parent_id"))
                       or r["date"] >= p["p_recent_from"])]
        recent.sort(key=lambda r: r["created_at"], reverse=True)
        week = [{k: r.get(k) for k in ("amount", "date", "description", "category_id", "is_recurring",
                                         "recurring_parent_id", "project_id", "categories")}
                for r in tx if r["type"] == "expense" and not r.get("project_id")
                and p["p_week_from"] <= r["date"] <= p["p_week_to"]]
        older = any(r["type"] == "expense" and not r.get("project_id") and r.get("is_recurring") is False
                    and not r.get("recurring_parent_id") and r["date"] < p["p_week_start"] for r in tx)
        return {"family": _family(t), "members": _members(t), "categories": _cats(t),
                "rows": _month_rows(t, p["p_year"], p["p_month"]),
                "recent": recent[:p["p_recent_limit"]], "week_rows": week,
                "week_has_older": older, "has_transactions": bool(tx)}
    if name == "get_settings_page":
        totals = {}
        for r in tx:
            if not r.get("project_id") and r.get("category_id") and p["p_avg_from"] <= r["date"] < p["p_avg_to"]:
                totals[r["category_id"]] = totals.get(r["category_id"], 0) + float(r["amount"])
        rec = sorted((r for r in tx if r.get("is_recurring") is True), key=lambda r: -float(r["amount"]))
        ended = [{k: r.get(k) for k in ("id", "amount", "type", "category_id", "description", "recurring_end_date")}
                 for r in rec if r.get("recurring_end_date")]
        return {"family": _family(t), "members": _members(t), "categories": _cats(t),
                "category_totals": totals, "recurring": rec, "ended": ended, "ended_last": {},
                "transaction_count": len(tx)}
    if name == "get_projects_page":
        last = {}
        for r in tx:
            if r.get("project_id"):
                last[r["project_id"]] = max(last.get(r["project_id"], ""), r["date"])
        return {"family": _family(t), "members": _members(t),
                "projects": sorted(t["projects"], key=lambda x: x["created_at"], reverse=True),
                "totals": {k: {kk: v[kk] for kk in ("expense", "income", "savings")} for k, v in _totals(t).items()},
                "last_activity": last}
    if name == "get_months_page":
        rows = [{"id": r["id"], "amount": r["amount"], "category_id": r["category_id"], "date": r["date"],
                 "description": r["description"], "user_id": r["user_id"],
                 "profiles": {"name": r["profiles"]["name"]} if r.get("profiles") else None}
                for r in tx if r["type"] == "expense" and not r.get("project_id")
                and p["p_from"] <= r["date"] < p["p_to"]]
        return {"family": _family(t), "members": _members(t), "categories": _cats(t),
                "archive": _archive(t), "expense_rows": rows}
    if name == "merge_family_settings":
        _family(t)["settings"].update(p.get("p_patch") or {})
        return True
    return None   # log_app_open, renew_expired_invite_code, push_*, …


def _totals(t):
    out = {}
    for r in t["transactions"]:
        pid = r.get("project_id")
        if not pid:
            continue
        a = out.setdefault(pid, {"project_id": pid, "expense": 0.0, "income": 0.0, "savings": 0.0})
        if r["type"] in a:
            a[r["type"]] += float(r["amount"])
    return out
