"""Render the current LangGraph definition without maintaining a separate diagram.

    uv run visualize.py                    write graph.png
    uv run visualize.py --format ascii     print the graph in the terminal
    uv run visualize.py --format mermaid   print Mermaid source
"""
import argparse
from pathlib import Path

from engine.graph import current_graph


def main():
    parser = argparse.ArgumentParser(description="Visualize the compiled agent graph")
    parser.add_argument("--format", choices=("png", "ascii", "mermaid"), default="png")
    parser.add_argument("--output", type=Path, help="Output file (defaults to graph.png for PNG)")
    args = parser.parse_args()

    graph = current_graph().get_graph()
    if args.format == "ascii":
        print(graph.draw_ascii())
    elif args.format == "mermaid":
        mermaid = graph.draw_mermaid()
        if args.output:
            args.output.write_text(mermaid + "\n")
            print(f"Created {args.output}")
        else:
            print(mermaid)
    else:
        output = args.output or Path("graph.png")
        graph.draw_mermaid_png(output_file_path=str(output))
        print(f"Created {output}")


if __name__ == "__main__":
    main()
