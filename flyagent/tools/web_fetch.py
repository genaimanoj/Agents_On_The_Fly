"""Fetch and extract text content from a URL."""

from __future__ import annotations

from flyagent.config import ToolConfig


def create_tool(cfg: ToolConfig | None):
    from flyagent.tools import ToolInfo

    extra = cfg.extra if cfg else {}
    max_len = extra.get("max_content_length", 15000)
    timeout = extra.get("timeout_seconds", 30)
    user_agent = extra.get("user_agent", "FlyAgent/0.1 Research Bot")

    async def execute(url: str) -> str:
        import httpx
        import html2text

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout,
                headers={"User-Agent": user_agent},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()

            converter = html2text.HTML2Text()
            converter.ignore_links = False
            converter.ignore_images = True
            converter.body_width = 0
            text = converter.handle(resp.text)

            if len(text) > max_len:
                text = text[:max_len] + f"\n\n... [truncated at {max_len} chars]"
            return text

        except httpx.HTTPStatusError as e:
            return f"HTTP error {e.response.status_code} fetching {url}"
        except httpx.ConnectError:
            return f"Connection failed for {url}"
        except Exception as e:
            return f"Error fetching {url}: {e}"

    return ToolInfo(
        name="web_fetch",
        description="Fetch a URL and extract its text content. Returns cleaned markdown text.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
            },
            "required": ["url"],
        },
        execute=execute,
    )
