"""任务完整详情组装（核心能力）。

``build_task_full_data`` 原定义于 ``api/admin/error_tracking_endpoints.py``（ops-admin/ee）。
但用户端核心路由 ``api/agent/conversation_routes.py`` 也依赖它来返回 task-detail，
因此将其下沉到核心 ``services`` 层，避免“核心依赖闭源 ee 模块”的耦合：
开源快照移除 ee/admin 后，核心仍可正常工作。行为与原实现完全一致（纯搬运）。
"""
import json
from typing import Any, Dict, List, Optional

from ..utils.asyncpg_utils import utc_isoformat
from ..crud.video.video_other import get_latest_final_video_url_by_thread
from ..crud.error_tracking import (
    build_image_consistency_display_lines,
    build_video_consistency_display_lines,
)


async def build_task_full_data(record) -> Dict[str, Any]:
    """构建任务完整详情数据（与 get_task_full_detail 共用逻辑，供用户端 conversation/task-detail 调用）。"""
    # 使用新的CRUD模块
    from ..crud.video.video_other import get_video_analysis_by_uuid, get_video_assembly_by_uuid
    from ..crud.video.video_story import (
        get_video_story_outline_by_uuid,
        get_video_story_outline_by_conversation_id,
        get_video_story_outline_by_run_id,
        get_scenes_by_uuids,
        get_scenes_by_conversation,
        get_detailed_shots_by_uuids,
        get_detailed_shots_by_conversation,
        get_chapters_by_uuids,
        get_chapters_by_story_outline_id,
    )
    from ..crud.video.video_character import (
        get_characters_by_conversation,
        get_character_versions_batch,
        get_character_multi_view_image_versions_by_ids,
    )
    from ..crud.video.video_keyframe import (
        get_keyframes_by_conversation,
        get_keyframe_versions_by_keyframe_ids
    )
    from ..crud.video.video_generation import (
        get_video_generations_by_conversation,
        get_video_generation_versions_by_video_generation_ids
    )
    from ..crud.video.video_audio import (
        get_narrations_by_conversation,
        get_narration_versions_by_narration_ids,
        get_audio_effects_by_conversation,
        get_audio_effect_versions_by_audio_effect_ids,
        get_music_generations_by_conversation,
        get_music_generation_versions_by_music_generation_ids,
        get_audio_segments_by_uuids,
        get_video_audio_transcriptions_by_uuids,
        get_video_audio_transcription_by_thread_id,
        get_video_audio_transcription_by_run_id,
        get_video_audio_sections_by_transcription_uuid,
        get_video_audio_segments_by_transcription_uuid,
    )
    from ..crud.video.video_segment import get_video_segments_with_data_by_run_id
    

    # 组装完整数据
    chapter_uuid_to_normalized_order: Dict[str, int] = {}
    full_data = {
        # 基础信息
        "id": record.id,
        "uuid": record.uuid,
        "task_id": record.task_id,
        "conversation_id": record.conversation_id,
        "thread_id": record.thread_id,
        "user_id": record.user_id,
        "detected_language": record.detected_language,
        "generation_config": record.generation_config,
        "actual_target_duration": record.actual_target_duration,
        "task_input": record.task_input,
        "user_input_data": record.user_input_data,
        "task_start_time": utc_isoformat(record.task_start_time),
        "task_finish_time": utc_isoformat(record.task_finish_time),
        "task_status": record.task_status,
        "final_video_url": record.final_video_url,
        "latest_final_video_url": await get_latest_final_video_url_by_thread(record.thread_id or "") if record.thread_id else record.final_video_url,
        "tool_consistency_summary": getattr(record, "tool_consistency_summary", None),
        "cost": getattr(record, "cost", None),
        "created_at": utc_isoformat(record.created_at),
        "updated_at": utc_isoformat(record.updated_at),

        # 实际数据（从 UUID 引用获取）
        "analysis_data": None,
        "story_outline_data": None,
        "audio_transcriptions_data": [],
        "audio_sections_data": [],
        "audio_segments_data": [],
        "characters_data": [],
        "scenes_data": [],
        "keyframes_data": [],
        "videos_data": [],
        "narrations_data": [],
        "audio_effects_data": [],
        "music_data": [],
        "video_assembly_data": None,
        "video_segments_data": [],
        "shots_data": [],
        "chapters_data": [],
        "chapter_id_to_info": {},
        "audio_segment_id_to_info": {},
        "fusion_images_data": [],
        "multiview_images_data": []
    }
    
    # 1. 获取分析数据
    if record.analysis_uuid:
        analysis = await get_video_analysis_by_uuid(record.analysis_uuid)
        if analysis:
            # 解析JSON字符串字段
            key_elements = []
            if analysis.key_elements:
                try:
                    key_elements = json.loads(analysis.key_elements) if isinstance(analysis.key_elements, str) else analysis.key_elements
                except:
                    key_elements = [analysis.key_elements]
            
            style_preferences = []
            if analysis.style_preferences:
                try:
                    style_preferences = json.loads(analysis.style_preferences) if isinstance(analysis.style_preferences, str) else analysis.style_preferences
                except:
                    style_preferences = [analysis.style_preferences]
            
            full_data["analysis_data"] = {
                "uuid": getattr(analysis, "uuid", None),
                "video_type": getattr(analysis, "video_type", None),
                "duration": getattr(analysis, "duration", None),
                "main_character": getattr(analysis, "main_character", None),
                "purpose": getattr(analysis, "purpose", None),
                "key_elements": key_elements,
                "style_preferences": style_preferences,
                "target_audience": getattr(analysis, "target_audience", None),
                "next_action": getattr(analysis, "next_action", None),
                "content_category": getattr(analysis, "content_category", None),
                "run_id": getattr(analysis, "run_id", None),
                "user_id": getattr(analysis, "user_id", None),
                "conversation_id": getattr(analysis, "conversation_id", None),
                "thread_id": getattr(analysis, "thread_id", None),
                "hidden_style_description": getattr(analysis, "hidden_style_description", None),
                "curated_style_prompt_id": getattr(analysis, "curated_style_prompt_id", None),
                "additional_data": getattr(analysis, "additional_data", None),
                "created_at": utc_isoformat(getattr(analysis, "created_at", None)),
                "updated_at": utc_isoformat(getattr(analysis, "updated_at", None)),
            }
    
    # 2. 获取故事大纲（与其它 tab 一致：优先 record 上的 uuid，否则按 conversation_id / run_id 查）
    outline = None
    if record.story_outline_uuid:
        outline = await get_video_story_outline_by_uuid(record.story_outline_uuid)
    if not outline and record.conversation_id:
        outline = await get_video_story_outline_by_conversation_id(record.conversation_id)
    if not outline and record.task_id:
        outline = await get_video_story_outline_by_run_id(record.task_id)
    if outline:
        # 风格只用 analysis：不再返回 outline 的 style_guide，避免前端用 outline 兜底展示风格
        full_data["story_outline_data"] = {
            "uuid": getattr(outline, "uuid", None),
            "title": getattr(outline, "title", None),
            "description": getattr(outline, "description", None),
            "themes": getattr(outline, "themes", None) or [],
            "target_audience": getattr(outline, "target_audience", None),
            "total_duration": getattr(outline, "total_duration", None),
            "narrative_structure": getattr(outline, "narrative_structure", None),
            "theme": getattr(outline, "theme", None),
            "key_message": getattr(outline, "key_message", None),
            "structure": getattr(outline, "structure", None),
            "analysis_id": getattr(outline, "analysis_id", None),
            "audio_transcription_uuid": getattr(outline, "audio_transcription_uuid", None),
            "current_version_index": getattr(outline, "current_version_index", 0),
            "run_id": getattr(outline, "run_id", None),
            "user_id": getattr(outline, "user_id", None),
            "conversation_id": getattr(outline, "conversation_id", None),
            "thread_id": getattr(outline, "thread_id", None),
            "additional_data": getattr(outline, "additional_data", None),
            "created_at": utc_isoformat(getattr(outline, "created_at", None)),
            "updated_at": utc_isoformat(getattr(outline, "updated_at", None)),
        }
        # 大纲下的完整章节列表（含 audio_section_uuid / current_version_index 绑定）
        from app.utils.chapter_order import sort_chapters_by_order

        outline_chapters = sort_chapters_by_order(
            await get_chapters_by_story_outline_id(outline.uuid)
        )
        for i, ch in enumerate(outline_chapters):
            ch_uuid = getattr(ch, "uuid", None)
            if ch_uuid:
                chapter_uuid_to_normalized_order[ch_uuid] = i
            full_data["chapters_data"].append({
                "uuid": ch_uuid,
                "order": i,
                "title": getattr(ch, "title", None) or "",
                "description": getattr(ch, "description", None) or "",
                "duration": getattr(ch, "duration", None),
                "audio_segment_ids": getattr(ch, "audio_segment_ids", None) or [],
                "audio_section_uuid": getattr(ch, "audio_section_uuid", None),
                "current_version_index": getattr(ch, "current_version_index", 0),
                "created_at": utc_isoformat(getattr(ch, "created_at", None)),
                "updated_at": utc_isoformat(getattr(ch, "updated_at", None)),
            })
    
    # 2.1 获取音频转录与段落（音乐 MV 三层：transcription + section，供详情展示绑定与 Global 字段）
    # 优先用任务记录的 audio_transcription_uuids；若为空则用 thread_id / task_id 按线程或 run 查转录，确保有 section 时能展示
    trans_map = {}
    if record.audio_transcription_uuids:
        trans_map = await get_video_audio_transcriptions_by_uuids(record.audio_transcription_uuids)
    elif record.thread_id:
        trans = await get_video_audio_transcription_by_thread_id(record.thread_id)
        if trans and getattr(trans, "uuid", None):
            trans_map = {trans.uuid: trans}
    elif record.task_id:
        trans = await get_video_audio_transcription_by_run_id(record.task_id)
        if trans and getattr(trans, "uuid", None):
            trans_map = {trans.uuid: trans}
    if trans_map:
        for _uid, trans in trans_map.items():
            full_data["audio_transcriptions_data"].append({
                "id": getattr(trans, "id", None),
                "uuid": getattr(trans, "uuid", None),
                "user_id": getattr(trans, "user_id", None),
                "conversation_id": getattr(trans, "conversation_id", None),
                "thread_id": getattr(trans, "thread_id", None),
                "run_id": getattr(trans, "run_id", None),
                "task": getattr(trans, "task", None),
                "language": getattr(trans, "language", None),
                "duration": getattr(trans, "duration", None),
                "text": getattr(trans, "text", None),
                "audio_url": getattr(trans, "audio_url", None),
                "filename": getattr(trans, "filename", None),
                "is_instrumental": getattr(trans, "is_instrumental", False),
                "song_name": getattr(trans, "song_name", None),
                "global_bpm": getattr(trans, "global_bpm", None),
                "genre": getattr(trans, "genre", None),
                "global_emotion": getattr(trans, "global_emotion", None),
                "suggested_global_theme": getattr(trans, "suggested_global_theme", None),
                "suggested_color_palette": getattr(trans, "suggested_color_palette", None),
                "additional_data": getattr(trans, "additional_data", None),
                "created_at": utc_isoformat(getattr(trans, "created_at", None)),
                "updated_at": utc_isoformat(getattr(trans, "updated_at", None)),
            })
            segs = await get_video_audio_segments_by_transcription_uuid(_uid)
            for seg in segs:
                full_data["audio_segments_data"].append({
                    "id": getattr(seg, "id", None),
                    "uuid": getattr(seg, "uuid", None),
                    "user_id": getattr(seg, "user_id", None),
                    "conversation_id": getattr(seg, "conversation_id", None),
                    "thread_id": getattr(seg, "thread_id", None),
                    "run_id": getattr(seg, "run_id", None),
                    "transcription_uuid": getattr(seg, "transcription_uuid", None),
                    "segment_id": getattr(seg, "segment_id", None),
                    "start": getattr(seg, "start", None),
                    "end": getattr(seg, "end", None),
                    "duration": getattr(seg, "duration", None),
                    "text": getattr(seg, "text", None),
                    "emotion": getattr(seg, "emotion", None),
                    "tempo": getattr(seg, "tempo", None),
                    "vocal_presence": getattr(seg, "vocal_presence", None),
                    "additional_data": getattr(seg, "additional_data", None),
                    "created_at": utc_isoformat(getattr(seg, "created_at", None)),
                    "updated_at": utc_isoformat(getattr(seg, "updated_at", None)),
                })
            sections = await get_video_audio_sections_by_transcription_uuid(_uid)
            for sec in sections:
                full_data["audio_sections_data"].append({
                    "uuid": getattr(sec, "uuid", None),
                    "transcription_uuid": _uid,
                    "section_type": getattr(sec, "section_type", None),
                    "start_time": getattr(sec, "start_time", None),
                    "end_time": getattr(sec, "end_time", None),
                    "musical_features": getattr(sec, "musical_features", None),
                    "section_emotion": getattr(sec, "section_emotion", None),
                    "suggested_visual_intensity": getattr(sec, "suggested_visual_intensity", None),
                    "suggested_rhythmic_strategy": getattr(sec, "suggested_rhythmic_strategy", None),
                    "suggested_visual_theme": getattr(sec, "suggested_visual_theme", None),
                    "suggested_context": getattr(sec, "suggested_context", None),
                    "created_at": utc_isoformat(getattr(sec, "created_at", None)),
                    "updated_at": utc_isoformat(getattr(sec, "updated_at", None)),
                })
    
    # 3. 获取角色数据（含当前版本）；角色版本与多视角图均批量拉取，避免 for 循环内调 DB（N+1）
    if record.conversation_id and record.thread_id:
        characters = await get_characters_by_conversation(record.conversation_id, record.thread_id)
        full_data["characters_data"] = []
        char_uuids = [getattr(c, "uuid", None) for c in characters if getattr(c, "uuid", None)]
        versions_by_char = await get_character_versions_batch(char_uuids, record.user_id) if char_uuids else {}
        multiview_ids = []
        for char_uuid, versions in versions_by_char.items():
            for version in versions:
                vid = getattr(version, "selected_multi_view_version_id", None) or getattr(version, "multi_view_image_version_id", None)
                if vid:
                    multiview_ids.append(vid)
        multiview_map = await get_character_multi_view_image_versions_by_ids(multiview_ids) if multiview_ids else {}

        for char in characters:
            versions = versions_by_char.get(getattr(char, "uuid", None), [])
            current_image_url = getattr(char, "image_url", None) or ""
            current_prompt = None
            if versions:
                current_version = None
                idx_cur = getattr(char, "current_version_index", 0)
                if idx_cur < len(versions):
                    current_version = versions[idx_cur]
                else:
                    current_version = versions[-1]
                if current_version:
                    current_image_url = getattr(current_version, "character_image_url", None) or current_image_url
                    current_prompt = getattr(current_version, "t2i_prompt", None)
            versions_data = []
            for idx, version in enumerate(versions):
                multiview_version_data = None
                multiview_version_id = getattr(version, "selected_multi_view_version_id", None) or getattr(version, "multi_view_image_version_id", None)
                if multiview_version_id and multiview_version_id in multiview_map:
                    multiview_version = multiview_map[multiview_version_id]
                    multiview_version_data = {
                        "uuid": multiview_version.uuid,
                        "version_number": multiview_version.version_number,
                        "multi_view_image_url": multiview_version.multi_view_image_url,
                        "multi_view_prompt": getattr(multiview_version, "multi_view_prompt", None),
                        "t2i_prompt": getattr(multiview_version, "multi_view_prompt", None),
                        "success": getattr(multiview_version, "success", True),
                        "error_msg": getattr(multiview_version, "error_msg", None),
                        "raw_error_msg": getattr(multiview_version, "raw_error_msg", None),
                        "created_at": utc_isoformat(getattr(multiview_version, "created_at", None)),
                        "model": getattr(multiview_version, "model", None),
                        "provider": getattr(multiview_version, "provider", None),
                        "aspect_ratio": getattr(multiview_version, "aspect_ratio", None),
                        "resolution": getattr(multiview_version, "resolution", None),
                        "seed": getattr(multiview_version, "seed", None),
                    }
                versions_data.append({
                    "uuid": getattr(version, "uuid", None),
                    "version_number": getattr(version, "version_number", 0),
                    "character_image_url": getattr(version, "character_image_url", None),
                    "t2i_prompt": getattr(version, "t2i_prompt", None),
                    "reference_image_urls": getattr(version, "reference_image_urls", None) or [],
                    "is_current": idx == (getattr(char, "current_version_index", 0) if getattr(char, "current_version_index", 0) < len(versions) else len(versions) - 1),
                    "multiview": multiview_version_data,
                    "created_at": utc_isoformat(getattr(version, "created_at", None)),
                    "provider": getattr(version, "provider", None),
                    "model": getattr(version, "model", None),
                    "image_generation_tool": getattr(version, "image_generation_tool", None),
                    "aspect_ratio": getattr(version, "aspect_ratio", None),
                    "resolution": getattr(version, "resolution", None),
                    "seed": getattr(version, "seed", None),
                    "success": getattr(version, "success", True),
                    "error_msg": getattr(version, "error_msg", None),
                    "raw_error_msg": getattr(version, "raw_error_msg", None),
                    "image_tool_metrics": getattr(version, "image_tool_metrics", None),
                    "tool_duration_sec": getattr(version, "tool_duration_sec", None),
                    "tool_cost": getattr(version, "tool_cost", None),
                    "consistency_display_lines": build_image_consistency_display_lines(getattr(version, "image_tool_metrics", None)),
                })
            full_data["characters_data"].append({
                "uuid": getattr(char, "uuid", None),
                "name": getattr(char, "name", None),
                "type": getattr(char, "type", None),
                "description": getattr(char, "description", None),
                "personality": getattr(char, "personality", None),
                "appearance": getattr(char, "appearance", None),
                "role": getattr(char, "role", None),
                "image_url": current_image_url,
                "prompt": current_prompt,
                "current_version_index": getattr(char, "current_version_index", 0),
                "versions": versions_data,
                "created_at": utc_isoformat(getattr(char, "created_at", None)),
            })
        
        # 4. 场景与镜头：按 thread 拉取（不依赖 record.scene_uuids/shot_uuids），进行中任务也能看到已产生的数据
        if record.conversation_id and record.thread_id:
            scenes = await get_scenes_by_conversation(record.conversation_id, record.thread_id)
            shots = await get_detailed_shots_by_conversation(record.conversation_id, record.thread_id)
            # 场景：VideoSceneDB 全字段，getattr 防御
            scenes_list = [
                {
                    "id": getattr(scene, "id", None),
                    "uuid": getattr(scene, "uuid", None),
                    "user_id": getattr(scene, "user_id", None),
                    "conversation_id": getattr(scene, "conversation_id", None),
                    "thread_id": getattr(scene, "thread_id", None),
                    "run_id": getattr(scene, "run_id", None),
                    "scene_number": getattr(scene, "scene_number", 0),
                    "title": getattr(scene, "title", None),
                    "description": getattr(scene, "description", None),
                    "duration": getattr(scene, "duration", 0),
                    "camera_angle": getattr(scene, "camera_angle", None),
                    "character_action": getattr(scene, "character_action", None),
                    "visual_style": getattr(scene, "visual_style", None),
                    "transition_style": getattr(scene, "transition_style", None),
                    "is_bridge": getattr(scene, "is_bridge", False),
                    "character_ids": getattr(scene, "character_ids", None) or [],
                    "audio_segment_ids": getattr(scene, "audio_segment_ids", None) or [],
                    "chapter_id": getattr(scene, "chapter_id", None),
                    "generation_mode": getattr(scene, "generation_mode", None),
                    "current_version_index": getattr(scene, "current_version_index", 0),
                    "additional_data": getattr(scene, "additional_data", None),
                    "created_at": utc_isoformat(getattr(scene, "created_at", None)),
                    "updated_at": utc_isoformat(getattr(scene, "updated_at", None)),
                }
                for scene in scenes
            ]
            full_data["scenes_data"] = sorted(scenes_list, key=lambda s: (s["scene_number"], s["uuid"] or ""))
            # 镜头：VideoDetailedShotDB 全字段，getattr 防御
            full_data["shots_data"] = [
                {
                    "id": getattr(shot, "id", None),
                    "uuid": getattr(shot, "uuid", None),
                    "user_id": getattr(shot, "user_id", None),
                    "conversation_id": getattr(shot, "conversation_id", None),
                    "thread_id": getattr(shot, "thread_id", None),
                    "run_id": getattr(shot, "run_id", None),
                    "storyboard_detail_id": getattr(shot, "storyboard_detail_id", None),
                    "scene_id": getattr(shot, "scene_id", None),
                    "chapter_id": getattr(shot, "chapter_id", None),
                    "shot_number": getattr(shot, "shot_number", 0),
                    "duration": getattr(shot, "duration", 0),
                    "shot_type": getattr(shot, "shot_type", None),
                    "camera_position": getattr(shot, "camera_position", None),
                    "camera_angle": getattr(shot, "camera_angle", None),
                    "subject_angle": getattr(shot, "subject_angle", None),
                    "subject_pose": getattr(shot, "subject_pose", None),
                    "scene_description": getattr(shot, "scene_description", None),
                    "camera_movement": getattr(shot, "camera_movement", None),
                    "lighting": getattr(shot, "lighting", None),
                    "visual_effects": getattr(shot, "visual_effects", None),
                    "transition": getattr(shot, "transition", None),
                    "dialogue": getattr(shot, "dialogue", None),
                    "sound_effects": getattr(shot, "sound_effects", None),
                    "narration": getattr(shot, "narration", None),
                    "is_bridge": getattr(shot, "is_bridge", False),
                    "character_ids": getattr(shot, "character_ids", None) or [],
                    "audio_segment_ids": getattr(shot, "audio_segment_ids", None) or [],
                    "style_guide": getattr(shot, "style_guide", None),
                    "generation_mode": getattr(shot, "generation_mode", None),
                    "generation_routing": getattr(shot, "generation_routing", None),
                    "additional_data": getattr(shot, "additional_data", None),
                    "created_at": utc_isoformat(getattr(shot, "created_at", None)),
                    "updated_at": utc_isoformat(getattr(shot, "updated_at", None)),
                }
                for shot in shots
            ]
            # 批量解析 chapter_id / audio_segment_ids 为可读信息（避免 N+1）
            chapter_ids = set()
            for s in scenes_list:
                if s.get("chapter_id"):
                    chapter_ids.add(s["chapter_id"])
            for s in full_data["shots_data"]:
                if s.get("chapter_id"):
                    chapter_ids.add(s["chapter_id"])
            chapters = await get_chapters_by_uuids(list(chapter_ids)) if chapter_ids else []
            chapter_id_to_info = {}
            for ch in chapters:
                cid = getattr(ch, "uuid", None)
                if cid:
                    chapter_id_to_info[cid] = {
                        "order": chapter_uuid_to_normalized_order.get(
                            cid, getattr(ch, "order", 0)
                        ),
                        "title": getattr(ch, "title", None) or "",
                        "description": getattr(ch, "description", None) or "",
                        "duration": getattr(ch, "duration", None),
                        "audio_section_uuid": getattr(ch, "audio_section_uuid", None),
                        "current_version_index": getattr(ch, "current_version_index", 0),
                    }
            full_data["chapter_id_to_info"] = chapter_id_to_info
            audio_segment_ids = set()
            for s in scenes_list:
                for aid in (s.get("audio_segment_ids") or []):
                    audio_segment_ids.add(aid)
            for s in full_data["shots_data"]:
                for aid in (s.get("audio_segment_ids") or []):
                    audio_segment_ids.add(aid)
            segments = await get_audio_segments_by_uuids(list(audio_segment_ids)) if audio_segment_ids else []
            transcription_uuids = list({getattr(seg, "transcription_uuid", None) for seg in segments if getattr(seg, "transcription_uuid", None)})
            transcription_map = await get_video_audio_transcriptions_by_uuids(transcription_uuids) if transcription_uuids else {}
            audio_segment_id_to_info = {}
            for seg in segments:
                sid = getattr(seg, "uuid", None)
                if sid:
                    tid = getattr(seg, "transcription_uuid", None)
                    trans = transcription_map.get(tid) if tid else None
                    parent_audio_url = getattr(trans, "audio_url", None) if trans else None
                    audio_segment_id_to_info[sid] = {
                        "segment_id": getattr(seg, "segment_id", None),
                        "text": getattr(seg, "text", None) or "",
                        "duration": getattr(seg, "duration", None),
                        "start": getattr(seg, "start", None),
                        "end": getattr(seg, "end", None),
                        "emotion": getattr(seg, "emotion", None),
                        "tempo": getattr(seg, "tempo", None),
                        "vocal_presence": getattr(seg, "vocal_presence", None),
                        "parent_audio_url": parent_audio_url,
                    }
            full_data["audio_segment_id_to_info"] = audio_segment_id_to_info
        else:
            full_data["scenes_data"] = []
            full_data["shots_data"] = []
            full_data["chapter_id_to_info"] = {}
            full_data["audio_segment_id_to_info"] = {}
        
        # 5. 获取关键帧数据（含当前版本；CRUD 已按 shot_number 排序）
        keyframes = await get_keyframes_by_conversation(record.conversation_id, record.thread_id)
        if keyframes:
            keyframe_ids = [kf.uuid for kf in keyframes]
            keyframe_versions = await get_keyframe_versions_by_keyframe_ids(keyframe_ids)
            keyframe_version_map = {}
            for version in keyframe_versions:
                kf_id = version.keyframe_id
                if kf_id not in keyframe_version_map:
                    keyframe_version_map[kf_id] = []
                keyframe_version_map[kf_id].append(version)
            full_data["keyframes_data"] = []
            for kf in keyframes:
                versions = keyframe_version_map.get(kf.uuid, [])
                if versions:
                    current_version = None
                    if kf.current_version_index < len(versions):
                        current_version = versions[kf.current_version_index]
                    else:
                        current_version = versions[-1]
                    versions_data = []
                    for idx, version in enumerate(versions):
                        versions_data.append({
                            "uuid": version.uuid,
                            "version_number": version.version_number,
                            "keyframe_url": version.keyframe_url,
                            "t2i_prompt": version.t2i_prompt,
                            "reference_image_urls": version.reference_image_urls or [],
                            "character_version_ids": version.character_version_ids or [],
                            "is_current": idx == (kf.current_version_index if kf.current_version_index < len(versions) else len(versions) - 1),
                            "created_at": utc_isoformat(getattr(version, 'created_at', None)),
                            "provider": getattr(version, "provider", None),
                            "model": getattr(version, "model", None),
                            "image_generation_tool": getattr(version, "image_generation_tool", None),
                            "aspect_ratio": getattr(version, "aspect_ratio", None),
                            "resolution": getattr(version, "resolution", None),
                            "seed": getattr(version, "seed", None),
                            "success": getattr(version, "success", True),
                            "error_msg": getattr(version, "error_msg", None),
                            "raw_error_msg": getattr(version, "raw_error_msg", None),
                            "image_tool_metrics": getattr(version, "image_tool_metrics", None),
                            "tool_duration_sec": getattr(version, "tool_duration_sec", None),
                            "tool_cost": getattr(version, "tool_cost", None),
                            "consistency_display_lines": build_image_consistency_display_lines(getattr(version, "image_tool_metrics", None)),
                        })
                    full_data["keyframes_data"].append({
                        "uuid": kf.uuid,
                        "shot_number": kf.shot_number,
                        "keyframe_url": current_version.keyframe_url,
                        "t2i_prompt": current_version.t2i_prompt,
                        "reference_image_urls": current_version.reference_image_urls or [],
                        "character_version_ids": current_version.character_version_ids or [],
                        "current_version_index": kf.current_version_index,
                        "versions": versions_data,
                        "created_at": utc_isoformat(kf.created_at),
                    })
        else:
            full_data["keyframes_data"] = []
        
        # 6. 获取视频数据（含当前版本）；按 shot_number 排序展示
        videos = await get_video_generations_by_conversation(record.conversation_id, record.thread_id)
        if videos:
            videos = sorted(videos, key=lambda v: v.shot_number)
            video_ids = [v.uuid for v in videos]
            video_versions = await get_video_generation_versions_by_video_generation_ids(video_ids)
            video_version_map = {}
            for version in video_versions:
                vid_id = version.video_generation_id
                if vid_id not in video_version_map:
                    video_version_map[vid_id] = []
                video_version_map[vid_id].append(version)
            full_data["videos_data"] = []
            for video in videos:
                versions = video_version_map.get(video.uuid, [])
                if versions:
                    # 找到当前版本（根据 current_version_index）
                    current_version = None
                    if video.current_version_index < len(versions):
                        current_version = versions[video.current_version_index]
                    else:
                        current_version = versions[-1]  # 取最新版本
                    
                    # 构建所有版本数据
                    versions_data = []
                    for idx, version in enumerate(versions):
                        v_metrics = getattr(version, "video_tool_metrics", None)
                        v_duration = getattr(version, "tool_duration_sec", None)
                        v_cost = getattr(version, "tool_cost", None)
                        if isinstance(v_metrics, dict) and (v_duration is not None or v_cost is not None):
                            v_metrics = dict(v_metrics)
                            if v_duration is not None:
                                v_metrics["tool_duration_sec"] = v_duration
                            if v_cost is not None:
                                v_metrics["tool_cost"] = v_cost
                        _ad = getattr(version, "additional_data", None) or {}
                        versions_data.append({
                            "uuid": version.uuid,
                            "version_number": version.version_number,
                            "video_url": version.video_url,
                            "motion_prompt": getattr(version, "motion_prompt", None),
                            "duration": getattr(version, "duration", None),
                            "is_current": idx == (video.current_version_index if video.current_version_index < len(versions) else len(versions) - 1),
                            "created_at": utc_isoformat(getattr(version, 'created_at', None)),
                            "provider": getattr(version, "provider", None),
                            "model": getattr(version, "model", None),
                            "video_generation_tool": getattr(version, "video_generation_tool", None),
                            "keyframe_url": getattr(version, "keyframe_url", None),
                            "keyframe_version_ids": getattr(version, "keyframe_version_ids", None) or [],
                            "aspect_ratio": getattr(version, "aspect_ratio", None),
                            "resolution": getattr(version, "resolution", None),
                            "negative_prompt": getattr(version, "negative_prompt", None),
                            "style": getattr(version, "style", None),
                            "seed": getattr(version, "seed", None),
                            "generation_mode": getattr(version, "generation_mode", None),
                            "success": getattr(version, "success", True),
                            "error_msg": getattr(version, "error_msg", None),
                            "raw_error_msg": getattr(version, "raw_error_msg", None),
                            "video_tool_metrics": v_metrics,
                            "tool_duration_sec": v_duration,
                            "tool_cost": v_cost,
                            "consistency_display_lines": build_video_consistency_display_lines(getattr(version, "video_tool_metrics", None)),
                            "consistency_reason": _ad.get("consistency_reason"),
                        })

                    full_data["videos_data"].append({
                        "uuid": video.uuid,
                        "shot_number": video.shot_number,
                        "video_url": current_version.video_url,
                        "motion_prompt": getattr(current_version, "motion_prompt", None),
                        "duration": getattr(current_version, "duration", None),
                        "current_version_index": video.current_version_index,
                        "versions": versions_data,
                        "created_at": utc_isoformat(video.created_at)
                    })
        else:
            full_data["videos_data"] = []
        
        # 7. 获取旁白数据（需要从版本表获取实际数据）
        if record.narration_uuids:
            # 获取所有旁白主表记录
            narrations = await get_narrations_by_conversation(record.conversation_id, record.thread_id)
            full_data["narrations_data"] = []
            
            # 批量获取所有旁白版本 - 避免N+1
            if narrations:
                narration_ids = [n.uuid for n in narrations]
                all_narration_versions = await get_narration_versions_by_narration_ids(narration_ids)
                
                # 按narration_id分组（version 为 VideoNarrationVersionDB，用属性访问）
                versions_by_narration = {}
                for version in all_narration_versions:
                    narration_id = version.narration_id
                    if narration_id not in versions_by_narration:
                        versions_by_narration[narration_id] = []
                    versions_by_narration[narration_id].append(version)
            
            for narration in narrations:
                # 获取该旁白的所有版本（从已查询的数据中获取）
                versions = sorted(versions_by_narration.get(narration.uuid, []), key=lambda v: v.version_number)
                
                if versions:
                    # 使用当前版本
                    current_version = None
                    if narration.current_version_index < len(versions):
                        current_version = versions[narration.current_version_index]
                    else:
                        current_version = versions[-1]
                    
                    full_data["narrations_data"].append({
                        "uuid": narration.uuid,
                        "version_uuid": getattr(current_version, "uuid", None),
                        "shot_number": narration.shot_number,
                        "narration_text": current_version.narration_text,
                        "audio_url": current_version.audio_url,
                        "duration": current_version.duration,
                        "created_at": utc_isoformat(narration.created_at)
                    })
        else:
            full_data["narrations_data"] = []
        
        # 8. 获取音效数据（需要从版本表获取实际数据）
        if record.audio_effect_uuids:
            audio_effects = await get_audio_effects_by_conversation(record.conversation_id, record.thread_id)
            full_data["audio_effects_data"] = []
            
            # 批量获取所有音效版本 - 避免N+1
            if audio_effects:
                effect_ids = [e.uuid for e in audio_effects]
                all_effect_versions = await get_audio_effect_versions_by_audio_effect_ids(effect_ids)
                
                # 按audio_effect_id分组（version 为 VideoAudioEffectVersionDB，用属性访问）
                versions_by_effect = {}
                for version in all_effect_versions:
                    effect_id = version.audio_effect_id
                    if effect_id not in versions_by_effect:
                        versions_by_effect[effect_id] = []
                    versions_by_effect[effect_id].append(version)
            
            for effect in audio_effects:
                # 获取该音效的所有版本（从已查询的数据中获取）
                versions = sorted(versions_by_effect.get(effect.uuid, []), key=lambda v: v.version_number)
                
                if versions:
                    # 使用当前版本
                    current_version = None
                    if effect.current_version_index < len(versions):
                        current_version = versions[effect.current_version_index]
                    else:
                        current_version = versions[-1]
                    
                    full_data["audio_effects_data"].append({
                        "uuid": effect.uuid,
                        "version_uuid": getattr(current_version, "uuid", None),
                        "shot_number": effect.shot_number,
                        "audio_prompt": current_version.audio_prompt,
                        "audio_url": current_version.audio_url,
                        "duration": current_version.duration,
                        "created_at": utc_isoformat(effect.created_at)
                    })
        else:
            full_data["audio_effects_data"] = []
        
        # 9. 获取音乐数据（需要从版本表获取实际数据）；有音频片段时也拉取音乐，用 music 版本的 music_url 作为分段音频展示
        has_segments = bool(full_data.get("audio_segment_id_to_info"))
        if record.music_generation_uuids or has_segments:
            music_list = await get_music_generations_by_conversation(record.conversation_id, record.thread_id)
            full_data["music_data"] = []
            segment_audio_url_map = {}  # audio_segment_id -> 切割后的片段音频 URL (music_url，来自 music generation version)
            segment_to_music_version = {}

            # 批量获取所有音乐版本 - 避免N+1
            if music_list:
                music_ids = [m.uuid for m in music_list]
                all_music_versions = await get_music_generation_versions_by_music_generation_ids(music_ids)

                # 按music_generation_id分组（version 为 VideoMusicGenerationVersionDB，用属性访问）
                versions_by_music = {}
                for version in all_music_versions:
                    music_id = version.music_generation_id
                    if music_id not in versions_by_music:
                        versions_by_music[music_id] = []
                    versions_by_music[music_id].append(version)

                for music in music_list:
                    # 获取该音乐的所有版本（从已查询的数据中获取）
                    versions = sorted(versions_by_music.get(music.uuid, []), key=lambda v: v.version_number)

                    if versions:
                        # 使用当前版本
                        current_version = None
                        if music.current_version_index < len(versions):
                            current_version = versions[music.current_version_index]
                        else:
                            current_version = versions[-1]

                        version_uuid = getattr(current_version, "uuid", None)
                        # 构建片段 -> 切割后音频 URL（music 版本的 music_url 即分段音频）；以及 segment -> music/version 映射便于 debug
                        if getattr(current_version, "music_url", None):
                            seg_ids = []
                            if getattr(current_version, "audio_segment_id", None):
                                seg_ids.append(current_version.audio_segment_id)
                            if getattr(current_version, "audio_segment_ids", None):
                                seg_ids.extend(current_version.audio_segment_ids)
                            for seg_id in seg_ids:
                                if seg_id:
                                    segment_audio_url_map[seg_id] = current_version.music_url
                                    segment_to_music_version[seg_id] = {
                                        "music_generation_uuid": music.uuid,
                                        "music_generation_version_uuid": version_uuid,
                                    }

                        full_data["music_data"].append({
                            "uuid": music.uuid,
                            "version_uuid": version_uuid,
                            "music_url": current_version.music_url,
                            "music_prompt": current_version.music_prompt,
                            "duration": current_version.duration,
                            "created_at": utc_isoformat(music.created_at)
                        })
            # 回填 audio_segment_id_to_info：展示用 segment_audio_url（分段音频，来自 music generation version）；以及 segment -> music/version 映射便于 debug
            for sid, info in full_data.get("audio_segment_id_to_info", {}).items():
                info["segment_audio_url"] = segment_audio_url_map.get(sid)
                mapping = segment_to_music_version.get(sid)
                if mapping:
                    info["music_generation_uuid"] = mapping["music_generation_uuid"]
                    info["music_generation_version_uuid"] = mapping["music_generation_version_uuid"]
        else:
            full_data["music_data"] = []
            for sid, info in full_data.get("audio_segment_id_to_info", {}).items():
                info["segment_audio_url"] = None
        
        # 10. 获取视频片段数据（需要从版本表获取实际数据）
        if record.video_segments_uuids:
            from ..crud.video.video_segment import get_video_segment_versions_by_segment_ids
            
            # 获取所有视频片段主表记录（使用新CRUD）
            segments = await get_video_segments_with_data_by_run_id(record.task_id)
            
            # 批量获取所有片段版本 - 避免N+1
            segment_ids = [s.uuid for s in segments]
            versions_by_segment = await get_video_segment_versions_by_segment_ids(segment_ids)
            
            full_data["video_segments_data"] = []
            for segment in segments:
                # 获取该片段的所有版本（从已查询的数据中获取）
                versions = versions_by_segment.get(segment.uuid, [])
                
                if versions:
                    # 使用当前版本
                    current_version = None
                    if segment.current_version_index < len(versions):
                        current_version = versions[segment.current_version_index]
                    else:
                        current_version = versions[-1]
                    
                    full_data["video_segments_data"].append({
                        "uuid": segment.uuid,
                        "version_uuid": getattr(current_version, "uuid", None),
                        "segment_number": segment.segment_number,
                        "video_url": current_version.video_url,
                        "created_at": utc_isoformat(segment.created_at)
                    })
        else:
            full_data["video_segments_data"] = []
    
    # 11. 获取视频合成数据
    if record.video_assembly_uuid:
        assembly = await get_video_assembly_by_uuid(record.video_assembly_uuid)
        if assembly:
            full_data["video_assembly_data"] = {
                "uuid": assembly.uuid,
                "final_video_url": assembly.final_video_url,
                "total_duration": assembly.total_duration,
                "success": assembly.success,
                "created_at": utc_isoformat(assembly.created_at)
            }
    
    # 12. 获取融合图数据
    if record.conversation_id and record.thread_id:
        # 使用CRUD查询（暂时保持dict返回）
        from ..crud.video.video_character import get_character_fusion_images_by_character_ids
        
        # 获取该对话下所有角色ID
        if record.character_uuids:
            fusion_images = await get_character_fusion_images_by_character_ids(record.character_uuids)
        else:
            fusion_images = []
        
        full_data["fusion_images_data"] = [
            {
                "uuid": getattr(fusion, "uuid", None),
                "fusion_key": getattr(fusion, "fusion_key", None),
                "image_type": getattr(fusion, "image_type", None),
                "character_ids": getattr(fusion, "character_ids", None) or [],
                "fusion_image_url": getattr(fusion, "fusion_image_url", None),
                "fusion_prompt": getattr(fusion, "fusion_prompt", None),
                "model": getattr(fusion, "model", None),
                "provider": getattr(fusion, "provider", None),
                "success": getattr(fusion, "success", True),
                "error_msg": getattr(fusion, "error_msg", None),
                "created_at": utc_isoformat(getattr(fusion, "created_at", None)),
            }
            for fusion in fusion_images
        ]
        
        # 13. 获取多视角图数据（批量查询，避免 N+1）
        from ..crud.video.video_character import get_character_multi_view_images_batch
        
        multiview_latest = await get_character_multi_view_images_batch(char_uuids, record.user_id) if char_uuids else {}
        full_data["multiview_images_data"] = []
        for char_uuid, version in multiview_latest.items():
            if not version:
                continue
            full_data["multiview_images_data"].append({
                "uuid": getattr(version, "multi_view_image_id", None),
                "version_uuid": getattr(version, "uuid", None),
                "video_character_id": char_uuid,
                "multi_view_image_url": getattr(version, "multi_view_image_url", None),
                "multi_view_prompt": getattr(version, "multi_view_prompt", None),
                "version_number": getattr(version, "version_number", None),
                "model": getattr(version, "model", None),
                "provider": getattr(version, "provider", None),
                "success": getattr(version, "success", True),
                "error_msg": getattr(version, "error_msg", None),
                "created_at": utc_isoformat(getattr(version, "created_at", None)),
            })
    
    # 14. 获取该任务的所有编辑记录（CRUD 返回 (list, total)，取列表；大 page_size 拉全量）
    shot_edits, _ = await get_shot_edit_records(task_id=record.task_id, page=1, page_size=9999)
    full_data["shot_edits"] = [
        {
            "id": getattr(edit, "id", None),
            "uuid": getattr(edit, "uuid", None),
            "shot_number": getattr(edit, "shot_number", None),
            "old_version_number": getattr(edit, "old_version_number", None),
            "new_version_number": getattr(edit, "new_version_number", None),
            "old_video_url": getattr(edit, "old_video_url", None),
            "new_video_url": getattr(edit, "new_video_url", None),
            "model": getattr(edit, "model", None),
            "old_prompt": getattr(edit, "old_prompt", None),
            "new_prompt": getattr(edit, "new_prompt", None),
            "user_action": getattr(edit, "user_action", None),
            "success": getattr(edit, "success", None),
            "cascaded_from_storyboard_edit_id": getattr(edit, "cascaded_from_storyboard_edit_id", None),
            "user_feedback": getattr(edit, "user_feedback", None),
            "edit_instruction": getattr(edit, "edit_instruction", None),
            "error_msg": getattr(edit, "error_msg", None),
            "duration": getattr(edit, "duration", None),
            "created_at": utc_isoformat(getattr(edit, "created_at", None)),
        }
        for edit in shot_edits
    ]
    
    storyboard_edits, _ = await get_storyboard_edit_records(task_id=record.task_id, page=1, page_size=9999)
    full_data["storyboard_edits"] = [
        {
            "id": getattr(edit, "id", None),
            "uuid": getattr(edit, "uuid", None),
            "shot_number": getattr(edit, "shot_number", None),
            "old_version_number": getattr(edit, "old_version_number", None),
            "new_version_number": getattr(edit, "new_version_number", None),
            "old_image_url": getattr(edit, "old_image_url", None),
            "new_image_url": getattr(edit, "new_image_url", None),
            "model": getattr(edit, "model", None),
            "old_prompt": getattr(edit, "old_prompt", None),
            "new_prompt": getattr(edit, "new_prompt", None),
            "user_action": getattr(edit, "user_action", None),
            "success": getattr(edit, "success", None),
            "user_feedback": getattr(edit, "user_feedback", None),
            "edit_instruction": getattr(edit, "edit_instruction", None),
            "error_msg": getattr(edit, "error_msg", None),
            "created_at": utc_isoformat(getattr(edit, "created_at", None)),
        }
        for edit in storyboard_edits
    ]
    
    character_edits, _ = await get_character_edit_records(task_id=record.task_id, page=1, page_size=9999)
    full_data["character_edits"] = [
        {
            "id": getattr(edit, "id", None),
            "uuid": getattr(edit, "uuid", None),
            "character_name": getattr(edit, "character_name", None),
            "old_version_number": getattr(edit, "old_version_number", None),
            "new_version_number": getattr(edit, "new_version_number", None),
            "old_image_url": getattr(edit, "old_image_url", None),
            "new_image_url": getattr(edit, "new_image_url", None),
            "model": getattr(edit, "model", None),
            "old_prompt": getattr(edit, "old_prompt", None),
            "new_prompt": getattr(edit, "new_prompt", None),
            "user_action": getattr(edit, "user_action", None),
            "success": getattr(edit, "success", None),
            "user_feedback": getattr(edit, "user_feedback", None),
            "edit_instruction": getattr(edit, "edit_instruction", None),
            "error_msg": getattr(edit, "error_msg", None),
            "created_at": utc_isoformat(getattr(edit, "created_at", None)),
        }
        for edit in character_edits
    ]
    
    return full_data
