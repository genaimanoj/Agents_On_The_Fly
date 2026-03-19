#!/usr/bin/env python3
"""FlyAgent — Agent Sandbox with dynamic ICTM-based SubAgent creation.

Supports task modes: research, coding, automation, general.

Usage:
    python main.py "Build a Python web scraper for news headlines"
    python main.py --mode coding "Fix the bug in app.py"
    python main.py --mode research "What are the latest advances in quantum computing?"
    python main.py  # Interactive mode
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown


console = Console()

TASK_MODES = ["research", "coding", "automation", "general"]


async def run(query: str | None = None, task_mode: str | None = None) -> None:
    from flyagent.config import load_config
    from flyagent.orchestrator import Orchestrator

    config = load_config()

    # Override task mode if specified
    if task_mode:
        config.orchestrator.task_mode = task_mode

    mode = config.orchestrator.task_mode

    console.print(Panel(
        "[bold]FlyAgent Sandbox[/bold] — Agents On The Fly\n"
        f"[dim]Mode: {mode} | Dynamically creates specialized SubAgents on the fly[/dim]",
        style="bold blue",
    ))

    if not query:
        query = console.input(f"\n[bold]Enter your task ({mode} mode):[/bold] ").strip()
        if not query:
            console.print("[red]No task provided. Exiting.[/red]")
            return

    orchestrator = Orchestrator(config)
    result = await orchestrator.run(query)

    # Display final report
    console.print("\n")
    console.print(Panel(
        Markdown(result.report),
        title=f"Task Result (confidence: {result.confidence})",
        style="green",
        padding=(1, 2),
    ))

    # Stats
    console.print(
        f"\n[dim]Stats: {result.total_attempts} orchestrator steps | "
        f"{len(result.task_entries)} subagents spawned | "
        f"{result.elapsed_seconds:.1f}s total | "
        f"mode: {mode}[/dim]"
    )


def main_cli():
    parser = argparse.ArgumentParser(
        description="FlyAgent Sandbox — Agents On The Fly",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py "What are the latest AI breakthroughs?"
  python main.py --mode coding "Create a REST API with FastAPI"
  python main.py --mode automation "Set up a CI/CD pipeline"
  python main.py --mode research "Compare transformer architectures"
  python main.py  # Interactive mode
        """,
    )
    parser.add_argument("query", nargs="*", help="Task description")
    parser.add_argument(
        "--mode", "-m",
        choices=TASK_MODES,
        default=None,
        help="Task mode (default: from config.toml)",
    )
    args = parser.parse_args()

    query = " ".join(args.query) if args.query else None
    asyncio.run(run(query, task_mode=args.mode))


if __name__ == "__main__":
    main_cli()
