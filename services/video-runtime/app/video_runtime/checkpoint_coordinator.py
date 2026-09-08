from __future__ import annotations

import asyncio
import json
import logging

from .deepseek_client import DeepSeekHarnessClient
from .models import PlanCheckpoint
from .runtime import VideoBuildRuntime


logger = logging.getLogger(__name__)


async def refresh_checkpoint(runtime: VideoBuildRuntime, checkpoint: PlanCheckpoint) -> PlanCheckpoint:
    """Project durable task results into a delivery without changing revision ownership."""
    if checkpoint.planning_mode != "agentic":
        return checkpoint
    from .runtime import _checkpoint_artifact_summary, _checkpoint_task_parameters
    plan = await runtime.repo.get_plan(checkpoint.plan_id)
    planned_by_id = {item.step_id: item for item in plan.items}
    states = await runtime.repo.list_build_steps(checkpoint.project_id, checkpoint.build_id)
    artifacts = [await runtime.repo.get_artifact(checkpoint.project_id, item.result_artifact_version_id)
                 for item in states if item.status == "completed" and item.result_artifact_version_id]
    snapshot = []
    for item in states:
        planned = planned_by_id.get(item.plan_step_id)
        snapshot.append({
            "step_id": item.plan_step_id,
            "objective": planned.objective if planned else "",
            "capability": planned.capability if planned else "",
            "depends_on": planned.depends_on if planned else [],
            "parameters": _checkpoint_task_parameters(planned.parameters if planned else {}),
            "status": item.status,
            "error": item.error,
            "artifact_version_id": item.result_artifact_version_id,
        })
    instruction = checkpoint.planner_instruction.split("Task snapshot at notification time:", 1)[0]
    return checkpoint.model_copy(update={
        "artifact_version_ids": [item.id for item in artifacts],
        "artifact_summaries": [_checkpoint_artifact_summary(item) for item in artifacts],
        "planner_instruction": instruction + "Current task snapshot (supersedes earlier notifications):\n"
        + json.dumps(snapshot, ensure_ascii=False),
    })


def delivered_turn_ended(checkpoint: PlanCheckpoint, history: dict) -> bool:
    """Only a consumed checkpoint message followed by its turn end permits recovery."""
    consumed = False
    ended = False
    for entry in history.get("events", []):
        event = entry.get("event", {})
        if event.get("time", 0) < checkpoint.updated_at.timestamp() * 1000:
            continue
        if event.get("type") == "user/message":
            content = event.get("data", {}).get("content", [])
            text = "\n".join(item.get("text", "") for item in content if item.get("type") == "text")
            if text.startswith("CUTI_VIDEO_CHECKPOINT_V1") and checkpoint.id in text:
                consumed = True
                ended = False
        elif event.get("type") == "turn/start":
            # Do not interrupt a later turn that may already be resolving the checkpoint.
            ended = False
        elif event.get("type") == "turn/end" and consumed:
            ended = True
    return consumed and ended


def checkpoint_prompt(
    checkpoint: PlanCheckpoint,
    language_contract: dict[str, str] | None = None,
) -> str:
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
        "delivery_attempt": checkpoint.delivery_attempts,
        "previous_delivery_error": checkpoint.error,
    }
    if checkpoint.planning_mode == "agentic":
        instructions = [
            "This build uses Cuti continuous PlanPatch semantics. The Workflow is a "
            "capability and creative-policy boundary, not a precompiled DAG.",
            "Inspect the real completed Artifacts before deciding what comes next. Call "
            "video_plan_patch_submit with a partial video_spec_patch and add_tasks containing "
            "the next tasks and their dependencies. Use cancel_task_ids to cancel only pending work.",
            "Dependencies may refer to existing tasks or tasks in the same patch. Runtime only "
            "starts ready tasks. Each task completion or failure requests another planning turn "
            "without waiting for unrelated running tasks. Multiple updates during a planning "
            "turn are coalesced into the next durable snapshot. An empty patch acknowledges "
            "the snapshot while existing tasks continue.",
            "Before ending this turn you MUST submit video_plan_patch_submit, even with "
            "add_tasks=[] when waiting for existing work. A text-only promise to continue "
            "does not acknowledge this checkpoint. Read the current task snapshot: do not "
            "assume an Artifact is still generating because an earlier message said so.",
            "Only set goal_satisfied=true when the final playable deliverable exists, never "
            "merely because work was queued. Do not add tasks in the completion patch; include "
            "any VideoSpec facts learned so far as a final partial patch.",
            "For every video-generation task, parameters.prompt is the final prompt sent to "
            "the provider, not a short synopsis. Make it duration-aware and preserve all "
            "relevant concrete subject identity, wardrobe/product, setting, lighting, camera, "
            "motion, action/performance, dialogue/native audio, continuity start/end state, "
            "visual style, exclusions, and user constraints. Inspect prior task parameters.prompt "
            "and Artifact metadata.generation_context. Later or repaired clips must retain "
            "comparable specificity instead of collapsing into plot-only bullets. Adapt the "
            "detail and temporal beats to the requested duration; do not assume a fixed clip count.",
            "When a video uses generated identity, character, product, scene, keyframe, or continuity "
            "references, name the producing task ids in both depends_on and the appropriate "
            "reference_from_steps/start_image_from_step parameter. For continuity_mode "
            "shared_reference_images, all shared identity and setting sheets must reach every segment; "
            "a textual phrase such as 'the same character' is not a media reference.",
        ]
    else:
        instructions = [
            "Use the real artifact summaries to complete the unresolved creative fields.",
            "Then call video_checkpoint_resolve once with either a complete revised VideoSpec "
            "or a video_spec_patch that completes this phase, the exact base revisions, and "
            "idempotency_key checkpoint:<delivery_id>.",
            "Do not propose BuildSteps; the staged Workflow compiler owns them.",
        ]
    language_instruction = ""
    if language_contract:
        from app.chat.v2.language import video_language_instruction

        language_instruction = video_language_instruction(language_contract)
    return "\n".join(filter(None, [
        "CUTI_VIDEO_CHECKPOINT_V1",
        "A durable video build reached a semantic planning checkpoint.",
        "This is an automatic continuation of the same user request and Session.",
        language_instruction,
        "Do not create a project, switch Workflow, or repeat completed media steps.",
        "Call video_workflow_load for the exact workflow_id below, then call "
        "video_checkpoint_inspect with the exact project/build/checkpoint ids.",
        "From video_workflow_load, call video_skill_load for every returned "
        "skillDependencies entry and read every referenced bundled resource required by "
        "the Workflow before resolving the checkpoint.",
        *instructions,
        "Do not ask the user for confirmation unless the checkpoint explicitly says so.",
        "Checkpoint payload:",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        "ACTION REQUIRED IN THIS TURN: call video_checkpoint_inspect now with "
        + json.dumps({"project_id": checkpoint.project_id, "build_id": checkpoint.build_id,
                      "checkpoint_id": checkpoint.id}, ensure_ascii=False)
        + ". Then submit "
        + ("video_plan_patch_submit (base_revision is base_plan_revision above)" if checkpoint.planning_mode == "agentic"
           else "video_checkpoint_resolve")
        + ". Do not end with a promise to execute later. The task remains blocked until the tool accepts your patch.",
    ]))


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
            locks = getattr(self.runtime, "session_control_locks", None)
            lock = (
                locks.setdefault(checkpoint.project_id, asyncio.Lock())
                if locks is not None else asyncio.Lock()
            )
            async with lock:
                get_build = getattr(self.runtime.repo, "get_build", None)
                if callable(get_build):
                    build = await get_build(checkpoint.project_id, checkpoint.build_id)
                    if build.status in {"cancelled", "failed", "completed"}:
                        return True
                checkpoint = await refresh_checkpoint(self.runtime, checkpoint)
                get_plan = getattr(self.runtime.repo, "get_plan", None)
                language_values = None
                if callable(get_plan):
                    plan = await get_plan(checkpoint.plan_id)
                    language_source = plan.video_spec or plan.project_intent
                    language_values = (
                        language_source.language_contract.model_dump(mode="json")
                        if language_source is not None else None
                    )
                await self.deepseek.prompt(
                    checkpoint.session_id,
                    checkpoint_prompt(checkpoint, language_values),
                    mode="queue",
                )
        except Exception as exc:
            await self.runtime.repo.fail_checkpoint_delivery(checkpoint.id, str(exc))
            logger.exception("Failed to deliver video checkpoint %s", checkpoint.id)
        return True

    async def reconcile_finished_turns(self) -> None:
        for checkpoint in await self.runtime.repo.list_planning_checkpoints():
            try:
                history = await self.deepseek.history(checkpoint.session_id, max_messages=100)
                if not delivered_turn_ended(checkpoint, history):
                    continue
                lock = self.runtime.session_control_locks.setdefault(checkpoint.project_id, asyncio.Lock())
                async with lock:
                    await self.runtime.repo.fail_checkpoint_delivery(
                        checkpoint.id, "Agent turn ended without submitting a PlanPatch; refreshing task state",
                        expected_attempt=checkpoint.delivery_attempts,
                    )
            except Exception:
                logger.exception("Failed to reconcile checkpoint %s", checkpoint.id)

    async def _run(self) -> None:
        while True:
            try:
                await self.reconcile_finished_turns()
                handled = await self.run_once()
                if not handled:
                    await asyncio.sleep(self.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Video checkpoint coordinator iteration failed")
                await asyncio.sleep(self.poll_seconds)
