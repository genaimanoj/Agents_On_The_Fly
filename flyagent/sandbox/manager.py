"""SandboxManager — creates isolated tempdir workspaces per sub-agent.

Each sandbox is a temporary directory that acts as a fully isolated workspace.
The sub-agent gets tools (shell_exec, python_exec, file_read, file_write, etc.)
that are scoped to this tmpdir instead of the main workspace.  When the
sub-agent finishes, the sandbox is cleaned up automatically.

This follows the pattern used by AutoGen (Docker executor), Open Interpreter
(sandbox mode), and E2B (isolated cloud sandboxes) — but uses simple
subprocess + tmpdir isolation that works everywhere without Docker.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from flyagent.config import AppConfig, ToolConfig
from flyagent.tools import ToolInfo, ToolRegistry

logger = logging.getLogger("flyagent.sandbox")


class SandboxManager:
    """Creates and manages isolated sandbox environments for sub-agents.

    Usage::

        mgr = SandboxManager(config)
        sandbox = mgr.create()          # returns a Sandbox handle
        tools = sandbox.build_tools()    # tools scoped to sandbox dir
        # ... run sub-agent with these tools ...
        sandbox.cleanup()               # remove tmpdir
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._active: dict[str, "Sandbox"] = {}

    def create(self, sandbox_id: str = "") -> "Sandbox":
        """Create a new isolated sandbox environment."""
        sandbox = Sandbox(self.config, sandbox_id=sandbox_id)
        self._active[sandbox.sandbox_id] = sandbox
        logger.info(
            f"Sandbox created: {sandbox.sandbox_id} at {sandbox.work_dir}",
        )
        return sandbox

    def get(self, sandbox_id: str) -> "Sandbox | None":
        return self._active.get(sandbox_id)

    def cleanup(self, sandbox_id: str) -> None:
        """Clean up a specific sandbox."""
        sandbox = self._active.pop(sandbox_id, None)
        if sandbox:
            sandbox.cleanup()

    def cleanup_all(self) -> None:
        """Clean up all active sandboxes."""
        for sid in list(self._active):
            self.cleanup(sid)


class Sandbox:
    """An isolated execution environment backed by a temporary directory.

    All file and execution tools created by this sandbox are scoped to
    ``self.work_dir`` — a tmpdir that is destroyed on cleanup.
    """

    def __init__(self, config: AppConfig, sandbox_id: str = ""):
        self.config = config
        self.sandbox_id = sandbox_id or tempfile.mktemp(prefix="sbx_")[-8:]
        self.work_dir = Path(tempfile.mkdtemp(prefix=f"flyagent_sbx_{self.sandbox_id}_"))
        self._alive = True

    # ── Tool factories scoped to sandbox dir ──────────────────

    def build_tools(self, tool_names: list[str] | None = None) -> dict[str, ToolInfo]:
        """Build tool instances scoped to this sandbox's tmpdir.

        Only sandbox-capable tools are redirected.  Research tools
        (web_search, arxiv, etc.) are created normally from the global
        registry since they don't touch the filesystem.
        """
        if tool_names is None:
            tool_names = [
                "shell_exec", "python_exec", "file_read", "file_write",
                "file_edit", "file_list", "grep_search",
            ]

        tools: dict[str, ToolInfo] = {}
        for name in tool_names:
            builder = _SANDBOX_TOOL_BUILDERS.get(name)
            if builder:
                tools[name] = builder(self)
            else:
                # Fall through to global registry for non-sandboxed tools
                tools[name] = _build_passthrough_tool(name, self.config)
        return tools

    def collect_outputs(self) -> dict[str, str]:
        """Read all files created in the sandbox and return as a dict.

        Useful for the orchestrator to inspect what the sub-agent produced.
        """
        outputs: dict[str, str] = {}
        if not self.work_dir.exists():
            return outputs
        for fpath in self.work_dir.rglob("*"):
            if fpath.is_file():
                rel = str(fpath.relative_to(self.work_dir))
                try:
                    outputs[rel] = fpath.read_text(encoding="utf-8", errors="replace")[:50_000]
                except Exception:
                    outputs[rel] = "<binary or unreadable>"
        return outputs

    def cleanup(self) -> None:
        """Remove the sandbox tmpdir."""
        if self._alive and self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)
            self._alive = False
            logger.info(f"Sandbox cleaned up: {self.sandbox_id}")

    def __del__(self):
        self.cleanup()


# ── Sandbox-scoped tool builders ──────────────────────────────

_SANDBOX_TOOL_BUILDERS: dict[str, Any] = {}


def _sbx_tool(name: str):
    """Decorator to register a sandbox-scoped tool builder."""
    def deco(fn):
        _SANDBOX_TOOL_BUILDERS[name] = fn
        return fn
    return deco


@_sbx_tool("shell_exec")
def _build_shell_exec(sbx: Sandbox) -> ToolInfo:
    import asyncio
    import os

    timeout = sbx.config.tools.get("shell_exec")
    timeout_s = (timeout.extra if timeout else {}).get("timeout_seconds", 60)

    async def execute(command: str, working_directory: str = "") -> str:
        cwd = sbx.work_dir
        if working_directory:
            candidate = (sbx.work_dir / working_directory).resolve()
            if str(candidate).startswith(str(sbx.work_dir)):
                cwd = candidate

        env = os.environ.copy()
        env["HOME"] = str(sbx.work_dir)
        env["SANDBOX_ID"] = sbx.sandbox_id
        # Restrict PATH to standard system dirs only
        env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            return f"[SANDBOX] Command timed out after {timeout_s}s"

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
            "Execute a shell command (bash) in the SANDBOXED environment. "
            "All commands run inside an isolated temporary directory. "
            "Use for running programs, installing packages (pip install), "
            "compiling code, and any terminal command. "
            "The sandbox is destroyed after the task completes."
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
                    "description": "Subdirectory within sandbox to run in (optional)",
                },
            },
            "required": ["command"],
        },
        execute=execute,
    )


@_sbx_tool("python_exec")
def _build_python_exec(sbx: Sandbox) -> ToolInfo:
    import asyncio
    import os

    timeout = sbx.config.tools.get("python_exec")
    timeout_s = (timeout.extra if timeout else {}).get("timeout_seconds", 30)

    async def execute(code: str) -> str:
        env = os.environ.copy()
        env["HOME"] = str(sbx.work_dir)
        env["SANDBOX_ID"] = sbx.sandbox_id

        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(sbx.work_dir),
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            return f"[SANDBOX] Execution timed out after {timeout_s}s"

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
        description=(
            "Execute Python code in the SANDBOXED environment. "
            "Code runs in an isolated temp directory. Use print() to see results."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
            },
            "required": ["code"],
        },
        execute=execute,
    )


@_sbx_tool("file_read")
def _build_file_read(sbx: Sandbox) -> ToolInfo:
    max_size = 512 * 1024

    def _safe_path(requested: str) -> Path:
        resolved = (sbx.work_dir / requested).resolve()
        if not str(resolved).startswith(str(sbx.work_dir)):
            raise ValueError(f"Path escapes sandbox: {requested}")
        return resolved

    async def execute(path: str) -> str:
        try:
            fpath = _safe_path(path)
            if not fpath.exists() or not fpath.is_file():
                return f"File not found: {path}"
            size = fpath.stat().st_size
            if size > max_size:
                return f"File too large ({size} bytes > {max_size} limit)"
            return fpath.read_text(encoding="utf-8", errors="replace")
        except ValueError as e:
            return f"Access denied: {e}"

    return ToolInfo(
        name="file_read",
        description="Read a text file from the sandboxed workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within sandbox"},
            },
            "required": ["path"],
        },
        execute=execute,
    )


@_sbx_tool("file_write")
def _build_file_write(sbx: Sandbox) -> ToolInfo:
    def _safe_path(requested: str) -> Path:
        resolved = (sbx.work_dir / requested).resolve()
        if not str(resolved).startswith(str(sbx.work_dir)):
            raise ValueError(f"Path escapes sandbox: {requested}")
        return resolved

    async def execute(path: str, content: str) -> str:
        try:
            fpath = _safe_path(path)
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
            return f"[SANDBOX] Written {len(content)} chars to {path}"
        except ValueError as e:
            return f"Write denied: {e}"
        except Exception as e:
            return f"Write error: {e}"

    return ToolInfo(
        name="file_write",
        description="Write content to a file in the sandboxed workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within sandbox"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
        execute=execute,
    )


@_sbx_tool("file_edit")
def _build_file_edit(sbx: Sandbox) -> ToolInfo:
    def _safe_path(requested: str) -> Path:
        resolved = (sbx.work_dir / requested).resolve()
        if not str(resolved).startswith(str(sbx.work_dir)):
            raise ValueError(f"Path escapes sandbox: {requested}")
        return resolved

    async def execute(path: str, old_text: str, new_text: str) -> str:
        try:
            fpath = _safe_path(path)
            if not fpath.exists():
                return f"File not found: {path}"
            content = fpath.read_text(encoding="utf-8")
            if old_text not in content:
                return f"old_text not found in {path}"
            updated = content.replace(old_text, new_text, 1)
            fpath.write_text(updated, encoding="utf-8")
            return f"[SANDBOX] Edited {path}: replaced {len(old_text)} chars"
        except ValueError as e:
            return f"Edit denied: {e}"

    return ToolInfo(
        name="file_edit",
        description="Find-and-replace text in a file within the sandboxed workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within sandbox"},
                "old_text": {"type": "string", "description": "Text to find"},
                "new_text": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_text", "new_text"],
        },
        execute=execute,
    )


@_sbx_tool("file_list")
def _build_file_list(sbx: Sandbox) -> ToolInfo:
    max_depth = 4

    async def execute(path: str = ".", depth: int = 2) -> str:
        target = (sbx.work_dir / path).resolve()
        if not str(target).startswith(str(sbx.work_dir)):
            return "Path escapes sandbox."
        if not target.is_dir():
            return f"Not a directory: {path}"

        depth = min(depth, max_depth)
        lines: list[str] = []

        def _walk(p: Path, prefix: str, d: int):
            if d > depth:
                return
            try:
                entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except PermissionError:
                lines.append(f"{prefix}[permission denied]")
                return
            for entry in entries:
                if entry.is_dir():
                    lines.append(f"{prefix}{entry.name}/")
                    _walk(entry, prefix + "  ", d + 1)
                else:
                    size = entry.stat().st_size
                    lines.append(f"{prefix}{entry.name} ({size}B)")

        _walk(target, "", 1)
        return "\n".join(lines) if lines else "(empty directory)"

    return ToolInfo(
        name="file_list",
        description="List files and directories in the sandboxed workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: root of sandbox)"},
                "depth": {"type": "integer", "description": "Max depth to list (default: 2)"},
            },
            "required": [],
        },
        execute=execute,
    )


@_sbx_tool("grep_search")
def _build_grep_search(sbx: Sandbox) -> ToolInfo:
    import re as _re

    max_results = 50

    async def execute(pattern: str, path: str = ".", file_glob: str = "") -> str:
        target = (sbx.work_dir / path).resolve()
        if not str(target).startswith(str(sbx.work_dir)):
            return "Path escapes sandbox."

        try:
            regex = _re.compile(pattern)
        except _re.error as e:
            return f"Invalid regex: {e}"

        results: list[str] = []
        files = target.rglob(file_glob or "*") if target.is_dir() else [target]

        for fpath in files:
            if not fpath.is_file():
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    rel = fpath.relative_to(sbx.work_dir)
                    results.append(f"{rel}:{i}: {line.rstrip()[:200]}")
                    if len(results) >= max_results:
                        results.append(f"... (truncated at {max_results} results)")
                        return "\n".join(results)

        return "\n".join(results) if results else "No matches found."

    return ToolInfo(
        name="grep_search",
        description="Search for a regex pattern in files within the sandboxed workspace.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file path to search"},
                "file_glob": {"type": "string", "description": "Glob pattern for file names (e.g. '*.py')"},
            },
            "required": ["pattern"],
        },
        execute=execute,
    )


def _build_passthrough_tool(name: str, config: AppConfig) -> ToolInfo:
    """Build a non-sandboxed tool from the global registry.

    Used for tools that don't touch the filesystem (web_search, etc.).
    """
    registry = ToolRegistry(config)
    tool = registry.get(name)
    if tool is None:
        raise ValueError(f"Unknown tool: {name}")
    return tool
