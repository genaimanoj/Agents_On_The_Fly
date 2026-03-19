"""File read/write tools — scoped to workspace directory."""

from __future__ import annotations

from pathlib import Path

from flyagent.config import ToolConfig, PROJECT_ROOT


def _safe_path(requested: str, base_dir: Path) -> Path:
    """Resolve path and ensure it stays within the allowed base."""
    resolved = (base_dir / requested).resolve()
    if not str(resolved).startswith(str(base_dir.resolve())):
        raise ValueError(f"Path escapes workspace: {requested}")
    return resolved


def create_read_tool(cfg: ToolConfig | None):
    from flyagent.tools import ToolInfo

    extra = cfg.extra if cfg else {}
    allowed_dirs = extra.get("allowed_dirs", ["./workspace"])
    max_size = extra.get("max_file_size_kb", 512) * 1024

    bases = [(PROJECT_ROOT / d).resolve() for d in allowed_dirs]

    async def execute(path: str) -> str:
        for base in bases:
            try:
                fpath = _safe_path(path, base)
                if fpath.exists() and fpath.is_file():
                    size = fpath.stat().st_size
                    if size > max_size:
                        return f"File too large ({size} bytes > {max_size} limit)"
                    return fpath.read_text(encoding="utf-8", errors="replace")
            except ValueError:
                continue
        return f"File not found or not accessible: {path}"

    return ToolInfo(
        name="file_read",
        description="Read a text file from the workspace directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within workspace"},
            },
            "required": ["path"],
        },
        execute=execute,
    )


def create_write_tool(cfg: ToolConfig | None):
    from flyagent.tools import ToolInfo

    extra = cfg.extra if cfg else {}
    output_dir = (PROJECT_ROOT / extra.get("output_dir", "./workspace")).resolve()

    async def execute(path: str, content: str) -> str:
        try:
            fpath = _safe_path(path, output_dir)
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
            return f"Written {len(content)} chars to {fpath.relative_to(PROJECT_ROOT)}"
        except ValueError as e:
            return f"Write denied: {e}"
        except Exception as e:
            return f"Write error: {e}"

    return ToolInfo(
        name="file_write",
        description="Write content to a file in the workspace directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within workspace"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
        execute=execute,
    )
