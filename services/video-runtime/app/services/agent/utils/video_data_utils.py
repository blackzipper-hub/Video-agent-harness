"""
视频数据获取工具：从 state 等构建 VideoAssemblyData 等通用方法。
"""
import logging
import math
from typing import Optional
from langgraph.runtime import Runtime

from ....models.video_state import VideoAgentState, VideoAssemblyData
from .database_utils import get_story_outline_from_db, get_audio_transcription_from_db
from ....crud.video.video_generation import (
    get_video_generations_by_run_id,
    get_video_generation_versions_by_video_generation_ids
)
from ..schemas import VideoContextSchema

logger = logging.getLogger(__name__)


async def get_video_assembly_data_from_state(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
) -> Optional[VideoAssemblyData]:
    """从 state 中的 UUID 重新构建 VideoAssemblyData。"""
    try:
        story_outline_uuid = state.get("story_outline_uuid")
        audio_files = state.get("audio_files", [])
        if not story_outline_uuid:
            logger.error("❌ 缺少 story_outline_uuid")
            return None

        from ....models.database import AsyncSessionLocal

        story_outline = None
        video_generations_db = None
        all_versions = None
        
        # 加载数据（使用asyncpg CRUD）
        story_outline = await get_story_outline_from_db(story_outline_uuid)
        if not story_outline:
            logger.error(f"❌ 找不到故事大纲: {story_outline_uuid}")
            return None
        run_id = story_outline.run_id
        video_generations_db = await get_video_generations_by_run_id(run_id)
        if not video_generations_db:
            logger.error(f"❌ 找不到视频生成数据: run_id={run_id}")
            return None
        video_generation_ids = [vg.uuid for vg in video_generations_db]
        all_versions = await get_video_generation_versions_by_video_generation_ids(video_generation_ids)

        video_gen_id_to_versions = {}
        for version in all_versions:
            video_gen_id = version.video_generation_id
            if video_gen_id not in video_gen_id_to_versions:
                video_gen_id_to_versions[video_gen_id] = []
            video_gen_id_to_versions[video_gen_id].append(version)
        for video_gen_id in video_gen_id_to_versions:
            video_gen_id_to_versions[video_gen_id].sort(key=lambda v: v.version_number)

        from ....models.video_state import VideoGenerationVersion
        all_video_generations = []
        for video_gen_db in video_generations_db:
            versions = video_gen_id_to_versions.get(video_gen_db.uuid, [])
            if not versions:
                logger.warning(f"⚠️ 跳过没有版本的视频片段: shot_{video_gen_db.shot_number}")
                continue
            current_index = video_gen_db.current_version_index
            current_version = versions[current_index] if 0 <= current_index < len(versions) else versions[0]
            seed = None
            resolution = None
            if current_version.additional_data:
                seed = current_version.additional_data.get("seed")
                resolution = current_version.additional_data.get("resolution")
            video_generation_version = VideoGenerationVersion(
                shot_number=current_version.shot_number,
                video_url=current_version.video_url or "",
                duration=int(math.ceil(current_version.duration)) if current_version.duration is not None else 0,
                i2v_prompt=current_version.motion_prompt,
                is_bridge=current_version.is_bridge,
                keyframe_url=current_version.keyframe_url,
                success=current_version.success,
                error_msg=current_version.error_msg,
                audio_segment_ids=current_version.audio_segment_ids,
                seed=seed,
                resolution=resolution,
                generation_mode=getattr(current_version, "generation_mode", None),
                audio_url=getattr(current_version, "audio_url", None),
            )
            video_generation_version.version_id = current_version.uuid
            video_generation_version.video_generation_id = current_version.video_generation_id
            if current_version.success:
                logger.info(f"✅ 视频片段 shot_{video_gen_db.shot_number}: 成功")
            else:
                logger.warning(f"⚠️ 视频片段 shot_{video_gen_db.shot_number}: 失败 - {current_version.error_msg or 'Unknown error'}")
            all_video_generations.append(video_generation_version)

        if not all_video_generations:
            logger.error("❌ 没有任何视频片段")
            return None
        all_video_generations.sort(key=lambda x: x.shot_number)

        from ..video.video_assembly_service import (
            get_narrations_for_videos_by_run_id,
            get_audio_effects_for_videos_by_run_id,
            get_music_data_by_run_id,
        )
        # 加载音频数据（使用asyncpg CRUD）
        narrations_data = await get_narrations_for_videos_by_run_id(run_id)
        audio_effects_data = await get_audio_effects_for_videos_by_run_id(run_id)
        music_data = await get_music_data_by_run_id(run_id)
        logger.info(f"✅ 批量查询音频数据: narrations={len(narrations_data)}, effects={len(audio_effects_data)}, music={len(music_data)}")
        
        audio_transcription = None
        audio_transcription_uuids = state.get("audio_transcription_uuids", [])
        if audio_transcription_uuids:
            audio_transcription = await get_audio_transcription_from_db(audio_transcription_uuids)

        assembly_data = VideoAssemblyData(
            story_outline=story_outline,
            video_generations=all_video_generations,
            narrations_data=narrations_data,
            audio_effects_data=audio_effects_data,
            music_data=music_data,
            audio_transcription=audio_transcription,
            uploaded_audio_files=audio_files,
        )
        success_count = sum(1 for v in all_video_generations if v.success)
        logger.info(f"✅ 成功构建 VideoAssemblyData: {len(all_video_generations)} 个视频片段 (成功: {success_count})")
        return assembly_data
    except Exception as e:
        logger.error(f"❌ 获取 VideoAssemblyData 失败: {e}")
        return None
