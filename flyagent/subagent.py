"""SubAgent — dynamically created executor with an ICTM tuple.

Emits events via EventBus for real-time UI streaming.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

from flyagent.config import AppConfig
from flyagent.ictm import ICTM
from flyagent.llm import create_model, chat_turn
from flyagent.prompts.subagent import build_system_prompt, build_step_prompt
from flyagent.tools import ToolRegistry

logger = logging.getLogger("flyagent.subagent")
console = Console()


@dataclass
class SubAgentResult:
    """Raw result from a SubAgent — no completion judgment.

    The Orchestrator (MainAgent) evaluates these findings and decides
    whether the subtask succeeded, needs retry, or is sufficient.
    """
    findings: str = ""
    sources: str = ""
    steps_taken: int = 0
    memory: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    exhausted_steps: bool = False  # True if SubAgent hit max_steps without report_back


def _fix_json_newlines(s: str) -> str:
    """Escape literal newlines/tabs inside JSON string values."""
    out: list[str] = []
    in_str = False
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == '"' and (i == 0 or s[i - 1] != '\\'):
            in_str = not in_str
            out.append(ch)
        elif in_str and ch == '\n':
            out.append('\\n')
        elif in_str and ch == '\r':
            out.append('\\r')
        elif in_str and ch == '\t':
            out.append('\\t')
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


def _parse_json(text: str) -> dict:
    # Strip markdown fences
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    text = text.strip()

    # Extract outermost JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
    else:
        candidate = text

    # Try parsing as-is
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Fix literal newlines and retry
    try:
        return json.loads(_fix_json_newlines(candidate))
    except json.JSONDecodeError:
        pass

    return {"action": "report_back", "params": {"findings": text, "sources": "Unable to parse structured response"}}


async def run_subagent(
    ictm: ICTM,
    config: AppConfig,
    tool_registry: ToolRegistry,
    event_bus: Any | None = None,
    research_id: str = "",
    subagent_id: int = 0,
) -> SubAgentResult:
    """Execute a SubAgent with the given ICTM tuple."""

    async def _emit(event_type: str, data: dict) -> None:
        if event_bus:
            await event_bus.emit(research_id, event_type, data)

    t0 = time.time()
    max_steps = config.subagent.max_steps
    model_cfg = config.get_model(ictm.model_tier)

    tool_descriptions = tool_registry.describe_subset(ictm.tools)
    tools = {t.name: t for t in tool_registry.subset(ictm.tools)}

    sys_prompt = build_system_prompt(
        task_instruction=ictm.instruction,
        context=ictm.context,
        tool_descriptions=tool_descriptions,
    )
    model = create_model(model_cfg, system_instruction=sys_prompt)
    chat = model.start_chat(history=[])

    memory_entries: list[str] = []
    trace: list[dict] = []
    observation = "Starting task."

    for step in range(1, max_steps + 1):
        step_prompt = build_step_prompt(
            current_step=step,
            max_steps=max_steps,
            memory="\n".join(memory_entries[-10:]) if memory_entries else "",
            observation=observation,
        )

        try:
            raw_response = await chat_turn(chat, step_prompt)
        except Exception as e:
            logger.error(
                f"SubAgent #{subagent_id} LLM error step {step}: {e}",
                extra={"research_id": research_id, "agent_id": f"sub_{subagent_id}", "step": step},
            )
            await _emit("log", {"level": "ERROR", "message": f"SubAgent #{subagent_id} LLM error: {e}"})
            observation = f"LLM error: {e}"
            continue

        decision = _parse_json(raw_response)
        action_name = decision.get("action", "report_back")
        params = decision.get("params", {})
        mem = decision.get("memory", "")

        # Backward compat: treat legacy "finish" as "report_back"
        if action_name == "finish":
            action_name = "report_back"
            if "result" in params and "findings" not in params:
                params["findings"] = params.pop("result", "")
            if "summary" in params and "sources" not in params:
                params["sources"] = params.pop("summary", "")
            params.pop("status", None)

        if mem:
            memory_entries.append(f"[Step {step}] {mem}")

        logger.info(
            f"SubAgent #{subagent_id} step {step}: {action_name}",
            extra={
                "research_id": research_id, "agent_type": "subagent",
                "agent_id": f"sub_{subagent_id}", "action": action_name, "step": step,
            },
        )
        await _emit("subagent_step", {
            "id": subagent_id, "step": step, "max_steps": max_steps,
            "action": action_name, "params_preview": json.dumps(params)[:200],
        })

        if config.output.verbose:
            console.print(f"  [dim]Step {step}/{max_steps}[/dim] action=[cyan]{action_name}[/cyan]", highlight=False)

        trace_entry: dict[str, Any] = {"step": step, "action": action_name, "params": params, "memory": mem}

        # ── Report back to Orchestrator ──
        if action_name == "report_back":
            trace.append(trace_entry)
            return SubAgentResult(
                findings=params.get("findings", ""),
                sources=params.get("sources", ""),
                steps_taken=step,
                memory=memory_entries,
                trace=trace,
                elapsed_seconds=time.time() - t0,
            )

        # ── Tool execution ──
        tool = tools.get(action_name)
        if not tool:
            observation = f"Unknown tool '{action_name}'. Available: {list(tools.keys())}"
            trace_entry["error"] = observation
            trace.append(trace_entry)
            await _emit("log", {"level": "WARN", "message": f"SubAgent #{subagent_id}: {observation}"})
            continue

        try:
            observation = await tool.execute(**params)
            if len(observation) > 4000:
                observation = observation[:4000] + "\n... [truncated]"
        except TypeError as e:
            observation = f"Tool parameter error for '{action_name}': {e}"
            trace_entry["error"] = observation
        except Exception as e:
            observation = f"Tool error ({action_name}): {e}"
            trace_entry["error"] = observation

        trace_entry["result_preview"] = observation[:200]
        trace.append(trace_entry)

    # SubAgent exhausted its step budget — return whatever was gathered
    return SubAgentResult(
        findings="\n".join(memory_entries),
        sources="Exhausted step budget before explicit report_back.",
        steps_taken=max_steps,
        memory=memory_entries,
        trace=trace,
        elapsed_seconds=time.time() - t0,
        exhausted_steps=True,
    )
