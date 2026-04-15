"""Shell command execution tool — runs commands in a subprocess."""

from __future__ import annotations

import asyncio
import os

from flyagent.config import ToolConfig, PROJECT_ROOT


def create_tool(cfg: ToolConfig | None):
    from flyagent.tools import ToolInfo

    extra = cfg.extra if cfg else {}
    timeout = extra.get("timeout_seconds", 60)
    working_dir = extra.get("working_dir", "./workspace")
    resolved_dir = (PROJECT_ROOT / working_dir).resolve()

    async def execute(command: str, working_directory: str = "") -> str:
        cwd = resolved_dir
        if working_directory:
            candidate = (resolved_dir / working_directory).resolve()
            if str(candidate).startswith(str(resolved_dir)):
                cwd = candidate

        env = os.environ.copy()
        env["HOME"] = str(resolved_dir)

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Command timed out after {timeout}s"

        output = ""
        if stdout:
            output += stdout.decode(errors="replace")
        if stderr:
            output += "\nSTDERR:\n" + stderr.decode(errors="replace")
        if not output.strip():
            output = f"(exit code: {proc.returncode})"
        else:
            output += f"\n(exit code: {proc.returncode})"
        return output[:8000]

    return ToolInfo(
        name="shell_exec",
        description=(
            "Execute a shell command (bash) and return stdout/stderr. "
            "Use for running programs, installing packages, compiling code, "
            "managing files, git operations, and any terminal command."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute (bash)",
                },
                "working_directory": {
                    "type": "string",
                    "description": "Subdirectory within workspace to run command in (optional)",
                },
            },
            "required": ["command"],
        },
        execute=execute,
    )
