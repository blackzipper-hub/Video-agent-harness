from __future__ import annotations

from app.orchestration.workflow_compiler.registry import WorkflowSpec

from ..initial_build import BuildPlanValidationError, topological_steps
from ..models import (
    CheckpointResolution,
    PlanCheckpoint,
    ProjectIntent,
    RebuildPlan,
    RebuildPlanItem,
    VideoSpec,
)
from ..plugins import BaseVideoPlugin, PluginContext
from ..workflow_plans import compile_mv_compat


class MusicVideoWorkflowPlugin(BaseVideoPlugin):
    workflow_id = "cuti.music-video"
    workflow_title = "Cuti Music Video"
    compiler_name = "MusicVideoWorkflowPlugin.compile_build_plan"
    extra_capabilities: tuple[str, ...] = ()
    instruction_skill_id = "mv"
    instruction_appendix = ""

    def planning_mode(self, _workflow_id: str) -> str:
        return "staged"

    def describe_workflows(self) -> list[dict]:
        workflow = self._workflow(self.workflow_id)
        pipeline = list(dict.fromkeys([*workflow.pipeline, *self.extra_capabilities]))
        return [{
            "id": self.workflow_id,
            "title": self.workflow_title,
            "mode": workflow.mode,
            "parameters": dict(workflow.parameters),
            "pipeline": pipeline,
            "requiresKeyframe": workflow.requires_keyframe,
            "entrypoints": ["text", "image", "audio", "video"],
            "skillDependencies": [],
            "source": "plugin",
            "pluginId": self.workflow_id,
            "available": True,
            "unavailableReason": None,
            "requiredCapabilities": pipeline,
            "missingCapabilities": [],
            "userSelectable": True,
            "executionKind": "dedicated_plugin_compiler",
            "compiler": self.compiler_name,
            # This alias executes the original Cuti $mv contract.  Planning
            # instructions and linked resources therefore remain owned by the
            # real mv Skill instead of an instruction-less synthetic id.
            "instructionSkillId": self.instruction_skill_id,
            "instructionAppendix": self.instruction_appendix,
            "planning": {"mode": "staged", "checkpoints": []},
        }]

    @staticmethod
    def _workflow(workflow_id: str) -> WorkflowSpec:
        return WorkflowSpec(
            skill_name=workflow_id,
            title="Cuti Music Video",
            # This plugin uses the same Suno + smart-cut contract as $mv.
            # Labeling it seedance_mv made its initial staged phase call
            # atomic.music.generate while its final compiler called Suno.
            mode="mv",
            parameters={"workflow_mode": "music_video", "content_category": "music_video"},
            pipeline=(
                "suno.generate", "media.audio_analyze", "media.audio_cut",
                "atomic.image.generate", "api.provider.generate",
                "media.concat", "media.mix_audio",
            ),
            requires_keyframe=False,
        )

    async def compile_build_plan(self, context: PluginContext, spec: VideoSpec) -> RebuildPlan:
        return compile_mv_compat(self._workflow(spec.workflow_id), context, spec)

    async def compile_initial(
        self, context: PluginContext, intent: ProjectIntent,
    ) -> RebuildPlan:
        from ..staged_planning import compile_initial_phase

        return compile_initial_phase(
            workflow=self._workflow(intent.workflow_id), context=context, intent=intent,
        )

    async def compile_phase(
        self,
        context: PluginContext,
        checkpoint: PlanCheckpoint,
        resolution: CheckpointResolution,
        plan: RebuildPlan,
    ) -> RebuildPlan:
        from ..staged_planning import (
            append_phase,
            copy_plan_with_appended_phase,
            failed_checkpoint_step_ids,
        )

        full_plan = await self.compile_build_plan(context, resolution.video_spec)
        added, next_checkpoint = append_phase(
            existing_plan=plan,
            full_plan=full_plan,
            workflow=self._workflow(plan.workflow_id),
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


class LipsyncMusicVideoWorkflowPlugin(MusicVideoWorkflowPlugin):
    workflow_id = "cuti.lipsync-music-video"
    workflow_title = "Cuti Lipsync Music Video"
    compiler_name = "LipsyncMusicVideoWorkflowPlugin.compile_build_plan"
    extra_capabilities = ("media.tts", "media.lipsync")
    instruction_appendix = (
        "\n\n## Cuti lipsync extension\n"
        "After the original $mv music-video master is complete, create the requested "
        "narration track and apply one media.lipsync pass. Do not replace the original "
        "$mv planning, music analysis, cast locking, shot generation, or final mix stages."
    )

    async def compile_build_plan(self, context: PluginContext, spec: VideoSpec) -> RebuildPlan:
        if not any(shot.narration.strip() for shot in spec.shots):
            raise BuildPlanValidationError("lipsync workflow requires narration")
        plan = await super().compile_build_plan(context, spec)
        final = next((item for item in plan.items if item.step_id == "final-video"), None)
        if final is None:
            raise BuildPlanValidationError("lipsync workflow requires a completed music video")
        final.step_id = "music-video"
        final.output_artifact_id = f"{plan.project_id}:music-video"
        final.output_artifact_type = "video_mixed"
        final.parameters["final_output"] = False
        for item in plan.items:
            item.depends_on = [
                "music-video" if value == "final-video" else value
                for value in item.depends_on
            ]
            for parameter in ("video_step", "source_video_step"):
                if item.parameters.get(parameter) == "final-video":
                    item.parameters[parameter] = "music-video"
        narration = next((item for item in plan.items if item.step_id == "narration"), None)
        if narration is None:
            narration = RebuildPlanItem(
                step_id="narration", output_artifact_id=f"{plan.project_id}:narration",
                output_artifact_type="audio_narration", action="create", capability="media.tts",
                parameters={
                    "text": "\n".join(shot.narration for shot in spec.shots if shot.narration),
                    "voice_id": spec.audio.narration_voice,
                }, depends_on=["mv-shot-plan"], estimated_cost=0.05,
                reason="Create the requested lipsync voice track",
            )
            plan.items.append(narration)
        lipsync = RebuildPlanItem(
            step_id="final-video",
            output_artifact_id=f"{plan.project_id}:final-video",
            output_artifact_type="final_video",
            action="create",
            capability="media.lipsync",
            parameters={
                "video_step": "music-video",
                "audio_step": "narration",
                "model": "sync/lipsync-2-pro",
                "final_output": True,
                "require_audio": True,
                "target_duration_seconds": spec.target_duration_seconds,
            },
            depends_on=["music-video", "narration"],
            estimated_cost=0.35,
            reason="Apply project narration as a lipsync pass",
        )
        for item in plan.items:
            if item.action == "validate":
                item.depends_on = [
                    "final-video" if value == "music-video" else value
                    for value in item.depends_on
                ]
                if item.parameters.get("video_step") == "music-video":
                    item.parameters["video_step"] = "final-video"
        plan.items.append(lipsync)
        for index, item in enumerate(plan.items, start=1):
            item.order = index
        topological_steps(plan.items)
        plan.estimated_cost = round(sum(item.estimated_cost for item in plan.items), 6)
        return plan
