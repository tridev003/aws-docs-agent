"""Tiny CLI to smoke-test the agent without booting Streamlit."""
from __future__ import annotations

import logging
import sys
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from aws_docs_agent.agent.graph import AgentSession

console = Console()


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s"
    )
    session = AgentSession()
    if not session.is_ready:
        console.print(
            "[bold red]Vector index not found.[/bold red] "
            "Run `make ingest` (or `python -m aws_docs_agent.rag.ingest`) first."
        )
        sys.exit(1)

    console.print(
        Panel.fit(
            "AWS Docs Copilot, type 'exit' to quit.\n"
            f"Indexed services: {', '.join(session.toolkit.indexed_services())}",
            border_style="cyan",
        )
    )
    while True:
        try:
            q = console.input("[bold cyan]you ›[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nbye")
            return
        if not q:
            continue
        if q.lower() in {"exit", "quit"}:
            return
        answer, sources, _ = session.turn(q)
        console.print(Panel(Markdown(answer or "[no answer]"), title="assistant", border_style="green"))
        if sources:
            console.print("[bold]sources:[/bold]")
            for s in sources:
                console.print(f"  • {s.title}, {s.url}")


if __name__ == "__main__":
    main()
