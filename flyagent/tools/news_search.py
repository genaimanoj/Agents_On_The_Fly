"""News search tool — uses DuckDuckGo News (no API key required)."""

from __future__ import annotations

import asyncio

from flyagent.config import ToolConfig


def create_tool(cfg: ToolConfig | None):
    from flyagent.tools import ToolInfo

    extra = cfg.extra if cfg else {}
    default_max = extra.get("max_results", 8)
    timelimit = extra.get("timelimit", "m")

    async def execute(query: str, max_results: int = default_max) -> str:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        def _search():
            with DDGS() as ddgs:
                results = list(ddgs.news(query, max_results=max_results, timelimit=timelimit))
            return results

        results = await asyncio.to_thread(_search)
        if not results:
            return "No news results found."

        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r.get('title', 'No title')}")
            lines.append(f"    Source: {r.get('source', 'Unknown')} | Date: {r.get('date', 'N/A')}")
            lines.append(f"    URL: {r.get('url', 'N/A')}")
            lines.append(f"    {r.get('body', '')[:300]}")
            lines.append("")
        return "\n".join(lines)

    return ToolInfo(
        name="news_search",
        description="Search recent news articles. Returns headlines, sources, dates, and snippets.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "News search query"},
                "max_results": {"type": "integer", "description": "Max results (default 8)"},
            },
            "required": ["query"],
        },
        execute=execute,
    )
