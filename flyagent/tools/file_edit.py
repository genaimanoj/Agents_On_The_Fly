"""File edit tool — find-and-replace within files in workspace."""

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

    async def execute(path: str, old_text: str, new_text: str) -> str:
        try:
            fpath = _safe_path(path, workspace)
            if not fpath.exists():
                return f"File not found: {path}"
            content = fpath.read_text(encoding="utf-8", errors="replace")
            if old_text not in content:
                return f"old_text not found in {path}. File has {len(content)} chars."
            updated = content.replace(old_text, new_text, 1)
            fpath.write_text(updated, encoding="utf-8")
            return f"Replaced in {path}. File now has {len(updated)} chars."
        except ValueError as e:
            return f"Edit denied: {e}"
        except Exception as e:
            return f"Edit error: {e}"

    return ToolInfo(
        name="file_edit",
        description=(
            "Edit a file by replacing old_text with new_text (first occurrence). "
            "Use for modifying code, config files, or any text file in workspace."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within workspace"},
                "old_text": {"type": "string", "description": "Exact text to find and replace"},
                "new_text": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_text", "new_text"],
        },
        execute=execute,
    )
