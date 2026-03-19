"""Web search tool — uses DuckDuckGo (no API key required)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from flyagent.config import ToolConfig


def create_tool(cfg: ToolConfig | None):
    from flyagent.tools import ToolInfo

    extra = cfg.extra if cfg else {}
    max_results = extra.get("max_results", 8)
    region = extra.get("region", "wt-wt")

    async def execute(query: str, max_results: int = max_results) -> str:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        def _search():
            with DDGS() as ddgs:
                results = list(ddgs.text(query, region=region, max_results=max_results))
            return results

        results = await asyncio.to_thread(_search)
        if not results:
            return "No search results found."

        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r.get('title', 'No title')}")
            lines.append(f"    URL: {r.get('href', 'N/A')}")
            lines.append(f"    {r.get('body', '')[:300]}")
            lines.append("")
        return "\n".join(lines)

    return ToolInfo(
        name="web_search",
        description="Search the web using DuckDuckGo. Returns titles, URLs, and snippets.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "max_results": {"type": "integer", "description": "Max results to return (default 8)"},
            },
            "required": ["query"],
        },
        execute=execute,
    )
