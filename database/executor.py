"""Runs SQL against a configured database: read-only, one statement, ATTACH blocked, time-limited."""
import sqlite3
import time
from contextlib import closing

from database.references import MAX_ROWS
from database.registry import db_path

TIMEOUT_S = 10


def _deny_attach(action, *_):
    # read-only already blocks writes; this stops ATTACH of some other (writable) file
    return sqlite3.SQLITE_DENY if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH) else sqlite3.SQLITE_OK


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


def table_names(database):
    return [r[0] for r in run_sql(database, "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                                            "AND name NOT LIKE 'sqlite_%' ORDER BY name", limit=10_000)[1]]
