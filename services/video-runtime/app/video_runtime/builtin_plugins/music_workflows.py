from __future__ import annotations

from ..initial_build import BuildPlanValidationError, SeedanceStoryWorkflow
from ..models import RebuildPlan, RebuildPlanItem, VideoSpec
from ..plugins import BaseVideoPlugin, PluginContext


class MusicVideoWorkflowPlugin(BaseVideoPlugin):
    async def compile_build_plan(self, context: PluginContext, spec: VideoSpec) -> RebuildPlan:
        if not spec.audio.bgm_prompt.strip():
            raise BuildPlanValidationError("music-video workflow requires audio.bgm_prompt")
        base = spec.model_copy(deep=True)
        base.workflow_id = SeedanceStoryWorkflow.id
        plan = SeedanceStoryWorkflow().compile(
            project_id=str(context.project_id),
            base_project_version_id=str(context.values.get("base_project_version_id") or ""),
            spec=base,
        )
        plan.workflow_id = spec.workflow_id
        plan.video_spec = spec
        return plan


class LipsyncMusicVideoWorkflowPlugin(MusicVideoWorkflowPlugin):
    async def compile_build_plan(self, context: PluginContext, spec: VideoSpec) -> RebuildPlan:
        if not any(shot.narration.strip() for shot in spec.shots):
            raise BuildPlanValidationError("lipsync workflow requires narration")
        plan = await super().compile_build_plan(context, spec)
        step_ids = {item.step_id for item in plan.items}
        if "assembled-video" not in step_ids or "narration" not in step_ids:
            raise BuildPlanValidationError("lipsync workflow requires assembled video and narration")
        lipsync = RebuildPlanItem(
            step_id="lipsync-video",
            output_artifact_id=f"{plan.project_id}:lipsync-video",
            output_artifact_type="video_lipsync",
            action="create",
            capability="media.lipsync",
            parameters={
                "video_step": "assembled-video",
                "audio_step": "narration",
                "model": "sync/lipsync-2-pro",
            },
            depends_on=["assembled-video", "narration"],
            estimated_cost=0.35,
            reason="Apply project narration as a lipsync pass",
        )
        for item in plan.items:
            if item.step_id == "assembled-video":
                continue
            item.depends_on = ["lipsync-video" if value == "assembled-video" else value for value in item.depends_on]
            if item.parameters.get("video_step") == "assembled-video":
                item.parameters["video_step"] = "lipsync-video"
        plan.items.append(lipsync)
        for index, item in enumerate(plan.items, start=1):
            item.order = index
        plan.estimated_cost = round(sum(item.estimated_cost for item in plan.items), 6)
        return plan
