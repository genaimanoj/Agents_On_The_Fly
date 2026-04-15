"""DockerSandbox — container-based isolated execution for sub-agents.

Each sandboxed SubAgent gets its own Docker container with:
- Read-only root filesystem (--read-only)
- Only /workspace (bind mount from host tmpdir) and /tmp (tmpfs) are writable
- Resource limits (memory, CPU, PIDs)
- Network isolation (--network none when allow_network=false)
- No privilege escalation (--security-opt no-new-privileges)
- Non-root user inside container

The container stays alive via `sleep infinity` and commands run via `docker exec`.
This avoids the overhead of creating a new container per command (~50ms exec vs ~300ms run).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from flyagent.config import AppConfig
from flyagent.tools import ToolInfo

logger = logging.getLogger("flyagent.sandbox.docker")


class DockerSandbox:
    """An isolated execution environment backed by a Docker container.

    Interface-compatible with the local ``Sandbox`` class so that
    ``SubAgent`` code requires minimal changes.
    """

    def __init__(self, config: AppConfig, sandbox_id: str = ""):
        self.config = config
        self.sandbox_id = sandbox_id or tempfile.mktemp(prefix="sbx_")[-8:]
        # Host-side directory that gets bind-mounted into the container at /workspace
        self.work_dir = Path(tempfile.mkdtemp(prefix=f"flyagent_dsbx_{self.sandbox_id}_"))
        self._container_name = f"flyagent_sbx_{self.sandbox_id}"
        self._alive = False

    # ── Container lifecycle ──────────────────────────────────────

    async def start(self) -> None:
        """Create and start the Docker container."""
        sbx_cfg = self.config.sandbox

        cmd = [
            "docker", "run", "-d",
            "--name", self._container_name,
            # Mount host tmpdir as /workspace inside container
            "-v", f"{self.work_dir}:/workspace",
            "-w", "/workspace",
            # Security: read-only root filesystem
            "--read-only",
            # Writable /tmp inside container (RAM-backed, size-limited)
            "--tmpfs", "/tmp:size=100m",
            # Resource limits
            "--memory", getattr(sbx_cfg, "docker_memory", "512m"),
            "--cpus", str(getattr(sbx_cfg, "docker_cpus", 1.0)),
            "--pids-limit", "100",
            # No privilege escalation
            "--security-opt", "no-new-privileges",
            # Label for orphan cleanup
            "--label", "flyagent=sandbox",
            "--label", f"flyagent.sandbox_id={self.sandbox_id}",
        ]

        # Network policy
        if not sbx_cfg.allow_network:
            cmd.extend(["--network", "none"])

        # Image and entrypoint
        image = getattr(sbx_cfg, "docker_image", "flyagent-sandbox:latest")
        cmd.extend([image, "sleep", "infinity"])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            raise RuntimeError(
                f"Failed to start Docker sandbox {self._container_name}: {err}"
            )

        self._alive = True
        logger.info(
            f"Docker sandbox started: {self._container_name} "
            f"(image={image}, network={'bridge' if sbx_cfg.allow_network else 'none'})"
        )

    async def cleanup(self) -> None:
        """Stop and remove the Docker container, then remove host tmpdir."""
        if not self._alive:
            return

        self._alive = False

        # Remove container (force-kill if still running)
        proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", self._container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # Remove host-side tmpdir
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)

        logger.info(f"Docker sandbox cleaned up: {self._container_name}")

    def __del__(self):
        # Best-effort sync cleanup — async cleanup should be called explicitly
        if self._alive and self.work_dir.exists():
            shutil.rmtree(self.work_dir, ignore_errors=True)

    # ── Tool factories ───────────────────────────────────────────

    def build_tools(self, tool_names: list[str] | None = None) -> dict[str, ToolInfo]:
        """Build tool instances for this Docker sandbox.

        shell_exec and python_exec run via ``docker exec``.
        File tools (read/write/edit/list/grep) operate on the host-side
        bind mount directory and reuse the existing sandbox tool builders.
        """
        from flyagent.sandbox.manager import _SANDBOX_TOOL_BUILDERS, _build_passthrough_tool

        if tool_names is None:
            tool_names = [
                "shell_exec", "python_exec", "file_read", "file_write",
                "file_edit", "file_list", "grep_search",
            ]

        # Docker-specific tool builders for shell and python execution
        docker_builders = {
            "shell_exec": self._build_shell_exec,
            "python_exec": self._build_python_exec,
        }

        tools: dict[str, ToolInfo] = {}
        for name in tool_names:
            if name in docker_builders:
                tools[name] = docker_builders[name]()
            elif name in _SANDBOX_TOOL_BUILDERS:
                # File tools work on host-side bind mount — reuse as-is
                tools[name] = _SANDBOX_TOOL_BUILDERS[name](self)
                pass
            else:
                tools[name] = _build_passthrough_tool(name, self.config)
        return tools

    def collect_outputs(self) -> dict[str, str]:
        """Read all files created in the sandbox workspace.

        Since work_dir is a bind mount, files are directly accessible on the host.
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

    # ── Docker exec tool builders ────────────────────────────────

    def _build_shell_exec(self) -> ToolInfo:
        timeout_cfg = self.config.tools.get("shell_exec")
        timeout_s = (timeout_cfg.extra if timeout_cfg else {}).get("timeout_seconds", 60)
        container = self._container_name

        async def execute(command: str, working_directory: str = "") -> str:
            cwd = f"/workspace/{working_directory}" if working_directory else "/workspace"

            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "-w", cwd, container,
                "bash", "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                return f"[DOCKER SANDBOX] Command timed out after {timeout_s}s"

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
                "Execute a shell command (bash) in the SANDBOXED Docker container. "
                "Commands run inside an isolated container with full freedom — "
                "you can install packages, compile code, run any terminal command. "
                "The container is destroyed after the task completes."
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
                        "description": "Subdirectory within /workspace to run in (optional)",
                    },
                },
                "required": ["command"],
            },
            execute=execute,
        )

    def _build_python_exec(self) -> ToolInfo:
        timeout_cfg = self.config.tools.get("python_exec")
        timeout_s = (timeout_cfg.extra if timeout_cfg else {}).get("timeout_seconds", 30)
        container = self._container_name

        async def execute(code: str) -> str:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", "-w", "/workspace", container,
                "python3", "-c", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                return f"[DOCKER SANDBOX] Execution timed out after {timeout_s}s"

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
                "Execute Python code in the SANDBOXED Docker container. "
                "Code runs with full freedom — all imports are available. "
                "Use print() to see results."
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


# ── Orphan cleanup utility ───────────────────────────────────────

async def cleanup_orphaned_containers() -> int:
    """Remove any stale flyagent sandbox containers from previous runs.

    Returns the number of containers removed.
    """
    proc = await asyncio.create_subprocess_exec(
        "docker", "ps", "-a", "-q", "--filter", "label=flyagent=sandbox",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()

    if not stdout or not stdout.strip():
        return 0

    container_ids = stdout.decode().strip().split("\n")
    count = 0
    for cid in container_ids:
        cid = cid.strip()
        if not cid:
            continue
        rm_proc = await asyncio.create_subprocess_exec(
            "docker", "rm", "-f", cid,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await rm_proc.communicate()
        count += 1

    if count:
        logger.info(f"Cleaned up {count} orphaned sandbox container(s)")
    return count
