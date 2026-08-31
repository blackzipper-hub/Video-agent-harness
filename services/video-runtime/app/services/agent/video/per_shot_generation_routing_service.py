"""
Per-shot 生成路由节点：assign_generation_mode_to_shots + 写入 generation_routing（口型 auto 时按分辨率固定工具，不再调用结构化 LLM）。

`resolve_user_option_for_shot` 在 **`per_shot_user_option_resolve.py`**（keyframe / video 执行前合并 per-shot 工具覆盖），亦可从本模块 re-export 导入。
"""
import logging
from typing import Any, Dict, List, Optional, Union

from langgraph.runtime import Runtime

from ....models.tool_enums import ContentCategory, GenerationMode, Resolution
from ....models.user_options import ImageGenerationTool, UserOption, VideoGenerationTool
from ....models.video_state import AudioTranscription, VideoAgentState
from .per_shot_user_option_resolve import resolve_user_option_for_shot
from ..schemas import VideoContextSchema

logger = logging.getLogger(__name__)


def _has_vocal(shot: Any, uuid_to_segment: dict) -> bool:
    """判断 shot 对应音频片段是否有人声：优先 vocal_presence，兼容老数据回退到 text 非空。"""
    for seg_uuid in (getattr(shot, "audio_segment_ids", None) or []):
        seg = uuid_to_segment.get(seg_uuid)
        if not seg:
            continue
        vp = getattr(seg, "vocal_presence", None)
        if vp is True:
            return True
        if vp is None and seg.text and seg.text.strip():
            return True
    return False


def _is_person_shot(shot: Any, character_type_map: Optional[Dict[str, str]]) -> bool:
    """判断 shot 是否包含至少一个人物角色（非空镜）。无 type 映射时默认 True（兼容）。"""
    if not character_type_map:
        return True
    for cid in (getattr(shot, "character_ids", None) or []):
        if character_type_map.get(cid, "character") == "character":
            return True
    return False


def assign_generation_mode_to_shots(
    shots: List[Any],
    audio_transcription: Optional[AudioTranscription],
    content_category: Optional[str] = None,
    lipsync_coverage: int = 0,
    character_type_map: Optional[Dict[str, str]] = None,
    allow_lipsync: bool = True,
) -> List[Any]:
    """收口 shot.generation_mode：不根据 lipsync_coverage / Lip-Sync MV 升格口型，只尊重上游已写入的模式。

    规则：
    1. 无人物角色（character type）→ empty_shot
    2. generation_mode 未设置或无法识别 → normal（不得因有人声升格 lipsync）
    3. normal / empty_shot → 保持，永不升格 lipsync
    4. 仅上游已标 lipsync 的镜可保留 lipsync，且须有人声；无人声则降为 normal

    content_category / lipsync_coverage 保留参数以兼容调用方，本函数不再用于按覆盖率抽选口型镜。
    Product Launch 无人物但有 narration 时保留 normal（画外音），不降为 empty_shot。
    allow_lipsync=False 时强制将上游 lipsync 降为 normal（BGM / 无分段等）。
    """
    _ = lipsync_coverage

    if not allow_lipsync:
        downgraded = 0
        for shot in shots:
            gm = getattr(shot, "generation_mode", None)
            if gm and str(gm).strip().lower() == GenerationMode.LIPSYNC.value:
                shot.generation_mode = GenerationMode.NORMAL.value
                downgraded += 1
        if downgraded:
            logger.info(
                "🎭 assign_generation_mode: allow_lipsync=False，已将 %d 个 lipsync 镜降为 normal",
                downgraded,
            )
        if not audio_transcription:
            return shots

    _is_product_launch = content_category == ContentCategory.PRODUCT_LAUNCH.value

    if not audio_transcription:
        empty_shot_count = 0
        lipsync_downgraded = 0
        voiceover_count = 0
        for shot in shots:
            if not _is_person_shot(shot, character_type_map):
                _narration = (getattr(shot, "narration", None) or "").strip()
                if _is_product_launch and _narration:
                    shot.generation_mode = GenerationMode.NORMAL.value
                    voiceover_count += 1
                    continue
                shot.generation_mode = GenerationMode.EMPTY_SHOT.value
                empty_shot_count += 1
                continue
            gm = getattr(shot, "generation_mode", None)
            gm_norm = str(gm).strip().lower() if gm else ""
            if gm_norm == GenerationMode.LIPSYNC.value:
                if not (getattr(shot, "narration", None) or "").strip():
                    shot.generation_mode = GenerationMode.NORMAL.value
                    lipsync_downgraded += 1
                    continue
            if not getattr(shot, "generation_mode", None):
                shot.generation_mode = GenerationMode.NORMAL.value
        if lipsync_downgraded:
            logger.info(
                "🎭 assign_generation_mode: 无音乐转录，已将 %d 个无旁白 lipsync 镜降为 normal（Product Launch 等）",
                lipsync_downgraded,
            )
        if voiceover_count:
            logger.info(
                "🎭 assign_generation_mode: Product Launch 无人物但有旁白，画外音 normal=%d",
                voiceover_count,
            )
        if empty_shot_count:
            logger.info(
                "🎭 assign_generation_mode: 无音频，空镜=%d / %d",
                empty_shot_count,
                len(shots),
            )
        return shots

    uuid_to_segment = {seg.uuid: seg for seg in audio_transcription.segments}
    lipsync_count = 0
    empty_shot_count = 0

    for shot in shots:
        if not _is_person_shot(shot, character_type_map):
            shot.generation_mode = GenerationMode.EMPTY_SHOT.value
            empty_shot_count += 1
            continue

        gm = getattr(shot, "generation_mode", None)
        if not gm or not str(gm).strip():
            shot.generation_mode = GenerationMode.NORMAL.value
            continue

        gm_norm = str(gm).strip().lower()
        if gm_norm == GenerationMode.EMPTY_SHOT.value:
            shot.generation_mode = GenerationMode.EMPTY_SHOT.value
            continue
        if gm_norm == GenerationMode.NORMAL.value:
            shot.generation_mode = GenerationMode.NORMAL.value
            continue
        if gm_norm == GenerationMode.LIPSYNC.value:
            if _has_vocal(shot, uuid_to_segment):
                shot.generation_mode = GenerationMode.LIPSYNC.value
                lipsync_count += 1
            else:
                shot.generation_mode = GenerationMode.NORMAL.value
            continue

        shot.generation_mode = GenerationMode.NORMAL.value

    logger.info(
        "🎭 assign_generation_mode: %d lipsync / %d total (空镜=%d, 仅保留上游 lipsync)",
        lipsync_count,
        len(shots),
        empty_shot_count,
    )
    return shots


def _lipsync_tool_value_for_auto_resolution(user_option: UserOption) -> str:
    """复用 resolve_effective_lipsync_tool（单一来源）。"""
    from ....models.user_options import resolve_effective_lipsync_tool
    return resolve_effective_lipsync_tool(user_option).value


async def per_shot_generation_routing_node(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any,
) -> Union[VideoAgentState, Dict[str, Any]]:
    """按规则分配 generation_mode；写入每镜 generation_routing（图像/普通视频默认 auto；口型 auto 时按分辨率固定工具）。不调用 per-shot 结构化 LLM。"""
    user_input_data = state.get("user_input_data")
    user_option = user_input_data.user_option if user_input_data else None
    shot_uuids = state.get("shot_uuids", [])
    if not user_option or not shot_uuids:
        logger.info(
            "[per_shot_generation_routing] 跳过：无 user_option 或 shot_uuids thread_id=%s",
            state.get("thread_id"),
        )
        return {}

    from ....services.agent.utils.database_utils import get_detailed_shots_from_db

    shots_data = await get_detailed_shots_from_db(shot_uuids)
    if not shots_data:
        logger.warning(
            "[per_shot_generation_routing] 未查到镜头 thread_id=%s",
            state.get("thread_id"),
        )
        return {}

    thread_id = state.get("thread_id", "") or ""
    run_id = state.get("run_id", "") or ""

    audio_transcription_uuids = state.get("audio_transcription_uuids", [])
    _at: Optional[AudioTranscription] = None
    if audio_transcription_uuids:
        from ....services.agent.utils.database_utils import get_audio_transcription_from_db

        _at = await get_audio_transcription_from_db(audio_transcription_uuids)
        if not _at:
            logger.info("[per_shot_generation_routing] 无转录数据，跳过 assign thread_id=%s", thread_id)
    else:
        logger.info(
            "[per_shot_generation_routing] 无 audio_transcription_uuids，跳过 assign thread_id=%s",
            thread_id,
        )

    from ....models.user_options import should_enable_lipsync_for_run

    allow_lipsync = should_enable_lipsync_for_run(
        user_option,
        music_intent=state.get("music_intent"),
        music_workflow_mode=state.get("music_workflow_mode"),
        audio_transcription=_at,
    )

    _is_product_launch = user_option.content_category == ContentCategory.PRODUCT_LAUNCH
    assign_ran = bool(_at or not allow_lipsync or _is_product_launch)
    if assign_ran:
        _cc = user_option.content_category.value if user_option.content_category else None
        _lc = 100 if _cc in (ContentCategory.LIP_SYNC_MV.value, ContentCategory.PRODUCT_LAUNCH.value) else getattr(user_option, "lipsync_coverage", 0)

        _char_type_map: Dict[str, str] = {}
        _all_char_ids = set()
        for shot in shots_data:
            _all_char_ids.update(getattr(shot, "character_ids", None) or [])
        if _all_char_ids:
            from ....services.agent.utils.database_utils import get_characters_from_db

            _chars = await get_characters_from_db(list(_all_char_ids))
            for c in _chars:
                _char_type_map[c.id] = getattr(c, "type", "character")
                if hasattr(c.type, "value"):
                    _char_type_map[c.id] = c.type.value

        assign_generation_mode_to_shots(
            shots_data, _at, _cc, _lc, _char_type_map, allow_lipsync=allow_lipsync
        )

    lipsync_auto = user_option.lipsync_video_tool == VideoGenerationTool.AUTO
    fixed_lipsync_val = _lipsync_tool_value_for_auto_resolution(user_option) if lipsync_auto else VideoGenerationTool.AUTO.value

    logger.info(
        "[per_shot_generation_routing] lipsync_routing_diag thread_id=%s run_id=%s "
        "user_lipsync_video_tool=%s lipsync_auto=%s resolution=%s fixed_lipsync_when_auto=%s "
        "assign_generation_mode_ran=%s",
        thread_id,
        run_id,
        getattr(user_option.lipsync_video_tool, "value", user_option.lipsync_video_tool),
        lipsync_auto,
        getattr(user_option.resolution, "value", user_option.resolution),
        fixed_lipsync_val if lipsync_auto else "(n/a-user-not-auto)",
        assign_ran,
    )

    from ....crud.video.video_story import update_detailed_shot_generation_routing, update_shot_generation_mode

    written = 0
    lipsync_shot_count = 0
    sample_lipsync_routing_val: Optional[str] = None
    for shot in shots_data:
        gm = getattr(shot, "generation_mode", None) or GenerationMode.NORMAL.value
        lip_val = fixed_lipsync_val if (lipsync_auto and gm == GenerationMode.LIPSYNC.value) else VideoGenerationTool.AUTO.value
        if gm == GenerationMode.LIPSYNC.value:
            lipsync_shot_count += 1
            if sample_lipsync_routing_val is None:
                sample_lipsync_routing_val = lip_val
        routing: Dict[str, Any] = {
            "image_generation_tool": ImageGenerationTool.AUTO.value,
            "normal_video_tool": VideoGenerationTool.AUTO.value,
            "lipsync_video_tool": lip_val,
        }
        uuid = getattr(shot, "uuid", None)
        if uuid:
            await update_detailed_shot_generation_routing(uuid, routing)
            written += 1
        if gm:
            await update_shot_generation_mode(shot.uuid, gm)

    logger.info(
        "[per_shot_generation_routing] 无 LLM：写 routing 镜头数=%s generation_mode 写回 thread_id=%s run_id=%s",
        written,
        thread_id,
        run_id,
    )
    logger.info(
        "[per_shot_generation_routing] lipsync_routing_diag thread_id=%s run_id=%s "
        "shots_generation_mode_lipsync=%s sample_lipsync_video_tool_written=%s",
        thread_id,
        run_id,
        lipsync_shot_count,
        sample_lipsync_routing_val,
    )
    return {}
