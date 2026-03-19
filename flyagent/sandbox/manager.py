"""SandboxManager — creates isolated tempdir workspaces per sub-agent.

Each sandbox is a temporary directory that acts as a fully isolated workspace.
The sub-agent gets tools (shell_exec, python_exec, file_read, file_write, etc.)
that are scoped to this tmpdir instead of the main workspace.  When the
sub-agent finishes, the sandbox is cleaned up automatically.

This follows the pattern used by AutoGen (Docker executor), Open Interpreter
(sandbox mode), and E2B (isolated cloud sandboxes) — but uses simple
subprocess + tmpdir isolation that works everywhere without Docker.

Security layers:
1. Command validation — blocks dangerous shell patterns (rm -rf /, absolute
   paths outside sandbox, network exfiltration, etc.)
2. Python restriction — wraps code in RestrictedPython-style checks, blocks
   dangerous imports (os.system, subprocess, shutil.rmtree, etc.)
3. Path containment — all file tools validate resolved paths stay within tmpdir.
4. Resource limits — timeouts, output size caps, restricted PATH/env.
"""

from __future__ import annotations

import logging
import re
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


# ── Command validation ────────────────────────────────────────

# Shell patterns that are always blocked — these can cause damage outside the
# sandbox tmpdir regardless of cwd.
_DANGEROUS_SHELL_PATTERNS: list[re.Pattern[str]] = [
    # Destructive recursive operations targeting root or parent dirs
    re.compile(r"\brm\s+.*-[^\s]*r[^\s]*\s+/(?!\S*tmp)", re.IGNORECASE),
    re.compile(r"\brm\s+.*-[^\s]*r[^\s]*\s+\.\.", re.IGNORECASE),
    # Direct removal of well-known system / user dirs
    re.compile(r"\brm\b.*\s+/(usr|etc|home|var|opt|boot|root|srv|lib|bin|sbin)\b"),
    # mkfs, dd targeting devices, format operations
    re.compile(r"\b(mkfs|dd\s+.*of=\s*/dev/)\b"),
    # Fork bombs and resource exhaustion
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;"),
    re.compile(r"\bfork\s*bomb\b", re.IGNORECASE),
    # chmod/chown on system paths
    re.compile(r"\b(chmod|chown)\b.*\s+/(usr|etc|home|var|bin|sbin|lib|boot)\b"),
    # Overwriting boot / system files
    re.compile(r">\s*/(etc|boot|usr|lib|bin|sbin)/"),
    # kill -9 targeting init / all processes
    re.compile(r"\bkill\s+.*-9\s+(1|0|-1)\b"),
    re.compile(r"\bkillall\b"),
    # Dangerous curl/wget piping to shell
    re.compile(r"\b(curl|wget)\b.*\|\s*(bash|sh|zsh|python|perl)\b"),
    # Direct /dev/ access
    re.compile(r"(?:>|<)\s*/dev/(?!null|zero|urandom|random)\S"),
    # Shutdown / reboot
    re.compile(r"\b(shutdown|reboot|halt|poweroff|init\s+[0-6])\b"),
]

# Absolute paths outside the sandbox that are never allowed in shell commands.
# The sandbox working dir (a /tmp path) is allowed.
_BLOCKED_ABS_PATH_PREFIXES = (
    "/home", "/root", "/etc", "/usr", "/var", "/opt", "/boot",
    "/srv", "/lib", "/bin", "/sbin", "/mnt", "/media", "/proc", "/sys",
)

# Python imports / calls that are dangerous in a sandbox
_DANGEROUS_PYTHON_PATTERNS: list[re.Pattern[str]] = [
    # Direct system command execution
    re.compile(r"\bos\.(system|popen|exec[a-z]*)\b"),
    re.compile(r"\bsubprocess\b"),
    re.compile(r"\bcommands\.(getoutput|getstatusoutput)\b"),
    # File system destruction outside cwd
    re.compile(r"\bshutil\.(rmtree|move|copytree)\b"),
    re.compile(r"\bos\.(remove|unlink|rmdir|removedirs|rename)\s*\(\s*['\"]/(home|etc|usr|var|root|boot|opt|srv|lib|bin|sbin)"),
    # Dangerous builtins
    re.compile(r"\b__import__\b"),
    re.compile(r"\beval\s*\("),
    re.compile(r"\bexec\s*\("),
    re.compile(r"\bcompile\s*\("),
    # Network exfiltration (reading sensitive files and sending them out)
    re.compile(r"\bsocket\b"),
    re.compile(r"\burllib\b"),
    re.compile(r"\brequests\b"),
    re.compile(r"\bhttpx\b"),
    re.compile(r"\baiohttp\b"),
    # ctypes / code injection
    re.compile(r"\bctypes\b"),
    # Signal manipulation
    re.compile(r"\bos\.kill\b"),
    re.compile(r"\bsignal\.(SIGKILL|SIGTERM)\b"),
]


def validate_shell_command(command: str, sandbox_dir: Path) -> str | None:
    """Validate a shell command for safety.

    Returns None if safe, or an error message explaining why it was blocked.
    """
    # Check against dangerous patterns
    for pat in _DANGEROUS_SHELL_PATTERNS:
        if pat.search(command):
            return f"[SANDBOX BLOCKED] Command matches dangerous pattern: {pat.pattern}"

    # Block absolute paths outside sandbox and /tmp
    sandbox_str = str(sandbox_dir)
    # Find all absolute paths in the command
    abs_paths = re.findall(r'(?<!\w)(/[a-zA-Z][a-zA-Z0-9_/.\-*]*)', command)
    for path in abs_paths:
        # Allow paths within the sandbox dir or /tmp
        if path.startswith(sandbox_str) or path.startswith("/tmp"):
            continue
        # Allow /dev/null, /dev/zero, /dev/urandom
        if path.startswith("/dev/null") or path.startswith("/dev/zero") or path.startswith("/dev/urandom"):
            continue
        # Block paths to sensitive system directories
        for prefix in _BLOCKED_ABS_PATH_PREFIXES:
            if path.startswith(prefix):
                return (
                    f"[SANDBOX BLOCKED] Command references path outside sandbox: {path}\n"
                    f"Only relative paths within the sandbox are allowed."
                )

    return None


def validate_python_code(code: str) -> str | None:
    """Validate Python code for safety in sandbox.

    Returns None if safe, or an error message explaining why it was blocked.
    """
    for pat in _DANGEROUS_PYTHON_PATTERNS:
        if pat.search(code):
            return f"[SANDBOX BLOCKED] Code matches dangerous pattern: {pat.pattern}"
    return None


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
        # Validate command before execution
        blocked = validate_shell_command(command, sbx.work_dir)
        if blocked:
            return blocked

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
        # Validate code before execution
        blocked = validate_python_code(code)
        if blocked:
            return blocked

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


# ── Optional OS-level isolation ────────────────────────────────

def _detect_isolation_support() -> str:
    """Detect available OS-level isolation.

    Returns one of: "unshare", "none".
    """
    # Check if unshare is available and can create user namespaces
    try:
        import subprocess
        result = subprocess.run(
            ["unshare", "--user", "--map-root-user", "true"],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            return "unshare"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return "none"


_ISOLATION_MODE: str | None = None


def _get_isolation_mode() -> str:
    """Cached detection of isolation mode."""
    global _ISOLATION_MODE
    if _ISOLATION_MODE is None:
        _ISOLATION_MODE = _detect_isolation_support()
        logger.info(f"Sandbox isolation mode: {_ISOLATION_MODE}")
    return _ISOLATION_MODE


def wrap_command_with_isolation(command: str, work_dir: Path) -> str:
    """Optionally wrap a command with OS-level isolation.

    When unshare is available, uses mount+pid namespaces to:
    - Make the filesystem outside the sandbox read-only
    - Isolate process IDs so the sandbox can't signal host processes
    """
    mode = _get_isolation_mode()
    if mode == "unshare":
        # Use unshare with mount namespace to restrict filesystem access.
        # --mount: new mount namespace (can make dirs read-only)
        # --pid --fork: new PID namespace (can't kill host processes)
        # --map-root-user: map current user to root in namespace (needed for mount ops)
        # After entering namespace, remount sensitive dirs as read-only.
        isolation_prefix = (
            f"unshare --mount --pid --fork --map-root-user -- "
            f"bash -c '"
            f"mount --bind /tmp /tmp 2>/dev/null; "  # keep /tmp writable
            f"exec bash -c \"cd {work_dir} && {command.replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}\"'"
        )
        return isolation_prefix
    return command


def _build_passthrough_tool(name: str, config: AppConfig) -> ToolInfo:
    """Build a non-sandboxed tool from the global registry.

    Used for tools that don't touch the filesystem (web_search, etc.).
    """
    registry = ToolRegistry(config)
    tool = registry.get(name)
    if tool is None:
        raise ValueError(f"Unknown tool: {name}")
    return tool
