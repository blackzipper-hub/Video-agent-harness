from __future__ import annotations

from app.orchestration.workflow_compiler.registry import WorkflowSpec

from ..initial_build import BuildPlanValidationError, topological_steps
from ..models import RebuildPlan, RebuildPlanItem, VideoSpec
from ..plugins import BaseVideoPlugin, PluginContext
from ..workflow_plans import compile_mv


class MusicVideoWorkflowPlugin(BaseVideoPlugin):
    async def compile_build_plan(self, context: PluginContext, spec: VideoSpec) -> RebuildPlan:
        workflow = WorkflowSpec(
            skill_name=spec.workflow_id,
            title="Cuti Music Video",
            mode="seedance_mv",
            parameters={"workflow_mode": "music_video", "content_category": "music_video"},
            pipeline=(
                "atomic.music.generate", "media.audio.analyze", "media.audio.trim",
                "atomic.video.generate", "media.concat", "media.mix_audio",
            ),
            requires_keyframe=False,
        )
        return compile_mv(workflow, context, spec)


class LipsyncMusicVideoWorkflowPlugin(MusicVideoWorkflowPlugin):
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
                }, depends_on=["script"], estimated_cost=0.05,
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
