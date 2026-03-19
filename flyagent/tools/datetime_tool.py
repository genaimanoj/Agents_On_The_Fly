"""Current date/time tool."""

from __future__ import annotations

from datetime import datetime, timezone

from flyagent.config import ToolConfig


def create_tool(cfg: ToolConfig | None):
    from flyagent.tools import ToolInfo

    async def execute() -> str:
        now = datetime.now(timezone.utc)
        return (
            f"Current UTC: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"ISO format: {now.isoformat()}"
        )

    return ToolInfo(
        name="get_datetime",
        description="Get the current date and time (UTC).",
        parameters={"type": "object", "properties": {}, "required": []},
        execute=execute,
    )
