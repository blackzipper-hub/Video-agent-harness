"""Video analysis API endpoints"""

import asyncio
import time
from fastapi import APIRouter, Depends, Security, HTTPException, Query, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
import logging
import os
import shutil

from ...services.auth_service import auth_service
from ...schemas import ResponseModel
from ...services.video_data_service import VideoDataService, video_data_service, get_video_data_service
from ...crud.video.video_audio import (
    get_video_audio_transcription_by_uuid,
    get_video_audio_transcription_by_thread_id,
    get_video_audio_segments_by_transcription_uuid,
    get_video_audio_sections_by_transcription_uuid,
    get_audio_segments_by_uuids,
    get_narrations_by_conversation,
    get_narration_versions_by_narration_ids,
    get_narration_by_uuid,
    get_narrations_by_uuids,
    get_audio_effects_by_conversation,
    get_audio_effect_versions_by_audio_effect_ids,
    get_audio_effect_by_uuid,
    get_audio_effects_by_uuids,
    get_music_generation_by_uuid,
    get_music_generations_by_conversation,
    get_music_generations_by_latest_transcription,
    get_music_generations_by_uuids,
    get_music_generation_versions_by_music_generation_ids
)
from ...crud.video.video_story import (
    get_chapter_by_uuid,
    get_chapters_by_story_outline_id,
    get_video_story_outline_by_uuid,
    get_scene_by_uuid,
    get_scenes_by_uuids,
    get_scenes_by_run_id,
    get_scenes_by_conversation,
    get_storyboard_detail_by_uuid,
    get_detailed_shots_by_storyboard_id,
    get_detailed_shots_by_conversation,
    get_detailed_shots_by_uuids,
    update_chapter,
    update_scene,
    update_detailed_shots_description_by_scene_id,
)
from ...crud.video.video_generation import (
    get_video_generations_by_uuids,
    get_video_generations_by_run_id,
    get_video_generations_by_conversation,
    get_video_generation_versions_by_video_generation_ids
)
from ...crud.video.video_keyframe import (
    get_keyframes_by_run_id,
    get_keyframes_by_conversation,
    get_reflection_results_by_keyframe_version_ids
)
from ...crud.video.video_character import (
    get_character_by_uuid,
    get_characters_by_uuids,
    get_characters_by_conversation
)
from ...crud.conversation import async_get_conversation_by_thread_id
from ...crud.video.video_other import get_video_assembly_by_uuid, get_video_analysis_by_thread_id, get_latest_assembly_mode_by_thread
# 从video/video_audio.py导入返回msgspec对象的版本
from ...crud.video.video_audio import get_music_generations_by_run_id
import json
from ...exceptions import BusinessException, BusinessExceptionCode
from ...utils.asyncpg_utils import utc_isoformat

router = APIRouter(prefix="/video-analysis", tags=["video-analysis"])

logger = logging.getLogger(__name__)


def _parse_style_preferences(style_preferences_str: Optional[str]) -> List[str]:
    """
    解析 style_preferences 字段，兼容字符串和 JSON 数组格式
    
    Args:
        style_preferences_str: 数据库中的 style_preferences 字段值
        
    Returns:
        List[str]: 解析后的风格偏好列表
    """
    if not style_preferences_str:
        return []
    
    # 尝试作为 JSON 数组解析
    try:
        parsed = json.loads(style_preferences_str)
        if isinstance(parsed, list):
            return parsed
        else:
            # JSON 解析成功但不是列表，转换为字符串列表
            return [str(parsed)]
    except (json.JSONDecodeError, TypeError):
        # 如果不是有效的 JSON，作为普通字符串处理，包装成列表
        return [style_preferences_str]


# ==================== Batch Request Models (纯run_id模式) ====================

class VideoGenerationsBatchRequest(BaseModel):
    """视频生成批量查询请求"""
    run_id: str = Field(..., description="执行ID（必填）")


class KeyframesBatchRequest(BaseModel):
    """关键帧批量查询请求"""
    run_id: str = Field(..., description="执行ID（必填）")


class ScenesBatchRequest(BaseModel):
    """场景批量查询请求"""
    run_id: str = Field(..., description="执行ID（必填）")


class MusicGenerationsBatchRequest(BaseModel):
    """音乐生成批量查询请求"""
    run_id: str = Field(..., description="执行ID（必填）")


class ThreadScopedRequest(BaseModel):
    """按 thread_id 聚合查询（方案 A：一个 video space 按 thread 展示）"""
    thread_id: str = Field(..., description="线程ID（必填）")


# ==================== Response Models ====================

class VideoAnalysisResponse(BaseModel):
    """视频分析响应模型：style_preferences 供 StyleSection；content_category 供面板与旁白展示对齐。"""
    style_preferences: List[str] = []
    content_category: Optional[str] = None


class AudioSegmentResponse(BaseModel):
    """音频片段响应模型（含 music_mv 三层 segment 级字段）"""
    uuid: str
    id: int
    start: float
    end: float
    duration: float
    text: str
    emotion: Optional[str] = None
    tempo: Optional[str] = None
    vocal_presence: Optional[bool] = None


class AudioWordResponse(BaseModel):
    """音频词汇响应模型"""
    id: int
    word: str
    start: float
    end: float


class AudioSectionResponse(BaseModel):
    """音频段落响应模型（music_mv 三层 section 级，对应 video_audio_section）"""
    uuid: str
    transcription_uuid: str
    section_type: str
    start_time: float
    end_time: float
    musical_features: Optional[str] = None
    section_emotion: Optional[str] = None
    suggested_visual_intensity: Optional[str] = None
    suggested_rhythmic_strategy: Optional[str] = None
    suggested_visual_theme: Optional[str] = None
    suggested_context: Optional[str] = None


class AudioTranscriptionResponse(BaseModel):
    """音频转录响应模型（含 music_mv 三层 Global + sections + segments）"""
    task: str
    language: str
    duration: float
    text: str
    audio_url: str
    run_id: str
    segments: List[AudioSegmentResponse]
    sections: List[AudioSectionResponse] = []  # 段落列表，按 start_time 排序
    created_at: str
    updated_at: str
    words: Optional[List[AudioWordResponse]] = None  # 使用专门的响应模型
    ass_subtitle_url: Optional[str] = None  # 基于 words 生成的LLM智能字幕
    # 整曲 Global（music_mv 三层）
    song_name: Optional[str] = None
    global_bpm: Optional[float] = None
    genre: Optional[str] = None
    global_emotion: Optional[str] = None
    suggested_global_theme: Optional[str] = None
    suggested_color_palette: Optional[str] = None


class StoryOutlineResponse(BaseModel):
    """故事大纲响应模型（仅含前端展示用字段；风格只用 analysis 的 style_preferences，此处不再返回 style_guide）"""
    uuid: str  # 供前端拉取章节列表、编辑章节用
    title: str
    theme: str
    description: str
    key_message: str
    total_duration: float  # 支持 double 新列（秒），兼容老数据 int
    structure: List[Dict[str, Any]] = []  # 章节对象列表


class CharacterVersionResponse(BaseModel):
    """角色版本响应模型"""
    id: str
    version_number: int
    character_image_url: str
    t2i_prompt: str
    provider: str
    reference_image_urls: Optional[List[str]] = None
    success: bool
    error_msg: Optional[str] = None
    created_at: str
    updated_at: str


class CharacterResponse(BaseModel):
    """角色响应模型"""
    id: str
    name: str
    image_url: Optional[str] = None  # 兼容老数据
    type: Optional[str] = None  # character / location / object
    current_version_index: int = 0  # 仅在 get_characters_by_run_id 接口中使用
    selected_version_id: Optional[str] = None  # ⭐ 选中的角色版本UUID，仅在 get_characters_by_run_id 接口中使用
    versions: Optional[List[CharacterVersionResponse]] = None  # 版本列表，仅在 get_characters_by_run_id 接口中使用
    run_id: str


class CharacterListResponse(BaseModel):
    """角色列表响应模型"""
    characters: List[CharacterResponse]


class CharacterInfo(BaseModel):
    """角色信息模型"""
    uuid: str
    name: str
    image_url: Optional[str] = None


# Chapter Response Models
class ChapterResponse(BaseModel):
    """章节响应模型"""
    uuid: str
    order: int
    title: str
    description: str
    duration: float


class ChapterUpdateRequest(BaseModel):
    """章节编辑请求（uuid 在 body，直接改主表）"""
    uuid: str
    title: Optional[str] = None
    description: Optional[str] = None
    duration: Optional[float] = None
    order: Optional[int] = None
    audio_segment_ids: Optional[List[str]] = None
    additional_data: Optional[Dict[str, Any]] = None
    audio_section_uuid: Optional[str] = None


class ChapterListResponse(BaseModel):
    """章节列表响应"""
    chapters: List[ChapterResponse]


class SceneResponse(BaseModel):
    """场景响应模型"""
    uuid: str
    scene_number: int
    title: str
    description: str
    duration: float
    run_id: str
    character_ids: List[str] = Field(default_factory=list)
    narrations: List["SceneNarrationItem"] = Field(default_factory=list)


class SceneNarrationItem(BaseModel):
    """场景内镜头旁白（分镜文案 + 可选 TTS）"""
    shot_number: int
    narration_text: Optional[str] = None
    enhanced_prompt: Optional[str] = None
    audio_url: Optional[str] = None
    duration: Optional[float] = None
    has_narration: bool = True


class SceneUpdateRequest(BaseModel):
    """场景编辑请求（uuid 在 body，直接改主表）"""
    uuid: str
    title: Optional[str] = None
    description: Optional[str] = None
    duration: Optional[float] = None
    camera_angle: Optional[str] = None
    character_action: Optional[str] = None
    visual_style: Optional[str] = None
    transition_style: Optional[str] = None
    is_bridge: Optional[bool] = None
    character_ids: Optional[List[str]] = None
    audio_segment_ids: Optional[List[str]] = None
    chapter_id: Optional[str] = None
    generation_mode: Optional[str] = None
    additional_data: Optional[Dict[str, Any]] = None


class SceneListResponse(BaseModel):
    """场景列表响应模型"""
    scenes: List[SceneResponse]


class DetailedShotResponse(BaseModel):
    """详细镜头响应模型"""
    shot_number: int
    duration: float  # 支持 double（秒），兼容老数据 int
    shot_type: str
    camera_movement: str


def _parse_content_category_from_conversation_user_option(raw: Optional[Any]) -> Optional[str]:
    """从 conversations.user_option JSON 读取 content_category。"""
    if raw is None:
        return None
    try:
        if isinstance(raw, str):
            d = json.loads(raw)
        elif isinstance(raw, dict):
            d = raw
        else:
            return None
        cc = d.get("content_category")
        return str(cc) if cc is not None else None
    except Exception:
        return None


def _content_category_supports_scene_narrations(content_category: Optional[str]) -> bool:
    from ...models.tool_enums import ContentCategory

    cc = ContentCategory.from_value(content_category)
    return cc in (ContentCategory.PRODUCT_LAUNCH, ContentCategory.SHORT_DRAMA)


async def _resolve_content_category_for_thread(thread_id: str) -> Optional[str]:
    analysis = await get_video_analysis_by_thread_id(thread_id)
    if analysis and getattr(analysis, "content_category", None):
        return str(analysis.content_category)
    conversation = await async_get_conversation_by_thread_id(thread_id)
    if conversation:
        return _parse_content_category_from_conversation_user_option(
            getattr(conversation, "user_option", None)
        )
    return None


def _version_additional_data(ver: Any) -> Dict[str, Any]:
    ad = getattr(ver, "additional_data", None)
    if ad is None and isinstance(ver, dict):
        ad = ver.get("additional_data")
    if isinstance(ad, str):
        try:
            ad = json.loads(ad)
        except Exception:
            return {}
    return ad if isinstance(ad, dict) else {}


def _preview_video_url_from_version(ver: Any) -> Optional[str]:
    """从 version.additional_data 读取 lipsync 带声预览 URL。"""
    pv = _version_additional_data(ver).get("preview_video_url")
    return str(pv) if pv else None


def _reference_image_urls_from_version(ver: Any) -> List[str]:
    """从 version.additional_data 读取 T2V/视频生成时实际使用的参考图。"""
    refs = _version_additional_data(ver).get("reference_image_urls")
    if not isinstance(refs, list):
        return []
    return [str(u) for u in refs if u]


async def _build_scene_narrations_map(
    scene_uuids: List[str],
    conversation_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    content_category: Optional[str] = None,
) -> Dict[str, List[SceneNarrationItem]]:
    """按场景聚合镜头旁白文案；若提供 thread 则合并 TTS 音频。仅 Product Launch 返回旁白。"""
    from ...crud.video.video_story import batch_get_scenes_with_shots

    if not scene_uuids:
        return {}

    if not _content_category_supports_scene_narrations(content_category):
        return {scene_uuid: [] for scene_uuid in scene_uuids}

    audio_by_shot: Dict[int, Dict[str, Any]] = {}
    if conversation_id and thread_id:
        try:
            narrations = await get_narrations_by_conversation(conversation_id, thread_id)
            if narrations:
                narration_ids = [n.uuid for n in narrations]
                versions = await get_narration_versions_by_narration_ids(narration_ids)
                versions_by_narration: Dict[str, list] = {}
                for version in versions:
                    versions_by_narration.setdefault(version.narration_id, []).append(version)
                for narration in narrations:
                    vers = versions_by_narration.get(narration.uuid) or []
                    if not vers:
                        continue
                    idx = narration.current_version_index or 0
                    if idx < 0 or idx >= len(vers):
                        idx = 0
                    current = vers[idx]
                    if not getattr(current, "success", True):
                        continue
                    audio_by_shot[narration.shot_number] = {
                        "narration_text": current.narration_text,
                        "enhanced_prompt": current.enhanced_prompt,
                        "audio_url": current.audio_url,
                        "duration": current.duration,
                    }
        except Exception as e:
            logger.warning("build_scene_narrations_map: load TTS failed: %s", e)

    scenes_with_shots = await batch_get_scenes_with_shots(scene_uuids)
    result: Dict[str, List[SceneNarrationItem]] = {}
    for scene_uuid, payload in scenes_with_shots.items():
        items: List[SceneNarrationItem] = []
        for shot in payload.get("shots") or []:
            shot_number = int(getattr(shot, "shot_number", 0) or 0)
            narration_text = getattr(shot, "narration", None) or ""
            audio = audio_by_shot.get(shot_number, {})
            gm = str(getattr(shot, "generation_mode", None) or "").strip().lower()
            if gm == "empty_shot":
                items.append(
                    SceneNarrationItem(
                        shot_number=shot_number,
                        has_narration=False,
                        duration=getattr(shot, "duration", None),
                    )
                )
                continue
            if not narration_text.strip() and not audio:
                continue
            items.append(
                SceneNarrationItem(
                    shot_number=shot_number,
                    narration_text=narration_text or audio.get("narration_text"),
                    enhanced_prompt=audio.get("enhanced_prompt") or narration_text or None,
                    audio_url=audio.get("audio_url"),
                    duration=audio.get("duration") or getattr(shot, "duration", None),
                )
            )
        result[scene_uuid] = items
    return result


def _parse_enable_continuity_mode_from_conversation_user_option(raw: Optional[Any]) -> bool:
    """从 conversations.user_option JSON 读取是否开启首尾帧连续模式。"""
    if raw is None:
        return False
    try:
        if isinstance(raw, str):
            d = json.loads(raw)
        elif isinstance(raw, dict):
            d = raw
        else:
            return False
        return bool(d.get("enable_continuity_mode"))
    except Exception:
        return False


def _infer_continuity_from_keyframe_response_counts(responses: List[Any]) -> bool:
    """同一 shot_number 下有多条 keyframe 根记录时，推断为首尾帧模式（无 user_option 时的兜底）。"""
    by_shot: Dict[int, int] = {}
    for kf in responses:
        sn = getattr(kf, "shot_number", None)
        if sn is not None:
            by_shot[sn] = by_shot.get(sn, 0) + 1
    return any(c >= 2 for c in by_shot.values()) if by_shot else False


def _keyframe_list_shot_and_record_totals(
    *,
    shot_total_n: int,
    continuity_from_user: bool,
    keyframe_responses: List[Any],
) -> tuple[int, int]:
    """返回 (shot_total, 期望关键帧根记录条数)。首尾帧模式下每条镜头对应首帧+尾帧两条根记录，故为 2×shot_total。"""
    st = shot_total_n
    if st <= 0 and keyframe_responses:
        st = len({getattr(r, "shot_number", None) for r in keyframe_responses if getattr(r, "shot_number", None) is not None})
    if st <= 0:
        return 0, 0
    infer_cont = _infer_continuity_from_keyframe_response_counts(keyframe_responses)
    use_cont = continuity_from_user or infer_cont
    mult = 2 if use_cont else 1
    return st, st * mult


class KeyframeVersionResponse(BaseModel):
    """关键帧版本响应模型"""
    uuid: str
    version_number: int
    shot_number: int
    keyframe_url: str
    t2i_prompt: str
    reference_image_urls: List[str] = []
    success: bool
    error_msg: Optional[str] = None
    frame_index: int = Field(default=0, description="帧索引：0=首帧, -1=尾帧, 1/2/3...=中间帧")
    reflection_issues: List[Dict[str, Any]] = Field(default_factory=list, description="该版本对应的 keyframe reflection 问题列表（如有）")


class KeyframeResponse(BaseModel):
    """关键帧响应模型"""
    uuid: str
    shot_number: int
    reference_image_urls: List[str] = []
    current_version_index: int
    versions: List[KeyframeVersionResponse] = []
    run_id: str
    generation_mode: Optional[str] = None  # normal | lipsync | empty_shot，来自 detailed_shot，用于前端展示与 hover 说明


class KeyframeListResponse(BaseModel):
    """关键帧列表响应模型"""
    keyframes: List[KeyframeResponse]
    shot_total: int = Field(
        0,
        description="分镜镜头数（与 detailed_shots 一致），前端按镜占位 1…N，与 scenes.length 对齐",
    )
    total: int = Field(
        0,
        description="期望关键帧根记录条数：普通模式=shot_total；首尾帧模式(enable_continuity_mode)=2×shot_total（每镜首帧+尾帧各一条根记录）",
    )


class NarrationVersionResponse(BaseModel):
    """旁白版本响应模型"""
    narration_text: str
    audio_url: Optional[str] = None


class NarrationResponse(BaseModel):
    """旁白响应模型"""
    shot_number: int
    current_version_index: int
    versions: List[NarrationVersionResponse] = []


class NarrationListResponse(BaseModel):
    """旁白列表响应模型"""
    narrations: List[NarrationResponse]


class AudioEffectVersionResponse(BaseModel):
    """音效版本响应模型"""
    audio_prompt: str
    audio_url: Optional[str] = None
    
    @property
    def prompt(self) -> str:
        """兼容性属性：前端使用 .prompt 访问 audio_prompt"""
        return self.audio_prompt


class AudioEffectResponse(BaseModel):
    """音效响应模型"""
    shot_number: int
    current_version_index: int
    versions: List[AudioEffectVersionResponse] = []


class AudioEffectListResponse(BaseModel):
    """音效列表响应模型"""
    audio_effects: List[AudioEffectResponse]


class MusicGenerationVersionResponse(BaseModel):
    """音乐生成版本响应模型"""
    uuid: str
    music_prompt: Optional[str] = None
    music_url: Optional[str] = None


class MusicGenerationResponse(BaseModel):
    """音乐生成响应模型"""
    uuid: str
    is_instrumental: bool
    current_version_index: int
    music_url: Optional[str] = None  # 当前版本的完整音乐 URL，与 music_generation 平级
    versions: List[MusicGenerationVersionResponse]


class MusicGenerationListResponse(BaseModel):
    """音乐生成列表响应模型"""
    music_generations: List[MusicGenerationResponse]
    assembly_mode: Optional[str] = None  # audio_driven / video_driven / music_driven，用于展示
    music_url: Optional[str] = None  # 完整音乐 URL，与 assembly_mode 平级；有 is_full_story_music 时取该条 BGM 的当前版本 URL，否则空


class VideoGenerationVersionResponse(BaseModel):
    """视频生成版本响应模型"""
    uuid: str
    version_number: int
    shot_number: int
    video_url: str
    success: bool
    error_msg: Optional[str] = None
    keyframe_url: Optional[str] = None
    motion_prompt: Optional[str] = None
    duration: Optional[float] = None
    preview_video_url: Optional[str] = Field(
        default=None,
        description="lipsync 去音轨前的带声预览，供核对口型/音频",
    )
    audio_url: Optional[str] = Field(
        default=None,
        description="lipsync 使用的参考音频 URL",
    )


class VideoGenerationResponse(BaseModel):
    """视频生成响应模型"""
    uuid: str
    shot_number: int
    keyframe_url: Optional[str] = None
    current_version_index: int
    versions: List[VideoGenerationVersionResponse]
    keyframe_id: Optional[str] = None
    run_id: str


class VideoGenerationListResponse(BaseModel):
    """视频生成列表响应模型"""
    video_generations: List[VideoGenerationResponse]
    total: int = Field(
        0,
        description="期望镜头槽位数（通常等于该 thread 下分镜镜头数），供前端与 video_generations 列表对齐占位",
    )


class VideoAssemblyResponse(BaseModel):
    """视频合成响应模型"""
    uuid: str
    final_video_url: str
    final_video_url_no_subtitle: Optional[str] = None
    total_duration: float
    success: bool
    error_msg: Optional[str] = None
    assembly_mode: Optional[str] = None
    story_outline_id: str


class StoryboardDetailResponse(BaseModel):
    """详细分镜响应模型"""
    total_duration: float  # 支持 double 新列（秒），兼容老数据 int
    visual_style: str
    shots_count: int
    shots: List[DetailedShotResponse]
    run_id: str
    created_at: str
    updated_at: str


@router.get("/analysis/{uuid}", response_model=ResponseModel[VideoAnalysisResponse])
async def get_analysis_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    service: VideoDataService = Depends(get_video_data_service)
):
    """根据UUID获取视频分析数据。主流程只展示 style_preferences（StyleSection），只返回该字段。"""
    analysis = await service.get_video_analysis(uuid, user_id)
    response_data = VideoAnalysisResponse(
        style_preferences=_parse_style_preferences(analysis.style_preferences),
        content_category=getattr(analysis, "content_category", None),
    )
    
    return ResponseModel.success(data=response_data)


@router.post("/analysis-by-thread", response_model=ResponseModel[VideoAnalysisResponse])
async def get_analysis_data_by_thread(
    request: ThreadScopedRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """根据 thread_id 获取该 thread 下最新一条视频分析（style_preferences + content_category）。"""
    conversation = await async_get_conversation_by_thread_id(request.thread_id)
    if not conversation or conversation.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权访问此 thread 或对话不存在"
        )
    analysis = await get_video_analysis_by_thread_id(request.thread_id)
    if not analysis or getattr(analysis, "user_id", None) != user_id:
        return ResponseModel.success(data=VideoAnalysisResponse(style_preferences=[]))
    response_data = VideoAnalysisResponse(
        style_preferences=_parse_style_preferences(analysis.style_preferences),
        content_category=getattr(analysis, "content_category", None),
    )
    return ResponseModel.success(data=response_data)


@router.get("/audio-transcription/{uuid}", response_model=ResponseModel[AudioTranscriptionResponse])
async def get_audio_transcription_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    service: VideoDataService = Depends(get_video_data_service)
):
    """根据UUID获取音频转录数据"""
    # service已通过Depends注入
    
    # 获取音频转录及其片段数据
    transcription, segments = await service.get_audio_transcription_with_segments(uuid, user_id)
    
    # 获取该转录下的段落（music_mv 三层 section）
    sections_db = await get_video_audio_sections_by_transcription_uuid(uuid)
    section_responses = [
        AudioSectionResponse(
            uuid=getattr(s, "uuid", ""),
            transcription_uuid=uuid,
            section_type=getattr(s, "section_type", ""),
            start_time=getattr(s, "start_time", 0.0),
            end_time=getattr(s, "end_time", 0.0),
            musical_features=getattr(s, "musical_features", None),
            section_emotion=getattr(s, "section_emotion", None),
            suggested_visual_intensity=getattr(s, "suggested_visual_intensity", None),
            suggested_rhythmic_strategy=getattr(s, "suggested_rhythmic_strategy", None),
            suggested_visual_theme=getattr(s, "suggested_visual_theme", None),
            suggested_context=getattr(s, "suggested_context", None),
        )
        for s in sections_db
    ]
    
    # 构建响应数据（含 segment 级 emotion / tempo / vocal_presence）
    segment_responses = [
        AudioSegmentResponse(
            uuid=seg.uuid,
            id=seg.segment_id,
            start=seg.start,
            end=seg.end,
            duration=seg.duration,
            text=seg.text,
            emotion=getattr(seg, "emotion", None),
            tempo=getattr(seg, "tempo", None),
            vocal_presence=getattr(seg, "vocal_presence", None),
        )
        for seg in segments
    ]
    
    # 从 additional_data 中提取 words 和字幕URL
    words_responses = None
    ass_subtitle_url = None
    if transcription.additional_data:
        words_data = transcription.additional_data.get('words')
        if words_data:
            # 转换为 AudioWordResponse 对象列表
            words_responses = [
                AudioWordResponse(
                    id=word_dict.get('id', idx + 1),  # 兼容旧数据
                    word=word_dict['word'],
                    start=word_dict['start'],
                    end=word_dict['end']
                )
                for idx, word_dict in enumerate(words_data)
            ]
        ass_subtitle_url = transcription.additional_data.get('ass_subtitle_url')
    
    response_data = AudioTranscriptionResponse(
        task=transcription.task,
        language=transcription.language,
        duration=transcription.duration,
        text=transcription.text,
        audio_url=transcription.audio_url,
        run_id=transcription.run_id,
        segments=segment_responses,
        sections=section_responses,
        created_at=utc_isoformat(transcription.created_at),
        updated_at=utc_isoformat(transcription.updated_at),
        words=words_responses,
        ass_subtitle_url=ass_subtitle_url,
        song_name=getattr(transcription, "song_name", None),
        global_bpm=getattr(transcription, "global_bpm", None),
        genre=getattr(transcription, "genre", None),
        global_emotion=getattr(transcription, "global_emotion", None),
        suggested_global_theme=getattr(transcription, "suggested_global_theme", None),
        suggested_color_palette=getattr(transcription, "suggested_color_palette", None),
    )
    
    return ResponseModel.success(data=response_data)


@router.get("/story-outline/{uuid}", response_model=ResponseModel[StoryOutlineResponse])
async def get_story_outline_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    service: VideoDataService = Depends(get_video_data_service)
):
    """根据UUID获取故事大纲数据"""
    # service已通过Depends注入
    outline, structure = await service.get_story_outline_with_structure(uuid, user_id)
    
    # 构建响应数据
    outline_uuid = outline.uuid if hasattr(outline, 'uuid') else outline.get('uuid', '')
    response_data = StoryOutlineResponse(
        uuid=outline_uuid,
        title=outline.title,
        theme=outline.theme,
        description=outline.description,
        key_message=outline.key_message,
        total_duration=outline.total_duration,
        structure=structure,
    )
    
    return ResponseModel.success(data=response_data)


@router.get("/character/{uuid}", response_model=ResponseModel[CharacterResponse])
async def get_character_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)  # ✅ 修复：使用 FastAPI 依赖注入
):
    """根据UUID获取角色数据"""
    character = await get_character_by_uuid(uuid)
    if not character:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "Character data not found"
        )
    
    # 检查权限
    if character.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access this character data"
        )
    
    # 构建响应数据
    response_data = CharacterResponse(
        id=character.uuid,
        name=character.name,
        image_url=character.image_url,
        type=getattr(character, "type", None),
        run_id=character.run_id
    )
    
    return ResponseModel.success(data=response_data)


@router.get("/characters", response_model=ResponseModel[CharacterListResponse])
async def get_characters_data(
    uuids: str,  # 逗号分隔的UUID列表
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    service: VideoDataService = Depends(get_video_data_service)
):
    """根据UUID列表获取多个角色数据"""
    # service已通过Depends注入
    uuid_list = [uuid.strip() for uuid in uuids.split(',') if uuid.strip()]
    if not uuid_list:
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER,
            "No valid UUIDs provided"
        )
    
    characters = await service.get_characters(uuid_list, user_id)
    if not characters:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "No character data found"
        )
        
    # 构建响应数据，包含版本兼容性处理
    character_responses = []
    for character in characters:
        # 获取当前版本的图片URL（兼容性逻辑）
        character_image_url = character.image_url
        current_image_url = character_image_url or ""
        character_uuid = character.uuid
        character_name = character.name
        logger.info(f"🎭 处理角色 {character_name} (UUID: {character_uuid})")
        logger.info(f"🎭 原始image_url: {character_image_url}")
        
        # 尝试获取最新版本的图片
        try:
            from ...crud.video.video_character import get_character_versions_by_character_id
            version_records = await get_character_versions_by_character_id(character_uuid, user_id)
            logger.info(f"🎭 找到 {len(version_records) if version_records else 0} 个版本记录")
            
            if version_records:
                # 获取当前版本索引对应的版本，如果没有则使用最新版本
                current_version_index = getattr(character, "current_version_index", 0)
                logger.info(f"🎭 当前版本索引: {current_version_index}")
                
                if current_version_index < len(version_records):
                    current_version = version_records[current_version_index]
                else:
                    # 使用最新版本
                    current_version = max(version_records, key=lambda v: (v.version_number))
                
                version_num = current_version.version_number
                version_img = current_version.character_image_url
                logger.info(f"🎭 选择版本 {version_num}, character_image_url: {version_img}")
                
                if version_img:
                    current_image_url = version_img
        except Exception as e:
            # 如果获取版本失败，使用原有的image_url
            logger.warning(f"🎭 获取角色版本失败: {e}")
            pass
        
        logger.info(f"🎭 最终image_url: {current_image_url}")
        
        character_responses.append(CharacterResponse(
            id=character_uuid,
            name=character_name,
            image_url=current_image_url,  # 使用兼容性处理后的图片URL
            type=getattr(character, "type", None),
            run_id=character.run_id
        ))
    
    
    response_data = CharacterListResponse(
        characters=character_responses
    )
    
    return ResponseModel.success(data=response_data)


@router.get("/characters/run/{run_id}", response_model=ResponseModel[CharacterListResponse])
async def get_characters_by_run_id(
    run_id: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    include_versions: bool = Query(False, description="是否包含版本信息")  # ✅ 修复：使用 FastAPI 依赖注入
):
    """根据run_id获取角色数据列表"""
    from ...crud.video.video_character import get_characters_by_run_id, get_character_versions_by_character_id
    
    try:
        characters = await get_characters_by_run_id(run_id, user_id)
        if not characters:
            return ResponseModel.success(data=CharacterListResponse(characters=[]))
        
        # 构建响应数据
        character_responses = []
        for character in characters:
            versions = None
            if include_versions:
                # 获取版本信息
                version_records = await get_character_versions_by_character_id(character.uuid, user_id)
                
                versions = []
                for version in version_records:
                    v_uuid = version.uuid
                    v_version_number = version.version_number
                    v_character_image_url = version.character_image_url
                    v_t2i_prompt = version.t2i_prompt
                    v_provider = version.provider
                    # reference_image_urls 已在 CRUD 层统一规范为 list
                    v_reference_image_urls = version.reference_image_urls or []
                    v_success = version.success
                    v_error_msg = version.error_msg
                    v_created_at = version.created_at
                    v_updated_at = version.updated_at
                    
                    versions.append(
                        CharacterVersionResponse(
                            id=version.uuid,
                            version_number=version.version_number,
                            character_image_url=version.character_image_url,
                            t2i_prompt=version.t2i_prompt,
                            provider=version.provider,
                            reference_image_urls=v_reference_image_urls,
                            success=version.success,
                            error_msg=version.error_msg,
                            created_at=utc_isoformat(getattr(version, 'created_at', None)),
                            updated_at=utc_isoformat(getattr(version, 'updated_at', None))
                        )
                    )
            
            # 获取当前版本的图片URL（兼容性逻辑）
            # 1. 没有版本时使用老字段image_url
            # 2. 有版本时优先使用当前版本的character_image_url
            # character 为 VideoCharacterDB (msgspec Struct)，用属性访问
            current_image_url = getattr(character, 'image_url', None) or ""
            current_version_index = getattr(character, 'current_version_index', 0)
            if versions and len(versions) > current_version_index:
                current_version = versions[current_version_index]
                if current_version.character_image_url:
                    current_image_url = current_version.character_image_url
            
            character_responses.append(CharacterResponse(
                id=character.uuid,
                name=character.name,
                image_url=current_image_url,
                type=getattr(character, "type", None),
                current_version_index=current_version_index,
                selected_version_id=getattr(character, 'selected_version_id', None),  # ⭐ 选中的角色版本ID
                versions=versions,
                run_id=character.run_id
            ))
        
        response_data = CharacterListResponse(
            characters=character_responses
        )
        
        return ResponseModel.success(data=response_data)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"根据run_id获取角色失败: {e}")
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            f"Failed to fetch characters by run_id: {str(e)}"
        )
    # ✅ 修复：使用 Depends(get_async_db) 时，FastAPI 会自动管理连接的关闭，不需要手动关闭


@router.get("/chapters-by-outline/{outline_uuid}", response_model=ResponseModel[ChapterListResponse])
async def get_chapters_by_outline(
    outline_uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """根据故事大纲 UUID 获取章节列表（含 uuid，供前端双击编辑）"""
    outline = await get_video_story_outline_by_uuid(outline_uuid)
    if not outline:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "故事大纲不存在"
        )
    if outline.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access this outline"
        )
    chapters = await get_chapters_by_story_outline_id(outline_uuid)
    from app.utils.chapter_order import sort_chapters_by_order

    sorted_chapters = sort_chapters_by_order(chapters)
    result = ChapterListResponse(
        chapters=[
            ChapterResponse(
                uuid=ch.uuid,
                order=i,
                title=ch.title,
                description=ch.description,
                duration=ch.duration
            )
            for i, ch in enumerate(sorted_chapters)
        ]
    )
    return ResponseModel.success(data=result)


@router.get("/chapter/{uuid}", response_model=ResponseModel[ChapterResponse])
async def get_chapter_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)  # ✅ 修复：使用 FastAPI 依赖注入
):
    """根据UUID获取章节数据"""
    chapter = await get_chapter_by_uuid(uuid)
    if not chapter:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "章节不存在"
        )
    
    result = ChapterResponse(
        uuid=chapter.uuid,
        order=chapter.order,
        title=chapter.title,
        description=chapter.description,
        duration=chapter.duration
    )
    
    return ResponseModel.success(data=result)


@router.post("/chapter", response_model=ResponseModel[ChapterResponse])
async def post_chapter_edit(
    request: ChapterUpdateRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """编辑章节（直接改主表，无版本表）。uuid 在 request body。"""
    uuid = request.uuid
    chapter = await get_chapter_by_uuid(uuid)
    if not chapter:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "章节不存在"
        )
    if chapter.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to edit this chapter"
        )
    payload = request.model_dump(exclude_unset=True)
    payload.pop("uuid", None)
    if not payload:
        result = ChapterResponse(
            uuid=chapter.uuid,
            order=chapter.order,
            title=chapter.title,
            description=chapter.description,
            duration=chapter.duration
        )
        return ResponseModel.success(data=result)
    ok = await update_chapter(uuid, payload)
    if not ok:
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            "更新章节失败"
        )
    updated = await get_chapter_by_uuid(uuid)
    result = ChapterResponse(
        uuid=updated.uuid,
        order=updated.order,
        title=updated.title,
        description=updated.description,
        duration=updated.duration
    )
    return ResponseModel.success(data=result)


@router.get("/scene/{uuid}", response_model=ResponseModel[SceneResponse])
async def get_scene_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)  # ✅ 修复：使用 FastAPI 依赖注入
):
    """根据UUID获取场景数据"""
    scene = await get_scene_by_uuid(uuid)
    if not scene:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "Scene data not found"
        )
    
    # 检查权限
    if scene.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access this scene data"
        )
    
    # 构建响应数据
    response_data = SceneResponse(
        uuid=scene.uuid,
        scene_number=scene.scene_number,
        title=scene.title,
        description=scene.description,
        duration=scene.duration,
        run_id=scene.run_id,
        character_ids=scene.character_ids or [],
    )
    
    return ResponseModel.success(data=response_data)


@router.post("/scene", response_model=ResponseModel[SceneResponse])
async def post_scene_edit(
    request: SceneUpdateRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """编辑场景（直接改主表，无版本表）。uuid 在 request body。"""
    uuid = request.uuid
    scene = await get_scene_by_uuid(uuid)
    if not scene:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "Scene data not found"
        )
    if scene.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to edit this scene"
        )
    payload = request.model_dump(exclude_unset=True)
    payload.pop("uuid", None)
    if not payload:
        response_data = SceneResponse(
            uuid=scene.uuid,
            scene_number=scene.scene_number,
            title=scene.title,
            description=scene.description,
            duration=scene.duration,
            run_id=scene.run_id,
            character_ids=scene.character_ids or [],
        )
        return ResponseModel.success(data=response_data)
    ok = await update_scene(uuid, payload)
    if not ok:
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            "更新场景失败"
        )
    updated = await get_scene_by_uuid(uuid)
    if "description" in payload and updated and updated.description is not None:
        await update_detailed_shots_description_by_scene_id(uuid, updated.description)
    response_data = SceneResponse(
        uuid=updated.uuid,
        scene_number=updated.scene_number,
        title=updated.title,
        description=updated.description,
        duration=updated.duration,
        run_id=updated.run_id,
        character_ids=updated.character_ids or [],
    )
    return ResponseModel.success(data=response_data)


@router.post("/scenes", response_model=ResponseModel[SceneListResponse])
async def get_scenes_data(
    request: ScenesBatchRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    service: VideoDataService = Depends(get_video_data_service)
):
    """根据run_id获取场景数据（批量）"""
    # service已通过Depends注入
    
    # 使用run_id获取所有场景
    scenes_from_db = await get_scenes_by_run_id(request.run_id)
    if not scenes_from_db:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            f"No scene data found for run_id: {request.run_id}"
        )
    
    uuid_list = [scene.uuid for scene in scenes_from_db]
    if not uuid_list:
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER,
            "No valid UUIDs provided"
        )
    
    scenes = await service.get_scenes(uuid_list, user_id)
    if not scenes:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "No scene data found"
        )
    scenes = sorted(scenes, key=lambda x: x.scene_number)
    
    # 收集所有角色UUID
    all_character_uuids = []
    for scene in scenes:
        scene_character_ids = scene.character_ids
        if scene_character_ids:
            all_character_uuids.extend(scene_character_ids)
    
    # 去重并获取角色信息
    unique_character_uuids = list(set(all_character_uuids))
    characters_data = await service.get_characters_info_by_uuids(unique_character_uuids, user_id)
    
    # 创建角色UUID到角色信息的映射
    character_map = {char.uuid: char for char in characters_data}
    
    # 收集所有音频片段UUID
    all_audio_segment_uuids = []
    for scene in scenes:
        scene_audio_segment_ids = scene.audio_segment_ids
        if scene_audio_segment_ids:
            all_audio_segment_uuids.extend(scene_audio_segment_ids)
    
    # 去重并获取音频片段信息
    unique_audio_segment_uuids = list(set(all_audio_segment_uuids))
    audio_segments_data = await get_audio_segments_by_uuids(unique_audio_segment_uuids)
    
    # 创建音频片段UUID到音频片段信息的映射
    audio_segment_map = {seg.uuid: seg for seg in audio_segments_data}
    
    # 构建响应数据
    thread_id = scenes_from_db[0].thread_id if scenes_from_db else None
    content_category = await _resolve_content_category_for_thread(thread_id) if thread_id else None
    narrations_map = await _build_scene_narrations_map(
        uuid_list, content_category=content_category,
    )
    scene_responses = [
        SceneResponse(
            uuid=scene.uuid,
            scene_number=scene.scene_number,
            title=scene.title,
            description=scene.description,
            duration=scene.duration,
            run_id=scene.run_id,
            character_ids=scene.character_ids or [],
            narrations=narrations_map.get(scene.uuid, []),
        )
        for scene in scenes
    ]
    
    response_data = SceneListResponse(
        scenes=scene_responses
    )
    
    return ResponseModel.success(data=response_data)


@router.post("/scenes-by-thread", response_model=ResponseModel[SceneListResponse])
async def get_scenes_data_by_thread(
    request: ThreadScopedRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    service: VideoDataService = Depends(get_video_data_service)
):
    """根据 thread_id 获取该 thread 下所有场景（多 run 聚合）。无数据时返回空列表。"""
    conversation = await async_get_conversation_by_thread_id(request.thread_id)
    if not conversation or conversation.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权访问此 thread 或对话不存在"
        )
    conv_id = str(conversation.id)
    scenes_from_db = await get_scenes_by_conversation(conv_id, request.thread_id)
    if not scenes_from_db:
        return ResponseModel.success(data=SceneListResponse(scenes=[]))
    uuid_list = [s.uuid for s in scenes_from_db]
    scenes = await service.get_scenes(uuid_list, user_id)
    if not scenes:
        return ResponseModel.success(data=SceneListResponse(scenes=[]))
    scenes = sorted(scenes, key=lambda x: x.scene_number)
    all_character_uuids = []
    for scene in scenes:
        if scene.character_ids:
            all_character_uuids.extend(scene.character_ids)
    unique_character_uuids = list(set(all_character_uuids))
    characters_data = await service.get_characters_info_by_uuids(unique_character_uuids, user_id)
    character_map = {char.uuid: char for char in characters_data}
    all_audio_segment_uuids = []
    for scene in scenes:
        if scene.audio_segment_ids:
            all_audio_segment_uuids.extend(scene.audio_segment_ids)
    unique_audio_segment_uuids = list(set(all_audio_segment_uuids))
    audio_segments_data = await get_audio_segments_by_uuids(unique_audio_segment_uuids)
    audio_segment_map = {seg.uuid: seg for seg in audio_segments_data}
    content_category = await _resolve_content_category_for_thread(request.thread_id)
    narrations_map = await _build_scene_narrations_map(
        uuid_list,
        conversation_id=conv_id,
        thread_id=request.thread_id,
        content_category=content_category,
    )
    scene_responses = [
        SceneResponse(
            uuid=scene.uuid,
            scene_number=scene.scene_number,
            title=scene.title,
            description=scene.description,
            duration=scene.duration,
            run_id=scene.run_id,
            character_ids=scene.character_ids or [],
            narrations=narrations_map.get(scene.uuid, []),
        )
        for scene in scenes
    ]
    return ResponseModel.success(data=SceneListResponse(scenes=scene_responses))


@router.get("/storyboard-detail/{uuid}", response_model=ResponseModel[StoryboardDetailResponse])
async def get_storyboard_detail_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)  # ✅ 修复：使用 FastAPI 依赖注入
):
    """根据UUID获取详细分镜数据"""
    storyboard_detail = await get_storyboard_detail_by_uuid(uuid)
    if not storyboard_detail:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "Storyboard detail data not found"
        )
    
    # 检查权限
    if storyboard_detail.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access this storyboard detail data"
        )
    
    # 获取详细镜头
    detailed_shots = await get_detailed_shots_by_storyboard_id(storyboard_detail.uuid)
    
    # 构建详细镜头响应数据
    shot_responses = [
        DetailedShotResponse(
            uuid=shot.uuid,
            shot_number=shot.shot_number,
            duration=shot.duration,
            shot_type=shot.shot_type,
            camera_position=shot.camera_position,  # 新增：相机机位
            camera_angle=shot.camera_angle,  # 新增：相机角度
            subject_angle=shot.subject_angle,  # 新增：主体角度
            subject_pose=shot.subject_pose,  # 新增：主体姿势
            scene_description=shot.scene_description,
            camera_movement=shot.camera_movement,
            lighting=shot.lighting,
            visual_effects=shot.visual_effects,
            transition=shot.transition,
            dialogue=shot.dialogue,
            sound_effects=shot.sound_effects,
            is_bridge=shot.is_bridge,
            character_ids=shot.character_ids if shot.character_ids else [],
            scene_id=shot.scene_id,
            created_at=utc_isoformat(shot.created_at),
            updated_at=utc_isoformat(shot.updated_at)
        )
        for shot in detailed_shots
    ]
    
    # 构建响应数据
    response_data = StoryboardDetailResponse(
        total_duration=storyboard_detail.total_duration,
        visual_style=storyboard_detail.visual_style,
        shots_count=storyboard_detail.shots_count,
        shots=shot_responses,
        run_id=storyboard_detail.run_id,
        created_at=utc_isoformat(storyboard_detail.created_at),
        updated_at=utc_isoformat(storyboard_detail.updated_at)
    )
    
    return ResponseModel.success(data=response_data)
    # ✅ FastAPI 自动管理连接生命周期，不需要手动关闭


@router.get("/detailed-shots", response_model=ResponseModel[List[DetailedShotResponse]])
async def get_detailed_shots_data(
    uuids: str,  # 逗号分隔的UUID列表
    user_id: str = Security(auth_service.get_current_user_or_service_user)  # ✅ 修复：使用 FastAPI 依赖注入
):
    """根据UUID列表获取多个详细镜头数据"""
    uuid_list = [uuid.strip() for uuid in uuids.split(',') if uuid.strip()]
    if not uuid_list:
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER,
            "No valid UUIDs provided"
        )
    
    detailed_shots = await get_detailed_shots_by_uuids(uuid_list)
    if not detailed_shots:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "No detailed shot data found"
        )
    
    # 检查权限 - 确保所有详细镜头都属于当前用户
    for shot in detailed_shots:
        if shot.user_id != user_id:
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                f"No permission to access detailed shot {shot.shot_number}"
            )
    
    # 收集所有音频片段UUID
    all_audio_segment_uuids = []
    for shot in detailed_shots:
        if shot.audio_segment_ids:
            all_audio_segment_uuids.extend(shot.audio_segment_ids)
    
    # 去重并获取音频片段信息
    unique_audio_segment_uuids = list(set(all_audio_segment_uuids))
    audio_segments_data = await get_audio_segments_by_uuids(unique_audio_segment_uuids)
    
    # 创建音频片段UUID到音频片段信息的映射
    audio_segment_map = {seg.uuid: seg for seg in audio_segments_data}
    
    # 构建响应数据
    shot_responses = [
        DetailedShotResponse(
            shot_number=shot.shot_number,
            duration=shot.duration,
            shot_type=shot.shot_type,
            camera_movement=shot.camera_movement
        )
        for shot in detailed_shots
    ]
    
    return ResponseModel.success(data=shot_responses)
    # ✅ 修复：使用 Depends(get_async_db) 时，FastAPI 会自动管理连接的关闭，不需要手动关闭


@router.post("/keyframes", response_model=ResponseModel[KeyframeListResponse])
async def get_keyframes_data(
    request: KeyframesBatchRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    service: VideoDataService = Depends(get_video_data_service)
):
    """根据run_id获取关键帧数据（批量）"""
    import time
    import logging
    logger = logging.getLogger(__name__)
    
    # service已通过Depends注入
    t0 = time.perf_counter()
    
    # 使用run_id获取所有关键帧
    keyframes_from_db = await get_keyframes_by_run_id(request.run_id)
    t1 = time.perf_counter()
    logger.info(f"⏱️  [keyframes] Step1 获取keyframes: {(t1-t0)*1000:.1f}ms, 数量: {len(keyframes_from_db) if keyframes_from_db else 0}")
    if not keyframes_from_db:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            f"No keyframe data found for run_id: {request.run_id}"
        )
    
    uuid_list = [kf.uuid if hasattr(kf, 'uuid') else kf['uuid'] for kf in keyframes_from_db]
    if not uuid_list:
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER,
            "UUID list cannot be empty"
        )
    
    # 使用VideoDataService获取关键帧及版本数据
    keyframes_with_versions = await service.get_keyframes_with_versions(uuid_list, user_id)
    t2 = time.perf_counter()
    logger.info(f"⏱️  [keyframes] Step2 获取versions: {(t2-t1)*1000:.1f}ms")
    
    if not keyframes_with_versions:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "No keyframe data found"
        )
    
    # 收集所有 version uuid，批量拉取 reflection issues（该 version 对应的 video keyframe reflection 问题）
    all_version_uuids = []
    for item in keyframes_with_versions:
        for v in item.get("versions", []):
            vid = v.uuid if hasattr(v, "uuid") else v.get("uuid")
            if vid:
                all_version_uuids.append(vid)
    reflection_by_version = {}
    if all_version_uuids:
        reflection_results = await get_reflection_results_by_keyframe_version_ids(all_version_uuids)
        for rr in reflection_results:
            vid = rr.get("keyframe_version_id")
            if vid:
                reflection_by_version[vid] = rr
    
    t3 = time.perf_counter()
    # 构建响应数据
    keyframe_responses = []
    for item in keyframes_with_versions:
        keyframe = item["keyframe"]
        versions = item["versions"]
        
        # ⭐ 按 shot_number 和 version_number 排序（版本已经按照首帧/尾帧生成顺序）
        # frame_index 在 keyframe 层级，不在 version 层级
        versions = sorted(versions, key=lambda v: (
            v.shot_number,
            v.version_number
        ))
        
        version_responses = []
        for version in versions:
            # 从 additional_data 中提取 seed、aspect_ratio、resolution 和 model
            seed = None
            aspect_ratio = None
            resolution = None
            model = None
            # additional_data 已在 CRUD 层统一规范为 dict
            version_additional_data = version.additional_data
            if isinstance(version_additional_data, dict):
                seed = version_additional_data.get('seed')
                aspect_ratio = version_additional_data.get('aspect_ratio')
                resolution = version_additional_data.get('resolution')
                model = version_additional_data.get('model')
            
            version_uuid = version.uuid
            version_version_number = version.version_number
            version_shot_number = version.shot_number
            version_keyframe_url = version.keyframe_url
            version_t2i_prompt = version.t2i_prompt
            # reference_image_urls 已在 CRUD 层统一规范为 list
            version_reference_image_urls = version.reference_image_urls or []
            version_success = version.success
            version_error_msg = version.error_msg
            keyframe_frame_index = keyframe.frame_index
            
            reflection_issues = (reflection_by_version.get(version_uuid) or {}).get("issues") or []
            version_responses.append(KeyframeVersionResponse(
                uuid=version_uuid,
                version_number=version_version_number,
                shot_number=version_shot_number,
                keyframe_url=version_keyframe_url,
                t2i_prompt=version_t2i_prompt,
                reference_image_urls=version_reference_image_urls,
                success=version_success,
                error_msg=version_error_msg,
                frame_index=keyframe_frame_index,  # ⭐ 修复：从 keyframe 获取 frame_index
                reflection_issues=reflection_issues,
            ))
        
        # 每个 keyframe 只构建一个 KeyframeResponse（在 version 循环外）
        keyframe_uuid = keyframe.uuid
        keyframe_shot_number = keyframe.shot_number
        keyframe_reference_image_urls = keyframe.reference_image_urls or []
        keyframe_current_version_index = keyframe.current_version_index
        keyframe_run_id = keyframe.run_id
        
        keyframe_responses.append(
            KeyframeResponse(
                uuid=keyframe_uuid,
                shot_number=keyframe_shot_number,
                reference_image_urls=keyframe_reference_image_urls,
                current_version_index=keyframe_current_version_index,
                versions=version_responses,
                run_id=keyframe_run_id
            )
        )
    
    # ⭐ 关键修复：对 keyframes 数组排序 - 按 shot_number, 然后 frame_index (首帧0在前，尾帧-1在后)
    keyframe_responses = sorted(
        keyframe_responses, 
        key=lambda kf: (
            kf.shot_number, 
            0 if (kf.versions and kf.versions[0].frame_index == 0) else 1
            )
        )
    
    t4 = time.perf_counter()
    logger.info(f"⏱️  [keyframes] Step3 构建响应: {(t4-t3)*1000:.1f}ms, 记录数: {len(keyframe_responses)}")
    
    st, tk = _keyframe_list_shot_and_record_totals(
        shot_total_n=0,
        continuity_from_user=False,
        keyframe_responses=keyframe_responses,
    )
    result = KeyframeListResponse(
        keyframes=keyframe_responses,
        shot_total=st,
        total=tk,
    )
    
    t5 = time.perf_counter()
    logger.info(f"⏱️  [keyframes] 总耗时: {(t5-t0)*1000:.1f}ms (DB查询: {(t2-t0)*1000:.1f}ms, 数据处理: {(t4-t3)*1000:.1f}ms, 序列化: {(t5-t4)*1000:.1f}ms)")
    
    return ResponseModel.success(data=result)


@router.post("/keyframes-by-thread", response_model=ResponseModel[KeyframeListResponse])
async def get_keyframes_data_by_thread(
    request: ThreadScopedRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    service: VideoDataService = Depends(get_video_data_service)
):
    """根据 thread_id 获取该 thread 下所有关键帧（多 run 聚合）。无数据时返回空列表。"""
    conversation = await async_get_conversation_by_thread_id(request.thread_id)
    if not conversation or conversation.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权访问此 thread 或对话不存在"
        )
    conv_id = str(conversation.id)
    continuity_user = _parse_enable_continuity_mode_from_conversation_user_option(getattr(conversation, "user_option", None))
    # 先取分镜镜头数（与前端 scenes.length / 按镜占位一致）
    detailed_shots = await get_detailed_shots_by_conversation(conv_id, request.thread_id)
    expected_shot_total = len(detailed_shots) if detailed_shots else 0
    generation_mode_by_number = {s.shot_number: (getattr(s, "generation_mode", None) or "") for s in detailed_shots}
    keyframes_from_db = await get_keyframes_by_conversation(conv_id, request.thread_id)
    if not keyframes_from_db:
        st, tk = _keyframe_list_shot_and_record_totals(
            shot_total_n=expected_shot_total,
            continuity_from_user=continuity_user,
            keyframe_responses=[],
        )
        return ResponseModel.success(data=KeyframeListResponse(keyframes=[], shot_total=st, total=tk))
    uuid_list = [kf.uuid if hasattr(kf, "uuid") else kf["uuid"] for kf in keyframes_from_db]
    keyframes_with_versions = await service.get_keyframes_with_versions(uuid_list, user_id)
    if not keyframes_with_versions:
        st, tk = _keyframe_list_shot_and_record_totals(
            shot_total_n=expected_shot_total,
            continuity_from_user=continuity_user,
            keyframe_responses=[],
        )
        return ResponseModel.success(data=KeyframeListResponse(keyframes=[], shot_total=st, total=tk))
    all_version_uuids = []
    for item in keyframes_with_versions:
        for v in item.get("versions", []):
            vid = v.uuid if hasattr(v, "uuid") else v.get("uuid")
            if vid:
                all_version_uuids.append(vid)
    reflection_by_version = {}
    if all_version_uuids:
        reflection_results = await get_reflection_results_by_keyframe_version_ids(all_version_uuids)
        for rr in reflection_results:
            vid = rr.get("keyframe_version_id")
            if vid:
                reflection_by_version[vid] = rr
    keyframe_responses = []
    for item in keyframes_with_versions:
        keyframe = item["keyframe"]
        versions = item["versions"]
        versions = sorted(versions, key=lambda v: (v.shot_number, v.version_number))
        version_responses = []
        for version in versions:
            version_additional_data = version.additional_data
            seed = aspect_ratio = resolution = model = None
            if isinstance(version_additional_data, dict):
                seed = version_additional_data.get("seed")
                aspect_ratio = version_additional_data.get("aspect_ratio")
                resolution = version_additional_data.get("resolution")
                model = version_additional_data.get("model")
            version_uuid = version.uuid
            reflection_issues = (reflection_by_version.get(version_uuid) or {}).get("issues") or []
            version_responses.append(KeyframeVersionResponse(
                uuid=version_uuid,
                version_number=version.version_number,
                shot_number=version.shot_number,
                keyframe_url=version.keyframe_url,
                t2i_prompt=version.t2i_prompt,
                reference_image_urls=version.reference_image_urls or [],
                success=version.success,
                error_msg=version.error_msg,
                frame_index=keyframe.frame_index,
                reflection_issues=reflection_issues,
            ))
        generation_mode = generation_mode_by_number.get(keyframe.shot_number)
        keyframe_responses.append(
            KeyframeResponse(
                uuid=keyframe.uuid,
                shot_number=keyframe.shot_number,
                reference_image_urls=keyframe.reference_image_urls or [],
                current_version_index=keyframe.current_version_index,
                versions=version_responses,
                run_id=keyframe.run_id,
                generation_mode=generation_mode,
            )
        )
    keyframe_responses = sorted(
        keyframe_responses,
        key=lambda kf: (kf.shot_number, 0 if (kf.versions and kf.versions[0].frame_index == 0) else 1)
    )
    st, tk = _keyframe_list_shot_and_record_totals(
        shot_total_n=expected_shot_total,
        continuity_from_user=continuity_user,
        keyframe_responses=keyframe_responses,
    )
    return ResponseModel.success(data=KeyframeListResponse(keyframes=keyframe_responses, shot_total=st, total=tk))


@router.get("/music-generation/{uuid}", response_model=ResponseModel[MusicGenerationResponse])
async def get_music_generation_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据UUID获取音乐生成数据"""
    # 获取音乐生成数据
    music_generation = await get_music_generation_by_uuid(uuid)
    if not music_generation:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "Music generation data not found"
        )
        
    # 检查权限
    if music_generation.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access this music generation"
        )
        
    # 获取版本数据
    versions = await get_music_generation_versions_by_music_generation_ids([music_generation.uuid])
    versions_sorted = sorted(versions, key=lambda v: getattr(v, "version_number", 0))
        
    # 构建版本响应数据
    version_responses = []
    for version in versions_sorted:
        version_responses.append(MusicGenerationVersionResponse(
            uuid=version.uuid,
            music_prompt=version.music_prompt,
            music_url=(getattr(version, "music_url", None) or "")
        ))
        
    # 当前版本的完整音乐 URL
    current_version = versions_sorted[music_generation.current_version_index] if 0 <= music_generation.current_version_index < len(versions_sorted) else None
    music_url = (getattr(current_version, "music_url", None) or "") if current_version else ""
        
    # 构建响应数据
    result = MusicGenerationResponse(
        uuid=music_generation.uuid,
        is_instrumental=music_generation.is_instrumental,
        current_version_index=music_generation.current_version_index,
        music_url=music_url,
        versions=version_responses
    )
        
    return ResponseModel.success(data=result)

@router.get("/audio-effects", response_model=ResponseModel[AudioEffectListResponse])
async def get_audio_effects_data(
    conversation_id: str,
    thread_id: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据对话ID和线程ID获取音效数据"""
    # 获取音效数据
    audio_effects = await get_audio_effects_by_conversation(conversation_id, thread_id)
    if not audio_effects:
        return ResponseModel.success(data=AudioEffectListResponse(audio_effects=[]))
        
    # 检查权限（检查第一个音效的权限）
    if audio_effects[0].user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access these audio effects"
        )
        
    # 获取所有音效的版本数据
    audio_effect_ids = [audio_effect.uuid for audio_effect in audio_effects]
    audio_effect_versions = await get_audio_effect_versions_by_audio_effect_ids(audio_effect_ids)
        
    # 按音效ID分组版本数据
    versions_by_audio_effect = {}
    for version in audio_effect_versions:
        if version.audio_effect_id not in versions_by_audio_effect:
            versions_by_audio_effect[version.audio_effect_id] = []
        versions_by_audio_effect[version.audio_effect_id].append(version)
        
    # 构建响应数据
    audio_effect_responses = []
    for audio_effect in audio_effects:
        # 获取该音效的所有版本
        versions = versions_by_audio_effect.get(audio_effect.uuid, [])
        version_responses = []
            
        for version in versions:
            version_responses.append(
                AudioEffectVersionResponse(
                    audio_prompt=version.audio_prompt,
                    audio_url=version.audio_url
                )
            )
            
        audio_effect_responses.append(
            AudioEffectResponse(
                shot_number=audio_effect.shot_number,
                current_version_index=audio_effect.current_version_index,
                versions=version_responses
            )
        )
        
    result = AudioEffectListResponse(
        audio_effects=audio_effect_responses
    )
        
    return ResponseModel.success(data=result)

@router.get("/audio-effect/{uuid}", response_model=ResponseModel[AudioEffectResponse])
async def get_audio_effect_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据UUID获取单个音效数据"""
    # 获取音效数据
    audio_effect = await get_audio_effect_by_uuid(uuid)
    if not audio_effect:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "Audio effect not found"
        )
        
    # 检查权限
    if audio_effect.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access this audio effect"
        )
        
    # 获取音效版本数据
    audio_effect_versions = await get_audio_effect_versions_by_audio_effect_ids([audio_effect.uuid])
        
    # 构建版本响应数据
    version_responses = []
    for version in audio_effect_versions:
        version_responses.append(
            AudioEffectVersionResponse(
                uuid=version.uuid,
                version_number=version.version_number,
                shot_number=version.shot_number,
                video_url=version.video_url,
                audio_prompt=version.audio_prompt,
                enhanced_prompt=version.enhanced_prompt,
                audio_url=version.audio_url,
                provider=version.provider,
                duration=version.duration,
                params=version.params,
                is_bridge=version.is_bridge,
                success=version.success,
                error_msg=version.error_msg,
                created_at=utc_isoformat(version.created_at),
                updated_at=utc_isoformat(version.updated_at)
            )
        )
        
    # 构建响应数据
    result = AudioEffectResponse(
        shot_number=audio_effect.shot_number,
        current_version_index=audio_effect.current_version_index,
        versions=version_responses
    )
        
    return ResponseModel.success(data=result)

from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.encoders import jsonable_encoder
# ...
@router.post("/video-generations") # 🚀 使用纯 asyncpg，绕过 SQLAlchemy
async def get_video_generations_data(
    request: VideoGenerationsBatchRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """根据run_id获取视频生成数据（批量）- 使用纯asyncpg高性能查询"""
    from ...models.database import get_asyncpg_pool
    from fastapi.responses import JSONResponse
    
    t0 = time.perf_counter()
    pool = get_asyncpg_pool()
    
    async with pool.acquire() as conn:
        t_acquire = time.perf_counter()
        
        # Step 1: 查询视频生成记录（含 detailed_shot_id 用于取镜头设计时长）
        sql1 = """
            SELECT id, uuid, shot_number, keyframe_id, run_id, current_version_index,
                   created_at, updated_at, user_id, detailed_shot_id
            FROM video_generations
            WHERE run_id = $1
            ORDER BY shot_number
        """
        t1_start = time.perf_counter()
        rows1 = await conn.fetch(sql1, request.run_id)
        t1_end = time.perf_counter()
        
        if not rows1:
            t_total = time.perf_counter()
            logger.info(f"⏱️  [video-generations] 总耗时: {(t_total-t0)*1000:.1f}ms (无数据)")
            return JSONResponse(content={
                "code": 0,
                "message": "success",
                "data": {"video_generations": [], "total": 0}
            })
        
        # 提取UUID列表 (rows1是asyncpg.Record，用dict访问)
        uuid_list = [row['uuid'] for row in rows1]
        shot_uuids = [row['detailed_shot_id'] for row in rows1 if row.get('detailed_shot_id')]
        
        # Step 2: 查询版本数据
        sql2 = """
            SELECT uuid, version_number, shot_number, video_url, success, 
                   error_msg, keyframe_url, motion_prompt, duration, video_generation_id,
                   additional_data, audio_url
            FROM video_generation_versions
            WHERE video_generation_id = ANY($1::text[])
            ORDER BY version_number
        """
        t2_start = time.perf_counter()
        rows2 = await conn.fetch(sql2, uuid_list)
        t2_end = time.perf_counter()
        
        t_query_done = time.perf_counter()
        
        # 镜头设计时长（与成片时间轴一致），用于覆盖 version 的整数 duration
        shot_duration_by_uuid = {}
        shot_character_ids_by_uuid: Dict[str, List[str]] = {}
        if shot_uuids:
            detailed_shots = await get_detailed_shots_by_uuids(shot_uuids)
            shot_duration_by_uuid = {s.uuid: s.duration for s in detailed_shots}
            shot_character_ids_by_uuid = {
                s.uuid: (getattr(s, "character_ids", None) or []) for s in detailed_shots
            }
        
        # 组织数据：按 video_generation_id 分组版本
        versions_map = {}
        for row in rows2:
            vid = row['video_generation_id']
            if vid not in versions_map:
                versions_map[vid] = []
            versions_map[vid].append(row)
        
        # 构建响应
        video_generation_responses = []
        for gen_row in rows1:
            # 权限检查
            if gen_row['user_id'] != user_id:
                continue
            
            versions = versions_map.get(gen_row['uuid'], [])
            detailed_shot_id = gen_row.get('detailed_shot_id')
            shot_duration = shot_duration_by_uuid.get(detailed_shot_id) if detailed_shot_id else None
            character_ids = shot_character_ids_by_uuid.get(detailed_shot_id, []) if detailed_shot_id else []
            
            version_responses = []
            for ver_row in versions:
                dur = shot_duration
                if dur is None:
                    dur = ver_row.get('duration')
                if dur is not None:
                    dur = float(dur)
                # 只返回生成时实际写入的参考图；禁止用 shot deps 回填（会伪装成喂过图）
                ref_urls = _reference_image_urls_from_version(ver_row)
                version_responses.append({
                    "uuid": ver_row['uuid'],
                    "version_number": ver_row['version_number'],
                    "shot_number": ver_row['shot_number'],
                    "video_url": ver_row['video_url'],
                    "success": ver_row['success'],
                    "error_msg": ver_row['error_msg'],
                    "keyframe_url": ver_row['keyframe_url'],
                    "motion_prompt": ver_row['motion_prompt'],
                    "duration": dur,
                    "preview_video_url": _preview_video_url_from_version(ver_row),
                    "audio_url": ver_row.get('audio_url'),
                    "reference_image_urls": ref_urls,
                })
            
            current_keyframe_url = None
            current_version_index = gen_row['current_version_index']
            if versions and 0 <= current_version_index < len(versions):
                current_keyframe_url = versions[current_version_index]['keyframe_url']
            elif versions:
                current_keyframe_url = versions[0]['keyframe_url']
            
            video_generation_responses.append({
                "uuid": gen_row['uuid'],
                "shot_number": gen_row['shot_number'],
                "keyframe_url": current_keyframe_url,
                "current_version_index": current_version_index,
                "versions": version_responses,
                "keyframe_id": gen_row['keyframe_id'],
                "run_id": gen_row['run_id'],
                "character_ids": character_ids,
            })
        
        t_build_done = time.perf_counter()
        _vg_total = len({g["shot_number"] for g in video_generation_responses}) if video_generation_responses else 0
        result = {"video_generations": video_generation_responses, "total": _vg_total}
        
        t_final = time.perf_counter()
        logger.info(
            f"⏱️  [video-generations] 总耗时: {(t_final-t0)*1000:.1f}ms "
            f"(获取连接: {(t_acquire-t0)*1000:.1f}ms, "
            f"Step1查询: {(t1_end-t1_start)*1000:.1f}ms, "
            f"Step2查询: {(t2_end-t2_start)*1000:.1f}ms, "
            f"数据处理: {(t_build_done-t_query_done)*1000:.1f}ms, "
            f"序列化: {(t_final-t_build_done)*1000:.1f}ms)"
        )
        
        return JSONResponse(content={
            "code": 0,
            "message": "success",
            "data": result
        })


@router.post("/video-generations-by-thread")
async def get_video_generations_data_by_thread(
    request: ThreadScopedRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """根据 thread_id 获取该 thread 下所有视频生成数据（多 run 聚合）。无数据时返回空列表。"""
    from fastapi.responses import JSONResponse
    conversation = await async_get_conversation_by_thread_id(request.thread_id)
    if not conversation or conversation.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权访问此 thread 或对话不存在"
        )
    conv_id = str(conversation.id)
    detailed_shots_vg = await get_detailed_shots_by_conversation(conv_id, request.thread_id)
    expected_shot_total_vg = len(detailed_shots_vg) if detailed_shots_vg else 0
    generations_from_db = await get_video_generations_by_conversation(conv_id, request.thread_id)
    if not generations_from_db:
        return JSONResponse(content={
            "code": 0,
            "message": "success",
            "data": {"video_generations": [], "total": expected_shot_total_vg}
        })
    uuid_list = [g.uuid for g in generations_from_db]
    all_versions = await get_video_generation_versions_by_video_generation_ids(uuid_list)
    versions_map = {}
    for v in all_versions:
        vid = getattr(v, "video_generation_id", None) or (v.get("video_generation_id") if isinstance(v, dict) else None)
        if vid:
            if vid not in versions_map:
                versions_map[vid] = []
            versions_map[vid].append(v)
    shot_uuids = [g.detailed_shot_id for g in generations_from_db if getattr(g, "detailed_shot_id", None)]
    detailed_shots = await get_detailed_shots_by_uuids(shot_uuids) if shot_uuids else []
    shot_duration_by_uuid = {s.uuid: s.duration for s in detailed_shots}
    generation_mode_by_uuid = {s.uuid: (getattr(s, "generation_mode", None) or "") for s in detailed_shots}
    shot_character_ids_by_uuid = {
        s.uuid: (getattr(s, "character_ids", None) or []) for s in detailed_shots
    }
    video_generation_responses = []
    for gen in generations_from_db:
        if getattr(gen, "user_id", None) != user_id:
            continue
        versions = versions_map.get(gen.uuid, [])
        detailed_shot_id = getattr(gen, "detailed_shot_id", None)
        shot_duration = shot_duration_by_uuid.get(detailed_shot_id) if detailed_shot_id else None
        generation_mode = generation_mode_by_uuid.get(detailed_shot_id, "") if detailed_shot_id else ""
        character_ids = shot_character_ids_by_uuid.get(detailed_shot_id, []) if detailed_shot_id else []
        version_responses = []
        for ver in versions:
            dur = shot_duration
            if dur is None:
                dur = getattr(ver, "duration", None) or (ver.get("duration") if isinstance(ver, dict) else None)
            if dur is not None:
                dur = float(dur)
            # 只返回生成时实际写入的参考图；禁止用 shot deps 回填（会伪装成喂过图）
            ref_urls = _reference_image_urls_from_version(ver)
            version_responses.append({
                "uuid": getattr(ver, "uuid", None) or (ver.get("uuid") if isinstance(ver, dict) else None),
                "version_number": getattr(ver, "version_number", 0),
                "shot_number": getattr(ver, "shot_number", 0),
                "video_url": getattr(ver, "video_url", "") or "",
                "success": getattr(ver, "success", False),
                "error_msg": getattr(ver, "error_msg", None),
                "keyframe_url": getattr(ver, "keyframe_url", "") or "",
                "motion_prompt": getattr(ver, "motion_prompt", "") or "",
                "duration": dur,
                "preview_video_url": _preview_video_url_from_version(ver),
                "audio_url": getattr(ver, "audio_url", None) or (ver.get("audio_url") if isinstance(ver, dict) else None),
                "reference_image_urls": ref_urls,
            })
        current_version_index = getattr(gen, "current_version_index", 0) or 0
        current_keyframe_url = None
        if versions and 0 <= current_version_index < len(versions):
            v0 = versions[current_version_index]
            current_keyframe_url = getattr(v0, "keyframe_url", None) or (v0.get("keyframe_url") if isinstance(v0, dict) else None)
        elif versions:
            v0 = versions[0]
            current_keyframe_url = getattr(v0, "keyframe_url", None) or (v0.get("keyframe_url") if isinstance(v0, dict) else None)
        video_generation_responses.append({
            "uuid": gen.uuid,
            "shot_number": gen.shot_number,
            "keyframe_url": current_keyframe_url,
            "current_version_index": current_version_index,
            "versions": version_responses,
            "keyframe_id": gen.keyframe_id,
            "run_id": gen.run_id,
            "generation_mode": generation_mode,
            "character_ids": character_ids,
        })
    video_generation_responses.sort(key=lambda x: x["shot_number"])
    total_slots_vg = expected_shot_total_vg if expected_shot_total_vg > 0 else len(video_generation_responses)
    return JSONResponse(content={
        "code": 0,
        "message": "success",
        "data": {"video_generations": video_generation_responses, "total": total_slots_vg}
    })


@router.get("/video-assembly/thread/{thread_id}", response_model=ResponseModel[Optional[VideoAssemblyResponse]])
async def get_video_assembly_by_thread(
    thread_id: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据 thread_id 获取最新成功的视频合成数据"""
    try:
        from ...crud.video.video_other import get_video_assemblies_by_thread_id
        assemblies = await get_video_assemblies_by_thread_id(thread_id)
        successful = [a for a in assemblies if a.success]
        if not successful:
            return ResponseModel.success(data=None)
        video_assembly = successful[0]
        if video_assembly.user_id != user_id:
            raise BusinessException(BusinessExceptionCode.PERMISSION_DENIED, "No permission")
        result = VideoAssemblyResponse(
            uuid=video_assembly.uuid,
            final_video_url=video_assembly.final_video_url,
            final_video_url_no_subtitle=video_assembly.final_video_url_no_subtitle,
            total_duration=video_assembly.total_duration,
            success=video_assembly.success,
            error_msg=video_assembly.error_msg,
            assembly_mode=video_assembly.assembly_mode,
            story_outline_id=video_assembly.story_outline_id,
        )
        return ResponseModel.success(data=result)
    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"获取 thread 视频合成数据失败: {e}", exc_info=True)
        raise BusinessException(BusinessExceptionCode.INTERNAL_SERVER_ERROR, f"获取视频合成数据失败: {str(e)}")


@router.get("/video-assembly/{uuid}", response_model=ResponseModel[VideoAssemblyResponse])
async def get_video_assembly_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据UUID获取视频合成数据"""
    try:
        # 获取视频合成数据
        video_assembly = await get_video_assembly_by_uuid(uuid)
        if not video_assembly:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "Video assembly data not found"
            )
        
        # 检查权限
        assembly_user_id = video_assembly.user_id
        if assembly_user_id != user_id:
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                "No permission to access this video assembly"
            )
        
        # 构建响应数据
        result = VideoAssemblyResponse(
            uuid=video_assembly.uuid,
            final_video_url=video_assembly.final_video_url,
            final_video_url_no_subtitle=video_assembly.final_video_url_no_subtitle,
            total_duration=video_assembly.total_duration,
            success=video_assembly.success,
            error_msg=video_assembly.error_msg,
            assembly_mode=video_assembly.assembly_mode,
            story_outline_id=video_assembly.story_outline_id
        )
        
        return ResponseModel.success(data=result)
    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"获取视频合成数据失败: {e}", exc_info=True)
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            f"获取视频合成数据失败: {str(e)}"
        )


@router.get("/audio-effects", response_model=ResponseModel[AudioEffectListResponse])
async def get_audio_effects_data(
    conversation_id: str,
    thread_id: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据对话ID和线程ID获取音效数据"""
    # 获取音效数据
    audio_effects = await get_audio_effects_by_conversation(conversation_id, thread_id)
    if not audio_effects:
        return ResponseModel.success(data=AudioEffectListResponse(audio_effects=[]))
        
    # 检查权限（检查第一个音效的权限）
    if audio_effects[0].user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access these audio effects"
        )
        
    # 获取所有音效的版本数据
    audio_effect_ids = [audio_effect.uuid for audio_effect in audio_effects]
    audio_effect_versions = await get_audio_effect_versions_by_audio_effect_ids(audio_effect_ids)
        
    # 按音效ID分组版本数据
    versions_by_audio_effect = {}
    for version in audio_effect_versions:
        if version.audio_effect_id not in versions_by_audio_effect:
            versions_by_audio_effect[version.audio_effect_id] = []
        versions_by_audio_effect[version.audio_effect_id].append(version)
        
    # 构建响应数据
    audio_effect_responses = []
    for audio_effect in audio_effects:
        # 获取该音效的所有版本
        versions = versions_by_audio_effect.get(audio_effect.uuid, [])
        version_responses = []
            
        for version in versions:
            version_responses.append(
                AudioEffectVersionResponse(
                    audio_prompt=version.audio_prompt,
                    audio_url=version.audio_url
                )
            )
            
        audio_effect_responses.append(
            AudioEffectResponse(
                shot_number=audio_effect.shot_number,
                current_version_index=audio_effect.current_version_index,
                versions=version_responses
            )
        )
        
    result = AudioEffectListResponse(
        audio_effects=audio_effect_responses
    )
        
    return ResponseModel.success(data=result)

@router.get("/audio-effect/{uuid}", response_model=ResponseModel[AudioEffectResponse])
async def get_audio_effect_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据UUID获取单个音效数据"""
    # 获取音效数据
    audio_effect = await get_audio_effect_by_uuid(uuid)
    if not audio_effect:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "Audio effect not found"
        )
        
    # 检查权限
    if audio_effect.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access this audio effect"
        )
        
    # 获取音效版本数据
    audio_effect_versions = await get_audio_effect_versions_by_audio_effect_ids([audio_effect.uuid])
        
    # 构建版本响应数据
    version_responses = []
    for version in audio_effect_versions:
        version_responses.append(
            AudioEffectVersionResponse(
                uuid=version.uuid,
                version_number=version.version_number,
                shot_number=version.shot_number,
                video_url=version.video_url,
                audio_prompt=version.audio_prompt,
                enhanced_prompt=version.enhanced_prompt,
                audio_url=version.audio_url,
                provider=version.provider,
                duration=version.duration,
                params=version.params,
                is_bridge=version.is_bridge,
                success=version.success,
                error_msg=version.error_msg,
                created_at=utc_isoformat(version.created_at),
                updated_at=utc_isoformat(version.updated_at)
            )
        )
        
    # 构建响应数据
    result = AudioEffectResponse(
        shot_number=audio_effect.shot_number,
        current_version_index=audio_effect.current_version_index,
        versions=version_responses
    )
        
    return ResponseModel.success(data=result)

@router.get("/narrations", response_model=ResponseModel[NarrationListResponse])
async def get_narrations_data(
    conversation_id: str,
    thread_id: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据对话ID和线程ID获取旁白数据"""
    # 获取旁白数据
    narrations = await get_narrations_by_conversation(conversation_id, thread_id)
    if not narrations:
        return ResponseModel.success(data=NarrationListResponse(narrations=[]))
        
    # 检查权限（检查第一个旁白的权限）
    if narrations[0].user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access these narrations"
        )
        
    # 获取所有旁白的版本数据
    narration_ids = [narration.uuid for narration in narrations]
    narration_versions = await get_narration_versions_by_narration_ids(narration_ids)
        
    # 按旁白ID分组版本数据
    versions_by_narration = {}
    for version in narration_versions:
        if version.narration_id not in versions_by_narration:
            versions_by_narration[version.narration_id] = []
        versions_by_narration[version.narration_id].append(version)
        
    # 构建响应数据
    narration_responses = []
    for narration in narrations:
        # 获取该旁白的所有版本
        versions = versions_by_narration.get(narration.uuid, [])
        version_responses = []
            
        for version in versions:
            # 从params字典中获取voice_id和emotion
            params = version.params or {}
            version_responses.append(
                NarrationVersionResponse(
                    narration_text=version.narration_text,
                    audio_url=version.audio_url
                )
            )
            
        narration_responses.append(
            NarrationResponse(
                shot_number=narration.shot_number,
                current_version_index=narration.current_version_index,
                versions=version_responses
            )
        )
        
    result = NarrationListResponse(
        narrations=narration_responses
    )
        
    return ResponseModel.success(data=result)

@router.get("/narrations/by-uuids", response_model=ResponseModel[NarrationListResponse])
async def get_narrations_by_uuids_data(
    uuids: str,  # 逗号分隔的UUID列表
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据UUID列表批量获取旁白数据"""
    uuid_list = [uuid.strip() for uuid in uuids.split(',') if uuid.strip()]
    if not uuid_list:
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER,
            "No valid UUIDs provided"
        )
        
    # 获取旁白数据
    narrations = await get_narrations_by_uuids(uuid_list)
    if not narrations:
        return ResponseModel.success(data=NarrationListResponse(narrations=[]))
        
    # 检查权限（检查第一个旁白的权限）
    if narrations[0].user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access these narrations"
        )
        
    # 获取所有旁白的版本数据
    narration_ids = [narration.uuid for narration in narrations]
    narration_versions = await get_narration_versions_by_narration_ids(narration_ids)
        
    # 按旁白ID分组版本数据
    versions_by_narration = {}
    for version in narration_versions:
        if version.narration_id not in versions_by_narration:
            versions_by_narration[version.narration_id] = []
        versions_by_narration[version.narration_id].append(version)
        
    # 构建响应数据
    narration_responses = []
    for narration in narrations:
        # 获取该旁白的所有版本
        versions = versions_by_narration.get(narration.uuid, [])
        version_responses = []
            
        for version in versions:
            # 从params字典中获取voice_id和emotion
            params = version.params or {}
            version_responses.append(
                NarrationVersionResponse(
                    narration_text=version.narration_text,
                    audio_url=version.audio_url
                )
            )
            
        narration_responses.append(
            NarrationResponse(
                shot_number=narration.shot_number,
                current_version_index=narration.current_version_index,
                versions=version_responses
            )
        )
        
    result = NarrationListResponse(
        narrations=narration_responses
    )
        
    return ResponseModel.success(data=result)

@router.get("/audio-effects", response_model=ResponseModel[AudioEffectListResponse])
async def get_audio_effects_data(
    conversation_id: str,
    thread_id: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据对话ID和线程ID获取音效数据"""
    # 获取音效数据
    audio_effects = await get_audio_effects_by_conversation(conversation_id, thread_id)
    if not audio_effects:
        return ResponseModel.success(data=AudioEffectListResponse(audio_effects=[]))
        
    # 检查权限（检查第一个音效的权限）
    if audio_effects[0].user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access these audio effects"
        )
        
    # 获取所有音效的版本数据
    audio_effect_ids = [audio_effect.uuid for audio_effect in audio_effects]
    audio_effect_versions = await get_audio_effect_versions_by_audio_effect_ids(audio_effect_ids)
        
    # 按音效ID分组版本数据
    versions_by_audio_effect = {}
    for version in audio_effect_versions:
        if version.audio_effect_id not in versions_by_audio_effect:
            versions_by_audio_effect[version.audio_effect_id] = []
        versions_by_audio_effect[version.audio_effect_id].append(version)
        
    # 构建响应数据
    audio_effect_responses = []
    for audio_effect in audio_effects:
        # 获取该音效的所有版本
        versions = versions_by_audio_effect.get(audio_effect.uuid, [])
        version_responses = []
            
        for version in versions:
            version_responses.append(
                AudioEffectVersionResponse(
                    audio_prompt=version.audio_prompt,
                    audio_url=version.audio_url
                )
            )
            
        audio_effect_responses.append(
            AudioEffectResponse(
                shot_number=audio_effect.shot_number,
                current_version_index=audio_effect.current_version_index,
                versions=version_responses
            )
        )
        
    result = AudioEffectListResponse(
        audio_effects=audio_effect_responses
    )
        
    return ResponseModel.success(data=result)

@router.get("/audio-effect/{uuid}", response_model=ResponseModel[AudioEffectResponse])
async def get_audio_effect_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据UUID获取单个音效数据"""
    # 获取音效数据
    audio_effect = await get_audio_effect_by_uuid(uuid)
    if not audio_effect:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "Audio effect not found"
        )
        
    # 检查权限
    if audio_effect.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access this audio effect"
        )
        
    # 获取音效版本数据
    audio_effect_versions = await get_audio_effect_versions_by_audio_effect_ids([audio_effect.uuid])
        
    # 构建版本响应数据
    version_responses = []
    for version in audio_effect_versions:
        version_responses.append(
            AudioEffectVersionResponse(
                uuid=version.uuid,
                version_number=version.version_number,
                shot_number=version.shot_number,
                video_url=version.video_url,
                audio_prompt=version.audio_prompt,
                enhanced_prompt=version.enhanced_prompt,
                audio_url=version.audio_url,
                provider=version.provider,
                duration=version.duration,
                params=version.params,
                is_bridge=version.is_bridge,
                success=version.success,
                error_msg=version.error_msg,
                created_at=utc_isoformat(version.created_at),
                updated_at=utc_isoformat(version.updated_at)
            )
        )
        
    # 构建响应数据
    result = AudioEffectResponse(
        shot_number=audio_effect.shot_number,
        current_version_index=audio_effect.current_version_index,
        versions=version_responses
    )
        
    return ResponseModel.success(data=result)

@router.get("/audio-effects/by-uuids", response_model=ResponseModel[AudioEffectListResponse])
async def get_audio_effects_by_uuids_data(
    uuids: str,  # 逗号分隔的UUID列表
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据UUID列表批量获取音效数据"""
    uuid_list = [uuid.strip() for uuid in uuids.split(',') if uuid.strip()]
    if not uuid_list:
        raise BusinessException(
            BusinessExceptionCode.INVALID_PARAMETER,
            "No valid UUIDs provided"
        )
        
    # 获取音效数据
    audio_effects = await get_audio_effects_by_uuids(uuid_list)
    if not audio_effects:
        return ResponseModel.success(data=AudioEffectListResponse(audio_effects=[]))
        
    # 检查权限（检查第一个音效的权限）
    if audio_effects[0].user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access these audio effects"
        )
        
    # 获取所有音效的版本数据
    audio_effect_ids = [audio_effect.uuid for audio_effect in audio_effects]
    audio_effect_versions = await get_audio_effect_versions_by_audio_effect_ids(audio_effect_ids)
        
    # 按音效ID分组版本数据
    versions_by_audio_effect = {}
    for version in audio_effect_versions:
        if version.audio_effect_id not in versions_by_audio_effect:
            versions_by_audio_effect[version.audio_effect_id] = []
        versions_by_audio_effect[version.audio_effect_id].append(version)
        
    # 构建响应数据
    audio_effect_responses = []
    for audio_effect in audio_effects:
        # 获取该音效的所有版本
        versions = versions_by_audio_effect.get(audio_effect.uuid, [])
        version_responses = []
            
        for version in versions:
            version_responses.append(
                AudioEffectVersionResponse(
                    audio_prompt=version.audio_prompt,
                    audio_url=version.audio_url
                )
            )
            
        audio_effect_responses.append(
            AudioEffectResponse(
                shot_number=audio_effect.shot_number,
                current_version_index=audio_effect.current_version_index,
                versions=version_responses
            )
        )
        
    result = AudioEffectListResponse(
        audio_effects=audio_effect_responses
    )
        
    return ResponseModel.success(data=result)

@router.get("/narration/{uuid}", response_model=ResponseModel[NarrationResponse])
async def get_narration_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据UUID获取单个旁白数据"""
    # 获取旁白数据
    narration = await get_narration_by_uuid(uuid)
    if not narration:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "Narration not found"
        )
        
    # 检查权限
    if narration.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access this narration"
        )
        
    # 获取旁白版本数据
    narration_versions = await get_narration_versions_by_narration_ids([narration.uuid])
        
    # 构建版本响应数据
    version_responses = []
    for version in narration_versions:
        # 从params字典中获取voice_id和emotion
        params = version.params or {}
        version_responses.append(
            NarrationVersionResponse(
                uuid=version.uuid,
                version_number=version.version_number,
                shot_number=version.shot_number,
                narration_text=version.narration_text,
                enhanced_prompt=version.enhanced_prompt,
                audio_url=version.audio_url,
                provider=version.provider,
                voice_id=params.get("voice_id", ""),
                emotion=params.get("emotion", ""),
                duration=version.duration,
                is_bridge=version.is_bridge,
                success=version.success,
                error_msg=version.error_msg,
                created_at=utc_isoformat(version.created_at),
                updated_at=utc_isoformat(version.updated_at)
            )
        )
        
    # 构建响应数据
    result = NarrationResponse(
        shot_number=narration.shot_number,
        current_version_index=narration.current_version_index,
        versions=version_responses
    )
        
    return ResponseModel.success(data=result)

@router.get("/audio-effects", response_model=ResponseModel[AudioEffectListResponse])
async def get_audio_effects_data(
    conversation_id: str,
    thread_id: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据对话ID和线程ID获取音效数据"""
    # 获取音效数据
    audio_effects = await get_audio_effects_by_conversation(conversation_id, thread_id)
    if not audio_effects:
        return ResponseModel.success(data=AudioEffectListResponse(audio_effects=[]))
        
    # 检查权限（检查第一个音效的权限）
    if audio_effects[0].user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access these audio effects"
        )
        
    # 获取所有音效的版本数据
    audio_effect_ids = [audio_effect.uuid for audio_effect in audio_effects]
    audio_effect_versions = await get_audio_effect_versions_by_audio_effect_ids(audio_effect_ids)
        
    # 按音效ID分组版本数据
    versions_by_audio_effect = {}
    for version in audio_effect_versions:
        if version.audio_effect_id not in versions_by_audio_effect:
            versions_by_audio_effect[version.audio_effect_id] = []
        versions_by_audio_effect[version.audio_effect_id].append(version)
        
    # 构建响应数据
    audio_effect_responses = []
    for audio_effect in audio_effects:
        # 获取该音效的所有版本
        versions = versions_by_audio_effect.get(audio_effect.uuid, [])
        version_responses = []
            
        for version in versions:
            version_responses.append(
                AudioEffectVersionResponse(
                    audio_prompt=version.audio_prompt,
                    audio_url=version.audio_url
                )
            )
            
        audio_effect_responses.append(
            AudioEffectResponse(
                shot_number=audio_effect.shot_number,
                current_version_index=audio_effect.current_version_index,
                versions=version_responses
            )
        )
        
    result = AudioEffectListResponse(
        audio_effects=audio_effect_responses
    )
        
    return ResponseModel.success(data=result)

@router.get("/audio-effect/{uuid}", response_model=ResponseModel[AudioEffectResponse])
async def get_audio_effect_data(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据UUID获取单个音效数据"""
    # 获取音效数据
    audio_effect = await get_audio_effect_by_uuid(uuid)
    if not audio_effect:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "Audio effect not found"
        )
        
    # 检查权限
    if audio_effect.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "No permission to access this audio effect"
        )
        
    # 获取音效版本数据
    audio_effect_versions = await get_audio_effect_versions_by_audio_effect_ids([audio_effect.uuid])
        
    # 构建版本响应数据
    version_responses = []
    for version in audio_effect_versions:
        version_responses.append(
            AudioEffectVersionResponse(
                uuid=version.uuid,
                version_number=version.version_number,
                shot_number=version.shot_number,
                video_url=version.video_url,
                audio_prompt=version.audio_prompt,
                enhanced_prompt=version.enhanced_prompt,
                audio_url=version.audio_url,
                provider=version.provider,
                duration=version.duration,
                params=version.params,
                is_bridge=version.is_bridge,
                success=version.success,
                error_msg=version.error_msg,
                created_at=utc_isoformat(version.created_at),
                updated_at=utc_isoformat(version.updated_at)
            )
        )
        
    # 构建响应数据
    result = AudioEffectResponse(
        shot_number=audio_effect.shot_number,
        current_version_index=audio_effect.current_version_index,
        versions=version_responses
    )
        
    return ResponseModel.success(data=result)
# ==================== 下载相关响应模型 ====================

class DownloadResponse(BaseModel):
    """下载响应模型"""
    download_url: str
    filename: str
    file_size: Optional[int] = None
    created_at: str


# ==================== 下载接口 ====================

@router.get("/video-assembly/{uuid}/download")
async def download_video_resources(
    uuid: str,
    background_tasks: BackgroundTasks,
    resource_type: str = Query("complete", description="资源类型: complete(完整资源包) 或 current(当前视频资源)"),
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """下载视频资源包（ZIP格式） - 直接返回文件，并在发送后自动清理临时文件
    
    Args:
        uuid: 视频合成UUID
        resource_type: 资源类型 - "complete"(完整资源包) 或 "current"(当前视频相关资源)
    """
    zip_file_path = None
    temp_dir = None
    
    try:
        from ...services.download_service import download_service
        
        # 获取视频合成记录
        video_assembly = await get_video_assembly_by_uuid(uuid)
        if not video_assembly:
            raise HTTPException(status_code=404, detail="视频合成记录不存在")
        
        # 权限验证
        if video_assembly.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权限访问此资源")
        
        # 根据类型调用不同的下载服务
        if resource_type == "complete":
            zip_file_path = await download_service.create_complete_resource_package(
                video_assembly_uuid=uuid
            )
        else:
            raise HTTPException(status_code=400, detail="无效的资源类型，仅支持 'complete'")
        
        # 获取临时目录路径（zip文件的父目录）
        temp_dir = os.path.dirname(zip_file_path)
        
        # 提取文件名
        filename = os.path.basename(zip_file_path)
        
        # 同步清理函数（BackgroundTasks 需要 callable，在线程池中执行 rmtree）
        def cleanup_temp_files_sync():
            try:
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                    logging.getLogger(__name__).info(f"🗑️ 已清理临时目录: {temp_dir}")
            except Exception as e:
                logging.getLogger(__name__).error(f"⚠️ 清理临时目录失败: {e}")
        
        # 添加后台清理任务（传入 callable，不能传 cleanup_temp_files_sync() 的返回值）
        background_tasks.add_task(cleanup_temp_files_sync)
        
        # 直接返回文件
        return FileResponse(
            path=zip_file_path,
            filename=filename,
            media_type='application/zip',
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
        
    except HTTPException:
        # 如果出错，立即清理临时文件（在线程中执行，避免阻塞）
        if temp_dir and os.path.exists(temp_dir):
            try:
                await asyncio.to_thread(shutil.rmtree, temp_dir)
            except Exception:
                pass
        raise
    except Exception as e:
        # 如果出错，立即清理临时文件（在线程中执行，避免阻塞）
        if zip_file_path:
            temp_dir = os.path.dirname(zip_file_path)
            if temp_dir and os.path.exists(temp_dir):
                try:
                    await asyncio.to_thread(shutil.rmtree, temp_dir)
                except Exception:
                    pass
        raise HTTPException(status_code=500, detail=f"生成资源包失败: {str(e)}")


@router.get("/video-assembly/{uuid}/download-video")
async def download_single_video(
    uuid: str,
    version: str = Query("with_subtitle", description="视频版本: with_subtitle(有字幕) 或 no_subtitle(无字幕)"),
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """下载单个视频文件（通过后端代理，解决CORS问题）
    
    Args:
        uuid: 视频合成UUID
        version: 视频版本 - "with_subtitle"(有字幕) 或 "no_subtitle"(无字幕)
    """
    try:
        # 获取视频合成记录
        video_assembly = await get_video_assembly_by_uuid(uuid)
        if not video_assembly:
            raise HTTPException(status_code=404, detail="视频合成记录不存在")
        
        # 权限验证
        if video_assembly.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权限访问此资源")
        
        # 获取视频URL
        if version == "with_subtitle":
            video_url = video_assembly.final_video_url
            if not video_url:
                raise HTTPException(status_code=404, detail="有字幕视频不存在")
            filename = f"video_with_subtitle_{uuid}.mp4"
        elif version == "no_subtitle":
            video_url = video_assembly.final_video_url_no_subtitle
            if not video_url:
                raise HTTPException(status_code=404, detail="无字幕视频不存在")
            filename = f"video_no_subtitle_{uuid}.mp4"
        else:
            raise HTTPException(status_code=400, detail="无效的版本参数")
        
        # 使用StreamingResponse直接流式返回，不需要下载到服务器
        import aiohttp
        
        async def stream_video():
            async with aiohttp.ClientSession() as session:
                async with session.get(video_url) as response:
                    if response.status != 200:
                        raise HTTPException(status_code=500, detail=f"下载视频失败: {response.status}")
                    
                    # 流式读取并返回
                    async for chunk in response.content.iter_chunked(8192):
                        yield chunk
        
        return StreamingResponse(
            stream_video(),
            media_type='video/mp4',
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"下载视频失败: {str(e)}")

@router.get("/video-assembly/{uuid}/export")
async def export_video_versions(
    uuid: str,
    background_tasks: BackgroundTasks,
    version: str = Query("with_subtitle", description="视频版本: with_subtitle(有字幕) 或 no_subtitle(无字幕) 或 both(两个版本)"),
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """导出视频多版本（ZIP格式） - 支持有字幕/无字幕/两个版本
    
    Args:
        uuid: 视频合成UUID
        version: 视频版本 - "with_subtitle"(有字幕), "no_subtitle"(无字幕), "both"(两个版本)
    """
    zip_file_path = None
    temp_dir = None
    
    try:
        from ...services.download_service import download_service
        
        # 获取视频合成记录
        video_assembly = await get_video_assembly_by_uuid(uuid)
        if not video_assembly:
            raise HTTPException(status_code=404, detail="视频合成记录不存在")
        
        # 权限验证
        if video_assembly.user_id != user_id:
            raise HTTPException(status_code=403, detail="无权限访问此资源")
        
        # 创建临时目录
        import tempfile
        import asyncio as _asyncio
        temp_dir = await _asyncio.to_thread(tempfile.mkdtemp, prefix="video_export_")
        
        # 根据版本类型准备文件
        files_to_package = []
        
        if version in ["with_subtitle", "both"]:
            # 有字幕版本
            if video_assembly.final_video_url:
                video_filename = f"video_with_subtitle_{uuid}.mp4"
                files_to_package.append({
                    "url": video_assembly.final_video_url,
                    "filename": video_filename
                })
        
        if version in ["no_subtitle", "both"]:
            # 无字幕版本
            if video_assembly.final_video_url_no_subtitle:
                video_filename = f"video_no_subtitle_{uuid}.mp4"
                files_to_package.append({
                    "url": video_assembly.final_video_url_no_subtitle,
                    "filename": video_filename
                })
            elif version == "no_subtitle":
                raise HTTPException(status_code=404, detail="无字幕版本不存在")
        
        if not files_to_package:
            raise HTTPException(status_code=404, detail="没有可用的视频文件")
        
        # 下载文件到临时目录（异步写，不阻塞事件循环）
        import aiohttp
        import aiofiles
        async with aiohttp.ClientSession() as session:
            for file_info in files_to_package:
                async with session.get(file_info["url"]) as response:
                    if response.status == 200:
                        file_path = os.path.join(temp_dir, file_info["filename"])
                        data = await response.read()
                        async with aiofiles.open(file_path, 'wb') as f:
                            await f.write(data)
                    else:
                        raise HTTPException(status_code=500, detail=f"下载视频失败: {file_info['url']}")
        
        # 创建ZIP文件（在线程池中执行，避免阻塞事件循环）
        zip_filename = f"video_export_{version}_{uuid}.zip"
        zip_file_path = os.path.join(temp_dir, zip_filename)
        
        import zipfile
        def _write_zip_sync():
            with zipfile.ZipFile(zip_file_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_info in files_to_package:
                    file_path = os.path.join(temp_dir, file_info["filename"])
                    if os.path.exists(file_path):
                        zipf.write(file_path, file_info["filename"])
        await asyncio.to_thread(_write_zip_sync)
        
        # 同步清理函数（BackgroundTasks 需要 callable，在线程池中执行 rmtree）
        def cleanup_temp_files_sync():
            try:
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                    logging.getLogger(__name__).info(f"🗑️ 已清理临时目录: {temp_dir}")
            except Exception as e:
                logging.getLogger(__name__).error(f"⚠️ 清理临时目录失败: {e}")
        
        # 添加后台清理任务（传入 callable，不能传 cleanup_temp_files_sync() 的返回值）
        background_tasks.add_task(cleanup_temp_files_sync)
        
        # 直接返回文件
        return FileResponse(
            path=zip_file_path,
            filename=zip_filename,
            media_type='application/zip',
            headers={
                "Content-Disposition": f"attachment; filename={zip_filename}"
            }
        )
        
    except HTTPException:
        # 如果出错，立即清理临时文件（在线程中执行，避免阻塞）
        if temp_dir and os.path.exists(temp_dir):
            try:
                await asyncio.to_thread(shutil.rmtree, temp_dir)
            except Exception:
                pass
        raise
    except Exception as e:
        # 如果出错，立即清理临时文件（在线程中执行，避免阻塞）
        if temp_dir and os.path.exists(temp_dir):
            try:
                await asyncio.to_thread(shutil.rmtree, temp_dir)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"导出视频失败: {str(e)}")

@router.post("/music-generations", response_model=ResponseModel[MusicGenerationListResponse])
async def get_music_generations_data(
    request: MusicGenerationsBatchRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据run_id获取音乐生成数据（批量）"""
    try:
        # 使用run_id获取所有音乐生成记录
        music_generations_from_db = await get_music_generations_by_run_id(request.run_id)
        if not music_generations_from_db:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                f"No music generation data found for run_id: {request.run_id}"
            )
        
        uuid_list = [mg.uuid for mg in music_generations_from_db]
        if not uuid_list:
            raise BusinessException(
                BusinessExceptionCode.INVALID_PARAMETER,
                "UUID list cannot be empty"
            )
        
        # 获取音乐生成数据
        music_generations = await get_music_generations_by_uuids(uuid_list)
        
        # 检查权限并过滤
        filtered_generations = []
        for music_generation in music_generations:
            mg_user_id = music_generation.user_id
            if mg_user_id == user_id:
                filtered_generations.append(music_generation)
        
        if not filtered_generations:
            return ResponseModel.success(data=MusicGenerationListResponse(
                music_generations=[],
                assembly_mode="",
                music_url=""
            ))
        
        # 获取所有版本数据
        music_generation_ids = [mg.uuid for mg in filtered_generations]
        all_versions = await get_music_generation_versions_by_music_generation_ids(music_generation_ids)
        
        # 按音乐生成ID分组版本
        versions_by_generation = {}
        for version in all_versions:
            version_music_gen_id = version.music_generation_id
            if version_music_gen_id not in versions_by_generation:
                versions_by_generation[version_music_gen_id] = []
            versions_by_generation[version_music_gen_id].append(version)
        
        # 构建响应数据
        music_generation_responses = []
        for music_generation in filtered_generations:
            # 获取该音乐生成的版本
            mg_uuid = music_generation.uuid
            versions = versions_by_generation.get(mg_uuid, [])
            
            # 构建版本响应数据（按 version_number 排序）
            versions_sorted = sorted(versions, key=lambda v: getattr(v, "version_number", 0))
            version_responses = []
            for version in versions_sorted:
                version_responses.append(MusicGenerationVersionResponse(
                    uuid=getattr(version, "uuid", ""),
                    music_prompt=getattr(version, "music_prompt", ""),
                    music_url=(getattr(version, "music_url", None) or "")
                ))
            
            # 当前版本的完整音乐 URL
            mg_current_version_index = music_generation.current_version_index
            current_version = versions_sorted[mg_current_version_index] if 0 <= mg_current_version_index < len(versions_sorted) else None
            music_url = (getattr(current_version, "music_url", None) or "") if current_version else ""
            
            # 构建音乐生成响应数据
            music_generation_responses.append(MusicGenerationResponse(
                uuid=mg_uuid,
                is_instrumental=music_generation.is_instrumental,
                current_version_index=mg_current_version_index,
                music_url=music_url,
                versions=version_responses
            ))
        
        # 完整音乐 URL（与 assembly_mode 平级）：取 is_full_story_music=True 的那条的当前版本 music_url
        full_music_url = ""
        for music_generation in filtered_generations:
            if getattr(music_generation, "is_full_story_music", False):
                vers = versions_by_generation.get(music_generation.uuid, [])
                vers_sorted = sorted(vers, key=lambda v: getattr(v, "version_number", 0))
                idx = getattr(music_generation, "current_version_index", 0)
                cur = vers_sorted[idx] if 0 <= idx < len(vers_sorted) else None
                if cur and getattr(cur, "music_url", None):
                    full_music_url = (cur.music_url or "")
                    break
        
        result = MusicGenerationListResponse(
            music_generations=music_generation_responses,
            assembly_mode="",
            music_url=full_music_url
        )
        
        return ResponseModel.success(data=result)
    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"获取音乐生成数据失败: {e}", exc_info=True)
        raise BusinessException(
            BusinessExceptionCode.INTERNAL_SERVER_ERROR,
            f"获取音乐生成数据失败: {str(e)}"
        )


@router.post("/music-generations-by-thread", response_model=ResponseModel[MusicGenerationListResponse])
async def get_music_generations_data_by_thread(
    request: ThreadScopedRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据 thread_id 获取该 thread 下所有音乐生成数据（多 run 聚合）。无数据时返回空列表。"""
    conversation = await async_get_conversation_by_thread_id(request.thread_id)
    if not conversation or conversation.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权访问此 thread 或对话不存在"
        )
    conv_id = str(conversation.id)
    assembly_mode_raw = await get_latest_assembly_mode_by_thread(request.thread_id)
    # 仅对外展示 audio_driven / video_driven（历史 DB 可能存了 music_driven，映射为 audio_driven）
    assembly_mode = "audio_driven" if (assembly_mode_raw or "").strip().lower() == "music_driven" else (assembly_mode_raw or "")
    # 用"最新 transcription 关联的 mg"做版本过滤：每次 smart_clip apply 都会创建新 transcription
    # 并按其 segments 重建 per-segment mg；前端只展示当前版本的 mg 集合，旧 transcription 关联的
    # mg 自然在数据层被过滤掉，无需 archived 等额外标记。
    music_generations_from_db = await get_music_generations_by_latest_transcription(conv_id, request.thread_id)
    if not music_generations_from_db:
        return ResponseModel.success(data=MusicGenerationListResponse(music_generations=[], assembly_mode=(assembly_mode or ""), music_url=""))
    uuid_list = [mg.uuid for mg in music_generations_from_db]
    music_generations = await get_music_generations_by_uuids(uuid_list)
    filtered_by_uuid = {mg.uuid: mg for mg in music_generations if getattr(mg, "user_id", None) == user_id}
    if not filtered_by_uuid:
        return ResponseModel.success(data=MusicGenerationListResponse(music_generations=[], assembly_mode=(assembly_mode or ""), music_url=""))
    # 业务逻辑排序：按 shot_number 升序（与 get_music_generations_by_conversation 一致），无 shot_number 的整片 BGM 放最后
    ordered_uuids = [
        mg.uuid for mg in music_generations_from_db
        if mg.uuid in filtered_by_uuid
    ]
    music_generation_ids = list(filtered_by_uuid.keys())
    all_versions = await get_music_generation_versions_by_music_generation_ids(music_generation_ids)
    versions_by_generation = {}
    for version in all_versions:
        mid = getattr(version, "music_generation_id", None)
        if mid not in versions_by_generation:
            versions_by_generation[mid] = []
        versions_by_generation[mid].append(version)
    music_generation_responses = []
    for mg_uuid in ordered_uuids:
        music_generation = filtered_by_uuid[mg_uuid]
        versions = versions_by_generation.get(mg_uuid, [])
        # 业务逻辑排序：版本按 version_number 升序
        versions_sorted = sorted(versions, key=lambda v: getattr(v, "version_number", 0))
        version_responses = [
            MusicGenerationVersionResponse(
                uuid=getattr(v, "uuid", ""),
                music_prompt=getattr(v, "music_prompt", ""),
                music_url=(getattr(v, "music_url", None) or "")
            )
            for v in versions_sorted
        ]
        current_version = versions_sorted[idx] if (idx := getattr(music_generation, "current_version_index", 0)) < len(versions_sorted) else None
        music_url = (getattr(current_version, "music_url", None) or "") if current_version else ""
        music_generation_responses.append(MusicGenerationResponse(
            uuid=mg_uuid,
            is_instrumental=getattr(music_generation, "is_instrumental", False),
            current_version_index=getattr(music_generation, "current_version_index", 0),
            music_url=music_url,
            versions=version_responses
        ))
    # 完整音乐 URL（与 assembly_mode 平级）：优先 is_full_story_music=True 的 BGM；否则用该 thread 的音频转录整轨 audio_url（上传/Suno 整曲）
    full_music_url = ""
    for mg_uuid in ordered_uuids:
        mg = filtered_by_uuid.get(mg_uuid)
        if mg and getattr(mg, "is_full_story_music", False):
            vers = versions_by_generation.get(mg_uuid, [])
            vers_sorted = sorted(vers, key=lambda v: getattr(v, "version_number", 0))
            idx = getattr(mg, "current_version_index", 0)
            cur = vers_sorted[idx] if 0 <= idx < len(vers_sorted) else None
            if cur and getattr(cur, "music_url", None):
                full_music_url = (cur.music_url or "")
                break
    if not full_music_url:
        transcription = await get_video_audio_transcription_by_thread_id(request.thread_id)
        if transcription and getattr(transcription, "audio_url", None):
            full_music_url = (transcription.audio_url or "")
    return ResponseModel.success(data=MusicGenerationListResponse(music_generations=music_generation_responses, assembly_mode=(assembly_mode or ""), music_url=full_music_url))


@router.post("/characters-by-thread", response_model=ResponseModel[CharacterListResponse])
async def get_characters_data_by_thread(
    request: ThreadScopedRequest,
    user_id: str = Security(auth_service.get_current_user_or_service_user),
    include_versions: bool = Query(False, description="是否包含版本信息")
):
    """根据 thread_id 获取该 thread 下所有角色（多 run 聚合）。无数据时返回空列表。"""
    from ...crud.video.video_character import get_character_versions_by_character_id
    conversation = await async_get_conversation_by_thread_id(request.thread_id)
    if not conversation or conversation.user_id != user_id:
        raise BusinessException(
            BusinessExceptionCode.PERMISSION_DENIED,
            "无权访问此 thread 或对话不存在"
        )
    conv_id = str(conversation.id)
    characters = await get_characters_by_conversation(conv_id, request.thread_id)
    if not characters:
        return ResponseModel.success(data=CharacterListResponse(characters=[]))
    character_responses = []
    for character in characters:
        versions = None
        if include_versions:
            version_records = await get_character_versions_by_character_id(character.uuid, user_id)
            versions = [
                CharacterVersionResponse(
                    id=v.uuid,
                    version_number=v.version_number,
                    character_image_url=v.character_image_url,
                    t2i_prompt=v.t2i_prompt,
                    provider=v.provider,
                    reference_image_urls=v.reference_image_urls or [],
                    success=v.success,
                    error_msg=v.error_msg,
                    created_at=utc_isoformat(getattr(v, "created_at", None)),
                    updated_at=utc_isoformat(getattr(v, "updated_at", None))
                )
                for v in version_records
            ]
        current_image_url = getattr(character, "image_url", None) or ""
        current_version_index = getattr(character, "current_version_index", 0)
        selected_version_id = getattr(character, "selected_version_id", None)
        # Character selection is persisted by version UUID. Regeneration appends a
        # version without rewriting the legacy numeric index, so resolving only by
        # current_version_index can keep returning an older failed (empty) image.
        # Prefer the explicitly selected version and retain the index as a legacy
        # fallback for records created before selected_version_id existed.
        selected_version = next(
            (version for version in (versions or []) if version.id == selected_version_id),
            None,
        )
        if selected_version and selected_version.character_image_url:
            current_image_url = selected_version.character_image_url
            current_version_index = (versions or []).index(selected_version)
        elif versions and len(versions) > current_version_index and versions[current_version_index].character_image_url:
            current_image_url = versions[current_version_index].character_image_url
        character_responses.append(CharacterResponse(
            id=character.uuid,
            name=character.name,
            image_url=current_image_url,
            type=getattr(character, "type", None),
            current_version_index=current_version_index,
            selected_version_id=selected_version_id,
            versions=versions,
            run_id=character.run_id
        ))
    return ResponseModel.success(data=CharacterListResponse(characters=character_responses))
