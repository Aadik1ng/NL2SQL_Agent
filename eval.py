"""Run the golden dataset (golden.json) through the agent and score every answer.

    uv run eval.py                  all golden questions
    uv run eval.py G04 G16          by id
    uv run eval.py spec             by tag (spec, cross-db, temporal, data-quality, ...)
    uv run eval.py --freeze         recompute expected answers from reference SQL and save them
    uv run eval.py --judge          also have a Pydantic AI judge check every claim against the tool results
                                    (JUDGE_MODEL picks its model; defaults to MODEL)

Scoring by answer_type:
    ids    the IDs on the answer's "Answer IDs:" line (or anywhere, if the line is missing) must
           include every expected ID, and at least 75% of them must be expected. Empty expected
           set = the agent must cite none.
    value  some number in the answer (₹, lakh/L, crore/Cr understood) within tolerance_pct.
    text   expected text appears in the answer (case-insensitive).
The judge is reported separately as "faithful": correct IDs with a made-up vendor name still fail it.
"""
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from pydantic import BaseModel, Field

DATA = Path(__file__).parent / "data"
GOLDEN = Path(__file__).with_name("golden.json")
ID_PATTERNS = {"customer": r"\bC\d{4}\b", "vendor": r"\bV\d{3}\b", "sku": r"\b[A-Z]{3}-\d{3}\b",
               "invoice": r"\bINV-\d{5}\b"}
UNITS = {"cr": 1e7, "crore": 1e7, "crores": 1e7, "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "k": 1e3}
JUDGE_INSTRUCTIONS = """You audit answers written by a data agent. You get the question, the dataset date, every tool \
call the agent made (with its SQL or arguments) and what it returned, and the final answer. Check each specific claim \
in the answer (IDs, names, amounts, counts, dates, rankings) against that evidence. Supported: values present in the \
results, simple arithmetic or unit conversion of them (rupees to lakhs, sums, date differences, rounding), and \
descriptions of what a shown query computed. Not claims: definitions, assumptions stated as such, recommendations, \
opinions. List every unsupported claim briefly. Be strict about names and numbers; don't nitpick wording."""


class Verdict(BaseModel):
    """Audit of one answer against the evidence the agent saw."""
    notes: str = Field(description="Scratchpad: check the answer's key claims one by one against the evidence first")
    unsupported_claims: list[str] = Field(default_factory=list, description=(
        "ONLY claims that, after checking, are wrong or absent from the evidence, quoted briefly. "
        "Never list a claim that checks out."))

    @property
    def supported(self):
        return not self.unsupported_claims


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


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    golden = load_golden()
    live = reference_answers(golden)
    if "--freeze" in sys.argv:
        for item in golden:
            item["expected"] = live[item["id"]]
        GOLDEN.write_text(json.dumps(golden, indent=2, ensure_ascii=False) + "\n")
        print(f"froze expected answers for {len(golden)} golden items")
        return
    drift = [item["id"] for item in golden if item["expected"] != live[item["id"]]]
    if drift:
        sys.exit(f"Data no longer matches golden answers for {drift}. Reseed with the pinned date "
                 f"(uv run seed.py) or, if the change is intended, uv run eval.py --freeze")

    # imported here so seed.py can use reference_answers() without an API key
    from cli.app import ask
    from config.settings import MODEL, TRACING
    from database.registry import as_of
    from engine.graph import current_graph
    from engine.planner import pydantic_model
    from langchain_core.messages import AIMessage, ToolMessage
    from pydantic_ai import Agent

    judge_model = os.environ.get("JUDGE_MODEL", MODEL)
    judge = Agent(pydantic_model(judge_model), output_type=Verdict, instructions=JUDGE_INSTRUCTIONS, retries=2,
                  model_settings={"temperature": 0, "max_tokens": 2048}, name="judge") if "--judge" in sys.argv else None

    items = [i for i in golden if not args or i["id"] in args or set(args) & set(i["tags"])]
    results = []
    for item in items:
        started = time.monotonic()
        thread = f"eval-{item['id']}-{int(time.time())}"
        try:
            answer = ask(item["question"], thread_id=thread)
        except Exception as e:  # one failed API call (rate limit, credits, outage) shouldn't sink the whole run
            results.append((item, False, f"ERROR {type(e).__name__}: {str(e)[:160]}", time.monotonic() - started, None))
            continue
        ok, detail = score(item, answer)
        scores = {"eval_pass": (ok, f"{item['id']}: {detail}")}
        verdict = None
        if judge:
            messages = current_graph().get_state({"configurable": {"thread_id": thread}}).values["messages"]
            calls = {c["id"]: c["args"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls}
            evidence = "\n\n".join(f"{m.name}({json.dumps(calls.get(m.tool_call_id), ensure_ascii=False)})\n-> {m.content}"
                                    for m in messages if isinstance(m, ToolMessage))
            verdict = judge.run_sync(f"Question: {item['question']}\nDataset date: {as_of()}\n\n"
                                     f"Tool calls and results:\n{evidence}\n\nAnswer:\n{answer}").output
            scores["faithful"] = (verdict.supported, "; ".join(verdict.unsupported_claims) or "all claims supported")
        results.append((item, ok, detail, time.monotonic() - started, verdict))
        if TRACING:  # scores land on the Langfuse session, next to the trace
            from langfuse import get_client
            for name, (value, comment) in scores.items():
                get_client().create_score(name=name, value=float(value), session_id=thread, data_type="BOOLEAN",
                                          comment=comment)
            get_client().flush()

    print("\n" + "=" * 100)
    for item, ok, detail, secs, verdict in results:
        faithful = "" if verdict is None else "  faithful" if verdict.supported else \
            f"  UNSUPPORTED: {'; '.join(verdict.unsupported_claims)}"
        print(f"{'PASS' if ok else 'FAIL'}  {item['id']}  {item['difficulty']:<6}  {secs:5.1f}s  {detail}{faithful}")
    passed = sum(r[1] for r in results)
    summary = f"{passed}/{len(results)} correct"
    if judge:
        summary += f" · {sum(bool(r[4] and r[4].supported) for r in results)}/{len(results)} faithful (judge: {judge_model})"
    print(f"{summary} · {MODEL}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
