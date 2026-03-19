"""Python code execution tool — runs code in a subprocess."""

from __future__ import annotations

import asyncio

from flyagent.config import ToolConfig


def create_tool(cfg: ToolConfig | None):
    from flyagent.tools import ToolInfo

    extra = cfg.extra if cfg else {}
    timeout = extra.get("timeout_seconds", 30)

    async def execute(code: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Execution timed out after {timeout}s"

        output = ""
        if stdout:
            output += stdout.decode(errors="replace")
        if stderr:
            output += "\nSTDERR:\n" + stderr.decode(errors="replace")
        if not output.strip():
            output = "(no output)"
        return output[:5000]

    return ToolInfo(
        name="python_exec",
        description="Execute Python code and return stdout/stderr. Use print() to see results.",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
            },
            "required": ["code"],
        },
        execute=execute,
    )
