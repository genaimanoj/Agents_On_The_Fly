"""Wikipedia search — uses the free Wikipedia REST API."""

from __future__ import annotations

from flyagent.config import ToolConfig


def create_tool(cfg: ToolConfig | None):
    from flyagent.tools import ToolInfo

    extra = cfg.extra if cfg else {}
    default_sentences = extra.get("sentences", 10)

    async def execute(query: str, sentences: int = default_sentences) -> str:
        import httpx

        search_url = "https://en.wikipedia.org/w/api.php"
        search_params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": 3,
            "format": "json",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Search for matching articles
                resp = await client.get(search_url, params=search_params)
                resp.raise_for_status()
                data = resp.json()

                results = data.get("query", {}).get("search", [])
                if not results:
                    return f"No Wikipedia articles found for '{query}'."

                lines = []
                for r in results[:3]:
                    title = r["title"]
                    # Get summary via REST API
                    summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib_quote(title)}"
                    try:
                        sresp = await client.get(summary_url)
                        if sresp.status_code == 200:
                            sdata = sresp.json()
                            extract = sdata.get("extract", "No summary available.")
                            page_url = sdata.get("content_urls", {}).get("desktop", {}).get("page", "")
                            lines.append(f"## {title}")
                            lines.append(f"URL: {page_url}")
                            lines.append(f"{extract}")
                            lines.append("")
                    except Exception:
                        lines.append(f"## {title}\n(Failed to fetch summary)\n")

                return "\n".join(lines) if lines else "No content retrieved."

        except Exception as e:
            return f"Wikipedia search error: {e}"

    return ToolInfo(
        name="wikipedia_search",
        description="Search Wikipedia for encyclopedic information. Returns article summaries.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Topic to search on Wikipedia"},
            },
            "required": ["query"],
        },
        execute=execute,
    )


def urllib_quote(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s.replace(" ", "_"), safe="")
