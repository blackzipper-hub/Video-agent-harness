"""
VideoAgent 服务 - 视频生成智能代理
"""
import logging
import math
import asyncio
import os
from typing import Dict, Any, List, Optional, Union, cast, Tuple, TYPE_CHECKING
from datetime import datetime
import json
import uuid
import time
import functools
from dataclasses import dataclass

# 导入 CRUD 函数
from ...crud.video.video_character import update_character_version_multi_view_image_full
from ...crud.conversation import async_merge_conversation_run_stage_timing

if TYPE_CHECKING:
    from ...schemas.video.video_generation import VideoGenerationDB, VideoGenerationVersionDB
    from ...schemas.video.video_character import VideoCharacterGenerationVersionDB
    from ...api.agent.agent_router_endpoints import VideoAssemblyVideoRequest, SyncSegmentsVideoVersionRequest, VideoSegmentVersionRequest
    from ...models.user_options import UserOption
    from ...models.image_result import CharacterImageGenerationResult
    from ...models.video_state import CharacterProfile

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage
from openai import AsyncOpenAI
from prompts.prompt_config import PromptName
from langgraph.graph import StateGraph, END
from langgraph.runtime import Runtime
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Send, interrupt, Command
from pydantic import ValidationError
from langsmith import get_current_run_tree, traceable

from .video.video_analysis_service import video_analysis_node as video_analysis_node_func
from .video.outline_generation_service import outline_generation_node as outline_generation_node_func
from .video.main_character_design_service import main_character_design_node as main_character_design_node_func
from .video.storyboard_detail_generation_service import storyboard_detail_generation_node
from .video.storyboard_first_frame_revision_service import storyboard_first_frame_revision_node
from .video.per_shot_generation_routing_service import per_shot_generation_routing_node
from .video.scene_generation_service import scene_generation_node


from .video.visual_elements_matching_service import visual_elements_matching_node
from .video.character_fusion_service import character_fusion_node
from .video.agent_video_constants import ENABLE_MULTIVIEW, ENABLE_FUSION
from .video.video_segments_service import video_segments_node
from .video.keyframe_reflection_service import keyframe_reflection_node
from .schemas import VideoContextSchema
from .utils.cancellation import raise_if_cancelled
from ...models.video_state import (
    VideoAgentState,
    StoryboardDetailLLMOutput, DetailedShot,
    Keyframe, VideoSegment, FinalVideo, CharacterProfile, BackgroundMusic, MusicClipInfo, MusicVersion,
    GenerationPhase, KeyframeWithVersions, VideoGenerationWithVersions,
    KeyframeVersion, VideoGenerationVersion, VideoAssembly,
    AudioTranscription, AudioSegment, StoryboardScene,
    CharacterAnalysisAction, NarrationVersion, NarrationWithVersions,
    AudioEffectVersion, AudioEffectWithVersions, VideoAssemblyData, MusicAnalysisResult,
    CharacterImageInfo,
)
from ...exceptions import BusinessException, BusinessExceptionCode
from ...models.user_options import UserOption, ImageGenerationTool
from ...models.version_regenerate_strategy import (
    CharacterRegenerateStrategy,
    KeyframeRegenerateStrategy,
    VideoRegenerateStrategy,
    parse_character_regenerate_strategy,
    parse_keyframe_regenerate_strategy,
    parse_video_regenerate_strategy,
)
from .video.regenerate.regenerate_by_request_service import _coerce_version_regenerate_strategy
from ...schemas.video.video_keyframe import VideoKeyframeVersionDB
from ...schemas.video.video_generation import VideoGenerationVersionDB, VideoGenerationDB
from ...models.error_tracking import UserActionType
from ...services.tool_service import ToolService
from ...utils.s3_utils import s3_utils
from ...models.tool_enums import get_target_pixels_for_video
from .base_agent import BaseAgent, MessageType, MessageRole
from ...utils.i18n import get_current_language
# 路由逻辑已内置到服务中

logger = logging.getLogger(__name__)


def _build_default_video_workflow_path(state: VideoAgentState) -> List[Dict[str, str]]:
    """与 cuti-front-end MessageArea 步骤 id 对齐；优先用 state 已解析的 workflow_path。"""
    cached = state.get("workflow_path") if isinstance(state, dict) else None
    if isinstance(cached, list) and cached:
        return cached
    from .video.music_generation_service import build_video_workflow_path, MusicIntentType

    user_input_data = state.get("user_input_data")
    audio_files = list(user_input_data.audio_files or []) if user_input_data else []
    intent_raw = state.get("music_intent")
    intent = None
    if intent_raw:
        try:
            intent = MusicIntentType(str(intent_raw))
        except ValueError:
            intent = None
    from ...models.user_options import VideoGenerationTool

    user_option = user_input_data.user_option if user_input_data else None
    is_sora = bool(
        user_option
        and user_option.video_generation_tool
        in (VideoGenerationTool.OPENAI_SORA, VideoGenerationTool.OPENAI_SORA_PRO)
    )
    path, _ = build_video_workflow_path(
        has_audio=bool(audio_files),
        music_intent=intent,
        include_music=not is_sora,
    )
    return path


async def _sync_prepare_merge_segment(
    segment_number: int,
    video_gen_versions: list,
    segment_data: dict,
    audio_duration: float,
) -> Tuple:
    """Sync 路径公共逻辑：S3 迁移 + 构建 detailed_shot_ids + 合并视频 + 计算状态。
    更新已有 segment 和创建新 segment 时共用，消除重复代码。

    Returns:
        (SegmentMergeResult, video_urls)
    """
    from .video.video_segments_service import merge_segment_videos

    # S3 迁移：所有有 video_url 的 version 迁移到本 CDN
    url_by_version: Dict[str, str] = {}
    for v in video_gen_versions:
        if not v.video_url:
            continue
        target_w, target_h = get_target_pixels_for_video(
            getattr(v, "resolution", None), getattr(v, "aspect_ratio", None)
        )
        try:
            url = await s3_utils.ensure_video_on_our_s3(
                v.video_url,
                generation_id=v.uuid,
                target_width=target_w,
                target_height=target_h,
                update_version_uuid=v.uuid,
            )
            url_by_version[v.uuid] = url
        except Exception as e:
            logger.warning(f"兜底迁移 video_generation_version {v.uuid[:8]}... 失败: {e}")

    # 构建 per-shot 信息：ALL shots（包括失败的），保持顺序
    all_shot_video_urls: List[Optional[str]] = [
        url_by_version.get(v.uuid) for v in video_gen_versions
    ]

    video_generations_list = segment_data.get('video_generations') or []
    all_detailed_shot_ids: List[Optional[str]] = []
    for idx, v in enumerate(video_gen_versions):
        vg = video_generations_list[idx] if idx < len(video_generations_list) else None
        sid = getattr(vg, 'detailed_shot_id', None) if vg else None
        all_detailed_shot_ids.append(sid)

    from ...models.tool_enums import GenerationMode as _GM
    is_lipsync = any(
        getattr(v, 'generation_mode', None) == _GM.LIPSYNC.value
        for v in video_gen_versions
    )

    target_duration = float(audio_duration)
    successful_count = sum(1 for u in all_shot_video_urls if u)
    logger.info(
        f"📊 Segment {segment_number}: {successful_count}/{len(video_gen_versions)} 视频成功, "
        f"目标时长={target_duration:.2f}s, is_lipsync={is_lipsync}"
    )
    if is_lipsync:
        logger.info(
            f"🎭 Segment {segment_number}: lipsync 镜头，合并走 merge_and_trim_lipsync_videos（禁止整片调速）"
        )

    merge_result = await merge_segment_videos(
        segment_number=segment_number,
        all_video_urls=all_shot_video_urls,
        target_duration=target_duration,
        all_detailed_shot_ids=all_detailed_shot_ids,
        is_lipsync=is_lipsync,
    )
    logger.info(f"🎯 Segment {segment_number} 状态: {merge_result.status.value}")

    video_urls = [u for u in all_shot_video_urls if u]
    return merge_result, video_urls


async def _sync_segment_duration_for_db(
    segment_number: int,
    audio_duration: float,
    merge_result,
) -> float:
    """Sync 写 DB 用合并后 MSC probe，与 pipeline Layer3 一致。"""
    from ...utils import media_service_client as msc

    duration_float = float(audio_duration)
    if not (merge_result.success and merge_result.merged_video_url):
        return duration_float
    try:
        _merged_vi = await msc.video_info(merge_result.merged_video_url)
        _merged_probe = float(_merged_vi.get("duration", 0))
        _merged_delta = _merged_probe - duration_float
        if abs(_merged_delta) > 0.02:
            logger.warning(
                "[时长诊断·Sync Layer3] 片段 %s 写DB: target=%.4fs actual_probe=%.4fs"
                " Δ=%+.4fs → DB写入实测时长",
                segment_number, duration_float, _merged_probe, _merged_delta,
            )
        else:
            logger.info(
                "[时长诊断·Sync Layer3] 片段 %s 写DB: target=%.4fs actual_probe=%.4fs"
                " Δ=%+.4fs → DB写入实测时长",
                segment_number, duration_float, _merged_probe, _merged_delta,
            )
        if _merged_probe > 0:
            return _merged_probe
    except Exception as e:
        logger.warning(
            "[时长诊断·Sync Layer3] 片段 %s video_info 失败，回退 audio target: %s",
            segment_number, e,
        )
    return duration_float


# 状态更新类型定义

class VideoAgentService(BaseAgent):
    """视频代理服务 - 集成编辑和评估功能"""
    
    def __init__(self):
        """初始化服务"""
        super().__init__()
        # default LLM 已移除：各 node 内部按需 load_prompt（见 08-video-agent-default-llm-call-chain.md）

        # ⭐ BGM 后台任务存储：key=run_id, value=asyncio.Task
        # BGM 节点变为 fire-and-forget launcher，不阻塞 LangGraph superstep。
        # video_assembly_node 中 await 此任务以确保 BGM 数据就绪。
        self._bgm_tasks: Dict[str, asyncio.Task] = {}

        # 构建工作流图
        self.agent = self._build_graph()
    
    
    def _timed_node(self, name: str, fn):
        """包装 node：成功完成后把墙钟累加进 conversation_runs.additional_data.stage_timings。

        - gate_* 节点跳过（用户等待，不计入耗时）。
        - 仅在成功完成时记录；node 抛出（interrupt / 错误）时不记录，异常原样向上抛。
        - 记录失败不影响主流程（best-effort）。
        """
        if name.startswith("gate_"):
            return fn

        @functools.wraps(fn)
        async def _wrapped(state, runtime):
            _t0 = time.perf_counter()
            result = await fn(state, runtime)
            try:
                elapsed = round(time.perf_counter() - _t0, 3)
                run_id = state.get("run_id") if isinstance(state, dict) else None
                if run_id:
                    await async_merge_conversation_run_stage_timing(run_id, name, elapsed)
            except Exception as _e:
                logger.debug("stage_timing record skip (%s): %s", name, _e)
            return result

        return _wrapped

    def _add_timed_node(self, workflow, name: str, fn):
        """注册 node 时套上耗时埋点（见 _timed_node）。"""
        workflow.add_node(name, self._timed_node(name, fn))

    def _build_graph(self):
        """构建智能路由的LangGraph工作流"""
        workflow = StateGraph(VideoAgentState)
        
        # 添加节点
        self._add_timed_node(workflow, "user_input_analysis", self.user_input_analysis_node)
        self._add_timed_node(workflow, "video_analysis", self.video_analysis_node)
        self._add_timed_node(workflow, "outline_generation", self.outline_generation_node)
        self._add_timed_node(workflow, "main_character_design", self.main_character_design_node)
        self._add_timed_node(workflow, "scene_generation", self.scene_generation_node)
        self._add_timed_node(workflow, "visual_elements_matching", self.visual_elements_matching_node)  # 新增：视觉元素匹配节点
        self._add_timed_node(workflow, "storyboard_detail_generation", self.storyboard_detail_generation_node)
        self._add_timed_node(workflow, "storyboard_first_frame_revision", self.storyboard_first_frame_revision_node)  # 首帧合规修订
        self._add_timed_node(workflow, "per_shot_generation_routing", self.per_shot_generation_routing_node)  # per-shot generation_mode（原 video 内逻辑）
        self._add_timed_node(workflow, "character_fusion", self.character_fusion_node)  # 新增：角色融合图生成节点
        self._add_timed_node(workflow, "keyframe_generation", self.keyframe_generation_node)
        self._add_timed_node(workflow, "keyframe_reflection", self.keyframe_reflection_node)
        self._add_timed_node(workflow, "narration_generation", self.narration_generation_node)
        self._add_timed_node(workflow, "video_generation", self.video_generation_node)
        self._add_timed_node(workflow, "audio_effect_generation", self.audio_effect_generation_node)
        self._add_timed_node(workflow, "music_generation", self.music_generation_node)
        self._add_timed_node(workflow, "music_bgm_generation", self.music_bgm_generation_node)  # ← 新增：并行BGM生成
        self._add_timed_node(workflow, "video_segments", self.video_segments_node)
        self._add_timed_node(workflow, "video_assembly", self.video_assembly_node)
        # Interrupt 门控节点：6 处
        self._add_timed_node(workflow, "gate_after_music", self.gate_after_music_node)
        self._add_timed_node(workflow, "gate_after_outline", self.gate_after_outline_node)
        self._add_timed_node(workflow, "gate_after_character", self.gate_after_character_node)
        self._add_timed_node(workflow, "gate_after_storyboard_detail", self.gate_after_storyboard_detail_node)
        self._add_timed_node(workflow, "gate_after_keyframe_reflection", self.gate_after_keyframe_reflection_node)
        self._add_timed_node(workflow, "gate_after_shots", self.gate_after_shots_node)

        # ========== 设置入口点和主线流程 ==========
        workflow.set_entry_point("user_input_analysis")
        workflow.add_edge("user_input_analysis", "music_generation")  # ✅ 前置：先处理音频/音乐
        workflow.add_edge("music_generation", "gate_after_music")  # 门控：音乐生成后确认
        workflow.add_edge("gate_after_music", "video_analysis")  # ✅ 使用实际音乐时长
        workflow.add_edge("video_analysis", "outline_generation")
        
        # ========== outline 后门控 → 角色设计 → 门控 → 场景 → BGM launcher ==========
        # ⭐ BGM 放在角色确认之后、scene_generation 之后，避免在用户未确认角色前就拉 BGM。
        #    music_bgm_generation_node 为 fire-and-forget launcher，启动后台任务后立即返回；
        #    gate_after_keyframe_reflection 中 await 该任务，interrupt 前 BGM 已就绪。
        workflow.add_edge("outline_generation", "gate_after_outline")  # 门控：大纲生成后确认
        workflow.add_edge("gate_after_outline", "main_character_design")
        
        # ========== 主线：角色设计 → 门控（1）→ 场景 → BGM launcher → 视觉匹配 → 分镜/融合 ==========
        workflow.add_edge("main_character_design", "gate_after_character")
        workflow.add_edge("gate_after_character", "scene_generation")
        workflow.add_edge("scene_generation", "music_bgm_generation")  # ✅ 第一个 interrupt 之后，scene 后启动 BGM
        workflow.add_edge("music_bgm_generation", "visual_elements_matching")
        workflow.add_edge("visual_elements_matching", "storyboard_detail_generation")
        workflow.add_edge("visual_elements_matching", "character_fusion")  # 并行

        # ========== 分镜 → 门控 → 首帧修订 → per-shot generation_mode → keyframe + narration ==========
        # 首帧修订必须在 keyframe 之前完成；per_shot_generation_routing 分配 generation_mode 并写回 DB（docs/auto-model-selection-proposal.md §5）
        # narration 须在 per_shot_routing 之后，以便跳过 empty_shot；keyframe 需等 routing 与 character_fusion 都完成（fan-in）
        workflow.add_edge("storyboard_detail_generation", "gate_after_storyboard_detail")  # 门控：分镜细节后确认
        workflow.add_edge("gate_after_storyboard_detail", "storyboard_first_frame_revision")
        workflow.add_edge("storyboard_first_frame_revision", "per_shot_generation_routing")
        workflow.add_edge(["per_shot_generation_routing", "character_fusion"], "keyframe_generation")  # fan-in
        workflow.add_edge("per_shot_generation_routing", "narration_generation")

        # ========== keyframe → reflection → 门控（2）→ video ==========
        workflow.add_edge("keyframe_generation", "keyframe_reflection")
        workflow.add_edge("keyframe_reflection", "gate_after_keyframe_reflection")
        workflow.add_edge(["gate_after_keyframe_reflection", "narration_generation"], "video_generation")

        # ========== 视频片段后门控（3）→ segments → assembly ==========
        workflow.add_edge("video_generation", "gate_after_shots")
        workflow.add_edge("gate_after_shots", "video_segments")

        # ========== 汇聚：等待所有图内分支完成 ==========
        workflow.add_edge(["narration_generation", "video_segments", "music_bgm_generation"], "video_assembly")
        workflow.add_edge("video_assembly", END)
        
        # 编译图
        logger.info("✅ VideoAgent智能路由工作流图构建完成")
        return workflow.compile(checkpointer=True, name="Video Agent")
    
    def route_after_scene_generation(self, state: VideoAgentState) -> str:
        """场景生成后的路由决策"""
        # 检查是否需要扩展角色
        scene_uuids = state.get("scene_uuids", [])
        if scene_uuids:
            return "extend_characters"
        return "continue_storyboard"

    # ==================== Interrupt 门控节点 ====================
    # 6 处：after_music → after_outline → after_character → after_storyboard_detail → after_keyframe_reflection → after_shots

    def _blocking_failure_payload(
        self, state: VideoAgentState, stage: str
    ) -> Optional[Dict[str, Any]]:
        """若 state 里有匹配本 stage 的阻断失败，返回失败中断 payload，否则 None。

        generation 节点已写入 state["stage_failure"] 并发过 GENERATION_FAILED 对话事件；
        gate 在这里据此做「失败暂停」：interrupt 后 worker 读到 disable_auto_resume 不排自动继续。
        """
        from .video.stage_failure import (
            get_blocking_failure_for_stage,
            build_failure_interrupt_payload,
        )
        failure = get_blocking_failure_for_stage(state, stage)
        if failure:
            return build_failure_interrupt_payload(failure)
        return None

    async def gate_after_music_node(
        self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]
    ) -> Union[VideoAgentState, Dict[str, Any]]:
        """音乐生成完成后暂停，等待用户确认音乐/时长再继续分析。

        ✨ Smart Clip：从 ``music_generation_uuids[0]`` 的 ``additional_data.smart_clip`` 拉出
        分析结果（lyrics_provided / auto_lyrics_song 路径才有），随 interrupt payload 一起带给前端，
        前端在 after_music 暂停 UI 内嵌入 SmartClipModal；用户决策由 resume_data.smart_clip 携带回来。

        full_auto（``state.full_auto``，仅 SmartTest 端到端走这个分支；前端 user_option.full_auto
        是另一套「15s 倒计时自动继续」机制，不会进 state.full_auto）：True 时直接跳过 interrupt。
        """
        if state.get("full_auto"):
            logger.info("🎵 [gate_after_music] state.full_auto=True，跳过 interrupt（SmartTest 路径）")
            return {}
        from .video.music_generation_service import (
            build_gate_after_music_interrupt_payload,
            should_skip_gate_after_music,
        )
        if should_skip_gate_after_music(state):
            logger.info(
                "🎵 [gate_after_music] music_workflow_mode=%s，跳过 interrupt（与 path 无 music 对齐）",
                state.get("music_workflow_mode"),
            )
            return {}
        _fail_payload = self._blocking_failure_payload(state, "music")
        if _fail_payload is not None:
            logger.warning("⛔ [gate_after_music] 音乐生成失败，失败暂停（不自动继续）")
            interrupt(_fail_payload)
            return {}
        credit_estimate = {}
        try:
            from .video.cost_estimation import get_credit_estimate_for_gate
            credit_estimate = await get_credit_estimate_for_gate(state, "after_music")
        except Exception as e:
            logger.debug("gate_after_music credit estimate skip: %s", e)
        time_estimate = {}
        try:
            from .video.time_estimation import get_time_estimate_for_gate
            time_estimate = await get_time_estimate_for_gate(state, "after_music")
        except Exception as e:
            logger.debug("gate_after_music time estimate skip: %s", e)

        # 拉 smart_clip（可选；BGM / 用户上传音频路径无此字段；灰度 flag 关闭也跳过）
        smart_clip_payload = None
        try:
            from ...config import settings as _settings
            if getattr(_settings, "MUSIC_SMART_CLIP_ENABLED", True):
                mg_uuids = state.get("music_generation_uuids") or []
                if mg_uuids:
                    from ...crud.video.video_audio import get_music_generation_by_uuid
                    mg = await get_music_generation_by_uuid(mg_uuids[0])
                    ad = getattr(mg, "additional_data", None) or {}
                    if isinstance(ad, dict):
                        sc = ad.get("smart_clip")
                        if isinstance(sc, dict) and sc.get("status") == "ready":
                            # 把 music_generation_uuid 一起回传，前端 resume_data 直接带回
                            smart_clip_payload = {**sc, "music_generation_uuid": mg_uuids[0]}
                logger.info(
                    "🎵 [gate_after_music] smart_clip lookup: mg_count=%d, mg[0]=%s, ad_has_smart_clip=%s, status=%s, payload_ready=%s",
                    len(mg_uuids),
                    (mg_uuids[0] if mg_uuids else None),
                    isinstance(ad, dict) and ("smart_clip" in ad),
                    (isinstance(ad, dict) and isinstance(ad.get("smart_clip"), dict) and ad.get("smart_clip", {}).get("status")),
                    smart_clip_payload is not None,
                )
            else:
                logger.info("🎵 [gate_after_music] MUSIC_SMART_CLIP_ENABLED=False, 跳过 smart_clip")
        except Exception as e:
            logger.warning("🎵 [gate_after_music] 读取 smart_clip 失败（不阻塞 gate）: %s", e)

        payload = build_gate_after_music_interrupt_payload(
            state,
            credit_estimate=credit_estimate,
            smart_clip_payload=smart_clip_payload,
        )
        if isinstance(payload, dict):
            payload["time_estimate"] = time_estimate
        if smart_clip_payload is not None:
            logger.info(
                "🎵 [gate_after_music] interrupt WITH smart_clip: mg=%s, method=%s, recommended=[%.2f-%.2f]s, peaks_count=%s",
                smart_clip_payload.get("music_generation_uuid"),
                smart_clip_payload.get("method"),
                float(smart_clip_payload.get("recommended", {}).get("start_sec", 0.0)),
                float(smart_clip_payload.get("recommended", {}).get("end_sec", 0.0)),
                len((smart_clip_payload.get("peaks") or {}).get("peaks") or []) if isinstance(smart_clip_payload.get("peaks"), dict) else "n/a",
            )
        else:
            logger.info("🎵 [gate_after_music] interrupt WITHOUT smart_clip（普通确认）")
        # ⚠️ interrupt() 第一次执行时抛出挂起 graph；resume 时返回 resume_data 并继续往下执行节点剩余代码。
        # 因此可以在这里检测 smart_clip apply hook 是否已经切了 transcription，并把 state 的
        # actual_target_duration / audio_transcription_uuids 同步刷新成截断版，避免下游 video_analysis
        # 等节点继续按原始（未截断）时长算镜头数。
        interrupt(payload)
        update: Dict[str, Any] = {}
        try:
            thread_id = state.get("thread_id", "") or ""
            if thread_id:
                from ...crud.video.video_audio import get_video_audio_transcription_by_thread_id
                latest_tr = await get_video_audio_transcription_by_thread_id(thread_id)
                if latest_tr is not None:
                    is_trimmed = bool((getattr(latest_tr, "additional_data", None) or {}).get("from_smart_clip"))
                    if is_trimmed:
                        old_uuids = state.get("audio_transcription_uuids") or []
                        if latest_tr.uuid not in old_uuids:
                            update["audio_transcription_uuids"] = [latest_tr.uuid]
                        new_dur = float(latest_tr.duration or 0.0)
                        if new_dur > 0 and abs((state.get("actual_target_duration") or 0) - new_dur) > 0.05:
                            update["actual_target_duration"] = new_dur
                        if update:
                            logger.info(
                                "🎵 [gate_after_music] smart_clip 已 apply，刷新 state: tr=%s duration=%.2fs",
                                latest_tr.uuid, new_dur,
                            )
        except Exception as e:
            logger.warning("🎵 [gate_after_music] resume 后刷新 transcription 失败（不阻塞）: %s", e)
        return update

    async def gate_after_outline_node(
        self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]
    ) -> Union[VideoAgentState, Dict[str, Any]]:
        """故事大纲生成完成后暂停，等待用户确认大纲再进入角色设计。full_auto 时不 interrupt。"""
        if state.get("full_auto"):
            return {}
        _fail_payload = self._blocking_failure_payload(state, "outline")
        if _fail_payload is not None:
            logger.warning("⛔ [gate_after_outline] 大纲生成失败，失败暂停（不自动继续）")
            interrupt(_fail_payload)
            return {}
        credit_estimate = {}
        try:
            from .video.cost_estimation import get_credit_estimate_for_gate
            credit_estimate = await get_credit_estimate_for_gate(state, "after_outline")
        except Exception as e:
            logger.debug("gate_after_outline credit estimate skip: %s", e)
        time_estimate = {}
        try:
            from .video.time_estimation import get_time_estimate_for_gate
            time_estimate = await get_time_estimate_for_gate(state, "after_outline")
        except Exception as e:
            logger.debug("gate_after_outline time estimate skip: %s", e)
        interrupt({
            "step": "after_outline",
            "message_key": "video.pause.after_outline",
            "message_default": "故事大纲已就绪，是否确认继续角色设计？",
            "credit_estimate": credit_estimate,
            "time_estimate": time_estimate,
        })
        return {}

    async def gate_after_character_node(
        self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]
    ) -> Union[VideoAgentState, Dict[str, Any]]:
        """主角色设计完成后暂停，等待用户确认再进入场景与分镜。full_auto 时不 interrupt。"""
        if state.get("full_auto"):
            return {}
        _fail_payload = self._blocking_failure_payload(state, "character")
        if _fail_payload is not None:
            logger.warning("⛔ [gate_after_character] 角色生成失败，失败暂停（不自动继续）")
            interrupt(_fail_payload)
            return {}
        credit_estimate = {}
        try:
            from .video.cost_estimation import get_credit_estimate_for_gate
            credit_estimate = await get_credit_estimate_for_gate(state, "after_character")
        except Exception as e:
            logger.debug("gate_after_character credit estimate skip: %s", e)
        time_estimate = {}
        try:
            from .video.time_estimation import get_time_estimate_for_gate
            time_estimate = await get_time_estimate_for_gate(state, "after_character")
        except Exception as e:
            logger.debug("gate_after_character time estimate skip: %s", e)
        interrupt({
            "step": "after_character",
            "message_key": "video.pause.after_character",
            "message_default": "角色设计已就绪，是否继续生成场景与分镜？",
            "credit_estimate": credit_estimate,
            "time_estimate": time_estimate,
        })
        return {}

    async def gate_after_storyboard_detail_node(
        self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]
    ) -> Union[VideoAgentState, Dict[str, Any]]:
        """分镜细节生成完成后暂停，等待用户确认分镜再进入首帧修订与关键帧。full_auto 时不 interrupt。"""
        if state.get("full_auto"):
            return {}
        from .video.music_generation_service import should_skip_keyframe_pipeline_from_state

        skip_kf = should_skip_keyframe_pipeline_from_state(state)
        credit_estimate = {}
        try:
            from .video.cost_estimation import get_credit_estimate_for_gate
            credit_estimate = await get_credit_estimate_for_gate(state, "after_storyboard_detail")
        except Exception as e:
            logger.debug("gate_after_storyboard_detail credit estimate skip: %s", e)
        time_estimate = {}
        try:
            from .video.time_estimation import get_time_estimate_for_gate
            time_estimate = await get_time_estimate_for_gate(state, "after_storyboard_detail")
        except Exception as e:
            logger.debug("gate_after_storyboard_detail time estimate skip: %s", e)
        if skip_kf:
            interrupt({
                "step": "after_storyboard_detail",
                "message_key": "video.pause.after_storyboard_detail_ref_t2v",
                "message_default": "分镜细节已就绪，是否确认继续直接生成视频（跳过关键帧）？",
                "credit_estimate": credit_estimate,
                "time_estimate": time_estimate,
            })
        else:
            interrupt({
                "step": "after_storyboard_detail",
                "message_key": "video.pause.after_storyboard_detail",
                "message_default": "分镜细节已就绪，是否确认继续生成关键帧？",
                "credit_estimate": credit_estimate,
                "time_estimate": time_estimate,
            })
        return {}

    async def gate_after_keyframe_reflection_node(
        self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]
    ) -> Union[VideoAgentState, Dict[str, Any]]:
        """关键帧反思完成后暂停，等待用户确认再生成视频片段。
        在 interrupt 前 await BGM 后台任务，确保第二个门控时 BGM 已就绪并合并进 state。"""
        run_id = state.get("run_id", "")
        bgm_task = self._bgm_tasks.pop(run_id, None)
        if bgm_task is not None:
            try:
                logger.info(f"🎵 第二个门控前等待 BGM 后台任务完成 (run_id={run_id})...")
                bgm_result = await bgm_task
                if bgm_result and isinstance(bgm_result, dict):
                    bgm_uuids = bgm_result.get("music_generation_uuids")
                    if bgm_uuids:
                        existing = state.get("music_generation_uuids") or []
                        state["music_generation_uuids"] = list(existing) + list(bgm_uuids)
                        logger.info(f"✅ BGM 任务完成并合并至 state，music_generation_uuids: {bgm_uuids}")
            except Exception as e:
                logger.error(f"⚠️ BGM 后台任务失败，继续门控（无 BGM）: {e}")
        from .video.music_generation_service import should_skip_keyframe_pipeline_from_state

        # reference_t2v：无关键帧可确认；分镜门控已确认过。仍 await BGM，然后跳过本 interrupt。
        if should_skip_keyframe_pipeline_from_state(state):
            logger.info(
                "🖼️ [gate_after_keyframe_reflection] shot_workflow_mode=reference_t2v，跳过 interrupt"
            )
            return {}
        if state.get("full_auto"):
            return {}
        _fail_payload = self._blocking_failure_payload(state, "keyframe")
        if _fail_payload is not None:
            logger.warning("⛔ [gate_after_keyframe_reflection] 关键帧生成有失败，失败暂停（不自动继续）")
            interrupt(_fail_payload)
            # resume 后重跑关键帧，勿直接进入 video_generation
            logger.info("🔁 [gate_after_keyframe_reflection] resume after failed_keyframe → goto keyframe_generation")
            return Command(goto="keyframe_generation", update={"stage_failure": None})
        credit_estimate = {}
        try:
            from .video.cost_estimation import get_credit_estimate_for_gate
            credit_estimate = await get_credit_estimate_for_gate(state, "after_keyframe_reflection")
        except Exception as e:
            logger.debug("gate_after_keyframe_reflection credit estimate skip: %s", e)
        time_estimate = {}
        try:
            from .video.time_estimation import get_time_estimate_for_gate
            time_estimate = await get_time_estimate_for_gate(state, "after_keyframe_reflection")
        except Exception as e:
            logger.debug("gate_after_keyframe_reflection time estimate skip: %s", e)
        wf_mode = (state.get("music_workflow_mode") or "").strip()
        mg_uuids = state.get("music_generation_uuids") or []
        if wf_mode == "bgm_parallel" and mg_uuids:
            from .video.music_generation_service import build_gate_after_bgm_ready_interrupt_payload

            payload = build_gate_after_bgm_ready_interrupt_payload(
                state, credit_estimate=credit_estimate
            )
            if isinstance(payload, dict):
                payload["time_estimate"] = time_estimate
            logger.info(
                "🎵 [gate_after_keyframe_reflection] bgm_parallel BGM 已就绪，使用试听确认门控 mg_count=%d",
                len(mg_uuids),
            )
        else:
            payload = {
                "step": "after_keyframe_reflection",
                "message_key": "video.pause.after_keyframe_reflection",
                "message_default": "关键帧已就绪，是否继续生成视频片段？",
                "credit_estimate": credit_estimate,
                "time_estimate": time_estimate,
            }
        interrupt(payload)
        return {}

    async def gate_after_shots_node(
        self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]
    ) -> Union[VideoAgentState, Dict[str, Any]]:
        """镜头视频生成完成后暂停，等待用户确认再进入片段合并与成片。full_auto 时不 interrupt。

        口型若需要，已在 video_generation 按 generation_mode=lipsync 完成；本 gate 之后是 segments → assembly。
        """
        if state.get("full_auto"):
            return {}
        _fail_payload = self._blocking_failure_payload(state, "video")
        if _fail_payload is not None:
            logger.warning("⛔ [gate_after_shots] 镜头视频生成有失败，失败暂停（不自动继续）")
            interrupt(_fail_payload)
            # resume 后重跑镜头视频，勿进入 segments/assembly（无可用 VG 时会秒失败）
            logger.info("🔁 [gate_after_shots] resume after failed_video → goto video_generation")
            return Command(goto="video_generation", update={"stage_failure": None})
        credit_estimate = {}
        try:
            from .video.cost_estimation import get_credit_estimate_for_gate
            credit_estimate = await get_credit_estimate_for_gate(state, "after_shots")
        except Exception as e:
            logger.debug("gate_after_shots credit estimate skip: %s", e)
        time_estimate = {}
        try:
            from .video.time_estimation import get_time_estimate_for_gate
            time_estimate = await get_time_estimate_for_gate(state, "after_shots")
        except Exception as e:
            logger.debug("gate_after_shots time estimate skip: %s", e)
        interrupt({
            "step": "after_shots",
            "message_key": "video.pause.after_shots",
            "message_default": "镜头视频已就绪，是否继续合并片段与成片？",
            "credit_estimate": credit_estimate,
            "time_estimate": time_estimate,
        })
        return {}

    # ==================== 节点实现 ====================


    async def user_input_analysis_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """用户输入分析节点 - 分析上传的视频内容 + 创建任务记录"""
        from .video.user_input_analysis_service import user_input_analysis_node
        from ...crud.error_tracking import create_task_record
        
        # 创建任务记录（使用asyncpg CRUD）
        try:
            task_record_uuid = await create_task_record(state=state)
            logger.info(f"📝 创建任务记录: {task_record_uuid}, run_id={state.get('run_id')}")
        except Exception as e:
            logger.error(f"❌ 创建任务记录失败: {e}，继续执行任务")
        
        result = await user_input_analysis_node(
            state=state,
            runtime=runtime,
            send_event_func=self._create_async_send_event_func(runtime, state)
        )
        if isinstance(result, dict):
            _msgs = result.get("messages")
            _uid = result.get("user_input_data")
            logger.info(
                "user_input_analysis_node (wrapper): return keys=%s messages_len=%s has_user_input_data=%s",
                list(result.keys()),
                len(_msgs) if isinstance(_msgs, list) else type(_msgs).__name__,
                _uid is not None,
            )
        else:
            logger.info(
                "user_input_analysis_node (wrapper): return type=%s (non-dict)",
                type(result).__name__,
            )
        from .video.content_category_service import apply_early_content_category_to_result
        from .video.music_generation_service import resolve_video_workflow_at_user_input

        result = await apply_early_content_category_to_result(state, result)
        workflow_update = await resolve_video_workflow_at_user_input(state, result)
        if isinstance(result, dict):
            wf_messages = workflow_update.pop("messages", None)
            result = {**result, **workflow_update}
            if wf_messages:
                existing = result.get("messages") or []
                result["messages"] = [*existing, *wf_messages]
        else:
            result = workflow_update

        send_event_func = self._create_async_send_event_func(runtime, state)
        try:
            merged_state = {**state, **(result if isinstance(result, dict) else {})}
            wf_path = merged_state.get("workflow_path") or _build_default_video_workflow_path(merged_state)
            music_mode = merged_state.get("music_workflow_mode")
            extra_wf: Dict[str, Any] = {
                "workflow_version": "2",
                "run_id": state.get("run_id"),
                "path": wf_path,
            }
            # 注入每个 step 的预估耗时（首包 confidence=low，gate 时再刷新）
            try:
                from .video.time_estimation import (
                    get_time_estimate_from_state,
                    attach_step_seconds_to_path,
                )
                include_music = any(
                    (isinstance(e, dict) and e.get("id") == "music") for e in wf_path
                )
                time_estimate = await get_time_estimate_from_state(
                    merged_state, include_music=include_music
                )
                enriched = attach_step_seconds_to_path(wf_path, time_estimate, music_mode)
                extra_wf["path"] = enriched["path"]
                extra_wf["total_est_seconds"] = enriched["total_est_seconds"]
                extra_wf["estimate_confidence"] = enriched["estimate_confidence"]
            except Exception as te:
                logger.warning("workflow_state time estimate failed: %s", te)
            if music_mode:
                extra_wf["music_mode"] = music_mode
            shot_mode = merged_state.get("shot_workflow_mode")
            if shot_mode:
                extra_wf["shot_mode"] = shot_mode
            if merged_state.get("music_intent"):
                extra_wf["music_intent"] = merged_state["music_intent"]
            await send_event_func(
                event_type=MessageType.WORKFLOW_STATE,
                conversation_id=state.get("conversation_id"),
                extra_data=extra_wf,
                hidden=True,
            )
        except Exception as e:
            logger.warning("workflow_state emit failed: %s", e)
        return result

    async def video_analysis_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """视频需求分析节点"""
        return await video_analysis_node_func(
            state,
            runtime,
            self._create_async_send_event_func(runtime, state)
        )
    
    def _create_async_send_event_func(self, runtime: Runtime[VideoContextSchema], state: VideoAgentState):
        """创建 async_send_event_func，从 state 注入 conversation_uuid 和 run_id，确保事件能推送到 Redis stream。
        resume 时优先使用 config 中的 run_id（当前任务 run_id），否则事件会写入旧 run_id 的 stream。"""
        conversation_uuid = state.get("conversation_uuid") if isinstance(state, dict) else getattr(state, "conversation_uuid", None)
        run_id = state.get("run_id") if isinstance(state, dict) else getattr(state, "run_id", None)
        try:
            from langgraph.config import get_config
            config_run_id = (get_config() or {}).get("configurable", {}).get("run_id")
            if config_run_id:
                run_id = config_run_id
        except Exception:
            pass

        async def async_send_event_with_db(*args, **kwargs):
            if "conversation_uuid" not in kwargs and conversation_uuid:
                kwargs["conversation_uuid"] = conversation_uuid
            # 注入 run_id，否则 async_send_event 不会写入 Redis stream（send_to_stream and run_id 条件）
            if "run_id" not in kwargs and run_id:
                kwargs["run_id"] = run_id
            extra = kwargs.get("extra_data") or {}
            if "run_id" not in extra and run_id:
                extra["run_id"] = run_id
                kwargs["extra_data"] = extra
            return await self.async_send_event(*args, **kwargs)
        return async_send_event_with_db
    
    async def outline_generation_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """故事梗概和分镜大纲生成节点（合并）"""
        return await outline_generation_node_func(
            state, 
            runtime, 
            self._create_async_send_event_func(runtime, state)
        )

    async def main_character_design_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """主要角色设计节点 - 在故事大纲生成后设计主要角色和配角"""
        return await main_character_design_node_func(state, runtime, self._create_async_send_event_func(runtime, state))


    async def scene_generation_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """场景生成节点 - 基于故事大纲和角色生成场景（LLM 由 service 内部 prompt loader 获取）"""
        return await scene_generation_node(state, runtime, self._create_async_send_event_func(runtime, state))
    
    async def visual_elements_matching_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """视觉元素匹配节点 - 为所有scenes匹配visual elements并更新character_ids"""
        return await visual_elements_matching_node(state, runtime, self._create_async_send_event_func(runtime, state))
    
    async def character_fusion_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """角色融合图生成节点 - 根据模型能力生成多角色融合图（LLM 由 service 内部 prompt loader 获取）"""
        return await character_fusion_node(state, runtime, self._create_async_send_event_func(runtime, state))
            
    async def storyboard_detail_generation_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """详细分镜生成节点 - 基于场景批量生成详细镜头"""
        return await storyboard_detail_generation_node(state, runtime, self._create_async_send_event_func(runtime, state))

    async def storyboard_first_frame_revision_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """分镜首帧合规修订节点 - 检查并修订违反 I2V 首帧约束的镜头描述"""
        return await storyboard_first_frame_revision_node(state, runtime, self._create_async_send_event_func(runtime, state))

    async def per_shot_generation_routing_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """Per-shot generation_mode 分配（原 video_generation 内逻辑；见 per_shot_generation_routing_service）"""
        return await per_shot_generation_routing_node(state, runtime, self._create_async_send_event_func(runtime, state))

    async def keyframe_generation_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """关键帧生成节点 - 为每个镜头生成关键帧"""
        from ...services.agent.video.keyframe_generation_service import keyframe_generation_node
        return await keyframe_generation_node(state, runtime, self._create_async_send_event_func(runtime, state))
    
    async def keyframe_reflection_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """关键帧反思节点 - 分析关键帧的角色一致性并进行优化（节点内按需 load VLM/LLM）"""
        from ...services.agent.video.keyframe_reflection_service import keyframe_reflection_node
        return await keyframe_reflection_node(state, runtime, self._create_async_send_event_func(runtime, state))

    async def narration_generation_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """旁白生成节点 - 为每个镜头生成旁白（节点内按需 load VIDEO_COMPLETION_MESSAGE 的 LLM）"""
        from ...services.agent.video.narration_generation_service import narration_generation_node
        return await narration_generation_node(state, runtime, self._create_async_send_event_func(runtime, state))

    def continue_to_parallel_generation(self, state: VideoAgentState) -> List[Send]:
        """并行调用视频生成和音乐生成的路由方法"""
        return [
            Send("video_generation", state),
            Send("music_generation", state)
        ]
    
    async def audio_effect_generation_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """音效生成节点 - 为视频生成音效（prompt+LLM 在 generate_single_audio_effect 内按需 load）"""
        from ...services.agent.video.audio_effect_generation_service import audio_effect_generation_node
        return await audio_effect_generation_node(state, runtime, self._create_async_send_event_func(runtime, state))
    

    async def music_generation_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """音乐生成节点（前置节点，支持数据库持久化）"""
        from ...services.agent.video.music_generation_service import music_generation_node
        return await music_generation_node(state, runtime, self._create_async_send_event_func(runtime, state))
    
    async def music_bgm_generation_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """音乐BGM生成节点 — fire-and-forget launcher
        
        ⭐ 关键设计：不在此节点中 await BGM 生成结果，而是用 asyncio.create_task 启动后台任务，
        节点立即返回 {}。这样不会阻塞 LangGraph 的 superstep，主线流程可以继续执行。
        
        BGM 后台任务写入数据库后，video_assembly_node 中 await 此任务以确保数据就绪。
        """
        from ...services.agent.video.music_generation_service import music_bgm_generation_node as _bgm_node_impl
        
        # 检查是否需要生成BGM（快速判断，不阻塞）
        generation_config = state.get("generation_config")
        if not generation_config or not generation_config.generate_music:
            logger.info("🎵 跳过BGM生成（不需要或已有音频）")
            return {}
        
        run_id = state.get("run_id", "")
        logger.info(f"🎵 BGM launcher: 启动后台 BGM 生成任务 (run_id={run_id})")
        
        # ⭐ 启动后台任务，不 await —— 不阻塞当前 superstep
        send_event_func = self._create_async_send_event_func(runtime, state)
        task = asyncio.create_task(
            _bgm_node_impl(state, runtime, send_event_func),
            name=f"bgm_generation_{run_id}"
        )
        self._bgm_tasks[run_id] = task
        
        # 立即返回空结果，不阻塞主线
        return {}

    async def video_generation_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """视频片段生成节点（批量处理，支持上下文）"""
        from ...services.agent.video.video_generation_service import video_generation_node
        return await video_generation_node(state, runtime, self._create_async_send_event_func(runtime, state))
    
    
    async def video_segments_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """视频片段处理节点 - 音频驱动的视频合并"""
        return await video_segments_node(state, runtime, self._create_async_send_event_func(runtime, state))

    async def video_assembly_node(self, state: VideoAgentState, runtime: Runtime[VideoContextSchema]) -> Union[VideoAgentState, Dict[str, Any]]:
        """视频合成节点（重构版本，支持数据库持久化和完整关联ID）
        
        ⭐ BGM 通常在 gate_after_keyframe_reflection 中已 await 并合并进 state；
           此处对未 await 的 task 做兜底等待（如旧流程或异常路径）。
        """
        from ...services.agent.video.video_assembly_service import video_assembly_node
        
        # ⭐ 兜底：若 BGM 未在第二个门控中 await（如 resume 等），此处再等
        run_id = state.get("run_id", "")
        bgm_task = self._bgm_tasks.pop(run_id, None)
        if bgm_task is not None:
            try:
                logger.info(f"🎵 等待BGM后台任务完成 (run_id={run_id})...")
                bgm_result = await bgm_task
                # 将 BGM 结果合并到 state（主要是 music_generation_uuids）
                if bgm_result and isinstance(bgm_result, dict):
                    bgm_uuids = bgm_result.get("music_generation_uuids")
                    if bgm_uuids:
                        existing = state.get("music_generation_uuids") or []
                        state["music_generation_uuids"] = list(existing) + list(bgm_uuids)
                        logger.info(f"✅ BGM任务完成，music_generation_uuids: {bgm_uuids}")
                    else:
                        logger.info(f"✅ BGM任务完成（无新 UUID）")
            except Exception as e:
                logger.error(f"⚠️ BGM后台任务失败，继续合成（无BGM）: {e}")
        
        return await video_assembly_node(state, runtime, self._create_async_send_event_func(runtime, state))

    # ===== 直接调用方法（不使用LangGraph） =====
    @traceable(name="Regenerate Keyframes")
    async def regenerate_keyframes_by_request(
        self,
        keyframes: List[Any],
        user_id: str,
        user_option: Optional['UserOption'] = None,
        langsmith_extra: Optional[Dict[str, Any]] = None,
        request_thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """根据请求重新生成关键帧。实现见 `video.regenerate.regenerate_by_request_service`。"""
        from .video.regenerate.regenerate_by_request_service import regenerate_keyframes_by_request as _regenerate_keyframes_by_request
        return await _regenerate_keyframes_by_request(
            self, keyframes, user_id, user_option, langsmith_extra, request_thread_id,
        )

    @traceable(name="Regenerate Videos")
    async def regenerate_videos_by_request(
        self,
        videos: List[Any],
        user_id: str,
        user_option: Optional['UserOption'] = None,
        langsmith_extra: Optional[Dict[str, Any]] = None,
        request_thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """根据请求重新生成视频。实现见 `video.regenerate.regenerate_by_request_service`。"""
        from .video.regenerate.regenerate_by_request_service import regenerate_videos_by_request as _regenerate_videos_by_request
        return await _regenerate_videos_by_request(
            self, videos, user_id, user_option, langsmith_extra, request_thread_id,
        )

    @traceable(name="Regenerate Characters")
    async def regenerate_characters_by_request(self, characters: List[Any], user_id: str, user_option: Optional['UserOption'] = None, langsmith_extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """根据请求重新生成角色"""
        from ...crud.video.video_character import get_character_version_by_uuid
        from ...crud.error_tracking import create_task_record
        from langsmith import get_current_run_tree
        import uuid
        from datetime import datetime
        
        # 1. 创建任务记录（在方法开始时）
        run_tree = get_current_run_tree()
        run_id = str(run_tree.id) if run_tree and run_tree.id else str(uuid.uuid4())
        
        # 从第一个character获取conversation_id和thread_id（如果存在）
        conversation_id = None  # 默认值
        thread_id = None  # 默认值
        
        if characters:
            # 尝试从第一个character的版本中获取
            first_character_uuid = characters[0].uuid
            if characters[0].versions:
                from ...crud.video.video_character import get_character_by_uuid
                character_db = await get_character_by_uuid(first_character_uuid)
                if character_db:
                    _conv = character_db.get('conversation_id') if isinstance(character_db, dict) else getattr(character_db, 'conversation_id', None)
                    _tid = character_db.get('thread_id') if isinstance(character_db, dict) else getattr(character_db, 'thread_id', None)
                    conversation_id = str(_conv) if _conv else None
                    thread_id = _tid
        
        # 创建任务记录（使用asyncpg CRUD）
        from ...models.task_status import TaskStatus
        state_for_record = {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "task_input": f"Regenerate characters: {len(characters)} characters",
            "task_status": TaskStatus.RUNNING.value,
            "task_start_time": datetime.utcnow()
        }
        task_record_uuid = await create_task_record(state=state_for_record)
        
        results = []
        total_versions = 0
        
        for character_req in characters:
            character_uuid = character_req.uuid
            
            for version_req in character_req.versions:
                total_versions += 1
                version_uuid = version_req.uuid
                custom_prompt = version_req.custom_prompt
                instruction = getattr(version_req, "instruction", None)
                if isinstance(instruction, str):
                    instruction = instruction.strip() or None
                else:
                    instruction = None
                _ch_strategy = _coerce_version_regenerate_strategy(getattr(version_req, "regenerate_strategy", None))

                # 获取角色版本信息（使用asyncpg CRUD，只传 uuid）
                version_db_obj = await get_character_version_by_uuid(version_uuid)
                if not version_db_obj:
                    raise BusinessException(
                        BusinessExceptionCode.RESOURCE_NOT_FOUND,
                        f"角色版本 {version_uuid} 不存在"
                    )
                # 兼容 dict 与 msgspec 对象
                version_user_id = version_db_obj.get('user_id') if isinstance(version_db_obj, dict) else getattr(version_db_obj, 'user_id', None)
                if version_user_id != user_id:
                    raise BusinessException(
                        BusinessExceptionCode.PERMISSION_DENIED,
                        f"无权限访问角色版本 {version_uuid}"
                    )
                
                # 从角色版本重建 user_option，优先用「当时参数」
                from ...services.agent.utils.user_option_rebuild import rebuild_user_option_from_character_version
                effective_user_option = rebuild_user_option_from_character_version(version_db_obj, fallback=user_option)
                result = await self._regenerate_character_version(
                    version_db_obj,
                    custom_prompt,
                    user_id,
                    effective_user_option,
                    instruction=instruction,
                    regenerate_strategy=_ch_strategy,
                )
                _cr = result.get("character_result")
                _t2i_ch = (getattr(_cr, "generated_prompt", None) if _cr else None) if result.get("success") else None
                results.append({
                    "character_uuid": character_uuid,
                    "success": result.get("success", False),
                    "new_version_uuid": result.get("new_version_uuid") if result.get("success") else None,
                    "t2i_prompt": _t2i_ch,
                    "error": result.get("error_msg") if not result.get("success") else None
                })
        
        successful_count = len([r for r in results if r["success"]])
        final_status = "completed" if successful_count > 0 else "failed"

        # 3. task_record：终态（billing 已迁至 conversation_run）
        try:
            from ...crud.error_tracking import update_task_record

            await update_task_record(
                run_id=run_id,
                updates={
                    "task_status": final_status,
                    "task_finish_time": datetime.utcnow(),
                }
            )
        except Exception as e:
            logger.error(f"Regenerate characters 任务记录更新失败: error={e}")

        return {
            "run_id": run_id,
            "conversation_id": conversation_id,
            "thread_id": thread_id,
            "total_versions": total_versions,
            "successful_count": successful_count,
            "failed_count": total_versions - successful_count,
            "results": results
        }
    
    async def _regenerate_character_multi_view_internal(
        self,
        character_db: Any,
        target_version: Any,
        user_id: str,
        user_option: Optional['UserOption'] = None,
        custom_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """内部方法：重新生成单个角色的多视角图"""
        from .video.multi_view_generation_service import generate_multi_view_character_sheet
        
        # 准备角色数据（构造 CharacterProfile 对象）
        from ...models.video_state import CharacterProfile, VisualElementType
        
        character_profile = CharacterProfile(
            id=character_db.uuid,
            type=VisualElementType.CHARACTER if character_db.type == "character" else character_db.type,
            name=character_db.name,
            description=character_db.description,
            role=character_db.role,
            personality=character_db.personality,
            appearance=character_db.appearance,
            style=character_db.style,
            body_type=character_db.body_type
        )
        
        # 读取故事大纲（使用asyncpg CRUD）
        story_outline = None
        if character_db.run_id:
            from ...crud.video.video_story import get_video_story_outline_by_run_id
            story_outline = await get_video_story_outline_by_run_id(character_db.run_id)
        
        # 调用多视角图生成服务（LLM 由 generate_multi_view_character_sheet 内部 prompt loader 获取）
        logger.info(f"🎨 开始重新生成多视角图: character={character_db.name}, version={target_version.version_number}")
        
        # 获取reference_image_urls（如果有）
        reference_images = None
        if target_version.reference_image_urls:
            # 将URL列表转换为简单的对象列表
            reference_images = [{"url": url} for url in target_version.reference_image_urls]
        
        # 从 state 获取 detected_language（如果可用）
        detected_language = None
        if isinstance(getattr(self, 'state', None), dict):
            detected_language = self.state.get("detected_language")
        
        # 使用 custom_prompt 作为 user_input（如果提供）
        user_input_for_generation = custom_prompt or ""
        
        multi_view_result, multi_view_messages = await generate_multi_view_character_sheet(
            element=character_profile,
            main_image_url=target_version.character_image_url,
            story_outline=story_outline,
            user_option=user_option,
            reference_images=reference_images,
            user_input=user_input_for_generation,
            detected_language=detected_language
        )
        
        if not multi_view_result or not multi_view_result.success:
            error_msg = multi_view_result.error_msg if multi_view_result else "多视角图生成失败"
            logger.error(f"❌ 多视角图生成失败: {error_msg}")
            return {
                "success": False,
                "error_msg": error_msg
            }
        
        # 提取 ai_messages
        from ...services.agent.utils.message_utils import extract_ai_message_json
        ai_messages_json = None
        if multi_view_messages:
            result_for_extraction = {"messages": multi_view_messages if isinstance(multi_view_messages, list) else [multi_view_messages]}
            ai_messages_json = extract_ai_message_json(result_for_extraction)
        
        # 创建新的多视角图版本并关联（使用asyncpg CRUD）
        multi_view_version_id = await update_character_version_multi_view_image_full(
            character_uuid=character_db.uuid,
            version_number=target_version.version_number,
            multi_view_image_url=multi_view_result.image_url,
            multi_view_prompt=multi_view_result.generated_prompt,
            user_id=user_id,
            provider=multi_view_result.provider or target_version.provider,
            aspect_ratio=multi_view_result.aspect_ratio,
            resolution=multi_view_result.resolution,
            model=multi_view_result.model,
            seed=multi_view_result.seed,
                success=multi_view_result.success,
                error_msg=multi_view_result.error_msg,
                raw_error_msg=multi_view_result.raw_error_msg,
                ai_messages=ai_messages_json,
                conversation_id=str(character_db.conversation_id) if character_db.conversation_id is not None else None,
                thread_id=character_db.thread_id,
                run_id=character_db.run_id
        )
        
        if not multi_view_version_id:
            logger.error(f"❌ 多视角图版本创建失败")
            return {
                "success": False,
                "error_msg": "多视角图版本创建失败"
            }
        
        # 获取新创建的版本信息和设置选中版本（使用asyncpg CRUD）
        # 获取新创建的版本信息（用于返回版本号）
        from ...crud.video.video_character import get_character_multi_view_image_version_by_id
        multi_view_version = await get_character_multi_view_image_version_by_id(
            multi_view_version_id
        )
        
        logger.info(f"✅ 多视角图重新生成成功: {multi_view_result.image_url} (version_id: {multi_view_version_id})")
        
        # ⭐ 设置角色版本选中的多视角图版本
        from ...crud.video.video_character import update_character_version_selected_multi_view
        await update_character_version_selected_multi_view(target_version.uuid, multi_view_version_id)
        logger.info(f"✅ 设置角色版本选中多视角图版本: {multi_view_version_id}")
        
        return {
            "success": True,
            "multi_view_image_url": multi_view_result.image_url,
            "multi_view_prompt": multi_view_result.generated_prompt,
            "multi_view_image_version_id": multi_view_version_id
        }
    
    async def _regenerate_fusion_for_character(
        self,
        character_id: str,
        run_id: str,
        user_id: str,
        user_option: Optional['UserOption'] = None,
        llm: Any = None
    ) -> Dict[str, Any]:
        """为指定角色重新生成融合图（完全重用 character_fusion_service 的代码）
        
        ✅ 重用逻辑：
        1. 使用 _load_fusion_data_from_db 加载所有数据
        2. 过滤出包含该角色的 scenes
        3. 使用 generate_fusion_images_for_scenes 生成融合图（✅ 重用核心逻辑）
        """
        from ...services.agent.video.character_fusion_service import (
            _load_fusion_data_from_db,
            generate_fusion_images_for_scenes
        )
        from ...crud.video.video_story import get_scenes_by_run_id, get_video_story_outline_by_run_id
        
        # 融合图生成由 generate_fusion_images_for_scenes -> generate_character_fusion_image 内部
        # 按需 load_prompt(VIDEO_CHARACTER_FUSION_IMAGE_GENERATION) 获取 prompt+llm，此处不加载 default LLM
        try:
            # 获取所有 scenes（通过 run_id）- 使用asyncpg CRUD
            scenes_db = await get_scenes_by_run_id(run_id)
            if not scenes_db:
                logger.warning(f"⚠️ 未找到 scenes 数据（run_id: {run_id}），跳过 fusion 生成")
                return {
                    "success": False,
                    "error_msg": "未找到 scenes 数据",
                    "generated_count": 0
                }
            
            scene_uuids = [scene.uuid for scene in scenes_db]
            
            # 获取 story_outline_uuid（用于 _load_fusion_data_from_db）
            story_outline_db = await get_video_story_outline_by_run_id(run_id)
            story_outline_uuid = story_outline_db.uuid if story_outline_db else None
            
            # 获取所有 character_uuids（从 scenes 中收集）
            all_character_ids = set()
            for scene_db in scenes_db:
                if scene_db.character_ids:
                    all_character_ids.update(scene_db.character_ids)
            
            # ✅ 重用 _load_fusion_data_from_db 加载所有数据
            # 构建一个简化的 state（_load_fusion_data_from_db 需要的字段）
            simplified_state: Dict[str, Any] = {
                "scene_uuids": scene_uuids,
                "character_uuids": list(all_character_ids),  # 从 scenes 收集的所有角色
                "user_id": user_id,
                "story_outline_uuid": story_outline_uuid,
                "conversation_id": scenes_db[0].conversation_id if scenes_db else None,
                "thread_id": scenes_db[0].thread_id if scenes_db else None,
                "run_id": run_id
            }
            
            # ✅ 重用现有的数据加载函数
            scenes_data, characters_data, character_images, story_outline = await _load_fusion_data_from_db(
                simplified_state,  # type: ignore
                scene_uuids
            )
            
            if not scenes_data:
                logger.warning(f"⚠️ 未找到 scenes 数据，跳过 fusion 生成")
                return {
                    "success": False,
                    "error_msg": "未找到 scenes 数据",
                    "generated_count": 0
                }
            
            # 找出包含该角色的 scenes
            relevant_scenes = [
                scene for scene in scenes_data
                if scene.character_ids and character_id in scene.character_ids
            ]
            
            if not relevant_scenes:
                logger.info(f"ℹ️ 角色 {character_id} 不在任何 scene 中，跳过 fusion 生成")
                return {
                    "success": True,
                    "generated_count": 0,
                    "message": "角色不在任何 scene 中"
                }
            
            logger.info(f"📊 找到 {len(relevant_scenes)} 个包含角色 {character_id} 的 scenes")
            
            # ✅ 连接已释放，长时间操作（LLM 调用）不占用连接
            
            # ✅ 重用核心逻辑函数（与 character_fusion_node 使用相同的函数）
            saved_count, failed_count, all_messages = await generate_fusion_images_for_scenes(
                scenes_data=relevant_scenes,  # 只传入包含该角色的 scenes
                characters_data=characters_data,
                character_images=character_images,
                story_outline=story_outline,
                user_option=user_option,
                state=simplified_state,  # type: ignore
                llm=None  # 下游 generate_character_fusion_image 内部 load_prompt(VIDEO_CHARACTER_FUSION_IMAGE_GENERATION)
            )
            
            return {
                "success": True,
                "generated_count": saved_count,
                "failed_count": failed_count,
                "total_count": saved_count + failed_count
            }
            
        except Exception as e:
            logger.error(f"❌ 重新生成 Fusion 图失败: {e}", exc_info=True)
            return {
                "success": False,
                "error_msg": str(e),
                "generated_count": 0
            }
    
    async def video_assembly_by_request(self, thread_id: str, segment_versions: Optional[List['VideoSegmentVersionRequest']] = None, user_id: str = None, user_option: Optional['UserOption'] = None, videos: Optional[List['VideoAssemblyVideoRequest']] = None) -> Dict[str, Any]:
        """根据请求重新合成视频。按 thread 组装：用 conversation_id + thread_id 取该 thread 当前全部资源。
        
        Args:
            thread_id: 线程ID（必填）
            segment_versions: 视频片段版本列表（audio-driven模式）
            user_id: 用户ID
            user_option: 用户选项
            videos: 视频版本列表（video-driven模式，传入时先 sync 再合成）
        """
        from ...crud.video.video_story import get_video_story_outline_by_thread_id
        from ...crud.video.video_generation import get_video_generations_by_conversation
        
        story_outline = await get_video_story_outline_by_thread_id(thread_id)
        if not story_outline:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "找不到故事梗概"
            )
        
        # 验证权限（CRUD 返回 VideoStoryOutlineDB）
        if story_outline.user_id != user_id:
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                "无权限访问此数据"
            )
        
        conversation_id = story_outline.conversation_id
        thread_id = story_outline.thread_id
        
        # 按 thread 获取视频生成（该 thread 下当前全部 shot）
        video_generations = await get_video_generations_by_conversation(conversation_id, thread_id)
        if not video_generations:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "找不到视频片段"
            )
        
        # 若请求中带了 shot 版本（videos），先执行 sync segments，再合成（不展示 lyrics 时前端只调 assemble 并传 videos）
        if videos:
            from ...api.agent.agent_router_endpoints import SyncSegmentsVideoVersionRequest
            video_versions = [
                SyncSegmentsVideoVersionRequest(
                    video_generation_uuid=v.uuid,
                    video_generation_version_uuid=v.selected_version.uuid,
                )
                for v in videos
                if v.selected_version
            ]
            if video_versions:
                logger.info(f"🔄 Assemble 前先 sync segments: {len(video_versions)} 个 shot 版本（force=True，不比较版本直接更新）")
                await self.sync_segments_by_request(
                    thread_id=thread_id,
                    video_versions=video_versions,
                    user_id=user_id,
                    user_option=user_option,
                    force=True,
                )
        
        # 构建 request 中 video_uuid -> selected_version_uuid 的映射
        video_uuid_to_selected_version_uuid: Dict[str, str] = {}
        if videos:
            for selected_video in videos:
                if selected_video.selected_version:
                    video_uuid_to_selected_version_uuid[selected_video.uuid] = selected_video.selected_version.uuid
        
        # 一次性获取所有 video_generation 的所有版本（一次SQL查询）
        video_generation_ids = [vg.uuid for vg in video_generations]
        from ...crud.video.video_generation import get_video_generation_versions_by_video_generation_ids
        all_versions = await get_video_generation_versions_by_video_generation_ids(video_generation_ids)
        
        # 构建映射：video_generation_id -> List[version]（按version_number排序）
        video_gen_id_to_versions: Dict[str, List] = {}
        for version in all_versions:
            video_gen_id = version.video_generation_id
            if video_gen_id not in video_gen_id_to_versions:
                video_gen_id_to_versions[video_gen_id] = []
            video_gen_id_to_versions[video_gen_id].append(version)
        
        # 对每个video_generation的版本列表按version_number排序
        for video_gen_id in video_gen_id_to_versions:
            video_gen_id_to_versions[video_gen_id].sort(key=lambda v: v.version_number)
        
        # 构建 version_uuid -> version 的映射，用于快速查找指定版本
        version_uuid_map: Dict[str, Any] = {v.uuid: v for v in all_versions}
        
        # 按 thread 获取旁白、音效、音乐（该 thread 下当前全部资源）
        from ...services.agent.video.video_assembly_service import get_narrations_for_videos, get_audio_effects_for_videos, get_music_data_for_videos
        narrations_data = await get_narrations_for_videos(conversation_id, thread_id)
        audio_effects_data = await get_audio_effects_for_videos(conversation_id, thread_id)
        music_data = await get_music_data_for_videos(conversation_id, thread_id)
        
        # 音频转录：按 thread 取
        audio_transcription = None
        from ...crud.video.video_audio import get_video_audio_transcription_by_thread_id
        from ...services.agent.utils.database_utils import get_audio_transcription_from_db
        
        transcription_db = await get_video_audio_transcription_by_thread_id(thread_id)
        if transcription_db:
            # 使用通用方法转换，additional_data 包含原始 whisper 的 words 和 segments
            audio_transcription = await get_audio_transcription_from_db([transcription_db.uuid])
        
        # 构建选中的视频生成版本列表
        # 版本选择逻辑（三步走）：
        # 1. 如果 request 中指定了版本，使用指定的版本
        # 2. 否则使用 current_version_index 选择当前版本
        # 3. 如果索引无效，使用第一个版本
        selected_video_generations = []
        for video_gen in video_generations:
            video_gen_id = video_gen.uuid
            selected_version = None
            
            # 步骤1：检查 request 中是否指定了版本
            if video_gen_id in video_uuid_to_selected_version_uuid:
                selected_version_uuid = video_uuid_to_selected_version_uuid[video_gen_id]
                selected_version = version_uuid_map.get(selected_version_uuid)
            
            # 步骤2：如果没有指定版本，使用 current_version_index 选择当前版本
            if not selected_version:
                versions = video_gen_id_to_versions.get(video_gen_id, [])
                if versions:
                    current_index = video_gen.current_version_index
                    if 0 <= current_index < len(versions):
                        selected_version = versions[current_index]
                    else:
                        # 步骤3：索引无效，使用第一个版本
                        selected_version = versions[0]
            
            if selected_version and selected_version.success and selected_version.video_url:
                # 转换为 VideoGenerationVersion 对象
                from ...models.video_state import VideoGenerationVersion
                # 从 additional_data 中提取 seed 和 resolution
                seed = None
                resolution = None
                if selected_version.additional_data:
                    seed = selected_version.additional_data.get('seed')
                    resolution = selected_version.additional_data.get('resolution')
                
                video_generation_version = VideoGenerationVersion(
                    shot_number=selected_version.shot_number,
                    video_url=selected_version.video_url,
                    duration=int(math.ceil(selected_version.duration)) if selected_version.duration is not None else 0,
                    i2v_prompt=selected_version.motion_prompt,
                    is_bridge=selected_version.is_bridge,
                    keyframe_url=selected_version.keyframe_url,
                    success=selected_version.success,
                    error_msg=selected_version.error_msg,
                    audio_segment_ids=selected_version.audio_segment_ids,
                    seed=seed,
                    resolution=resolution
                )
                video_generation_version.version_id = selected_version.uuid
                video_generation_version.video_generation_id = selected_version.video_generation_id
                selected_video_generations.append(video_generation_version)
        
        if not selected_video_generations:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "没有可用的视频片段"
            )
        
        # 🔧 关键修复：按 shot_number 排序，确保合并时顺序正确
        selected_video_generations.sort(key=lambda x: x.shot_number)

        # 获取 video segments 数据（用于 audio-driven 模式）；按 thread 取
        video_segments_data, segments_to_update = await self._prepare_video_segments_data(
            conversation_id=conversation_id,
            thread_id=thread_id,
            segment_versions=segment_versions
        )
        
        # 创建视频合成数据对象
        from ...models.video_state import VideoAssemblyData
        assembly_data = VideoAssemblyData(
            story_outline=story_outline,
            video_generations=selected_video_generations,
            narrations_data=narrations_data,
            audio_effects_data=audio_effects_data,
            music_data=music_data,
            audio_transcription=audio_transcription,
            uploaded_audio_files=[],
            video_segments_data=video_segments_data
        )
        
        # 创建 send_event 包装函数，注入 conversation_uuid（与 node 传 _create_async_send_event_func 一致）
        conversation_uuid = None
        if story_outline.conversation_id:
            from ...crud.conversation import async_get_conversation_by_id
            try:
                conversation = await async_get_conversation_by_id(int(story_outline.conversation_id))
                conversation_uuid = conversation.uuid if conversation else None
            except Exception as e:
                logger.warning(f"获取 conversation_uuid 失败: {e}")

        async def send_event_wrapper(*args, **kwargs):
            if "conversation_uuid" not in kwargs and conversation_uuid:
                kwargs["conversation_uuid"] = conversation_uuid
            return await self.async_send_event(*args, **kwargs)

        from ...services.agent.video.video_assembly_service import execute_video_merge_core
        result = await execute_video_merge_core(
            assembly_data,
            send_event_wrapper,
            run_id=getattr(story_outline, "run_id", None) or "",
            thread_id=thread_id,
        )

        # ✅ 在视频合并成功后，批量更新数据库中的 current_version_index（用户选择的片段版本）
        if segments_to_update:
            from ...crud.video.video_segment import batch_update_video_segment_current_version_index
            updates = [
                {"uuid": seg.uuid, "current_version_index": seg.current_version_index}
                for seg in segments_to_update
            ]
            await batch_update_video_segment_current_version_index(updates)
        
        return result

    # ===== 辅助方法 =====
    
    async def _prepare_video_segments_data(
        self,
        conversation_id: str = None,
        thread_id: str = None,
        segment_versions: Optional[List['VideoSegmentVersionRequest']] = None
     ) -> tuple[Dict[int, Dict], List]:
        """准备视频片段数据（用于 audio-driven 模式）。按 thread 取：conversation_id + thread_id。
        
        Args:
            conversation_id: 对话ID（与 thread_id 一起使用）
            thread_id: 线程ID
            segment_versions: 用户选择的片段版本列表
            
        Returns:
            (video_segments_data, segments_to_update)
            - video_segments_data: segment_number -> {segment, version} 的映射
            - segments_to_update: 需要更新 current_version_index 的 segment 对象列表（已废弃，始终返回空列表）
        """
        from ...crud.video.video_segment import (
            get_video_segments_with_data_by_conversation_and_thread,
            get_video_segment_versions_by_segment_ids
        )
        
        if not conversation_id or not thread_id:
            logger.info("📹 缺少 conversation_id 或 thread_id，无 video segments 数据")
            return {}, []
        
        # 按 thread 获取 video segments
        video_segments_db = await get_video_segments_with_data_by_conversation_and_thread(conversation_id, thread_id)
        
        if not video_segments_db:
            logger.info("📹 没有找到 video segments 数据")
            return {}, []
        
        # 🔧 批量获取所有 segment 的所有版本（一次查询，使用通用 crud 函数）
        segment_ids = [seg.uuid for seg in video_segments_db]
        segment_id_to_versions = await get_video_segment_versions_by_segment_ids(segment_ids)
        
        # 🔧 处理 segment_versions 请求 - 构建 uuid 到 version_uuid 的映射
        segment_uuid_to_selected_version_uuid = {}
        version_uuid_map = {}
        
        if segment_versions:
            logger.info(f"🎯 收到 {len(segment_versions)} 个片段版本选择请求")
            segment_uuid_to_selected_version_uuid = {
                sv.segment_uuid: sv.segment_version_uuid
                for sv in segment_versions
            }
            
            # 构建 version_uuid -> version 的映射（从所有版本中）
            for versions in segment_id_to_versions.values():
                for version in versions:
                    version_uuid_map[version.uuid] = version
        
        # 构建 video_segments_data 并处理版本选择
        video_segments_data = {}
        segments_to_update = []  # 需要更新 current_version_index 的 segments
        
        for segment_db in video_segments_db:
            versions = segment_id_to_versions.get(segment_db.uuid, [])
            if not versions:
                logger.warning(f"⚠️ 视频片段 {segment_db.segment_number} 没有版本数据")
                continue
            
            selected_version = None
            selected_version_index = segment_db.current_version_index
            
            # 步骤1：如果 request 中指定了版本，使用指定的版本
            if segment_db.uuid in segment_uuid_to_selected_version_uuid:
                selected_version_uuid = segment_uuid_to_selected_version_uuid[segment_db.uuid]
                selected_version = version_uuid_map.get(selected_version_uuid)
                
                if selected_version:
                    # 找到选中版本的索引
                    for idx, ver in enumerate(versions):
                        if ver.uuid == selected_version_uuid:
                            selected_version_index = idx
                            break
                    
                    # 如果索引改变了，标记需要更新（但不立即更新数据库）
                    if segment_db.current_version_index != selected_version_index:
                        segment_db.current_version_index = selected_version_index
                        segments_to_update.append(segment_db)
                        logger.info(f"✅ 将更新片段 {segment_db.segment_number} 的 current_version_index: {selected_version_index}")
            
            # 步骤2：如果没有指定版本，使用 current_version_index 选择版本
            if not selected_version:
                if 0 <= selected_version_index < len(versions):
                    selected_version = versions[selected_version_index]
                else:
                    # 如果索引无效，使用第一个版本
                    selected_version = versions[0]
                    logger.warning(f"⚠️ 视频片段 {segment_db.segment_number} 的 current_version_index={selected_version_index} 无效，使用第一个版本")
            
            video_segments_data[segment_db.segment_number] = {
                'segment': segment_db,
                'version': selected_version
            }
            vurl = (selected_version.video_url or "") if selected_version else ""
            logger.info(
                f"   [更新视频] _prepare_video_segments_data segment_number={segment_db.segment_number} segment_uuid={segment_db.uuid[:8]}... "
                f"video_url={'有' if vurl else '无'}({vurl[:50] + '...' if len(vurl) > 50 else vurl}) success={getattr(selected_version, 'success', None)}"
            )
        
        logger.info(f"📹 获取到 {len(video_segments_data)} 个视频片段数据")
        return video_segments_data, segments_to_update
    

    async def _regenerate_keyframe_with_reflection(self, version_db: 'VideoKeyframeVersionDB', user_id: str, user_option: Optional['UserOption'] = None) -> Dict[str, Any]:
        """使用 AI reflection 优化并重新生成关键帧（小火箭功能）
        
        工作流程：
        1. 调用 reflect_single_keyframe 进行 VLM 分析
        2. 如果 VLM 判断需要重新生成，使用优化后的描述重新生成
        3. 创建新的关键帧版本和编辑记录
        
        Args:
            version_db: 关键帧版本数据库对象
            user_id: 用户ID
            user_option: 用户选项
        
        Returns:
            包含生成结果的字典
        """
        from ...crud.video.video_story import get_detailed_shot_by_id
        from ...crud.video.video_keyframe import get_keyframe_by_uuid
        from ...services.agent.utils.database_utils import get_characters_from_db, get_character_images_with_latest_versions
        from ...services.agent.video.keyframe_reflection_service import reflect_single_keyframe
        from ...models.video_state import KeyframeVersion, DetailedShot, CharacterProfile
        
        # 读取数据（使用asyncpg CRUD）
        # 获取关联的关键帧以获取detailed_shot_id
        keyframe_db = await get_keyframe_by_uuid(version_db.keyframe_id)
        if not keyframe_db:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "关联的关键帧不存在"
            )
        
        # 获取详细镜头信息
        shot = await get_detailed_shot_by_id(keyframe_db.detailed_shot_id) if keyframe_db.detailed_shot_id else None
        if not shot:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "找不到对应的详细镜头信息"
            )
        
        # 获取所有可用的角色数据（从conversation中获取）
        from ...crud.video.video_character import get_characters_by_conversation
        all_characters_db = await get_characters_by_conversation(version_db.conversation_id, version_db.thread_id)
        
        # 获取角色图片
        character_images = {}
        if shot.character_ids:
            character_images = await get_character_images_with_latest_versions(shot.character_ids, user_id)
        
        # 转换为CharacterProfile格式
        all_characters_data = []
        for char_db in all_characters_db:
            all_characters_data.append(CharacterProfile(
                id=char_db.uuid,
                name=char_db.name,
                description=char_db.description or "",
                appearance=char_db.appearance or "",
                personality=char_db.personality or "",
                role=char_db.role or "",
                type=char_db.type,
                style=char_db.style or ""
            ))
        
        # 构建 KeyframeVersion 对象
        keyframe_version = KeyframeVersion(
            shot_number=keyframe_db.shot_number,
            is_bridge=keyframe_db.is_bridge,
            reference_image_urls=keyframe_db.reference_image_urls or [],
            keyframe_url=version_db.keyframe_url,
            t2i_prompt=version_db.t2i_prompt,
            provider=version_db.provider,
            success=version_db.success,
            error_msg=version_db.error_msg,
            audio_segment_ids=version_db.audio_segment_ids,
            version_id=version_db.uuid,
            keyframe_uuid=keyframe_db.uuid,
            scene_id=keyframe_db.scene_id,
            storyboard_detail_id=keyframe_db.storyboard_detail_id,
            detailed_shot_id=keyframe_db.detailed_shot_id
        )
        
        # 构建角色映射
        char_map = {char.id: char for char in all_characters_data}
        
        # 构建 state（用于 reflection）；regenerate_single_keyframe 从 state["user_input_data"].user_option 读取
        from types import SimpleNamespace
        state = {
            "conversation_id": version_db.conversation_id,
            "thread_id": version_db.thread_id,
            "run_id": version_db.run_id,
            "user_id": user_id,
            "user_input_data": SimpleNamespace(user_option=user_option),
        }
        
        reflection_result, _ = await reflect_single_keyframe(
            keyframe=keyframe_version,
            shot=shot,
            characters=all_characters_data,
            character_images=character_images,
            char_map=char_map,
            state=state,
            auto_regenerate=True
        )
        
        # 处理 reflection 结果
        storyboard_edit_record_id = None
        if reflection_result.needs_regeneration and reflection_result.new_keyframe_uuid:
            # Reflection 已自动重新生成，获取新版本信息（使用asyncpg CRUD）
            from ...crud.video.video_keyframe import get_keyframe_version_by_uuid, get_keyframe_versions_by_keyframe_ids
            
            new_version_db = await get_keyframe_version_by_uuid(reflection_result.new_keyframe_uuid)
            if new_version_db:
                # 创建编辑记录
                try:
                    from ...crud.error_tracking import create_storyboard_edit_record, get_task_record_by_run_id
                    
                    # 获取任务记录
                    task_record = await get_task_record_by_run_id(version_db.run_id)
                    
                    # 获取下一个版本号
                    existing_versions = await get_keyframe_versions_by_keyframe_ids([version_db.keyframe_id])
                    version_number = len(existing_versions)
                    
                    storyboard_edit_record_id = await create_storyboard_edit_record(
                            task_record_id=task_record.uuid if task_record else None,
                            keyframe_id=version_db.keyframe_id,
                            user_id=user_id,
                            conversation_id=version_db.conversation_id,
                            thread_id=version_db.thread_id,
                            run_id=version_db.run_id,
                            shot_number=keyframe_version.shot_number,
                            old_version_id=version_db.uuid,
                            new_version_id=reflection_result.new_keyframe_uuid,
                            old_version_number=version_db.version_number,
                            new_version_number=version_number,
                            old_image_url=version_db.keyframe_url,
                            new_image_url=reflection_result.new_keyframe_url or "",
                            model=new_version_db.provider or version_db.provider,
                            old_prompt=version_db.t2i_prompt,
                            new_prompt=reflection_result.new_t2i_prompt or new_version_db.t2i_prompt,
                            user_action=UserActionType.RE_GEN,  # Reflection 优化属于重新生成
                            user_feedback=None,
                            edit_instruction=f"AI Reflection 优化: {reflection_result.analysis_summary}",
                            success=True,
                            error_msg=None
                    )
                    logger.info(f"📝 创建 Storyboard 编辑记录（Reflection）: shot={keyframe_version.shot_number}, record_id={storyboard_edit_record_id}")
                except Exception as e:
                    logger.error(f"❌ 创建 Storyboard 编辑记录失败: {e}，但继续执行")
            
            return {
                "success": True,
                "keyframe_version": keyframe_version,
                "new_version_uuid": reflection_result.new_keyframe_uuid,
                "storyboard_edit_record_id": storyboard_edit_record_id,
                "reflection_result": {
                    "needs_regeneration": reflection_result.needs_regeneration,
                    "analysis_summary": reflection_result.analysis_summary,
                    "improved_description": reflection_result.improved_description,
                    "issues": [issue.dict() for issue in reflection_result.issues] if reflection_result.issues else []
                },
                "error_msg": None
            }
        else:
            # Reflection 判断不需要重新生成
            logger.info(f"✅ Reflection 分析完成，镜头 {keyframe_version.shot_number} 无需重新生成")
            return {
                "success": False,
                "keyframe_version": keyframe_version,
                "new_version_uuid": None,
                "storyboard_edit_record_id": None,
                "reflection_result": {
                    "needs_regeneration": False,
                    "analysis_summary": reflection_result.analysis_summary,
                    "improved_description": "",
                    "issues": [issue.dict() for issue in reflection_result.issues] if reflection_result.issues else []
                },
                "error_msg": "Reflection 分析判断无需重新生成"
            }
    
    async def _regenerate_keyframe_version(
        self,
        version_db: 'VideoKeyframeVersionDB',
        custom_prompt: Optional[str],
        user_id: str,
        user_option: Optional['UserOption'] = None,
        llm: Any = None,
        instruction: Optional[str] = None,
        regenerate_strategy: Optional[str] = None,
    ) -> Dict[str, Any]:
        """重新生成单个关键帧版本
        
        ✅ 修复：移除 db 参数，内部按需创建连接
        
        生成策略：
        1. custom_prompt（前端请求）：
           - 与 node 一致用 _build_character_images_dict 取图，再 resolve + execute（不调 generate_batch_keyframes）
        
        2. 非 custom_prompt：
           - 调用 keyframe 三部曲全部：generate_batch_keyframes
           - 包含：生成 prompt + 评估修正 + 并发调用工具
           - 系统自动生成 prompt，并进行内容审核和一致性评估
           - ⚠️ 注意：此分支目前没有实际使用场景，前端请求时总是会传递 custom_prompt
        
        Edit Record 逻辑：
        - custom_prompt 分支：创建 Storyboard 编辑记录，判断是 RE_GEN 还是 EDIT_PROMPT
        - 非 custom_prompt 分支：创建 Storyboard 编辑记录，固定为 RE_GEN（目前没有实际使用场景）
        """
        from ...crud.video.video_story import get_detailed_shot_by_id
        from ...crud.video.video_keyframe import get_keyframe_by_uuid
        from ...services.agent.utils.database_utils import get_characters_from_db
        
        # 关键帧执行由 execute_batch_keyframe_generation 内部按 shot 调用
        # _build_single_keyframe_tool_call_prompt 获取 prompt+llm，此处不加载 default LLM
        # 读取数据（使用asyncpg CRUD）
        # 获取关联的关键帧以获取detailed_shot_id
        keyframe_db = await get_keyframe_by_uuid(version_db.keyframe_id)
        if not keyframe_db:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "关联的关键帧不存在"
            )
        
        # 获取详细镜头信息
        shot = await get_detailed_shot_by_id(keyframe_db.detailed_shot_id) if keyframe_db.detailed_shot_id else None
        if not shot:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "找不到对应的详细镜头信息"
            )
        
        # 🎯 在regenerate之前，先重新匹配visual elements
        # 注意：shot.character_ids已经包含了scene的character_ids（因为detail生成时是从scene继承的）
        existing_character_ids = shot.character_ids if shot.character_ids else []
        
        # 获取所有可用的角色数据（从conversation中获取）
        from ...crud.video.video_character import get_characters_by_conversation
        all_characters_db = await get_characters_by_conversation(version_db.conversation_id, version_db.thread_id)
        
        # 转换为CharacterProfile格式
        from ...models.video_state import CharacterProfile
        all_characters_data = []
        for char_db in all_characters_db:
            all_characters_data.append(CharacterProfile(
                id=char_db.uuid,
                name=char_db.name,
                description=char_db.description or "",
                appearance=char_db.appearance or "",
                personality=char_db.personality or "",
                role=char_db.role or "",
                type=char_db.type,
                style=char_db.style or ""
            ))
        
            # 如果没有获取到，尝试从existing_character_ids获取
            if not all_characters_data and existing_character_ids:
                all_characters_data = await get_characters_from_db(existing_character_ids)
        
        # ✅ 连接已释放，长时间操作（LLM 调用）不占用连接
        # 有 custom_prompt 和/或 instruction：resolve + execute（不调 generate_batch_keyframes）
        _cp = (custom_prompt or "").strip() if custom_prompt else ""
        _ins = (instruction or "").strip() if instruction else ""
        try:
            _norm_st = parse_keyframe_regenerate_strategy(regenerate_strategy)
        except ValueError:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                f"不支持的关键帧 regenerate_strategy: {regenerate_strategy}",
            )
        if _norm_st == KeyframeRegenerateStrategy.PROMPT_REGENERATE and not _cp:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "prompt_regenerate 需要非空 custom_prompt",
            )
        if _norm_st == KeyframeRegenerateStrategy.INSTRUCTION_REGENERATE and not _ins:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "instruction_regenerate 需要非空 instruction",
            )
        if _norm_st == KeyframeRegenerateStrategy.INSTRUCTION_MERGE_PROMPT and not _ins:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "instruction_merge_prompt 需要非空 instruction",
            )
        if _norm_st == KeyframeRegenerateStrategy.INSTRUCTION_EDIT_IMAGE and not _ins:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "instruction_edit_image 需要非空 instruction",
            )
        if _norm_st == KeyframeRegenerateStrategy.INSTRUCTION_EDIT_IMAGE and not (getattr(version_db, "keyframe_url", None) or "").strip():
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "instruction_edit_image 需要当前版本已有成图 keyframe_url",
            )

        tid_for_lang = getattr(version_db, "thread_id", None)
        detected_language_rf: Optional[str] = None
        if tid_for_lang:
            from .video.regenerate.regenerate_keyframe_service import _get_detected_language_from_db
            detected_language_rf = await _get_detected_language_from_db(tid_for_lang)

        if _norm_st == KeyframeRegenerateStrategy.INSTRUCTION_MERGE_PROMPT:
            from ...models.video_state import KeyframeVersion as KfVersionModel
            from .video.regenerate.instruction_merge_prompt import instruction_merge_to_full_prompt

            base_for_merge = _cp if _cp else (getattr(version_db, "t2i_prompt", None) or "").strip()
            merged_t2i = await instruction_merge_to_full_prompt(
                base_prompt=base_for_merge,
                instruction=_ins,
                asset_kind="关键帧 t2i 画面",
                detected_language=detected_language_rf,
            )
            keep_url = (getattr(version_db, "keyframe_url", None) or "").strip()
            ref_urls = getattr(version_db, "reference_image_urls", None) or []
            if not isinstance(ref_urls, list):
                ref_urls = []
            shot_number_rf = int(getattr(keyframe_db, "shot_number", shot.shot_number) or 0)
            frame_index_rf = int(getattr(keyframe_db, "frame_index", 0) or 0)
            _cur_ver_uuid = str(getattr(version_db, "uuid", None) or "")
            kf_obj_rf = KfVersionModel(
                shot_number=shot_number_rf,
                keyframe_url=keep_url,
                t2i_prompt=merged_t2i,
                provider=getattr(version_db, "provider", "") or "",
                is_bridge=bool(getattr(keyframe_db, "is_bridge", False)),
                reference_image_urls=ref_urls or None,
                success=True,
                frame_index=frame_index_rf,
                version_id=_cur_ver_uuid,
                keyframe_uuid=version_db.keyframe_id,
            )
            return {
                "success": True,
                "keyframe_version": kf_obj_rf,
                "new_version_uuid": None,
                "storyboard_edit_record_id": None,
                "error_msg": None,
            }

        if _norm_st == KeyframeRegenerateStrategy.INSTRUCTION_EDIT_IMAGE:
            from ...crud.video.video_keyframe import (
                create_keyframe_version,
                get_keyframe_versions_by_keyframe_ids,
            )
            from ...models.video_state import KeyframeVersion as KfVersionModelIe
            from .video.regenerate.instruction_merge_prompt import instruction_merge_to_full_prompt
            from .video.regenerate.regenerate_image_edit_i2i import run_regenerate_image_edit_i2i

            src_kf_url = (getattr(version_db, "keyframe_url", None) or "").strip()
            img_res = await run_regenerate_image_edit_i2i(
                instruction=_ins,
                reference_image_urls=[src_kf_url],
                user_option=user_option,
                shot_for_resolve=shot,
                detected_language=detected_language_rf,
                skip_consistency_check=False,
            )
            if not img_res or not img_res.success:
                err = (img_res.error_msg if img_res else None) or "图像编辑失败"
                return {
                    "success": False,
                    "keyframe_version": None,
                    "new_version_uuid": None,
                    "storyboard_edit_record_id": None,
                    "error_msg": err,
                }
            new_img_url = (img_res.image_url or "").strip()
            base_prompt_ie = (getattr(version_db, "t2i_prompt", None) or "").strip()
            try:
                new_t2i_ie = await instruction_merge_to_full_prompt(
                    base_prompt=base_prompt_ie,
                    instruction=_ins,
                    asset_kind="关键帧基于成图的编辑（已反映到新图）",
                    detected_language=detected_language_rf,
                )
            except Exception as _merge_e:
                logger.warning("image_edit: t2i prompt merge fallback: %s", _merge_e)
                new_t2i_ie = base_prompt_ie
            ref_urls_ie = getattr(version_db, "reference_image_urls", None) or []
            if not isinstance(ref_urls_ie, list):
                ref_urls_ie = []
            existing_ie = await get_keyframe_versions_by_keyframe_ids([version_db.keyframe_id])
            version_number_ie = len(existing_ie) + 1
            shot_number_ie = int(getattr(keyframe_db, "shot_number", shot.shot_number) or 0)
            frame_index_ie = int(getattr(keyframe_db, "frame_index", 0) or 0)
            char_ver_ids_ie = getattr(version_db, "character_version_ids", None)
            prov_str_ie = (img_res.provider or "") or (getattr(version_db, "provider", None) or "")
            new_ver_uuid_ie = await create_keyframe_version(
                keyframe_id=version_db.keyframe_id,
                version_number=version_number_ie,
                shot_number=shot_number_ie,
                keyframe_url=new_img_url,
                t2i_prompt=new_t2i_ie,
                provider=prov_str_ie or "",
                is_bridge=bool(getattr(keyframe_db, "is_bridge", False)),
                reference_image_urls=ref_urls_ie,
                success=True,
                conversation_id=str(version_db.conversation_id),
                thread_id=version_db.thread_id,
                run_id=version_db.run_id,
                user_id=user_id,
                character_version_ids=char_ver_ids_ie if isinstance(char_ver_ids_ie, list) else None,
                aspect_ratio=getattr(version_db, "aspect_ratio", None),
                resolution=getattr(version_db, "resolution", None),
                model=img_res.model or getattr(version_db, "model", None),
                image_generation_tool=getattr(version_db, "image_generation_tool", None),
            )
            kf_obj_ie = KfVersionModelIe(
                shot_number=shot_number_ie,
                keyframe_url=new_img_url,
                t2i_prompt=new_t2i_ie,
                provider=prov_str_ie or "",
                is_bridge=bool(getattr(keyframe_db, "is_bridge", False)),
                reference_image_urls=ref_urls_ie or None,
                success=True,
                frame_index=frame_index_ie,
                version_id=new_ver_uuid_ie,
                keyframe_uuid=version_db.keyframe_id,
            )
            return {
                "success": True,
                "keyframe_version": kf_obj_ie,
                "new_version_uuid": new_ver_uuid_ie,
                "storyboard_edit_record_id": None,
                "error_msg": None,
            }

        if _norm_st in (KeyframeRegenerateStrategy.PROMPT_REGENERATE, KeyframeRegenerateStrategy.INSTRUCTION_REGENERATE) and (_cp or _ins):
            from ...services.agent.video.keyframe_generation_service import (
                _build_character_images_dict,
                KeyframePromptResult,
                resolve_keyframe_prompt_reference_images,
                execute_batch_keyframe_generation,
            )

            # 获取角色图片（使用asyncpg CRUD，CharacterImageInfo 内含 profile）
            character_images = await _build_character_images_dict(set(shot.character_ids or []), user_id)

            if _ins and not _cp:
                base_t2i = (getattr(version_db, "t2i_prompt", None) or "").strip()
            else:
                base_t2i = _cp
            user_regen = _ins if _ins else None
            single_prompt = KeyframePromptResult(
                shot_number=shot.shot_number,
                t2i_prompt=base_t2i,
                frame_index=0,
                user_regenerate_instruction=user_regen,
            )
            # 复用 version_db.reference_image_urls：编辑 prompt / instruction 重新生成时沿用上次匹配过的参考图,
            # 避免因模型能力配置/常量变更而误丢 location 等图。空 list 视为"上一次本就无 refs"，仍走重新匹配。
            _reuse_urls_pr = getattr(version_db, "reference_image_urls", None)
            if not isinstance(_reuse_urls_pr, list) or not _reuse_urls_pr:
                _reuse_urls_pr = None
            else:
                # 变更检测：仅当这一镜所有视觉元素（人物/物品/场所）的当前选中版本 == 关键帧存的版本时才复用；
                # 只要有元素换了版本/被增删 → 不复用，按当前选中版本重新匹配，避免复用旧角色/场所 ref。
                from ...services.agent.video.keyframe_generation_service import should_reuse_ref_image_urls
                _stored_cvids = getattr(version_db, "character_version_ids", None)
                if not should_reuse_ref_image_urls(shot, character_images, _stored_cvids):
                    logger.info(
                        "🔄 元素版本变化，跳过复用旧 ref，按当前选中版本重新匹配：shot=%s stored_cvids=%s",
                        getattr(shot, "shot_number", None), _stored_cvids,
                    )
                    _reuse_urls_pr = None
            resolved_prompts = await resolve_keyframe_prompt_reference_images(
                shots_batch=[shot],
                character_images=character_images,
                prompts=[single_prompt],
                user_option=user_option,
                user_id=user_id,
                reuse_ref_image_urls=_reuse_urls_pr,
            )
            _messages, keyframe_versions = await execute_batch_keyframe_generation(
                shots_batch=[shot],
                character_images=character_images,
                prompts=resolved_prompts,
                user_option=user_option,
                shared_semaphore=None,
                user_input=_ins if _ins else base_t2i,
                first_frame_images=None,
                user_id=user_id,
                skip_consistency_check=True,
                reuse_ref_image_urls=_reuse_urls_pr,
            )

            # 获取生成结果
            keyframe_version = keyframe_versions[0]
            keyframe_version.version_id = version_db.uuid
            keyframe_version.keyframe_uuid = version_db.keyframe_id

            # ✅ 写回数据时使用新连接
            new_version_uuid = None
            storyboard_edit_record_id = None
            if keyframe_version.success:
                from ...crud.video.video_keyframe import create_keyframe_version, get_keyframe_versions_by_keyframe_ids

                # 获取下一个版本号
                existing_versions = await get_keyframe_versions_by_keyframe_ids([version_db.keyframe_id])
                version_number = len(existing_versions) + 1

                # 获取该镜头使用的角色版本ID列表（与 _build_character_images_dict 选中版本一致）
                character_version_ids = []
                if shot.character_ids:
                    try:
                        from ...services.agent.video.keyframe_generation_service import _build_character_images_dict
                        _cim = await _build_character_images_dict(set(shot.character_ids), user_id)
                        for char_id in shot.character_ids:
                            ci = _cim.get(char_id)
                            if ci and getattr(ci, "version_id", None):
                                character_version_ids.append(ci.version_id)
                    except Exception as e:
                        logger.warning(f"获取角色版本失败: {e}")

                # 准备 additional_data（兼容老数据）及再生参数字段
                additional_data = {}
                if keyframe_version.seed is not None:
                    additional_data['seed'] = keyframe_version.seed
                if keyframe_version.resolution:
                    additional_data['resolution'] = keyframe_version.resolution
                from ...services.agent.utils.database_utils import _str_attr
                image_tool_for_insert = getattr(version_db, "image_generation_tool", None)
                new_version_uuid = await create_keyframe_version(
                    keyframe_id=version_db.keyframe_id,
                    version_number=version_number,
                    shot_number=keyframe_version.shot_number,
                    keyframe_url=keyframe_version.keyframe_url,
                    t2i_prompt=keyframe_version.t2i_prompt,
                    provider=getattr(keyframe_version, "provider", version_db.provider),
                    is_bridge=keyframe_version.is_bridge,
                    reference_image_urls=keyframe_version.reference_image_urls,
                    success=keyframe_version.success,
                    conversation_id=version_db.conversation_id,
                    thread_id=version_db.thread_id,
                    run_id=version_db.run_id,
                    user_id=user_id,
                    error_msg=keyframe_version.error_msg,
                    audio_segment_ids=getattr(keyframe_version, "audio_segment_ids", None),
                    ai_messages=getattr(keyframe_version, "ai_messages_json", None),
                    character_version_ids=character_version_ids if character_version_ids else None,
                    additional_data=additional_data if additional_data else None,
                    aspect_ratio=_str_attr(keyframe_version, "aspect_ratio"),
                    resolution=_str_attr(keyframe_version, "resolution"),
                    seed=getattr(keyframe_version, "seed", None),
                    model=getattr(keyframe_version, "model", None),
                    image_generation_tool=image_tool_for_insert,
                )

                # 创建编辑记录（此分支为 custom_prompt 前端请求）
                try:
                    from ...crud.error_tracking import create_storyboard_edit_record, get_task_record_by_run_id

                    # 判断用户操作类型
                    user_action = UserActionType.RE_GEN
                    if _ins or (_cp and _cp != (version_db.t2i_prompt or "")):
                        user_action = UserActionType.EDIT_PROMPT

                    # 获取任务记录
                    task_record = await get_task_record_by_run_id(version_db.run_id)

                    storyboard_edit_record_id = await create_storyboard_edit_record(
                        task_record_id=task_record.uuid if task_record else None,
                        keyframe_id=version_db.keyframe_id,
                        user_id=user_id,
                        conversation_id=version_db.conversation_id,
                        thread_id=version_db.thread_id,
                        run_id=version_db.run_id,
                        shot_number=keyframe_version.shot_number,
                        old_version_id=version_db.uuid,
                        new_version_id=new_version_uuid,
                        old_version_number=version_db.version_number,
                        new_version_number=version_number,
                        old_image_url=version_db.keyframe_url,
                        new_image_url=keyframe_version.keyframe_url,
                        model=getattr(keyframe_version, 'provider', version_db.provider),
                        old_prompt=version_db.t2i_prompt,
                        new_prompt=keyframe_version.t2i_prompt,
                        user_action=user_action,
                        user_feedback=None,  # TODO: 前端传递
                        edit_instruction=_ins if _ins else None,
                        success=keyframe_version.success,
                        error_msg=keyframe_version.error_msg
                    )
                    logger.info(f"📝 创建 Storyboard 编辑记录: shot={keyframe_version.shot_number}, action={user_action}, record_id={storyboard_edit_record_id}")
                except Exception as e:
                    logger.error(f"❌ 创建 Storyboard 编辑记录失败: {e}，但继续执行")

                logger.info(f"✅ 关键帧重新生成成功: {new_version_uuid}")
            else:
                logger.error(f"❌ 关键帧重新生成失败: {keyframe_version.error_msg}")

            return {
                "success": keyframe_version.success,
                "keyframe_version": keyframe_version,
                "new_version_uuid": new_version_uuid,
                "storyboard_edit_record_id": storyboard_edit_record_id,
                "error_msg": keyframe_version.error_msg
            }
        else:
            # ⚠️ 注意：此分支（非 custom_prompt）目前没有实际使用场景
            # 前端请求时总是会传递 custom_prompt（要么是用户输入的，要么是当前版本的 prompt）
            # 此分支保留用于未来可能的场景（如系统自动重新生成）
            # 没有自定义提示词，调用generate_batch_keyframes让系统自动生成prompt
            from ...services.agent.video.keyframe_generation_service import generate_batch_keyframes, _build_character_images_dict
            
            # 获取角色图片：与关键帧节点一致，使用选中角色版本
            character_images = {}
            if shot.character_ids:
                character_images = await _build_character_images_dict(set(shot.character_ids), user_id)
            
            # 调用generate_batch_keyframes（批量处理单个镜头，系统自动生成prompt；内部按需 load_prompt）
            messages, keyframe_versions = await generate_batch_keyframes(
                shots_batch=[shot],
                character_images=character_images,
                prev_shot=None,
                next_shot=None,
                user_option=user_option,
                skip_consistency_check=True,
            )
            
            # 获取生成结果（generate_batch_keyframes 一定会返回结果）
            keyframe_version = keyframe_versions[0]
            # 更新version_id以保持一致性
            keyframe_version.version_id = version_db.uuid
            keyframe_version.keyframe_uuid = version_db.keyframe_id
            
            # ✅ 写回数据时使用新连接
            new_version_uuid = None
            storyboard_edit_record_id = None
            if keyframe_version.success:
                from ...crud.video.video_keyframe import create_keyframe_version, get_keyframe_versions_by_keyframe_ids
                
                # 获取下一个版本号
                existing_versions = await get_keyframe_versions_by_keyframe_ids([version_db.keyframe_id])
                version_number = len(existing_versions) + 1
                
                # 获取该镜头使用的角色版本ID列表（与上方 character_images 选中版本一致）
                character_version_ids = []
                if shot.character_ids:
                    for char_id in shot.character_ids:
                        ci = character_images.get(char_id)
                        if ci and getattr(ci, "version_id", None):
                            character_version_ids.append(ci.version_id)
                
                # 准备 additional_data 及再生参数字段
                additional_data = {}
                if keyframe_version.seed is not None:
                    additional_data['seed'] = keyframe_version.seed
                if keyframe_version.resolution:
                    additional_data['resolution'] = keyframe_version.resolution
                from ...services.agent.utils.database_utils import _str_attr
                image_tool_for_insert = getattr(version_db, "image_generation_tool", None)
                new_version_uuid = await create_keyframe_version(
                    keyframe_id=version_db.keyframe_id,
                    version_number=version_number,
                    shot_number=keyframe_version.shot_number,
                    keyframe_url=keyframe_version.keyframe_url,
                    t2i_prompt=keyframe_version.t2i_prompt,
                    provider=getattr(keyframe_version, "provider", version_db.provider),
                    is_bridge=keyframe_version.is_bridge,
                    reference_image_urls=keyframe_version.reference_image_urls,
                    success=keyframe_version.success,
                    conversation_id=version_db.conversation_id,
                    thread_id=version_db.thread_id,
                    run_id=version_db.run_id,
                    user_id=user_id,
                    error_msg=keyframe_version.error_msg,
                    audio_segment_ids=getattr(keyframe_version, "audio_segment_ids", None),
                    ai_messages=getattr(keyframe_version, "ai_messages_json", None),
                    character_version_ids=character_version_ids if character_version_ids else None,
                    additional_data=additional_data if additional_data else None,
                    aspect_ratio=_str_attr(keyframe_version, "aspect_ratio"),
                    resolution=_str_attr(keyframe_version, "resolution"),
                    seed=getattr(keyframe_version, "seed", None),
                    model=getattr(keyframe_version, "model", None),
                    image_generation_tool=image_tool_for_insert,
                )
                
                # 创建编辑记录（非 custom_prompt 自动重新生成）
                # ⚠️ 注意：此分支（非 custom_prompt）目前没有实际使用场景
                # 前端请求时总是会传递 custom_prompt，此分支保留用于未来可能的场景
                try:
                    from ...crud.error_tracking import create_storyboard_edit_record, get_task_record_by_run_id
                    
                    # 判断用户操作类型（无自定义prompt时，都是RE_GEN）
                    user_action = UserActionType.RE_GEN
                    
                    # 获取任务记录
                    task_record = await get_task_record_by_run_id(version_db.run_id)
                    
                    storyboard_edit_record_id = await create_storyboard_edit_record(
                        task_record_id=task_record.uuid if task_record else None,
                        keyframe_id=version_db.keyframe_id,
                        user_id=user_id,
                        conversation_id=version_db.conversation_id,
                        thread_id=version_db.thread_id,
                        run_id=version_db.run_id,
                        shot_number=keyframe_version.shot_number,
                        old_version_id=version_db.uuid,
                        new_version_id=new_version_uuid,
                        old_version_number=version_db.version_number,
                        new_version_number=version_number,
                        old_image_url=version_db.keyframe_url,
                        new_image_url=keyframe_version.keyframe_url,
                        model=getattr(keyframe_version, 'provider', version_db.provider),
                        old_prompt=version_db.t2i_prompt,
                        new_prompt=keyframe_version.t2i_prompt,  # 使用系统生成的新prompt
                        user_action=user_action,
                        user_feedback=None,
                        edit_instruction=None,
                        success=keyframe_version.success,
                        error_msg=keyframe_version.error_msg
                    )
                    logger.info(f"📝 创建 Storyboard 编辑记录: shot={keyframe_version.shot_number}, action={user_action}, record_id={storyboard_edit_record_id}")
                except Exception as e:
                    logger.error(f"❌ 创建 Storyboard 编辑记录失败: {e}，但继续执行")
                
                logger.info(f"✅ 关键帧重新生成成功: {new_version_uuid}")
            else:
                logger.error(f"❌ 关键帧重新生成失败: {keyframe_version.error_msg}")
            
            return {
                "success": keyframe_version.success,
                "keyframe_version": keyframe_version,
                "new_version_uuid": new_version_uuid,
                "storyboard_edit_record_id": storyboard_edit_record_id,
                "error_msg": keyframe_version.error_msg
            }
    
    async def _regenerate_character_with_custom_prompt(
        self,
        character: 'CharacterProfile', 
        images: List[Dict[str, str]], 
        custom_prompt: str, 
        user_option: Optional['UserOption'] = None,
        detected_language: Optional[str] = None,
        user_regenerate_instruction: Optional[str] = None,
    ) -> 'CharacterImageGenerationResult':
        """处理自定义提示词的角色重新生成（参考execute_batch_keyframe_generation模式）"""
        from ...services.tool_service import ToolService
        from ...models.image_result import CharacterImageGenerationResult
        from prompts.prompt_config import PROMPTS_CONFIG, PromptName
        from ...services.agent.utils.llm_resilience import (
            StructuredResilienceKind,
            ainvoke_structured_resilient,
        )
        from typing import cast

        try:
            # 根据是否有参考图片选择对应的工具模式
            from ...models.tool_enums import ToolMode, DefaultValues
            if images:
                tools_info = ToolService.get_image_generation_tools(user_option, ToolMode.I2I)
                image_tools = tools_info.tool_objects
                logger.info(f"🎨 使用I2I工具重新生成角色图片（自定义提示词，有{len(images)}张参考图片）")
            else:
                tools_info = ToolService.get_image_generation_tools(user_option, ToolMode.T2I)
                image_tools = tools_info.tool_objects
                logger.info(f"🎨 使用T2I工具重新生成角色图片（自定义提示词，无参考图片）")
            
            tool_name = user_option.image_generation_tool if user_option else DefaultValues.DEFAULT_IMAGE_TOOL_NAME
            
            # skill-based tool director prompt
            from langchain_core.messages import SystemMessage, HumanMessage
            from app.orchestration.skills.prompt_context import (
                facts_human_message,
                skill_system_message,
            )

            mode = "i2i" if images else "t2i"
            _char_lang = (detected_language or "en").strip() or "en"
            has_regen = bool(user_regenerate_instruction and str(user_regenerate_instruction).strip())
            regen_ins = (user_regenerate_instruction or "").strip()
            ref_urls = [img.get("url") for img in images if img.get("url")] if images else []

            system = skill_system_message(
                "character-regen-tool-director",
                lead="Follow character-regen-tool-director.",
            )
            facts = {
                "tool_name": tool_name,
                "mode": mode,
                "character": {
                    "name": character.name,
                    "description": character.description or "",
                    "appearance": character.appearance or "",
                    "personality": character.personality or "",
                    "style": character.style or "",
                    "role": character.role or "",
                },
                "reference_image_urls": ref_urls,
                "reference_image_count": len(ref_urls),
                "detected_language": _char_lang,
                "t2i_prompt": custom_prompt,
                "has_user_regenerate_instruction": has_regen,
                "user_regenerate_instruction": regen_ins if has_regen else "",
            }
            messages = [
                SystemMessage(content=system),
                HumanMessage(content=facts_human_message(facts)),
            ]
            from .utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
            apply_language_suffix_to_system_message_in_messages(messages, detected_language)
            
            from ...tools.context_schemas import ImageGenerationContext

            # 创建 context：model 从 tools_info.tool_type 取，与选链一致
            reference_image_urls = []
            if images:
                reference_image_urls = [img.get("url") for img in images if img.get("url")]
            
            from ...models.tool_enums import AspectRatio, Resolution, DefaultValues
            
            context = ImageGenerationContext(
                aspect_ratio=(
                    AspectRatio(user_option.aspect_ratio.value)
                    if user_option and user_option.aspect_ratio
                    else DefaultValues.IMAGE_ASPECT_RATIO
                ),
                resolution=(
                    Resolution(user_option.resolution.value)
                    if user_option and user_option.resolution
                    else DefaultValues.IMAGE_RESOLUTION
                ),
                model=tools_info.tool_type if tools_info and tools_info.tools else None,
                reference_image_urls=reference_image_urls if reference_image_urls else None,
                language=detected_language,  # 穿透到 tool runtime 供 i18n 使用
            )
            
            inputs = {"messages": messages}
            result = await ainvoke_structured_resilient(
                kind=StructuredResilienceKind.CREATE_AGENT,
                prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_SINGLE_CHARACTER_TOOL_CALL_PROMPT],
                agent_inputs=inputs,
                agent_tools=image_tools,
                context_schema=ImageGenerationContext,
                agent_invoke_context=context,
                wrap_agent_parse_fallback=True,
                log_context={"phase": "regenerate_character_custom_prompt"},
            )
            
            # 获取结构化结果
            image_result: CharacterImageGenerationResult = cast(CharacterImageGenerationResult, result.get("structured_response"))
            
            logger.info(f"✅ 自定义提示词LLM生成角色 {character.name} 完成，成功: {image_result.success if image_result else False}")
            
            return image_result
            
        except Exception as e:
            error_msg = f"自定义提示词LLM生成角色 {character.name} 失败: {str(e)}"
            logger.error(error_msg)
            # 返回失败的CharacterImageGenerationResult
            return CharacterImageGenerationResult(
                success=False,
                message=error_msg,
                image_url="",
                generated_prompt=custom_prompt,
                provider="",
                reference_image_urls=[]
            )

    async def _save_character_version_to_database(
        self,
        version_db: 'VideoCharacterGenerationVersionDB', 
        image_result: 'CharacterImageGenerationResult', 
        custom_prompt: Optional[str], 
        user_option: Optional['UserOption'], 
        user_id: str,
        edit_instruction: Optional[str] = None,
    ) -> str:
        """保存角色版本到数据库（参考character_design_utils的逻辑）"""
        from ...crud.video.video_character import create_character_version, get_character_versions_by_character_id
        
        # 处理图片URL和成功状态
        character_image_url = ""
        if image_result and image_result.success and image_result.image_url:
            character_image_url = image_result.image_url
            logger.info(f"✅ 角色重新生成图片成功: {character_image_url}")
        else:
            logger.warning(f"⚠️ 角色重新生成图片失败")
        
        # 获取下一个版本号
        existing_versions = await get_character_versions_by_character_id(version_db.video_character_id, user_id)
        version_number = len(existing_versions) + 1
        
        # ⭐ 处理多视角图：如果是 version 2+，从 version 1 复用多视角图版本ID
        multi_view_image_version_id = None
        
        if version_number > 1:
            try:
                from ...crud.video.video_character import get_character_versions_by_character_id
                
                # 获取 version 1 的版本记录
                version_1_list = await get_character_versions_by_character_id(
                    version_db.video_character_id, user_id
                )
                version_1 = next((v for v in version_1_list if v.version_number == 1), None)
                
                if version_1 and version_1.multi_view_image_version_id:
                    multi_view_image_version_id = version_1.multi_view_image_version_id
                    logger.info(f"✅ 复用 version 1 的多视角图版本: {multi_view_image_version_id}")
            except Exception as e:
                logger.warning(f"⚠️ 获取 version 1 的多视角图版本失败（不影响主流程）: {e}")
        
        # 🔥 无论LLM成功失败都创建版本记录，确保数据完整性
        # 安全获取数据，处理image_result可能为空的情况
        t2i_prompt = custom_prompt or ""
        reference_image_urls = version_db.reference_image_urls or []
        provider = "default"  # 默认值
        success = False
        error_msg = None
        
        if image_result:
            if not custom_prompt:
                t2i_prompt = image_result.generated_prompt or ""
            reference_image_urls = image_result.reference_image_urls or version_db.reference_image_urls or []
            provider = image_result.provider or (user_option.video_generation_tool if user_option else "default")
            success = image_result.success
            error_msg = image_result.message if not image_result.success else None
            logger.info(f"✅ 从image_result获取到数据: provider={provider}, t2i_prompt={t2i_prompt[:100] if t2i_prompt else ''}..., 参考图片数量={len(reference_image_urls)}")
        else:
            # image_result为空时的处理
            provider = user_option.video_generation_tool if user_option else "default"
            error_msg = "图片生成失败：未获取到结果"
            logger.warning(f"⚠️ image_result为空，使用默认provider: {provider}")
        
        # 再生参数字段：从 image_result / user_option / version_db 取
        aspect_ratio = None
        resolution = None
        model_val = None
        seed_val = None
        image_tool_val = None
        if image_result:
            aspect_ratio = getattr(image_result, "aspect_ratio", None)
            if aspect_ratio and hasattr(aspect_ratio, "value"):
                aspect_ratio = aspect_ratio.value
            resolution = getattr(image_result, "resolution", None)
            if resolution and hasattr(resolution, "value"):
                resolution = resolution.value
            model_val = getattr(image_result, "model", None)
            if model_val and hasattr(model_val, "value"):
                model_val = model_val.value
            seed_val = getattr(image_result, "seed", None)
        if user_option:
            if aspect_ratio is None and user_option.aspect_ratio:
                aspect_ratio = user_option.aspect_ratio.value
            if resolution is None and user_option.resolution:
                resolution = user_option.resolution.value
            image_tool_val = user_option.image_generation_tool.value if user_option.image_generation_tool else None
        new_version_uuid = await create_character_version(
            version_data={
                "video_character_id": version_db.video_character_id,
                "conversation_id": version_db.conversation_id,
                "thread_id": version_db.thread_id,
                "run_id": version_db.run_id,
                "user_id": user_id,
                "version_number": version_number,
                "character_image_url": character_image_url,
                "t2i_prompt": t2i_prompt,
                "provider": provider,
                "reference_image_urls": reference_image_urls,
                "success": success,
                "error_msg": error_msg,
                "ai_messages": None,
                "multi_view_image_version_id": multi_view_image_version_id,
                "aspect_ratio": aspect_ratio,
                "resolution": resolution,
                "model": model_val,
                "seed": seed_val,
                "image_generation_tool": image_tool_val,
            }
        )
        logger.info(f"✅ 角色版本记录创建成功: {new_version_uuid}")
        
        # 创建编辑记录
        # ⚠️ 注意：此函数在 custom_prompt 和非 custom_prompt 两种情况下都会被调用
        # - custom_prompt 存在：前端请求，创建 edit record
        # - custom_prompt 为 None：系统自动生成（目前没有实际使用场景，前端总是传递 custom_prompt）
        try:
            from ...crud.error_tracking import create_character_edit_record, get_task_record_by_run_id
            from ...crud.video.video_character import get_character_by_uuid
            
            # 判断用户操作类型
            user_action = UserActionType.RE_GEN
            _ep = edit_instruction or ""
            if _ep or (custom_prompt and custom_prompt != version_db.t2i_prompt):
                user_action = UserActionType.EDIT_PROMPT
            
            # 获取任务记录
            task_record = await get_task_record_by_run_id(version_db.run_id)
            
            # 获取角色名称
            character_db = await get_character_by_uuid(version_db.video_character_id)
            character_name = character_db.name if character_db else "Unknown"
            
            await create_character_edit_record(
                task_record_id=task_record.uuid if task_record else None,
                character_id=version_db.video_character_id,
                user_id=user_id,
                conversation_id=version_db.conversation_id,
                thread_id=version_db.thread_id,
                run_id=version_db.run_id,
                character_name=character_name,
                old_version_id=version_db.uuid,
                new_version_id=new_version_uuid,
                old_version_number=version_db.version_number,
                new_version_number=version_number,
                old_image_url=version_db.character_image_url,
                new_image_url=character_image_url,
                model=provider,
                old_prompt=version_db.t2i_prompt,
                new_prompt=t2i_prompt,
                user_action=user_action,
                user_feedback=None,  # TODO: 前端传递
                edit_instruction=edit_instruction,
                success=success,
                error_msg=error_msg
            )
            logger.info(f"📝 创建 Character 编辑记录: character={character_name}, action={user_action}")
        except Exception as e:
            logger.error(f"❌ 创建 Character 编辑记录失败: {e}，但继续执行")
        
        return new_version_uuid

    async def _regenerate_character_version(
        self,
        version_db: 'VideoCharacterGenerationVersionDB',
        custom_prompt: Optional[str],
        user_id: str,
        user_option: Optional['UserOption'] = None,
        instruction: Optional[str] = None,
        regenerate_strategy: Optional[str] = None,
    ) -> Dict[str, Any]:
        """重新生成单个角色版本

        生成策略：
        1. custom_prompt（前端请求）：
           - 调用 _regenerate_character_with_custom_prompt
           - 直接使用用户提供的 prompt
        
        2. 非 custom_prompt：
           - 调用 _generate_character_image_with_llm
           - 系统自动生成 prompt（使用 LLM 分析角色描述）
           - ⚠️ 注意：此分支目前没有实际使用场景，前端请求时总是会传递 custom_prompt
        
        级联生成：
        - 角色图片重新生成成功后，会自动级联生成：
          1. 多视角图（multi-view）：调用 _regenerate_character_multi_view_internal
          2. 融合图（fusion）：调用 _regenerate_fusion_for_character（如果角色在 scenes 中）
        - 级联生成使用默认 prompt，不传递 custom_prompt
        
        Edit Record 逻辑：
        - 在 _save_character_version_to_database 中创建 Character 编辑记录
        - 判断是 RE_GEN 还是 EDIT_PROMPT（基于 custom_prompt 是否与原有 prompt 不同）
        - ⚠️ 注意：非 custom_prompt 分支也会创建 edit record，但目前没有实际使用场景
        """
        from ...crud.video.video_character import get_character_by_uuid
        from ...services.agent.video.main_character_design_service import _generate_character_image_with_llm
        from ...models.video_state import CharacterProfile
        from prompts.prompt_config import PromptName
        
        # 读取数据（使用asyncpg CRUD）
        # 获取关联的角色信息
        character_db = await get_character_by_uuid(version_db.video_character_id)
        if not character_db:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "关联的角色不存在"
            )
        
        # 构建CharacterProfile对象（从数据库角色信息）
        from ...models.video_state import VisualElementType
        character_profile = CharacterProfile(
            id=character_db.uuid,
            type=VisualElementType(character_db.type),  # 将字符串转为枚举类型
            name=character_db.name,
            description=character_db.description,
            appearance=character_db.appearance or "",
            personality=character_db.personality or "",
            role=character_db.role or "character",
            style=character_db.style or "",
            body_type=character_db.body_type or "",
            character_image_url=character_db.image_url or ""
        )
        
        # 获取参考图片
        images = []
        if version_db.reference_image_urls:
            images = [{"url": url} for url in version_db.reference_image_urls]
        
        # 从 state 获取 detected_language，回退从 DB conversation_runs 取
        detected_language = None
        if isinstance(getattr(self, 'state', None), dict):
            detected_language = getattr(self, 'state', {}).get("detected_language")
        if not detected_language:
            thread_id = None
            if isinstance(getattr(self, 'state', None), dict):
                thread_id = self.state.get("thread_id")
            if not thread_id:
                thread_id = getattr(version_db, "thread_id", None)
            if thread_id:
                from .video.regenerate.regenerate_keyframe_service import _get_detected_language_from_db
                detected_language = await _get_detected_language_from_db(thread_id)
        
        _ccp = (custom_prompt or "").strip() if custom_prompt else ""
        _cins = (instruction or "").strip() if instruction else ""
        try:
            _norm_c = parse_character_regenerate_strategy(regenerate_strategy)
        except ValueError:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                f"不支持的角色 regenerate_strategy: {regenerate_strategy}",
            )
        if _norm_c == CharacterRegenerateStrategy.PROMPT_REGENERATE and not _ccp and not _cins:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "prompt_regenerate 需要非空 custom_prompt 或 instruction",
            )
        if _norm_c == CharacterRegenerateStrategy.INSTRUCTION_MERGE_PROMPT and not _cins:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "instruction_merge_prompt 需要非空 instruction",
            )
        if _norm_c == CharacterRegenerateStrategy.INSTRUCTION_EDIT_IMAGE and not _cins:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "instruction_edit_image 需要非空 instruction",
            )
        if _norm_c == CharacterRegenerateStrategy.INSTRUCTION_EDIT_IMAGE and not (getattr(version_db, "character_image_url", None) or "").strip():
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "instruction_edit_image 需要当前角色版本已有 character_image_url",
            )

        tid_c = getattr(version_db, "thread_id", None)
        detected_language_c: Optional[str] = None
        if tid_c:
            from .video.regenerate.regenerate_keyframe_service import _get_detected_language_from_db
            detected_language_c = await _get_detected_language_from_db(tid_c)

        if _norm_c == CharacterRegenerateStrategy.INSTRUCTION_MERGE_PROMPT:
            from ...models.image_result import CharacterImageGenerationResult
            from .video.regenerate.instruction_merge_prompt import instruction_merge_to_full_prompt

            base_ch = _ccp if _ccp else (getattr(version_db, "t2i_prompt", None) or "").strip()
            merged_ch = await instruction_merge_to_full_prompt(
                base_prompt=base_ch,
                instruction=_cins,
                asset_kind="角色主图 t2i",
                detected_language=detected_language_c,
            )
            keep_img = (getattr(version_db, "character_image_url", None) or "").strip()
            ref_ch = getattr(version_db, "reference_image_urls", None) or []
            if not isinstance(ref_ch, list):
                ref_ch = []
            img_res_ch = CharacterImageGenerationResult.success_result(
                image_url=keep_img or "",
                generated_prompt=merged_ch,
                provider=getattr(version_db, "provider", None) or "",
                reference_image_urls=ref_ch,
            )
            return {"success": True, "character_result": img_res_ch, "new_version_uuid": None}

        if _norm_c == CharacterRegenerateStrategy.INSTRUCTION_EDIT_IMAGE:
            from ...crud.video.video_character import (
                create_character_version,
                get_character_versions_by_character_id,
            )
            from ...models.image_result import CharacterImageGenerationResult
            from .video.regenerate.instruction_merge_prompt import instruction_merge_to_full_prompt
            from .video.regenerate.regenerate_image_edit_i2i import run_regenerate_image_edit_i2i

            src_ch = (getattr(version_db, "character_image_url", None) or "").strip()
            img_ed = await run_regenerate_image_edit_i2i(
                instruction=_cins,
                reference_image_urls=[src_ch],
                user_option=user_option,
                shot_for_resolve=None,
                detected_language=detected_language_c,
                skip_consistency_check=False,
            )
            if not img_ed or not img_ed.success:
                err_c = (img_ed.error_msg if img_ed else None) or "角色图像编辑失败"
                fail_c = CharacterImageGenerationResult.error_result(
                    error_message=err_c,
                    provider=(img_ed.provider if img_ed and img_ed.provider else "") or "",
                )
                return {"success": False, "character_result": fail_c, "new_version_uuid": None}
            new_u_c = (img_ed.image_url or "").strip()
            base_t_c = (getattr(version_db, "t2i_prompt", None) or "").strip()
            try:
                new_t_c = await instruction_merge_to_full_prompt(
                    base_prompt=base_t_c,
                    instruction=_cins,
                    asset_kind="角色基于成图的编辑",
                    detected_language=detected_language_c,
                )
            except Exception as _e_c:
                logger.warning("character image_edit: prompt merge fallback: %s", _e_c)
                new_t_c = base_t_c
            ref_ie_c = getattr(version_db, "reference_image_urls", None) or []
            if not isinstance(ref_ie_c, list):
                ref_ie_c = []
            existing_ie_c = await get_character_versions_by_character_id(version_db.video_character_id, user_id)
            vn_ie_c = len(existing_ie_c) + 1
            new_uuid_ie_c = await create_character_version(
                version_data={
                    "video_character_id": version_db.video_character_id,
                    "conversation_id": version_db.conversation_id,
                    "thread_id": version_db.thread_id,
                    "run_id": version_db.run_id,
                    "user_id": user_id,
                    "version_number": vn_ie_c,
                    "character_image_url": new_u_c,
                    "t2i_prompt": new_t_c,
                    "provider": img_ed.provider or getattr(version_db, "provider", None) or "default",
                    "reference_image_urls": ref_ie_c,
                    "success": True,
                    "model": img_ed.model or getattr(version_db, "model", None),
                    "aspect_ratio": getattr(version_db, "aspect_ratio", None),
                    "resolution": getattr(version_db, "resolution", None),
                    "image_generation_tool": getattr(version_db, "image_generation_tool", None),
                }
            )
            ok_res_c = CharacterImageGenerationResult.success_result(
                image_url=new_u_c,
                generated_prompt=new_t_c,
                provider=img_ed.provider or "",
                reference_image_urls=ref_ie_c,
                model=img_ed.model,
            )
            return {"success": True, "character_result": ok_res_c, "new_version_uuid": new_uuid_ie_c}

        if _norm_c == CharacterRegenerateStrategy.PROMPT_REGENERATE and (_ccp or _cins):
            if _cins and not _ccp:
                base_char_prompt = (getattr(version_db, "t2i_prompt", None) or "").strip()
            else:
                base_char_prompt = _ccp
            image_result = await self._regenerate_character_with_custom_prompt(
                character_profile,
                images,
                base_char_prompt,
                user_option,
                detected_language=detected_language,
                user_regenerate_instruction=_cins if _cins else None,
            )
        else:
            # ⚠️ 注意：此分支（非 custom_prompt）目前没有实际使用场景
            # 前端请求时总是会传递 custom_prompt（要么是用户输入的，要么是当前版本的 prompt）
            # 此分支保留用于未来可能的场景（如系统自动重新生成）
            # 使用标准的LLM生成流程
            
            image_result, new_messages = await _generate_character_image_with_llm(
                character_profile, images, user_option, None,
                user_input="", detected_language=detected_language
            )
        
        # 写回数据（使用asyncpg CRUD）
        # 保存到数据库并返回结果
        new_version_uuid = await self._save_character_version_to_database(
            version_db, image_result, custom_prompt, user_option, user_id, edit_instruction=_cins if _cins else None
        )
        
        # ⭐ 角色图片重新生成成功后，自动重新生成多视角图和融合图
        if image_result and image_result.success and image_result.image_url:
            # 获取新创建的版本信息
            from ...crud.video.video_character import get_character_version_by_uuid
            new_version_db = await get_character_version_by_uuid(new_version_uuid)
            
            if new_version_db and new_version_db.character_image_url:
                # 长时间操作（LLM 调用）不占用连接
                # 级联生成受 agent_video_constants 控制

                # 1. 级联生成 multi-view（ENABLE_MULTIVIEW 时执行）
                if ENABLE_MULTIVIEW:
                    try:
                        logger.info(f"🎨 角色图片重新生成成功，开始自动重新生成 multi-view...")
                        multi_view_result = await self._regenerate_character_multi_view_internal(
                            character_db=character_db,
                            target_version=new_version_db,
                            user_id=user_id,
                            user_option=user_option,
                            custom_prompt=None  # 角色重新生成时，multi-view 使用默认 prompt
                        )
                        if multi_view_result and multi_view_result.get("success"):
                            logger.info(f"✅ 多视角图自动重新生成成功: {multi_view_result.get('multi_view_image_url')}")
                        else:
                            error_msg = multi_view_result.get("error_msg") if multi_view_result else "多视角图生成失败"
                            logger.warning(f"⚠️ 多视角图自动重新生成失败: {error_msg}")
                    except Exception as e:
                        logger.error(f"❌ 自动重新生成多视角图时发生异常: {e}", exc_info=True)
                else:
                    logger.info("⏭️ ENABLE_MULTIVIEW=False，跳过多视角图级联生成")

                # 2. 级联生成 fusion（ENABLE_FUSION 且角色在 scenes 中时执行）
                if ENABLE_FUSION:
                    try:
                        logger.info(f"🎨 开始自动重新生成 fusion 图...")
                        fusion_result = await self._regenerate_fusion_for_character(
                            character_id=character_db.uuid,
                            run_id=version_db.run_id,
                            user_id=user_id,
                            user_option=user_option,
                        )
                        if fusion_result and fusion_result.get("success"):
                            logger.info(f"✅ Fusion 图自动重新生成成功: 生成了 {fusion_result.get('generated_count', 0)} 个融合图")
                        else:
                            error_msg = fusion_result.get("error_msg") if fusion_result else "Fusion 图生成失败"
                            logger.warning(f"⚠️ Fusion 图自动重新生成失败: {error_msg}")
                    except Exception as e:
                        logger.error(f"❌ 自动重新生成 Fusion 图时发生异常: {e}", exc_info=True)
                else:
                    logger.info("⏭️ ENABLE_FUSION=False，跳过 fusion 级联生成")
            else:
                logger.warning(f"⚠️ 新版本没有主图，跳过多视角图和融合图自动生成")
        
        return {
            "success": image_result.success if image_result else False,
            "character_result": image_result,  # 直接返回ImageGenerationResult
            "new_version_uuid": new_version_uuid
        }
    
    async def _regenerate_video_version(
        self,
        version_db: 'VideoGenerationVersionDB',
        custom_prompt: Optional[str],
        user_id: str,
        user_option: Optional['UserOption'] = None,
        cascaded_from_storyboard_edit_id: Optional[str] = None,
        instruction: Optional[str] = None,
        regenerate_strategy: Optional[str] = None,
        forced_keyframe_version_uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """重新生成单个视频版本
        
        ✅ 修复：移除 db 参数，内部按需创建连接
        
        生成策略：
        1. custom_prompt（前端请求）：
           - 直接调用 execute_single_video（tool execution）
           - 直接使用用户提供的 prompt，跳过 prompt 生成和评估修正
        
        2. 非 custom_prompt（keyframe 级联生成）：
           - 调用 video 三部曲全部：generate_batch_videos
           - 包含：生成 prompt + 评估修正 + 并发调用工具
           - 系统自动生成 prompt，并进行内容审核和一致性评估
        
        Edit Record 逻辑：
        - custom_prompt 分支：创建 Shot 编辑记录，判断是 RE_GEN 还是 EDIT_PROMPT
        - 非 custom_prompt 分支：创建 Shot 编辑记录，判断是 CASCADED_FROM_KEYFRAME 还是 RE_GEN（用于级联生成场景）
        
        Args:
            cascaded_from_storyboard_edit_id: 如果是由keyframe级联生成，传入对应的storyboard编辑记录ID
            forced_keyframe_version_uuid: 级联时指定使用的 keyframe 版本 UUID（不写 keyframe.current_version_index）
        """
        from ...crud.video.video_story import get_detailed_shot_by_id
        from ...crud.video.video_keyframe import get_keyframe_by_uuid
        from ...crud.video.video_generation import get_video_generation_by_uuid
        from ...models.tool_enums import GenerationMode
        
        # 读取数据（使用asyncpg CRUD）
        # 通过 video_generation_id 获取 VideoGenerationDB，然后获取关键帧信息
        video_generation_db = await get_video_generation_by_uuid(version_db.video_generation_id) if version_db.video_generation_id else None
        if not video_generation_db:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "无法找到对应的视频生成记录"
            )
        
        # 获取关键帧对应的详细镜头信息
        keyframe_db = await get_keyframe_by_uuid(video_generation_db.keyframe_id) if video_generation_db.keyframe_id else None
        shot = await get_detailed_shot_by_id(keyframe_db.detailed_shot_id) if keyframe_db and keyframe_db.detailed_shot_id else None
        
        if not shot:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "无法找到对应的详细镜头信息"
            )
        
        # 从DB获取最新的KeyframeVersion，必须要有
        if not keyframe_db:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "无法找到对应的关键帧信息"
            )
        
        from ...crud.video.video_keyframe import get_keyframe_versions_by_keyframe_ids
        keyframe_versions_db = await get_keyframe_versions_by_keyframe_ids([keyframe_db.uuid])
        
        if not keyframe_versions_db:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                f"关键帧 {keyframe_db.uuid} 没有版本数据"
            )
        
        # 按 version_number 排序；级联用「刚生成的最新版」；否则优先 forced_keyframe_version_uuid，再用 keyframe.current_version_index
        sorted_keyframe_versions = sorted(keyframe_versions_db, key=lambda v: v.version_number)
        if cascaded_from_storyboard_edit_id:
            selected_keyframe_version_db = sorted_keyframe_versions[-1]
        else:
            forced_kv = (forced_keyframe_version_uuid or "").strip()
            selected_keyframe_version_db = None
            if forced_kv:
                selected_keyframe_version_db = next(
                    (v for v in sorted_keyframe_versions if getattr(v, "uuid", None) == forced_kv),
                    None,
                )
            if selected_keyframe_version_db is None:
                idx = getattr(keyframe_db, "current_version_index", 0)
                idx = max(0, min(idx, len(sorted_keyframe_versions) - 1))
                selected_keyframe_version_db = sorted_keyframe_versions[idx]
        
        # 构建KeyframeVersion对象
        keyframe_version = KeyframeVersion(
            shot_number=keyframe_db.shot_number,
            is_bridge=keyframe_db.is_bridge,
            reference_image_urls=keyframe_db.reference_image_urls or [],
            keyframe_url=selected_keyframe_version_db.keyframe_url,
            t2i_prompt=selected_keyframe_version_db.t2i_prompt,
            provider=selected_keyframe_version_db.provider,
            success=selected_keyframe_version_db.success,
            error_msg=selected_keyframe_version_db.error_msg,
            audio_segment_ids=selected_keyframe_version_db.audio_segment_ids,
            version_id=selected_keyframe_version_db.uuid,
            keyframe_uuid=keyframe_db.uuid,
            scene_id=keyframe_db.scene_id,
            storyboard_detail_id=keyframe_db.storyboard_detail_id,
            detailed_shot_id=keyframe_db.detailed_shot_id
        )
        
        # 长时间操作（LLM 调用）不占用连接
        # execute_single_video / generate_batch_videos 内部自行 load prompt+LLM，无需传入 llm
        _vcp = (custom_prompt or "").strip() if custom_prompt else ""
        _vins = (instruction or "").strip() if instruction else ""
        try:
            _norm_v = parse_video_regenerate_strategy(regenerate_strategy)
        except ValueError:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                f"不支持的视频 regenerate_strategy: {regenerate_strategy}",
            )
        if _norm_v == VideoRegenerateStrategy.INSTRUCTION_MERGE_PROMPT and not _vins:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "instruction_merge_prompt 需要非空 instruction",
            )

        tid_v = getattr(version_db, "thread_id", None)
        detected_language_v: Optional[str] = None
        if tid_v:
            from .video.regenerate.regenerate_keyframe_service import _get_detected_language_from_db
            detected_language_v = await _get_detected_language_from_db(tid_v)

        if _norm_v == VideoRegenerateStrategy.INSTRUCTION_MERGE_PROMPT:
            from ...models.video_state import VideoGenerationVersion as VgvModel
            from .video.regenerate.instruction_merge_prompt import instruction_merge_to_full_prompt

            base_mo = _vcp if _vcp else (getattr(version_db, "motion_prompt", None) or "").strip()
            merged_mo = await instruction_merge_to_full_prompt(
                base_prompt=base_mo,
                instruction=_vins,
                asset_kind="镜头视频 motion / i2v 文案",
                detected_language=detected_language_v,
            )
            keep_vid = (getattr(version_db, "video_url", None) or "").strip()
            from ...services.agent.utils.database_utils import _str_attr
            _dur_f = getattr(version_db, "duration", None)
            _dur_f = float(_dur_f) if _dur_f is not None else None
            _dur_i = int(_dur_f) if _dur_f is not None else 5
            _ver_uuid = str(getattr(version_db, "uuid", None) or "")
            vgv = VgvModel(
                shot_number=int(getattr(version_db, "shot_number", shot.shot_number) or 0),
                video_url=keep_vid,
                duration=_dur_i,
                i2v_prompt=merged_mo,
                provider=getattr(version_db, "provider", "") or "",
                is_bridge=bool(getattr(version_db, "is_bridge", False)),
                keyframe_url=(getattr(version_db, "keyframe_url", None) or ""),
                keyframe_version_ids=getattr(version_db, "keyframe_version_ids", None),
                success=True,
                version_id=_ver_uuid,
                video_generation_id=version_db.video_generation_id,
                generation_mode=_str_attr(version_db, "generation_mode"),
                audio_url=getattr(version_db, "audio_url", None),
            )
            return {
                "success": True,
                "video_generation_version": vgv,
                "new_version_uuid": None,
                "error_msg": None,
            }

        if _norm_v == VideoRegenerateStrategy.PROMPT_REGENERATE and (_vcp or _vins):
            from ...services.agent.video.video_generation_service import (
                execute_single_video, init_execution_context, VideoGenerationPrompt,
            )
            if _vins and not _vcp:
                _motion = getattr(version_db, "motion_prompt", None) or ""
                base_i2v = (_motion or "").strip()
            else:
                base_i2v = _vcp
            _user_regen = _vins if _vins else None
            custom_prompt_result = VideoGenerationPrompt(
                shot_number=shot.shot_number,
                i2v_prompt=base_i2v,
                user_regenerate_instruction=_user_regen,
                start_image_url=selected_keyframe_version_db.keyframe_url,
                end_image_url=None,
                needs_end_image=False,
                consistency_reason=None,
                generation_mode=getattr(shot, "generation_mode", None),
            )
            
            # 🎭 Lipsync 重生：如果 shot 是 lipsync 模式，需要构建 audio_url 映射
            _regen_audio_map = None
            if shot.generation_mode == GenerationMode.LIPSYNC.value:
                _orig_audio_url = version_db.audio_url
                if _orig_audio_url:
                    _regen_audio_map = {shot.shot_number: _orig_audio_url}
                    logger.info(f"🎭 Lipsync 重生: 复用原始 audio_url")
            
            exec_ctx = await init_execution_context(
                user_option=user_option,
                lipsync_audio_url_map=_regen_audio_map,
                skip_consistency_check=True,
            )
            video_generation_version, messages = await execute_single_video(
                keyframe_version, shot, custom_prompt_result, exec_ctx,
            )
            # 更新version_id以保持一致性
            video_generation_version.version_id = version_db.uuid
            video_generation_version.video_generation_id = version_db.video_generation_id
            
            # 写回数据（使用asyncpg CRUD）
            new_version_uuid = None
            if video_generation_version.success:
                from ...crud.video.video_generation import (
                    create_video_generation_version,
                    get_video_generation_versions_by_video_generation_ids,
                )
                
                # 获取下一个版本号
                existing_versions = await get_video_generation_versions_by_video_generation_ids([version_db.video_generation_id])
                version_number = len(existing_versions) + 1
                
                # 准备 additional_data 及再生参数字段
                additional_data = {}
                if video_generation_version.seed is not None:
                    additional_data['seed'] = video_generation_version.seed
                if video_generation_version.resolution:
                    additional_data['resolution'] = video_generation_version.resolution
                _ref_urls = getattr(video_generation_version, "reference_image_urls", None) or []
                if _ref_urls:
                    additional_data["reference_image_urls"] = list(_ref_urls)
                if getattr(video_generation_version, "preview_video_url", None):
                    additional_data["preview_video_url"] = video_generation_version.preview_video_url
                from ...services.agent.utils.database_utils import _str_attr
                video_tool_for_insert = getattr(version_db, "video_generation_tool", None)
                new_version_uuid = await create_video_generation_version(
                    video_generation_id=version_db.video_generation_id,
                    version_number=version_number,
                    shot_number=video_generation_version.shot_number,
                    video_url=video_generation_version.video_url,
                    provider=getattr(video_generation_version, "provider", version_db.provider),
                    is_bridge=video_generation_version.is_bridge,
                    success=video_generation_version.success,
                    conversation_id=version_db.conversation_id,
                    thread_id=version_db.thread_id,
                    run_id=version_db.run_id,
                    user_id=user_id,
                    error_msg=video_generation_version.error_msg,
                    keyframe_url=video_generation_version.keyframe_url,
                    keyframe_version_ids=video_generation_version.keyframe_version_ids,
                    motion_prompt=video_generation_version.i2v_prompt,
                    duration=video_generation_version.duration,
                    ai_messages=getattr(video_generation_version, "ai_messages_json", None),
                    additional_data=additional_data if additional_data else None,
                    audio_segment_ids=video_generation_version.audio_segment_ids,
                    aspect_ratio=_str_attr(video_generation_version, "aspect_ratio"),
                    resolution=_str_attr(video_generation_version, "resolution"),
                    video_generation_tool=video_tool_for_insert,
                    model=getattr(video_generation_version, "model", None),
                    generation_mode=video_generation_version.generation_mode,
                    audio_url=video_generation_version.audio_url,
                    video_tool_metrics=getattr(video_generation_version, "video_tool_metrics", None),
                    tool_duration_sec=getattr(video_generation_version, "tool_duration_sec", None),
                    tool_cost=getattr(video_generation_version, "tool_cost", None),
                )
                
                # 创建编辑记录（此分支为 custom_prompt 前端请求）
                try:
                    from ...crud.error_tracking import create_shot_edit_record, get_task_record_by_run_id
                    
                    # 判断用户操作类型
                    user_action = UserActionType.RE_GEN
                    if _vins or (_vcp and _vcp != (version_db.motion_prompt or "")):
                        user_action = UserActionType.EDIT_PROMPT
                    
                    # 获取任务记录
                    task_record = await get_task_record_by_run_id(version_db.run_id)
                    
                    await create_shot_edit_record(
                        task_record_id=task_record.uuid if task_record else None,
                        video_generation_id=version_db.video_generation_id,
                        user_id=user_id,
                        conversation_id=version_db.conversation_id,
                        thread_id=version_db.thread_id,
                        run_id=version_db.run_id,
                        shot_number=video_generation_version.shot_number,
                        old_version_id=version_db.uuid,
                        new_version_id=new_version_uuid,
                        old_version_number=version_db.version_number,
                        new_version_number=version_number,
                        old_video_url=version_db.video_url,
                        new_video_url=video_generation_version.video_url,
                        duration=video_generation_version.duration,
                        model=getattr(video_generation_version, 'provider', version_db.provider),
                        old_prompt=version_db.motion_prompt or "",
                        new_prompt=video_generation_version.i2v_prompt,
                        user_action=user_action,
                        user_feedback=None,  # TODO: 前端传递
                        edit_instruction=_vins if _vins else None,
                        success=video_generation_version.success,
                        error_msg=video_generation_version.error_msg
                    )
                    logger.info(f"📝 创建 Shot 编辑记录: shot={video_generation_version.shot_number}, action={user_action}")
                except Exception as e:
                    logger.error(f"❌ 创建 Shot 编辑记录失败: {e}，但继续执行")
                
                logger.info(f"✅ 视频重新生成成功: {new_version_uuid}")
            else:
                logger.error(f"❌ 视频重新生成失败: {video_generation_version.error_msg}")
            
            return {
                "success": video_generation_version.success,
                "video_generation_version": video_generation_version,
                "new_version_uuid": new_version_uuid,
                "error_msg": video_generation_version.error_msg
            }
        else:
            # ⚠️ 注意：此分支（非 custom_prompt）用于级联生成场景
            # 当 keyframe 重新生成后，会自动级联生成对应的 video
            # 此时使用非 custom_prompt 模式，调用 video 三部曲全部（生成 prompt + 评估修正 + 并发调用工具）
            # 没有自定义提示词，调用generate_batch_videos让系统自动生成prompt
            # 使用上方已按 current_version_index / 级联规则选中的 keyframe_version（与 execute_single_video 分支一致）
            from ...services.agent.video.video_generation_service import generate_batch_videos
            
            # 使用默认的用户输入
            user_input = shot.scene_description or "重新生成视频片段"
            
            # 🎭 Lipsync 重生：构建 audio_url 映射
            _regen_audio_map2 = None
            if shot.generation_mode == GenerationMode.LIPSYNC.value:
                _orig_audio_url2 = version_db.audio_url
                if _orig_audio_url2:
                    _regen_audio_map2 = {shot.shot_number: _orig_audio_url2}
                    logger.info(f"🎭 Lipsync 级联重生: 复用原始 audio_url")
            
            # 调用generate_batch_videos（批量处理单个视频，系统自动生成prompt）；内部自行 load prompt+LLM
            _cascade_resilience_ctx = {
                "run_id": getattr(version_db, "run_id", None),
                "thread_id": getattr(version_db, "thread_id", None),
                "conversation_id": getattr(version_db, "conversation_id", None),
                "caller": "video_regenerate_cascade",
            }
            video_messages, video_generation_versions = await generate_batch_videos(
                keyframes_batch=[keyframe_version],
                shots_batch=[shot],
                user_input=user_input,
                prev_shot=None,
                next_shot=None,
                user_option=user_option,
                story_outline=None,
                lipsync_audio_url_map=_regen_audio_map2,
                skip_consistency_check=True,
                log_context=_cascade_resilience_ctx,
            )
            
            # 获取生成结果（generate_batch_videos 一定会返回结果）
            result = video_generation_versions[0]
            # 更新version_id以保持一致性
            result.version_id = version_db.uuid
            result.video_generation_id = version_db.video_generation_id
            
            # 写回数据（使用asyncpg CRUD）
            new_version_uuid = None
            if result.success:
                from ...crud.video.video_generation import (
                    create_video_generation_version,
                    get_video_generation_versions_by_video_generation_ids,
                )
                
                # 获取下一个版本号
                existing_versions = await get_video_generation_versions_by_video_generation_ids([version_db.video_generation_id])
                version_number = len(existing_versions) + 1
                
                # 准备 additional_data 及再生参数字段
                additional_data = {}
                if result.seed is not None:
                    additional_data['seed'] = result.seed
                if result.resolution:
                    additional_data['resolution'] = result.resolution
                _ref_urls = getattr(result, "reference_image_urls", None) or []
                if _ref_urls:
                    additional_data["reference_image_urls"] = list(_ref_urls)
                if getattr(result, "preview_video_url", None):
                    additional_data["preview_video_url"] = result.preview_video_url
                from ...services.agent.utils.database_utils import _str_attr
                prov = getattr(result, 'provider', version_db.provider)
                new_version_uuid = await create_video_generation_version(
                    video_generation_id=version_db.video_generation_id,
                    version_number=version_number,
                    shot_number=result.shot_number,
                    video_url=result.video_url,
                    provider=prov,
                    is_bridge=result.is_bridge,
                    success=result.success,
                    conversation_id=version_db.conversation_id,
                    thread_id=version_db.thread_id,
                    run_id=version_db.run_id,
                    user_id=user_id,
                    error_msg=result.error_msg,
                    keyframe_url=result.keyframe_url,
                    keyframe_version_ids=result.keyframe_version_ids,
                    motion_prompt=result.i2v_prompt,
                    duration=result.duration,
                    ai_messages=getattr(result, 'ai_messages_json', None),
                    audio_segment_ids=getattr(result, 'audio_segment_ids', version_db.audio_segment_ids),
                    additional_data=additional_data if additional_data else None,
                    aspect_ratio=_str_attr(result, 'aspect_ratio'),
                    resolution=_str_attr(result, 'resolution'),
                    video_generation_tool=getattr(version_db, 'video_generation_tool', None),
                    model=getattr(result, 'model', None),
                    generation_mode=result.generation_mode,
                    audio_url=result.audio_url,
                    video_tool_metrics=getattr(result, "video_tool_metrics", None),
                    tool_duration_sec=getattr(result, "tool_duration_sec", None),
                    tool_cost=getattr(result, "tool_cost", None),
                )
                logger.info(
                    "✅ video_generation 已追加新版本（不自动切换 current_version_index，由用户 select_version 选用）"
                )
                
                # 创建编辑记录（级联生成场景，无自定义prompt）
                # ⚠️ 注意：此分支用于级联生成场景（keyframe 重新生成后自动生成 video）
                # 如果是级联生成，使用 CASCADED_FROM_KEYFRAME；否则使用 RE_GEN（理论上不会发生，因为非 custom_prompt 都是级联）
                try:
                    from ...crud.error_tracking import create_shot_edit_record, get_task_record_by_run_id
                    
                    # 判断用户操作类型
                    # 如果是级联生成，使用CASCADED_FROM_KEYFRAME；否则使用RE_GEN
                    user_action = UserActionType.CASCADED_FROM_KEYFRAME if cascaded_from_storyboard_edit_id else UserActionType.RE_GEN
                    
                    # 获取任务记录
                    task_record = await get_task_record_by_run_id(version_db.run_id)
                    
                    await create_shot_edit_record(
                        task_record_id=task_record.uuid if task_record else None,
                        video_generation_id=version_db.video_generation_id,
                        user_id=user_id,
                        conversation_id=version_db.conversation_id,
                        thread_id=version_db.thread_id,
                        run_id=version_db.run_id,
                        shot_number=result.shot_number,
                        old_version_id=version_db.uuid,
                        new_version_id=new_version_uuid,
                        old_version_number=version_db.version_number,
                        new_version_number=version_number,
                        old_video_url=version_db.video_url,
                        new_video_url=result.video_url,
                        duration=result.duration,
                        model=getattr(result, 'provider', version_db.provider),
                        old_prompt=version_db.motion_prompt or "",
                        new_prompt=result.i2v_prompt,  # 使用系统生成的新prompt
                        user_action=user_action,
                        user_feedback=None,
                        edit_instruction=None,
                        success=result.success,
                        error_msg=result.error_msg,
                        cascaded_from_storyboard_edit_id=cascaded_from_storyboard_edit_id  # 关联storyboard编辑记录
                    )
                    logger.info(f"📝 创建 Shot 编辑记录: shot={result.shot_number}, action={user_action}, cascaded_from={cascaded_from_storyboard_edit_id}")
                except Exception as e:
                    logger.error(f"❌ 创建 Shot 编辑记录失败: {e}，但继续执行")
                
                logger.info(f"✅ 视频重新生成成功: {new_version_uuid}")
            else:
                logger.error(f"❌ 视频重新生成失败: {result.error_msg}")
            
            return {
                "success": result.success,
                "video_generation_version": result,
                "new_version_uuid": new_version_uuid,
                "error_msg": result.error_msg
            }
    
    async def _get_music_data_by_run_id(self, run_id: str) -> Dict[str, Dict[str, Any]]:
        """根据run_id获取音乐数据，按audio_segment_id组织
        
        Returns:
            Dict[str, Dict]: {audio_segment_id: {'music_generation': VideoMusicGenerationDB, 'version': VideoMusicGenerationVersionDB}}
        """
        try:
            from ...crud.video.video_audio import get_music_generations_by_run_id, get_music_generation_versions_by_music_generation_ids
            
            # 获取所有音乐生成（crud/video 使用 asyncpg，无需 db）
            music_generations = await get_music_generations_by_run_id(run_id)
            if not music_generations:
                return {}
            
            # 获取音乐版本
            music_generation_ids = [mg.uuid for mg in music_generations]
            music_versions = await get_music_generation_versions_by_music_generation_ids(music_generation_ids)
            
            # 🔧 按 audio_segment_id 组织数据（而不是 shot_number）
            music_by_audio_segment = {}
            for music_generation in music_generations:
                # 找到该音乐的最新版本
                versions = [v for v in music_versions if v.music_generation_id == music_generation.uuid]
                if versions:
                    # 使用最新版本（版本号最大的）
                    latest_version = max(versions, key=lambda x: x.version_number)
                    
                    # 从 version 中获取 audio_segment_ids（防御：DB 可能返回 JSON 字符串）
                    raw_ids = latest_version.audio_segment_ids or []
                    if isinstance(raw_ids, str):
                        try:
                            audio_segment_ids = json.loads(raw_ids)
                        except Exception:
                            audio_segment_ids = [raw_ids]
                    else:
                        audio_segment_ids = list(raw_ids) if raw_ids else []
                    if not audio_segment_ids:
                        logger.warning(f"⚠️ 音乐版本 {latest_version.uuid} 没有 audio_segment_ids，suno生成音乐，所以使用音乐版本uuid作为audio_segment_id")
                        audio_segment_ids = [latest_version.uuid]
                    
                    # 一个音乐版本可能对应多个 audio_segment_ids（虽然通常只有一个）
                    for audio_segment_id in audio_segment_ids:
                        music_by_audio_segment[audio_segment_id] = {
                            'music_generation': music_generation,
                            'version': latest_version
                        }
            
            logger.info(f"🎵 根据run_id获取到音乐数据: {len(music_by_audio_segment)} 个音频片段")
            return music_by_audio_segment
            
        except Exception as e:
            logger.error(f"❌ 根据run_id获取音乐数据失败: {e}")
            return {}
    
    async def sync_segments_by_request(
        self,
        thread_id: str,
        video_versions: List['SyncSegmentsVideoVersionRequest'],
        user_id: str,
        user_option: Optional['UserOption'] = None,
        force: bool = False
    ) -> Dict[str, Any]:
        """
        同步视频片段 - 按 thread 根据视频生成版本更新或创建 video segments。
        
        Args:
            thread_id: 线程ID（必填）
            force: 是否强制重新合成，默认False。如果为True，即使版本未变化也会重新合成
        
        性能优化：
        - 批量查询所有video_generation和version，避免循环查询
        - 使用映射表(UUID -> Object)加速查找
        - 一次性查询所有existing segments和versions
        
        核心逻辑：
        1. 根据 thread_id 取 story_outline 及 conversation_id/thread_id
        2. 根据 video_generation_uuid 和 video_generation_version_uuid 获取视频信息
        3. 根据 audio_segment_id 查找或创建 video_segment
        4. 如果找到对应segment，增加新的version（version_number + 1）
        5. 如果找不到，创建新的segment和version
        6. 更新 segment 的 current_version_index 指向本次同步产生的新版本（不写各 shot 的 video_generation.current_version_index）
        """
        from ...crud.video.video_story import get_video_story_outline_by_thread_id
        from ...crud.video.video_segment import (
            create_video_segment,
            create_video_segment_version,
            get_video_segment_versions,
            get_video_segments_with_data_by_conversation_and_thread
        )
        from ...crud.video.video_generation import (
            get_video_generations_by_uuids,
            get_video_generation_versions_by_uuids
        )
        
        logger.info(
            f"🔄 同步视频片段（重新更新视频）: thread_id={thread_id}, versions_count={len(video_versions)}, force={force} "
            f"[时长] 后续日志可 grep '[时长]' 排查音画对不齐"
        )
        for idx, v in enumerate(video_versions):
            logger.info(f"   [更新视频] 请求顺序 idx={idx}: video_generation_uuid={v.video_generation_uuid[:8]}..., version_uuid={v.video_generation_version_uuid[:8]}...")
        try:
            # 1. 按 thread 获取 story_outline
            story_outline = await get_video_story_outline_by_thread_id(thread_id)
            if not story_outline:
                raise Exception(f"找不到对应的 story_outline: thread_id={thread_id}")
            
            story_outline_id = story_outline.uuid
            conversation_id = story_outline.conversation_id
            thread_id = story_outline.thread_id
            run_id_for_create = getattr(story_outline, "run_id", None) or ""
            
            # 2. 批量查询所有video_generation和version（使用已有的批量查询函数）
            video_gen_uuids = [v.video_generation_uuid for v in video_versions]
            video_gen_version_uuids = [v.video_generation_version_uuid for v in video_versions]
            
            # 批量查询 video_generations
            video_gens_list = await get_video_generations_by_uuids(video_gen_uuids)
            video_gens_map = {vg.uuid: vg for vg in video_gens_list}
            logger.info(f"📦 批量查询 video_generations: {len(video_gens_map)} 条记录")
            
            # 批量查询 video_generation_versions（用户选中的版本）
            video_gen_versions_list = await get_video_generation_versions_by_uuids(video_gen_version_uuids)
            video_gen_versions_map = {vgv.uuid: vgv for vgv in video_gen_versions_list}
            logger.info(f"📦 批量查询 video_generation_versions: {len(video_gen_versions_map)} 条记录")
            
            # 🔧 批量查询所有 video_generation 的所有历史版本（用于计算 current_version_index）
            from ...crud.video.video_generation import get_video_generation_versions_by_video_generation_ids
            all_versions_list = await get_video_generation_versions_by_video_generation_ids(video_gen_uuids)
            
            # 构建映射：video_generation_id -> [所有版本]
            video_gen_id_to_all_versions = {}
            for ver in all_versions_list:
                if ver.video_generation_id not in video_gen_id_to_all_versions:
                    video_gen_id_to_all_versions[ver.video_generation_id] = []
                video_gen_id_to_all_versions[ver.video_generation_id].append(ver)
            
            # 对每个 video_generation 的版本按 version_number 排序
            for gen_id in video_gen_id_to_all_versions:
                video_gen_id_to_all_versions[gen_id].sort(key=lambda v: v.version_number)
            
            logger.info(f"📦 批量查询所有历史版本: {len(all_versions_list)} 条记录，涉及 {len(video_gen_id_to_all_versions)} 个 video_generation")
            
            # 3. 批量查询 existing segments 和 versions（按 thread）
            existing_segments = await get_video_segments_with_data_by_conversation_and_thread(conversation_id, thread_id)
            existing_segments = [seg for seg in existing_segments if seg.user_id == user_id]
            logger.info(f"📦 批量查询 existing segments: {len(existing_segments)} 条记录")
            
            # 批量查询所有 segment 的 versions（避免 N+1 问题）
            segment_versions_map = {}
            if existing_segments:
                from ...crud.video.video_segment import get_video_segment_versions_by_segment_ids
                segment_ids = [seg.uuid for seg in existing_segments]
                segment_versions_map = await get_video_segment_versions_by_segment_ids(segment_ids)
                logger.info(f"📦 批量查询 segment versions: {len(segment_versions_map)} 个segment的版本")
            
            # 4. 批量查询 music_data（按 thread）
            from .video.video_assembly_service import get_music_data_by_conversation_and_thread
            music_data = await get_music_data_by_conversation_and_thread(conversation_id, thread_id)
            logger.info(f"📦 批量查询 music_data: {len(music_data)} 个音频片段")
            
            # 5. 判断是 audio-driven 还是 video-driven 模式
            # 通过检查是否有 audio_segment_ids 来判断
            is_audio_driven = any(
                vgv.audio_segment_ids and len(vgv.audio_segment_ids) > 0
                for vgv in video_gen_versions_list
            )
            logger.info(f"📋 Sync 模式: {'Audio-driven' if is_audio_driven else 'Video-driven'}")
            
            # 6. 按 audio_segment_id 组织数据（使用映射表加速）；不写 video_generation.current_version_index（选用由用户 select_version）
            segments_by_audio = {}
            
            for video_version in video_versions:
                video_gen_uuid = video_version.video_generation_uuid
                video_gen_version_uuid = video_version.video_generation_version_uuid
                
                # 从映射表获取 video_generation
                video_gen = video_gens_map.get(video_gen_uuid)
                if not video_gen:
                    logger.warning(f"⚠️ 找不到 video_generation: {video_gen_uuid}")
                    continue
                
                # 从映射表获取 video_generation_version
                video_gen_version = video_gen_versions_map.get(video_gen_version_uuid)
                if not video_gen_version:
                    logger.warning(f"⚠️ 找不到 video_generation_version: {video_gen_version_uuid}")
                    continue
                
                # 获取 audio_segment_ids
                audio_segment_ids = video_gen_version.audio_segment_ids or []
                if not audio_segment_ids:
                    logger.warning(f"⚠️ video_generation_version {video_gen_version_uuid} 没有 audio_segment_ids")
                    continue
                
                # 通常一个视频只对应一个 audio_segment_id
                audio_segment_id = audio_segment_ids[0]
                
                if audio_segment_id not in segments_by_audio:
                    # 从 music_data 获取对应的 music_generation_id
                    music_generation_id = None
                    music_info = music_data.get(audio_segment_id)
                    if music_info and music_info.get('music_generation'):
                        music_generation_id = music_info['music_generation'].uuid
                    
                    segments_by_audio[audio_segment_id] = {
                        'video_generations': [],
                        'video_gen_versions': [],
                        'music_generation_id': music_generation_id,
                        'shot_numbers': []
                    }
                
                segments_by_audio[audio_segment_id]['video_generations'].append(video_gen)
                segments_by_audio[audio_segment_id]['video_gen_versions'].append(video_gen_version)
                segments_by_audio[audio_segment_id]['shot_numbers'].append(video_gen.shot_number)
            
            logger.info("ℹ️ sync_segments：不更新各 shot 的 current_version_index（与合并页「仅 sync 素材」一致）")
            
            logger.info(f"📊 按audio_segment_id分组: {len(segments_by_audio)} 个音频片段")
            for aid, data in segments_by_audio.items():
                shots = data.get('shot_numbers', [])
                vers = data.get('video_gen_versions', [])
                urls = [getattr(v, 'video_url', None) for v in vers] if vers else []
                url_previews = [(u[:50] + '...') if u and len(u) > 50 else (u or '') for u in urls]
                logger.info(f"   [更新视频] segments_by_audio audio_segment_id={aid[:8]}... -> shot_numbers={shots}, video_count={len(vers)}, video_urls={url_previews}")
            
            # 🔧 检测「应有段数 vs 实际有视频的段数」不一致（例如 58 音频 vs 55 个有 shot 的）
            sync_warning: Optional[Dict[str, Any]] = None
            if is_audio_driven and music_data:
                expected_count = len(music_data)
                actual_count = len(segments_by_audio)
                missing_audio_segment_ids = set(music_data.keys()) - set(segments_by_audio.keys())
                if missing_audio_segment_ids:
                    missing_list = sorted(missing_audio_segment_ids)[:25]
                    logger.warning(
                        "📊 【Sync 段数不一致】音频共 %s 段，当前仅 %s 段有对应视频，缺 %s 段。"
                        "缺失的 audio_segment_id（前25个）: %s%s",
                        expected_count,
                        actual_count,
                        len(missing_audio_segment_ids),
                        ", ".join(missing_list),
                        f" ... 共{len(missing_audio_segment_ids)}个" if len(missing_audio_segment_ids) > 25 else "",
                    )
                    sync_warning = {
                        "expected_segments_count": expected_count,
                        "actual_segments_count": actual_count,
                        "missing_count": len(missing_audio_segment_ids),
                        "missing_audio_segment_ids": list(missing_audio_segment_ids)[:50],
                    }
            
            # 7. 根据模式处理 segments
            synced_segments = []
            
            if is_audio_driven:
                # 全部按 shot 顺序：转录 segment 顺序（ORDER BY segment_id）= 时间线/shot 顺序
                from ...crud.video.video_audio import (
                    get_video_audio_transcription_by_thread_id,
                    get_video_audio_segments_by_transcription_uuid,
                )
                ordered_audio_segment_ids = []
                transcription_db = await get_video_audio_transcription_by_thread_id(thread_id)
                if transcription_db:
                    segments_db = await get_video_audio_segments_by_transcription_uuid(transcription_db.uuid)
                    if segments_db:
                        ordered_audio_segment_ids = [seg.uuid for seg in segments_db]
                        logger.info(
                            f"📊 Audio-driven 按 shot 顺序: 共 {len(ordered_audio_segment_ids)} 段"
                        )
                        for idx, aid in enumerate(ordered_audio_segment_ids):
                            logger.info(f"   [更新视频] ordered_audio_segment_ids 位置 idx={idx} -> segment_number={idx + 1}, audio_segment_id={aid[:8]}...")
                existing_by_segment_number = {s.segment_number: s for s in existing_segments}
                for sn, seg in existing_by_segment_number.items():
                    logger.info(f"   [更新视频] existing_by_segment_number segment_number={sn} -> segment_uuid={seg.uuid[:8]}...")

                # 兜底：分镜/大纲携带的 audio_segment_id 与当前音频转录段 id 完全不一致时
                # （常见于音乐 smart-clip 重裁/重转录后，大纲/分镜与转录各持一套 uuid），
                # segments_by_audio(按分镜 id) 与 ordered_audio_segment_ids(按转录 id) 永远匹配不上，
                # 会把每段都判为「缺失」而拼成黑屏。此时若两侧段数一致，按「镜头时间顺序」做位置对齐重映射，
                # 让真实镜头视频落回对应时间槽。仅在「完全错位且段数相同」时启用，避免误伤部分正确的数据。
                if ordered_audio_segment_ids and segments_by_audio:
                    _keys = set(segments_by_audio.keys())
                    _ordered = list(ordered_audio_segment_ids)
                    if _keys.isdisjoint(set(_ordered)) and len(segments_by_audio) == len(_ordered):
                        _groups_sorted = sorted(
                            segments_by_audio.values(),
                            key=lambda d: min((d.get("shot_numbers") or [10 ** 9])),
                        )
                        segments_by_audio = {
                            _ordered[i]: _grp for i, _grp in enumerate(_groups_sorted)
                        }
                        logger.warning(
                            "🩹 audio_segment_id 与音频转录段完全错位（大纲/分镜 vs 转录各一套 uuid），"
                            "按镜头时间顺序位置对齐重映射 %d 段，避免拼成黑屏。",
                            len(segments_by_audio),
                        )

                audio_segment_map = {seg.uuid: seg for seg in segments_db} if segments_db else {}
                
                synced_segments = await self._sync_segments_audio_driven(
                    ordered_audio_segment_ids=ordered_audio_segment_ids,
                    segments_by_audio=segments_by_audio,
                    music_data=music_data,
                    existing_segments=existing_segments,
                    existing_by_segment_number=existing_by_segment_number,
                    segment_versions_map=segment_versions_map,
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                    run_id=run_id_for_create,
                    user_id=user_id,
                    story_outline_id=story_outline_id,
                    force=force,
                    audio_segment_map=audio_segment_map,
                )
            else:
                # Video-driven 模式：一个 shot 对应一个 segment，不需要合并
                logger.info(f"📊 Video-driven 模式：处理 {len(video_versions)} 个 shots")
                
                synced_segments = await self._sync_segments_video_driven(
                    video_versions,
                    video_gens_map,
                    video_gen_versions_map,
                    video_gen_id_to_all_versions,
                    music_data,
                    existing_segments,
                    segment_versions_map,
                    conversation_id,
                    thread_id,
                    run_id_for_create,
                    user_id,
                    story_outline_id,
                    force=force
                )
            
            logger.info(f"🎉 同步完成: {len(synced_segments)} 个segments")
            
            result = {
                'success': True,
                'synced_segments': synced_segments,
                'total_count': len(synced_segments)
            }
            if sync_warning is not None:
                result['sync_warning'] = sync_warning
            return result
            
        except Exception as e:
            logger.error(f"❌ 同步视频片段失败: {e}", exc_info=True)
            raise
    
    async def _sync_segments_audio_driven(
        self,
        ordered_audio_segment_ids: List[str],
        segments_by_audio: Dict[str, Dict],
        music_data: Dict,
        existing_segments: List,
        existing_by_segment_number: Dict[int, Any],
        segment_versions_map: Dict,
        conversation_id: str,
        thread_id: str,
        run_id: str,
        user_id: str,
        story_outline_id: str,
        force: bool = False,
        audio_segment_map: Dict[str, Any] = None,
    ) -> List[Dict]:
        """Audio-driven 模式：按「位置」同步。第 i 个位置对应 segment_number=i，有视频则覆盖/加 version，无视频则补齐空 segment。
        """
        import asyncio
        from ...crud.video.video_segment import create_video_segment, create_video_segment_version, batch_update_video_segment_current_version_index
        
        logger.info(f"   [时长] _sync_segments_audio_driven 开始: {len(ordered_audio_segment_ids)} 个位置, force={force}, 并发=4")
        
        semaphore = asyncio.Semaphore(4)
        
        async def _process_one(i: int, audio_segment_id: str) -> Dict:
            """处理单个 segment 位置，返回 {"synced": dict, "index_update": dict|None, "new_segment_entry": (int, obj)|None}"""
            async with semaphore:
                segment_number = i + 1
                target_segment = existing_by_segment_number.get(segment_number)
                segment_data = segments_by_audio.get(audio_segment_id)
                logger.info(
                    f"   [更新视频] _sync_segments_audio_driven 位置 i={i} segment_number={segment_number} audio_segment_id={audio_segment_id[:8]}... "
                    f"has_segment_data={segment_data is not None} target_segment={target_segment.uuid[:8] + '...' if target_segment else None} target_segment_number={getattr(target_segment, 'segment_number', None)}"
                )
                
                # 该位置没有对应视频（缺失段）：补齐 segment 并确保有一条占位 version（duration 来自 music，assembly 会用黑屏）
                if not segment_data:
                    music_info = music_data.get(audio_segment_id)
                    music_generation_id = None
                    audio_duration = 0.0
                    if music_info and isinstance(music_info, dict):
                        if music_info.get('music_generation'):
                            music_generation_id = music_info['music_generation'].uuid
                        if music_info.get('version'):
                            raw_d = getattr(music_info['version'], 'duration', None)
                            audio_duration = float(raw_d) if raw_d is not None else 0.0
                            logger.info(
                                f"   [时长] 缺失段 segment_number={segment_number} 占位用音频时长 原始={raw_d!r} 类型={type(raw_d).__name__ if raw_d is not None else 'None'} -> {audio_duration}"
                            )
                    # 兜底：用 audio_segment 的 (end-start) 修正 probe 膨胀的旧数据
                    if audio_segment_map and audio_segment_id in audio_segment_map:
                        a_seg = audio_segment_map[audio_segment_id]
                        corrected = float(a_seg.end) - float(a_seg.start)
                        if corrected > 0 and abs(audio_duration - corrected) > 0.001:
                            logger.info(
                                f"   [时长兜底] segment_number={segment_number} duration {audio_duration:.3f}s -> {corrected:.3f}s (end-start)"
                            )
                            audio_duration = corrected
                    if target_segment:
                        # 已有 segment：无 version 或当前最新 version 不是占位（含错误绑定的视频）时，补/覆盖为占位 version
                        existing_versions = segment_versions_map.get(target_segment.uuid, [])
                        latest_is_placeholder = False
                        if existing_versions:
                            sorted_versions = sorted(existing_versions, key=lambda v: v.version_number)
                            latest = sorted_versions[-1]
                            latest_is_placeholder = not (latest.success and latest.video_url)
                        need_placeholder = (not existing_versions) or latest_is_placeholder
                        if need_placeholder:
                            from ...models.video_state import VideoSegmentResult, VideoSegmentStatus
                            placeholder_result = VideoSegmentResult(
                                segment_number=segment_number,
                                success=False,
                                status=VideoSegmentStatus.FAILED,
                                error="无对应视频，占位",
                                failed_video_count=0,
                                total_video_count=0,
                                video_generation_ids=[],
                                music_generation_id=music_generation_id,
                                narration_ids=[],
                                keyframe_ids=[],
                                scene_ids=[],
                                storyboard_detail_ids=[],
                                original_video_urls=[],
                                merged_video_url=None,
                                audio_url=None,
                                duration=audio_duration,
                                shot_numbers=[],
                            )
                            await create_video_segment_version(
                                video_segment_id=target_segment.uuid,
                                segment_result=placeholder_result,
                                conversation_id=conversation_id,
                                thread_id=thread_id,
                                run_id=run_id,
                                user_id=user_id,
                            )
                            logger.info(
                                f"🆕 缺失 segment {segment_number} 补建/覆盖占位 version，时长: {audio_duration:.2f}s"
                                + (" (覆盖错误绑定视频)" if existing_versions and not latest_is_placeholder else "")
                            )
                            return {
                                "synced": {
                                    'segment_uuid': target_segment.uuid,
                                    'segment_number': segment_number,
                                    'action': 'placeholder_version_created',
                                    'reason': 'missing_video_position_exists',
                                },
                                "index_update": {"uuid": target_segment.uuid, "current_version_index": len(existing_versions)},
                                "new_segment_entry": None,
                            }
                        return {
                            "synced": {
                                'segment_uuid': target_segment.uuid,
                                'segment_number': segment_number,
                                'action': 'skipped',
                                'reason': 'missing_video_position_exists',
                            },
                            "index_update": None,
                            "new_segment_entry": None,
                        }
                    # 无已有 segment，新建 segment + 占位 version
                    new_segment = await create_video_segment(
                        conversation_id=conversation_id,
                        thread_id=thread_id,
                        run_id=run_id,
                        user_id=user_id,
                        story_outline_id=story_outline_id,
                        music_generation_id=music_generation_id,
                        video_generation_ids=[],
                        narration_ids=[],
                        keyframe_ids=[],
                        scene_ids=[],
                        storyboard_detail_ids=[],
                        segment_number=segment_number
                    )
                    # 为占位 segment 创建一条 version（字段与 create_video_segment_version 插入逻辑保持一致）
                    from ...models.video_state import VideoSegmentResult, VideoSegmentStatus
                    placeholder_result = VideoSegmentResult(
                        segment_number=segment_number,
                        success=False,
                        status=VideoSegmentStatus.FAILED,
                        error="无对应视频，占位",
                        failed_video_count=0,
                        total_video_count=0,
                        video_generation_ids=[],
                        music_generation_id=music_generation_id,
                        narration_ids=[],
                        keyframe_ids=[],
                        scene_ids=[],
                        storyboard_detail_ids=[],
                        original_video_urls=[],
                        merged_video_url=None,
                        audio_url=None,
                        duration=audio_duration,
                        shot_numbers=[],
                    )
                    await create_video_segment_version(
                        video_segment_id=new_segment.uuid,
                        segment_result=placeholder_result,
                        conversation_id=conversation_id,
                        thread_id=thread_id,
                        run_id=run_id,
                        user_id=user_id,
                    )
                    logger.info(f"🆕 补齐缺失 segment {segment_number} (无视频)，已创建占位 version，时长: {audio_duration:.2f}s")
                    return {
                        "synced": {
                            'segment_uuid': new_segment.uuid,
                            'segment_number': segment_number,
                            'action': 'created',
                            'reason': 'missing_video_placeholder_with_version',
                        },
                        "index_update": {"uuid": new_segment.uuid, "current_version_index": 0},
                        "new_segment_entry": (segment_number, new_segment),
                    }
                
                video_gen_ids = [vg.uuid for vg in segment_data['video_generations']]
                video_gen_versions = segment_data['video_gen_versions']
                music_generation_id = segment_data['music_generation_id']
                shot_numbers = segment_data['shot_numbers']
                
                audio_duration = 0.0
                music_info = music_data.get(audio_segment_id)
                if music_info and music_info.get('version'):
                    raw_duration = music_info['version'].duration
                    audio_duration = float(raw_duration) if raw_duration is not None else 0.0
                    logger.info(
                        f"   [时长] segment_number={segment_number} 音频时长 原始值={raw_duration!r} 类型={type(raw_duration).__name__} "
                        f"-> audio_duration={audio_duration} (float)"
                    )
                    logger.info(f"   🎵 位置 {segment_number} 音频 {audio_segment_id[:8]}... 时长: {audio_duration:.2f}s")
                else:
                    logger.warning(f"   ⚠️ 未找到音频片段 {audio_segment_id[:8]}... 的时长信息")
                # 兜底：用 audio_segment 的 (end-start) 修正 probe 膨胀的旧数据
                if audio_segment_map and audio_segment_id in audio_segment_map:
                    a_seg = audio_segment_map[audio_segment_id]
                    corrected = float(a_seg.end) - float(a_seg.start)
                    if corrected > 0 and abs(audio_duration - corrected) > 0.001:
                        logger.info(
                            f"   [时长兜底] segment_number={segment_number} duration {audio_duration:.3f}s -> {corrected:.3f}s (end-start)"
                        )
                        audio_duration = corrected
                
                if target_segment:
                    # 8a. 已存在segment，检查是否需要创建新version
                    logger.info(f"✅ 找到已存在的segment: {target_segment.uuid}, segment_number={target_segment.segment_number}")
                    
                    # 从映射表获取现有versions（按 version_number 排序，取最大即最新）
                    existing_versions = segment_versions_map.get(target_segment.uuid, [])
                    if existing_versions:
                        existing_versions = sorted(existing_versions, key=lambda v: v.version_number)
                    
                    # 🔧 检查video_generation_version_ids是否有变化以及上次合成状态
                    current_video_gen_version_ids = [v.uuid for v in video_gen_versions]
                    
                    should_skip = False
                    if existing_versions:
                        latest_version = existing_versions[-1]  # 最新版本（version_number 最大）
                        previous_video_gen_version_ids = latest_version.video_generation_version_ids or []
                        
                        # 校验已有 version 的绑定是否属于当前 audio_segment_id（防止历史 bug 导致错误绑定）
                        previous_binding_valid = True
                        if previous_video_gen_version_ids:
                            from ...crud.video.video_generation import get_video_generation_versions_by_uuids
                            prev_gen_versions = await get_video_generation_versions_by_uuids(previous_video_gen_version_ids)
                            for pv in prev_gen_versions:
                                ids = pv.audio_segment_ids or []
                                if audio_segment_id not in ids:
                                    previous_binding_valid = False
                                    logger.info(
                                        f"⚠️ Segment {target_segment.segment_number}: 已有 version 绑定不属于本位置 "
                                        f"(audio_segment_id {audio_segment_id[:8]}... 不在 {[x[:8] for x in ids]}...)，需覆盖"
                                    )
                                    break
                        
                        # 检查版本是否相同
                        versions_unchanged = set(current_video_gen_version_ids) == set(previous_video_gen_version_ids)
                        
                        # 检查上次合成是否成功（从latest_version的success字段或video_url判断）
                        last_merge_successful = latest_version.success and latest_version.video_url is not None
                        
                        if previous_binding_valid and versions_unchanged and last_merge_successful and not force:
                            logger.info(f"🔄 Segment {target_segment.segment_number}: 版本未变化且上次合成成功，跳过sync")
                            logger.info(f"   - version_ids: {sorted(current_video_gen_version_ids)}")
                            logger.info(f"   - last_success: {last_merge_successful}")
                            should_skip = True
                        elif versions_unchanged and last_merge_successful and force:
                            logger.info(f"🔄 Segment {target_segment.segment_number}: 版本未变化但force=True，强制重新合成")
                            logger.info(f"   - version_ids: {sorted(current_video_gen_version_ids)}")
                            logger.info(f"   - last_success: {last_merge_successful}")
                        elif versions_unchanged and not last_merge_successful:
                            logger.info(f"🔄 Segment {target_segment.segment_number}: 版本未变化但上次合成失败，重新尝试合成")
                            logger.info(f"   - version_ids: {sorted(current_video_gen_version_ids)}")
                            logger.info(f"   - last_success: {last_merge_successful}")
                        else:
                            logger.info(f"🆕 Segment {target_segment.segment_number}: video_generation_version_ids 有变化，需要创建新version")
                            logger.info(f"   - 之前: {sorted(previous_video_gen_version_ids)}")
                            logger.info(f"   - 现在: {sorted(current_video_gen_version_ids)}")
                    
                    if should_skip:
                        return {
                            "synced": {
                                'segment_uuid': target_segment.uuid,
                                'segment_number': target_segment.segment_number,
                                'action': 'skipped',
                                'reason': 'version_unchanged_and_successful',
                            },
                            "index_update": None,
                            "new_segment_entry": None,
                        }
                    
                    new_version_number = len(existing_versions)
                    
                    # 🔧 统一入口：S3 迁移 + 合并 + 状态计算（与新建 segment 共用 _sync_prepare_merge_segment）
                    merge_result, video_urls = await _sync_prepare_merge_segment(
                        segment_number=target_segment.segment_number,
                        video_gen_versions=video_gen_versions,
                        segment_data=segment_data,
                        audio_duration=audio_duration,
                    )
                    
                    from ...models.video_state import VideoSegmentResult, VideoSegmentStatus
                    segment_duration_float = await _sync_segment_duration_for_db(
                        target_segment.segment_number, audio_duration, merge_result,
                    )
                    segment_result = VideoSegmentResult(
                        segment_number=target_segment.segment_number,
                        success=merge_result.success,
                        status=merge_result.status,
                        error=merge_result.error_msg,
                        failed_video_count=merge_result.failed_video_count,
                        total_video_count=merge_result.total_video_count,
                        video_generation_ids=video_gen_ids,
                        music_generation_id=music_generation_id,
                        narration_ids=[],
                        keyframe_ids=[],
                        scene_ids=[],
                        storyboard_detail_ids=[],
                        original_video_urls=video_urls,
                        merged_video_url=merge_result.merged_video_url,
                        audio_url=None,
                        duration=segment_duration_float,
                        shot_numbers=shot_numbers,
                    )
                    logger.info(
                        f"   [时长] 写入 segment version segment_number={target_segment.segment_number} duration={segment_duration_float!r} 类型={type(segment_duration_float).__name__}"
                    )
                    await create_video_segment_version(
                        video_segment_id=target_segment.uuid,
                        segment_result=segment_result,
                        conversation_id=conversation_id,
                        thread_id=thread_id,
                        run_id=run_id,
                        user_id=user_id
                    )
                    
                    logger.info(f"✅ 更新segment {target_segment.uuid}, segment_number={target_segment.segment_number}, 新version: {new_version_number}")
                    logger.info(
                        f"   [更新视频] 已写入 segment_number={target_segment.segment_number} segment_uuid={target_segment.uuid[:8]}... "
                        f"merged_video_url={'有' if merge_result.merged_video_url else '无'} success={merge_result.success}"
                    )
                    return {
                        "synced": {
                            'segment_uuid': target_segment.uuid,
                            'segment_number': target_segment.segment_number,
                            'action': 'updated',
                            'new_version_number': new_version_number,
                        },
                        "index_update": {"uuid": target_segment.uuid, "current_version_index": new_version_number},
                        "new_segment_entry": None,
                    }
                
                else:
                    # 该位置没有已有 segment，创建新 segment（按位置 segment_number）
                    logger.info(f"🆕 创建新 segment {segment_number} for audio_segment_id: {audio_segment_id[:8]}...")
                    
                    new_segment = await create_video_segment(
                        conversation_id=conversation_id,
                        thread_id=thread_id,
                        run_id=run_id,
                        user_id=user_id,
                        story_outline_id=story_outline_id,
                        music_generation_id=music_generation_id,
                        video_generation_ids=video_gen_ids,
                        narration_ids=[],
                        keyframe_ids=[],
                        scene_ids=[],
                        storyboard_detail_ids=[],
                        segment_number=segment_number
                    )
                    
                    # 🔧 统一入口：S3 迁移 + 合并 + 状态计算（与更新 segment 共用 _sync_prepare_merge_segment）
                    merge_result, video_urls = await _sync_prepare_merge_segment(
                        segment_number=segment_number,
                        video_gen_versions=video_gen_versions,
                        segment_data=segment_data,
                        audio_duration=audio_duration,
                    )
                    
                    from ...models.video_state import VideoSegmentResult, VideoSegmentStatus
                    segment_duration_float = await _sync_segment_duration_for_db(
                        segment_number, audio_duration, merge_result,
                    )
                    segment_result = VideoSegmentResult(
                        segment_number=segment_number,
                        success=merge_result.success,
                        status=merge_result.status,
                        error=merge_result.error_msg,
                        failed_video_count=merge_result.failed_video_count,
                        total_video_count=merge_result.total_video_count,
                        video_generation_ids=video_gen_ids,
                        music_generation_id=music_generation_id,
                        narration_ids=[],
                        keyframe_ids=[],
                        scene_ids=[],
                        storyboard_detail_ids=[],
                        original_video_urls=video_urls,
                        merged_video_url=merge_result.merged_video_url,
                        audio_url=None,
                        duration=segment_duration_float,
                        shot_numbers=shot_numbers,
                    )
                    logger.info(
                        f"   [时长] 写入 segment version 新Segment {segment_number} duration={segment_duration_float!r} 类型={type(segment_duration_float).__name__}"
                    )
                    await create_video_segment_version(
                        video_segment_id=new_segment.uuid,
                        segment_result=segment_result,
                        conversation_id=conversation_id,
                        thread_id=thread_id,
                        run_id=run_id,
                        user_id=user_id
                    )
                    
                    logger.info(
                        f"   [更新视频] 新建并写入 segment_number={segment_number} segment_uuid={new_segment.uuid[:8]}... "
                        f"merged_video_url={'有' if merge_result.merged_video_url else '无'} success={merge_result.success}"
                    )
                    logger.info(f"✅ 创建新segment {new_segment.uuid}, segment_number: {segment_number}")
                    return {
                        "synced": {
                            'segment_uuid': new_segment.uuid,
                            'segment_number': segment_number,
                            'action': 'created',
                            'new_version_number': 0,
                        },
                        "index_update": {"uuid": new_segment.uuid, "current_version_index": 0},
                        "new_segment_entry": (segment_number, new_segment),
                    }
        
        # 并发执行所有 segment，最多 6 个同时跑
        tasks = [_process_one(i, audio_segment_id) for i, audio_segment_id in enumerate(ordered_audio_segment_ids)]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 协作式取消：若因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
        await raise_if_cancelled()
        
        # 按原始顺序收集结果（保证 synced_segments 顺序与 ordered_audio_segment_ids 一致）
        synced_segments = []
        segment_index_updates = []
        for result in raw_results:
            if isinstance(result, BaseException):
                logger.error(f"❌ segment 并发处理异常: {result}", exc_info=result)
                continue
            if result["synced"]:
                synced_segments.append(result["synced"])
            if result["index_update"]:
                segment_index_updates.append(result["index_update"])
            if result["new_segment_entry"]:
                seg_num, new_seg = result["new_segment_entry"]
                existing_by_segment_number[seg_num] = new_seg
        
        if segment_index_updates:
            await batch_update_video_segment_current_version_index(segment_index_updates)
            logger.info(f"💾 已更新 {len(segment_index_updates)} 个 segment 的 current_version_index，assemble 将使用最新 version")
        return synced_segments
    
    async def _sync_segments_video_driven(
        self,
        video_versions: List,
        video_gens_map: Dict,
        video_gen_versions_map: Dict,
        video_gen_id_to_all_versions: Dict,
        music_data: Dict,
        existing_segments: List,
        segment_versions_map: Dict,
        conversation_id: str,
        thread_id: str,
        run_id: str,
        user_id: str,
        story_outline_id: str,
        force: bool = False
    ) -> List[Dict]:
        """Video-driven 模式的 segment 同步：一个 shot 对应一个 segment，不需要合并视频。force=True 时不比较版本直接更新。"""
        from ...crud.video.video_segment import create_video_segment, create_video_segment_version, batch_update_video_segment_current_version_index
        from ...crud.video.video_generation import get_video_generations_by_uuids
        from ...models.video_state import VideoSegmentResult
        
        synced_segments = []
        segment_index_updates = []  # sync 后更新 segment.current_version_index，assemble 才能拿到最新 version
        
        # 获取唯一的 music_generation（只有一个）
        music_generation_id = None
        if music_data:
            first_key = next(iter(music_data.keys()))
            music_info = music_data[first_key]
            if music_info and music_info.get('music_generation'):
                music_generation_id = music_info['music_generation'].uuid
                logger.info(f"🎵 Video-driven: 使用 music_generation_id: {music_generation_id}")
        
        # 批量获取 VideoGenerationDB 以获取关联 IDs
        video_gen_ids = [v.video_generation_uuid for v in video_versions]
        video_gen_dbs = await get_video_generations_by_uuids(video_gen_ids)
        video_gen_db_map = {vg.uuid: vg for vg in video_gen_dbs}
        
        segment_number = 1
        
        for video_version in video_versions:
            video_gen_uuid = video_version.video_generation_uuid
            video_gen_version_uuid = video_version.video_generation_version_uuid
            
            video_gen = video_gens_map.get(video_gen_uuid)
            video_gen_version = video_gen_versions_map.get(video_gen_version_uuid)
            
            if not video_gen or not video_gen_version:
                logger.warning(f"⚠️ 找不到 video_generation 或 version: {video_gen_uuid}")
                continue
            
            shot_number = video_gen.shot_number
            video_url = video_gen_version.video_url
            duration = video_gen_version.duration or 0.0
            
            # 从 VideoGenerationDB 获取关联 IDs
            keyframe_ids = []
            scene_ids = []
            storyboard_detail_ids = []
            
            if video_gen_uuid in video_gen_db_map:
                video_gen_db = video_gen_db_map[video_gen_uuid]
                if video_gen_db.keyframe_id:
                    keyframe_ids.append(video_gen_db.keyframe_id)
                if video_gen_db.scene_id:
                    scene_ids.append(video_gen_db.scene_id)
                if video_gen_db.storyboard_detail_id:
                    storyboard_detail_ids.append(video_gen_db.storyboard_detail_id)
            
            # 检查是否已有 segment（通过 video_generation_id 匹配）
            target_segment = None
            for seg in existing_segments:
                if video_gen_uuid in seg.video_generation_ids:
                    target_segment = seg
                    break
            
            if target_segment:
                # 已存在，检查是否需要创建新 version
                logger.info(f"✅ Video-driven: 找到已存在的segment {target_segment.segment_number}")
                
                existing_versions = segment_versions_map.get(target_segment.uuid, [])
                if existing_versions:
                    existing_versions = sorted(existing_versions, key=lambda v: v.version_number)
                
                # 🔧 检查video_generation_version_ids是否有变化
                current_video_gen_version_ids = [video_gen_version_uuid]
                
                if existing_versions:
                    latest_version = existing_versions[-1]  # 最新版本（version_number 最大）
                    previous_video_gen_version_ids = latest_version.video_generation_version_ids or []
                    
                    if not force and set(current_video_gen_version_ids) == set(previous_video_gen_version_ids):
                        logger.info(f"🔄 Video-driven Segment {target_segment.segment_number}: video_generation_version_ids 未变化，跳过sync")
                        logger.info(f"   - version_ids: {sorted(current_video_gen_version_ids)}")
                        
                        synced_segments.append({
                            'segment_uuid': target_segment.uuid,
                            'segment_number': target_segment.segment_number,
                            'action': 'skipped',
                            'reason': 'version_unchanged'
                        })
                        continue
                    elif force and set(current_video_gen_version_ids) == set(previous_video_gen_version_ids):
                        logger.info(f"🔄 Video-driven Segment {target_segment.segment_number}: 版本未变化但 force=True，强制更新")
                    else:
                        logger.info(f"🆕 Video-driven Segment {target_segment.segment_number}: video_generation_version_ids 有变化，需要创建新version")
                        logger.info(f"   - 之前: {sorted(previous_video_gen_version_ids)}")
                        logger.info(f"   - 现在: {sorted(current_video_gen_version_ids)}")
                
                new_version_number = len(existing_versions)
                
                segment_result = VideoSegmentResult(
                    segment_number=target_segment.segment_number,
                    success=video_gen_version.success and bool(video_url),
                    video_generation_ids=[video_gen_uuid],
                    music_generation_id=music_generation_id,
                    narration_ids=[],
                    keyframe_ids=keyframe_ids,
                    scene_ids=scene_ids,
                    storyboard_detail_ids=storyboard_detail_ids,
                    original_video_urls=[video_url] if video_url else [],
                    merged_video_url=video_url,  # video-driven 不需要合并
                    audio_url=None,
                    duration=duration,
                    shot_numbers=[shot_number]
                )
                
                await create_video_segment_version(
                    video_segment_id=target_segment.uuid,
                    segment_result=segment_result,
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    user_id=user_id
                )
                
                segment_index_updates.append({"uuid": target_segment.uuid, "current_version_index": new_version_number})
                synced_segments.append({
                    'segment_uuid': target_segment.uuid,
                    'segment_number': target_segment.segment_number,
                    'action': 'updated',
                    'new_version_number': new_version_number
                })
            else:
                # 不存在，创建新 segment
                logger.info(f"🆕 Video-driven: 创建新segment {segment_number}")
                
                new_segment = await create_video_segment(
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    user_id=user_id,
                    story_outline_id=story_outline_id,
                    music_generation_id=music_generation_id,
                    video_generation_ids=[video_gen_uuid],
                    narration_ids=[],
                    keyframe_ids=keyframe_ids,
                    scene_ids=scene_ids,
                    storyboard_detail_ids=storyboard_detail_ids,
                    segment_number=segment_number
                )
                
                segment_result = VideoSegmentResult(
                    segment_number=segment_number,
                    success=video_gen_version.success and bool(video_url),
                    video_generation_ids=[video_gen_uuid],
                    music_generation_id=music_generation_id,
                    narration_ids=[],
                    keyframe_ids=keyframe_ids,
                    scene_ids=scene_ids,
                    storyboard_detail_ids=storyboard_detail_ids,
                    original_video_urls=[video_url] if video_url else [],
                    merged_video_url=video_url,
                    audio_url=None,
                    duration=duration,
                    shot_numbers=[shot_number]
                )
                
                await create_video_segment_version(
                    video_segment_id=new_segment.uuid,
                    segment_result=segment_result,
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                    run_id=run_id,
                    user_id=user_id
                )
                
                segment_index_updates.append({"uuid": new_segment.uuid, "current_version_index": 0})
                synced_segments.append({
                    'segment_uuid': new_segment.uuid,
                    'segment_number': segment_number,
                    'action': 'created',
                    'new_version_number': 0
                })
                
                segment_number += 1
        
        if segment_index_updates:
            await batch_update_video_segment_current_version_index(segment_index_updates)
            logger.info(f"💾 Video-driven: 已更新 {len(segment_index_updates)} 个 segment 的 current_version_index")
        return synced_segments


# ==================== 全局实例管理 ====================

_video_agent_service_instance = None
def get_video_agent_service() -> VideoAgentService:
    """获取视频Agent服务单例"""
    global _video_agent_service_instance
    if _video_agent_service_instance is None:
        _video_agent_service_instance = VideoAgentService()
    return _video_agent_service_instance


def get_video_agent() -> CompiledStateGraph:
    return get_video_agent_service().agent
