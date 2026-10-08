"""Tools the agent can call.

Add a tool: write a function with type hints and a Google-style docstring, decorate it
with @tool(parse_docstring=True), and append it to TOOLS. The docstring is what the
model reads to decide when to call it.
"""
import itertools
import json
import re
import sqlite3
import statistics
import time
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Literal

from langchain_core.tools import tool

DATA = Path(__file__).parent / "data"
MAX_ROWS = 25  # rows shown to the model; {{rN.col}} references still carry every row
KEEP_ROWS = 10_000  # rows kept per result for {{rN.column}} references
TIMEOUT_S = 10
RESULTS = {}  # ponytail: every result kept for the process lifetime; fine for a CLI, add eviction for a server
_refs = itertools.count(1)
REF = re.compile(r"\{\{(r\d+)\.(\w+)\}\}")


def _deny_attach(action, *_):
    # read-only already blocks writes; this stops ATTACH of some other (writable) file
    return sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH) else sqlite3.SQLITE_OK


def run_sql(db, sql, params=(), limit=MAX_ROWS):
    """Execute one statement read-only. Returns (columns, rows) with up to limit+1 rows."""
    with closing(sqlite3.connect(f"file:{DATA / db}?mode=ro", uri=True)) as conn:
        conn.set_authorizer(_deny_attach)
        deadline = time.monotonic() + TIMEOUT_S
        conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
        try:
            cur = conn.execute(sql, params)
        except sqlite3.OperationalError as e:
            if str(e) == "interrupted":
                raise TimeoutError(f"query ran over {TIMEOUT_S}s; filter earlier or avoid cross joins") from e
            raise
        return [c[0] for c in cur.description or []], cur.fetchmany(limit + 1)


def to_json(cols, rows, ref=None):
    out = {"columns": cols, "rows": [[round(v, 2) if isinstance(v, float) else v for v in r] for r in rows[:MAX_ROWS]],
           "row_count": min(len(rows), MAX_ROWS)}
    if ref:
        out["result_ref"] = ref
    if len(rows) > MAX_ROWS:
        total = f"{KEEP_ROWS}+" if len(rows) > KEEP_ROWS else len(rows)
        out["note"] = (f"showing {MAX_ROWS} of {total} rows; aggregate in SQL, or pass a whole column on with "
                       f"{{{{{ref}.<column>}}}}")
    return json.dumps(out, default=str, ensure_ascii=False)


def remember(cols, rows):
    """Store a result under a new ref (r1, r2, ...) and return it formatted for the model."""
    ref = f"r{next(_refs)}"
    RESULTS[ref] = (cols, rows[:KEEP_ROWS])
    return to_json(cols, rows, ref)


def _quote(v):
    return str(v) if isinstance(v, (int, float)) else "'" + str(v).replace("'", "''") + "'"


def expand_refs(sql):
    """Replace {{r3.col}} with the distinct quoted values of `col` from stored result r3 (all rows, not just 100)."""
    def values(m):
        ref, col = m.groups()
        if ref not in RESULTS:
            raise ValueError(f"unknown result {ref}; available: {', '.join(RESULTS) or 'none'}")
        cols, rows = RESULTS[ref]
        if col not in cols:
            raise ValueError(f"{ref} has no column {col!r}; its columns are {cols}")
        i = cols.index(col)
        return ", ".join(dict.fromkeys(_quote(r[i]) for r in rows if r[i] is not None))
    return REF.sub(values, sql)


def schema_text():
    """DDL of both databases (column comments included) for the system prompt."""
    parts = []
    for db in ("erp", "crm"):
        with closing(sqlite3.connect(DATA / f"{db}.db")) as conn:
            ddl = [r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")]
        parts.append(f"### {db.upper()} database (tool: query_{db})\n" + ";\n".join(ddl) + ";")
    return "\n\n".join(parts)


def as_of():
    return run_sql("erp.db", "SELECT as_of FROM meta")[1][0][0]


@tool(parse_docstring=True)
def query_erp(sql: str) -> str:
    """Run ONE read-only SQLite query on the ERP database (customers, invoices, line_items, payments, inventory, vendors, purchase_orders, view invoice_balance). Returns JSON columns+rows, max 100 rows.

    Args:
        sql: A single SQLite SELECT/WITH statement. Cannot reference CRM tables. May contain {{rN.column}} to insert all values of a column from an earlier result, e.g. WHERE id IN ({{r2.erp_customer_id}}).
    """
    return remember(*run_sql("erp.db", expand_refs(sql), limit=KEEP_ROWS))


@tool(parse_docstring=True)
def query_crm(sql: str) -> str:
    """Run ONE read-only SQLite query on the CRM database (accounts, contacts, activities, opportunities, nps_responses). Link to ERP only via accounts.erp_customer_id. Returns JSON columns+rows, max 100 rows.

    Args:
        sql: A single SQLite SELECT/WITH statement. Cannot reference ERP tables. May contain {{rN.column}} to insert all values of a column from an earlier result, e.g. WHERE erp_customer_id IN ({{r1.id}}).
    """
    return remember(*run_sql("crm.db", expand_refs(sql), limit=KEEP_ROWS))


@tool(parse_docstring=True)
def find_customers_by_criteria(min_invoices_in_window: int = 0, window_days: int = 180, min_outstanding: float = 0,
                               min_overdue: float = 0, min_lifetime_spend: float = 0,
                               no_purchase_for_days: int | None = None, name_contains: str | None = None,
                               segment: str | None = None, customer_ids: list[str] | None = None) -> str:
    """ERP customer lookup with per-customer metrics: invoice counts, lifetime spend, outstanding and overdue balance, first and last invoice dates. All filters are optional and combined with AND. Amounts in INR.

    Args:
        min_invoices_in_window: Minimum number of invoices dated within the last window_days.
        window_days: Look-back window for min_invoices_in_window.
        min_outstanding: Minimum unpaid balance across all invoices.
        min_overdue: Minimum unpaid balance on invoices past their due date.
        min_lifetime_spend: Minimum total invoiced amount ever.
        no_purchase_for_days: Only customers whose last invoice is older than this many days.
        name_contains: Case-insensitive substring of the ERP customer name.
        segment: 'SME', 'Mid-Market' or 'Enterprise'.
        customer_ids: Restrict to these customer IDs.
    """
    where, params = [], {"window": f"-{window_days} days", "min_inv": min_invoices_in_window,
                         "min_out": min_outstanding, "min_overdue": min_overdue, "min_spend": min_lifetime_spend}
    if no_purchase_for_days is not None:
        where.append("last_invoice < date((SELECT as_of FROM meta), :dormant)")
        params["dormant"] = f"-{no_purchase_for_days} days"
    if name_contains:
        where.append("name LIKE :name")
        params["name"] = f"%{name_contains}%"
    if segment:
        where.append("segment = :segment")
        params["segment"] = segment
    if customer_ids:
        where.append(f"id IN ({','.join(f':id{i}' for i in range(len(customer_ids)))})")
        params.update({f"id{i}": c for i, c in enumerate(customer_ids)})
    sql = f"""
        WITH m AS (
          SELECT c.id, c.name, c.city, c.segment,
                 SUM(b.invoice_date >= date((SELECT as_of FROM meta), :window)) AS invoices_in_window,
                 COUNT(*) AS invoices_total,
                 ROUND(SUM(b.total_amount), 2) AS lifetime_spend,
                 ROUND(SUM(b.balance), 2) AS outstanding,
                 ROUND(SUM(CASE WHEN b.days_past_due > 0 THEN b.balance ELSE 0 END), 2) AS overdue,
                 MIN(b.invoice_date) AS first_invoice, MAX(b.invoice_date) AS last_invoice
          FROM customers c JOIN invoice_balance b ON b.customer_id = c.id
          GROUP BY c.id)
        SELECT * FROM m
        WHERE invoices_in_window >= :min_inv AND outstanding >= :min_out AND overdue >= :min_overdue
          AND lifetime_spend >= :min_spend {''.join(' AND ' + w for w in where)}
        ORDER BY lifetime_spend DESC"""
    return remember(*run_sql("erp.db", sql, params, limit=KEEP_ROWS))


@tool(parse_docstring=True)
def find_invoices(customer_ids: list[str] | None = None, status: list[str] | None = None, overdue_only: bool = False,
                  date_from: str | None = None, date_to: str | None = None, min_amount: float = 0,
                  with_line_items: bool = False) -> str:
    """ERP sales-invoice lookup with amount paid, balance, days past due and reason_code. Optionally one row per line item with SKU and product name. Newest first.

    Args:
        customer_ids: Only invoices of these customers.
        status: Any of 'paid', 'partial', 'unpaid'.
        overdue_only: Only invoices with a balance that are past their due date.
        date_from: Earliest invoice_date, 'YYYY-MM-DD'.
        date_to: Latest invoice_date, 'YYYY-MM-DD'.
        min_amount: Minimum invoice total in INR.
        with_line_items: Return one row per line item (sku, product, quantity, line_amount).
    """
    where, params = ["b.total_amount >= :min_amount"], {"min_amount": min_amount}
    if customer_ids:
        where.append(f"b.customer_id IN ({','.join(f':c{i}' for i in range(len(customer_ids)))})")
        params.update({f"c{i}": c for i, c in enumerate(customer_ids)})
    if status:
        where.append(f"b.status IN ({','.join(f':s{i}' for i in range(len(status)))})")
        params.update({f"s{i}": s for i, s in enumerate(status)})
    if overdue_only:
        where.append("b.balance > 0 AND b.days_past_due > 0")
    if date_from:
        where.append("b.invoice_date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        where.append("b.invoice_date <= :date_to")
        params["date_to"] = date_to
    lines = (", li.sku, inv.name AS product, li.quantity, li.amount AS line_amount",
             " JOIN line_items li ON li.invoice_id = b.invoice_id JOIN inventory inv ON inv.sku = li.sku")
    sql = (f"SELECT b.*{lines[0] if with_line_items else ''} FROM invoice_balance b{lines[1] if with_line_items else ''}"
           f" WHERE {' AND '.join(where)} ORDER BY b.invoice_date DESC")
    return remember(*run_sql("erp.db", sql, params, limit=KEEP_ROWS))


@tool(parse_docstring=True)
def analyze_trends(database: Literal["erp", "crm"], sql: str, recent_periods: int = 3) -> str:
    """Trend analysis over time periods; use it for any 'improved / declined / grew / trend' question. Runs `sql`, which must return exactly 3 columns: entity, period, value. Per entity: slope per period, average of the most recent periods vs the earlier ones, and the change. Sorted by change, most negative first.

    Args:
        database: Which database to run the SQL on.
        sql: Query returning (entity, period, value); period must sort chronologically as text, e.g. strftime('%Y-%m', date).
        recent_periods: How many of the latest periods (across all entities) count as recent.
    """
    cols, rows = run_sql(f"{database}.db", expand_refs(sql), limit=100_000)
    if len(cols) != 3:
        raise ValueError(f"sql must return 3 columns (entity, period, value), got {cols}")
    periods = sorted({str(r[1]) for r in rows})
    recent = set(periods[-recent_periods:])
    series = defaultdict(dict)
    for entity, period, value in rows:
        if value is not None:
            series[entity][str(period)] = float(value)
    out = []
    for entity, points in series.items():
        ps = sorted(points)
        now = [points[p] for p in ps if p in recent]
        before = [points[p] for p in ps if p not in recent]
        slope = statistics.linear_regression([periods.index(p) for p in ps], [points[p] for p in ps]).slope \
            if len(ps) > 1 else None
        recent_avg = statistics.fmean(now) if now else None
        prior_avg = statistics.fmean(before) if before else None
        change = recent_avg - prior_avg if now and before else None
        out.append([entity, len(ps), prior_avg, recent_avg, change,
                    change / abs(prior_avg) * 100 if change is not None and prior_avg else None, slope])
    out.sort(key=lambda r: (r[4] is None, r[4]))
    result = json.loads(remember(["entity", "n_periods", "prior_avg", "recent_avg", "change", "pct_change",
                                  "slope_per_period"], out))
    result["recent_periods"] = sorted(recent)
    return json.dumps(result)


TOOLS = [query_erp, query_crm, find_customers_by_criteria, find_invoices, analyze_trends]
