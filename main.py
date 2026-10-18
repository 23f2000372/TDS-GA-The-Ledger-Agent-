import json
import os
import re
import calendar
from datetime import datetime
from collections import defaultdict, Counter

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("Asia/Kolkata")

# ---------- LOAD DATA ----------
with open("all_orders.json") as f:
    raw_orders = json.load(f)

with open("rates_data.json") as f:
    RATES = json.load(f)["usd_per_unit"]

# Keep the latest version of each order
latest = {}
for order in raw_orders:
    oid = order["id"]
    previous = latest.get(oid)
    if previous is None or datetime.fromisoformat(
        order["updated_at"].replace("Z", "+00:00")
    ) > datetime.fromisoformat(previous["updated_at"].replace("Z", "+00:00")):
        latest[oid] = order

ORDERS = list(latest.values())

def usd(order):
    return float(order["amount"]) * RATES[order["currency"]]

def norm(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()

PRODUCTS = sorted(
    {o["product"] for o in ORDERS},
    key=len, reverse=True
)
CUSTOMERS = sorted(
    {o["customer"] for o in ORDERS},
    key=len, reverse=True
)
REGIONS = sorted(
    {o["region"] for o in ORDERS},
    key=len, reverse=True
)

MONTHS = {
    name.lower(): i
    for i, name in enumerate(calendar.month_name) if name
}
MONTHS.update({
    name.lower(): i
    for i, name in enumerate(calendar.month_abbr) if name
})

def answer_question(question):
    q = norm(question)
    if not q:
        raise ValueError("Please provide a question.")

    # Select the status relevant to the question
    if re.search(r"\b(refund|refunded|refunds)\b", q):
        status = "refunded"
    elif re.search(r"\b(void|voided|cancelled|canceled)\b", q):
        status = "void"
    else:
        status = "paid"

    rows = [o for o in ORDERS if o["status"] == status]

    # Region filter
    for region in REGIONS:
        if norm(region) in q:
            rows = [o for o in rows if o["region"] == region]
            break

    # Product filter
    for product in PRODUCTS:
        if norm(product) in q:
            rows = [o for o in rows if o["product"] == product]
            break

    # Customer filter
    for customer in CUSTOMERS:
        if norm(customer) in q:
            rows = [o for o in rows if o["customer"] == customer]
            break

    # Specific order ID
    ids = re.findall(r"\bord\s*[- ]?\s*\d+\b", question, re.I)
    if ids:
        target = re.sub(r"\s+", "", ids[0]).upper().replace("ORD", "ORD-")
        rows = [o for o in rows if o["id"].upper() == target]

    # Calendar filters, including March 2026 and 2026-03
    year_match = re.search(r"\b(20\d{2})\b", q)
    year = int(year_match.group(1)) if year_match else None
    month = None

    for name, number in MONTHS.items():
        if re.search(r"\b" + name + r"\b", q):
            month = number
            break

    numeric_month = re.search(r"\b20\d{2}[-/](0?[1-9]|1[0-2])\b", q)
    if numeric_month:
        year = int(numeric_month.group(0)[:4])
        month = int(numeric_month.group(1))

    if year or month:
        filtered = []
        
        for o in rows:
            dt = datetime.fromisoformat(
                o["created_at"].replace("Z", "+00:00")
            )
            dt = dt.astimezone(BUSINESS_TZ)
            if year and dt.year != year:
                continue
            if month and dt.month != month:
                continue
            
            filtered.append(o)
        rows = filtered

    # Ranking questions
    if re.search(r"\b(top|highest|most|best|largest|maximum)\b", q):
        if "customer" in q:
            groups = defaultdict(float)
            for o in rows:
                groups[o["customer"]] += usd(o)
            return max(groups, key=groups.get) if groups else None

        if "product" in q:
            groups = defaultdict(float)
            for o in rows:
                groups[o["product"]] += usd(o)
            return max(groups, key=groups.get) if groups else None

        if "region" in q:
            groups = defaultdict(float)
            for o in rows:
                groups[o["region"]] += usd(o)
            return max(groups, key=groups.get) if groups else None

    # Questions asking which products/customers/regions
    if re.search(r"\b(which|list|names|name of)\b", q):
        if "product" in q:
            return sorted({o["product"] for o in rows})
        if "customer" in q:
            return sorted({o["customer"] for o in rows})
        if "region" in q:
            return sorted({o["region"] for o in rows})

    # Count questions
    if re.search(r"\b(how many|number of|count|quantity of orders)\b", q):
        if re.search(r"\b(customers?|clients?)\b", q):
            return len({o["customer"] for o in rows})
        return len(rows)

    # Average questions
    if "average" in q or "mean" in q:
        if not rows:
            return 0
        return round(sum(usd(o) for o in rows) / len(rows), 2)

    # Product/customer/region ranking by count rather than revenue
    if re.search(r"\b(most orders|most frequently|popular|bestselling)\b", q):
        field = "product" if "product" in q else (
            "customer" if "customer" in q else "region"
        )
        counts = Counter(o[field] for o in rows)
        return counts.most_common(1)[0][0] if counts else None

    # Default financial answer: sum of relevant orders in USD
    return round(sum(usd(o) for o in rows), 2)


app = FastAPI(title="Acme Appliances Order Ledger API")

class Question(BaseModel):
    question: str

@app.get("/")
def health():
    return {
        "service": "Acme Appliances Order Ledger API",
        "status": "ready",
        "orders_loaded": len(ORDERS)
    }

@app.post("/")
@app.post("/answer")
def ask(body: Question):
    try:
        result = answer_question(body.question)
        return {"answer": result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0",
                port=int(os.environ.get("PORT", "8000")))
