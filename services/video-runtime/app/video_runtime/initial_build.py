from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable

from .models import (
    CheckpointResolution,
    PlanCheckpoint,
    ProjectIntent,
    RebuildPlan,
    RebuildPlanItem,
    VideoSpec,
)
from .plugins import BaseVideoPlugin, PluginContext


class BuildPlanValidationError(ValueError):
    pass


def topological_steps(items: Iterable[RebuildPlanItem]) -> list[RebuildPlanItem]:
    """Return a stable topological order and reject missing or cyclic dependencies."""
    steps = list(items)
    by_id = {item.step_id: item for item in steps}
    if len(by_id) != len(steps):
        raise BuildPlanValidationError("build step ids must be unique")
    missing = sorted({dep for item in steps for dep in item.depends_on if dep not in by_id})
    if missing:
        raise BuildPlanValidationError(f"build step dependencies do not exist: {', '.join(missing)}")
    incoming = {item.step_id: len(set(item.depends_on)) for item in steps}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for item in steps:
        for dependency in set(item.depends_on):
            outgoing[dependency].append(item.step_id)
    queue = deque(sorted(
        (step_id for step_id, count in incoming.items() if count == 0),
        key=lambda value: (by_id[value].order or 0, value),
    ))
    ordered: list[RebuildPlanItem] = []
    while queue:
        step_id = queue.popleft()
        ordered.append(by_id[step_id])
        for target in outgoing[step_id]:
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
        queue = deque(sorted(queue, key=lambda value: (by_id[value].order or 0, value)))
    if len(ordered) != len(steps):
        raise BuildPlanValidationError("build plan contains a dependency cycle")
    return ordered


class SeedanceStoryWorkflow:
    """Compile a VideoSpec; provider and media details stay behind capabilities."""

    id = "cuti.seedance-story"

    def compile(
        self,
        *,
        project_id: str,
        base_project_version_id: str,
        spec: VideoSpec,
    ) -> RebuildPlan:
        if spec.workflow_id != self.id:
            raise BuildPlanValidationError(f"unsupported workflow: {spec.workflow_id}")
        items: list[RebuildPlanItem] = []
        style_skills = {
            "cuti.cinematic": ["cinematic", "video-gen-prompting"],
        }.get(spec.style_id, [])
        image_model = {
            "wavespeed": "gpt-image-2",
            "openai": "gpt-image-2",
        }.get(spec.providers.image, spec.providers.image)

        def add(
            step_id: str,
            artifact_type: str,
            capability: str,
            *,
            parameters: dict | None = None,
            depends_on: list[str] | None = None,
            cost: float = 0.0,
            skills: list[str] | None = None,
        ) -> str:
            items.append(RebuildPlanItem(
                step_id=step_id,
                output_artifact_id=f"{project_id}:{step_id}",
                output_artifact_type=artifact_type,
                action="create",
                capability=capability,
                parameters=parameters or {},
                depends_on=depends_on or [],
                estimated_cost=cost,
                order=len(items) + 1,
                reason="Initial video build",
                skill_ids=skills or [],
            ))
            return step_id

        spec_step = add("spec", "video_spec", "runtime.artifact.persist", parameters={
            "title": spec.title, "content": spec.model_dump(mode="json"),
        })
        script_step = add("script", "script", "runtime.artifact.persist", parameters={
            "title": f"{spec.title} script",
            "content": [{"shotId": shot.id, "beat": shot.beat, "narration": shot.narration} for shot in spec.shots],
        }, depends_on=[spec_step])
        character_manifest = add(
            "characters", "characters", "runtime.artifact.persist",
            parameters={"title": "Characters", "content": [item.model_dump() for item in spec.characters]},
            depends_on=[spec_step],
        )
        storyboard = add(
            "storyboard", "storyboard", "runtime.artifact.persist",
            parameters={"title": "Storyboard", "content": [item.model_dump() for item in spec.shots]},
            depends_on=[script_step, character_manifest],
        )
        character_references: dict[str, str] = {}
        for character in spec.characters:
            character_references[character.id] = add(
                f"character-{character.id}-reference", "character_reference", "atomic.image.generate",
                parameters={
                    "prompt": (
                        f"Consistent production character reference sheet for {character.name}. "
                        f"Appearance: {character.appearance}; clothing: {character.clothing}; "
                        f"personality: {character.personality}"
                    ),
                    "model": image_model,
                    "aspect_ratio": spec.aspect_ratio,
                    "style_id": spec.style_id,
                    "character_id": character.id,
                },
                depends_on=[character_manifest], cost=0.08,
                skills=[
                    "character-director",
                    "character-image-tool-director",
                    *style_skills,
                ],
            )

        narration_text = "\n".join(shot.narration for shot in spec.shots if shot.narration).strip()
        narration = ""
        if narration_text:
            narration = add(
                "narration", "audio_narration", "media.tts",
                parameters={"text": narration_text, "voice_id": spec.audio.narration_voice},
                depends_on=[script_step], cost=0.05,
            )
        bgm = ""
        if spec.audio.bgm_prompt:
            bgm = add(
                "bgm", "audio_bgm", "atomic.music.generate",
                parameters={
                    "prompt": spec.audio.bgm_prompt,
                    "duration": spec.target_duration_seconds,
                    "target_duration": spec.target_duration_seconds,
                    "model": spec.providers.music,
                },
                depends_on=[spec_step], cost=0.10,
                skills=["music-director", "music-bgm-director"],
            )

        clip_steps: list[str] = []
        previous_tail = ""
        for shot in spec.shots:
            frame_dependencies = [storyboard]
            shot_character_references = [
                character_references[item]
                for item in shot.character_ids
                if item in character_references
            ]
            frame_dependencies.extend(shot_character_references)
            if previous_tail:
                frame_dependencies.append(previous_tail)
            keyframe = add(
                f"shot-{shot.id}-keyframe", "keyframe", "atomic.image.generate",
                parameters={
                    "prompt": shot.visual_prompt,
                    "model": image_model,
                    "aspect_ratio": spec.aspect_ratio,
                    "style_id": spec.style_id,
                    "shot_id": shot.id,
                    "strict_start_frame_from_step": previous_tail or None,
                },
                depends_on=frame_dependencies, cost=0.08,
                skills=[
                    "keyframe-director",
                    "keyframe-tool-director",
                    *style_skills,
                ],
            )
            video_dependencies = [keyframe]
            video_dependencies.extend(shot_character_references)
            clip = add(
                f"shot-{shot.id}-video", "video_clip", "atomic.video.generate",
                parameters={
                    "prompt": shot.visual_prompt,
                    "duration": shot.duration_seconds,
                    "model": spec.providers.video,
                    "shot_id": shot.id,
                    "start_image_from_step": keyframe,
                    "character_reference_from_steps": shot_character_references,
                },
                depends_on=video_dependencies, cost=0.65,
                skills=[
                    "video-director",
                    "video-tool-director",
                    *style_skills,
                ],
            )
            clip_steps.append(clip)
            previous_tail = add(
                f"shot-{shot.id}-tail", "continuity_frame", "media.extract_frame",
                parameters={"position": "last", "source_video_step": clip},
                depends_on=[clip],
            )

        timeline = add(
            "timeline", "timeline", "media.timeline.compose",
            parameters={
                "shots": [shot.model_dump(mode="json") for shot in spec.shots],
                "video_steps": clip_steps,
            },
            depends_on=clip_steps,
        )
        assembled = add(
            "assembled-video", "video_assembled", "media.concat",
            parameters={"video_steps": clip_steps}, depends_on=clip_steps, cost=0.02,
        )
        mixed = assembled
        for audio_step, mode, volume in ((narration, "replace", 1.0), (bgm, "overlay", 0.25)):
            if audio_step:
                mixed = add(
                    f"mix-{audio_step}", "video_mixed", "media.mix_audio",
                    parameters={
                        "video_step": mixed, "audio_step": audio_step,
                        "mode": mode, "audio_volume": volume,
                    },
                    depends_on=[mixed, audio_step], cost=0.01,
                )
        final_video = mixed
        if spec.audio.subtitles and narration_text:
            subtitle = add(
                "subtitles", "subtitle", "media.subtitle.compose",
                parameters={
                    "cues": self._subtitle_cues(spec), "format": "srt",
                },
                depends_on=[script_step],
            )
            final_video = add(
                "final-video", "final_video", "media.subtitle.burn",
                parameters={"video_step": mixed, "subtitle_step": subtitle},
                depends_on=[mixed, subtitle], cost=0.02,
            )
        final_step = next(item for item in items if item.step_id == final_video)
        final_step.parameters.update({
            "final_output": True,
            "target_duration_seconds": spec.target_duration_seconds,
            "require_audio": bool(narration or bgm),
            "require_subtitles": bool(spec.audio.subtitles and narration_text),
        })
        items.append(RebuildPlanItem(
            step_id="validate-final", action="validate", capability="cuti.continuity.validate",
            output_artifact_type="validation",
            depends_on=list(dict.fromkeys([*clip_steps, timeline, final_video])),
            parameters={"timeline_step": timeline, "video_step": final_video},
            order=len(items) + 1, reason="Validate continuity and final media",
        ))
        topological_steps(items)
        return RebuildPlan(
            project_id=project_id,
            kind="initial",
            base_project_version_id=base_project_version_id,
            workflow_id=spec.workflow_id,
            video_spec=spec,
            items=items,
            estimated_cost=round(sum(item.estimated_cost for item in items), 6),
        )

    @staticmethod
    def _subtitle_cues(spec: VideoSpec) -> list[dict]:
        cues: list[dict] = []
        cursor = 0.0
        for shot in spec.shots:
            if shot.narration:
                cues.append({
                    "start": cursor,
                    "end": cursor + shot.duration_seconds,
                    "text": shot.narration,
                })
            cursor += shot.duration_seconds
        return cues


class SeedanceStoryPlugin(BaseVideoPlugin):
    def describe_workflows(self) -> list[dict]:
        """Keep the migrated prototype readable for old projects, not selectable for new ones."""
        return [{
            "id": SeedanceStoryWorkflow.id,
            "title": "Legacy Cuti Seedance Story compatibility",
            "mode": "legacy_compatibility",
            "parameters": {},
            "pipeline": [],
            "requiresKeyframe": True,
            "entrypoints": [],
            "skillDependencies": ["workflow-keyframe-pipeline"],
            "source": "plugin",
            "pluginId": "cuti.seedance-story",
            "available": True,
            "unavailableReason": None,
            "requiredCapabilities": [],
            "missingCapabilities": [],
            "userSelectable": False,
            "executionKind": "legacy_compatibility",
            "compiler": "SeedanceStoryPlugin.compile_build_plan",
        }]

    def planning_mode(self, _workflow_id: str) -> str:
        return "staged"

    async def compile_build_plan(
        self, context: PluginContext, spec: VideoSpec,
    ) -> RebuildPlan:
        if not context.project_id:
            raise BuildPlanValidationError("workflow requires a project")
        base_version_id = str(context.values.get("base_project_version_id") or "")
        return SeedanceStoryWorkflow().compile(
            project_id=context.project_id,
            base_project_version_id=base_version_id,
            spec=spec,
        )

    async def compile_initial(
        self, context: PluginContext, intent: ProjectIntent,
    ) -> RebuildPlan:
        from app.orchestration.workflow_compiler.registry import (
            WorkflowCheckpointSpec,
            WorkflowPlanningSpec,
            WorkflowSpec,
        )
        from .staged_planning import compile_initial_phase

        workflow = WorkflowSpec(
            skill_name=intent.workflow_id,
            title="Cuti Seedance Story",
            mode="keyframe_pipeline",
            planning=WorkflowPlanningSpec(
                mode="staged",
                checkpoints=(
                    WorkflowCheckpointSpec(
                        id="story_ready",
                        after_phase="story_intent",
                        next_phase="reference_production",
                        required_artifacts=("story_draft",),
                        resolves=("characters", "shots", "audio"),
                        instruction=(
                            "Complete the production VideoSpec from the generated story draft."
                        ),
                    ),
                    WorkflowCheckpointSpec(
                        id="references_ready",
                        after_phase="reference_production",
                        next_phase="keyframe_production",
                        required_artifacts=("character_reference",),
                        resolves=("keyframe_prompts",),
                        instruction=(
                            "Refine keyframe composition from the real character references."
                        ),
                    ),
                    WorkflowCheckpointSpec(
                        id="keyframes_ready",
                        after_phase="keyframe_production",
                        next_phase="video_production",
                        required_artifacts=("keyframe",),
                        resolves=("video_motion", "transitions", "timeline"),
                        instruction="Plan motion and transitions from the generated keyframes.",
                    ),
                ),
            ),
        )
        return compile_initial_phase(workflow=workflow, context=context, intent=intent)

    async def compile_phase(
        self,
        context: PluginContext,
        checkpoint: PlanCheckpoint,
        resolution: CheckpointResolution,
        plan: RebuildPlan,
    ) -> RebuildPlan:
        from app.orchestration.workflow_compiler.registry import (
            WorkflowCheckpointSpec,
            WorkflowPlanningSpec,
            WorkflowSpec,
        )
        from .staged_planning import (
            append_phase,
            copy_plan_with_appended_phase,
            failed_checkpoint_step_ids,
        )

        workflow = WorkflowSpec(
            skill_name=plan.workflow_id,
            title="Cuti Seedance Story",
            mode="keyframe_pipeline",
            planning=WorkflowPlanningSpec(
                mode="staged",
                checkpoints=(
                    WorkflowCheckpointSpec(
                        id="story_ready", after_phase="story_intent",
                        next_phase="reference_production",
                        required_artifacts=("story_draft",),
                    ),
                    WorkflowCheckpointSpec(
                        id="references_ready", after_phase="reference_production",
                        next_phase="keyframe_production",
                        required_artifacts=("character_reference",),
                    ),
                    WorkflowCheckpointSpec(
                        id="keyframes_ready", after_phase="keyframe_production",
                        next_phase="video_production", required_artifacts=("keyframe",),
                    ),
                ),
            ),
        )
        full_plan = await self.compile_build_plan(context, resolution.video_spec)
        added, next_checkpoint = append_phase(
            existing_plan=plan,
            full_plan=full_plan,
            workflow=workflow,
            checkpoint_id=checkpoint.phase,
            proposed_steps=resolution.proposed_steps,
            repair_step_ids=failed_checkpoint_step_ids(checkpoint),
        )
        return copy_plan_with_appended_phase(
            plan,
            spec=resolution.video_spec,
            added_items=added,
            spec_revision_id=str(context.values["video_spec_revision_id"]),
            next_checkpoint=next_checkpoint,
        )
