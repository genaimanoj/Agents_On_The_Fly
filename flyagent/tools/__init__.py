"""Tool registry — maps tool names to async callables."""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from flyagent.config import AppConfig

# Lazy imports to avoid loading unused deps
_TOOL_FACTORIES: dict[str, Callable] = {}


def _register(name: str):
    def decorator(factory_fn):
        _TOOL_FACTORIES[name] = factory_fn
        return factory_fn
    return decorator


# ── Registration ──────────────────────────────────────────────

@_register("web_search")
def _make_web_search(cfg):
    from flyagent.tools.web_search import create_tool
    return create_tool(cfg)


@_register("web_fetch")
def _make_web_fetch(cfg):
    from flyagent.tools.web_fetch import create_tool
    return create_tool(cfg)


@_register("arxiv_search")
def _make_arxiv(cfg):
    from flyagent.tools.arxiv_tool import create_tool
    return create_tool(cfg)


@_register("wikipedia_search")
def _make_wiki(cfg):
    from flyagent.tools.wikipedia_tool import create_tool
    return create_tool(cfg)


@_register("news_search")
def _make_news(cfg):
    from flyagent.tools.news_search import create_tool
    return create_tool(cfg)


@_register("file_read")
def _make_file_read(cfg):
    from flyagent.tools.file_ops import create_read_tool
    return create_read_tool(cfg)


@_register("file_write")
def _make_file_write(cfg):
    from flyagent.tools.file_ops import create_write_tool
    return create_write_tool(cfg)


@_register("python_exec")
def _make_python_exec(cfg):
    from flyagent.tools.python_exec import create_tool
    return create_tool(cfg)


@_register("get_datetime")
def _make_datetime(cfg):
    from flyagent.tools.datetime_tool import create_tool
    return create_tool(cfg)


# ── Public API ────────────────────────────────────────────────

class ToolInfo:
    """Holds a tool's metadata and executor."""
    def __init__(self, name: str, description: str, parameters: dict[str, Any],
                 execute: Callable[..., Awaitable[str]]):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.execute = execute

    def schema_text(self) -> str:
        params_desc = []
        props = self.parameters.get("properties", {})
        for pname, pinfo in props.items():
            req = "(required)" if pname in self.parameters.get("required", []) else "(optional)"
            params_desc.append(f"    - {pname} ({pinfo.get('type', 'string')}) {req}: {pinfo.get('description', '')}")
        params_text = "\n".join(params_desc) if params_desc else "    (no parameters)"
        return f"### {self.name}\n{self.description}\nParameters:\n{params_text}"


class ToolRegistry:
    """Builds and holds enabled tools from config."""

    def __init__(self, config: AppConfig):
        self._tools: dict[str, ToolInfo] = {}
        for name, factory in _TOOL_FACTORIES.items():
            tool_cfg = config.tools.get(name)
            if tool_cfg and not tool_cfg.enabled:
                continue
            self._tools[name] = factory(tool_cfg)

    @property
    def all_names(self) -> list[str]:
        return list(self._tools.keys())

    def get(self, name: str) -> ToolInfo | None:
        return self._tools.get(name)

    def subset(self, names: list[str]) -> list[ToolInfo]:
        return [self._tools[n] for n in names if n in self._tools]

    def describe_all(self) -> str:
        return "\n\n".join(t.schema_text() for t in self._tools.values())

    def describe_subset(self, names: list[str]) -> str:
        tools = self.subset(names)
        return "\n\n".join(t.schema_text() for t in tools)
