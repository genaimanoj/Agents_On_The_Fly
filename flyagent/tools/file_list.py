"""File listing / directory tree tool — explore workspace contents."""

from __future__ import annotations

from pathlib import Path

from flyagent.config import ToolConfig, PROJECT_ROOT


def _safe_path(requested: str, base_dir: Path) -> Path:
    resolved = (base_dir / requested).resolve()
    if not str(resolved).startswith(str(base_dir.resolve())):
        raise ValueError(f"Path escapes workspace: {requested}")
    return resolved


def create_tool(cfg: ToolConfig | None):
    from flyagent.tools import ToolInfo

    extra = cfg.extra if cfg else {}
    workspace = (PROJECT_ROOT / extra.get("workspace_dir", "./workspace")).resolve()
    max_depth = extra.get("max_depth", 4)

    async def execute(path: str = ".", pattern: str = "*", depth: int = 0) -> str:
        try:
            target = _safe_path(path, workspace)
        except ValueError as e:
            return f"Access denied: {e}"

        if not target.exists():
            return f"Path does not exist: {path}"

        if target.is_file():
            stat = target.stat()
            return f"File: {path} ({stat.st_size} bytes)"

        effective_depth = min(depth if depth > 0 else max_depth, max_depth)

        lines = [f"Directory: {path}/"]
        count = 0

        def _walk(dir_path: Path, indent: int, current_depth: int):
            nonlocal count
            if current_depth > effective_depth:
                return
            try:
                entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name))
            except PermissionError:
                lines.append("  " * indent + "[permission denied]")
                return

            for entry in entries:
                if entry.name.startswith(".") and current_depth > 1:
                    continue
                count += 1
                if count > 500:
                    lines.append("  " * indent + "... (truncated at 500 entries)")
                    return
                rel = entry.relative_to(workspace) if str(entry).startswith(str(workspace)) else entry.name
                if entry.is_dir():
                    lines.append("  " * indent + f"📁 {entry.name}/")
                    _walk(entry, indent + 1, current_depth + 1)
                else:
                    size = entry.stat().st_size
                    lines.append("  " * indent + f"📄 {entry.name} ({size} bytes)")

        _walk(target, 1, 1)

        if count == 0:
            lines.append("  (empty directory)")

        return "\n".join(lines)

    return ToolInfo(
        name="file_list",
        description=(
            "List files and directories in the workspace. "
            "Shows a tree view with file sizes. Use to explore project structure."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within workspace (default: root)",
                },
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern filter (default: *)",
                },
                "depth": {
                    "type": "integer",
                    "description": "Max directory depth to show (default: config value)",
                },
            },
            "required": [],
        },
        execute=execute,
    )
