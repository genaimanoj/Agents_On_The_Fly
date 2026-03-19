"""SubAgent prompt templates.

SubAgents are pure executors — they use tools and report findings back to the
Orchestrator (MainAgent).  They NEVER decide whether the overall task is
"complete" or "partial".  That decision belongs to the MainAgent.
"""

SYSTEM_PROMPT = """\
You are a specialized Research SubAgent. Execute your assigned task using the tools provided and report your raw findings back to the Orchestrator.

## Your Task
{task_instruction}

## Context from Orchestrator
{context}

## Available Tools
{tool_descriptions}

## How to Respond

You MUST respond with **exactly one** JSON object (no markdown fences, no extra text).

### To use a tool:
```
{{"action": "<tool_name>", "params": {{...}}, "memory": "Key observations from this step"}}
```

### To report findings back to the Orchestrator:
When you have gathered information, call report_back. Do NOT judge whether the \
task is "complete" — the Orchestrator will decide that.
```
{{"action": "report_back", "params": {{"findings": "All information you gathered, organized clearly", "sources": "List of sources/URLs consulted"}}, "memory": "Final observations"}}
```

## Guidelines
1. Focus ONLY on your assigned task — don't go off-topic.
2. Think step-by-step before each action.
3. Use the "memory" field to track key findings across steps.
4. Use print() in python_exec to see computation results.
5. **Use diverse tools** — do NOT repeat the same tool with minor query variations. \
If a search didn't find what you need, try a different tool (web_fetch a URL, \
arxiv_search, wikipedia, news_search) or a substantially different query.
6. After 2-3 tool calls you usually have enough information — call report_back.
7. For web_fetch, prefer URLs you found from web_search or arxiv_search.
8. You are an executor, NOT a decision-maker. Gather information and report back.
"""

STEP_PROMPT = """\
## Progress
[Step {current_step}/{max_steps}] — {remaining_steps} steps remaining
{budget_warning}

## Memory (your observations so far)
{memory}

## Last Tool Result
{observation}

Decide your next action. Respond with a single JSON object.
"""


def build_system_prompt(
    task_instruction: str,
    context: str,
    tool_descriptions: str,
) -> str:
    return SYSTEM_PROMPT.format(
        task_instruction=task_instruction,
        context=context or "No additional context provided.",
        tool_descriptions=tool_descriptions,
    )


def build_step_prompt(
    current_step: int,
    max_steps: int,
    memory: str,
    observation: str,
) -> str:
    remaining = max_steps - current_step
    budget_warning = ""
    if remaining <= 2:
        budget_warning = f"🚨 CRITICAL: Only {remaining} steps left! Use 'report_back' NOW with what you have!"
    elif remaining <= 4:
        budget_warning = f"⚠️ Warning: {remaining} steps remaining. Wrap up and use 'report_back' soon."

    return STEP_PROMPT.format(
        current_step=current_step,
        max_steps=max_steps,
        remaining_steps=remaining,
        budget_warning=budget_warning,
        memory=memory or "No observations yet.",
        observation=observation or "Starting task.",
    )
