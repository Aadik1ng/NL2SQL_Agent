"""Running questions: ask() streams one question through the graph, main() is the interactive session."""
import sys
import time
import uuid

from langchain_core.messages import HumanMessage

from cli.render import console, show_answer, show_call, show_observation, show_plan, show_thought
from config.settings import LANGFUSE_KEYS, MODEL, TRACING
from database.registry import as_of, load_registry
from engine.graph import MAX_TOOL_ROUNDS, current_graph


def ask(question, thread_id="cli"):
    """Run one question, streaming the agent's reasoning to the terminal. Returns the final answer text."""
    graph = current_graph()
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
                show_plan(data["plan"])
            elif node == "agent":
                msg = data["messages"][-1]
                if not msg.tool_calls:
                    answer = msg.content
                    show_answer(answer)
                    continue
                if msg.content:
                    show_thought(msg.content)
                for call in msg.tool_calls:
                    step += 1
                    show_call(step, call)
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
    console.print(f"[bold]Agentic SQL engine[/] · databases: {', '.join(load_registry()['databases'])} · "
                  f"as of {as_of()} · {MODEL}\n"
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
