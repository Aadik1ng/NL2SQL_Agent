"""The golden dataset: loading golden.json, re-running its reference SQL, and scoring an answer against it.

Kept free of agent imports, because demo_data's self-check uses reference_answers() without an API key.
"""
import json
import re
import sqlite3
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
GOLDEN = Path(__file__).with_name("golden.json")
ID_PATTERNS = {"customer": r"\bC\d{4}\b", "vendor": r"\bV\d{3}\b", "sku": r"\b[A-Z]{3}-\d{3}\b",
               "invoice": r"\bINV-\d{5}\b"}
UNITS = {"cr": 1e7, "crore": 1e7, "crores": 1e7, "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "k": 1e3}


def load_golden():
    return json.loads(GOLDEN.read_text())


def reference_answers(golden=None):
    """Run every item's reference SQL (erp.db with crm.db attached as `crm`)."""
    conn = sqlite3.connect(f"file:{DATA / 'erp.db'}?mode=ro", uri=True)
    conn.execute("ATTACH ? AS crm", (f"file:{DATA / 'crm.db'}?mode=ro",))
    as_of = conn.execute("SELECT as_of FROM meta").fetchone()[0]
    answers = {}
    for item in golden or load_golden():
        rows = conn.execute("\n".join(item["reference_sql"]), {"as_of": as_of}).fetchall()
        kind = item["answer_type"]
        answers[item["id"]] = (sorted({r[0] for r in rows}) if kind == "ids" else
                               round(rows[0][0], 2) if kind == "value" else rows[0][0])
    conn.close()
    return answers


def numbers(text):
    for num, unit in re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*(crores?|cr|lakhs?|l|k)?\b", text, re.I):
        yield float(num.replace(",", "")) * UNITS.get(unit.lower(), 1)


def score(item, answer):
    """(passed, detail) for one answer."""
    expected = item["expected"]
    if item["answer_type"] == "value":
        tol = item.get("tolerance_pct", 1) / 100
        ok = any(abs(x - expected) <= tol * abs(expected) for x in numbers(answer))
        return ok, f"expected ≈ {expected:,.0f}"
    if item["answer_type"] == "text":
        return expected.lower() in answer.lower(), f"expected '{expected}'"
    line = re.search(r"Answer IDs:(.*)", answer, re.I)
    cited = set(re.findall(ID_PATTERNS[item["id_type"]], line.group(1) if line else answer))
    want = set(expected)
    if not want:
        return not cited, "correctly empty" if not cited else f"expected none, cited {sorted(cited)}"
    hits = cited & want
    recall, precision = len(hits) / len(want), len(hits) / len(cited) if cited else 0.0
    detail = f"recall {recall:.0%} precision {precision:.0%}"
    if want - cited:
        detail += f" missed {sorted(want - cited)}"
    if cited - want:
        detail += f" extra {sorted(cited - want)}"
    return recall == 1 and precision >= 0.75, detail
