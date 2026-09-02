"""Phase selection for semantic-checkpoint video builds."""

from __future__ import annotations

from copy import deepcopy
from typing import Iterable

from app.orchestration.workflow_compiler.registry import WorkflowSpec

from .initial_build import BuildPlanValidationError, topological_steps
from .models import (
    PlanCheckpointDefinition,
    ProjectIntent,
    RebuildPlan,
    RebuildPlanItem,
    VideoSpec,
)
from .plugins import PluginContext


MUSIC_MODES = {"mv", "seedance_mv"}
PRODUCT_MODES = {
    "product_ad_video", "cuti_product_workflow",
    "cuti_scenario_product_workflow", "libtv_product_workflow",
}
DIRECT_MODES = {"direct_video", "seedance2"}
STORY_MODES = {"keyframe_pipeline", "short_drama", "short_drama_workflow"}


def _remap_step_references(value, remap: dict[str, str]):
    if isinstance(value, str):
        return remap.get(value, value)
    if isinstance(value, list):
        return [_remap_step_references(item, remap) for item in value]
    if isinstance(value, dict):
        return {
            key: _remap_step_references(item, remap)
            for key, item in value.items()
        }
    return value


def effective_planning_mode(workflow: WorkflowSpec | None, workflow_id: str) -> str:
    """Return explicit Skill policy, with staged defaults for built-in workflows."""
    if workflow is not None and workflow.planning.mode != "full":
        return workflow.planning.mode
    if workflow_id == "cuti.seedance-story":
        return "staged"
    if workflow_id in {"cuti.music-video", "cuti.lipsync-music-video"}:
        return "staged"
    return workflow.planning.mode if workflow is not None else "full"


def initial_checkpoint(workflow: WorkflowSpec | None, workflow_id: str) -> PlanCheckpointDefinition:
    mode = workflow.mode if workflow is not None else {
        "cuti.seedance-story": "keyframe_pipeline",
        "cuti.music-video": "seedance_mv",
        "cuti.lipsync-music-video": "seedance_mv",
    }.get(workflow_id, "direct_video")
    planning_mode = effective_planning_mode(workflow, workflow_id)
    if workflow is not None and workflow.planning.checkpoints:
        declared = workflow.planning.checkpoints[0]
        return PlanCheckpointDefinition(
            id=declared.id,
            phase=declared.after_phase,
            next_phase=declared.next_phase,
            required_artifact_types=list(declared.required_artifacts),
            resolves=list(declared.resolves),
            planner_instruction=declared.instruction,
            planning_mode="agentic" if planning_mode == "agentic" else "staged",
        )
    if mode in MUSIC_MODES:
        return PlanCheckpointDefinition(
            id="music_ready", phase="music_analysis", next_phase="visual_production",
            required_artifact_types=["audiomap", "audio_cut"],
            resolves=["shots", "captions", "timeline"],
            planner_instruction=(
                "Use the real music duration, beat map, lyrics, and cut window to write the "
                "complete VideoSpec. Do not invent timing that conflicts with the artifacts."
            ),
        )
    if mode in PRODUCT_MODES:
        return PlanCheckpointDefinition(
            id="product_ready", phase="source_analysis", next_phase="visual_production",
            required_artifact_types=["product_analysis", "source_image"],
            resolves=["characters", "shots", "product_constraints"],
            planner_instruction=(
                "Inspect the actual product source artifacts and create shots that preserve "
                "product identity and verified features."
            ),
        )
    if mode in STORY_MODES:
        return PlanCheckpointDefinition(
            id="story_ready", phase="story_intent", next_phase="reference_production",
            required_artifact_types=["story_draft"],
            resolves=["characters", "shots", "audio"],
            planner_instruction=(
                "Turn the intent into a complete story VideoSpec. Lock character identity, "
                "scene continuity, shot order, and durations before reference generation."
            ),
        )
    return PlanCheckpointDefinition(
        id="creative_ready", phase="creative_intent", next_phase="visual_production",
        required_artifact_types=["project_intent"],
        resolves=["shots", "references"],
        planner_instruction=(
            "Choose the concrete video actions allowed by this Workflow and produce a complete "
            "VideoSpec. Keep Seedance native audio and do not add unrelated production stages."
        ),
        planning_mode="agentic" if planning_mode == "agentic" else "staged",
    )


def compile_initial_phase(
    *,
    workflow: WorkflowSpec | None,
    context: PluginContext,
    intent: ProjectIntent,
) -> RebuildPlan:
    """Compile only work that can run before later creative facts exist."""
    if not context.project_id:
        raise BuildPlanValidationError("staged workflow requires a project")
    project_id = context.project_id
    sources = dict(context.values.get("source_artifacts") or {})
    items: list[RebuildPlanItem] = []

    def add(
        step_id: str,
        artifact_type: str,
        capability: str,
        *,
        parameters: dict | None = None,
        depends_on: list[str] | None = None,
        action: str = "create",
        artifact_version_id: str = "",
        cost: float = 0,
    ) -> str:
        items.append(RebuildPlanItem(
            step_id=step_id,
            artifact_version_id=artifact_version_id,
            output_artifact_id=f"{project_id}:{step_id}",
            output_artifact_type=artifact_type,
            action=action,
            capability=capability,
            parameters=parameters or {},
            depends_on=depends_on or [],
            estimated_cost=cost,
            order=len(items) + 1,
            reason="Staged Workflow initial phase",
        ))
        return step_id

    intent_step = add(
        "intent", "project_intent", "runtime.artifact.persist",
        parameters={"title": intent.title, "content": intent.model_dump(mode="json")},
    )
    source_steps: dict[str, str] = {}
    for index, logical_id in enumerate(intent.source_asset_ids, start=1):
        artifact = sources.get(logical_id)
        if artifact is None:
            raise BuildPlanValidationError(f"source artifact is unavailable: {logical_id}")
        source_steps[logical_id] = add(
            f"source-{index}", artifact.type, "", action="reuse",
            artifact_version_id=artifact.id,
        )

    checkpoint = initial_checkpoint(workflow, intent.workflow_id)
    mode = workflow.mode if workflow is not None else {
        "cuti.music-video": "seedance_mv",
        "cuti.lipsync-music-video": "seedance_mv",
    }.get(intent.workflow_id, "")
    if mode in MUSIC_MODES:
        audio_sources = [
            source_steps[logical_id]
            for logical_id in intent.source_asset_ids
            if sources[logical_id].type in {"source_audio", "audio", "audio_bgm"}
        ]
        if audio_sources:
            music = audio_sources[0]
        else:
            prompt = str(
                intent.workflow_parameters.get("music_prompt")
                or intent.workflow_parameters.get("bgm_prompt")
                or intent.brief
            ).strip()
            music = add(
                "music", "audio_bgm", "suno.generate",
                parameters={
                    "prompt": prompt,
                    "title": intent.title,
                    **{
                        key: value for key, value in intent.workflow_parameters.items()
                        if key in {"tags", "lyrics", "custom_mode", "instrumental", "vocal_gender"}
                    },
                },
                depends_on=[intent_step], cost=0.10,
            )
        analysis = add(
            "music-analysis", "audiomap", "media.audio_analyze",
            parameters={
                "audio_step": music,
                "target_duration_sec": intent.target_duration_seconds,
                "transcribe": True,
            },
            depends_on=[music],
        )
        add(
            "music-cut", "audio_cut", "media.audio_cut",
            parameters={
                "audio_step": music,
                "analysis_step": analysis,
                "duration": intent.target_duration_seconds,
            },
            depends_on=[music, analysis],
        )
    elif mode in PRODUCT_MODES:
        image_sources = [
            source_steps[logical_id]
            for logical_id in intent.source_asset_ids
            if sources[logical_id].type in {"source_image", "image", "character_reference"}
        ]
        if not image_sources:
            raise BuildPlanValidationError("product workflows require a source image")
        add(
            "product-analysis", "product_analysis", "atomic.text.generate",
            parameters={
                "objective": intent.brief,
                "source_artifact_ids": list(intent.source_asset_ids),
                "instruction": (
                    "Extract only visually supported product identity, features, branding, "
                    "materials, and constraints. Mark unknown claims as unknown."
                ),
            },
            depends_on=[intent_step, *image_sources],
        )
    elif mode in STORY_MODES:
        add(
            "story-draft", "story_draft", "atomic.text.generate",
            parameters={
                "objective": intent.brief,
                "target_duration_seconds": intent.target_duration_seconds,
                "language": intent.language,
                "instruction": (
                    "Produce a concise story/script draft only. Do not invent provider "
                    "operations or execute later production phases."
                ),
            },
            depends_on=[intent_step],
        )

    topological_steps(items)
    return RebuildPlan(
        project_id=project_id,
        kind="initial",
        base_project_version_id=str(context.values.get("base_project_version_id") or ""),
        workflow_id=intent.workflow_id,
        project_intent=intent,
        items=items,
        schema_version=2,
        current_revision=1,
        current_phase=checkpoint.phase,
        next_checkpoint=checkpoint,
        estimated_cost=round(sum(item.estimated_cost for item in items), 6),
    )


def next_checkpoint_for(
    workflow: WorkflowSpec | None,
    workflow_id: str,
    checkpoint_id: str,
) -> PlanCheckpointDefinition | None:
    if workflow is not None and workflow.planning.checkpoints:
        declared = list(workflow.planning.checkpoints)
        current = next((
            index for index, item in enumerate(declared)
            if item.id == checkpoint_id or item.after_phase == checkpoint_id
        ), -1)
        if current >= 0 and current + 1 < len(declared):
            value = declared[current + 1]
            return PlanCheckpointDefinition(
                id=value.id,
                phase=value.after_phase,
                next_phase=value.next_phase,
                required_artifact_types=list(value.required_artifacts),
                resolves=list(value.resolves),
                planner_instruction=value.instruction,
                planning_mode=(
                    "agentic" if effective_planning_mode(workflow, workflow_id) == "agentic"
                    else "staged"
                ),
            )
    return None


def append_phase(
    *,
    existing_plan: RebuildPlan,
    full_plan: RebuildPlan,
    workflow: WorkflowSpec | None,
    checkpoint_id: str,
    proposed_steps: Iterable[RebuildPlanItem] = (),
) -> tuple[list[RebuildPlanItem], PlanCheckpointDefinition | None]:
    """Select the next safe phase from a complete compiler result."""
    existing_ids = {item.step_id for item in existing_plan.items}
    if checkpoint_id == "semantic_validation":
        immutable_types = {
            "project_intent", "video_spec", "script", "characters", "storyboard",
            "outline", "scenes", "shots", "source_image", "source_audio",
            "source_video", "audiomap", "audio_cut",
        }
        candidates = [
            item.model_copy(deep=True) for item in full_plan.items
            if (
                item.action == "validate"
                or (item.action == "create" and item.output_artifact_type not in immutable_types)
            )
        ]
        remap = {item.step_id: f"{item.step_id}-repair-1" for item in candidates}
        for item in candidates:
            original_id = item.step_id
            item.step_id = remap[original_id]
            item.depends_on = [remap.get(value, value) for value in item.depends_on]
            item.parameters = _remap_step_references(item.parameters, remap)
            item.idempotency_key = ""
            item.reason = f"One-time semantic repair of {original_id}"
        known = existing_ids | {item.step_id for item in candidates}
        for item in candidates:
            missing = sorted(set(item.depends_on) - known)
            if missing:
                raise BuildPlanValidationError(
                    f"repair step {item.step_id} has unknown dependencies: {missing}"
                )
        topological_steps([*existing_plan.items, *candidates])
        return candidates, None
    proposed = [item.model_copy(deep=True) for item in proposed_steps]
    if proposed:
        if effective_planning_mode(workflow, existing_plan.workflow_id) != "agentic":
            raise BuildPlanValidationError("only agentic workflows accept proposed_steps")
        allowed = workflow.allowed_capabilities if workflow is not None else None
        for item in proposed:
            if item.step_id in existing_ids:
                raise BuildPlanValidationError(f"plan step already exists: {item.step_id}")
            if allowed is not None and item.capability not in allowed:
                raise BuildPlanValidationError(
                    f"workflow does not allow capability {item.capability}"
                )
            if item.action not in {"create", "validate"}:
                raise BuildPlanValidationError("agentic phases may only create or validate artifacts")
        known = existing_ids | {item.step_id for item in proposed}
        for item in proposed:
            missing = sorted(set(item.depends_on) - known)
            if missing:
                raise BuildPlanValidationError(
                    f"agentic step {item.step_id} has unknown dependencies: {missing}"
                )
        topological_steps([*existing_plan.items, *proposed])
        return proposed, next_checkpoint_for(workflow, existing_plan.workflow_id, checkpoint_id)

    remaining = [
        item.model_copy(deep=True) for item in full_plan.items
        if item.step_id not in existing_ids
    ]
    next_checkpoint = next_checkpoint_for(workflow, existing_plan.workflow_id, checkpoint_id)
    if next_checkpoint is not None:
        future_phase = next_checkpoint.phase
        if future_phase == "reference_production":
            selected = [
                item for item in remaining
                if item.step_id in {"spec", "script", "characters", "storyboard", "outline", "scenes", "shots", "narration", "bgm"}
                or (item.step_id.startswith("character-") and item.step_id.endswith("-reference"))
            ]
        elif future_phase == "keyframe_production":
            # The original Cuti continuity graph feeds each clip's real tail into
            # the next shot's keyframe. Producing every keyframe up front would
            # silently remove that contract, so this semantic checkpoint uses the
            # first real keyframe as the visual anchor. Remaining keyframes stay in
            # the deterministic video phase, where their tail dependencies exist.
            first_keyframe = next((
                item for item in remaining if item.output_artifact_type == "keyframe"
            ), None)
            selected = [first_keyframe] if first_keyframe is not None else []
        else:
            selected = remaining
    else:
        selected = remaining
    selected_ids = existing_ids | {item.step_id for item in selected}
    for item in selected:
        missing = sorted(set(item.depends_on) - selected_ids)
        if missing:
            raise BuildPlanValidationError(
                f"phase step {item.step_id} depends on a later phase: {missing}"
            )
    topological_steps([*existing_plan.items, *selected])
    return selected, next_checkpoint


def completed_spec_revision_sections(spec: VideoSpec) -> list[str]:
    return [
        "title", "format", "workflow", "style", "characters", "shots", "audio",
        "providers", "automation", "timeline",
    ]


def copy_plan_with_appended_phase(
    plan: RebuildPlan,
    *,
    spec: VideoSpec,
    added_items: list[RebuildPlanItem],
    spec_revision_id: str,
    next_checkpoint: PlanCheckpointDefinition | None,
) -> RebuildPlan:
    updated = deepcopy(plan)
    updated.items.extend(added_items)
    updated.video_spec = spec
    updated.video_spec_revision_id = spec_revision_id
    updated.current_revision += 1
    updated.current_phase = (
        next_checkpoint.phase if next_checkpoint is not None else "final_production"
    )
    updated.next_checkpoint = next_checkpoint
    updated.estimated_cost = round(
        sum(item.estimated_cost for item in updated.items), 6,
    )
    topological_steps(updated.items)
    return updated
