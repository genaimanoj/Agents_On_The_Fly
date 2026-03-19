"""Orchestrator (MainAgent) — decomposes tasks and spawns SubAgents on the fly.

Implements the ICTM framework from AOrchestra. Supports multiple task modes:
research, coding, automation, general. Emits events via EventBus for real-time
UI streaming and logs via OTEL-compliant structured logging.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from flyagent.config import AppConfig
from flyagent.ictm import ICTM
from flyagent.llm import create_model, chat_turn
from flyagent.logging_setup import research_id_var, trace_id_var
from flyagent.prompts.orchestrator import build_system_prompt, build_step_prompt
from flyagent.subagent import SubAgentResult, run_subagent
from flyagent.tools import ToolRegistry

logger = logging.getLogger("flyagent.orchestrator")
console = Console()


@dataclass
class TaskEntry:
    attempt: int
    ictm: ICTM
    result: SubAgentResult | None = None


@dataclass
class OrchestrationResult:
    query: str
    report: str = ""
    confidence: str = "low"
    task_entries: list[TaskEntry] = field(default_factory=list)
    total_attempts: int = 0
    elapsed_seconds: float = 0.0


def _fix_json_newlines(s: str) -> str:
    """Escape literal newlines/tabs that appear inside JSON string values.

    LLMs frequently emit real line-breaks inside JSON strings instead of \\n.
    We walk the string, track whether we're inside a quoted value, and replace
    raw control characters with their escape sequences.
    """
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

    # Extract the outermost JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
    else:
        candidate = text

    # Try parsing as-is first
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Fix literal newlines inside JSON strings and retry
    try:
        return json.loads(_fix_json_newlines(candidate))
    except json.JSONDecodeError:
        pass

    # Last resort: try to extract key fields with regex
    action_m = re.search(r'"action"\s*:\s*"([^"]+)"', text)
    report_m = re.search(r'"report"\s*:\s*"(.*?)"(?:\s*[,}])', text, re.DOTALL)
    conf_m = re.search(r'"confidence"\s*:\s*"([^"]+)"', text)
    if action_m and action_m.group(1) == "submit_report" and report_m:
        report_text = report_m.group(1).replace('\\n', '\n').replace('\\"', '"')
        return {
            "action": "submit_report",
            "params": {
                "report": report_text,
                "confidence": conf_m.group(1) if conf_m else "medium",
            },
        }

    return {"action": "submit_report", "params": {"report": text, "confidence": "low"}}


def _format_subtask_history(entries: list[TaskEntry]) -> str:
    """Format subtask history for the Orchestrator to evaluate.

    The Orchestrator sees raw findings from each SubAgent and decides
    whether the information is sufficient, needs follow-up, or retry.
    """
    if not entries:
        return "No subtasks completed yet."
    lines = []
    for i, e in enumerate(entries, 1):
        r = e.result
        exhausted = "⚠️ EXHAUSTED STEPS" if (r and r.exhausted_steps) else ""
        header = (
            f"[SubAgent #{i} | Attempt {e.attempt}] "
            f"model={e.ictm.model_tier} | tools={e.ictm.tools} | "
            f"steps={r.steps_taken if r else 0} | "
            f"time={r.elapsed_seconds:.1f}s {exhausted}" if r else "pending"
        )
        lines.append(header)
        lines.append(f"  Task: {e.ictm.instruction[:150]}")
        if r and r.findings:
            preview = r.findings[:600]
            if len(r.findings) > 600:
                preview += "... [truncated]"
            lines.append(f"  Findings: {preview}")
        if r and r.sources:
            lines.append(f"  Sources: {r.sources[:200]}")
        lines.append("")
    return "\n".join(lines)


class Orchestrator:
    """MainAgent that orchestrates tasks via dynamic SubAgent creation.

    Supports task modes: research, coding, automation, general.
    """

    def __init__(self, config: AppConfig, event_bus: Any | None = None):
        self.config = config
        self.tool_registry = ToolRegistry(config)
        self.event_bus = event_bus

    async def _emit(self, research_id: str, event_type: str, data: dict) -> None:
        if self.event_bus:
            await self.event_bus.emit(research_id, event_type, data)

    async def run(self, query: str, research_id: str = "") -> OrchestrationResult:
        if not research_id:
            research_id = uuid.uuid4().hex[:12]

        # Set context vars for structured logging
        trace_token = trace_id_var.set(uuid.uuid4().hex)
        rid_token = research_id_var.set(research_id)

        t0 = time.time()
        max_attempts = self.config.orchestrator.max_attempts
        model_cfg = self.config.get_model(self.config.orchestrator.model_tier)
        task_entries: list[TaskEntry] = []

        task_mode = self.config.orchestrator.task_mode

        logger.info(
            "Starting task",
            extra={"research_id": research_id, "agent_type": "orchestrator", "task_mode": task_mode},
        )

        await self._emit(research_id, "orchestrator_started", {
            "query": query,
            "model": model_cfg.model,
            "model_tier": self.config.orchestrator.model_tier,
            "max_attempts": max_attempts,
            "task_mode": task_mode,
            "tools": self.tool_registry.all_names,
        })

        console.print(Panel(
            f"[bold]Task:[/bold] {query}\n"
            f"[dim]Mode: {task_mode} | Model: {model_cfg.model} | Max attempts: {max_attempts}[/dim]",
            title="FlyAgent Sandbox Orchestrator",
            style="blue",
        ))

        sys_prompt = build_system_prompt(
            tool_descriptions=self.tool_registry.describe_all(),
            task_mode=task_mode,
            task_depth=self.config.orchestrator.task_depth,
        )
        model = create_model(model_cfg, system_instruction=sys_prompt)
        chat = model.start_chat(history=[])

        for attempt in range(1, max_attempts + 1):
            logger.info(
                f"Orchestrator attempt {attempt}/{max_attempts}",
                extra={"research_id": research_id, "agent_type": "orchestrator", "step": attempt},
            )
            await self._emit(research_id, "orchestrator_attempt", {
                "attempt": attempt,
                "max_attempts": max_attempts,
            })
            console.print(f"\n[bold yellow]--- Orchestrator Attempt {attempt}/{max_attempts} ---[/bold yellow]")

            step_prompt = build_step_prompt(
                query=query,
                subtask_history=_format_subtask_history(task_entries),
                current_attempt=attempt,
                max_attempts=max_attempts,
                subtask_count=len(task_entries),
                min_subtasks=self.config.orchestrator.min_subtasks,
                task_depth=self.config.orchestrator.task_depth,
            )

            try:
                raw_response = await chat_turn(chat, step_prompt)
            except Exception as e:
                logger.error(f"LLM error: {e}", extra={"research_id": research_id})
                await self._emit(research_id, "log", {"level": "ERROR", "message": f"LLM error: {e}"})
                continue

            decision = _parse_json(raw_response)
            action = decision.get("action", "submit_report")
            reasoning = decision.get("reasoning", "")
            params = decision.get("params", {})

            if reasoning:
                logger.info(f"Reasoning: {reasoning[:200]}", extra={"research_id": research_id})
                await self._emit(research_id, "log", {"level": "INFO", "message": f"Reasoning: {reasoning[:200]}"})

            # ── Enforce min_subtasks: reject early submit ──
            min_sub = self.config.orchestrator.min_subtasks
            if action == "submit_report" and len(task_entries) < min_sub:
                needed = min_sub - len(task_entries)
                logger.info(
                    f"Blocked early submit — need {needed} more subtask(s) (min={min_sub})",
                    extra={"research_id": research_id},
                )
                await self._emit(research_id, "log", {
                    "level": "WARN",
                    "message": f"Submit blocked: {len(task_entries)}/{min_sub} subtasks completed. Delegating more.",
                })
                # Tell the LLM to delegate instead
                rejection = (
                    f"SYSTEM: Your submit_report was REJECTED because you have only completed "
                    f"{len(task_entries)} subtask(s) but the minimum is {min_sub}. "
                    f"You MUST delegate {needed} more subtask(s) before submitting. "
                    f"Respond with a delegate_task action."
                )
                try:
                    raw_response = await chat_turn(chat, rejection)
                    decision = _parse_json(raw_response)
                    action = decision.get("action", "delegate_task")
                    reasoning = decision.get("reasoning", "")
                    params = decision.get("params", {})
                    if reasoning:
                        await self._emit(research_id, "log", {"level": "INFO", "message": f"Reasoning: {reasoning[:200]}"})
                except Exception as e:
                    logger.error(f"LLM error on rejection retry: {e}", extra={"research_id": research_id})
                    continue

            # ── submit_report ──
            if action == "submit_report":
                report = params.get("report", raw_response)
                confidence = params.get("confidence", "medium")
                logger.info(f"Report submitted (confidence={confidence})", extra={"research_id": research_id})

                await self._emit(research_id, "report_submitted", {
                    "report": report,
                    "confidence": confidence,
                })

                result = OrchestrationResult(
                    query=query, report=report, confidence=confidence,
                    task_entries=task_entries, total_attempts=attempt,
                    elapsed_seconds=time.time() - t0,
                )

                await self._emit(research_id, "research_complete", {
                    "elapsed": result.elapsed_seconds,
                    "subagents_spawned": len(task_entries),
                    "total_attempts": attempt,
                })

                self._save_artifacts(result)
                trace_id_var.reset(trace_token)
                research_id_var.reset(rid_token)
                return result

            # ── delegate_task ──
            if action == "delegate_task":
                subagent_idx = len(task_entries) + 1
                sandboxed = params.get("sandboxed", False)
                ictm = ICTM(
                    instruction=params.get("task_instruction", ""),
                    context=params.get("context", ""),
                    tools=params.get("tools", []),
                    model_tier=params.get("model_tier", "balanced"),
                    sandboxed=sandboxed,
                )
                entry = TaskEntry(attempt=attempt, ictm=ictm)
                task_entries.append(entry)

                sbx_label = " [SANDBOXED]" if sandboxed else ""
                logger.info(
                    f"Spawning SubAgent #{subagent_idx}{sbx_label}: {ictm.instruction[:80]}",
                    extra={"research_id": research_id, "agent_type": "orchestrator", "agent_id": f"sub_{subagent_idx}"},
                )
                await self._emit(research_id, "subagent_created", {
                    "id": subagent_idx,
                    "instruction": ictm.instruction,
                    "context": ictm.context[:200],
                    "tools": ictm.tools,
                    "model_tier": ictm.model_tier,
                    "sandboxed": sandboxed,
                    "max_steps": self.config.subagent.max_steps,
                })

                console.print(Panel(
                    f"[bold]{ictm.instruction[:120]}[/bold]\n"
                    f"[dim]Model: {ictm.model_tier} | Tools: {ictm.tools}{sbx_label}[/dim]",
                    title=f"SubAgent #{subagent_idx}",
                    style="cyan",
                ))

                sub_result = await run_subagent(
                    ictm, self.config, self.tool_registry,
                    event_bus=self.event_bus,
                    research_id=research_id,
                    subagent_id=subagent_idx,
                )
                entry.result = sub_result

                # SubAgent reports raw findings — Orchestrator evaluates
                exhausted_label = " (exhausted steps)" if sub_result.exhausted_steps else ""
                await self._emit(research_id, "subagent_finished", {
                    "id": subagent_idx,
                    "status": "reported_back",
                    "summary": f"Findings returned{exhausted_label}",
                    "steps_taken": sub_result.steps_taken,
                    "elapsed": sub_result.elapsed_seconds,
                    "result_preview": sub_result.findings[:300] if sub_result.findings else "",
                    "exhausted_steps": sub_result.exhausted_steps,
                })

                style = "yellow" if sub_result.exhausted_steps else "green"
                console.print(
                    f"  [{style}]SubAgent reported back{exhausted_label}[/{style}] "
                    f"({sub_result.steps_taken} steps, {sub_result.elapsed_seconds:.1f}s)"
                )
                continue

            # Unknown → treat as report
            result = OrchestrationResult(
                query=query, report=raw_response, confidence="low",
                task_entries=task_entries, total_attempts=attempt,
                elapsed_seconds=time.time() - t0,
            )
            await self._emit(research_id, "research_complete", {
                "elapsed": result.elapsed_seconds, "subagents_spawned": len(task_entries),
                "total_attempts": attempt,
            })
            self._save_artifacts(result)
            trace_id_var.reset(trace_token)
            research_id_var.reset(rid_token)
            return result

        # Budget exhausted
        partial = "# Task Report (Partial — budget exhausted)\n\n"
        partial += f"**Task:** {query}\n\n"
        for i, e in enumerate(task_entries, 1):
            r = e.result
            partial += f"## Finding {i}\n**Task:** {e.ictm.instruction}\n\n"
            if r and r.result:
                partial += f"{r.result}\n\n"

        result = OrchestrationResult(
            query=query, report=partial, confidence="low",
            task_entries=task_entries, total_attempts=max_attempts,
            elapsed_seconds=time.time() - t0,
        )
        await self._emit(research_id, "report_submitted", {"report": partial, "confidence": "low"})
        await self._emit(research_id, "research_complete", {
            "elapsed": result.elapsed_seconds, "subagents_spawned": len(task_entries),
            "total_attempts": max_attempts,
        })
        self._save_artifacts(result)
        trace_id_var.reset(trace_token)
        research_id_var.reset(rid_token)
        return result

    def _save_artifacts(self, result: OrchestrationResult) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        if self.config.output.save_final_report:
            rd = Path(self.config.output.report_dir)
            rd.mkdir(parents=True, exist_ok=True)
            (rd / f"report_{ts}.md").write_text(result.report, encoding="utf-8")
        if self.config.output.save_trajectory:
            td = Path(self.config.output.trajectory_dir)
            td.mkdir(parents=True, exist_ok=True)
            traj = {
                "query": result.query, "confidence": result.confidence,
                "total_attempts": result.total_attempts,
                "elapsed_seconds": result.elapsed_seconds,
                "subtasks": [
                    {
                        "attempt": e.attempt,
                        "ictm": {"instruction": e.ictm.instruction, "context": e.ictm.context[:500],
                                 "tools": e.ictm.tools, "model_tier": e.ictm.model_tier,
                                 "sandboxed": e.ictm.sandboxed},
                        **({"result": {"findings": e.result.findings[:2000],
                                       "sources": e.result.sources, "steps_taken": e.result.steps_taken,
                                       "elapsed_seconds": e.result.elapsed_seconds,
                                       "exhausted_steps": e.result.exhausted_steps,
                                       "trace": e.result.trace}}
                           if e.result else {}),
                    }
                    for e in result.task_entries
                ],
            }
            (td / f"trajectory_{ts}.json").write_text(
                json.dumps(traj, indent=2, ensure_ascii=False), encoding="utf-8"
            )
