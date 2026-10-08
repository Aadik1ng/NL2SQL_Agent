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
from evaluation.runner import main

if __name__ == "__main__":
    main()
