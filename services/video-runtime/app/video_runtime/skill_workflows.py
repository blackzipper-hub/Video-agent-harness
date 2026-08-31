"""Adapt Cuti workflow SKILL.md declarations into Video Runtime workflow plugins."""

from __future__ import annotations

from typing import Any

from app.orchestration.workflow_compiler.registry import (
    WorkflowSpec,
)

from .initial_build import SeedanceStoryWorkflow, topological_steps
from .models import RebuildPlan, RebuildPlanItem, VideoSpec
from .plugins import BaseVideoPlugin, PluginContext, VideoPluginRegistry
from .plugins.models import (
    PluginContributions,
    PluginPermissions,
    VideoPluginManifest,
)
from .skills import VideoSkillRuntime


SKILL_WORKFLOW_PLUGIN_ID = "cuti.skill-workflows"


class SkillWorkflowPlugin(BaseVideoPlugin):
    """Compile installed Workflow Skills through the deterministic build graph."""

    def __init__(self, workflows: dict[str, WorkflowSpec]) -> None:
        self._workflows = dict(workflows)

    def describe_workflows(self) -> list[dict[str, Any]]:
        return [
            {
                "id": spec.skill_name,
                "title": spec.title,
                "mode": spec.mode,
                "parameters": dict(spec.parameters),
                "pipeline": list(spec.pipeline),
                "requiresKeyframe": spec.requires_keyframe,
                "entrypoints": list(spec.entrypoints),
                "skillDependencies": list(spec.skill_dependencies),
                "source": "skill",
                "pluginId": SKILL_WORKFLOW_PLUGIN_ID,
            }
            for spec in self._workflows.values()
        ]

    async def compile_build_plan(
        self,
        context: PluginContext,
        video_spec: VideoSpec,
    ) -> RebuildPlan:
        workflow = self._workflows.get(video_spec.workflow_id)
        if workflow is None:
            raise LookupError(f"workflow Skill is not installed: {video_spec.workflow_id}")
        if not context.project_id:
            raise ValueError("workflow Skill requires a project")
        if workflow.mode == "seedance2":
            return self._compile_seedance2(
                workflow=workflow,
                project_id=context.project_id,
                base_project_version_id=str(
                    context.values.get("base_project_version_id") or ""
                ),
                video_spec=video_spec,
            )
        seedance_spec = video_spec.model_copy(update={
            "workflow_id": SeedanceStoryWorkflow.id,
        })
        plan = SeedanceStoryWorkflow().compile(
            project_id=context.project_id,
            base_project_version_id=str(
                context.values.get("base_project_version_id") or ""
            ),
            spec=seedance_spec,
        )
        if not workflow.requires_keyframe:
            self._remove_keyframe_generation(
                plan,
                video_spec,
                direct=workflow.mode == "direct_video",
            )
        for item in plan.items:
            if item.capability.startswith("atomic."):
                for key, value in workflow.parameters.items():
                    item.parameters.setdefault(key, value)
                item.parameters.setdefault("activated_workflow", workflow.skill_name)
            item.reason = f"Workflow Skill: {workflow.title}"
        plan.workflow_id = workflow.skill_name
        plan.video_spec = video_spec
        plan.estimated_cost = round(
            sum(item.estimated_cost for item in plan.items), 6,
        )
        topological_steps(plan.items)
        return plan

    @staticmethod
    def _compile_seedance2(
        *, workflow: WorkflowSpec, project_id: str,
        base_project_version_id: str, video_spec: VideoSpec,
    ) -> RebuildPlan:
        """Compile the Seedance Skill's native-audio workflow without story voice-over."""
        items: list[RebuildPlanItem] = []

        def add(
            step_id: str,
            artifact_type: str,
            capability: str,
            *,
            parameters: dict[str, Any] | None = None,
            depends_on: list[str] | None = None,
            cost: float = 0.0,
            skills: list[str] | None = None,
        ) -> str:
            merged = dict(parameters or {})
            if capability.startswith("atomic."):
                for key, value in workflow.parameters.items():
                    merged.setdefault(key, value)
                merged.setdefault("activated_workflow", workflow.skill_name)
            items.append(RebuildPlanItem(
                step_id=step_id,
                output_artifact_id=f"{project_id}:{step_id}",
                output_artifact_type=artifact_type,
                action="create",
                capability=capability,
                parameters=merged,
                depends_on=depends_on or [],
                estimated_cost=cost,
                order=len(items) + 1,
                reason=f"Workflow Skill: {workflow.title}",
                skill_ids=skills or [],
            ))
            return step_id

        spec_step = add("spec", "video_spec", "runtime.artifact.persist", parameters={
            "title": video_spec.title,
            "content": video_spec.model_dump(mode="json"),
        })
        script_step = add("script", "script", "runtime.artifact.persist", parameters={
            "title": f"{video_spec.title} script",
            "content": [{
                "shotId": shot.id,
                "beat": shot.beat,
                "visualPrompt": shot.visual_prompt,
            } for shot in video_spec.shots],
        }, depends_on=[spec_step])
        character_manifest = add(
            "characters", "characters", "runtime.artifact.persist",
            parameters={
                "title": "Characters",
                "content": [item.model_dump(mode="json") for item in video_spec.characters],
            },
            depends_on=[spec_step],
        )
        storyboard = add(
            "storyboard", "storyboard", "runtime.artifact.persist",
            parameters={
                "title": "Storyboard",
                "content": [item.model_dump(mode="json") for item in video_spec.shots],
            },
            depends_on=[script_step, character_manifest],
        )

        clip_steps: list[str] = []
        previous_tail = ""
        for shot in video_spec.shots:
            dependencies = [storyboard]
            if previous_tail:
                dependencies.append(previous_tail)
            parameters: dict[str, Any] = {
                "prompt": shot.visual_prompt,
                "duration": shot.duration_seconds,
                "model": video_spec.providers.video,
                "resolution": video_spec.resolution,
                "aspect_ratio": video_spec.aspect_ratio,
                "generate_audio": True,
                "shot_id": shot.id,
            }
            if previous_tail:
                parameters.update({
                    "generation_mode": "i2v",
                    "start_image_from_step": previous_tail,
                })
            clip = add(
                f"shot-{shot.id}-video",
                "video_clip",
                "atomic.video.generate",
                parameters=parameters,
                depends_on=dependencies,
                cost=0.65,
            )
            clip_steps.append(clip)
            previous_tail = add(
                f"shot-{shot.id}-tail",
                "continuity_frame",
                "media.extract_frame",
                parameters={
                    "position": "last",
                    "format": "png",
                    "source_video_step": clip,
                },
                depends_on=[clip],
            )

        timeline = add(
            "timeline", "timeline", "media.timeline.compose",
            parameters={
                "shots": [shot.model_dump(mode="json") for shot in video_spec.shots],
                "video_steps": clip_steps,
            },
            depends_on=clip_steps,
        )
        final_video = add(
            "final-video", "final_video", "media.concat",
            parameters={
                "video_steps": clip_steps,
                "normalize": True,
                "transition_duration": 0.125,
                "final_output": True,
                "require_audio": True,
                "require_subtitles": False,
                "target_duration_seconds": video_spec.target_duration_seconds,
            },
            depends_on=clip_steps,
            cost=0.02,
        )
        items.append(RebuildPlanItem(
            step_id="validate-final",
            action="validate",
            capability="cuti.continuity.validate",
            output_artifact_type="validation",
            depends_on=[*clip_steps, timeline, final_video],
            parameters={"timeline_step": timeline, "video_step": final_video},
            order=len(items) + 1,
            reason=f"Workflow Skill: {workflow.title}",
        ))
        topological_steps(items)
        return RebuildPlan(
            project_id=project_id,
            kind="initial",
            base_project_version_id=base_project_version_id,
            workflow_id=workflow.skill_name,
            video_spec=video_spec,
            items=items,
            estimated_cost=round(sum(item.estimated_cost for item in items), 6),
        )

    @staticmethod
    def _remove_keyframe_generation(
        plan: RebuildPlan,
        video_spec: VideoSpec,
        *,
        direct: bool,
    ) -> None:
        removed = {
            item.step_id
            for item in plan.items
            if item.output_artifact_type == "keyframe"
            or (direct and item.output_artifact_type == "character_reference")
        }
        plan.items = [item for item in plan.items if item.step_id not in removed]
        previous_tail = ""
        for shot in video_spec.shots:
            step_id = f"shot-{shot.id}-video"
            video_step = next(item for item in plan.items if item.step_id == step_id)
            references = [] if direct else [
                f"character-{character_id}-reference"
                for character_id in shot.character_ids
            ]
            video_step.depends_on = list(dict.fromkeys([
                "storyboard",
                *references,
                *([previous_tail] if previous_tail else []),
            ]))
            video_step.parameters.pop("start_image_from_step", None)
            video_step.parameters["character_reference_from_steps"] = references
            if previous_tail:
                video_step.parameters["start_image_from_step"] = previous_tail
            previous_tail = f"shot-{shot.id}-tail"
        for item in plan.items:
            item.depends_on = [
                dependency for dependency in item.depends_on
                if dependency not in removed
            ]


async def load_workflow_skills(
    plugins: VideoPluginRegistry,
    skills: VideoSkillRuntime,
) -> int:
    """Register the workflows already validated by the process-owned Skill Runtime."""
    workflows = skills.workflows.items()
    if not workflows:
        return 0
    manifest = VideoPluginManifest(
        id=SKILL_WORKFLOW_PLUGIN_ID,
        version="1.0.0",
        runtime_entrypoint=(
            "app.video_runtime.skill_workflows:SkillWorkflowPlugin"
        ),
        trusted=True,
        contributions=PluginContributions(workflows=sorted(workflows)),
        skills=sorted(workflows),
        permissions=PluginPermissions(sandbox=False),
    )
    await plugins.register(
        manifest,
        SkillWorkflowPlugin(workflows),
        source_path=skills.roots[0],
    )
    return len(workflows)


async def reload_workflow_skills(
    plugins: VideoPluginRegistry,
    skills: VideoSkillRuntime,
) -> int:
    """Reload the unified catalog and replace its generated workflow plugin."""
    skills.reload()
    try:
        plugins.get(SKILL_WORKFLOW_PLUGIN_ID)
    except LookupError:
        pass
    else:
        await plugins.unload(SKILL_WORKFLOW_PLUGIN_ID)
    return await load_workflow_skills(plugins, skills)
