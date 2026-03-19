"""ICTM — the core abstraction from AOrchestra.

Every dynamically created SubAgent is defined by a 4-tuple:
    φ = ⟨I, C, T, M⟩
where
    I = Instruction   — actionable task directive
    C = Context       — curated information for the agent
    T = Tools         — list of tool names the agent can use
    M = Model         — which LLM tier to use
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ICTM:
    instruction: str
    context: str = ""
    tools: list[str] = field(default_factory=list)
    model_tier: str = "balanced"

    def summary(self) -> str:
        return (
            f"ICTM(model={self.model_tier}, tools={self.tools}, "
            f"instruction={self.instruction[:80]}...)"
        )
