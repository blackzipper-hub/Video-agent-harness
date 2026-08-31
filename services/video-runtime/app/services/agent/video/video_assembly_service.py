"""
视频合成功能模块
负责视频合成节点的实现和相关辅助功能
"""
import asyncio
import logging
import math
import os
import uuid
from datetime import datetime
from typing import Dict, Any, Union, List, Optional, Tuple
from langgraph.runtime import Runtime

from ....models.video_state import VideoAgentState, VideoAssemblyData, MusicAnalysisResult, AudioTranscription, AudioSegment
from ....exceptions import BusinessException, BusinessExceptionCode
from ..schemas import VideoContextSchema
from ....services.agent.utils.database_utils import get_story_outline_from_db, get_video_generations_from_db
from ....utils.file_utils import MediaType
from ....utils.i18n import get_i18n_message_async
from ....utils.subtitle_utils import (
    create_subtitle_segments_from_audio_mapping,
    create_subtitle_segments_from_narrations,
    create_temp_subtitle_file,
    add_subtitles_to_video_command,
    SubtitleSegment,
)
from ....schemas.video.video_segment import VideoSegmentVersionDB
from ....crud.video.video_generation import get_video_generation_versions_by_uuids
from ....utils import media_service_client as msc

logger = logging.getLogger(__name__)


async def get_narrations_for_videos(conversation_id: str, thread_id: str) -> Dict[int, Dict[str, Any]]:
    """获取视频对应的旁白数据（crud/video 使用 asyncpg，无需 db）"""
    try:
        from ....crud.video.video_audio import get_narrations_by_conversation, get_narration_versions_by_narration_ids
        from ....services.agent.utils.database_utils import _select_version
        
        # 获取所有旁白
        narrations = await get_narrations_by_conversation(conversation_id, thread_id)
        if not narrations:
            return {}
        
        # 获取旁白版本
        narration_ids = [n.uuid for n in narrations]
        narration_versions = await get_narration_versions_by_narration_ids(narration_ids)
        versions_by_narration: Dict[str, List[Any]] = {}
        for v in narration_versions:
            versions_by_narration.setdefault(v.narration_id, []).append(v)
        
        # 按镜头编号组织数据
        narrations_by_shot = {}
        for narration in narrations:
            chosen = _select_version(narration, versions_by_narration.get(narration.uuid, []))
            if not chosen or not chosen.success or not (chosen.audio_url or "").strip():
                continue
            narrations_by_shot[narration.shot_number] = {
                'narration': narration,
                'version': chosen
            }
        
        logger.info(f"🎙️ 获取到旁白数据: {list(narrations_by_shot.keys())} 镜头")
        return narrations_by_shot
        
    except Exception as e:
        logger.error(f"❌ 获取旁白数据失败: {e}")
        return {}


async def get_audio_effects_for_videos(conversation_id: str, thread_id: str) -> Dict[int, Dict[str, Any]]:
    """获取视频对应的音效数据（crud/video 使用 asyncpg，无需 db）"""
    try:
        from ....crud.video.video_audio import get_audio_effects_by_conversation, get_audio_effect_versions_by_audio_effect_ids
        
        # 获取所有音效
        audio_effects = await get_audio_effects_by_conversation(conversation_id, thread_id)
        if not audio_effects:
            return {}
        
        # 获取音效版本
        audio_effect_ids = [ae.uuid for ae in audio_effects]
        audio_effect_versions = await get_audio_effect_versions_by_audio_effect_ids(audio_effect_ids)
        
        # 按镜头编号组织数据
        audio_effects_by_shot = {}
        for audio_effect in audio_effects:
            # 找到该音效的最新版本
            versions = [v for v in audio_effect_versions if v.audio_effect_id == audio_effect.uuid]
            if versions:
                # 使用最新版本（版本号最大的）
                latest_version = max(versions, key=lambda x: x.version_number)
                audio_effects_by_shot[audio_effect.shot_number] = {
                    'audio_effect': audio_effect,
                    'version': latest_version
                }
        
        logger.info(f"🎵 获取到音效数据: {list(audio_effects_by_shot.keys())} 镜头")
        return audio_effects_by_shot
        
    except Exception as e:
        logger.error(f"❌ 获取音效数据失败: {e}")
        return {}


async def get_music_data_for_videos(conversation_id: str, thread_id: str) -> Dict[str, Dict[str, Any]]:
    """获取视频对应的音乐数据，按audio_segment_id组织（crud/video 使用 asyncpg，无需 db）
    
    Returns:
        Dict[str, Dict]: {audio_segment_id: {'music_generation': VideoMusicGenerationDB, 'version': VideoMusicGenerationVersionDB}}
    """
    try:
        from ....crud.video.video_audio import get_music_generations_by_latest_transcription, get_music_generation_versions_by_music_generation_ids
        
        # 仅取"最新 transcription 关联的 mg"——每次 smart_clip apply 后都会重建一批新 per-segment
        # mg 关联到新 transcription，timeline assembly 永远应该用当前生效的那一组（旧 transcription
        # 的 mg 时间轴已无效）。
        music_generations = await get_music_generations_by_latest_transcription(conversation_id, thread_id)
        if not music_generations:
            return {}
        # 业务逻辑排序：按 shot_number 升序，无 shot_number 的（整片 BGM）放最后
        music_generations = sorted(music_generations, key=lambda mg: (mg.shot_number is None, mg.shot_number or 0))
        
        # 获取音乐版本
        music_generation_ids = [mg.uuid for mg in music_generations]
        music_versions = await get_music_generation_versions_by_music_generation_ids(music_generation_ids)
        
        # 🔧 按 audio_segment_id 组织数据（而不是 shot_number）
        music_by_audio_segment = {}
        for music_generation in music_generations:
            # 找到该音乐的所有版本并按 version_number 排序，取最新
            versions = [v for v in music_versions if v.music_generation_id == music_generation.uuid]
            if versions:
                versions_sorted = sorted(versions, key=lambda x: x.version_number)
                latest_version = versions_sorted[-1]
                
                # 从 version 中获取 audio_segment_ids（与 get_music_data_by_conversation_and_thread 一致：无则用版本 uuid 作为 fallback）
                audio_segment_ids = latest_version.audio_segment_ids or []
                if not audio_segment_ids:
                    logger.warning(f"⚠️ 音乐版本 {latest_version.uuid} 没有 audio_segment_ids，使用音乐版本uuid作为audio_segment_id")
                    audio_segment_ids = [latest_version.uuid]
                
                # 一个音乐版本可能对应多个 audio_segment_ids（虽然通常只有一个）
                for audio_segment_id in audio_segment_ids:
                    music_by_audio_segment[audio_segment_id] = {
                        'music_generation': music_generation,
                        'version': latest_version
                    }
        
        logger.info(f"🎵 获取到音乐数据: {len(music_by_audio_segment)} 个音频片段")
        return music_by_audio_segment
        
    except Exception as e:
        logger.error(f"❌ 获取音乐数据失败: {e}")
        return {}


async def get_narrations_for_videos_by_run_id(run_id: str) -> Dict[int, Dict[str, Any]]:
    """根据run_id获取视频对应的旁白数据（crud/video 使用 asyncpg，无需 db）"""
    try:
        from ....crud.video.video_audio import get_narrations_by_run_id, get_narration_versions_by_narration_ids
        from ....services.agent.utils.database_utils import _select_version
        
        # 获取所有旁白
        narrations = await get_narrations_by_run_id(run_id)
        if not narrations:
            return {}
        
        # 获取旁白版本
        narration_ids = [n.uuid for n in narrations]
        narration_versions = await get_narration_versions_by_narration_ids(narration_ids)
        versions_by_narration: Dict[str, List[Any]] = {}
        for v in narration_versions:
            versions_by_narration.setdefault(v.narration_id, []).append(v)
        
        # 按镜头编号组织数据
        narrations_by_shot = {}
        for narration in narrations:
            chosen = _select_version(narration, versions_by_narration.get(narration.uuid, []))
            if not chosen or not chosen.success or not (chosen.audio_url or "").strip():
                continue
            narrations_by_shot[narration.shot_number] = {
                'narration': narration,
                'version': chosen
            }
        
        logger.info(f"🎙️ 根据run_id获取到旁白数据: {list(narrations_by_shot.keys())} 镜头")
        return narrations_by_shot
        
    except Exception as e:
        logger.error(f"❌ 根据run_id获取旁白数据失败: {e}")
        return {}


async def get_audio_effects_for_videos_by_run_id(run_id: str) -> Dict[int, Dict[str, Any]]:
    """根据run_id获取视频对应的音效数据（crud/video 使用 asyncpg，无需 db）"""
    try:
        from ....crud.video.video_audio import get_audio_effects_by_run_id, get_audio_effect_versions_by_audio_effect_ids
        
        # 获取所有音效
        audio_effects = await get_audio_effects_by_run_id(run_id)
        if not audio_effects:
            return {}
        
        # 获取音效版本
        audio_effect_ids = [ae.uuid for ae in audio_effects]
        audio_effect_versions = await get_audio_effect_versions_by_audio_effect_ids(audio_effect_ids)
        
        # 按镜头编号组织数据
        audio_effects_by_shot = {}
        for audio_effect in audio_effects:
            # 找到该音效的最新版本
            versions = [v for v in audio_effect_versions if v.audio_effect_id == audio_effect.uuid]
            if versions:
                # 使用最新版本（版本号最大的）
                latest_version = max(versions, key=lambda x: x.version_number)
                audio_effects_by_shot[audio_effect.shot_number] = {
                    'audio_effect': audio_effect,
                    'version': latest_version
                }
        
        logger.info(f"🎵 根据run_id获取到音效数据: {list(audio_effects_by_shot.keys())} 镜头")
        return audio_effects_by_shot
        
    except Exception as e:
        logger.error(f"❌ 根据run_id获取音效数据失败: {e}")
        return {}


async def get_music_data_by_run_id(run_id: str) -> Dict[str, Dict[str, Any]]:
    """根据run_id获取音乐数据，按audio_segment_id组织（crud/video 使用 asyncpg，无需 db）
    
    Returns:
        Dict[str, Dict]: {audio_segment_id: {'music_generation': VideoMusicGenerationDB, 'version': VideoMusicGenerationVersionDB}}
    """
    try:
        from ....crud.video.video_audio import get_music_generations_by_run_id, get_music_generation_versions_by_music_generation_ids
        
        # 获取所有音乐生成
        music_generations = await get_music_generations_by_run_id(run_id)
        if not music_generations:
            return {}
        # 业务逻辑排序：按 shot_number 升序，无 shot_number 的（整片 BGM）放最后
        music_generations = sorted(music_generations, key=lambda mg: (mg.shot_number is None, mg.shot_number or 0))
        
        # 获取音乐版本
        music_generation_ids = [mg.uuid for mg in music_generations]
        music_versions = await get_music_generation_versions_by_music_generation_ids(music_generation_ids)
        
        # 🔧 按 audio_segment_id 组织数据（而不是 shot_number）
        music_by_audio_segment = {}
        for music_generation in music_generations:
            # 找到该音乐的所有版本并按 updated_at 排序，取最新（用户编辑歌词后使用最新修改的版本）
            versions = [v for v in music_versions if v.music_generation_id == music_generation.uuid]
            if versions:
                versions_sorted = sorted(versions, key=lambda x: x.updated_at)
                latest_version = versions_sorted[-1]
                
                # 从 version 中获取 audio_segment_ids
                audio_segment_ids = latest_version.audio_segment_ids or []
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


async def get_music_data_by_conversation_and_thread(
    conversation_id: str, thread_id: str
) -> Dict[str, Dict[str, Any]]:
    """根据 conversation_id + thread_id 获取该 thread 下音乐数据，按 audio_segment_id 组织（用于按 thread 同步/组装）
    
    Returns:
        Dict[str, Dict]: {audio_segment_id: {'music_generation': VideoMusicGenerationDB, 'version': VideoMusicGenerationVersionDB}}
    """
    try:
        from ....crud.video.video_audio import get_music_generations_by_latest_transcription, get_music_generation_versions_by_music_generation_ids
        
        music_generations = await get_music_generations_by_latest_transcription(conversation_id, thread_id)
        if not music_generations:
            return {}
        music_generations = sorted(music_generations, key=lambda mg: (mg.shot_number is None, mg.shot_number or 0))
        
        music_generation_ids = [mg.uuid for mg in music_generations]
        music_versions = await get_music_generation_versions_by_music_generation_ids(music_generation_ids)
        
        music_by_audio_segment = {}
        for music_generation in music_generations:
            versions = [v for v in music_versions if v.music_generation_id == music_generation.uuid]
            if versions:
                versions_sorted = sorted(versions, key=lambda x: x.updated_at)
                latest_version = versions_sorted[-1]
                audio_segment_ids = latest_version.audio_segment_ids or []
                if not audio_segment_ids:
                    logger.warning(f"⚠️ 音乐版本 {latest_version.uuid} 没有 audio_segment_ids，使用音乐版本uuid作为audio_segment_id")
                    audio_segment_ids = [latest_version.uuid]
                for audio_segment_id in audio_segment_ids:
                    music_by_audio_segment[audio_segment_id] = {
                        'music_generation': music_generation,
                        'version': latest_version
                    }
        
        logger.info(f"🎵 根据 conversation+thread 获取到音乐数据: {len(music_by_audio_segment)} 个音频片段")
        return music_by_audio_segment
    except Exception as e:
        logger.error(f"❌ 根据 conversation+thread 获取音乐数据失败: {e}")
        return {}


async def execute_video_merge_core(
    assembly_data: VideoAssemblyData,
    send_event_func: Any,
    run_id: str = None,
    thread_id: str = None,
    detected_language: Optional[str] = None
) -> Dict[str, Any]:
    """视频合并核心逻辑 - 重构版本，接收封装的数据对象
    
    Args:
        assembly_data: 视频合成所需的所有数据
        send_event_func: 发送事件的函数
        run_id: 当前运行ID
        thread_id: 对话线程ID
        detected_language: 检测到的语言
        
    Returns:
        包含合成结果和源头资源信息的字典
    """
    import uuid
    from datetime import datetime
    from ....models.video_state import VideoAssembly
    from ....services.agent.base_agent import MessageType
    
    # 实际视频拼接逻辑
    final_video_url = None
    final_video_url_no_subtitle = None
    subtitle_url = None
    success = True
    error_msg = None
    actual_duration = 0.0  # 实际视频时长
    
    try:
        story_outline = assembly_data.story_outline
        
        logger.info(f"🎙️ 获取到 {len(assembly_data.narrations_data)} 个旁白，{len(assembly_data.audio_effects_data)} 个音效")
        logger.info(f"🎬 拼接模式: {assembly_data.assembly_mode}")
        
        # 根据音乐类型和歌词情况决定拼接策略
        assembly_result = await execute_assembly_strategy(
            assembly_data, story_outline
        )
        
        # ✅ 处理返回结果（可能是字符串或字典）
        subtitle_url = None
        if isinstance(assembly_result, dict):
            final_video_url = assembly_result.get("final_url")
            final_video_url_no_subtitle = assembly_result.get("final_no_subtitle_url")
            subtitle_url = assembly_result.get("subtitle_url")
        else:
            # 兼容旧模式（直接返回字符串）
            final_video_url = assembly_result
            final_video_url_no_subtitle = None
        
        # 🔧 计算实际视频时长（从视频片段或视频生成中累加）
        if assembly_data.video_segments_data:
            # Audio-driven 模式：使用 video_segments_data 的时长（防御：值须为 dict）
            for segment_data in assembly_data.video_segments_data.values():
                if not isinstance(segment_data, dict):
                    continue
                version = segment_data.get('version')
                if version and hasattr(version, 'duration'):
                    actual_duration += version.duration or 0.0
            logger.info(f"⏱️ 实际视频时长（来自 video_segments）: {actual_duration:.2f}s")
        else:
            # Video-driven 模式：使用 video_generations 的时长
            for video_gen in assembly_data.video_generations:
                if video_gen.success and video_gen.duration:
                    actual_duration += video_gen.duration
            logger.info(f"⏱️ 实际视频时长（来自 video_generations）: {actual_duration:.2f}s")
        
        logger.info(f"🎞️ 视频拼接完成: {final_video_url}")
        if final_video_url_no_subtitle:
            logger.info(f"🎞️ 无字幕版本: {final_video_url_no_subtitle}")
    except Exception as concat_error:
        logger.error(f"❌ 视频拼接失败: {concat_error}")
        success = False
        error_msg = str(concat_error)
        # 生成一个模拟的结果
        final_video_url = f"/api/videos/final_{uuid.uuid4().hex[:16]}.mp4"
        final_video_url_no_subtitle = None
        logger.warning(f"⚠️ 使用模拟视频URL: {final_video_url}")
    
    # 收集源头资源信息
    source_video_versions = {}
    source_video_urls = {}
    source_narration_versions = {}
    source_narration_urls = {}
    source_audio_effect_versions = {}
    source_audio_effect_urls = {}
    
    # 收集视频版本信息
    for video_gen in assembly_data.video_generations:
        if video_gen.success and video_gen.video_url:
            shot_number = video_gen.shot_number
            source_video_versions[shot_number] = video_gen.version_id
            source_video_urls[shot_number] = video_gen.video_url
    
    # 收集旁白版本信息（防御：值须为 dict 且含 'version'）
    for shot_number, narration_data in assembly_data.narrations_data.items():
        if not isinstance(narration_data, dict):
            continue
        version = narration_data.get('version')
        if version and getattr(version, 'audio_url', None):
            source_narration_versions[shot_number] = version.uuid
            source_narration_urls[shot_number] = version.audio_url
    
    # 收集音效版本信息（防御：值须为 dict 且含 'version'）
    for shot_number, audio_effect_data in assembly_data.audio_effects_data.items():
        if not isinstance(audio_effect_data, dict):
            continue
        version = audio_effect_data.get('version')
        if version and getattr(version, 'audio_url', None):
            source_audio_effect_versions[shot_number] = version.uuid
            source_audio_effect_urls[shot_number] = version.audio_url
    
    # 收集音乐版本信息（按 audio_segment_id 索引）（防御：值可能是字符串）
    source_music_versions = {}
    source_music_urls = {}
    for audio_segment_id, music_item in assembly_data.music_data.items():
        if not isinstance(music_item, dict):
            continue
        if music_item.get('version') and music_item['version'].success and music_item['version'].music_url:
            source_music_versions[audio_segment_id] = music_item['version'].uuid
            source_music_urls[audio_segment_id] = music_item['version'].music_url
    
    # 创建VideoAssembly对象
    video_assembly = VideoAssembly(
        final_video_url=final_video_url,
        final_video_url_no_subtitle=final_video_url_no_subtitle,
        total_duration=actual_duration,  # 🔧 使用实际视频时长，而不是 story_outline 的时长
        success=success,
        error_msg=error_msg,
        story_outline_id=story_outline.uuid,
        
        # 源头资源版本ID
        source_video_versions=source_video_versions,
        source_narration_versions=source_narration_versions,
        source_audio_effect_versions=source_audio_effect_versions,
        source_music_versions=source_music_versions,
        
        # 源头资源URL
        source_video_urls=source_video_urls,
        source_narration_urls=source_narration_urls,
        source_audio_effect_urls=source_audio_effect_urls,
        source_music_urls=source_music_urls,
        uploaded_audio_files=assembly_data.uploaded_audio_files,
        
        # 拼接模式记录
        assembly_mode=assembly_data.assembly_mode,
        
        # 元数据
        title=story_outline.title,
        created_at=datetime.now().isoformat(),
        additional_data={
            "has_audio_transcription": bool(assembly_data.audio_transcription),
            "video_count": len(assembly_data.video_urls),
            "narration_count": len(assembly_data.narrations_data),
            "audio_effect_count": len(assembly_data.audio_effects_data)
        }
    )
    
    # 保存到数据库
    from ....crud.video.video_other import create_video_assembly
    
    # 确保story_outline_id不为None
    story_outline_id = story_outline.uuid
    if not story_outline_id:
        logger.error(f"❌ story_outline.uuid为空，无法创建视频合成记录")
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "故事大纲UUID为空，无法创建视频合成记录"
        )
    
    additional_data_for_db = {
        "has_audio_transcription": bool(assembly_data.audio_transcription),
        "video_count": len(assembly_data.video_urls),
        "narration_count": len(assembly_data.narrations_data),
        "audio_effect_count": len(assembly_data.audio_effects_data),
    }
    if subtitle_url:
        additional_data_for_db["subtitle_url"] = subtitle_url

    assembly_uuid = await create_video_assembly(
        final_video_url=final_video_url,
        final_video_url_no_subtitle=final_video_url_no_subtitle,
        total_duration=float(story_outline.total_duration),
        success=success,
        conversation_id=story_outline.conversation_id,
        thread_id=story_outline.thread_id,
        run_id=story_outline.run_id or "",
        user_id=story_outline.user_id or "",
        story_outline_id=story_outline_id,
        error_msg=error_msg,
        additional_data=additional_data_for_db,
        # 新的源头资源字段
        source_video_versions=source_video_versions,
        source_narration_versions=source_narration_versions,
        source_audio_effect_versions=source_audio_effect_versions,
        source_music_versions=source_music_versions,
        source_video_urls=source_video_urls,
        source_narration_urls=source_narration_urls,
        source_audio_effect_urls=source_audio_effect_urls,
        source_music_urls=source_music_urls,
        uploaded_audio_files=assembly_data.uploaded_audio_files,
        assembly_mode=assembly_data.assembly_mode
    )
    
    # 获取 conversation_uuid
    conversation_uuid = None
    if story_outline.conversation_id:
        from ....crud.conversation import async_get_conversation_by_id
        try:
            conversation = await async_get_conversation_by_id(int(story_outline.conversation_id))
            conversation_uuid = conversation.uuid if conversation else None
        except Exception as e:
            logger.warning(f"获取 conversation_uuid 失败: {e}")
    
    # 发送事件
    await send_event_func(
        conversation_id=int(story_outline.conversation_id),
        conversation_uuid=conversation_uuid,
        event_type=MessageType.VIDEO_COMPLETED,
        message=await get_i18n_message_async("video.completed", default="视频拼接完成", lang=detected_language),
        extra_data={
            "video_assembly_uuid": assembly_uuid,
            "final_video_url": final_video_url,
            "total_duration": story_outline.total_duration,
            "video_count": len(assembly_data.video_urls),
            "video_title": story_outline.title,
            "run_id": run_id,
            "thread_id": thread_id
        }
    )
    
    return {
        "assembly_uuid": assembly_uuid,
        "final_video_url": final_video_url,
        "success": success
    }


async def execute_assembly_strategy(
    assembly_data: VideoAssemblyData,
    story_outline: Any
) -> str:
    """根据音乐来源执行相应的拼接策略
    
    两种模式：
    1. 上传音频模式（用户上传的音频文件）：以音乐片段为主，不添加旁白和音效
    2. Suno生成模式（AI生成的背景音乐）：以旁白为主，音乐作为背景
    """
    
    is_audio_driven = assembly_data.audio_transcription is not None
    
    if is_audio_driven:
        # 模式1：上传音频 - 使用 segment 结果 + 完整音频合并
        logger.info(f"🎵 上传音频模式：使用视频片段结果进行拼接")
        return await concatenate_segments_with_music_pieces(
            assembly_data, story_outline.title
        )
    else:
        # 模式2：Suno生成 - 旁白为主，音乐作为背景
        logger.info(f"🎙️ Suno生成模式：以旁白为主，音乐作为背景")
        
        # 先进行旁白驱动的拼接
        final_video_url = await concatenate_segments_narration_driven(
            assembly_data, story_outline.title
        )
        
        # 如果有音乐数据，添加背景音乐
        if assembly_data.music_data and len(assembly_data.music_data) > 0:
            final_video_url = await add_background_music_to_video(
                final_video_url, assembly_data, story_outline.title
            )
        
        return final_video_url


async def add_background_music_to_video(
    video_url: str,
    assembly_data: VideoAssemblyData,
    title: str
) -> str:
    """为视频添加背景音乐"""
    try:
        # 获取背景音乐（Suno 生成的整段背景音乐，通常只有一个）
        background_music = None
        if assembly_data.music_data:
            # 直接获取第一个音乐（music_data 现在以 audio_segment_id 为 key）（防御：值可能是字符串）
            first_key = next(iter(assembly_data.music_data.keys()))
            background_music = assembly_data.music_data[first_key]
            if not isinstance(background_music, dict):
                background_music = None
        
        if not background_music or not background_music.get('version') or not background_music['version'].success:
            logger.warning("🎵 没有找到有效的背景音乐，返回原视频")
            return video_url
        
        music_version = background_music['version']
        music_generation = background_music['music_generation']
        music_url = music_version.music_url if music_generation.is_full_story_music else music_version.original_audio_url
        
        if not music_url:
            logger.warning("🎵 背景音乐 URL 为空，返回原视频")
            return video_url
        
        logger.info(f"🎵 为视频添加背景音乐: {music_url}")
        
        # 使用视频合并工具添加背景音乐
        from ....utils.video_utils import add_background_music_to_video as add_bg_music
        
        return await add_bg_music(
            video_url=video_url,
            music_url=music_url,
            title=f"{title}_with_music"
        )
        
    except Exception as e:
        logger.error(f"❌ 添加背景音乐失败: {e}")
        return video_url  # 失败时返回原视频


async def _burn_subtitles_onto_video_url(
    video_url: str,
    subtitle_segments: List[SubtitleSegment],
    title: str,
) -> Tuple[Optional[str], Optional[str]]:
    """将 ASS 字幕烧录到视频并上传 S3。返回 (带字幕视频 URL, 字幕文件 URL)。"""
    if not video_url or not subtitle_segments:
        return None, None

    import tempfile
    from ....utils.s3_utils import s3_utils

    temp_dir = tempfile.mkdtemp(prefix="subtitle_burn_")
    subtitle_path = None
    input_path = None
    output_path = os.path.join(temp_dir, f"{title}_with_subtitle.mp4")

    def _read_bytes(path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    try:
        subtitle_path = create_temp_subtitle_file(subtitle_segments)
        input_path = await _download_media_to_temp(video_url, temp_dir, MediaType.VIDEO)
        if not input_path or not os.path.exists(input_path):
            logger.warning("⚠️ 字幕烧录：视频下载失败，跳过")
            return None, None

        cmd = add_subtitles_to_video_command(input_path, subtitle_path, output_path)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        if process.returncode != 0 or not os.path.exists(output_path):
            err = stderr.decode() if stderr else "Unknown error"
            logger.error(f"❌ 字幕烧录 ffmpeg 失败: {err}")
            return None, None

        video_bytes = await asyncio.to_thread(_read_bytes, output_path)
        burned_url = await s3_utils.upload_file(
            video_bytes,
            f"video_assembly/{uuid.uuid4().hex[:16]}_{title}_with_subtitle.mp4",
            "video/mp4",
        )

        subtitle_url = None
        if subtitle_path and os.path.exists(subtitle_path):
            ass_bytes = await asyncio.to_thread(_read_bytes, subtitle_path)
            subtitle_url = await s3_utils.upload_file(
                ass_bytes,
                f"subtitle/{uuid.uuid4().hex[:16]}_{title}.ass",
                "text/plain; charset=utf-8",
            )

        if burned_url:
            logger.info(f"✅ 字幕烧录完成: {burned_url}")
        return burned_url, subtitle_url
    except Exception as e:
        logger.error(f"❌ 字幕烧录失败: {e}")
        return None, None
    finally:
        for p in (subtitle_path, input_path, output_path):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        try:
            os.rmdir(temp_dir)
        except OSError:
            pass


async def concatenate_segments_narration_driven(
    assembly_data: VideoAssemblyData,
    title: str
) -> Union[str, Dict[str, Any]]:
    """旁白驱动的片段拼接：以旁白为主，音乐作为背景。

    无旁白/无音效（video_driven / Short Drama）：MSC video_concat 在混有/无音轨时会丢弃
    音轨，因此走 extract→timeline→mix 保片内声（空镜垫静音）。
    Product Launch：旁白拼接完成后返回无字幕成片（talking head 不烧字幕）。
    """
    from ....utils.video_utils import (
        concatenate_videos_with_narration_and_effects,
        concatenate_videos_silent_with_narration_at_end,
    )
    from .voice_delivery_contract import should_preserve_video_audio_in_assemble
    if not assembly_data.narrations_data and not assembly_data.audio_effects_data:
        # 保留 Seedance 片内声（对白/SFX）；MSC 直 concat 会丢掉混轨音频
        no_subtitle_url, _ = await concatenate_videos_silent_with_narration_at_end(
            assembly_data.video_urls,
            assembly_data.video_generations,
            {},
            title,
            preserve_video_audio=True,
        )
        logger.info(
            "✅ Video-driven concat preserve in-clip audio: %s",
            no_subtitle_url,
        )
        return no_subtitle_url
    use_silent_concat_then_mix = (
        assembly_data.narrations_data
        and not assembly_data.audio_effects_data
        and not assembly_data.music_data
    )
    shot_timeline = None
    try:
        if use_silent_concat_then_mix:
            # 旁白稀疏 → 保留片内对白声；Product Launch 全镜旁白 → 仍 mute 视频轨
            # 另：任一镜为 Seedance 片内对白时必须保轨（避免全镜误标旁白把对白静音）
            nar_shots = {
                sn for sn, nd in (assembly_data.narrations_data or {}).items()
                if nd and (nd.get("version") or None)
                and getattr(nd.get("version"), "success", False)
                and (getattr(nd.get("version"), "audio_url", None) or "").strip()
            }
            all_shots = {vg.shot_number for vg in assembly_data.video_generations}
            preserve_video_audio = bool(nar_shots) and nar_shots != all_shots
            if should_preserve_video_audio_in_assemble(assembly_data.video_generations):
                preserve_video_audio = True
            logger.info(
                "🎙️ 旁白混音 concat: narrated=%s all=%s preserve_video_audio=%s",
                sorted(nar_shots), sorted(all_shots), preserve_video_audio,
            )
            no_subtitle_url, shot_timeline = await concatenate_videos_silent_with_narration_at_end(
                assembly_data.video_urls,
                assembly_data.video_generations,
                assembly_data.narrations_data,
                title,
                preserve_video_audio=preserve_video_audio,
            )
        else:
            no_subtitle_url = await concatenate_videos_with_narration_and_effects(
                assembly_data.video_urls,
                assembly_data.video_generations,
                assembly_data.narrations_data,
                assembly_data.audio_effects_data,
                title,
            )
    except Exception as e:
        logger.error(f"❌ 旁白驱动视频合成失败: {e}")
        if assembly_data.narrations_data:
            raise
        return await concatenate_video_segments_by_urls(assembly_data.video_urls, title)

    if not assembly_data.narrations_data:
        return {
            "final_url": no_subtitle_url,
            "final_no_subtitle_url": no_subtitle_url,
            "subtitle_url": None,
        }

    # Product Launch / 纯旁白 talking head：不烧录字幕
    if use_silent_concat_then_mix:
        logger.info("🎙️ Product Launch talking head：跳过字幕烧录")
        return {
            "final_url": no_subtitle_url,
            "final_no_subtitle_url": no_subtitle_url,
            "subtitle_url": None,
        }

    subtitle_segments = create_subtitle_segments_from_narrations(
        assembly_data.narrations_data,
        shot_timeline=shot_timeline,
    )
    if not subtitle_segments:
        return {
            "final_url": no_subtitle_url,
            "final_no_subtitle_url": no_subtitle_url,
            "subtitle_url": None,
        }

    burned_url, subtitle_url = await _burn_subtitles_onto_video_url(
        no_subtitle_url,
        subtitle_segments,
        title,
    )
    if burned_url:
        return {
            "final_url": burned_url,
            "final_no_subtitle_url": no_subtitle_url,
            "subtitle_url": subtitle_url,
        }

    logger.warning("⚠️ 字幕烧录失败，回退为无字幕成片")
    return {
        "final_url": no_subtitle_url,
        "final_no_subtitle_url": no_subtitle_url,
        "subtitle_url": None,
    }


async def _prepare_subtitle_file(assembly_data: VideoAssemblyData, shot_audio_mapping: dict, temp_dir: str) -> Optional[str]:
    """
    准备字幕文件
    
    Args:
        assembly_data: 视频组装数据
        shot_audio_mapping: 镜头音频映射
        temp_dir: 临时目录
        
    Returns:
        字幕文件路径，如果失败返回None
    """
    subtitle_file_path = None
    try:
        logger.info("🎬 实时生成字幕文件")
        if assembly_data.narrations_data:
            subtitle_segments = create_subtitle_segments_from_narrations(assembly_data.narrations_data)
        else:
            subtitle_segments = create_subtitle_segments_from_audio_mapping(shot_audio_mapping, assembly_data.music_data)
        if subtitle_segments:
            subtitle_file_path = create_temp_subtitle_file(subtitle_segments)
            logger.info(f"🎬 字幕文件已生成: {subtitle_file_path}")
        else:
            logger.info("🎬 没有找到适合的字幕文本，跳过字幕生成")
                
    except Exception as e:
        logger.warning(f"⚠️ 字幕生成失败: {e}")
        subtitle_file_path = None
    
    return subtitle_file_path


# 成片增量 concat + 前缀漂移截短：每 CHUNK 段把「已合并前缀」与下一段拼接；probe(前缀) 比累计 DB target 长出阈值则 trim_only 截短。
# 仅缩短、不调速、不垫片；MSC trim_only 走 hybrid 时可能对尾部少量重编码，不是整条重编码；concat 仍优先 -c copy。
ASSEMBLY_INCREMENTAL_CONCAT_CHUNK_SIZE = 8
ASSEMBLY_PREFIX_DRIFT_TRIM_TRIGGER_SEC = 0.05
ASSEMBLY_PREFIX_DRIFT_HYSTERESIS_SEC = 0.02
ASSEMBLY_PREFIX_TRIM_MIN_INTERVAL_SEGMENTS = 4
ASSEMBLY_PREFIX_DRIFT_EMERGENCY_TRIM_SEC = 0.15
ASSEMBLY_PREFIX_MAX_TRIM_DELTA_SEC = 1.5
ASSEMBLY_PREFIX_TRIM_TOLERANCE_FORCE = 0.0


async def _incremental_concat_with_prefix_drift_trim(
    pieces: List[Tuple[int, float, str, float, int]],
    run_id: str,
    lipsync_sns: Optional[set] = None,
    fps: float = 24.0,
) -> str:
    """
    pieces: (segment_number, db_target_duration, url, pre_probed_duration, nb_frames)
    返回合并后的视频 URL。

    整数帧前缀漂移纠偏：concat -c copy 逐帧堆叠 → 成片帧数 == 各段 nb_frames 之和（精确可加，
    已实测验证）。因此「已合并前缀」的帧数可由各段 nb_frames 解析累加得到，无需每个 chunk 后
    re-probe（省去成片越来越大、下载越来越慢的 video_info 往返，是主要的提速点）。
    裁剪按整数帧落点（trim 到 trim_to_frames/fps 秒）；并「优先裁非口型边界」：若 chunk 边界段
    是口型段且未达紧急阈值，则推迟到下一个非口型边界再裁，避免裁到口型段尾帧影响对嘴形；仅当
    累计漂移达到紧急阈值时才允许在口型边界裁（防止漂移失控）。
    触发/紧急阈值沿用既有秒级常量（drift_sec = drift_frames/fps 后比较），保持既有调参手感。
    """
    if not pieces:
        raise ValueError("incremental concat: empty pieces")
    lipsync_sns = lipsync_sns or set()
    fps = fps if (fps and fps > 0) else 24.0
    merged_url: Optional[str] = None
    i = 0
    n = len(pieces)
    chunk = max(1, ASSEMBLY_INCREMENTAL_CONCAT_CHUNK_SIZE)
    last_trim_end_i: Optional[int] = None
    max_trim_frames = max(0, round(ASSEMBLY_PREFIX_MAX_TRIM_DELTA_SEC * fps))
    running_frames = 0  # 已合并前缀累计帧数（裁剪后重置为 trim_to_frames）
    while i < n:
        end = min(i + chunk, n)
        batch_urls = [pieces[j][2] for j in range(i, end)]
        sub_id = f"{run_id}_inc{i}_{end}"
        if merged_url is None:
            concat_result = await msc.video_concat(batch_urls, run_id=sub_id, normalize=False)
        else:
            concat_result = await msc.video_concat([merged_url] + batch_urls, run_id=sub_id, normalize=False)
        merged_url = concat_result["result_url"]
        # 解析累加帧数（不再 re-probe）：concat 成片帧数 == 各段 nb_frames 之和
        running_frames += sum(int(pieces[j][4] or 0) for j in range(i, end))
        i = end

        cum_target_sec = sum(pieces[j][1] for j in range(0, i))
        cum_target_frames = round(cum_target_sec * fps)
        drift_frames = running_frames - cum_target_frames
        drift_sec = drift_frames / fps
        boundary_sn = pieces[i - 1][0]
        is_lip = boundary_sn in lipsync_sns

        emergency = drift_sec > ASSEMBLY_PREFIX_DRIFT_EMERGENCY_TRIM_SEC
        since_trim = (
            (i - last_trim_end_i)
            if last_trim_end_i is not None
            else ASSEMBLY_PREFIX_TRIM_MIN_INTERVAL_SEGMENTS
        )
        effective_trigger = ASSEMBLY_PREFIX_DRIFT_TRIM_TRIGGER_SEC
        if (
            last_trim_end_i is not None
            and since_trim < ASSEMBLY_PREFIX_TRIM_MIN_INTERVAL_SEGMENTS
            and not emergency
        ):
            effective_trigger = ASSEMBLY_PREFIX_DRIFT_TRIM_TRIGGER_SEC + ASSEMBLY_PREFIX_DRIFT_HYSTERESIS_SEC
        logger.info(
            "📐 含 %d 段(整帧): 累计帧=%d 目标帧=%d drift=%d帧(%+.3fs) trigger=%.3fs 边界段#%d %s",
            i, running_frames, cum_target_frames, drift_frames, drift_sec,
            effective_trigger, boundary_sn, ("口型段" if is_lip else "非口型段"),
        )
        if drift_sec <= effective_trigger:
            continue
        # 优先裁非口型边界：边界是口型段且未达紧急阈值 → 推迟，保护对嘴形
        if is_lip and not emergency:
            logger.info(
                "⏭️ 边界段#%d 为口型段且未紧急(drift=%+.3fs)，推迟裁剪到下一个非口型边界以保护对嘴形",
                boundary_sn, drift_sec,
            )
            continue
        if emergency:
            trim_to_frames = cum_target_frames
        else:
            trim_to_frames = max(cum_target_frames, running_frames - max_trim_frames)
        cut_frames = running_frames - trim_to_frames
        if cut_frames <= 0:
            continue
        trim_to_sec = trim_to_frames / fps
        logger.info(
            "🔧 前缀漂移纠偏(整数帧 trim_only): 已含 %d 段 目标帧=%d 当前帧=%d cut=%d帧(%.3fs) "
            "→ trim_to=%.3fs (emergency=%s, 边界段#%d %s)",
            i, cum_target_frames, running_frames, cut_frames, cut_frames / fps,
            trim_to_sec, emergency, boundary_sn, ("⚠️口型段" if is_lip else "非口型段"),
        )
        trim_result = await msc.video_trim(
            merged_url,
            trim_to_sec,
            run_id=f"{sub_id}_pt",
            mode="trim_only",
            tolerance=ASSEMBLY_PREFIX_TRIM_TOLERANCE_FORCE,
        )
        merged_url = trim_result["result_url"]
        running_frames = trim_to_frames
        last_trim_end_i = i
    assert merged_url is not None
    return merged_url


def _dryrun_prefix_drift_trim_plan(
    pieces: List[Tuple[int, float, str, float]],
    fps_hint: float = 24.0,
) -> None:
    """仅计算、打日志：不调用 MSC、不下载、不改变任何合并行为。

    用 inc_pieces 里每段的 pre_probed_duration 解析推演「前缀漂移纠偏」会在哪些
    chunk 边界裁、各裁多少帧；与真实 _incremental_concat_with_prefix_drift_trim
    的 "🔧 前缀漂移纠偏" 实测日志逐条对照，用于验证解析法是否足够准（够准才考虑
    用它替换 O(n^2) 的重拼测量）。

    解析模型：running = 用 pre_probed 累加得到的「已合并前缀」时长；一旦推演触发
    裁剪，running 重置为 trim_to，后续段在其上继续累加（与真实算法对 merged_url
    的 video_info 行为一致）。fps_hint 仅用于把秒换算成帧便于直觉，默认 24。
    """
    if not pieces:
        logger.info("🧪 [dryrun] 无 pieces，跳过推演")
        return
    n = len(pieces)
    chunk = max(1, ASSEMBLY_INCREMENTAL_CONCAT_CHUNK_SIZE)
    running = 0.0
    i = 0
    last_trim_end_i: Optional[int] = None
    trims: List[Tuple[int, float]] = []
    logger.info(
        "🧪 [dryrun] 解析推演前缀漂移裁剪计划开始 (segments=%d, chunk=%d, fps_hint=%.3f)",
        n, chunk, fps_hint,
    )
    while i < n:
        end = min(i + chunk, n)
        # 整帧量化预测：每段对 concat 的贡献 ≈ round(db_target*fps)/fps（pre_probed≈db_target，预测不出漂移）
        batch_probed = sum(round(pieces[j][1] * fps_hint) for j in range(i, end)) / fps_hint
        running += batch_probed
        i = end
        cum_target = sum(pieces[j][1] for j in range(0, i))
        drift = running - cum_target
        emergency = drift > ASSEMBLY_PREFIX_DRIFT_EMERGENCY_TRIM_SEC
        since_trim = (
            (i - last_trim_end_i)
            if last_trim_end_i is not None
            else ASSEMBLY_PREFIX_TRIM_MIN_INTERVAL_SEGMENTS
        )
        effective_trigger = ASSEMBLY_PREFIX_DRIFT_TRIM_TRIGGER_SEC
        if (
            last_trim_end_i is not None
            and since_trim < ASSEMBLY_PREFIX_TRIM_MIN_INTERVAL_SEGMENTS
            and not emergency
        ):
            effective_trigger = (
                ASSEMBLY_PREFIX_DRIFT_TRIM_TRIGGER_SEC + ASSEMBLY_PREFIX_DRIFT_HYSTERESIS_SEC
            )
        if drift <= effective_trigger:
            logger.info(
                "🧪 [dryrun] 含 %d 段: cum_target=%.3fs running(整帧预测)=%.3fs drift=%+.3fs "
                "(trigger=%.3f) → 不裁",
                i, cum_target, running, drift, effective_trigger,
            )
            continue
        if emergency:
            trim_to = cum_target
        else:
            trim_to = max(cum_target, running - ASSEMBLY_PREFIX_MAX_TRIM_DELTA_SEC)
        cut_delta = running - trim_to
        if cut_delta <= 0:
            logger.info(
                "🧪 [dryrun] 含 %d 段: drift=%+.3fs 超阈值但 cut_delta<=0 → 不裁",
                i, drift,
            )
            continue
        logger.info(
            "🧪 [dryrun] 含 %d 段: cum_target=%.3fs running(整帧预测)=%.3fs drift=%+.3fs "
            "→ trim_to=%.3fs cut=%.3fs(≈%d帧@%.0ffps) emergency=%s",
            i, cum_target, running, drift, trim_to, cut_delta,
            round(cut_delta * fps_hint), fps_hint, emergency,
        )
        trims.append((i, cut_delta))
        running = trim_to
        last_trim_end_i = i
    total_cut = sum(c for _, c in trims)
    final_target = sum(p[1] for p in pieces)
    logger.info(
        "🧪 [dryrun] 计划完成: 触发裁剪 %d 次, 累计裁 %.3fs(≈%d帧@%.0ffps); "
        "解析最终前缀=%.3fs vs DB累计目标=%.3fs (Δ=%+.3fs)。"
        "请与真实 '🔧 前缀漂移纠偏' 日志对照裁剪次数/位置/帧数是否一致。",
        len(trims), total_cut, round(total_cut * fps_hint), fps_hint,
        running, final_target, running - final_target,
    )


def _dryrun_integer_frame_trim_plan(
    rows: List[Tuple[int, float, int]],
    lipsync_sns: set,
    fps: float,
) -> None:
    """仅计算、打日志：用真实 nb_frames 做「整数帧」前缀漂移预测，并标注每个裁剪边界段是否口型段。
    不调用 MSC、不下载、不改变任何合并行为。

    与 _dryrun_prefix_drift_trim_plan（按 round(db*fps) 近似）不同，这里用 MSC video/info
    返回的真实 nb_frames：因为 concat -c copy 逐帧堆叠，成片帧数 == 各段帧数和（无容器残差），
    所以整数帧预测对 concat 漂移是「精确」的，可与真实 '🔧 前缀漂移纠偏' 日志逐条对照。

    阈值仍用既有的秒级常量（drift_sec=drift_frames/fps 后比较），裁剪量按整帧落点；
    口型段标注用于后续「优先裁非口型段」的安全决策——本步仅打日志、不据此改裁剪位置。

    rows: [(segment_number, db_target_sec, nb_frames)]，按 concat 顺序。
    nb_frames<=0 时回退 round(db_target*fps)（MSC 尚未部署 nb_frames 的情况）。
    """
    if not rows or fps <= 0:
        logger.info("🧪 [frameplan] 跳过：无数据或 fps 非法 (fps=%.3f)", fps)
        return
    n = len(rows)
    chunk = max(1, ASSEMBLY_INCREMENTAL_CONCAT_CHUNK_SIZE)
    max_trim_frames = max(0, round(ASSEMBLY_PREFIX_MAX_TRIM_DELTA_SEC * fps))
    all_real = all(r[2] > 0 for r in rows)
    nb_source = "MSC nb_frames(精确)" if all_real else "round(db*fps)回退(MSC可能未部署nb_frames)"
    logger.info(
        "🧪 [frameplan] 整数帧预测开始 (segments=%d, chunk=%d, fps=%.3f, trigger=%.3fs, 帧源=%s)",
        n, chunk, fps, ASSEMBLY_PREFIX_DRIFT_TRIM_TRIGGER_SEC, nb_source,
    )

    def _frames_of(idx: int) -> int:
        f = rows[idx][2]
        return f if f > 0 else round(rows[idx][1] * fps)

    running_frames = 0  # 已合并前缀累计帧数（裁剪后重置为 trim_to_frames）
    i = 0
    last_trim_end_i: Optional[int] = None
    trims: List[Tuple[int, int, bool]] = []
    while i < n:
        end = min(i + chunk, n)
        for j in range(i, end):
            running_frames += _frames_of(j)
        i = end
        cum_target_frames = round(sum(rows[j][1] for j in range(0, i)) * fps)
        drift_frames = running_frames - cum_target_frames
        drift_sec = drift_frames / fps
        boundary_sn = rows[i - 1][0]
        is_lip = boundary_sn in lipsync_sns
        lip_tag = "⚠️口型段" if is_lip else "非口型段"
        emergency = drift_sec > ASSEMBLY_PREFIX_DRIFT_EMERGENCY_TRIM_SEC
        since_trim = (
            (i - last_trim_end_i)
            if last_trim_end_i is not None
            else ASSEMBLY_PREFIX_TRIM_MIN_INTERVAL_SEGMENTS
        )
        effective_trigger = ASSEMBLY_PREFIX_DRIFT_TRIM_TRIGGER_SEC
        if (
            last_trim_end_i is not None
            and since_trim < ASSEMBLY_PREFIX_TRIM_MIN_INTERVAL_SEGMENTS
            and not emergency
        ):
            effective_trigger = (
                ASSEMBLY_PREFIX_DRIFT_TRIM_TRIGGER_SEC + ASSEMBLY_PREFIX_DRIFT_HYSTERESIS_SEC
            )
        if drift_sec <= effective_trigger:
            logger.info(
                "🧪 [frameplan] 含 %d 段: 累计帧=%d 目标帧=%d drift=%d帧(%+.3fs) (trigger=%.3fs) → 不裁 | 边界段#%d %s",
                i, running_frames, cum_target_frames, drift_frames, drift_sec,
                effective_trigger, boundary_sn, lip_tag,
            )
            continue
        if emergency:
            trim_to_frames = cum_target_frames
        else:
            trim_to_frames = max(cum_target_frames, running_frames - max_trim_frames)
        cut_frames = running_frames - trim_to_frames
        if cut_frames <= 0:
            logger.info(
                "🧪 [frameplan] 含 %d 段: drift=%d帧(%+.3fs) 超阈值但 cut<=0 → 不裁 | 边界段#%d %s",
                i, drift_frames, drift_sec, boundary_sn, lip_tag,
            )
            continue
        logger.info(
            "🧪 [frameplan] 含 %d 段: 累计帧=%d 目标帧=%d drift=%d帧(%+.3fs) → 裁到%d帧, "
            "cut=%d帧(%.3fs) emergency=%s | 边界段#%d %s",
            i, running_frames, cum_target_frames, drift_frames, drift_sec,
            trim_to_frames, cut_frames, cut_frames / fps, emergency,
            boundary_sn, ("⚠️口型段(后续建议改裁邻近非口型段)" if is_lip else "非口型段(安全)"),
        )
        trims.append((i, cut_frames, is_lip))
        running_frames = trim_to_frames
        last_trim_end_i = i
    total_cut_frames = sum(c for _, c, _ in trims)
    lip_trims = sum(1 for _, _, l in trims if l)
    final_target_frames = round(sum(r[1] for r in rows) * fps)
    logger.info(
        "🧪 [frameplan] 计划完成: 触发裁剪 %d 次(其中落在口型段 %d 次), 累计裁 %d帧(%.3fs); "
        "预测最终前缀=%d帧 vs DB目标=%d帧 (Δ=%+d帧)。"
        "请与真实 '🔧 前缀漂移纠偏' 对照裁剪次数/位置/帧数。",
        len(trims), lip_trims, total_cut_frames, total_cut_frames / fps,
        running_frames, final_target_frames, running_frames - final_target_frames,
    )


async def concatenate_segments_with_music_pieces(
    assembly_data: VideoAssemblyData,
    title: str
) -> str:
    """上传音频模式：使用 segment service 的结果，合并 segments 并添加完整音频

    新流程（基于 segment service）：
    1. 从 video_segments_data 获取每个  segment 的结果
    2. 成功的 segment：直接使用（已经调整过 fps）
    3. 失败的 segment：生成黑屏占位（时长 = 音频片段时长）
    4. 合并所有 segment 视频
    5. 下载完整音频，调整视频时长匹配音频（微调）
    6. 替换视频音频（移除 lipsync 声音，只用完整音频）

    优点：
    - 使用 segment service 的结果，避免重复处理
    - 使用完整音频，避免音频片段合并导致的割裂感
    - 以音频为准调整视频时长，确保完全匹配
    - 失败的 segment 用黑屏占位，保证音频对齐
    """
    import os
    import uuid
    import tempfile
    from ....utils.video_utils import create_black_placeholder_video, get_video_duration, get_audio_duration_from_url

    if not assembly_data.music_data or len(assembly_data.music_data) == 0:
        logger.warning("🎵 没有找到音乐数据，使用标准拼接")
        return await concatenate_video_segments_by_urls(assembly_data.video_urls, title)

    # 🔧 获取完整音频 URL（audio-driven 模式）
    complete_audio_url = None
    complete_audio_duration = 0.0

    if assembly_data.audio_transcription and assembly_data.audio_transcription.audio_url:
        complete_audio_url = assembly_data.audio_transcription.audio_url
        complete_audio_duration = assembly_data.audio_transcription.duration
        logger.info(f"🎵 使用完整音频: {complete_audio_url}")
        logger.info(f"🎵 完整音频时长: {complete_audio_duration:.2f}s")
    else:
        logger.warning("⚠️ 没有找到完整音频 URL (audio_transcription.audio_url)，回退到标准拼接")
        return await concatenate_video_segments_by_urls(assembly_data.video_urls, title)

    # 🔧 检查是否有 video_segments_data
    if not assembly_data.video_segments_data or len(assembly_data.video_segments_data) == 0:
        logger.warning("⚠️ 没有找到 video_segments_data，回退到标准拼接")
        return await concatenate_video_segments_by_urls(assembly_data.video_urls, title)

    logger.info(f"🎵 Audio-driven 模式：使用 segment service 的结果")
    logger.info(f"📊 Segments 数量: {len(assembly_data.video_segments_data)}")

    # 🔧 从 video_segments_data 获取 segment 信息
    # video_segments_data: {segment_number: {'segment': VideoSegmentDB, 'version': VideoSegmentVersionDB}}
    segment_mapping = {}  # {segment_number: {'video_url', 'duration', 'success', 'music_generation_version_id'}}

    for segment_number, segment_data in assembly_data.video_segments_data.items():
        segment_version: Optional[VideoSegmentVersionDB] = segment_data.get('version')

        if not segment_version:
            logger.warning(f"⚠️ Segment {segment_number} 没有 version 数据，跳过")
            continue

        # 获取 segment 的结果（VideoSegmentVersionDB：video_url, lipsync_version_id, music_generation_version_id）
        success = segment_version.success
        video_url = segment_version.video_url if success else None
        duration = segment_version.duration
        music_generation_version_id = segment_version.music_generation_version_id
        lipsync_version_id = segment_version.lipsync_version_id

        segment_mapping[segment_number] = {
            'video_url': video_url,
            'duration': duration,
            'success': success,
            'music_generation_version_id': music_generation_version_id,
            'lipsync_version_id': lipsync_version_id,
            'video_generation_version_ids': segment_version.video_generation_version_ids or [],
        }

        status_str = "✅ 成功" if success else "❌ 失败"
        logger.info(f"   📹 Segment {segment_number}: {status_str}, 时长: {duration:.2f}s")

    # 🔧 若有 lipsync 版本，优先用 lipsync 的 video_url（唇形同步已合入，且最终会用完整音频替换）
    lipsync_version_ids = [m['lipsync_version_id'] for m in segment_mapping.values() if m.get('lipsync_version_id')]
    if lipsync_version_ids:
        from ....crud.video.video_other import get_lipsync_versions_by_ids
        lipsync_versions = await get_lipsync_versions_by_ids(lipsync_version_ids)
        lipsync_map = {lv.uuid: lv for lv in lipsync_versions}
        for segment_number, info in segment_mapping.items():
            lid = info.get('lipsync_version_id')
            if lid and lid in lipsync_map:
                lv = lipsync_map[lid]
                if lv.success and lv.video_url:
                    info['video_url'] = lv.video_url
                    logger.info(f"   🎭 Segment {segment_number} 使用 lipsync 视频: {lv.video_url[:60]}...")

    if not segment_mapping:
        logger.error("❌ 没有有效的 segment 数据")
        return await concatenate_video_segments_by_urls(assembly_data.video_urls, title)

    # 🔧 按 segment_number 排序，收集成功的 segment URLs
    sorted_segments = sorted(segment_mapping.items(), key=lambda x: x[0])
    logger.info(f"📊 Segment 处理顺序: {[seg_num for seg_num, _ in sorted_segments]}")

    segment_video_urls = []
    segments_to_concat: List[Tuple[int, float, str]] = []
    total_expected_duration = 0.0
    for seg_num, info in sorted_segments:
        video_url = info.get('video_url')
        success = info.get('success')
        duration = info.get('duration', 0)
        total_expected_duration += duration
        logger.info(
            f"   [更新视频] concatenate segment_number={seg_num} has_video_url={bool(video_url)} success={success} duration={duration}"
        )
        if success and video_url:
            segment_video_urls.append(video_url)
            segments_to_concat.append((seg_num, float(duration), video_url))
        else:
            logger.warning(f"   ⚠️ Segment {seg_num} 失败，跳过（视频将通过调速匹配音频时长）")

    if not segment_video_urls:
        logger.error("❌ 没有成功的 segment 视频")
        return await concatenate_video_segments_by_urls(assembly_data.video_urls, title)

    async def _probe_segment_video_info(sn: int, db_dur: float, url: str) -> Tuple[int, float, float, int, float]:
        vi = await msc.video_info(url)
        probed = float(vi.get("duration", 0))
        nb_frames = int(vi.get("nb_frames") or 0)
        fps = float(vi.get("fps") or 0.0)
        return sn, db_dur, probed, nb_frames, fps

    logger.info(
        f"📐 合并前逐段 probe（VideoSegmentVersion.duration vs MSC video_info），共 {len(segments_to_concat)} 段"
    )
    probe_rows = await asyncio.gather(
        *[_probe_segment_video_info(sn, db_d, u) for sn, db_d, u in segments_to_concat]
    )
    sum_probed = 0.0
    sum_db_concat_only = 0.0
    drift_lines: List[Tuple[int, float]] = []
    for sn, db_d, probed, _nbf, _fps in probe_rows:
        sum_probed += probed
        sum_db_concat_only += db_d
        delta = probed - db_d
        drift_lines.append((sn, delta))
        logger.info(
            f"   [时长诊断] 📐 Segment {sn}: db(version.duration/mapping)={db_d:.3f}s "
            f"probe(MSC video_info)={probed:.3f}s Δ={delta:+.3f}s"
        )
    drift_probe_vs_db = sum_probed - sum_db_concat_only
    logger.info(
        f"📊 参与 concat 的片段 DB 累计={sum_db_concat_only:.3f}s | MSC probe 累计={sum_probed:.3f}s | "
        f"素材层偏差(probe−DB)={drift_probe_vs_db:+.3f}s"
    )
    drift_lines.sort(key=lambda x: abs(x[1]), reverse=True)
    top_n = drift_lines[:5]
    if top_n:
        parts = [f"seg{k}:Δ{v:+.3f}s" for k, v in top_n]
        logger.info(f"📊 |Δ| 最大的前 {len(top_n)} 段: {', '.join(parts)}")

    # 步骤1：增量 concat + 前缀漂移 trim_only（控制中段累计漂移，避免仅靠成片尾裁）
    run_id = uuid.uuid4().hex[:12]
    predicted_drift = sum_probed - total_expected_duration
    inc_pieces: List[Tuple[int, float, str, float, int]] = [
        (probe_rows[j][0], probe_rows[j][1], segments_to_concat[j][2], probe_rows[j][2], probe_rows[j][3])
        for j in range(len(probe_rows))
    ]
    # 帧率（用于整数帧裁剪；各段一致，取首个有效值）
    fps_plan = next(
        (probe_rows[j][4] for j in range(len(probe_rows)) if probe_rows[j][4] > 0),
        24.0,
    )
    # 口型段集合：段内任一 video_generation_version 的 generation_mode == 'lipsync'
    # （lipsync_version_id 是旧字段，新管线改用 generation_mode 标记，不能据此判断）
    # 用途：整数帧前缀漂移纠偏时「优先裁非口型边界」，保护对嘴形。
    lipsync_sns: set = set()
    try:
        _all_gen_version_ids: List[str] = []
        for _info in segment_mapping.values():
            _all_gen_version_ids.extend(_info.get('video_generation_version_ids') or [])
        if _all_gen_version_ids:
            _gen_versions = await get_video_generation_versions_by_uuids(list(set(_all_gen_version_ids)))
            _gen_mode_by_uuid = {gv.uuid: gv.generation_mode for gv in _gen_versions}
            lipsync_sns = {
                sn for sn, info in segment_mapping.items()
                if any(
                    _gen_mode_by_uuid.get(gid) == 'lipsync'
                    for gid in (info.get('video_generation_version_ids') or [])
                )
            }
        logger.info(
            "🎭 口型段(generation_mode=lipsync)共 %d 段: %s", len(lipsync_sns), sorted(lipsync_sns),
        )
    except Exception as _le:
        logger.warning("⚠️ 口型段识别失败（将按全非口型处理，不影响合并）: %s", _le)

    logger.info(
        f"\n🎬 第一阶段：增量合并 {len(segment_video_urls)} 个 segment"
        f"（chunk={ASSEMBLY_INCREMENTAL_CONCAT_CHUNK_SIZE}，整数帧前缀漂移纠偏）..."
    )
    logger.info(
        f"📊 预期总时长: {total_expected_duration:.2f}s ({len(segment_video_urls)} segments) | "
        f"素材 probe 之和: {sum_probed:.3f}s | 预计漂移: {predicted_drift:+.3f}s"
    )
    # 🧪 推演日志（不影响合并）：解析裁剪计划 + 整数帧预测/口型标注
    try:
        _dryrun_prefix_drift_trim_plan(inc_pieces)
    except Exception as _dry_e:
        logger.warning("🧪 [dryrun] 解析推演失败（不影响合并）: %s", _dry_e)
    try:
        _frame_rows = [
            (probe_rows[j][0], probe_rows[j][1], probe_rows[j][3])
            for j in range(len(probe_rows))
        ]
        _dryrun_integer_frame_trim_plan(_frame_rows, lipsync_sns, fps_plan)
    except Exception as _fp_e:
        logger.warning("🧪 [frameplan] 整数帧推演失败（不影响合并）: %s", _fp_e)

    merged_video_url = await _incremental_concat_with_prefix_drift_trim(
        inc_pieces, run_id=run_id, lipsync_sns=lipsync_sns, fps=fps_plan,
    )
    logger.info(f"✅ Segment 视频增量合并完成: {merged_video_url}")

    concat_info = await msc.video_info(merged_video_url)
    concat_duration = float(concat_info.get("duration", 0))
    if total_expected_duration > 0 and concat_duration > total_expected_duration * 1.15:
        logger.warning(
            "⚠️ Assembly guard: concat 时长 %.2fs 远超预期 %.2fs (%.1fx)，"
            "尝试 normalize 单次重合并",
            concat_duration, total_expected_duration,
            concat_duration / total_expected_duration,
        )
        concat_result = await msc.video_concat(
            segment_video_urls, run_id=f"{run_id}_fix", normalize=True,
        )
        merged_video_url = concat_result["result_url"]
        concat_info = await msc.video_info(merged_video_url)
        concat_duration = float(concat_info.get("duration", 0))
        logger.info(f"✅ 重新合并后时长: {concat_duration:.2f}s")
    else:
        logger.info(f"📊 合并后实际时长: {concat_duration:.2f}s (预期: {total_expected_duration:.2f}s)")

    mux_vs_probe_sum = concat_duration - sum_probed
    logger.info(
        f"📊 concat 成片时长={concat_duration:.3f}s | 合并前各段 probe 之和={sum_probed:.3f}s "
        f"(copy concat 相对素材累计偏差={mux_vs_probe_sum:+.3f}s)"
    )

    # 步骤2：获取音频和视频时长，对齐
    logger.info(f"\n🎬 第二阶段：获取时长并对齐...")
    final_video_url = merged_video_url

    if complete_audio_url:
        audio_info = await msc.audio_info(complete_audio_url)
        actual_audio_duration = audio_info.get("duration")
        if actual_audio_duration:
            logger.info(f"🎵 实际音频时长: {actual_audio_duration:.2f}s (数据库值: {complete_audio_duration:.2f}s)")
            complete_audio_duration = float(actual_audio_duration)

        current_video_duration = concat_duration
        logger.info(f"📹 当前视频时长: {current_video_duration:.2f}s")
        logger.info(f"📊 时长差异: {abs(current_video_duration - complete_audio_duration):.2f}s")
        logger.info(
            f"📊 对齐参考: 音频(complete)={complete_audio_duration:.3f}s | "
            f"成片视频={current_video_duration:.3f}s | DB segment 累计(全映射)={total_expected_duration:.3f}s | "
            f"对齐策略: |diff|<={AV_ALIGN_SKIP_SEC}s 跳过 | "
            f"<={AV_LIGHT_ALIGN_MAX_SEC}s trim/pad | 否则 speed-adjust"
        )

        # 步骤2.1：视频时长对齐（音画同步：以完整音频时长为准）
        # 小偏差：MSC freeze_or_tail_slow（长则 hybrid 截短；短则静帧 copy 或尾部放慢/整片放慢，无黑场）
        # 大偏差：整片 speed-adjust，避免长段硬裁损失画面或极端变速
        duration_diff = current_video_duration - complete_audio_duration
        ad = abs(duration_diff)
        if ad <= AV_ALIGN_SKIP_SEC:
            logger.info(f"✅ 视频与音频时长在容差内 (|diff|={ad:.3f}s)，无需调整")
        elif ad <= AV_LIGHT_ALIGN_MAX_SEC:
            align_mode = "trim_only" if duration_diff > 0 else "freeze_or_tail_slow"
            logger.info(
                f"🎬 轻量对齐({align_mode}) 匹配音频: {current_video_duration:.2f}s -> {complete_audio_duration:.2f}s "
                f"(差异: {duration_diff:+.2f}s)"
            )
            trim_result = await msc.video_trim(
                merged_video_url,
                complete_audio_duration,
                run_id=run_id,
                mode=align_mode,
                tolerance=0.0,
            )
            merged_video_url = trim_result["result_url"]
            logger.info(f"✅ 轻量对齐完成: {merged_video_url}")
        else:
            speed_factor = current_video_duration / complete_audio_duration
            logger.info(
                f"🎬 整片调速匹配音频: {current_video_duration:.2f}s -> {complete_audio_duration:.2f}s "
                f"(差异: {duration_diff:+.2f}s, 速度因子: {speed_factor:.4f}x)"
            )
            speed_result = await msc.video_speed_adjust(merged_video_url, complete_audio_duration, run_id=run_id)
            merged_video_url = speed_result["result_url"]
            logger.info(f"✅ 视频速度微调完成: {merged_video_url}")

        # 步骤2.2：混入完整音频
        # ensure_on_s3 已 strip audio (-an)，所有下游操作均 -c copy，视频此时无音轨。
        # mix_audio 内部检测 has_audio 并自动选择 amix 或直接添加。
        logger.info(f"🎵 添加完整音频...")
        mix_result = await msc.video_mix_audio(
            video_url=merged_video_url,
            audio_url=complete_audio_url,
            run_id=run_id,
            video_volume=0.0,
            audio_volume=1.0,
        )
        final_video_url = mix_result["result_url"]
        logger.info(f"✅ 完整音频添加完成: {final_video_url}")
    else:
        logger.warning(f"⚠️ 没有完整音频 URL，使用静音视频")

    logger.info(f"✅ Audio-driven 拼接完成: {final_video_url}")
    return {
        "final_url": final_video_url,
        "final_no_subtitle_url": final_video_url,
        "subtitle_url": None,
    }



# 已放弃 packet 计数：除非流损坏严重，format.duration / stream.duration 加少量冗余即可
DURATION_PADDING_SEC = 0.02  # 时长冗余（秒），避免 setpts 等因元数据略短而截断

# concat 后以完整音频为准对齐成片视频（与 Cuti-Media-Service ffmpeg_service.trim_video 容差一致）
AV_ALIGN_SKIP_SEC = 0.02  # |diff| 小于此视为已对齐，不调 MSC
AV_LIGHT_ALIGN_MAX_SEC = 2.0  # |diff| 在此内用 trim/尾部补黑；更大用整片 speed-adjust，避免长段硬裁



async def _add_watermark_to_video(input_path: str, output_path: str) -> bool:
    """使用 ffmpeg 在视频右下角叠加 logo 水印。失败时返回 False，调用方使用原视频。
    实现委托给 utils.video_utils.add_watermark_to_video_async（shot 级水印在 s3_utils 上传时已做）。
    """
    from ....utils.video_utils import add_watermark_to_video_async
    return await add_watermark_to_video_async(input_path, output_path)

async def _download_media_to_temp(media_url: str, temp_dir: str, media_type: MediaType) -> Optional[str]:
    """直接下载媒体文件到临时目录，避免重复存储
    
    Args:
        media_url: 媒体URL
        temp_dir: 临时目录
        media_type: 媒体类型
        
    Returns:
        下载后的本地路径，失败时返回None
    """
    import uuid
    from ....utils.s3_utils import s3_utils
    
    try:
        # 生成temp目录中的文件名
        file_extension = ".mp4" if media_type == MediaType.VIDEO else ".mp3"
        temp_filename = f"temp_{uuid.uuid4().hex[:8]}{file_extension}"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        # 直接下载到临时目录，避免重复存储
        success = await s3_utils.download_file(media_url, temp_path)
        
        if success and os.path.exists(temp_path):
            logger.info(f"📁 媒体文件直接下载到temp: {temp_path}")
            return temp_path
        else:
            logger.error(f"❌ 无法下载媒体文件到temp: {media_url}")
            return None
    
    except Exception as e:
        logger.error(f"❌ 下载媒体到temp失败: {e}")
        return None


async def video_assembly_node(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any
) -> Union[VideoAgentState, Dict[str, Any]]:
    """视频合成节点（重构版本，支持数据库持久化和完整关联ID）"""
    # 获取必要的UUID
    story_outline_uuid = state.get("story_outline_uuid")
    audio_files = state.get("audio_files", [])
    
    if not story_outline_uuid:
        raise BusinessException(
            BusinessExceptionCode.VIDEO_ANALYSIS_UUID_MISSING,
            "缺少故事梗概UUID"
        )
    
    # 从数据库获取数据（使用asyncpg CRUD）
    story_outline = await get_story_outline_from_db(story_outline_uuid)
    conversation_id = story_outline.conversation_id
    thread_id = story_outline.thread_id
    
    # 按 thread 组装：同一 thread 下可能有多轮 run，用 conversation_id + thread_id 取该 thread 当前全部资源
    from ....crud.video.video_generation import get_video_generations_by_conversation, get_video_generation_versions_by_video_generation_ids
    video_generations_db = await get_video_generations_by_conversation(conversation_id, thread_id)
    
    if not video_generations_db:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "缺少视频生成数据，无法进行视频合成"
        )
    
    # 一次性批量获取所有 video_generation 的所有版本（一次SQL查询）
    video_generation_ids = [vg.uuid for vg in video_generations_db]
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
    
    # 🔧 关键修复：批量获取当前版本并转换为 VideoGenerationVersion，只保留成功的
    # 版本选择逻辑：使用 current_version_index 选择当前版本，索引无效则使用第一个版本
    from ....models.video_state import VideoGenerationVersion
    successful_video_generations = []
    for video_gen_db in video_generations_db:
        versions = video_gen_id_to_versions.get(video_gen_db.uuid, [])
        if not versions:
            logger.warning(f"⚠️ 跳过没有版本的视频片段: shot_{video_gen_db.shot_number}")
            continue
        
        # 根据 current_version_index 选择当前版本
        current_index = video_gen_db.current_version_index
        if 0 <= current_index < len(versions):
            current_version = versions[current_index]
        else:
            # 索引无效，使用第一个版本
            current_version = versions[0]
        
        if current_version and current_version.success and current_version.video_url:
            # 转换为 VideoGenerationVersion
            # 从 additional_data 中提取 seed 和 resolution
            seed = None
            resolution = None
            if current_version.additional_data:
                seed = current_version.additional_data.get('seed')
                resolution = current_version.additional_data.get('resolution')
            
            video_generation_version = VideoGenerationVersion(
                shot_number=current_version.shot_number,
                video_url=current_version.video_url,
                duration=int(math.ceil(current_version.duration)) if current_version.duration is not None else 0,
                i2v_prompt=current_version.motion_prompt,
                is_bridge=current_version.is_bridge,
                keyframe_url=current_version.keyframe_url,
                success=current_version.success,
                error_msg=current_version.error_msg,
                audio_segment_ids=current_version.audio_segment_ids,
                seed=seed,
                resolution=resolution
            )
            video_generation_version.version_id = current_version.uuid
            video_generation_version.video_generation_id = current_version.video_generation_id
            successful_video_generations.append(video_generation_version)
        else:
            logger.warning(f"⚠️ 跳过失败的视频片段: shot_{video_gen_db.shot_number}")
    
    if not successful_video_generations:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "没有可用的视频片段进行合成"
        )
    
    # 🔧 关键修复：按 shot_number 排序，确保合并时顺序正确
    successful_video_generations.sort(key=lambda x: x.shot_number)
    
    logger.info(f"🎬 开始视频合成: {story_outline.title}，共{len(successful_video_generations)}个成功的视频片段")
    
    # 按 thread 获取旁白/音效/音乐（该 thread 下当前全部资源）
    narrations_data = await get_narrations_for_videos(conversation_id, thread_id)
    audio_effects_data = await get_audio_effects_for_videos(conversation_id, thread_id)
    music_data = await get_music_data_for_videos(conversation_id, thread_id)
    
    # 从 state 中获取音频转录UUID，如果有则获取音频转录数据
    audio_transcription = None
    audio_transcription_uuids = state.get("audio_transcription_uuids", [])
    if audio_transcription_uuids:
        from ....services.agent.utils.database_utils import get_audio_transcription_from_db
        audio_transcription = await get_audio_transcription_from_db(audio_transcription_uuids)
    
    # 获取 video segments 数据（用于 audio-driven 模式）
    from ....crud.video.video_segment import (
        get_video_segments_with_data_by_conversation_and_thread,
        get_video_segment_versions_by_segment_ids
    )
    
    # 按 thread 获取 video segments
    video_segments_db = await get_video_segments_with_data_by_conversation_and_thread(conversation_id, thread_id)
    
    # 🔧 批量获取所有 segment 的所有版本（一次查询，使用通用 crud 函数）
    if video_segments_db:
        segment_ids = [seg.uuid for seg in video_segments_db]
        segment_id_to_versions = await get_video_segment_versions_by_segment_ids(segment_ids)
    else:
        segment_id_to_versions = {}
    
    # 构建 video_segments_data: {segment_number: {'segment': VideoSegmentDB, 'version': VideoSegmentVersionDB}}
    video_segments_data = {}
    
    for segment_db in video_segments_db:
        # 从批量查询结果中获取版本
        versions = segment_id_to_versions.get(segment_db.uuid, [])
        if not versions:
            logger.warning(f"⚠️ 视频片段 {segment_db.segment_number} 没有版本数据")
            continue
        
        # 根据 current_version_index 选择版本
        current_index = segment_db.current_version_index
        if 0 <= current_index < len(versions):
            selected_version = versions[current_index]
        else:
            # 如果索引无效，使用第一个版本
            selected_version = versions[0]
            logger.warning(f"⚠️ 视频片段 {segment_db.segment_number} 的 current_version_index={current_index} 无效，使用第一个版本")
        
        video_segments_data[segment_db.segment_number] = {
            'segment': segment_db,
            'version': selected_version
        }
        vurl = (selected_version.video_url or "") if selected_version else ""
        logger.info(
            f"   [更新视频] assembly video_segments_data segment_number={segment_db.segment_number} segment_uuid={segment_db.uuid[:8]}... "
            f"video_url={'有' if vurl else '无'}({vurl[:50] + '...' if len(vurl) > 50 else vurl}) success={getattr(selected_version, 'success', None)}"
        )
    
    logger.info(f"📹 获取到 {len(video_segments_data)} 个视频片段数据")
    
    # 创建视频合成数据对象
    assembly_data = VideoAssemblyData(
        story_outline=story_outline,
        video_generations=successful_video_generations,
        narrations_data=narrations_data,
        audio_effects_data=audio_effects_data,
        music_data=music_data,
        audio_transcription=audio_transcription,
        uploaded_audio_files=audio_files,
        video_segments_data=video_segments_data
    )
    
    # 调用核心的视频合并逻辑
    detected_language = state.get("detected_language")
    result = await execute_video_merge_core(
        assembly_data, 
        send_event_func,
        run_id=state.get("run_id"),
        thread_id=state.get("thread_id"),
        detected_language=detected_language
    )
    
    # 更新任务记录完成状态（与「重新统计」接口共用 build_and_update_tool_consistency_summary，保证一致）
    try:
        from ....crud.error_tracking import update_task_record, build_and_update_tool_consistency_summary

        run_id = state.get("run_id")
        tool_consistency_summary = None
        try:
            tool_consistency_summary = await build_and_update_tool_consistency_summary(run_id)
            if tool_consistency_summary is None and run_id:
                # 任务结束时可能尚有 version 未提交可见，延迟一次再试
                await asyncio.sleep(1.0)
                tool_consistency_summary = await build_and_update_tool_consistency_summary(run_id)
            if tool_consistency_summary is not None:
                logger.info("📝 任务结束已聚合一致性汇总 run_id=%s", run_id)
            else:
                logger.warning("📝 任务结束一致性汇总为空 run_id=%s（可能 version 未落库或 run_id 异常）", run_id)
        except Exception as e:
            logger.warning("聚合 tool_consistency_summary 跳过: %s", e)

        # 打印当前 state 的 UUID 字段用于调试
        logger.info(f"📝 更新任务记录 - analysis_uuid: {state.get('analysis_uuid')}")
        logger.info(f"📝 更新任务记录 - character_uuids: {state.get('character_uuids')}")
        logger.info(f"📝 更新任务记录 - shot_uuids: {state.get('shot_uuids')}")
        logger.info(f"📝 更新任务记录 - keyframe_uuids: {state.get('keyframe_uuids')}")

        updates = {
            "task_finish_time": datetime.utcnow(),
            "task_status": "completed" if result["success"] else "failed",
            "video_assembly_uuid": result.get("assembly_uuid"),
            "final_video_url": result.get("final_video_url"),
            "analysis_uuid": state.get("analysis_uuid"),
            "audio_transcription_uuids": state.get("audio_transcription_uuids"),
            "story_outline_uuid": state.get("story_outline_uuid"),
            "character_uuids": state.get("character_uuids"),
            "scene_uuids": state.get("scene_uuids"),
            "shot_uuids": state.get("shot_uuids"),
            "keyframe_uuids": state.get("keyframe_uuids"),
            "narration_uuids": state.get("narration_uuids"),
            "audio_effect_uuids": state.get("audio_effect_uuids"),
            "video_generation_uuids": state.get("video_generation_uuids"),
            "music_generation_uuids": state.get("music_generation_uuids"),
            "video_segments_uuids": state.get("video_segments_uuids"),
            "actual_target_duration": state.get("actual_target_duration"),
        }
        if tool_consistency_summary is not None:
            updates["tool_consistency_summary"] = tool_consistency_summary

        await update_task_record(
            run_id=run_id,
            updates=updates,
        )
        logger.info(f"✅ 任务完成记录已更新: run_id={state.get('run_id')}")
    except Exception as e:
        logger.error(f"❌ 更新任务完成记录失败: {e}，但任务继续")
        
    return {
        "video_assembly_uuid": result["assembly_uuid"],
        "final_video_url": result["final_video_url"]
    }




async def mix_narration_with_video(video_path: str, narration_url: str, temp_dir: str) -> Optional[str]:
    """将旁白与视频混合（当没有音乐时）"""
    try:
        import asyncio
        
        # 直接下载旁白到临时目录，避免重复存储
        narration_path = await _download_media_to_temp(narration_url, temp_dir, MediaType.AUDIO)
        if not narration_path or not os.path.exists(narration_path):
            logger.warning(f"⚠️ 旁白文件下载失败，返回原视频: {narration_url}")
            return video_path
        
        output_path = os.path.join(temp_dir, f"narration_{uuid.uuid4().hex[:8]}.mp4")
        
        cmd = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-i', narration_path,
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-map', '0:v',
            '-map', '1:a',
            '-shortest',
            output_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
        
        output_exists = await asyncio.to_thread(os.path.exists, output_path)
        if process.returncode == 0 and output_exists:
            return output_path
        else:
            error_msg = stderr.decode() if stderr else "Unknown error"
            logger.error(f"❌ 旁白混合失败: {error_msg}")
            return video_path
            
    except Exception as e:
        logger.error(f"旁白混合失败: {e}")
        return video_path


async def concatenate_video_segments_by_urls(
    video_urls: List[str], title: str, skip_watermark: bool = False
) -> str:
    """通过URL列表拼接视频片段。skip_watermark=True 时不再叠加水印（用于 assembly 的 video_urls 已为 shot 级水印）。"""
    import asyncio
    import uuid

    run_id = uuid.uuid4().hex
    result = await msc.video_concat(video_urls, run_id=run_id, normalize=False)
    result_url = result["result_url"]
    logger.info(f"✅ Media service video_concat 成功: {result_url}")
    return {
        "final_url": result_url,
        "final_no_subtitle_url": result_url,
    }

