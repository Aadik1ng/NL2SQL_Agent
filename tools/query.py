"""Query tools: one query_<name> tool per configured database, and describe_tables for schemas on demand."""
from langchain_core.tools import StructuredTool, tool

from database.executor import run_sql, table_names
from database.references import KEEP_ROWS, MAX_ROWS, check_not_retyped, expand_refs, remember, to_json


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
