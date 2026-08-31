"""Creative-agent boundaries for the dynamic video platform.

The package intentionally contains orchestration-facing roles, not provider
implementations. Provider calls remain capabilities/tools so every role can be
extended by a Skill without gaining implicit execution authority.
"""

from .base import AgentRole, SpecialistAgent

__all__ = ["AgentRole", "SpecialistAgent"]
