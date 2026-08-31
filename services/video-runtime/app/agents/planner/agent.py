from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.agents.base import AgentRole, SpecialistAgent
from app.agents.audio.agent import AUDIO_AGENT
from app.agents.creative.agent import CREATIVE_AGENT
from app.agents.director.agent import DIRECTOR_AGENT
from app.agents.editor.agent import EDITOR_AGENT
from app.agents.quality.agent import QUALITY_AGENT
from app.agents.visual.agent import VISUAL_AGENT
from app.chat.v2.capabilities import CapabilityRegistry
from app.chat.v2.deep_agent_runtime import DeepAgentRuntime, V2AgentContext
from app.chat.v2.skill_catalog import SkillCatalog
from app.chat.v2.tools import CoordinatorServices, build_coordinator_tools


class PlannerAgent(SpecialistAgent):
    """The sole LLM planning entrypoint for a project task graph.

    It owns tool registration and role visibility. It does not execute media;
    the Harness validates every PlanPatch and dispatches a Capability.
    """

    def __init__(
        self,
        runtime: DeepAgentRuntime,
        capabilities: CapabilityRegistry,
        skills: SkillCatalog | None = None,
        script_runner: Any | None = None,
    ) -> None:
        super().__init__(
            AgentRole.PLANNER,
            "Compiles user intent and Skills into a validated, revisable task graph.",
            ("*",),
        )
        self.runtime = runtime
        self.capabilities = capabilities
        self.skills = skills
        self.script_runner = script_runner
        self.specialists = (
            DIRECTOR_AGENT, CREATIVE_AGENT, VISUAL_AGENT, AUDIO_AGENT,
            EDITOR_AGENT, QUALITY_AGENT,
        )

    async def initialize(self, host: CoordinatorServices) -> None:
        await self.runtime.initialize(build_coordinator_tools(
            host, self.capabilities, self.skills, self.script_runner,
        ))

    async def close(self) -> None:
        await self.runtime.close()

    def role_context(self) -> str:
        return "\n".join(
            f"- {agent.role.value}: {agent.responsibility}"
            for agent in self.specialists
        )

    def stream(
        self, message: str, *, thread_id: str, context: V2AgentContext,
        callbacks: list[Any] | None = None,
    ) -> AsyncIterator[dict]:
        if self.runtime.graph is None:
            raise RuntimeError("Planner Agent is not initialized")
        enriched = (
            f"{message}\n\nAvailable specialist roles (advisory only):\n"
            f"{self.role_context()}"
        )
        return self.runtime.graph.astream_events(
            self.runtime.invocation_input(enriched),
            self.runtime.config(thread_id, callbacks), context=context, version="v2",
        )
