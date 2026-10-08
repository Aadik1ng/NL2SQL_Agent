"""analyze_trends: per-entity slope and recent-vs-earlier comparison, for "improved / declined / grew" questions."""
import json
import statistics
from collections import defaultdict

from langchain_core.tools import tool

from database.executor import run_sql
from database.references import expand_refs, remember


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
