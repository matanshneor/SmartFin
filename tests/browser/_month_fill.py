"""Filler transactions for the month page (7.10).

Since 5.10 the month page shows the 5 latest transactions, and "לכל עסקאות החודש"
(#txScreenOpen) appears only when there are more than 5. Scripts that created a
transaction or two and waited for the button got stuck. fill_month tops up until
there are more than 5. The accounts are temporary (_accounts), so there is nothing
to clean.
"""
import datetime
import json


def fill_month(page, base, count=6, desc="FILL"):
    cats = page.request.get(base + "/api/categories").json()
    cat = next(c["id"] for c in cats if c["type"] == "expense")
    for i in range(count):
        page.request.fetch(base + "/api/transactions", method="POST", headers={"Content-Type": "application/json"},
                           data=json.dumps({"amount": 1 + i, "type": "expense", "category_id": cat,
                                            "description": desc, "date": datetime.date.today().isoformat()}))
