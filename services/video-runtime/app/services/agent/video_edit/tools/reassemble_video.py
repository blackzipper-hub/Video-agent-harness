"""ReassembleVideoTool — 重新合成最终视频。"""
import logging
from typing import Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ReassembleVideoInput(BaseModel):
    """reassemble_video 无需额外输入（thread_id 已绑定）。"""
    pass


class ReassembleVideoTool(BaseTool):
    name: str = "reassemble_video"
    description: str = (
        "重新合成最终视频。使用当前 thread 下所有已有资源"
        "（视频片段、旁白、音效、配乐等）重新合成成片。"
        "通常在修改了某些镜头/旁白/配乐后调用。"
    )
    args_schema: Type[BaseModel] = ReassembleVideoInput

    run_id: str = ""
    user_id: str = ""
    thread_id: str = ""

    async def _arun(self, **kwargs) -> str:
        from .....services.agent.video_agent_service import get_video_agent_service

        video_agent_service = get_video_agent_service()

        # 依据每个镜头「当前选中版本」（用户可能刚 select_version 切到最新）构造 videos，
        # 让 assemble 前先 sync segments。否则 video_assembly_by_request 会直接拿旧的
        # video_segments 拼接——而这些 segment 可能仍指向初次失败时写入的黑屏占位视频
        # （black_segment_*），导致「切了最新镜头却仍是黑屏成片」。
        videos = await self._collect_selected_shot_versions()

        result = await video_agent_service.video_assembly_by_request(
            thread_id=self.thread_id,
            user_id=self.user_id,
            videos=videos or None,
        )

        if isinstance(result, dict):
            video_url = result.get("final_video_url", "")
            assembly_uuid = result.get("assembly_uuid", "")
            return (
                f"✅ 视频重新合成完成\n"
                f"  视频地址: {video_url}\n"
                f"  assembly_uuid: {assembly_uuid}"
            )

        return f"✅ 视频合成任务已提交: {result}"

    async def _collect_selected_shot_versions(self):
        """按 thread 收集各镜头当前应使用的版本，供 assemble 前 sync segments。

        选版规则：优先取 video_generation.current_version_index 指向的版本（尊重用户
        select_version 的选择）；若该版本没有成功产出的视频，则回退到最新的「成功且有
        URL」版本，避免把失败/黑屏版本同步进 segment。返回可直接传给
        video_assembly_by_request(videos=...) 的列表；出错时返回空列表，退回旧行为。
        """
        if not self.thread_id:
            return []
        try:
            from .....crud.video.video_generation import (
                get_video_generations_by_thread_id,
                get_video_generation_by_uuid,
                get_video_generation_versions_by_video_generation_ids,
            )
            from .....api.agent.agent_router_endpoints import (
                VideoAssemblyVideoRequest,
                VideoAssemblySelectedVersion,
            )
        except Exception as e:  # pragma: no cover - 导入异常极少见
            logger.warning("reassemble_video: 导入依赖失败，退回无 sync 合成: %s", e)
            return []

        def _has_video(v) -> bool:
            return bool(getattr(v, "success", False)) and bool(
                getattr(v, "video_url", None) or getattr(v, "preview_video_url", None)
            )

        videos = []
        try:
            all_vgs = await get_video_generations_by_thread_id(self.thread_id)
            for vg in all_vgs or []:
                versions = await get_video_generation_versions_by_video_generation_ids([vg.uuid])
                if not versions:
                    continue
                sorted_v = sorted(versions, key=lambda x: getattr(x, "version_number", 0))
                vg_row = await get_video_generation_by_uuid(vg.uuid)
                cur_idx = (getattr(vg_row, "current_version_index", 0) or 0) if vg_row else 0
                if cur_idx >= len(sorted_v):
                    cur_idx = len(sorted_v) - 1
                chosen = sorted_v[cur_idx]
                if not _has_video(chosen):
                    for v in reversed(sorted_v):
                        if _has_video(v):
                            chosen = v
                            break
                if _has_video(chosen):
                    videos.append(
                        VideoAssemblyVideoRequest(
                            uuid=vg.uuid,
                            selected_version=VideoAssemblySelectedVersion(uuid=chosen.uuid),
                        )
                    )
        except Exception as e:
            logger.warning("reassemble_video: 收集镜头版本失败，退回无 sync 合成: %s", e)
            return []

        logger.info("reassemble_video: 合成前将 sync %d 个镜头的选中版本", len(videos))
        return videos

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")
