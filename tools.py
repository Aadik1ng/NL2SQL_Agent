"""Tools the agent can call.

Databases come from databases.toml (see load_registry), re-read before every question: each one gets a
query_<name> tool, and describe_tables shows its schema on demand.

Add a tool: write a function with type hints and a Google-style docstring, decorate it with
@tool(parse_docstring=True), and add it to the list in tools_for(). The docstring is what the model reads to decide
when to call it.
"""
import itertools
import json
import os
import re
import sqlite3
import statistics
import time
import tomllib
from collections import defaultdict
from contextlib import closing
from datetime import date
from pathlib import Path

from langchain_core.tools import StructuredTool, tool

ROOT = Path(__file__).parent
REGISTRY = Path(os.environ.get("DATABASES", ROOT / "databases.toml"))
MAX_ROWS = 25  # rows shown to the model; {{rN.col}} references still carry every row
KEEP_ROWS = 10_000  # rows kept per result for {{rN.column}} references
TIMEOUT_S = 10
RESULTS = {}  # ponytail: every result kept for the process lifetime; fine for a CLI, add eviction for a server
_refs = itertools.count(1)
REF = re.compile(r"\{\{(r\d+)(?:\.(\w+))?\}\}")  # {{r3.col}} = a column's values, {{r3}} = the whole result


def _deny_attach(action, *_):
    # read-only already blocks writes; this stops ATTACH of some other (writable) file
    return sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH) else sqlite3.SQLITE_OK


def load_registry():
    """The configured databases, context and as-of date, read fresh from databases.toml."""
    return tomllib.loads(REGISTRY.read_text())


def db_path(database):
    databases = load_registry()["databases"]
    if database not in databases:
        raise ValueError(f"unknown database {database!r}; configured: {', '.join(databases)}")
    return (REGISTRY.parent / databases[database]["path"]).resolve()


def run_sql(database, sql, params=(), limit=MAX_ROWS):
    """Execute one statement read-only on a configured database. Returns (columns, rows), up to limit+1 rows."""
    with closing(sqlite3.connect(f"file:{db_path(database)}?mode=ro", uri=True)) as conn:
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
        out["note"] = (f"showing {MAX_ROWS} of {total} rows; aggregate in SQL, or use all rows in a later query: "
                       f"{{{{{ref}.<column>}}}} for a list, {{{{{ref}}}}} as a table")
    return json.dumps(out, default=str, ensure_ascii=False)


def remember(cols, rows):
    """Store a result under a new ref (r1, r2, ...) and return it formatted for the model."""
    ref = f"r{next(_refs)}"
    RESULTS[ref] = (cols, rows[:KEEP_ROWS])
    return to_json(cols, rows, ref)


def _quote(v):
    if v is None:
        return "NULL"
    return str(v) if isinstance(v, (int, float)) else "'" + str(v).replace("'", "''") + "'"


def _as_table(cols, rows):
    """A stored result as an inline table with its own column names, usable in FROM/JOIN of any database."""
    if not rows:
        return "(SELECT " + ", ".join(f'NULL AS "{c}"' for c in cols) + " WHERE 0)"
    names = ", ".join(f'column{i + 1} AS "{c}"' for i, c in enumerate(cols))
    tuples = ", ".join("(" + ", ".join(_quote(v) for v in r) + ")" for r in rows)
    return f"(SELECT {names} FROM (VALUES {tuples}))"


def expand_refs(sql):
    """{{r3.col}} -> the distinct quoted values of `col` in stored result r3, for IN (...).
    {{r3}} -> the whole of r3 as a table, for joining or grouping by columns that live in another database.
    Both use every stored row, not just the ones the model was shown."""
    def values(m):
        ref, col = m.groups()
        if ref not in RESULTS:
            raise ValueError(f"unknown result {ref}; available: {', '.join(RESULTS) or 'none'}")
        cols, rows = RESULTS[ref]
        if col is None:
            return _as_table(cols, rows)
        if col not in cols:
            raise ValueError(f"{ref} has no column {col!r}; its columns are {cols}")
        i = cols.index(col)
        return ", ".join(dict.fromkeys(_quote(r[i]) for r in rows if r[i] is not None))
    return REF.sub(values, sql)


def as_of():
    return load_registry().get("as_of") or date.today().isoformat()


def table_names(database):
    return [r[0] for r in run_sql(database, "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                                            "AND name NOT LIKE 'sqlite_%' ORDER BY name", limit=10_000)[1]]


def database_overview():
    """A few lines per configured database for the prompt: tool, description and tables (no columns). Tables get the
    optional one-line descriptions from databases.toml, for ones whose purpose isn't obvious from the name."""
    lines = []
    for name, cfg in load_registry()["databases"].items():
        notes = cfg.get("tables", {})
        tables = ", ".join(f"{t} ({notes[t]})" if t in notes else t for t in table_names(name))
        lines.append(f"- {name} (tool query_{name}): {cfg.get('description', '')}. Tables: {tables}")
    return "\n".join(lines)


def check_not_retyped(sql):
    """Reject queries that carry an earlier result as hand-typed literals: models drop or invent rows when copying."""
    if len(re.findall(r"'[^']*'", sql)) > 25 and not REF.search(sql):
        raise ValueError("This query retypes many values by hand. Use {{rN.column}} for a list of values or {{rN}} for "
                         "a whole earlier result as a table; copied data gets rows dropped or invented.")


def query_tool(name, description):
    def run(sql: str) -> str:
        check_not_retyped(sql)
        return remember(*run_sql(name, expand_refs(sql), limit=KEEP_ROWS))
    return StructuredTool.from_function(
        run, name=f"query_{name}",
        description=f"Run ONE read-only SQLite statement (SELECT/WITH) on the {name} database: {description}. It can't "
                    f"reference other databases' tables, but it can use earlier results from any database: "
                    f"{{{{rN.column}}}} inserts every value of a column (WHERE id IN ({{{{r2.customer_id}}}})) and "
                    f"{{{{rN}}}} inserts a whole result as a table (JOIN {{{{r2}}}} AS m ON m.customer_id = t.id). "
                    f"Returns JSON columns + rows; you see {MAX_ROWS} rows.")


@tool(parse_docstring=True)
def describe_tables(database: str, tables: list[str]) -> str:
    """Show the schema of some tables before querying them: columns, the comments that explain them (units, allowed values, which columns link to other databases) and 3 sample rows each.

    Args:
        database: A configured database name, e.g. erp.
        tables: Table or view names in that database.
    """
    known = table_names(database)
    out = []
    for t in tables:
        if t not in known:
            out.append(f"-- {t}: no such table in {database}; tables are {', '.join(known)}")
            continue
        ddl = run_sql(database, "SELECT sql FROM sqlite_master WHERE name = ?", (t,))[1][0][0]
        cols, rows = run_sql(database, f'SELECT * FROM "{t}" LIMIT 3')
        out.append(f"{ddl};\n-- sample rows: {to_json(cols, rows)}")
    return "\n\n".join(out)


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
    return remember(*run_sql("erp", sql, params, limit=KEEP_ROWS))


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
    return remember(*run_sql("erp", sql, params, limit=KEEP_ROWS))


@tool(parse_docstring=True)
def analyze_trends(database: str, sql: str, recent_periods: int = 3) -> str:
    """Trend analysis over time periods; use it for any 'improved / declined / grew / trend' question. Runs `sql`, which must return exactly 3 columns: entity, period, value. Per entity: slope per period, average of the most recent periods vs the earlier ones, and the change. Sorted by change, most negative first.

    Args:
        database: A configured database name to run the SQL on.
        sql: Query returning (entity, period, value); period must sort chronologically as text, e.g. strftime('%Y-%m', date).
        recent_periods: How many of the latest periods (across all entities) count as recent.
    """
    cols, rows = run_sql(database, expand_refs(sql), limit=100_000)
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


def tools_for(registry):
    """Tools for the configured databases. The two ERP helpers are written for this dataset's ERP schema, so they're
    offered only when an `erp` database is configured."""
    tools = [query_tool(name, cfg.get("description", "")) for name, cfg in registry["databases"].items()]
    tools += [describe_tables, analyze_trends]
    if "erp" in registry["databases"]:
        tools += [find_customers_by_criteria, find_invoices]
    return tools
