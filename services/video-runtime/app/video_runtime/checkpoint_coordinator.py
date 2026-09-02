from __future__ import annotations

import asyncio
import json
import logging

from .deepseek_client import DeepSeekHarnessClient
from .models import PlanCheckpoint
from .runtime import VideoBuildRuntime


logger = logging.getLogger(__name__)


def checkpoint_prompt(checkpoint: PlanCheckpoint) -> str:
    """Build an auditable continuation request without exposing hidden reasoning."""
    payload = {
        "project_id": checkpoint.project_id,
        "build_id": checkpoint.build_id,
        "checkpoint_id": checkpoint.id,
        "workflow_id": checkpoint.workflow_id,
        "completed_phase": checkpoint.phase,
        "next_phase": checkpoint.next_phase,
        "base_plan_revision": checkpoint.base_plan_revision,
        "base_spec_revision": checkpoint.base_spec_revision,
        "resolved_sections": checkpoint.resolved_sections,
        "unresolved_sections": checkpoint.unresolved_sections,
        "artifact_summaries": checkpoint.artifact_summaries,
        "planner_instruction": checkpoint.planner_instruction,
        "planning_mode": checkpoint.planning_mode,
        "delivery_id": checkpoint.delivery_id,
    }
    return "\n".join([
        "CUTI_VIDEO_CHECKPOINT_V1",
        "A durable video build reached a semantic planning checkpoint.",
        "This is an automatic continuation of the same user request and Session.",
        "Do not create a project, switch Workflow, or repeat completed media steps.",
        "Call video_workflow_load for the exact workflow_id below, then call "
        "video_checkpoint_inspect with the exact project/build/checkpoint ids.",
        "Use the real artifact summaries to complete the unresolved creative fields.",
        "Then call video_checkpoint_resolve once with either a complete revised VideoSpec "
        "or a video_spec_patch that completes this phase, the exact base revisions, and "
        "idempotency_key checkpoint:<delivery_id>.",
        "For staged workflows do not propose BuildSteps; the Workflow compiler owns them. "
        "Only agentic workflows may provide proposed_steps within allowed capabilities.",
        "Do not ask the user for confirmation unless the checkpoint explicitly says so.",
        "Checkpoint payload:",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
    ])


class CheckpointCoordinator:
    """Reliable checkpoint-to-Session delivery; it is not an Agent or planner."""

    def __init__(
        self,
        runtime: VideoBuildRuntime,
        deepseek: DeepSeekHarnessClient,
        *,
        poll_seconds: float = 1.0,
        lease_seconds: int = 900,
    ) -> None:
        self.runtime = runtime
        self.deepseek = deepseek
        self.poll_seconds = poll_seconds
        self.lease_seconds = lease_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="video-checkpoint-coordinator")

    async def close(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def run_once(self) -> bool:
        checkpoint = await self.runtime.repo.claim_pending_checkpoint(self.lease_seconds)
        if checkpoint is None:
            return False
        if checkpoint.delivery_attempts > checkpoint.max_delivery_attempts:
            await self.runtime.repo.fail_checkpoint_delivery(
                checkpoint.id,
                "automatic Agent continuation was not resolved after three deliveries",
            )
            return True
        try:
            await self.deepseek.prompt(
                checkpoint.session_id,
                checkpoint_prompt(checkpoint),
                mode="queue",
            )
        except Exception as exc:
            await self.runtime.repo.fail_checkpoint_delivery(checkpoint.id, str(exc))
            logger.exception("Failed to deliver video checkpoint %s", checkpoint.id)
        return True

    async def _run(self) -> None:
        while True:
            try:
                handled = await self.run_once()
                if not handled:
                    await asyncio.sleep(self.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Video checkpoint coordinator iteration failed")
                await asyncio.sleep(self.poll_seconds)
