"""ERP and CRM table definitions. The column comments are the data dictionary the agent reads via describe_tables."""

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
