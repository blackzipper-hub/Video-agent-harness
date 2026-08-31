from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class AgentRole(StrEnum):
    DIRECTOR = "director"
    PLANNER = "planner"
    CREATIVE = "creative"
    VISUAL = "visual"
    AUDIO = "audio"
    EDITOR = "editor"
    QUALITY = "quality"


@dataclass(frozen=True)
class SpecialistAgent:
    """A bounded creative role exposed to Planner and Skills.

    This is deliberately a policy object rather than another uncontrolled LLM
    loop. The Planner decides when to consult a role; all execution still goes
    through a validated Capability.
    """

    role: AgentRole
    responsibility: str
    allowed_capability_prefixes: tuple[str, ...]

    def accepts(self, capability_id: str) -> bool:
        return any(capability_id.startswith(prefix) for prefix in self.allowed_capability_prefixes)

    def prompt_context(self) -> Mapping[str, object]:
        return {
            "role": self.role.value,
            "responsibility": self.responsibility,
            "allowed_capability_prefixes": self.allowed_capability_prefixes,
        }
