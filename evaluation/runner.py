"""The eval command: run golden questions through the agent, score them, optionally judge them, report."""
import json
import os
import sys
import time

from evaluation.golden import GOLDEN, load_golden, reference_answers, score


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

    # imported here so --freeze runs without loading the agent (settings, API key, Langfuse check)
    from cli.app import ask
    from config.settings import MODEL, TRACING
    from engine.graph import current_graph
    from evaluation.judge import build_judge, judge_answer

    judge_model = os.environ.get("JUDGE_MODEL", MODEL)
    judge = build_judge(judge_model) if "--judge" in sys.argv else None

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
            verdict = judge_answer(judge, item["question"], messages, answer)
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
