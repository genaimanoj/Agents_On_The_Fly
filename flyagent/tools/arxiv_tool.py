"""ArXiv paper search — uses the free ArXiv API (no key required)."""

from __future__ import annotations

import asyncio
import urllib.parse
import xml.etree.ElementTree as ET

from flyagent.config import ToolConfig


_NS = {"atom": "http://www.w3.org/2005/Atom"}


def create_tool(cfg: ToolConfig | None):
    from flyagent.tools import ToolInfo

    extra = cfg.extra if cfg else {}
    default_max = extra.get("max_results", 8)
    sort_by = extra.get("sort_by", "relevance")

    async def execute(query: str, max_results: int = default_max) -> str:
        import httpx

        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": sort_by,
        }
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url)
                resp.raise_for_status()

            root = ET.fromstring(resp.text)
            entries = root.findall("atom:entry", _NS)

            if not entries:
                return "No papers found on ArXiv for this query."

            lines = []
            for i, entry in enumerate(entries, 1):
                title = entry.findtext("atom:title", "", _NS).strip().replace("\n", " ")
                summary = entry.findtext("atom:summary", "", _NS).strip().replace("\n", " ")[:300]
                published = entry.findtext("atom:published", "", _NS)[:10]
                arxiv_id = entry.findtext("atom:id", "", _NS).strip()
                authors = [a.findtext("atom:name", "", _NS) for a in entry.findall("atom:author", _NS)]
                author_str = ", ".join(authors[:4])
                if len(authors) > 4:
                    author_str += f" (+{len(authors)-4} more)"

                lines.append(f"[{i}] {title}")
                lines.append(f"    Authors: {author_str}")
                lines.append(f"    Published: {published}")
                lines.append(f"    URL: {arxiv_id}")
                lines.append(f"    Abstract: {summary}...")
                lines.append("")

            return "\n".join(lines)

        except Exception as e:
            return f"ArXiv search error: {e}"

    return ToolInfo(
        name="arxiv_search",
        description="Search academic papers on ArXiv. Returns titles, authors, dates, and abstracts.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query for academic papers"},
                "max_results": {"type": "integer", "description": "Max papers to return (default 8)"},
            },
            "required": ["query"],
        },
        execute=execute,
    )
