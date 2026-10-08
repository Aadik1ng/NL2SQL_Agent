"""The reasoning loop as a LangGraph graph: plan -> agent <-> tools. Rebuilt only when the database registry changes."""
import sys
from functools import cache

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic_ai import Agent

from config.settings import LLM_API_KEY, LLM_BASE_URL, MODEL, TRACING, reachable
from database.registry import REGISTRY, as_of, load_registry
from engine.planner import build_planner
from engine.prompts import AGENT_INSTRUCTIONS, OUT_OF_BUDGET, PLAN_INSTRUCTIONS, SYSTEM, database_overview
from tools import tools_for

MAX_TOOL_ROUNDS = 12  # after this the agent must answer with what it has


class State(MessagesState):
    plan: str


def cached_system(text):
    """System prompt marked for prompt caching: later calls re-read it at ~10% of the input price. Pays off on Claude
    Sonnet; it's under Haiku 4.5's 4,096-token caching minimum, and GPT caches automatically. (Via OpenRouter the
    marker only survives on system/user messages, not tool results, so the growing history can't be cached this way.)"""
    return SystemMessage(content=[{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}])


SAVER = InMemorySaver()  # shared across rebuilds, so a conversation survives a databases.toml change


def current_graph():
    """The graph for the databases configured right now; rebuilt only when databases.toml changes."""
    return build_graph(REGISTRY.read_text())


@cache
def build_graph(registry_text):
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
    registry = load_registry()
    tools = tools_for(registry)
    context = registry.get("context", "").strip()
    system = SYSTEM.format(context=f"\n{context}\n" if context else "", today=as_of(), databases=database_overview())
    planner = build_planner(system + PLAN_INSTRUCTIONS)
    worker = llm.bind_tools(tools)
    closer = llm.bind_tools(tools, tool_choice="none")  # tools stay declared because history contains tool calls

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
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))  # errors go back to the model as messages
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=SAVER)
