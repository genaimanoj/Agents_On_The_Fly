"""Grep/search tool — search file contents in workspace."""

from __future__ import annotations

import re
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
    max_results = extra.get("max_results", 50)

    async def execute(pattern: str, path: str = ".", file_glob: str = "*") -> str:
        try:
            target = _safe_path(path, workspace)
        except ValueError as e:
            return f"Access denied: {e}"

        if not target.exists():
            return f"Path does not exist: {path}"

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"Invalid regex: {e}"

        matches = []

        def _search(dir_path: Path):
            if len(matches) >= max_results:
                return
            try:
                for entry in sorted(dir_path.iterdir()):
                    if len(matches) >= max_results:
                        return
                    if entry.is_dir():
                        if not entry.name.startswith("."):
                            _search(entry)
                    elif entry.is_file() and entry.match(file_glob):
                        try:
                            text = entry.read_text(encoding="utf-8", errors="replace")
                            for i, line in enumerate(text.splitlines(), 1):
                                if regex.search(line):
                                    rel = entry.relative_to(workspace)
                                    matches.append(f"{rel}:{i}: {line.strip()[:150]}")
                                    if len(matches) >= max_results:
                                        return
                        except (PermissionError, OSError):
                            pass
            except PermissionError:
                pass

        if target.is_file():
            try:
                text = target.read_text(encoding="utf-8", errors="replace")
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        rel = target.relative_to(workspace)
                        matches.append(f"{rel}:{i}: {line.strip()[:150]}")
                        if len(matches) >= max_results:
                            break
            except (PermissionError, OSError) as e:
                return f"Cannot read file: {e}"
        else:
            _search(target)

        if not matches:
            return f"No matches for '{pattern}' in {path}"

        header = f"Found {len(matches)} match(es)"
        if len(matches) >= max_results:
            header += f" (truncated at {max_results})"
        return header + "\n" + "\n".join(matches)

    return ToolInfo(
        name="grep_search",
        description=(
            "Search file contents using regex patterns. "
            "Returns matching lines with file paths and line numbers. "
            "Use to find code, functions, variables, or any text pattern."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "Relative path within workspace to search (default: root)",
                },
                "file_glob": {
                    "type": "string",
                    "description": "File glob filter, e.g. '*.py' (default: *)",
                },
            },
            "required": ["pattern"],
        },
        execute=execute,
    )
