"""Agentic SQL engine: a LangGraph plan -> ReAct loop over separate ERP and CRM databases.

    uv run agent.py "your question"     answer one question
    uv run agent.py                     interactive; follow-ups keep context, /new resets

LLM calls go to one OpenAI-compatible endpoint: the LiteLLM proxy (LLM_BASE_URL, LLM_API_KEY, MODEL = proxy alias,
see litellm/config.yaml) or, without it, OpenRouter directly (OPENROUTER_API_KEY). Settings come from the
environment or .env. Set LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY to trace every run in Langfuse.

LangGraph runs the loop; Pydantic AI produces the typed plan.
"""
import json
import os
import sys
import time
import urllib.request
import uuid
from functools import cache
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from tools import TOOLS, as_of, schema_text

env_file = Path(__file__).with_name(".env")
if env_file.exists():
    for line in env_file.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
MODEL = os.environ.get("MODEL", "anthropic/claude-haiku-4.5")


def reachable(url):
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except Exception:
        return False


LANGFUSE_KEYS = bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))
# Tracing is on when keys are set and Langfuse answers; if it's down the agent just runs untraced
TRACING = LANGFUSE_KEYS and reachable(os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com") +
                                      "/api/public/health")
MAX_TOOL_ROUNDS = 12  # after this the agent must answer with what it has
console = Console()

SYSTEM = """You are a data analyst agent for an Indian B2B industrial distributor. You answer business questions by \
querying two separate SQLite databases with tools and reasoning over the results.

Today is {today} (the dataset date). Use this literal date in SQL, never date('now').
Money is INR. 1 lakh (L) = 100,000; 1 crore (Cr) = 10,000,000.

ERP and CRM are SEPARATE databases: one query cannot join across them. To combine them, query one side, take the IDs \
from the result and pass them into the next query. Every tool result has a result_ref (r1, r2, ...): write \
{{{{r3.erp_customer_id}}}} inside IN (...) and the tool inserts ALL values of that column from result r3 (not just the 25 \
rows you were shown), e.g. WHERE customer_id IN ({{{{r3.erp_customer_id}}}}). Never retype IDs from a result. "Not in the \
other database" questions (anti-joins) work the same way: fetch the other side's IDs, then NOT IN ({{{{rN.column}}}}). Customer names \
are not unique and are spelled differently in CRM, so always link by ID (customers.id = accounts.erp_customer_id).

{schema}"""

PLAN_INSTRUCTIONS = """

Before touching any data, write a plan for the user's latest question. Break it into sub-problems, decide which \
database or tool each needs, and order the steps (later steps usually need IDs from earlier ones). If a term is vague \
("repeat", "improved", "small", "top"), choose a concrete definition and say so."""

AGENT_INSTRUCTIONS = """

Your plan for the latest question:
{plan}

Work through the plan with tools.
- Before every tool call write one or two short lines: what the last result told you, and why you are making this call.
- Adapt: if a result is empty, surprising, or shows you need more data, change course and say why.
- If a query errors, read the error, fix the SQL against the schema and retry. Never repeat a failing query unchanged.
- Prefer one well-aggregated query over many small ones; you see at most 25 rows per result.
- Do ALL arithmetic in SQL, never in your head: date math with date()/julianday() against today's date (e.g. \
date(last_touch, '+30 days') AS due_on, julianday('{today}') - julianday(x) AS days_ago), money as \
ROUND(amount / 100000.0, 2) AS amount_lakhs, percentages, differences. In the answer, copy numbers from results.
- Every total, count or share you state must come from a query result. Need a total or "how many have X" you \
haven't computed? Run the SUM/COUNT; never add up rows yourself. Units: 100 lakhs = 1 crore; a column ending in \
_lakhs is lakhs; never relabel lakhs as crores.
- Sanity-check each result before building on it: does the row count make sense? Missing data is not zero: never \
COALESCE a missing average or count to 0 inside a comparison; drop entities without data on both sides.
- Before/after comparisons: compute both sides per entity in ONE GROUP BY with conditional aggregation \
(AVG(CASE WHEN ... THEN x END)), or use analyze_trends. Use a baseline long enough to hold several data points per \
entity and report the counts.
- "Improved", "grew", "declined", "dropped" mean a material change: state a threshold, require enough data on both \
sides, and rank by size. Tiny moves are noise, not findings.
- Time words carry meaning: "started with" = earliest by date, "grew to" = later by date, "last N months" = a \
date filter. Don't swap them for MIN/MAX of amounts.
- Use the helper tools (find_customers_by_criteria, find_invoices, analyze_trends) when they fit, raw SQL otherwise.

When you have enough evidence, reply WITHOUT tool calls with the final answer:
- Keep it under about 150 words plus one compact table. No preamble ("Perfect", "Now I have"): start with a \
one-line direct answer.
- Cite IDs next to names, e.g. "Sharma Traders (C0042)", vendors like V007, invoices like INV-00123.
- Never state a name, number or date that isn't in a tool result. Need a name? Query it, or show the ID alone.
- Show the key numbers (₹ in lakhs) and the definitions/assumptions you used.
- End with one "Why it matters" line and one or two suggested actions.
- Very last line: "Answer IDs: C0012, C0044" listing only the IDs that ARE the answer (customers, vendors, SKUs or \
invoices) after applying the threshold you stated, not ones you excluded or mention for contrast. "Answer IDs: none" \
if nothing matches or the answer is a single number or name."""

OUT_OF_BUDGET = "\n\nTool budget used up. Give your best final answer now from the evidence so far and say what is missing."


class Plan(BaseModel):
    """Plan for answering the question, written before running any query."""
    thought: str = Field(description="What is really being asked, how vague terms are interpreted (concrete "
                                     "thresholds), and what could go wrong")
    sub_problems: list[str] = Field(default_factory=list,
                                    description="The question broken into small, independently answerable parts")
    steps: list[str] = Field(description="Ordered steps; each names the tool/database, what it fetches or computes, "
                                         "and which earlier result it depends on")


class State(MessagesState):
    plan: str


def cached_system(text):
    """System prompt marked for prompt caching: later calls re-read it at ~10% of the input price. Pays off on Claude
    Sonnet; it's under Haiku 4.5's 4,096-token caching minimum, and GPT caches automatically. (Via OpenRouter the
    marker only survives on system/user messages, not tool results, so the growing history can't be cached this way.)"""
    return SystemMessage(content=[{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}])


def pydantic_model(name=None):
    """A Pydantic AI model on the same gateway as the LangGraph agent."""
    return OpenAIChatModel(name or MODEL, provider=LiteLLMProvider(api_base=LLM_BASE_URL, api_key=LLM_API_KEY))


@cache
def build_graph():
    if not LLM_API_KEY:
        sys.exit("Set LLM_API_KEY (LiteLLM proxy) or OPENROUTER_API_KEY in your environment or in .env")
    if "localhost" in LLM_BASE_URL and not reachable(LLM_BASE_URL + "/health/liveliness"):
        sys.exit("The LiteLLM gateway isn't running. Start Docker Desktop; if that's not enough, run "
                 "`docker compose up -d` once in this folder.")
    if TRACING:
        from langfuse import get_client
        get_client()  # sets up the OpenTelemetry exporter that Pydantic AI's spans go to
        Agent.instrument_all()
    # max_tokens is generous because reasoning models (GPT-5) count their hidden reasoning against it
    llm = ChatOpenAI(model=MODEL, base_url=LLM_BASE_URL, api_key=LLM_API_KEY, temperature=0, max_tokens=16384)
    system = SYSTEM.format(today=as_of(), schema=schema_text())
    # Pydantic AI validates the plan and, if a field is missing or malformed, sends the error back for a retry
    planner = Agent(pydantic_model(), output_type=Plan, instructions=system + PLAN_INSTRUCTIONS, retries=2,
                    model_settings={"temperature": 0, "max_tokens": 16384}, name="planner")
    worker = llm.bind_tools(TOOLS)
    closer = llm.bind_tools(TOOLS, tool_choice="none")  # tools stay declared because history contains tool calls

    def plan(state):
        conversation = "\n\n".join(
            f"User: {m.content}" if isinstance(m, HumanMessage) else f"Assistant: {m.content}"
            for m in state["messages"]
            if isinstance(m, HumanMessage) or (isinstance(m, AIMessage) and m.content and not m.tool_calls))
        try:
            p = planner.run_sync(conversation).output
        except Exception as e:  # a plan that still fails validation shouldn't kill the run
            return {"plan": f"(planner failed: {type(e).__name__}; decompose the question yourself as you go)"}
        text = (f"Thought: {p.thought}\n\nSub-problems:\n" + "\n".join(f"- {s}" for s in p.sub_problems)
                + "\n\nPlan:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(p.steps, 1)))
        return {"plan": text}

    def agent(state):
        msgs = state["messages"]
        last_question = max(i for i, m in enumerate(msgs) if isinstance(m, HumanMessage))
        rounds = sum(1 for m in msgs[last_question:] if isinstance(m, AIMessage) and m.tool_calls)
        prompt = system + AGENT_INSTRUCTIONS.format(plan=state["plan"], today=as_of())
        model = worker if rounds < MAX_TOOL_ROUNDS else closer
        if model is closer:
            prompt += OUT_OF_BUDGET
        return {"messages": [model.invoke([cached_system(prompt), *msgs])]}

    graph = StateGraph(State)
    graph.add_node("plan", plan)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(TOOLS, handle_tool_errors=True))  # errors go back to the model as messages
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=InMemorySaver())


def show_observation(msg):
    if msg.status == "error":
        console.print(f"[bold red]Observation (error):[/] {msg.content}")
        return
    data = json.loads(msg.content)
    note = f" [yellow]({data['note']})[/]" if "note" in data else ""
    ref = f" {data['result_ref']}" if "result_ref" in data else ""
    console.print(f"[bold blue]Observation{ref}:[/] {data['row_count']} row(s){note}")
    if not data["rows"]:
        return
    table = Table(*data["columns"], show_lines=False, header_style="dim", box=None, padding=(0, 1))
    for row in data["rows"][:6]:
        table.add_row(*("" if v is None else str(v) for v in row))
    console.print(table)
    if data["row_count"] > 6:
        console.print(f"[dim]  … {data['row_count'] - 6} more[/]")


def ask(question, thread_id="cli"):
    """Run one question, streaming the agent's reasoning to the terminal. Returns the final answer text."""
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 4 * MAX_TOOL_ROUNDS,
              "run_name": "agentic-sql", "metadata": {"langfuse_session_id": thread_id, "langfuse_tags": [MODEL]}}
    if TRACING:
        from langfuse.langchain import CallbackHandler
        tracer = CallbackHandler()
        config["callbacks"] = [tracer]
    console.rule(f"[bold]{question}")
    started, step, answer = time.monotonic(), 0, ""
    for update in graph.stream({"messages": [HumanMessage(question)]}, config, stream_mode="updates"):
        for node, data in update.items():
            if node == "plan":
                console.print(Panel(data["plan"], title="Agent Plan", border_style="magenta"))
            elif node == "agent":
                msg = data["messages"][-1]
                if not msg.tool_calls:
                    answer = msg.content
                    console.print(Panel(Markdown(answer), title="Answer", border_style="green"))
                    continue
                if msg.content:
                    console.print(f"[bold cyan]Agent Thought:[/] {msg.content}")
                for call in msg.tool_calls:
                    step += 1
                    console.print(f"[bold yellow]Agent Executed (step {step}): {call['name']}[/]")
                    args = dict(call["args"])
                    if "sql" in args:
                        console.print(Syntax(args.pop("sql"), "sql", theme="ansi_dark", word_wrap=True))
                    if args:
                        console.print(f"[dim]{json.dumps(args, ensure_ascii=False)}[/]")
            elif node == "tools":
                for msg in data["messages"]:
                    show_observation(msg)
    console.print(f"[dim]{step} tool call(s) · {time.monotonic() - started:.1f}s · {MODEL}[/]")
    if TRACING:
        from langfuse import get_client
        get_client().flush()
        console.print(f"[dim]trace: {get_client().get_trace_url(trace_id=tracer.last_trace_id)}[/]")
    return answer


def main():
    if len(sys.argv) > 1:
        ask(" ".join(sys.argv[1:]))
        return
    thread = str(uuid.uuid4())
    if LANGFUSE_KEYS and not TRACING:
        console.print("[dim]Langfuse isn't reachable, so tracing is off for this session.[/]")
    console.print(f"[bold]Agentic SQL engine[/] · data as of {as_of()} · {MODEL}\n"
                  "Ask a question. /new starts a fresh conversation, exit quits.")
    while True:
        try:
            question = console.input("\n[bold green]ask>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question in ("exit", "quit"):
            break
        if question == "/new":
            thread = str(uuid.uuid4())
        elif question:
            try:
                ask(question, thread)
            except Exception as e:  # e.g. provider rate limit or outage: report it and keep the session alive
                console.print(f"[bold red]Request failed:[/] {type(e).__name__}: {str(e)[:300]}")


if __name__ == "__main__":
    main()
