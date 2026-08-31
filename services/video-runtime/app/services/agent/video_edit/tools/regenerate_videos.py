"""RegenerateVideosTool — 重新生成指定镜头的视频片段。"""
import logging
import uuid
from typing import List, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RegenerateVideosInput(BaseModel):
    """regenerate_videos 的输入参数。"""
    shot_numbers: List[int] = Field(
        ..., description="要重新生成视频的镜头编号列表，如 [5, 7]",
    )
    instruction: str = Field(
        "", description="修改指令，如 '让运动更慢一些' '改为推镜头'",
    )
    mode: str = Field(
        "instruction",
        description=(
            "prompt 模式。"
            "instruction: 基于当前版本的 motion_prompt 叠加用户修改指令（默认，推荐）。"
            "direct_prompt: 将 instruction 直接作为完整 i2v_prompt 使用。"
        ),
    )
    regenerate_strategy: Optional[str] = Field(
        default=None,
        description="省略时默认 prompt_regenerate。显式：prompt_regenerate | instruction_merge_prompt（只融 motion 文案并 API 返回，不写新版本行，无 I2V）",
    )


class RegenerateVideosTool(BaseTool):
    name: str = "regenerate_videos"
    description: str = (
        "重新生成指定镜头的视频片段。支持按镜头编号指定。"
        "可在 instruction 中描述修改需求。"
        "mode 默认 instruction（基于当前 prompt 叠加修改），也可用 direct_prompt（整段替换）。"
    )
    args_schema: Type[BaseModel] = RegenerateVideosInput

    run_id: str = ""
    user_id: str = ""
    thread_id: str = ""

    async def _arun(
        self,
        shot_numbers: List[int],
        instruction: str = "",
        mode: str = "instruction",
        regenerate_strategy: Optional[str] = None,
    ) -> str:
        from .....crud.video.video_generation import (
            get_video_generations_by_run_id,
            get_video_generations_by_thread_id,
            get_video_generation_versions_by_video_generation_ids,
        )

        all_vgs = await get_video_generations_by_thread_id(self.thread_id) if self.thread_id else await get_video_generations_by_run_id(self.run_id)
        target_vgs = [v for v in all_vgs if v.shot_number in shot_numbers]

        if not target_vgs:
            return (
                f"未找到 shot {shot_numbers} 对应的视频记录。"
                "该阶段可能尚未批量生成，或上次生成结果未成功入库。"
                "若 pending_gate/interrupt 为 failed_video，应使用 "
                "continue_pipeline(gate=\"after_shots\") 重跑镜头视频；"
                "若为 after_keyframe_reflection，用 continue_pipeline(gate=\"after_keyframe_reflection\")；"
                "reference_t2v（跳关键帧）路径勿引导用户去重生成关键帧。"
            )

        from .....api.agent.agent_router_endpoints import (
            VideoRequest, VideoVersionRequest,
        )
        from .....services.task_enqueue_service import execute_regenerate_videos

        from .....crud.video.video_generation import (
            get_video_generation_by_uuid,
            get_video_generation_versions_by_video_generation_ids,
        )

        _rs_raw = (regenerate_strategy or "").strip()
        from .....models.version_regenerate_strategy import VideoRegenerateStrategy

        _rs_eff = (
            _rs_raw
            if _rs_raw
            else VideoRegenerateStrategy.PROMPT_REGENERATE.value
        )
        video_requests = []
        for vg in target_vgs:
            versions = await get_video_generation_versions_by_video_generation_ids([vg.uuid])
            if not versions:
                return f"视频 {vg.uuid} 无版本记录，无法再生。"
            vg_row = await get_video_generation_by_uuid(vg.uuid)
            current_idx = (vg_row.current_version_index or 0) if vg_row else 0
            sorted_v = sorted(versions, key=lambda x: getattr(x, "version_number", 0))
            if current_idx >= len(sorted_v):
                current_idx = len(sorted_v) - 1
            sel = sorted_v[current_idx]
            ver_uuid = getattr(sel, "uuid", "") or ""

            if mode == "direct_prompt":
                ver = VideoVersionRequest(
                    uuid=ver_uuid,
                    custom_prompt=instruction or None,
                    instruction=None,
                    regenerate_strategy=_rs_eff,
                )
            else:
                ver = VideoVersionRequest(
                    uuid=ver_uuid,
                    custom_prompt=None,
                    instruction=instruction or None,
                    regenerate_strategy=_rs_eff,
                )

            video_requests.append(
                VideoRequest(
                    uuid=vg.uuid,
                    shot_number=vg.shot_number,
                    versions=[ver],
                )
            )

        new_run_id = str(uuid.uuid4())
        result = await execute_regenerate_videos(
            thread_id=self.thread_id,
            run_id=new_run_id,
            user_id=self.user_id,
            videos=video_requests,
            user_option=None,
            regenerate_source="chat",
        )

        succeeded = result.get("succeeded", 0) if isinstance(result, dict) else 0
        failed = result.get("failed", 0) if isinstance(result, dict) else 0

        return (
            f"✅ 已提交 {len(target_vgs)} 个视频重新生成任务\n"
            f"  镜头: {', '.join(f'shot_{s}' for s in shot_numbers)}\n"
            f"  模式: {mode}\n"
            f"  指令: {instruction or '(无)'}\n"
            f"  结果: 成功 {succeeded}, 失败 {failed}"
        )

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")
