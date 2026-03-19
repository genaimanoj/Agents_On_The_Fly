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
    sandboxed: bool = False  # Run sub-agent in an isolated temp environment

    def summary(self) -> str:
        sbx = " [SANDBOXED]" if self.sandboxed else ""
        return (
            f"ICTM(model={self.model_tier}, tools={self.tools}, "
            f"instruction={self.instruction[:80]}...){sbx}"
        )
