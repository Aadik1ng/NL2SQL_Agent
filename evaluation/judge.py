"""The faithfulness judge: a Pydantic AI agent that checks every claim in an answer against the tool results."""
import json

from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from database.registry import as_of
from engine.planner import pydantic_model

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


def build_judge(judge_model):
    return Agent(pydantic_model(judge_model), output_type=Verdict, instructions=JUDGE_INSTRUCTIONS, retries=2,
                 model_settings={"temperature": 0, "max_tokens": 2048}, name="judge")


def judge_answer(judge, question, messages, answer):
    """Verdict on one answer, given the run's messages (every tool call with its arguments and result)."""
    calls = {c["id"]: c["args"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls}
    evidence = "\n\n".join(f"{m.name}({json.dumps(calls.get(m.tool_call_id), ensure_ascii=False)})\n-> {m.content}"
                            for m in messages if isinstance(m, ToolMessage))
    return judge.run_sync(f"Question: {question}\nDataset date: {as_of()}\n\n"
                          f"Tool calls and results:\n{evidence}\n\nAnswer:\n{answer}").output
