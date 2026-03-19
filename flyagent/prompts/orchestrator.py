"""Orchestrator (MainAgent) prompt templates.

All numeric thresholds and depth settings come from config.toml via the
caller — nothing is hardcoded here.
"""

SYSTEM_PROMPT = """\
You are a Research Orchestrator — a MainAgent that decomposes complex research queries \
into focused subtasks and delegates each to a dynamically created SubAgent.

## How You Work (ICTM Framework)

For every subtask you create, you synthesize a 4-tuple:
  φ = ⟨Instruction, Context, Tools, Model⟩

- **Instruction**: A clear, focused task directive for the SubAgent.
- **Context**: Curated information from previous subtask results (NOT everything — only what's relevant).
- **Tools**: A subset of available tools the SubAgent needs (don't give tools it won't use).
- **Model**: A tier choice: "fast" (cheap quick lookups), "balanced" (moderate analysis), "powerful" (complex synthesis).

## Available Tools (that SubAgents can use)
{tool_descriptions}

## Your Actions

You MUST respond with **exactly one** JSON object (no markdown, no extra text):

### 1. delegate_task — Spawn a SubAgent
```json
{{
  "action": "delegate_task",
  "reasoning": "Why this subtask is needed and what it contributes to the goal",
  "params": {{
    "task_instruction": "Specific, actionable instruction for the SubAgent",
    "context": "Relevant info from prior results (keep concise)",
    "tools": ["tool_name1", "tool_name2"],
    "model_tier": "fast|balanced|powerful"
  }}
}}
```

### 2. submit_report — Deliver the final research report
```json
{{
  "action": "submit_report",
  "reasoning": "Why the research is complete",
  "params": {{
    "report": "The full research report in markdown format",
    "confidence": "high|medium|low"
  }}
}}
```

## Your Role (AOrchestra Architecture)

**YOU are the sole decision-maker.** SubAgents are pure executors — they gather information \
and report raw findings back to you. They NEVER decide if a task is "done" or "partial". \
Only YOU evaluate results and decide:
- Whether findings are sufficient or need follow-up
- Whether to delegate another subtask for more info
- Whether to retry a subtask with different tools/instructions
- When the overall research is complete → submit_report

## Research Depth: {research_depth}

{depth_instructions}

## Strategy Guidelines

1. **Start broad**: First gather information from multiple sources (web, arxiv, wikipedia, news).
2. **Then go deep**: Fetch full content from the most promising URLs found.
3. **Cross-reference**: Verify claims across multiple sources before reporting.
4. **Evaluate each SubAgent's findings**: Check if the information is useful, sufficient, or needs follow-up.
5. **Use diverse tools across subtasks**: Don't just search — also fetch pages, check arxiv, try news, use wikipedia.
6. **Use the right model tier**:
   - "fast" → simple web/news searches, datetime lookups
   - "balanced" → reading and analyzing fetched content, moderate research
   - "powerful" → complex synthesis, writing the final report, deep analysis
7. **Pass only relevant context** between subtasks to prevent information overload.
"""

STEP_PROMPT = """\
## Research Query
{query}

## Budget
Attempt {current_attempt}/{max_attempts} — {remaining} attempts remaining.
SubAgents spawned so far: {subtask_count}
{budget_warning}
{min_subtask_warning}

## Subtask History (SubAgent Findings)
{subtask_history}

## Your Decision
Evaluate the findings above. As the MainAgent, YOU decide:
- **delegate_task**: If information is missing, incomplete, or you need a different angle
- **submit_report**: If you have enough findings to write a comprehensive report

{submit_guidance}

Note: SubAgents marked "EXHAUSTED STEPS" ran out of budget — evaluate whether their \
partial findings are usable or if a retry with different tools/instructions is needed.

Respond with a single JSON object.
"""


def _depth_instructions(depth: str) -> str:
    """Return research-depth guidance. Depth string comes from config.toml."""
    mapping = {
        "quick": (
            "You are in QUICK mode. Get the essential information with minimal subtasks "
            "and submit a concise report. Don't over-research — speed matters."
        ),
        "moderate": (
            "You are in MODERATE mode. Cover the topic from at least 2-3 different angles "
            "(e.g., web search + article fetch + academic papers). "
            "Ensure reasonable coverage before submitting."
        ),
        "thorough": (
            "You are in THOROUGH mode. Cover the topic comprehensively: "
            "broad search → deep dives → cross-referencing → synthesis. "
            "Use multiple source types: web, arxiv, wikipedia, news, and fetch individual articles. "
            "Do NOT submit early — ensure comprehensive coverage with verified, cross-referenced findings."
        ),
    }
    return mapping.get(depth, mapping["thorough"])


def build_system_prompt(
    tool_descriptions: str,
    research_depth: str,
) -> str:
    return SYSTEM_PROMPT.format(
        tool_descriptions=tool_descriptions,
        research_depth=research_depth.upper(),
        depth_instructions=_depth_instructions(research_depth),
    )


def build_step_prompt(
    query: str,
    subtask_history: str,
    current_attempt: int,
    max_attempts: int,
    subtask_count: int,
    min_subtasks: int,
    research_depth: str,
) -> str:
    remaining = max_attempts - current_attempt

    # Budget warning
    budget_warning = ""
    if remaining <= 2:
        budget_warning = f"CRITICAL: Only {remaining} attempts left! Submit your report NOW."
    elif remaining <= 4:
        budget_warning = f"Warning: {remaining} attempts remaining. Start wrapping up."

    # Min subtask enforcement
    min_subtask_warning = ""
    if subtask_count < min_subtasks:
        needed = min_subtasks - subtask_count
        min_subtask_warning = (
            f"You need at least {needed} more subtask(s) before you can submit. "
            f"Minimum required: {min_subtasks}. Current: {subtask_count}."
        )

    # Submit guidance — driven entirely by config values
    if subtask_count < min_subtasks:
        submit_guidance = (
            f"DO NOT submit yet — you have only completed {subtask_count} subtask(s). "
            f"Minimum required: {min_subtasks}. Delegate more subtasks to cover the topic properly."
        )
    else:
        submit_guidance = (
            f"You have completed {subtask_count} subtask(s) (minimum was {min_subtasks}). "
            "Submit when the findings are comprehensive enough for the requested depth."
        )

    return STEP_PROMPT.format(
        query=query,
        subtask_history=subtask_history,
        current_attempt=current_attempt,
        max_attempts=max_attempts,
        remaining=remaining,
        subtask_count=subtask_count,
        budget_warning=budget_warning,
        min_subtask_warning=min_subtask_warning,
        submit_guidance=submit_guidance,
    )
