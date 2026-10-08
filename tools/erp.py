"""Helpers written for this dataset's ERP schema. tools_for() offers them only when an `erp` database is configured."""
from langchain_core.tools import tool

from database.executor import run_sql
from database.references import KEEP_ROWS, remember


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
