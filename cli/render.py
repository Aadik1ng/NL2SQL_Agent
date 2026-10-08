"""What the terminal shows while the agent works: plan, thoughts, executed tools with their SQL, observations, answer."""
import json

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()


def show_plan(plan):
    console.print(Panel(plan, title="Agent Plan", border_style="magenta"))


def show_answer(answer):
    console.print(Panel(Markdown(answer), title="Answer", border_style="green"))


def show_thought(text):
    console.print(f"[bold cyan]Agent Thought:[/] {text}")


def show_call(step, call):
    console.print(f"[bold yellow]Agent Executed (step {step}): {call['name']}[/]")
    args = dict(call["args"])
    if "sql" in args:
        console.print(Syntax(args.pop("sql"), "sql", theme="ansi_dark", word_wrap=True))
    if args:
        console.print(f"[dim]{json.dumps(args, ensure_ascii=False)}[/]")


def show_observation(msg):
    if msg.status == "error":
        console.print(f"[bold red]Observation (error):[/] {msg.content}")
        return
    try:
        data = json.loads(msg.content)
    except ValueError:  # text results, e.g. describe_tables schemas
        tables = [line.split("(")[0].split()[-1] for line in msg.content.splitlines() if line.startswith("CREATE")]
        summary = f"schema of {', '.join(tables)}" if tables else msg.content.splitlines()[0][:120]
        console.print(f"[bold blue]Observation:[/] {summary}")
        return
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
