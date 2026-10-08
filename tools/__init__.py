"""Tools the agent can call.

Databases come from config/databases.toml, re-read before every question: each one gets a
query_<name> tool, and describe_tables shows its schema on demand.

Add a tool: write a function with type hints and a Google-style docstring in a module here, decorate it with
@tool(parse_docstring=True), and add it to the list in tools_for() below. The docstring is what the model reads to
decide when to call it.
"""
from tools.erp import find_customers_by_criteria, find_invoices
from tools.query import describe_tables, query_tool
from tools.trends import analyze_trends


def tools_for(registry):
    """Tools for the configured databases. The two ERP helpers are written for this dataset's ERP schema, so they're
    offered only when an `erp` database is configured."""
    tools = [query_tool(name, cfg.get("description", "")) for name, cfg in registry["databases"].items()]
    tools += [describe_tables, analyze_trends]
    if "erp" in registry["databases"]:
        tools += [find_customers_by_criteria, find_invoices]
    return tools
