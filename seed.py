"""Build data/erp.db and data/crm.db with realistic, deterministic ERP + CRM data.

The dataset's "today" is pinned (AS_OF, stored in each DB's `meta.as_of`) so every
run produces byte-identical data. Some customers and vendors are deliberately planted
so the brief's questions have known answers.

    uv run seed.py                       pinned date
    AS_OF=2027-01-15 uv run seed.py      different "today"
"""
import os
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

rng = random.Random(42)
TODAY = date.fromisoformat(os.environ.get("AS_OF", "2026-10-08"))
DATA = Path(__file__).parent / "data"

ERP_DDL = """
CREATE TABLE meta (as_of DATE NOT NULL);  -- the "today" of this dataset; use it instead of date('now')
CREATE TABLE customers (
  id TEXT PRIMARY KEY,            -- 'C0001'; CRM accounts.erp_customer_id points here
  name TEXT NOT NULL,             -- NOT unique: different companies in different cities can share a name
  city TEXT,
  state TEXT,
  segment TEXT,                   -- 'SME' | 'Mid-Market' | 'Enterprise'
  gstin TEXT,
  credit_terms_days INTEGER,      -- invoices.due_date = invoice_date + this
  created_on DATE
);
CREATE TABLE vendors (
  id TEXT PRIMARY KEY,            -- 'V001'
  name TEXT NOT NULL,
  city TEXT,
  category TEXT,                  -- product category they supply
  payment_terms_days INTEGER
);
CREATE TABLE inventory (
  sku TEXT PRIMARY KEY,           -- product catalog + stock, e.g. 'BRG-101'
  name TEXT NOT NULL,
  category TEXT,
  unit_price REAL,                -- INR list price
  stock_qty INTEGER,
  reorder_level INTEGER,
  vendor_id TEXT REFERENCES vendors(id)
);
CREATE TABLE invoices (
  id TEXT PRIMARY KEY,            -- 'INV-00001'; sales invoices we raised on customers
  customer_id TEXT NOT NULL REFERENCES customers(id),
  invoice_date DATE NOT NULL,
  due_date DATE NOT NULL,
  total_amount REAL NOT NULL,     -- INR, = SUM(line_items.amount)
  status TEXT NOT NULL,           -- 'paid' | 'partial' | 'unpaid'
  reason_code TEXT                -- why an overdue invoice is not paid: DISPUTE_PRICING, DISPUTE_QUALITY, PO_MISMATCH, CASH_FLOW, AWAITING_APPROVAL, DELIVERY_SHORTFALL. NULL when paid, not yet due, or not recorded
);
CREATE TABLE line_items (
  id INTEGER PRIMARY KEY,
  invoice_id TEXT NOT NULL REFERENCES invoices(id),
  sku TEXT NOT NULL REFERENCES inventory(sku),
  quantity INTEGER NOT NULL,
  unit_price REAL NOT NULL,       -- price actually charged (after discount)
  amount REAL NOT NULL            -- quantity * unit_price
);
CREATE TABLE payments (
  id INTEGER PRIMARY KEY,         -- customer receipts against sales invoices; an invoice can have several (part payments)
  invoice_id TEXT NOT NULL REFERENCES invoices(id),
  paid_on DATE NOT NULL,
  amount REAL NOT NULL,
  method TEXT                     -- 'NEFT' | 'RTGS' | 'UPI' | 'Cheque'
);
CREATE TABLE purchase_orders (
  id TEXT PRIMARY KEY,            -- 'PO-00001'; our purchases from vendors, plus the vendor's bill for it
  vendor_id TEXT NOT NULL REFERENCES vendors(id),
  sku TEXT REFERENCES inventory(sku),
  order_date DATE NOT NULL,
  quantity INTEGER,
  amount REAL NOT NULL,           -- INR
  expected_delivery DATE,
  delivered_on DATE,              -- NULL = not delivered yet
  vendor_invoice_no TEXT,         -- the vendor's bill number; NULL until delivered
  bill_due_date DATE,             -- payment due date on the vendor bill
  paid_on DATE                    -- when the vendor bill was settled; NULL = unpaid. Days late = julianday(paid_on) - julianday(bill_due_date)
);
CREATE VIEW invoice_balance AS    -- one row per sales invoice with amount paid, balance outstanding and days past due (vs meta.as_of)
SELECT i.id AS invoice_id, i.customer_id, i.invoice_date, i.due_date, i.total_amount, i.status, i.reason_code,
       COALESCE(p.paid, 0) AS amount_paid,
       ROUND(i.total_amount - COALESCE(p.paid, 0), 2) AS balance,
       CAST(julianday((SELECT as_of FROM meta)) - julianday(i.due_date) AS INTEGER) AS days_past_due
FROM invoices i
LEFT JOIN (SELECT invoice_id, SUM(amount) AS paid FROM payments GROUP BY invoice_id) p ON p.invoice_id = i.id;
"""

CRM_DDL = """
CREATE TABLE meta (as_of DATE NOT NULL);  -- the "today" of this dataset; use it instead of date('now')
CREATE TABLE accounts (
  id TEXT PRIMARY KEY,            -- 'A0001'
  erp_customer_id TEXT,           -- ERP customers.id. The ONLY reliable link to ERP: names differ between systems. NULL = prospect not in ERP. A few ERP customers have two accounts
  name TEXT NOT NULL,             -- typed by sales; may differ from ERP name ('M/s', 'Pvt. Ltd.', '&' vs 'and', upper case)
  owner_rep TEXT,                 -- sales rep who owns the account
  tier TEXT,                      -- 'Strategic' | 'Growth' | 'Standard'
  checkin_cadence_days INTEGER,   -- owner should contact the account at least every N days
  created_on DATE
);
CREATE TABLE contacts (
  id TEXT PRIMARY KEY,            -- 'CT0001'
  account_id TEXT NOT NULL REFERENCES accounts(id),
  name TEXT,
  title TEXT,
  email TEXT,
  phone TEXT
);
CREATE TABLE activities (
  id INTEGER PRIMARY KEY,         -- sales touchpoints
  account_id TEXT NOT NULL REFERENCES accounts(id),
  contact_id TEXT REFERENCES contacts(id),  -- may be NULL
  type TEXT NOT NULL,             -- 'call' | 'email' | 'meeting'
  occurred_at TEXT NOT NULL,      -- 'YYYY-MM-DD HH:MM'
  rep TEXT,                       -- who logged it
  subject TEXT,
  outcome TEXT                    -- 'positive' | 'neutral' | 'negative' | 'no_response'
);
CREATE TABLE opportunities (
  id TEXT PRIMARY KEY,            -- 'OPP-0001'
  account_id TEXT NOT NULL REFERENCES accounts(id),
  name TEXT,
  type TEXT,                      -- 'new_business' | 'upsell' | 'renewal' | 'win_back'
  stage TEXT,                     -- 'prospecting' | 'proposal' | 'negotiation' | 'closed_won' | 'closed_lost'
  amount REAL,                    -- INR
  created_on DATE,
  expected_close DATE
);
CREATE TABLE nps_responses (
  id INTEGER PRIMARY KEY,
  account_id TEXT NOT NULL REFERENCES accounts(id),
  contact_id TEXT REFERENCES contacts(id),
  score INTEGER NOT NULL,         -- 0-10: 9-10 promoter, 7-8 passive, 0-6 detractor
  responded_on DATE NOT NULL,
  comment TEXT
);
"""

CITIES = [("Mumbai", "Maharashtra", 27), ("Pune", "Maharashtra", 27), ("Nagpur", "Maharashtra", 27),
          ("Delhi", "Delhi", 7), ("Bengaluru", "Karnataka", 29), ("Chennai", "Tamil Nadu", 33),
          ("Coimbatore", "Tamil Nadu", 33), ("Hyderabad", "Telangana", 36), ("Ahmedabad", "Gujarat", 24),
          ("Surat", "Gujarat", 24), ("Rajkot", "Gujarat", 24), ("Kolkata", "West Bengal", 19),
          ("Jaipur", "Rajasthan", 8), ("Ludhiana", "Punjab", 3), ("Indore", "Madhya Pradesh", 23),
          ("Kochi", "Kerala", 32), ("Lucknow", "Uttar Pradesh", 9)]
NAME_HEADS = ["Sharma", "Gupta", "Agarwal", "Patel", "Mehta", "Shah", "Reddy", "Rao", "Iyer", "Nair", "Kulkarni",
              "Joshi", "Desai", "Malhotra", "Kapoor", "Bansal", "Jain", "Chopra", "Shree Ganesh", "Balaji",
              "Sai Krishna", "Lakshmi", "Om Sai", "Jai Bharat", "Navkar", "Siddhi Vinayak", "Mahalaxmi",
              "Vardhman", "Sunrise", "Apex", "Pioneer", "Trident", "Galaxy", "Supreme", "Royal", "Excel",
              "Orient", "Everest", "Unity", "Vikas", "Kaveri", "Ganga", "Narmada", "Deccan", "Konark", "Ashoka"]
NAME_TAILS = ["Traders", "Enterprises", "Industries", "Distributors", "Agencies", "Udyog", "Engineering",
              "Polymers", "Steel", "Hardware", "Electricals", "Pharma", "Textiles", "Foods", "Auto Parts",
              "Packaging", "Chemicals", "Infra", "Exports", "Machine Tools"]
LEGAL = ["", "", "", " Pvt Ltd", " Pvt Ltd", " LLP", " & Co"]
CATALOG = {  # category: (sku prefix, min price, max price, product names)
    "Bearings": ("BRG", 180, 4500, ["Ball Bearing 6204", "Ball Bearing 6305", "Taper Roller Bearing 32210",
                                    "Pillow Block UCP208", "Needle Roller Bearing NK25", "Spherical Bearing 22216"]),
    "Valves": ("VLV", 1200, 38000, ["Gate Valve 2in CI", "Ball Valve 1in SS304", "Butterfly Valve 4in",
                                    "Non-Return Valve 3in", "Solenoid Valve 24V DC"]),
    "Pipes & Fittings": ("PIP", 250, 9000, ["GI Pipe 1in 6m", "MS ERW Pipe 2in", "SS Elbow 1.5in",
                                            "HDPE Pipe 63mm 6m", "Pipe Flange 4in"]),
    "Cables": ("CBL", 900, 65000, ["Armoured Cable 4C 16sqmm 100m", "Control Cable 12C 100m",
                                   "FR House Wire 2.5sqmm 90m", "Flexible Cable 3C 1.5sqmm 100m"]),
    "Motors": ("MTR", 14000, 240000, ["Induction Motor 3HP", "Induction Motor 10HP", "Geared Motor 1HP",
                                      "Servo Motor 2kW", "Flameproof Motor 7.5HP"]),
    "Pumps": ("PMP", 6500, 120000, ["Centrifugal Pump 2HP", "Submersible Pump 5HP", "Dosing Pump 10LPH",
                                    "Gear Pump 15LPM"]),
    "Fasteners": ("FST", 150, 2500, ["Hex Bolt M12 box-100", "SS Nut M10 box-200", "Anchor Fastener M16 box-50",
                                     "Spring Washer M8 box-500", "Allen Bolt M6 box-100"]),
    "Safety Gear": ("PPE", 200, 6000, ["Safety Helmet ISI", "Safety Shoes S3", "Nitrile Gloves box-100",
                                       "Full Body Harness", "Safety Goggles"]),
    "Lubricants": ("LUB", 450, 18000, ["Hydraulic Oil 68 20L", "Gear Oil EP90 20L", "Lithium Grease 18kg",
                                       "Cutting Oil 20L"]),
    "Packaging": ("PKG", 300, 8000, ["Stretch Film 23mic", "Corrugated Box 5-ply x100", "BOPP Tape 72mm x72",
                                     "Pallet Strapping Roll"]),
    "Electrical Panels": ("ELC", 2500, 150000, ["MCB 32A TP", "MCCB 250A", "VFD 7.5kW", "Contactor 40A",
                                                "PLC Starter Kit"]),
    "Power Tools": ("TLS", 2200, 45000, ["Angle Grinder 4in", "Rotary Hammer 26mm", "Cordless Drill 18V",
                                         "Bench Grinder 8in"]),
}
VENDOR_HEADS = ["Precision", "Bharat", "National", "Allied", "Standard", "Shakti", "Kalyan", "Venkatesh", "Mahavir",
                "Ambica", "Tirupati", "Durga", "Neelkanth", "Satyam", "Ajanta", "Hari Om", "Classic", "Elite",
                "Modern", "Pragati", "Sapphire", "Western", "Eastern", "Southern"]
VENDOR_TAILS = [" Supplies", " Mfg Co", " Industries", " Corporation", " Sales"]
REPS = ["Priya Nair", "Rahul Verma", "Ankit Sharma", "Sneha Kulkarni", "Vikram Rao", "Meera Iyer", "Arjun Mehta",
        "Kavya Reddy"]
FIRST = ["Rajesh", "Suresh", "Anita", "Pooja", "Amit", "Neha", "Vijay", "Deepa", "Sanjay", "Kiran", "Manoj", "Ravi",
         "Sunita", "Arun", "Divya", "Prakash", "Swati", "Naveen", "Asha", "Imran", "Farah", "Joseph", "Mary",
         "Gurpreet", "Harpreet"]
LAST = ["Kumar", "Sharma", "Patel", "Reddy", "Nair", "Iyer", "Shah", "Gupta", "Joshi", "Khan", "D'Souza", "Singh",
        "Das", "Bose", "Pillai", "Verma"]
TITLES = ["Purchase Manager", "Owner", "Accounts Head", "Plant Head", "CFO", "Procurement Lead", "Director"]
REASONS = ["DISPUTE_PRICING", "DISPUTE_QUALITY", "PO_MISMATCH", "CASH_FLOW", "AWAITING_APPROVAL", "DELIVERY_SHORTFALL"]
SUBJECTS = {"call": ["Payment follow-up", "Order status", "Quarterly check-in", "Price negotiation"],
            "email": ["Quote sent", "Invoice reminder", "New product catalog", "Follow-up on proposal"],
            "meeting": ["Site visit", "Annual review", "Product demo", "Escalation meeting"]}
NPS_COMMENTS = {"promoter": ["Reliable delivery, good support", "Competitive pricing, responsive team",
                             "Quick turnaround on urgent orders"],
                "passive": ["Okay overall, delivery sometimes delayed", "Products fine, occasional invoicing errors",
                            "Average service"],
                "detractor": ["Repeated quality issues", "Billing disputes not resolved",
                              "Delivery delays hurting our production", "Sales team unresponsive"]}
# Diwali (Oct-Nov) and fiscal year-end (March) peaks, monsoon dip
SEASON = {1: 1.0, 2: 1.0, 3: 1.5, 4: 0.9, 5: 0.85, 6: 0.75, 7: 0.75, 8: 0.9, 9: 1.1, 10: 1.6, 11: 1.4, 12: 1.1}
# Planted profiles drive the demo questions; 'mid' and 'sme' are ordinary customers
PROFILES = [("enterprise", 10), ("grower", 7), ("headline", 6), ("headline_decoy", 4), ("unpaid3", 8),
            ("dormant_big", 6), ("dormant_small", 6), ("mid", 45), ("sme", 128)]


def ago(days):
    return TODAY - timedelta(days=days)


def between(a, b):
    return a + timedelta(days=rng.randint(0, max(0, (b - a).days)))


def season_dates(n, start, end):
    days = [start + timedelta(i) for i in range((end - start).days + 1)]
    return sorted(rng.choices(days, weights=[SEASON[x.month] for x in days], k=n))


def u(lo, hi):
    return rng.uniform(lo, hi)


def plan_invoices(profile):
    """List of (invoice_date, target_amount, mode). mode: normal | paid | unpaid (overdue, forced)."""
    def recent_tail(dates, within):  # ordinary customers keep buying: last invoice inside `within` days
        if dates[-1] < ago(within):
            dates[-1] = ago(rng.randint(1, within))
        return sorted(dates)

    if profile == "enterprise":
        dates = recent_tail(season_dates(rng.randint(28, 40), ago(rng.randint(690, 729)), ago(1)), 30)
        return [(x, u(6e5, 14e5), "normal") for x in dates]
    if profile == "grower":  # first order under 1L, last few over 5L
        n = rng.randint(10, 14)
        dates = recent_tail(season_dates(n, ago(rng.randint(560, 680)), ago(5)), 45)
        mids = [1e5 * 4 ** (i / max(1, n - 6)) for i in range(n - 5)]
        targets = [u(40e3, 60e3)] + mids + [u(6.5e5, 9e5) for _ in range(4)]
        return [(x, t, "normal") for x, t in zip(dates, targets)]
    if profile in ("headline", "headline_decoy"):  # 2 big overdue invoices in the last 6 months
        old = season_dates(rng.randint(6, 11), ago(rng.randint(450, 700)), ago(200))
        rows = [(x, u(1.6e5, 3.2e5), "paid") for x in old]
        rows += [(ago(x), u(3.0e5, 3.4e5), "unpaid") for x in rng.sample(range(75, 161), 2)]
        if rng.random() < 0.5:
            rows.append((ago(rng.randint(20, 70)), u(1.6e5, 2.5e5), "paid"))
        return sorted(rows)
    if profile == "unpaid3":  # 3-5 small overdue invoices
        old = season_dates(rng.randint(5, 10), ago(rng.randint(500, 700)), ago(160))
        rows = [(x, u(20e3, 70e3), "paid") for x in old]
        rows += [(ago(x), u(20e3, 60e3), "unpaid") for x in rng.sample(range(40, 151), rng.randint(3, 5))]
        return sorted(rows)
    if profile == "dormant_big":
        dates = season_dates(rng.randint(9, 14), ago(rng.randint(600, 729)), ago(rng.randint(200, 420)))
        return [(x, u(1.6e5, 3.2e5), "paid") for x in dates]
    if profile == "dormant_small":
        dates = season_dates(rng.randint(4, 7), ago(rng.randint(600, 700)), ago(rng.randint(200, 420)))
        return [(x, u(20e3, 70e3), "paid") for x in dates]
    if profile == "mid":
        dates = recent_tail(season_dates(rng.randint(8, 18), ago(rng.randint(400, 729)), ago(1)), 150)
        return [(x, u(1.6e5, 3.2e5), "normal") for x in dates]
    dates = recent_tail(season_dates(rng.randint(3, 14), ago(rng.randint(200, 729)), ago(1)), 150)
    return [(x, u(15e3, 1.2e5), "normal") for x in dates]


def build_lines(target, usual, products):
    """Line items landing within ~10% of target: every line uses a SKU priced <= 1/5 of its share."""
    n = rng.randint(1, 4) if target < 2e5 else rng.randint(2, 6)
    weights = [u(1, 3) for _ in range(n)]
    lines = []
    for w in weights:
        share = target * w / sum(weights)
        pool = ([p for p in usual if p["price"] <= share / 5] or [p for p in products if p["price"] <= share / 5]
                or [min(products, key=lambda p: p["price"])])
        p = rng.choice(pool)
        price = round(p["price"] * u(0.92, 1.0), 2)
        qty = max(1, round(share / price))
        lines.append((p["sku"], qty, price, round(qty * price, 2)))
    return lines


def settle(inv, payer, budget):
    """Decide payments for one invoice. Returns remaining budget of not-yet-paid invoices."""
    lo, hi = (-5, 5) if payer == "prompt" else (8, 40)
    pay = inv["due"] + timedelta(days=rng.randint(lo, hi))
    if inv["mode"] == "unpaid":
        inv["reason"] = rng.choice(REASONS) if rng.random() > 0.15 else None
        return budget
    if inv["mode"] == "normal" and pay >= TODAY and budget > 0:
        if rng.random() < 0.3:  # part payment
            inv["payments"].append((between(inv["date"], ago(1)), round(inv["total"] * u(0.3, 0.6), 2)))
        if inv["due"] < TODAY:
            inv["reason"] = rng.choice(REASONS) if rng.random() > 0.2 else None
        return budget - 1
    pay = min(max(pay, inv["date"]), ago(1))
    if rng.random() < 0.15:  # paid in two instalments
        first = round(inv["total"] * u(0.4, 0.7), 2)
        inv["payments"] += [(max(inv["date"], pay - timedelta(days=rng.randint(5, 20))), first),
                            (pay, round(inv["total"] - first, 2))]
    else:
        inv["payments"].append((pay, inv["total"]))
    return budget


def crm_name(name):
    r = rng.random()
    if r < 0.12:
        return "M/s " + name
    if r < 0.24:
        return name.replace(" Pvt Ltd", "") if "Pvt Ltd" in name else name + " Pvt. Ltd."
    if r < 0.32:
        return name.upper()
    if r < 0.38 and " & " in name:
        return name.replace(" & ", " and ")
    return name


def main():
    DATA.mkdir(exist_ok=True)
    for f in ("erp.db", "crm.db"):
        (DATA / f).unlink(missing_ok=True)
    erp, crm = sqlite3.connect(DATA / "erp.db"), sqlite3.connect(DATA / "crm.db")
    erp.executescript(ERP_DDL)
    crm.executescript(CRM_DDL)
    for db in (erp, crm):
        db.execute("INSERT INTO meta VALUES (?)", (TODAY.isoformat(),))

    # --- vendors + inventory -------------------------------------------------
    cats = list(CATALOG)
    vendors = []
    for i, head in enumerate(rng.sample(VENDOR_HEADS * 3, 55)):
        cat = cats[i % len(cats)]
        city = rng.choice(CITIES)
        vendors.append({"id": f"V{i + 1:03d}", "name": f"{head} {cat.split()[0]}{rng.choice(VENDOR_TAILS)}",
                        "city": city[0], "category": cat, "terms": rng.choice([30, 45, 60])})
    products = []
    for cat, (prefix, lo, hi, names) in CATALOG.items():
        for j, name in enumerate(names):
            products.append({"sku": f"{prefix}-{101 + j}", "name": name, "category": cat,
                             "price": round(u(lo, hi), -1),
                             "vendor": rng.choice([v["id"] for v in vendors if v["category"] == cat])})
    erp.executemany("INSERT INTO vendors VALUES (?,?,?,?,?)",
                    [(v["id"], v["name"], v["city"], v["category"], v["terms"]) for v in vendors])
    erp.executemany("INSERT INTO inventory VALUES (?,?,?,?,?,?,?)",
                    [(p["sku"], p["name"], p["category"], p["price"], rng.randint(0, 500), rng.randint(20, 100),
                      p["vendor"]) for p in products])

    # --- customers -----------------------------------------------------------
    names = rng.sample([f"{h} {t}" for h in NAME_HEADS for t in NAME_TAILS], 220)
    specs = []
    for profile, count in PROFILES:
        for _ in range(count):
            segment = {"enterprise": "Enterprise", "unpaid3": "SME", "dormant_small": "SME", "sme": "SME"}.get(
                profile, "Mid-Market")
            city = rng.choice(CITIES)
            specs.append({"profile": profile, "segment": segment, "name": names.pop() + rng.choice(LEGAL),
                          "city": city, "usual": rng.sample(products, 8),
                          "terms": {"Enterprise": rng.choice([45, 60]), "Mid-Market": rng.choice([30, 45]),
                                    "SME": rng.choice([15, 30])}[segment],
                          "payer": "prompt" if profile == "enterprise" else
                                   "slow" if profile.startswith("headline") else
                                   rng.choices(["prompt", "slow"], [65, 35])[0]})
    # Duplicate names: same company name, different city, different customer
    ordinary = [s for s in specs if s["profile"] in ("mid", "sme")]
    rng.shuffle(ordinary)
    targets = [next(s for s in specs if s["profile"] == p) for p in ("headline", "unpaid3", "dormant_big")]
    targets += ordinary[:3]
    for target, twin in zip(targets, ordinary[3:9]):
        twin["name"] = target["name"]
        twin["city"] = rng.choice([c for c in CITIES if c[0] != target["city"][0]])

    invoices = []
    for s in specs:
        s["invoices"] = []
        for when, target, mode in plan_invoices(s["profile"]):
            lines = build_lines(target, s["usual"], products)
            inv = {"spec": s, "date": when, "due": when + timedelta(days=s["terms"]), "lines": lines,
                   "total": round(sum(x[3] for x in lines), 2), "mode": mode, "payments": [], "reason": None}
            s["invoices"].append(inv)
            invoices.append(inv)
        budget = 2  # ordinary customers carry at most 2 open invoices
        for inv in sorted(s["invoices"], key=lambda x: x["date"], reverse=True):
            budget = settle(inv, s["payer"], budget)
        s["outstanding"] = sum(i["total"] - sum(a for _, a in i["payments"]) for i in s["invoices"])
        s["created"] = s["invoices"][0]["date"] - timedelta(days=rng.randint(0, 90))

    specs.sort(key=lambda s: (s["created"], s["name"]))
    for n, s in enumerate(specs, 1):
        s["id"] = f"C{n:04d}"
    erp.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)", [
        (s["id"], s["name"], s["city"][0], s["city"][1], s["segment"],
         f"{s['city'][2]:02d}{''.join(rng.choices('ABCDEFGHJKLMNPQRSTUVWXYZ', k=5))}{rng.randint(1000, 9999)}"
         f"{rng.choice('ABCDEFGH')}1Z{rng.randint(1, 9)}", s["terms"], s["created"].isoformat()) for s in specs])

    invoices.sort(key=lambda i: (i["date"], i["spec"]["id"]))
    inv_rows, line_rows, pay_rows = [], [], []
    for n, inv in enumerate(invoices, 1):
        iid = f"INV-{n:05d}"
        paid = round(sum(a for _, a in inv["payments"]), 2)
        status = "paid" if paid >= inv["total"] - 0.01 else "partial" if paid else "unpaid"
        inv_rows.append((iid, inv["spec"]["id"], inv["date"].isoformat(), inv["due"].isoformat(), inv["total"],
                         status, inv["reason"]))
        line_rows += [(iid, *line) for line in inv["lines"]]
        pay_rows += [(iid, d.isoformat(), a, rng.choice(["NEFT", "RTGS", "UPI", "Cheque"])) for d, a in
                     sorted(inv["payments"])]
    erp.executemany("INSERT INTO invoices VALUES (?,?,?,?,?,?,?)", inv_rows)
    erp.executemany("INSERT INTO line_items (invoice_id, sku, quantity, unit_price, amount) VALUES (?,?,?,?,?)",
                    line_rows)
    erp.executemany("INSERT INTO payments (invoice_id, paid_on, amount, method) VALUES (?,?,?,?)", pay_rows)

    # --- purchase orders: vendor bill timeliness -----------------------------
    by_cat = {c: [p for p in products if p["category"] == c] for c in cats}
    shuffled = rng.sample(vendors, len(vendors))
    improving, worsening = shuffled[:4], shuffled[4:7]
    po_rows = []
    for v in vendors:
        if v in improving or v in worsening:
            # enough bills on both sides of the 3-month line; old bills are paid before it too (even 35 days late),
            # so grouping by due date or by paid date gives the same answer
            dues = [ago(rng.randint(125, 180)) for _ in range(2)] + \
                   [ago(rng.randint(125, 330)) for _ in range(rng.randint(2, 5))] + \
                   [ago(rng.randint(5, 85)) for _ in range(rng.randint(3, 5))]
        else:
            dues = [ago(rng.randint(-60, 340)) for _ in range(rng.randint(5, 11))]
        base = rng.randint(-3, 10)
        for due in dues:
            recent = due >= ago(90)
            if v in improving:
                late = rng.randint(-3, 3) if recent else rng.randint(15, 35)
            elif v in worsening:
                late = rng.randint(18, 35) if recent else rng.randint(-2, 4)
            else:
                late = base + rng.randint(-4, 4)
            delivered = due - timedelta(days=v["terms"])
            ordered = delivered - timedelta(days=rng.randint(7, 20))
            p = rng.choice(by_cat[v["category"]])
            qty = max(1, round(u(40e3, 6e5) / p["price"]))
            billed = delivered <= TODAY
            paid = due + timedelta(days=late)
            paid = paid if billed and paid <= TODAY and rng.random() > 0.04 else None
            po_rows.append([v["id"], p["sku"], ordered.isoformat(), qty, round(qty * p["price"] * 0.72, 2),
                            (ordered + timedelta(days=rng.randint(7, 14))).isoformat(),
                            delivered.isoformat() if billed else None,
                            f"{v['name'][:3].upper()}/{rng.randint(1000, 9999)}" if billed else None,
                            due.isoformat() if billed else None, paid.isoformat() if paid else None])
    po_rows.sort(key=lambda r: r[2])
    erp.executemany("INSERT INTO purchase_orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(f"PO-{n:05d}", *r) for n, r in enumerate(po_rows, 1)])

    # --- CRM -----------------------------------------------------------------
    accounts, contacts, activities, opps, nps = [], [], [], [], []
    orphans = {id(s) for s in [s for s in ordinary[9:] if s["profile"] == "sme"][:8]}
    dup_accounts = {id(s) for s in [s for s in ordinary[9:] if s["profile"] == "mid"][:2]}

    def add_account(erp_id, name, profile, created, outstanding):
        aid = f"A{len(accounts) + 1:04d}"
        tier, cadence = (("Strategic", 14) if profile == "enterprise" else
                         ("Growth", rng.choice([21, 30])) if profile == "grower" else
                         ("Growth", 30) if profile in ("mid", "headline", "headline_decoy", "dormant_big") else
                         ("Standard", rng.choice([45, 60])))
        owner = rng.choice(REPS)
        accounts.append((aid, erp_id, name, owner, tier, cadence, created.isoformat()))
        cids = []
        for _ in range(rng.randint(1, 4)):
            first, last = rng.choice(FIRST), rng.choice(LAST)
            cid = f"CT{len(contacts) + 1:04d}"
            slug = "".join(ch for ch in name.lower().split()[0] if ch.isalnum()) or "co"
            contacts.append((cid, aid, f"{first} {last}", rng.choice(TITLES),
                             f"{first.lower()}.{last.lower().replace(chr(39), '')}@{slug}.in",
                             f"+91 9{rng.randint(100000000, 999999999)}"))
            cids.append(cid)
        # recency of last touch is what several questions hinge on
        if profile == "enterprise":
            recency = rng.randint(1, 10)
        elif profile == "headline":
            recency = rng.randint(35, 90)
        elif profile == "headline_decoy" or outstanding > 4e5:
            recency = rng.randint(1, 20)
        elif profile.startswith("dormant"):
            recency = rng.randint(60, 300)
        else:
            recency = rng.randint(0, 120)
        last_touch = ago(recency)
        lo, hi = {"Strategic": (15, 25), "Growth": (6, 12), "Standard": (2, 6)}[tier]
        for k in range(rng.randint(lo, hi)):
            when = last_touch if k == 0 else between(max(created, ago(730)), last_touch)
            kind = rng.choices(["call", "email", "meeting"], [45, 40, 15])[0]
            activities.append((aid, rng.choice(cids) if rng.random() > 0.1 else None, kind,
                               f"{when.isoformat()} {rng.randint(9, 18):02d}:{rng.choice(['00', '15', '30', '45'])}",
                               owner if rng.random() < 0.85 else rng.choice(REPS), rng.choice(SUBJECTS[kind]),
                               rng.choices(["positive", "neutral", "negative", "no_response"], [35, 35, 10, 20])[0]))
        # NPS: strategic accounts surveyed every 2-3 months, others occasionally
        base = rng.randint(5, 9)
        if tier == "Strategic":
            when, dates = ago(rng.randint(700, 729)), []
            while when < TODAY:
                dates.append(when)
                when += timedelta(days=rng.randint(60, 100))
        else:
            dates = [between(max(created, ago(730)), ago(1)) for _ in range(rng.randint(0, 3 if tier == "Growth" else 2))]
        for when in dates:
            score = max(0, min(10, base + rng.randint(-2, 2)))
            bucket = "promoter" if score >= 9 else "passive" if score >= 7 else "detractor"
            nps.append((aid, rng.choice(cids), score, when.isoformat(),
                        rng.choice(NPS_COMMENTS[bucket]) if rng.random() > 0.25 else None))
        return aid

    def add_opp(aid, kind, stage, amount, created):
        opps.append((f"OPP-{len(opps) + 1:04d}", aid, f"{kind.replace('_', ' ').title()} FY{created.year % 100}",
                     kind, stage, round(amount, -3), created.isoformat(),
                     (created + timedelta(days=rng.randint(30, 120))).isoformat()))

    dormant_big_seen = 0
    for s in specs:
        if id(s) in orphans:
            continue
        aid = add_account(s["id"], crm_name(s["name"]), s["profile"], s["created"], s["outstanding"])
        if id(s) in dup_accounts:  # same ERP customer entered twice by sales
            add_account(s["id"], crm_name(s["name"]).upper(), "sme", s["created"] + timedelta(days=200), 0)
        if s["profile"] == "dormant_big":
            if dormant_big_seen < 3:
                add_opp(aid, "win_back", rng.choice(["prospecting", "proposal"]), u(5e5, 15e5),
                        ago(rng.randint(20, 90)))
            elif dormant_big_seen < 5:
                add_opp(aid, "win_back", "closed_lost", u(5e5, 12e5), ago(rng.randint(220, 330)))
            dormant_big_seen += 1
        elif s["profile"] == "dormant_small" and rng.random() < 0.3:
            add_opp(aid, "win_back", "prospecting", u(1e5, 3e5), ago(rng.randint(20, 90)))
    for _ in range(5):  # prospects not yet in ERP
        name = f"{rng.choice(NAME_HEADS)} {rng.choice(NAME_TAILS)}"
        add_opp(add_account(None, name, "prospect", ago(rng.randint(30, 200)), 0), "new_business",
                rng.choice(["prospecting", "proposal"]), u(2e5, 8e5), ago(rng.randint(5, 60)))
    active = [a for a in accounts if a[1] and not any(o[1] == a[0] for o in opps)]
    for a in rng.sample(active, 140):
        created = ago(rng.randint(10, 540))
        stage = (rng.choice(["prospecting", "proposal", "negotiation"]) if created > ago(120)
                 else rng.choice(["closed_won", "closed_won", "closed_lost"]))
        lo, hi = {"Strategic": (10e5, 60e5), "Growth": (2e5, 12e5), "Standard": (0.5e5, 3e5)}[a[4]]
        add_opp(a[0], rng.choices(["upsell", "renewal", "new_business"], [55, 35, 10])[0], stage, u(lo, hi), created)

    crm.executemany("INSERT INTO accounts VALUES (?,?,?,?,?,?,?)", accounts)
    crm.executemany("INSERT INTO contacts VALUES (?,?,?,?,?,?)", contacts)
    activities.sort(key=lambda a: a[3])
    crm.executemany("INSERT INTO activities (account_id, contact_id, type, occurred_at, rep, subject, outcome) "
                    "VALUES (?,?,?,?,?,?,?)", activities)
    crm.executemany("INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?)", opps)
    nps.sort(key=lambda r: r[3])
    crm.executemany("INSERT INTO nps_responses (account_id, contact_id, score, responded_on, comment) "
                    "VALUES (?,?,?,?,?)", nps)
    erp.commit()
    crm.commit()

    for db, name in ((erp, "erp"), (crm, "crm")):
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'meta'")]
        print(f"{name}.db: " + ", ".join(f"{t}={db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}"
                                         for t in tables))
    print(f"as_of {TODAY}")


if __name__ == "__main__":
    main()
