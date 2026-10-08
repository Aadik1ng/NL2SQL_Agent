"""Query results the model can build on. Each result is stored as r1, r2, ... and later SQL can use it through
{{rN.col}} (one column's values) or {{rN}} (the whole result as a table), across databases. Also formats results for
the model and rejects SQL that carries an earlier result as hand-typed literals.
"""
import itertools
import json
import re

MAX_ROWS = 25  # rows shown to the model; {{rN.col}} references still carry every row
KEEP_ROWS = 10_000  # rows kept per result for {{rN.column}} references
RESULTS = {}  # ponytail: every result kept for the process lifetime; fine for a CLI, add eviction for a server
_refs = itertools.count(1)
REF = re.compile(r"\{\{(r\d+)(?:\.(\w+))?\}\}")  # {{r3.col}} = a column's values, {{r3}} = the whole result


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


def check_not_retyped(sql):
    """Reject queries that carry an earlier result as hand-typed literals: models drop or invent rows when copying."""
    if len(re.findall(r"'[^']*'", sql)) > 25 and not REF.search(sql):
        raise ValueError("This query retypes many values by hand. Use {{rN.column}} for a list of values or {{rN}} for "
                         "a whole earlier result as a table; copied data gets rows dropped or invented.")
