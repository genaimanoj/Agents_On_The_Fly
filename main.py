#!/usr/bin/env python3
"""FlyAgent — Deep Research Agent with dynamic ICTM-based SubAgent creation.

Usage:
    python main.py "What are the latest advances in quantum computing?"
    python main.py  # Interactive mode
"""

from __future__ import annotations

import asyncio
import sys

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown


console = Console()


async def run(query: str | None = None) -> None:
    from flyagent.config import load_config
    from flyagent.orchestrator import Orchestrator

    config = load_config()

    console.print(Panel(
        "[bold]FlyAgent[/bold] — ICTM-based Deep Research Agent\n"
        "[dim]Dynamically creates specialized SubAgents on the fly[/dim]",
        style="bold blue",
    ))

    if not query:
        query = console.input("\n[bold]🔬 Enter your research query:[/bold] ").strip()
        if not query:
            console.print("[red]No query provided. Exiting.[/red]")
            return

    orchestrator = Orchestrator(config)
    result = await orchestrator.run(query)

    # Display final report
    console.print("\n")
    console.print(Panel(
        Markdown(result.report),
        title=f"📋 Research Report (confidence: {result.confidence})",
        style="green",
        padding=(1, 2),
    ))

    # Stats
    console.print(
        f"\n[dim]Stats: {result.total_attempts} orchestrator steps | "
        f"{len(result.task_entries)} subagents spawned | "
        f"{result.elapsed_seconds:.1f}s total[/dim]"
    )


def main_cli():
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    asyncio.run(run(query))


if __name__ == "__main__":
    main_cli()
