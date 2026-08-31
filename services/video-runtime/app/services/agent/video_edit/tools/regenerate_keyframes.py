"""RegenerateKeyframesTool — 重新生成指定镜头的关键帧。"""
import logging
import uuid
from typing import List, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RegenerateKeyframesInput(BaseModel):
    """regenerate_keyframes 的输入参数。"""
    shot_numbers: List[int] = Field(
        ..., description="要重新生成关键帧的镜头编号列表，如 [3, 7]",
    )
    frame_position: str = Field(
        "first", description="关键帧位置: first (首帧) | last (尾帧) | all (全部)",
    )
    instruction: str = Field(
        "", description="修改指令，如 '增加画面亮度' '改为暖色调'",
    )
    mode: str = Field(
        "instruction",
        description=(
            "prompt 模式。"
            "instruction: 基于当前版本的 t2i_prompt 叠加用户修改指令（默认，推荐）。"
            "direct_prompt: 将 instruction 直接作为完整 t2i_prompt 使用。"
        ),
    )
    regenerate_strategy: Optional[str] = Field(
        default=None,
        description=(
            "省略时：mode=instruction → instruction_regenerate；mode=direct_prompt → prompt_regenerate。"
            "用户要求基于当前关键帧画面/原图做局部改色改物 → 显式 instruction_edit_image（用当前版本 keyframe_url 做 I2I）。"
            "显式：prompt_regenerate | instruction_regenerate | instruction_merge_prompt（只融 t2i 并 API 返回，不写新版本行）| instruction_edit_image"
        ),
    )


class RegenerateKeyframesTool(BaseTool):
    name: str = "regenerate_keyframes"
    description: str = (
        "重新生成指定镜头的关键帧。支持按镜头编号指定。"
        "frame_position: first (首帧) | last (尾帧) | all (全部)。"
        "instruction 写修改需求；mode 默认 instruction（基于当前 t2i_prompt 叠加），或 direct_prompt（整段替换）。"
        "若用户要求基于当前关键帧画面/原图做局部编辑，须传 regenerate_strategy=instruction_edit_image。"
    )
    args_schema: Type[BaseModel] = RegenerateKeyframesInput

    run_id: str = ""
    user_id: str = ""
    thread_id: str = ""

    async def _arun(
        self,
        shot_numbers: List[int],
        frame_position: str = "first",
        instruction: str = "",
        mode: str = "instruction",
        regenerate_strategy: Optional[str] = None,
    ) -> str:
        from .....crud.video.video_keyframe import (
            get_keyframe_by_uuid,
            get_keyframes_by_run_id,
            get_keyframes_by_thread_id,
            get_keyframe_versions_by_keyframe_ids,
        )

        frame_index_filter = None
        if frame_position == "first":
            frame_index_filter = 0
        elif frame_position == "last":
            frame_index_filter = -1

        all_kfs = await get_keyframes_by_thread_id(self.thread_id) if self.thread_id else await get_keyframes_by_run_id(self.run_id)
        target_kfs = []
        for kf in all_kfs:
            if kf.shot_number in shot_numbers:
                if frame_index_filter is not None and kf.frame_index != frame_index_filter:
                    continue
                target_kfs.append(kf)

        if not target_kfs:
            return f"未找到 shot {shot_numbers} 对应的关键帧。"

        logger.info(
            "[video_edit] regenerate_keyframes tool entry thread_id=%s run_id=%s shot_numbers=%s "
            "frame_position=%s mode=%s instruction_len=%s | create_agent 常见首轮仅 tool_calls、"
            "无助手正文；用户可见说明多在工具返回后的下一轮，见 system_prompt 首句措辞",
            self.thread_id,
            self.run_id,
            shot_numbers,
            frame_position,
            mode,
            len((instruction or "").strip()),
        )
        _rs_raw = (regenerate_strategy or "").strip()
        from .....models.version_regenerate_strategy import KeyframeRegenerateStrategy
        from .....api.agent.agent_router_endpoints import (
            KeyframeRequest, KeyframeVersionRequest,
        )
        from .....services.task_enqueue_service import execute_regenerate_keyframes

        _rs_eff = (
            _rs_raw
            if _rs_raw
            else (
                KeyframeRegenerateStrategy.INSTRUCTION_REGENERATE.value
                if mode == "instruction"
                else KeyframeRegenerateStrategy.PROMPT_REGENERATE.value
            )
        )

        keyframe_requests = []
        for kf in target_kfs:
            versions = await get_keyframe_versions_by_keyframe_ids([kf.uuid])
            if not versions:
                return f"关键帧 {kf.uuid} 无版本记录，无法再生。"
            kfdb = await get_keyframe_by_uuid(kf.uuid)
            current_idx = (kfdb.current_version_index or 0) if kfdb else 0
            sorted_v = sorted(versions, key=lambda x: getattr(x, "version_number", 0))
            if current_idx >= len(sorted_v):
                current_idx = len(sorted_v) - 1
            sel = sorted_v[current_idx]
            ver_uuid = getattr(sel, "uuid", "") or ""

            if mode == "direct_prompt":
                ver = KeyframeVersionRequest(
                    uuid=ver_uuid,
                    custom_prompt=instruction or None,
                    instruction=None,
                    regenerate_strategy=_rs_eff,
                )
            else:
                ver = KeyframeVersionRequest(
                    uuid=ver_uuid,
                    custom_prompt=None,
                    instruction=instruction or None,
                    regenerate_strategy=_rs_eff,
                )

            keyframe_requests.append(
                KeyframeRequest(
                    uuid=kf.uuid,
                    shot_number=kf.shot_number,
                    frame_index=kf.frame_index,
                    versions=[ver],
                )
            )

        new_run_id = str(uuid.uuid4())
        result = await execute_regenerate_keyframes(
            thread_id=self.thread_id,
            run_id=new_run_id,
            user_id=self.user_id,
            keyframes=keyframe_requests,
            user_option=None,
            regenerate_source="chat",
        )

        succeeded = result.get("succeeded", 0) if isinstance(result, dict) else 0
        failed = result.get("failed", 0) if isinstance(result, dict) else 0
        total = len(target_kfs)

        return (
            f"✅ 已提交 {total} 个关键帧重新生成任务\n"
            f"  镜头: {', '.join(f'shot_{s}' for s in shot_numbers)}\n"
            f"  位置: {frame_position}\n"
            f"  模式: {mode}\n"
            f"  指令: {instruction or '(无)'}\n"
            f"  结果: 成功 {succeeded}, 失败 {failed}"
        )

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")
