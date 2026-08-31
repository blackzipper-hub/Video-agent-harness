"""
数据库操作工具函数
"""
import json
import logging
import math
from typing import List, Optional, Any, Dict
from ....models.video_state import VideoAnalysisResult, AudioTranscription, AudioSegment, AudioWord, StoryOutline, StoryStructure, StoryChapter, CharacterProfile, StoryboardScene, DetailedShot, KeyframeVersion, NarrationVersion, VideoGenerationVersion, AudioEffectVersion, VideoAssembly
from ....schemas.video.video_generation import VideoGenerationDB
from sqlalchemy.ext.asyncio import AsyncSession
from ....exceptions import BusinessException, BusinessExceptionCode
from ....utils.time_format import format_sec_to_mmss, format_sec_range
# 从新的模块化CRUD导入（按业务逻辑分组）
from ....crud.video.video_other import get_video_analysis_by_uuid
from ....crud.video.video_audio import (
    get_video_audio_transcription_by_uuid,
    get_video_audio_segments_by_transcription_uuid,
    get_video_audio_sections_by_transcription_uuid,
    get_narrations_by_thread_id,
    get_narration_versions_by_narration_ids,
    get_audio_effects_by_conversation,
    get_audio_effect_versions_by_audio_effect_ids,
)
from ....crud.video.video_story import (
    get_video_story_outline_by_uuid,
    get_chapters_by_story_outline_id,
    get_scenes_by_uuids,
    get_detailed_shots_by_uuids
)
from ....crud.video.video_character import (
    get_characters_by_uuids,
    get_characters_by_thread_id,
    get_character_versions_batch,
    pick_selected_character_version,
)
from ....crud.video.video_keyframe import (
    get_keyframes_by_story_outline_id,
    get_keyframe_versions_by_keyframe_ids,
)
from ....crud.video.video_generation import (
    get_video_generations_by_thread_id,
    get_video_generation_versions_by_video_generation_ids,
)

logger = logging.getLogger(__name__)


def _select_version(parent: Any, versions: List[Any]) -> Optional[Any]:
    """按 current_version_index 选版本，越界回退到 version_number 最大的版本。无版本返回 None。"""
    if not versions:
        return None
    sorted_v = sorted(versions, key=lambda v: v.version_number)
    idx = getattr(parent, "current_version_index", None)
    if idx is not None and 0 <= idx < len(sorted_v):
        return sorted_v[idx]
    return max(versions, key=lambda v: v.version_number)


async def get_completed_keyframe_uuids_by_shot(
    story_outline_uuid: str,
    required_frames: set,
) -> Dict[str, List[str]]:
    """断点续跑幂等：返回该 outline 下「所需帧均已成功生成」的镜头 -> 其关键帧uuid列表。

    Args:
        story_outline_uuid: 当前视频的故事梗概UUID（用于把范围限定在本次视频，避免同一会话里
            上一条已完成视频的关键帧被误判为本视频已完成）。
        required_frames: 需要的 frame_index 集合（{0} 仅首帧；{0, -1} 首帧+尾帧）。

    判定：某镜头的每个 required frame 都存在「选中版本 success=True 且 keyframe_url 非空」。
    """
    if not story_outline_uuid:
        return {}
    keyframes_db = await get_keyframes_by_story_outline_id(story_outline_uuid)
    if not keyframes_db:
        return {}
    versions_db = await get_keyframe_versions_by_keyframe_ids([kf.uuid for kf in keyframes_db])
    versions_by_kf: Dict[str, List[Any]] = {}
    for v in versions_db:
        versions_by_kf.setdefault(v.keyframe_id, []).append(v)

    # detailed_shot_id -> {frame_index: keyframe_uuid（该帧成功）}
    done_frames_by_shot: Dict[str, Dict[int, str]] = {}
    for kf in keyframes_db:
        chosen = _select_version(kf, versions_by_kf.get(kf.uuid, []))
        if chosen and chosen.success and (chosen.keyframe_url or "").strip():
            done_frames_by_shot.setdefault(kf.detailed_shot_id, {})[kf.frame_index] = kf.uuid

    result: Dict[str, List[str]] = {}
    for shot_id, frames in done_frames_by_shot.items():
        if required_frames.issubset(set(frames.keys())):
            result[shot_id] = list(frames.values())
    return result


async def get_completed_video_uuids_by_shot_number(
    story_outline_uuid: str,
    thread_id: str,
) -> Dict[int, str]:
    """断点续跑幂等：返回该 outline 下已成功生成视频的 shot_number -> video_generation_uuid。

    以 thread_id 拉取后用 story_outline_id 过滤，确保只统计本次视频的已完成镜头。
    判定：选中版本 success=True 且 video_url 非空。
    """
    if not thread_id:
        return {}
    vgs = await get_video_generations_by_thread_id(thread_id)
    if story_outline_uuid:
        vgs = [vg for vg in vgs if getattr(vg, "story_outline_id", None) == story_outline_uuid]
    if not vgs:
        return {}
    versions_db = await get_video_generation_versions_by_video_generation_ids([vg.uuid for vg in vgs])
    versions_by_vg: Dict[str, List[Any]] = {}
    for v in versions_db:
        versions_by_vg.setdefault(v.video_generation_id, []).append(v)

    result: Dict[int, str] = {}
    for vg in vgs:
        chosen = _select_version(vg, versions_by_vg.get(vg.uuid, []))
        if chosen and chosen.success and chosen.video_url:
            result[vg.shot_number] = vg.uuid
    return result


async def get_completed_narration_uuids_by_shot_number(
    story_outline_uuid: str,
    thread_id: str,
) -> Dict[int, str]:
    """断点续跑幂等：返回该 outline 下已成功生成旁白的 shot_number -> narration_uuid。

    以 thread_id 拉取后用 story_outline_id 过滤，确保只统计本次视频的已完成镜头。
    判定：选中版本 success=True 且 audio_url 非空。
    """
    if not thread_id:
        return {}
    narrations = await get_narrations_by_thread_id(thread_id)
    if story_outline_uuid:
        narrations = [n for n in narrations if getattr(n, "story_outline_id", None) == story_outline_uuid]
    if not narrations:
        return {}
    versions_db = await get_narration_versions_by_narration_ids([n.uuid for n in narrations])
    versions_by_narration: Dict[str, List[Any]] = {}
    for v in versions_db:
        versions_by_narration.setdefault(v.narration_id, []).append(v)

    result: Dict[int, str] = {}
    for n in narrations:
        chosen = _select_version(n, versions_by_narration.get(n.uuid, []))
        if chosen and chosen.success and (chosen.audio_url or "").strip():
            result[n.shot_number] = n.uuid
    return result


async def get_completed_audio_effect_uuids_by_shot_number(
    story_outline_uuid: str,
    conversation_id: str,
    thread_id: str,
) -> Dict[int, str]:
    """断点续跑幂等：返回该 outline 下已成功生成音效的 shot_number -> audio_effect_uuid。

    以 (conversation_id, thread_id) 拉取后用 story_outline_id 过滤，确保只统计本次视频的已完成镜头。
    判定：选中版本 success=True 且 audio_url 非空。
    """
    if not conversation_id or not thread_id:
        return {}
    audio_effects = await get_audio_effects_by_conversation(conversation_id, thread_id)
    if story_outline_uuid:
        audio_effects = [ae for ae in audio_effects if getattr(ae, "story_outline_id", None) == story_outline_uuid]
    if not audio_effects:
        return {}
    versions_db = await get_audio_effect_versions_by_audio_effect_ids([ae.uuid for ae in audio_effects])
    versions_by_ae: Dict[str, List[Any]] = {}
    for v in versions_db:
        versions_by_ae.setdefault(v.audio_effect_id, []).append(v)

    result: Dict[int, str] = {}
    for ae in audio_effects:
        chosen = _select_version(ae, versions_by_ae.get(ae.uuid, []))
        if chosen and chosen.success and (chosen.audio_url or "").strip():
            result[ae.shot_number] = ae.uuid
    return result


async def get_completed_character_uuids_by_name(
    thread_id: str,
    user_id: str,
) -> Dict[str, str]:
    """断点续跑幂等：返回该 thread（=本视频）下「已成功生成」的角色名 -> character_uuid。

    一个 thread 永远只对应一个视频，故以 thread_id 作用域即可（角色表无 story_outline_id 列）。
    判定：选中版本 success=True 且 character_image_url 非空。
    """
    if not thread_id or not user_id:
        return {}
    characters = await get_characters_by_thread_id(thread_id, user_id)
    if not characters:
        return {}
    versions_by_char = await get_character_versions_batch([c.uuid for c in characters], user_id)

    result: Dict[str, str] = {}
    for c in characters:
        chosen = pick_selected_character_version(versions_by_char.get(c.uuid, []), c)
        if chosen and chosen.success and (chosen.character_image_url or "").strip():
            result[c.name] = c.uuid
    return result


def _ensure_dict(value: Any) -> Optional[Dict[str, Any]]:
    """将 JSON 字符串或已有 dict 规范为 dict，供 Pydantic 等使用"""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _str_attr(obj: Any, name: str) -> Optional[str]:
    """取对象属性，若为枚举则返回 .value，否则转 str。"""
    v = getattr(obj, name, None)
    if v is None:
        return None
    if hasattr(v, "value"):
        return str(v.value)
    return str(v)


def _uuid_to_str(value: Any) -> Optional[str]:
    """将 UUID 或 str 转为 str，供 Pydantic 模型使用（asyncpg 可能返回 uuid.UUID）。"""
    if value is None:
        return None
    return str(value)


async def get_video_analysis_from_db(analysis_uuid: str) -> VideoAnalysisResult:
    """从数据库获取视频分析数据（crud/video 使用 asyncpg，无需 db）"""
    if not analysis_uuid:
        raise BusinessException(
            BusinessExceptionCode.VIDEO_ANALYSIS_UUID_MISSING,
            "缺少视频分析UUID"
        )
    
    analysis_db = await get_video_analysis_by_uuid(analysis_uuid)
    if not analysis_db:
        raise BusinessException(
            BusinessExceptionCode.VIDEO_ANALYSIS_DATA_NOT_FOUND,
            f"未找到视频分析数据: {analysis_uuid}"
        )
    
    # 构建VideoAnalysisResult对象（content_category 从 DB 的 str 转为 ContentCategory enum）
    from ....models.video_state import ContentCategory
    cc = None
    raw_cc = (getattr(analysis_db, "content_category", None) or "").strip()
    if raw_cc:
        cc = ContentCategory.from_value(raw_cc)
    extra = _ensure_dict(getattr(analysis_db, "additional_data", None)) or {}
    return VideoAnalysisResult(
        video_type=analysis_db.video_type,
        duration=analysis_db.duration,
        main_character=analysis_db.main_character,
        purpose=analysis_db.purpose,
        key_elements=json.loads(analysis_db.key_elements) if analysis_db.key_elements else [],
        style_preferences=json.loads(analysis_db.style_preferences) if analysis_db.style_preferences else [],
        target_audience=analysis_db.target_audience,
        next_action=analysis_db.next_action,
        content_category=cc,
        matched_style_category=None,
        hidden_style_description=analysis_db.hidden_style_description,
        extra=extra,
    )


async def get_audio_transcription_from_db(audio_transcription_uuids: List[str]) -> Optional[AudioTranscription]:
    """从数据库获取音频转录数据（crud/video 使用 asyncpg，无需 db）"""
    if not audio_transcription_uuids:
        return None
    
    # 获取第一个音频转录（简化处理）
    transcription_db = await get_video_audio_transcription_by_uuid(audio_transcription_uuids[0])
    if not transcription_db:
        return None
    
    # 获取音频片段
    segments_db = await get_video_audio_segments_by_transcription_uuid(transcription_db.uuid)
    
    # 构建AudioTranscription对象
    segments = [
        AudioSegment(
            uuid=segment.uuid,
            id=segment.segment_id,
            start=segment.start,
            end=segment.end,
            duration=segment.duration,
            text=segment.text,
            emotion=segment.emotion,
            tempo=segment.tempo,
            vocal_presence=getattr(segment, "vocal_presence", None),
            vocal_gender=getattr(segment, "vocal_gender", None) if getattr(segment, "vocal_gender", None) in ("f", "m") else None,
        )
        for segment in segments_db
    ]
    
    # additional_data 从 DB 可能是 JSON 字符串，需解析为 dict；整曲 Global 从 DB 带出供 outline 双线设计使用
    return AudioTranscription(
        uuid=getattr(transcription_db, "uuid", None),
        task=transcription_db.task,
        language=transcription_db.language,
        duration=transcription_db.duration,
        text=transcription_db.text,
        segments=segments,
        audio_url=transcription_db.audio_url,
        filename=transcription_db.filename,
        is_instrumental=transcription_db.is_instrumental,
        additional_data=_ensure_dict(transcription_db.additional_data),
        song_name=getattr(transcription_db, "song_name", None),
        global_bpm=getattr(transcription_db, "global_bpm", None),
        genre=getattr(transcription_db, "genre", None),
        global_emotion=getattr(transcription_db, "global_emotion", None),
        suggested_global_theme=getattr(transcription_db, "suggested_global_theme", None),
        suggested_color_palette=getattr(transcription_db, "suggested_color_palette", None),
    )


def build_audio_context_for_prompts(
    transcription: AudioTranscription,
    sections: Optional[List[Any]] = None,
) -> Dict[str, str]:
    """从 transcription + sections 构建统一的音频上下文文本块，供 analysis / outline / scene 等 prompt 使用。

    使用三层信息：Global（整曲）、Section（段落）、Segment（切片）。
    sections 为 video_audio_section 列表（可选）；无 section 时仅输出 global + segments。

    Returns:
        dict: global_block, sections_block, segments_block；某块无内容时为空字符串。
    """
    # Global 整曲
    parts = []
    if getattr(transcription, "song_name", None):
        parts.append(f"歌曲名: {transcription.song_name}")
    if getattr(transcription, "global_bpm", None) is not None:
        parts.append(f"BPM: {transcription.global_bpm}")
    if getattr(transcription, "genre", None):
        parts.append(f"曲风: {transcription.genre}")
    if getattr(transcription, "global_emotion", None):
        parts.append(f"整体情绪: {transcription.global_emotion}")
    if getattr(transcription, "suggested_global_theme", None):
        parts.append(f"建议核心主题: {transcription.suggested_global_theme}")
    if getattr(transcription, "suggested_color_palette", None):
        parts.append(f"建议色彩倾向: {transcription.suggested_color_palette}")
    global_block = "\n".join(parts) if parts else ""
    if global_block:
        global_block = "**音乐线整曲信息：**\n" + global_block

    # Sections 段落（Song Structure）
    sections_block = ""
    sections_with_segments_block = ""
    segments_list = list(transcription.segments)

    if sections:
        lines = []
        for i, sec in enumerate(sections):
            st = getattr(sec, "start_time", 0) or 0
            et = getattr(sec, "end_time", 0) or 0
            dur = et - st if et > st else 0
            sec_type = getattr(sec, "section_type", "") or ""
            features = getattr(sec, "musical_features", "") or ""
            emotion = getattr(sec, "section_emotion", "") or ""
            intensity = getattr(sec, "suggested_visual_intensity", "") or ""
            strategy = getattr(sec, "suggested_rhythmic_strategy", "") or ""
            theme = getattr(sec, "suggested_visual_theme", "") or ""
            ctx = getattr(sec, "suggested_context", "") or ""
            line = f"  段落{i}: {sec_type} | {format_sec_range(st, et)} ({format_sec_to_mmss(dur)})"
            if features:
                line += f" | 音乐特征: {features}"
            if emotion:
                line += f" | 情绪: {emotion}"
            if intensity:
                line += f" | 视觉强度: {intensity}"
            if strategy:
                line += f" | 节奏策略: {strategy}"
            if theme:
                line += f" | 视觉主题: {theme}"
            if ctx:
                line += f" | 情境: {ctx}"
            lines.append(line)
        sections_block = "**曲式/段落（Song Structure）：**\n" + "\n".join(lines)

        # 段落 + 其下音频片段（层级展示，便于 1:1 章节设计）
        section_lines = []
        for i, sec in enumerate(sections):
            st = float(getattr(sec, "start_time", 0) or 0)
            et = float(getattr(sec, "end_time", 0) or 0)
            dur = et - st if et > st else 0
            sec_type = getattr(sec, "section_type", "") or ""
            features = getattr(sec, "musical_features", "") or ""
            emotion = getattr(sec, "section_emotion", "") or ""
            section_lines.append(f"  段落{i}: {sec_type} | {format_sec_range(st, et)} ({format_sec_to_mmss(dur)}) | 音乐特征: {features} | 情绪: {emotion}")
            for j, seg in enumerate(segments_list):
                if seg.start < et and seg.end > st:
                    text_display = (seg.text or "").strip() or ("[音乐段落]" if transcription.is_instrumental else "[停顿/无歌词]")
                    emotion_s = f" | 情感: {seg.emotion}" if getattr(seg, "emotion", None) else ""
                    tempo_s = f" | 速度: {seg.tempo}" if getattr(seg, "tempo", None) else ""
                    vocal = getattr(seg, "vocal_presence", None)
                    vocal_s = " | 人声: 有" if vocal is True else (" | 人声: 无" if vocal is False else "")
                    vocal_gender_s = ""
                    if getattr(seg, "vocal_gender", None) in ("f", "m"):
                        vocal_gender_s = " | 人声性别: 女" if seg.vocal_gender == "f" else " | 人声性别: 男"
                    section_lines.append(
                        f"    音频片段{j}: {format_sec_range(seg.start, seg.end)} ({format_sec_to_mmss(seg.duration)}) - \"{text_display}\"{emotion_s}{tempo_s}{vocal_s}{vocal_gender_s}"
                    )
        sections_with_segments_block = "**曲式/段落（Song Structure）与所属音频片段：**\n" + "\n".join(section_lines)

    # Segments 切片（扁平列表，无 section 时使用）
    seg_lines = []
    for i, seg in enumerate(segments_list):
        text_display = (seg.text or "").strip() or ("[音乐段落]" if transcription.is_instrumental else "[停顿/无歌词]")
        emotion_info = f" | 情感: {seg.emotion}" if getattr(seg, "emotion", None) else ""
        tempo_info = f" | 速度: {seg.tempo}" if getattr(seg, "tempo", None) else ""
        vocal = getattr(seg, "vocal_presence", None)
        vocal_info = " | 人声: 有" if vocal is True else (" | 人声: 无" if vocal is False else "")
        vocal_gender_info = ""
        if getattr(seg, "vocal_gender", None) in ("f", "m"):
            vocal_gender_info = " | 人声性别: 女" if seg.vocal_gender == "f" else " | 人声性别: 男"
        seg_lines.append(
            f"  音频片段{i}: {format_sec_range(seg.start, seg.end)} ({format_sec_to_mmss(seg.duration)}) - \"{text_display}\"{emotion_info}{tempo_info}{vocal_info}{vocal_gender_info}"
        )
    segments_block = "\n".join(seg_lines) if seg_lines else ""

    return {
        "global_block": global_block,
        "sections_block": sections_block,
        "sections_with_segments_block": sections_with_segments_block,
        "segments_block": segments_block,
    }


async def get_audio_transcription_with_sections(
    audio_transcription_uuids: List[str],
) -> tuple[Optional[AudioTranscription], List[Any]]:
    """加载转录及其段落列表（一层调用，供 analysis/outline 等复用）。"""
    transcription = await get_audio_transcription_from_db(audio_transcription_uuids)
    if not transcription or not getattr(transcription, "uuid", None):
        return transcription, []
    sections = await get_video_audio_sections_by_transcription_uuid(transcription.uuid)
    return transcription, list(sections) if sections else []


async def get_story_outline_from_db(story_outline_uuid: str) -> StoryOutline:
    """从数据库获取故事大纲数据（crud/video 使用 asyncpg，无需 db）"""
    if not story_outline_uuid:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            "缺少故事大纲UUID"
        )
    
    outline_db = await get_video_story_outline_by_uuid(story_outline_uuid)
    if not outline_db:
        raise BusinessException(
            BusinessExceptionCode.RESOURCE_NOT_FOUND,
            f"未找到故事大纲数据: {story_outline_uuid}"
        )
    
    # 获取章节信息（按 order 排序并规范为 0-based，兼容历史 1-based 数据）
    from ....utils.chapter_order import sort_chapters_by_order

    chapters_db = sort_chapters_by_order(
        await get_chapters_by_story_outline_id(outline_db.uuid)
    )

    from ....models.video_state import EnhancementCue

    chapters = []
    for i, chapter in enumerate(chapters_db):
        ad = _ensure_dict(getattr(chapter, "additional_data", None)) or {}
        raw_cues = ad.get("enhancement_cues") if isinstance(ad, dict) else None
        enhancement_cues = None
        if isinstance(raw_cues, list) and raw_cues:
            enhancement_cues = []
            for raw in raw_cues:
                if not isinstance(raw, dict):
                    continue
                desc = (raw.get("description") or "").strip()
                if not desc:
                    continue
                enhancement_cues.append(
                    EnhancementCue(
                        type=str(raw.get("type") or "broll"),
                        description=desc,
                        timestamp_hint=raw.get("timestamp_hint"),
                    )
                )
            if not enhancement_cues:
                enhancement_cues = None
        chapters.append(
            StoryChapter(
                id=chapter.uuid,
                title=chapter.title,
                description=chapter.description,
                duration=chapter.duration,
                order=i,
                audio_segment_ids=chapter.audio_segment_ids,
                audio_section_uuid=_uuid_to_str(getattr(chapter, "audio_section_uuid", None)),
                enhancement_cues=enhancement_cues,
            )
        )
    
    story_structure = StoryStructure(
        chapters=chapters
    )
    
    return StoryOutline(
        uuid=outline_db.uuid,
        title=outline_db.title,
        theme=outline_db.theme,
        key_message=outline_db.key_message,
        total_duration=outline_db.total_duration,
        style_guide=outline_db.style_guide,
        structure=story_structure,
        description=outline_db.description,
        analysis_uuid=outline_db.analysis_id,
        conversation_id=outline_db.conversation_id,
        thread_id=outline_db.thread_id,
        user_id=outline_db.user_id,
        run_id=outline_db.run_id
    )


async def get_character_images_with_latest_versions(
    character_uuids: List[str], 
    user_id: str
) -> Dict[str, str]:
    """获取角色「当前选中版本」主图 URL（与角色 select-version / current_version_index 一致）。使用 asyncpg CRUD。
    
    Args:
        character_uuids: 角色UUID列表
        user_id: 用户ID
        
    Returns:
        Dict[str, str]: 角色ID到选中版本图片URL的映射
    """
    character_images = {}
    
    if not character_uuids:
        return character_images
    
    try:
        from ....crud.video.video_character import get_characters_by_uuids, get_character_versions_batch, pick_selected_character_version
        char_rows = await get_characters_by_uuids(character_uuids)
        char_row_by_uuid = {r.uuid: r for r in char_rows}
        
        # 批量获取角色的选中版本图片
        character_uuids_with_images = [c.uuid for c in char_rows if getattr(c, "image_url", None)]
        if character_uuids_with_images:
            try:
                # 批量获取所有角色的版本
                all_versions = await get_character_versions_batch(character_uuids_with_images, user_id)
                
                # 构建角色UUID到选中版本图片的映射
                uuid_to_selected_image = {}
                for character_uuid, versions in all_versions.items():
                    if versions:
                        picked = pick_selected_character_version(versions, char_row_by_uuid.get(character_uuid))
                        if picked and picked.character_image_url:
                            uuid_to_selected_image[character_uuid] = picked.character_image_url
                
                # 构建最终的图片字典（优先使用选中版本图片）
                for character in char_rows:
                    if character.uuid in uuid_to_selected_image:
                        # 使用选中版本的图片
                        character_images[character.uuid] = uuid_to_selected_image[character.uuid]
                        logger.info(f"🎭 角色 {character.name} 使用选中版本图片: {uuid_to_selected_image[character.uuid]}")
                    elif character.image_url:
                        # 兜底使用原始图片
                        character_images[character.uuid] = character.image_url
                        logger.info(f"🎭 角色 {character.name} 使用原始图片: {character.image_url}")
                        
            except Exception as e:
                logger.warning(f"批量获取角色版本失败: {e}")
                # 如果批量获取失败，使用原始图片信息
                for character in char_rows:
                    if character.image_url:
                        character_images[character.uuid] = character.image_url
        else:
            # 没有有效的角色UUID，使用原始图片信息
            for character in char_rows:
                if character.image_url:
                    character_images[character.uuid] = character.image_url
                    
    except Exception as e:
        logger.error(f"获取角色图片失败: {e}")
        
    return character_images


async def get_characters_from_db(character_uuids: List[str]) -> List[CharacterProfile]:
    """从数据库获取角色信息 - 使用asyncpg CRUD"""
    try:
        if not character_uuids:
            return []
        
        from ....crud.video.video_character import get_characters_by_uuids
        
        characters_db = await get_characters_by_uuids(character_uuids)
        
        from ....models.video_state import VisualElementType
        
        return [
            CharacterProfile(
                id=char.uuid,
                type=VisualElementType(getattr(char, 'type', 'character')),  # 将字符串转为枚举类型，兼容旧数据
                name=char.name,
                description=char.description,
                role=char.role,
                personality=char.personality or "",
                appearance=char.appearance or "",
                style=char.style or "",
                character_image_url=char.image_url or "",
                body_type=char.body_type or ""
            )
            for char in characters_db
        ]
        
    except BusinessException as e:
        logger.error(f"获取角色信息失败: {e.detail}")
        raise e


async def get_scenes_from_db(scene_uuids: List[str]) -> List[StoryboardScene]:
    """从数据库获取场景信息 - 使用asyncpg CRUD"""
    try:
        if not scene_uuids:
            return []
        
        scenes_db = await get_scenes_by_uuids(scene_uuids)
        
        # 按 scene_number 排序
        scenes_db_sorted = sorted(scenes_db, key=lambda x: x.scene_number)
        
        return [
            StoryboardScene(
                uuid=scene.uuid,
                scene_number=scene.scene_number,
                title=scene.title,
                description=scene.description,
                duration=scene.duration,
                camera_angle=scene.camera_angle,
                character_action=scene.character_action,
                visual_style=scene.visual_style,
                transition_style=scene.transition_style,
                is_bridge=scene.is_bridge,
                character_ids=scene.character_ids or [],
                audio_segment_ids=scene.audio_segment_ids,
                chapter_id=scene.chapter_id,
                generation_mode=scene.generation_mode,
                additional_data=scene.additional_data,
            )
            for scene in scenes_db_sorted
        ]
        
    except BusinessException as e:
        logger.error(f"获取场景信息失败: {e.detail}")
        raise e


async def get_detailed_shots_from_db(shot_uuids: List[str]) -> List[DetailedShot]:
    """从数据库获取详细镜头信息 - 使用asyncpg CRUD"""
    try:
        if not shot_uuids:
            return []
        from ....crud.video.video_story import get_detailed_shots_by_uuids
        shots_db = await get_detailed_shots_by_uuids(shot_uuids)
        
        # 转换为DetailedShot对象
        shots = []
        for shot_db in shots_db:
            ad = _ensure_dict(getattr(shot_db, "additional_data", None)) or {}
            from ....services.agent.video.narration_gender_utils import (
                NARRATION_GENDER_KEY,
                normalize_narration_gender,
            )
            narration_gender = normalize_narration_gender(ad.get(NARRATION_GENDER_KEY))
            shot = DetailedShot(
                shot_number=shot_db.shot_number,
                duration=shot_db.duration,
                shot_type=shot_db.shot_type,
                camera_position=shot_db.camera_position,  # 新增：相机机位
                camera_angle=shot_db.camera_angle,  # 新增：相机角度
                subject_angle=shot_db.subject_angle,  # 新增：主体角度
                subject_pose=shot_db.subject_pose,  # 新增：主体姿势
                scene_description=shot_db.scene_description,
                camera_movement=shot_db.camera_movement,
                lighting=shot_db.lighting,
                visual_effects=shot_db.visual_effects,
                transition=shot_db.transition,
                dialogue=shot_db.dialogue,
                narration=shot_db.narration,
                narration_gender=narration_gender,
                sound_effects=shot_db.sound_effects,
                is_bridge=shot_db.is_bridge,
                character_ids=shot_db.character_ids if shot_db.character_ids else [],
                audio_segment_ids=shot_db.audio_segment_ids,
                style_guide=shot_db.style_guide,
                generation_mode=shot_db.generation_mode,
                generation_routing=_ensure_dict(getattr(shot_db, "generation_routing", None)),
                additional_data=ad,
            )
            # 添加数据库相关字段
            shot.uuid = shot_db.uuid
            shot.scene_id = shot_db.scene_id
            shot.storyboard_detail_id = shot_db.storyboard_detail_id
            shots.append(shot)

        # 数据源日志：关键帧用的 character_ids 来自 DB detailed_shot 表
        try:
            _ds = {
                "source": "video_detailed_shots",
                "shot_uuids": shot_uuids,
                "shots": [{"shot_number": s.shot_number, "character_ids": getattr(s, "character_ids", [])} for s in shots],
            }
            logger.info("[keyframe_datasource] get_detailed_shots_from_db: %s", _ds)
        except Exception as _e:
            logger.debug("keyframe_datasource log skip: %s", _e)
        
        return shots
        
    except BusinessException as e:
        logger.error(f"获取详细镜头信息失败: {e.detail}")
        raise e


async def save_keyframe_to_db(
    keyframe_version: 'KeyframeVersion', 
    shot: 'DetailedShot',
    story_outline_uuid: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    character_version_ids: Optional[List[str]] = None
) -> str:
    """保存关键帧到数据库（使用asyncpg CRUD）"""
    try:
        from ....crud.video.video_keyframe import create_keyframe, create_keyframe_version
        
        # 创建关键帧记录
        keyframe_uuid = await create_keyframe(
            shot_number=keyframe_version.shot_number,
            is_bridge=keyframe_version.is_bridge,
            reference_image_urls=keyframe_version.reference_image_urls,
            conversation_id=conversation_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            story_outline_id=story_outline_uuid,
            scene_id=keyframe_version.scene_id or getattr(shot, 'scene_id', ""),
            storyboard_detail_id=keyframe_version.storyboard_detail_id or getattr(shot, 'storyboard_detail_id', ""),
            detailed_shot_id=keyframe_version.detailed_shot_id or getattr(shot, 'uuid', ""),
            character_ids=getattr(shot, 'character_ids', None),
            frame_index=keyframe_version.frame_index  # ⭐ 新增：传递frame_index
        )
        
        # 创建关键帧版本记录（再生参数字段写入列，兼容老数据仍写 additional_data）
        additional_data = {}
        if hasattr(keyframe_version, 'seed') and keyframe_version.seed is not None:
            additional_data['seed'] = keyframe_version.seed
        if hasattr(keyframe_version, 'aspect_ratio') and keyframe_version.aspect_ratio:
            additional_data['aspect_ratio'] = keyframe_version.aspect_ratio
        if hasattr(keyframe_version, 'resolution') and keyframe_version.resolution:
            additional_data['resolution'] = keyframe_version.resolution
        if hasattr(keyframe_version, 'model') and keyframe_version.model:
            additional_data['model'] = keyframe_version.model
        if getattr(keyframe_version, 'applied_skill_ids', None):
            additional_data['applied_skill_ids'] = keyframe_version.applied_skill_ids
        if getattr(keyframe_version, 'constraint_coverage', None):
            additional_data['constraint_coverage'] = keyframe_version.constraint_coverage
        if getattr(keyframe_version, 'final_prompt', None):
            additional_data['final_prompt'] = keyframe_version.final_prompt

        version_uuid = await create_keyframe_version(
            keyframe_id=keyframe_uuid,
            version_number=1,
            shot_number=keyframe_version.shot_number,
            keyframe_url=keyframe_version.keyframe_url,
            t2i_prompt=keyframe_version.t2i_prompt,
            provider=keyframe_version.provider,
            is_bridge=keyframe_version.is_bridge,
            reference_image_urls=keyframe_version.reference_image_urls,
            success=keyframe_version.success,
            conversation_id=conversation_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            error_msg=keyframe_version.error_msg,
            raw_error_msg=getattr(keyframe_version, 'raw_error_msg', None),
            audio_segment_ids=keyframe_version.audio_segment_ids,
            ai_messages=keyframe_version.ai_messages_json,
            image_tool_metrics=keyframe_version.image_tool_metrics,
            tool_duration_sec=keyframe_version.tool_duration_sec,
            tool_cost=keyframe_version.tool_cost,
            character_version_ids=character_version_ids,
            additional_data=additional_data if additional_data else None,
            aspect_ratio=_str_attr(keyframe_version, 'aspect_ratio'),
            resolution=_str_attr(keyframe_version, 'resolution'),
            seed=getattr(keyframe_version, 'seed', None),
            model=getattr(keyframe_version, 'model', None),
            image_generation_tool=_str_attr(keyframe_version, 'image_generation_tool'),
        )
        
        return keyframe_uuid
        
    except Exception as e:
        logger.error(f"保存关键帧到数据库失败: {e}")
        raise


async def save_narration_to_db(
    narration_version: 'NarrationVersion', 
    shot: 'DetailedShot',
    story_outline_uuid: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str
) -> str:
    """保存旁白到数据库（使用 asyncpg，无需 db）"""
    try:
        from ....crud.video.video_audio import create_narration_shot_based, create_narration_version_shot_based

        # 创建旁白记录
        narration_uuid = await create_narration_shot_based(
            shot_number=narration_version.shot_number,
            is_bridge=narration_version.is_bridge,
            has_narration=True,
            conversation_id=conversation_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            story_outline_id=story_outline_uuid,
            scene_id=shot.scene_id or "",
            storyboard_detail_id=shot.storyboard_detail_id or "",
            detailed_shot_id=shot.uuid or ""
        )
        
        # 创建旁白版本记录
        narration_version_uuid = await create_narration_version_shot_based(
            narration_id=narration_uuid,
            version_number=narration_version.version_number,
            shot_number=narration_version.shot_number,
            narration_text=narration_version.narration_text,
            enhanced_prompt=narration_version.enhanced_prompt,
            audio_url=narration_version.audio_url,
            provider=narration_version.provider,
            params=narration_version.params,
            duration=narration_version.duration,
            is_bridge=narration_version.is_bridge,
            success=narration_version.success,
            error_msg=narration_version.error_msg,
            conversation_id=conversation_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            ai_messages=narration_version.ai_messages_json
        )
        
        # 更新旁白版本的UUID
        narration_version.uuid = narration_version_uuid
        
        return narration_uuid
        
    except Exception as e:
        logger.error(f"保存旁白到数据库失败: {e}")
        raise


async def get_keyframes_from_db(keyframe_uuids: List[str]) -> List[KeyframeVersion]:
    """从数据库获取关键帧信息 - 使用asyncpg CRUD"""
    try:
        if not keyframe_uuids:
            return []
        from ....crud.video.video_keyframe import get_keyframes_by_uuids, get_keyframe_versions_by_keyframe_ids
        from ....models.video_state import KeyframeVersion
        
        # 获取关键帧基础信息
        keyframes_db = await get_keyframes_by_uuids(keyframe_uuids)
        if not keyframes_db:
            logger.warning(f"未找到关键帧数据: {keyframe_uuids}")
            return []
        
        # 获取关键帧版本信息
        keyframe_ids = [kf.uuid for kf in keyframes_db]
        keyframe_versions_db = await get_keyframe_versions_by_keyframe_ids(keyframe_ids)
        
        # 按关键帧ID分组版本数据
        versions_by_keyframe = {}
        for version in keyframe_versions_db:
            if version.keyframe_id not in versions_by_keyframe:
                versions_by_keyframe[version.keyframe_id] = []
            versions_by_keyframe[version.keyframe_id].append(version)
        
        # 构建KeyframeVersion对象列表
        keyframe_versions = []
        for keyframe_db in keyframes_db:
            versions = versions_by_keyframe.get(keyframe_db.uuid, [])
            # 按版本号排序，取当前选中版本（current_version_index），越界则回退 version_number 最大
            if versions:
                sorted_kv = sorted(versions, key=lambda v: v.version_number)
                idx = getattr(keyframe_db, "current_version_index", None)
                if idx is not None and 0 <= idx < len(sorted_kv):
                    chosen_version = sorted_kv[idx]
                else:
                    chosen_version = max(versions, key=lambda v: v.version_number)
                
                # 从 additional_data 中提取 seed、aspect_ratio、resolution 和 model
                seed = None
                aspect_ratio = None
                resolution = None
                model = None
                if chosen_version.additional_data:
                    seed = chosen_version.additional_data.get('seed')
                    aspect_ratio = chosen_version.additional_data.get('aspect_ratio')
                    resolution = chosen_version.additional_data.get('resolution')
                    model = chosen_version.additional_data.get('model')
                
                keyframe_version = KeyframeVersion(
                    shot_number=keyframe_db.shot_number,
                    is_bridge=keyframe_db.is_bridge,
                    reference_image_urls=keyframe_db.reference_image_urls or [],
                    keyframe_url=chosen_version.keyframe_url,
                    t2i_prompt=chosen_version.t2i_prompt,
                    provider=chosen_version.provider,
                    success=chosen_version.success,
                    error_msg=chosen_version.error_msg,
                    audio_segment_ids=chosen_version.audio_segment_ids,
                    version_id=chosen_version.uuid,
                    frame_index=keyframe_db.frame_index,  # ⭐ 新增：从数据库读取frame_index
                    # 保留关键帧和相关ID信息
                    keyframe_uuid=keyframe_db.uuid,
                    scene_id=keyframe_db.scene_id,
                    storyboard_detail_id=keyframe_db.storyboard_detail_id,
                    detailed_shot_id=keyframe_db.detailed_shot_id,
                    seed=seed,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    model=model
                )
                keyframe_versions.append(keyframe_version)
        
        # 按镜头编号和帧索引排序（先按shot_number，再按frame_index，首帧在前）
        keyframe_versions.sort(key=lambda kf: (kf.shot_number, kf.frame_index if kf.frame_index >= 0 else 999))
        
        logger.info(f"✅ 成功获取{len(keyframe_versions)}个关键帧数据")
        return keyframe_versions
        
    except Exception as e:
        logger.error(f"从数据库获取关键帧失败: {e}")
        raise


async def get_video_generations_from_db(video_generation_uuids: List[str]) -> List[VideoGenerationVersion]:
    """从数据库获取视频生成信息 - 使用asyncpg CRUD"""
    try:
        if not video_generation_uuids:
            return []
        
        from ....crud.video.video_generation import get_video_generation_versions_by_video_generation_uuids
        from ....models.video_state import VideoGenerationVersion
        
        # 获取视频生成版本数据
        video_generation_versions_db = await get_video_generation_versions_by_video_generation_uuids(video_generation_uuids)
        
        video_generations = []
        for video_gen_db in video_generation_versions_db:
            # 从 additional_data 中提取 seed 和 resolution
            seed = None
            resolution = None
            if video_gen_db.additional_data:
                seed = video_gen_db.additional_data.get('seed')
                resolution = video_gen_db.additional_data.get('resolution')
            
            video_gen = VideoGenerationVersion(
                shot_number=video_gen_db.shot_number,
                video_url=video_gen_db.video_url,
                duration=int(math.ceil(video_gen_db.duration)) if video_gen_db.duration is not None else 0,
                i2v_prompt=video_gen_db.motion_prompt,
                provider=video_gen_db.provider,
                is_bridge=video_gen_db.is_bridge,
                keyframe_url=video_gen_db.keyframe_url,
                success=video_gen_db.success,
                error_msg=video_gen_db.error_msg,
                audio_segment_ids=video_gen_db.audio_segment_ids,
                seed=seed,
                resolution=resolution
            )
            # Set version_id and video_generation_id from database
            video_gen.version_id = video_gen_db.uuid
            video_gen.video_generation_id = video_gen_db.video_generation_id
            video_generations.append(video_gen)
        
        return video_generations
        
    except Exception as e:
        logger.error(f"从数据库获取视频生成失败: {e}")
        return []


async def save_video_generation_to_db(
    video_segment_version: 'VideoGenerationVersion', 
    keyframe: 'KeyframeVersion',
    story_outline_uuid: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    keyframe_ids: Optional[List[str]] = None  # ⭐ 新增：所有相关的keyframe_ids
) -> str:
    """保存视频生成到数据库（使用asyncpg CRUD）"""
    try:
        from ....crud.video.video_generation import create_video_generation, create_video_generation_version
        
        # 创建视频生成记录（reference_t2v 无关键帧时 keyframe_id 为空）
        _kf_uuid = getattr(keyframe, "keyframe_uuid", None) if keyframe else None
        _kf_uuid = (_kf_uuid or "").strip() or None
        _shot_id = getattr(keyframe, "detailed_shot_id", None) if keyframe else None
        if not _shot_id:
            _shot_id = getattr(video_segment_version, "detailed_shot_id", None) or ""
        video_generation_uuid = await create_video_generation(
            shot_number=video_segment_version.shot_number,
            is_bridge=video_segment_version.is_bridge,
            conversation_id=conversation_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            story_outline_id=story_outline_uuid,
            scene_id=(getattr(keyframe, "scene_id", None) or "") if keyframe else "",
            storyboard_detail_id=(getattr(keyframe, "storyboard_detail_id", None) or "") if keyframe else "",
            detailed_shot_id=_shot_id or "",
            keyframe_id=_kf_uuid,
            keyframe_ids=keyframe_ids  # ⭐ 新增：传递所有keyframe_ids
        )
        
        # 创建视频生成版本记录（再生参数字段写入列）
        additional_data = {}
        if hasattr(video_segment_version, 'seed') and video_segment_version.seed is not None:
            additional_data['seed'] = video_segment_version.seed
        if hasattr(video_segment_version, 'resolution') and video_segment_version.resolution:
            additional_data['resolution'] = video_segment_version.resolution
        if getattr(video_segment_version, 'consistency_reason', None):
            additional_data['consistency_reason'] = video_segment_version.consistency_reason
        if getattr(video_segment_version, 'preview_video_url', None):
            additional_data['preview_video_url'] = video_segment_version.preview_video_url
        _ref_urls = getattr(video_segment_version, 'reference_image_urls', None) or []
        if _ref_urls:
            additional_data['reference_image_urls'] = list(_ref_urls)

        provider = getattr(video_segment_version, 'provider', 'pollo')
        version_uuid = await create_video_generation_version(
            video_generation_id=video_generation_uuid,
            version_number=1,
            shot_number=video_segment_version.shot_number,
            video_url=video_segment_version.video_url,
            provider=provider,
            motion_prompt=getattr(video_segment_version, 'i2v_prompt', ''),
            duration=video_segment_version.duration,
            is_bridge=video_segment_version.is_bridge,
            keyframe_url=video_segment_version.keyframe_url,
            keyframe_version_ids=video_segment_version.keyframe_version_ids,
            success=video_segment_version.success,
            conversation_id=conversation_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            additional_data=additional_data if additional_data else None,
            error_msg=getattr(video_segment_version, 'error_msg', None),
            raw_error_msg=getattr(video_segment_version, 'raw_error_msg', None),
            ai_messages=getattr(video_segment_version, 'ai_messages_json', None),
            audio_segment_ids=getattr(video_segment_version, 'audio_segment_ids', None),
            aspect_ratio=_str_attr(video_segment_version, 'aspect_ratio'),
            resolution=_str_attr(video_segment_version, 'resolution'),
            video_generation_tool=_str_attr(video_segment_version, 'video_generation_tool'),
            model=getattr(video_segment_version, 'model', None),
            generation_mode=video_segment_version.generation_mode,
            audio_url=video_segment_version.audio_url,
            video_tool_metrics=getattr(video_segment_version, 'video_tool_metrics', None),
            tool_duration_sec=getattr(video_segment_version, 'tool_duration_sec', None),
            tool_cost=getattr(video_segment_version, 'tool_cost', None),
        )
        
        return video_generation_uuid
        
    except Exception as e:
        logger.error(f"保存视频生成到数据库失败: {e}")
        raise e


async def save_audio_effect_to_db(
    audio_effect_version: 'AudioEffectVersion', 
    video_gen: "VideoGenerationDB",
    story_outline_uuid: str,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str
) -> str:
    """保存音效到数据库（使用asyncpg CRUD）"""
    try:
        from ....crud.video.video_audio import create_audio_effect_shot_based, create_audio_effect_version_shot_based

        # 创建音效记录
        audio_effect_uuid = await create_audio_effect_shot_based(
            shot_number=audio_effect_version.shot_number,
            is_bridge=audio_effect_version.is_bridge,
            has_audio_effect=True,
            video_generation_id=video_gen.uuid,
            conversation_id=conversation_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            story_outline_id=story_outline_uuid,
            scene_id=video_gen.scene_id or "",
            storyboard_detail_id=video_gen.storyboard_detail_id or "",
            detailed_shot_id=video_gen.detailed_shot_id or ""
        )
        
        # 创建音效版本记录
        audio_effect_version_uuid = await create_audio_effect_version_shot_based(
            audio_effect_id=audio_effect_uuid,
            version_number=audio_effect_version.version_number,
            shot_number=audio_effect_version.shot_number,
            video_url=audio_effect_version.video_url,
            audio_prompt=audio_effect_version.audio_prompt,
            enhanced_prompt=audio_effect_version.enhanced_prompt,
            audio_url=audio_effect_version.audio_url,
            video_with_audio_url=audio_effect_version.video_with_audio_url,
            provider=audio_effect_version.provider,
            params=audio_effect_version.params,
            duration=audio_effect_version.duration,
            is_bridge=audio_effect_version.is_bridge,
            success=audio_effect_version.success,
            error_msg=audio_effect_version.error_msg,
            conversation_id=conversation_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id
        )
        
        logger.info(f"音效保存成功: {audio_effect_uuid}")
        return audio_effect_uuid
        
    except Exception as e:
        logger.error(f"保存音效到数据库失败: {e}")
        raise e


async def save_video_assembly_to_db(
    video_assembly: 'VideoAssembly',
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str
) -> str:
    """保存视频合成到数据库（使用asyncpg CRUD）"""
    try:
        from ....crud.video.video_other import create_video_assembly
        
        # 创建视频合成记录
        assembly_uuid = await create_video_assembly(
            final_video_url=video_assembly.final_video_url,
            total_duration=video_assembly.total_duration,
            success=video_assembly.success,
            error_msg=video_assembly.error_msg,
            video_urls=video_assembly.video_urls,
            music_url=video_assembly.music_url,
            audio_url=video_assembly.audio_url,
            story_outline_id=video_assembly.story_outline_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            additional_data=video_assembly.additional_data
        )
        
        return assembly_uuid
        
    except Exception as e:
        logger.error(f"保存视频合成到数据库失败: {e}")
        raise e

