"""
场景生成功能模块
负责场景生成节点的实现和相关功能
支持层次化生成：章节拆分 + 并行生成
"""
import logging
import math
import asyncio
from typing import List, Optional, Dict, Any, Union, cast, Tuple, Callable
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from ....models.video_state import (
    VideoAgentState,
    StoryOutline,
    CharacterProfile,
    AudioTranscription,
    StoryboardScene,
    StoryboardSceneForLLM,
    ScenesCollection,
    ScenesCollectionForLLM,
    StoryChapter,
    ImageUserInput,
    ContentCategory,
)
from ....models.tool_enums import GenerationMode
from ....models.user_options import UserOption, should_enable_lipsync_for_run
from ....exceptions import BusinessException, BusinessExceptionCode
from ....crud.video.video_story import create_scene, get_scenes_by_conversation
from ....services.agent.base_agent import MessageType
from ..schemas import VideoContextSchema
from app.agent_config.duration import (
    get_audio_driven_duration_values,
    get_audio_driven_split_threshold,
    get_video_driven_duration_values,
)
from ....services.agent.utils.prompt_utils import (
    apply_language_suffix_to_system_message_in_messages,
    attach_images_to_messages,
    log_video_spin_prompt_trace,
)
from ....services.agent.utils.database_utils import get_story_outline_from_db, get_characters_from_db, get_audio_transcription_from_db, get_scenes_from_db
from ..utils.cancellation import raise_if_cancelled
from pydantic import BaseModel, Field
from ....models.video_state import StoryboardScene

# ==================== 从 tools/new/video_scene_tools.py 移入 ====================
class SceneGenerationResult(BaseModel):
    """场景生成结果"""
    success: bool = Field(description="是否成功生成")
    scenes: List[StoryboardScene] = Field(description="生成的场景列表", default=[])


# 章节拆分相关的数据模型和函数
from typing import List as TypingList

class ChapterSegmentInfo(BaseModel):
    """章节片段信息"""
    title: str = Field(description="片段标题")
    description: str = Field(description="片段详细描述")
    duration: float = Field(description="片段时长（秒）")
    content_focus: str = Field(description="内容重点")


class ChapterSegmentationResult(BaseModel):
    """章节拆分结果"""
    segments: TypingList[ChapterSegmentInfo] = Field(description="拆分后的片段列表")

logger = logging.getLogger(__name__)


class ChapterSegment:
    """章节片段类，用于表示章节片段"""
    def __init__(self, chapter_id: str, segment_index: int, start_time: float, duration: float, 
                 title: str, description: str, chapter_title: str, content_focus: str = ""):
        self.chapter_id = chapter_id
        self.segment_index = segment_index
        self.start_time = start_time
        self.duration = duration
        self.title = title
        self.description = description
        self.chapter_title = chapter_title
        self.content_focus = content_focus


def _normalize_scene_llm_generation_mode(raw: Optional[str]) -> Optional[str]:
    from .generation_mode_normalize import normalize_generation_mode

    return normalize_generation_mode(raw)


def _resolve_scene_generation_mode(scene: StoryboardScene) -> str:
    """仅采用 LLM 显式填写的 generation_mode；未填或无法识别则 normal。"""
    from .generation_mode_normalize import coerce_generation_mode

    return coerce_generation_mode(getattr(scene, "generation_mode", None))


def _finalize_scene_generation_mode(scene: StoryboardScene, allow_lipsync: bool, content_category: Optional[str] = None) -> None:
    """定稿 generation_mode；纯 BGM / 无人声等不允许口型时降为 normal。"""
    scene.generation_mode = _resolve_scene_generation_mode(scene)
    if not allow_lipsync and scene.generation_mode == GenerationMode.LIPSYNC.value:
        scene.generation_mode = GenerationMode.NORMAL.value
    if content_category == ContentCategory.PRODUCT_LAUNCH.value and allow_lipsync:
        scene.generation_mode = GenerationMode.LIPSYNC.value


def _visual_enhancement_cues(chapter: Any) -> List[Dict[str, Any]]:
    """提取可拆镜的视觉线索（broll/hard_event）；排除 overlay/animation/title/text。"""
    raw = getattr(chapter, "enhancement_cues", None) or []
    out: List[Dict[str, Any]] = []
    for c in raw:
        if hasattr(c, "model_dump"):
            d = c.model_dump()
        elif isinstance(c, dict):
            d = dict(c)
        else:
            continue
        ctype = str(d.get("type") or "broll").lower()
        if ctype in ("overlay", "animation", "title", "text", "caption", "subtitle"):
            continue
        desc = (d.get("description") or "").strip()
        if not desc:
            continue
        out.append({"type": ctype or "broll", "description": desc, "timestamp_hint": d.get("timestamp_hint")})
    return out


def _llm_scene_to_runtime(scene: StoryboardSceneForLLM) -> StoryboardScene:
    """LLM 场景 → 运行时场景；shot_language/action_beats/hero_moment 写入 additional_data。"""
    from .visual_field_contract import fold_machine_visual_fields

    additional_data = fold_machine_visual_fields(
        shot_language=scene.shot_language,
        action_beats=scene.action_beats,
        hero_moment=scene.hero_moment,
    )
    return StoryboardScene(
        scene_number=scene.scene_number,
        title=scene.title,
        description=scene.description,
        duration=scene.duration,
        camera_angle=scene.camera_angle,
        character_action=scene.character_action,
        visual_style=scene.visual_style,
        transition_style=scene.transition_style,
        is_bridge=scene.is_bridge,
        character_ids=list(scene.character_ids or []),
        audio_segment_ids=scene.audio_segment_ids,
        generation_mode=scene.generation_mode,
        additional_data=additional_data or None,
    )


def _llm_scenes_collection_to_runtime(collection: ScenesCollectionForLLM) -> ScenesCollection:
    return ScenesCollection(scenes=[_llm_scene_to_runtime(s) for s in collection.scenes])


def _target_scene_count_for_chapter(
    total_duration: float,
    duration_based_count: int,
    visual_cue_count: int,
    *,
    min_scene_sec: float = 3.0,
    max_scenes_per_chapter: int = 3,
) -> int:
    """时长切分与 cue 拆镜取较大者，受最短墙钟与每章上限约束。

    - 无视觉 cue（或仅 1 条）：保持 duration_based（SD2 走 split_threshold=8）
    - ≥2 条 broll：允许拆到 min(cue_n, floor(total/min_scene), max_scenes)
    """
    if total_duration <= 0:
        return max(1, duration_based_count)
    max_by_min = max(1, int(total_duration // min_scene_sec))
    if visual_cue_count >= 2:
        cue_target = min(visual_cue_count, max_scenes_per_chapter, max_by_min)
        return max(1, max(duration_based_count, cue_target))
    return max(1, min(duration_based_count, max_by_min))


def _split_duration_evenly(total: float, n: int) -> List[float]:
    """均分时长；最后一槽吃剩余，保证总和精确。"""
    if n <= 1:
        return [round(total, 3)]
    avg = total / n
    parts: List[float] = []
    for i in range(n):
        if i == n - 1:
            parts.append(round(total - sum(parts), 3))
        else:
            parts.append(round(avg, 3))
    return parts


def _compute_scene_structure_for_chapter(
    chapter: StoryChapter,
    audio_transcription: AudioTranscription,
    user_option: Optional[UserOption] = None,
) -> List[Dict[str, Any]]:
    """预先计算章节的场景结构。

    1) 时长：get_audio_driven_split_threshold（SD2=8），禁止 min(supported)=4 切碎
    2) 语义：若 chapter.enhancement_cues 含 ≥2 条 broll，可在最短墙钟约束下拆到最多 3 镜
    槽位不含 generation_mode，由场景 LLM 输出后经 _resolve_scene_generation_mode 定稿。

    Returns:
        场景结构列表，元素含：
            audio_segment_index, audio_segment_uuid, duration, scene_number,
            optional enhancement_cue（绑定的视觉线索）
    """
    split_threshold = get_audio_driven_split_threshold(user_option)
    supported = get_audio_driven_duration_values(user_option) or [4]
    min_scene_sec = min(3.0, float(min(supported)))

    chapter_audio_segment_uuids = chapter.audio_segment_ids or []
    uuid_to_index = {seg.uuid: i for i, seg in enumerate(audio_transcription.segments)}
    uuid_to_segment = {seg.uuid: seg for seg in audio_transcription.segments}

    seg_entries: List[Dict[str, Any]] = []
    for seg_uuid in chapter_audio_segment_uuids:
        if seg_uuid not in uuid_to_segment:
            logger.warning(f"章节 {chapter.id} 引用的音频片段 {seg_uuid} 不存在")
            continue
        segment = uuid_to_segment[seg_uuid]
        seg_entries.append({
            "uuid": seg_uuid,
            "index": uuid_to_index[seg_uuid],
            "duration": float(segment.duration),
        })

    if not seg_entries:
        return []

    # 按时长阈值先算「仅 duration」的场景数（与旧逻辑一致）
    duration_based = 0
    for e in seg_entries:
        d = e["duration"]
        if d <= split_threshold:
            duration_based += 1
        else:
            duration_based += math.ceil(d / split_threshold)

    visual_cues = _visual_enhancement_cues(chapter)
    total_dur = sum(e["duration"] for e in seg_entries)
    target_n = _target_scene_count_for_chapter(
        total_dur,
        duration_based,
        len(visual_cues),
        min_scene_sec=min_scene_sec,
    )

    # 将 target_n 按各 segment 时长比例分配槽位
    weights = [e["duration"] for e in seg_entries]
    slots_per_seg = [0] * len(seg_entries)
    remaining = target_n
    if target_n < len(seg_entries):
        # 少槽：按旧 per-segment duration 逻辑
        scene_structure: List[Dict[str, Any]] = []
        scene_number = 1
        cue_i = 0
        for e in seg_entries:
            d = e["duration"]
            if d <= split_threshold:
                n_local = 1
            else:
                n_local = math.ceil(d / split_threshold)
            parts = _split_duration_evenly(d, n_local)
            for part in parts:
                cue = visual_cues[cue_i] if cue_i < len(visual_cues) else None
                row = {
                    "audio_segment_index": e["index"],
                    "audio_segment_uuid": e["uuid"],
                    "duration": part,
                    "scene_number": scene_number,
                }
                if cue:
                    row["enhancement_cue"] = cue
                    cue_i += 1
                scene_structure.append(row)
                scene_number += 1
        return scene_structure

    for i in range(len(seg_entries)):
        slots_per_seg[i] = 1
        remaining -= 1
    while remaining > 0:
        best_i = max(
            range(len(seg_entries)),
            key=lambda i: weights[i] / slots_per_seg[i],
        )
        slots_per_seg[best_i] += 1
        remaining -= 1

    scene_structure = []
    scene_number = 1
    cue_i = 0
    for e, n_local in zip(seg_entries, slots_per_seg):
        parts = _split_duration_evenly(e["duration"], n_local)
        for part in parts:
            cue = visual_cues[cue_i] if cue_i < len(visual_cues) else None
            row = {
                "audio_segment_index": e["index"],
                "audio_segment_uuid": e["uuid"],
                "duration": part,
                "scene_number": scene_number,
            }
            if cue:
                row["enhancement_cue"] = cue
                cue_i += 1
            scene_structure.append(row)
            scene_number += 1
            logger.info(
                "[时长诊断] 场景结构: segment_index=%s duration=%.3f cue=%s",
                e["index"],
                part,
                (cue or {}).get("description", "")[:60] if cue else "",
            )

    logger.info(
        "章节 %s 场景结构: total=%.1fs duration_based=%s cues=%s target=%s → %s 镜",
        chapter.id,
        total_dur,
        duration_based,
        len(visual_cues),
        target_n,
        len(scene_structure),
    )
    return scene_structure


def _expected_audio_driven_scene_count(
    story_outline: StoryOutline,
    audio_transcription: AudioTranscription,
    user_option: Optional[UserOption],
) -> int:
    """音频驱动下由结构唯一确定的场景槽位总数（与并行生成后全局 scene_number 1..N 一致）。"""
    chapters = (story_outline.structure and story_outline.structure.chapters) or []
    total = 0
    for ch in chapters:
        total += len(_compute_scene_structure_for_chapter(ch, audio_transcription, user_option))
    return total


def _dedupe_scenes_by_scene_number_first_wins(scenes: List[Any]) -> Dict[int, str]:
    """同 thread 下多行相同 scene_number 时保留 created_at 最早的一条（用于幂等与去重）。"""
    by_sn: Dict[int, str] = {}
    for s in sorted(scenes, key=lambda x: (x.scene_number, x.created_at)):
        if s.scene_number not in by_sn:
            by_sn[s.scene_number] = s.uuid
    return by_sn


def _merge_scene_additional_data(scene: StoryboardScene, patch: Dict[str, Any]) -> None:
    """浅合并 patch 到 scene.additional_data。"""
    base = dict(scene.additional_data) if isinstance(scene.additional_data, dict) else {}
    base.update(patch)
    scene.additional_data = base


def _attach_enhancement_cue_to_scene(scene: StoryboardScene, cue: Optional[Dict[str, Any]]) -> None:
    """把结构槽位上的视觉线索写入 scene.additional_data，供下游细节/对账使用。"""
    from .visual_field_contract import patch_from_enhancement_cue

    patch = patch_from_enhancement_cue(cue if isinstance(cue, dict) else None)
    if not patch:
        return
    _merge_scene_additional_data(scene, patch)


def _attach_chapter_cues_to_scenes_by_order(
    scenes: List[StoryboardScene],
    chapter: StoryChapter,
) -> None:
    """video-driven 等无预计算结构时：按顺序把 chapter 的 broll cues 绑到场景上。"""
    cues = _visual_enhancement_cues(chapter)
    if not cues:
        return
    for i, scene in enumerate(scenes):
        if i >= len(cues):
            break
        ad = scene.additional_data if isinstance(scene.additional_data, dict) else {}
        if ad.get("enhancement_cue"):
            continue
        _attach_enhancement_cue_to_scene(scene, cues[i])


def get_scene_enhancement_cue(scene: Any) -> Optional[Dict[str, Any]]:
    """从 scene.additional_data 读取 enhancement_cue（dict 或 None）。"""
    ad = getattr(scene, "additional_data", None)
    if not isinstance(ad, dict):
        return None
    cue = ad.get("enhancement_cue")
    if not isinstance(cue, dict):
        return None
    desc = (cue.get("description") or "").strip()
    if not desc:
        return None
    return {
        "type": cue.get("type") or "broll",
        "description": desc,
        "timestamp_hint": cue.get("timestamp_hint"),
    }


def _storyboard_scene_from_structure_slot(
    expected_structure: Dict[str, Any],
    chapter: StoryChapter,
    audio_transcription: AudioTranscription,
    allow_lipsync: bool = True,
) -> StoryboardScene:
    """LLM 未返回该槽位时，严格按 scene_structure 生成占位场景（时长/片段与结构一致）；无 LLM 时 generation_mode 为 normal。"""
    expected_uuid = expected_structure["audio_segment_uuid"]
    sn = int(expected_structure["scene_number"])
    dur = round(float(expected_structure["duration"]), 3)
    uuid_to_segment = {s.uuid: s for s in audio_transcription.segments}
    seg = uuid_to_segment.get(expected_uuid)
    seg_text = (seg.text or "").strip() if seg else ""
    cue = expected_structure.get("enhancement_cue") or {}
    cue_desc = (cue.get("description") or "").strip() if isinstance(cue, dict) else ""
    if cue_desc:
        desc = cue_desc
        title = f"场景{sn}"
    elif seg_text:
        desc = seg_text
        title = f"场景{sn}"
    else:
        desc = f"本场景由程序按音频结构补全（场景{sn}），与章节「{chapter.title}」叙事一致。"
        title = f"场景{sn}（结构补全）"
    scene = StoryboardScene(
        scene_number=sn,
        title=title,
        description=desc,
        duration=dur,
        camera_angle="中景",
        character_action="延续本章情节与情绪",
        visual_style="与故事风格一致",
        transition_style="自然切",
        character_ids=[],
        audio_segment_ids=[expected_uuid],
        chapter_id=chapter.id,
        generation_mode=None,
    )
    _attach_enhancement_cue_to_scene(scene, cue if isinstance(cue, dict) else None)
    _finalize_scene_generation_mode(scene, allow_lipsync)
    return scene


def _normalize_video_driven_scene_durations(
    scenes: List[StoryboardScene],
    chapter_duration: float,
    allowed_durations: Optional[List[int]] = None,
    content_category: Optional[str] = None,
) -> List[StoryboardScene]:
    """Video-driven 场景时长：优先保留 LLM；仅当 sum≠章时长 / ≤0 / 过碎时程序纠正。

    纠正时尽量按 LLM 相对权重缩放。不发明场景（Short Drama 不 densify）。
    content_category 可保留用于日志；max_scenes 截断仍允许。
    """
    if not scenes:
        return scenes

    chapter_dur = float(chapter_duration or 0)
    if chapter_dur <= 0:
        logger.warning(
            "⚠️ video-driven 章节时长=%.3f，跳过场景时长重分配（保持 LLM 原值）",
            chapter_dur,
        )
        return scenes

    from app.agent_config.duration import preferred_planning_unit, scene_count_unit

    allowed = sorted({int(d) for d in (allowed_durations or []) if int(d) > 0}) or [5]
    unit = preferred_planning_unit(allowed)
    # 上限按「最短可规划秒」算场数，避免 8s 章被 count_unit=5 压成 max=1、截掉 LLM 按 cue 拆的多场。
    # 程序只截荒谬过量，不替 LLM 发明场景。
    min_slot = min(allowed)
    max_scenes = max(1, int(chapter_dur // min_slot)) if chapter_dur >= min_slot else 1
    if content_category != ContentCategory.PRODUCT_LAUNCH.value:
        # 允许章内多场 4–5s（Default / Short Drama / Lip-Sync MV）
        max_scenes = max(max_scenes, max(1, int(chapter_dur // max(4, min_slot))))
    if len(scenes) > max_scenes:
        logger.warning(
            "⚠️ video-driven 场景数 %s 超过章节 %.1fs / min_slot=%ss 上限 %s，截断（保留 LLM 前序场）",
            len(scenes),
            chapter_dur,
            min_slot,
            max_scenes,
        )
        scenes = scenes[:max_scenes]

    durs = [float(s.duration or 0) for s in scenes]
    sum_d = sum(durs)
    # API 可生成秒数下限（Seedance 常为 4）。sum=章时长但含 2/2.5/3 时不能原样保留，
    # 否则生成端 ceil 到 4s → 成片远超规划（OM 从不在 skill 里写 <4）。
    min_allowed = float(min_slot)
    below_api = [d for d in durs if d > 0 and d < min_allowed]
    llm_ok = (
        all(d >= min_allowed for d in durs)
        and abs(sum_d - chapter_dur) <= 0.15
    )
    if llm_ok:
        head = sum(durs[:-1])
        scenes[-1].duration = float(round(chapter_dur - head, 3))
        logger.info(
            "✅ 保留 LLM 场景时长：chapter=%.3fs durations=%s",
            chapter_dur,
            [s.duration for s in scenes],
        )
        return scenes
    if below_api:
        logger.warning(
            "⚠️ video-driven 场景时长低于 api_min=%ss（%s），不保留 LLM 原值，按权重重分配",
            min_allowed,
            below_api,
        )

    # 场数过多时即使按权重缩放也会落到 api_min 以下（9×~3.3s）→ 截到可放下的场数
    max_fit = max(1, int(chapter_dur // min_allowed)) if chapter_dur >= min_allowed else 1
    if len(scenes) > max_fit:
        logger.warning(
            "⚠️ video-driven 场数 %s 无法在 chapter=%.1fs 下满足 api_min=%ss，截断为 %s",
            len(scenes),
            chapter_dur,
            min_allowed,
            max_fit,
        )
        scenes = scenes[:max_fit]
        durs = [float(s.duration or 0) for s in scenes]

    from app.agent_config.duration import allocate_durations_summing_to_target

    weights = [max(0.01, d) for d in durs] if any(d > 0 for d in durs) else [1.0] * len(scenes)
    wsum = sum(weights)
    scaled = [chapter_dur * (w / wsum) for w in weights]
    # 缩放后仍可能有 < api_min → 改用离散 unit 分配（总和=章时长）
    if any(d < min_allowed for d in scaled):
        allocated = allocate_durations_summing_to_target(chapter_dur, len(scenes), unit)
        for scene, dur in zip(scenes, allocated):
            scene.duration = float(round(dur, 3))
    else:
        for i, scene in enumerate(scenes[:-1]):
            scene.duration = float(round(scaled[i], 3))
        scenes[-1].duration = float(
            round(chapter_dur - sum(s.duration for s in scenes[:-1]), 3)
        )
    if scenes[-1].duration < 0:
        scenes[-1].duration = 0.0

    logger.info(
        "📐 video-driven 场景时长纠正（保相对权重 / api_min）：chapter=%.3fs unit=%ss n=%s durations=%s sum=%.3f category=%s",
        chapter_dur,
        unit,
        len(scenes),
        [s.duration for s in scenes],
        sum(s.duration for s in scenes),
        content_category,
    )
    return scenes


def _validate_and_convert_audio_driven_scenes(
    scenes: List[StoryboardScene],
    chapter: StoryChapter,
    audio_transcription: AudioTranscription,
    scene_structure: List[Dict[str, Any]],
    allow_lipsync: bool = True,
) -> List[StoryboardScene]:
    """校验并转换 audio-driven 模式的场景结果
    
    Args:
        scenes: LLM 生成的场景列表（audio_segment_ids 为 index 的字符串形式，如 "0", "1"）
        chapter: 章节信息
        audio_transcription: 音频转录数据
        scene_structure: 预先计算的场景结构
    
    Returns:
        与 scene_structure 等长的场景列表：LLM 偏少则补全槽位；偏多则截断丢弃多余项；
        audio_segment_ids 从 index 转为 UUID，且与结构槽位对齐。
    """
    # 建立 index 到 UUID 的映射（支持 int 和 str 类型的 index）
    index_to_uuid = {i: seg.uuid for i, seg in enumerate(audio_transcription.segments)}
    str_index_to_uuid = {str(i): seg.uuid for i, seg in enumerate(audio_transcription.segments)}
    
    # 校验场景数量
    if len(scenes) != len(scene_structure):
        logger.warning(
            f"⚠️ 场景数量不匹配！预期 {len(scene_structure)} 个场景，LLM 生成了 {len(scenes)} 个场景"
        )
    
    validated_scenes = []
    for i, scene in enumerate(scenes):
        if i >= len(scene_structure):
            logger.warning(f"⚠️ 场景 {i+1} 超出预先计算的结构，跳过")
            continue
        
        expected_structure = scene_structure[i]
        expected_uuid = expected_structure['audio_segment_uuid']
        
        # 转换 audio_segment_ids：从 index（字符串形式如 "0", "1"）转换为 UUID
        if scene.audio_segment_ids:
            converted_uuids = []
            for audio_id in scene.audio_segment_ids:
                # audio_id 可能是 "0", "1" 这样的字符串（Pydantic 自动转换的）
                if audio_id in str_index_to_uuid:
                    converted_uuids.append(str_index_to_uuid[audio_id])
                else:
                    logger.warning(f"⚠️ 场景 {scene.scene_number} 的音频片段 index '{audio_id}' 无效")
            
            scene.audio_segment_ids = converted_uuids
            
            # 校验 audio_segment_ids 是否匹配预期
            if len(scene.audio_segment_ids) != 1:
                logger.warning(
                    f"⚠️ 场景 {scene.scene_number}: 预期1个音频片段，实际有 {len(scene.audio_segment_ids)} 个，使用预期值"
                )
                scene.audio_segment_ids = [expected_uuid]
            elif scene.audio_segment_ids[0] != expected_uuid:
                logger.warning(
                    f"⚠️ 场景 {scene.scene_number}: 音频片段UUID不匹配，使用预期值 {expected_uuid}"
                )
                scene.audio_segment_ids = [expected_uuid]
        else:
            # 如果 LLM 没有返回 audio_segment_ids，使用预期的
            logger.warning(
                f"⚠️ 场景 {scene.scene_number}: 缺少 audio_segment_ids，使用预期值"
            )
            scene.audio_segment_ids = [expected_uuid]
        
        # 校验时长：统一用 expected_duration 的 ceil 写入（避免 int 截断导致「镜头 4s、segment 4.7s」）
        expected_duration = expected_structure['duration']
        if abs(scene.duration - expected_duration) > 0.1:  # 允许0.1秒误差
            logger.warning(
                f"⚠️ 场景 {scene.scene_number}: 时长不匹配（预期 {expected_duration:.1f}s，实际 {scene.duration:.1f}s），使用预期值"
            )
        scene.duration = round(expected_duration, 3)

        _attach_enhancement_cue_to_scene(scene, expected_structure.get("enhancement_cue"))

        _finalize_scene_generation_mode(scene, allow_lipsync)

        scene.scene_number = int(expected_structure["scene_number"])
        validated_scenes.append(scene)

    if len(scenes) > len(scene_structure):
        n_extra = len(scenes) - len(scene_structure)
        logger.warning(
            f"⚠️ LLM 多出 {n_extra} 个场景，已按 scene_structure 截断丢弃（仅保留前 {len(scene_structure)} 个槽位）"
        )

    if len(validated_scenes) < len(scene_structure):
        n_missing = len(scene_structure) - len(validated_scenes)
        logger.warning(
            f"⚠️ LLM 场景少于结构 {n_missing} 个，已按 scene_structure 补全"
        )
        for j in range(len(validated_scenes), len(scene_structure)):
            exp = scene_structure[j]
            validated_scenes.append(
                _storyboard_scene_from_structure_slot(
                    exp, chapter, audio_transcription, allow_lipsync=allow_lipsync
                )
            )

    logger.info(f"✅ 场景校验完成，共 {len(validated_scenes)} 个场景（与结构 {len(scene_structure)} 槽位一致）")
    return validated_scenes


async def _generate_scenes_for_chapter_with_llm(
    story_outline: StoryOutline,
    characters_data: List[CharacterProfile],
    chapter: StoryChapter,
    images: List[ImageUserInput],
    audio_transcription: Optional[AudioTranscription],
    user_option: Optional[UserOption] = None,
    detected_language: Optional[str] = None,
    user_input: str = "",
    content_category: Optional[str] = None,
    cached_prompt_template: Optional[Any] = None,
    cached_llm: Optional[Any] = None,
    allow_lipsync: bool = True,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    chapter_index: int = 0,
    total_chapters: int = 1,
    script_sections_for_chapter: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[SceneGenerationResult, List[BaseMessage]]:
    """使用 LLM 为特定章节生成场景"""
    try:
        logger.info(f"🎬 开始为章节 '{chapter.title}' 生成场景，时长: {chapter.duration}秒")
        
        # 判断是否为 audio-driven 模式
        is_audio_driven = audio_transcription is not None
        
        # 如果是 audio-driven 模式，预先计算场景结构
        scene_structure = None
        if is_audio_driven:
            scene_structure = _compute_scene_structure_for_chapter(chapter, audio_transcription, user_option)
            logger.info(f"📋 预先计算场景结构：共 {len(scene_structure)} 个场景")
            # 空槽位（chapter.audio_segment_ids 为空 / 无对应 segment）直接返回空，避免无谓 LLM 调用
            # 且 LLM 凭空生成的 scene 会绕过下方校验直接落 DB，导致总时长与 audio_transcription 不匹配
            if not scene_structure:
                logger.warning(
                    f"⚠️ 章节 '{chapter.title}' (id={chapter.id}) 在 audio-driven 模式下无对应 audio_segment 槽位，跳过 LLM 调用并返回空 scene 列表"
                )
                return SceneGenerationResult(success=True, scenes=[]), []

        # Deep-agent path (video- and audio-driven)
        if not (thread_id and run_id):
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "缺少 thread_id/run_id，无法走 scene deep-agent",
            )
        from app.services.agent.video.scene_stage import (
            export_chapter_scene_inputs,
            generate_scenes_for_chapter_via_deep_agent,
        )

        _img_urls = [
            (image.url if hasattr(image, "url") else image)
            for image in (images or [])
            if (getattr(image, "url", None) or isinstance(image, str))
        ]
        if is_audio_driven:
            from app.agent_config.duration import (
                api_min_duration,
                get_audio_driven_split_threshold,
                preferred_planning_unit,
            )

            audio_durations = get_audio_driven_duration_values(user_option)
            pref = (
                float(get_audio_driven_split_threshold(user_option))
                if user_option
                else float(preferred_planning_unit(audio_durations))
            )
            input_paths = export_chapter_scene_inputs(
                thread_id=thread_id,
                run_id=run_id,
                story_outline=story_outline,
                chapter=chapter,
                characters=characters_data,
                user_input=user_input,
                content_category=content_category,
                allowed_durations=[float(x) for x in (audio_durations or [])],
                # Prefer OM-ish unit — never advertise API min as the planning target.
                min_video_duration=pref,
                preferred_scene_duration=pref,
                api_min_duration=float(api_min_duration(audio_durations)),
                chapter_index=chapter_index,
                total_chapters=total_chapters,
                reference_image_urls=_img_urls,
                mode="audio_driven",
                audio_transcription=audio_transcription,
                scene_structure=scene_structure,
            )
        else:
            from app.agent_config.duration import planning_duration_hints

            video_durations, pref, api_min = planning_duration_hints(user_option)
            input_paths = export_chapter_scene_inputs(
                thread_id=thread_id,
                run_id=run_id,
                story_outline=story_outline,
                chapter=chapter,
                characters=characters_data,
                user_input=user_input,
                content_category=content_category,
                allowed_durations=[float(x) for x in (video_durations or [])],
                min_video_duration=float(pref),
                preferred_scene_duration=float(pref),
                api_min_duration=float(api_min),
                chapter_index=chapter_index,
                total_chapters=total_chapters,
                reference_image_urls=_img_urls,
                script_sections=script_sections_for_chapter,
            )
        scenes, msgs = await generate_scenes_for_chapter_via_deep_agent(
            thread_id=thread_id,
            run_id=run_id,
            input_paths=input_paths,
            detected_language=detected_language,
        )
        for scene in scenes:
            scene.chapter_id = chapter.id
        if is_audio_driven and scene_structure is not None:
            scenes = _validate_and_convert_audio_driven_scenes(
                scenes,
                chapter,
                audio_transcription,
                scene_structure,
                allow_lipsync=allow_lipsync,
            )
        else:
            # Always normalize generation_mode (LLM often writes "video"/"i2v"; only
            # normal | lipsync | empty_shot are valid). Product Launch still forces lipsync.
            for scene in scenes:
                _finalize_scene_generation_mode(scene, allow_lipsync, content_category)
        return SceneGenerationResult(success=True, scenes=scenes), list(msgs or [])
    except Exception as e:
        logger.error(f"章节 '{chapter.title}' 场景生成失败: {e}")
        raise


async def _generate_scenes_parallel(
    story_outline: StoryOutline,
    characters_data: List[CharacterProfile],
    images: List[ImageUserInput],
    audio_transcription: Optional[AudioTranscription],
    user_option: Optional[UserOption] = None,
    detected_language: Optional[str] = None,
    max_concurrent: Optional[int] = None,
    user_input: str = "",
    content_category: Optional[str] = None,
    allow_lipsync: bool = True,
    thread_id: Optional[str] = None,
    run_id: Optional[str] = None,
    analysis_data: Optional[Any] = None,
) -> Tuple[List[StoryboardScene], List[BaseMessage]]:
    """直接对章节并行生成场景（简化版本，不需要拆分片段）"""
    from ..utils.prompt_utils import get_concurrency_limit, add_random_delay
    
    # 使用配置的并发限制
    if max_concurrent is None:
        max_concurrent = get_concurrency_limit("scene_generation")
    
    logger.info("🎬 开始并行场景生成（直接对章节生成）")
    all_messages = []
    
    # 检查故事结构
    if not story_outline.structure or not story_outline.structure.chapters:
        logger.warning("⚠️ 故事大纲没有章节结构，无法进行场景生成")
        return [], []
    
    logger.info(f"🎬 并行为 {len(story_outline.structure.chapters)} 个章节生成场景...")

    if not (thread_id and run_id):
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "缺少 thread_id/run_id，无法走 scene deep-agent",
        )

    # OM Script layer (video-driven): beat map before parallel scenes
    script_by_chapter: Dict[str, List[Dict[str, Any]]] = {}
    is_audio_driven = audio_transcription is not None
    if (not is_audio_driven) and thread_id and run_id:
        try:
            from app.services.agent.video.script_stage import (
                export_script_inputs,
                generate_script_via_deep_agent,
            )
            from app.services.agent.stage_runtime.workspace import read_artifact_json
            from app.contracts.artifacts.script import ScriptArtifact

            try:
                script_art = ScriptArtifact.model_validate(
                    read_artifact_json(thread_id, run_id, "script.json")
                )
                logger.info("📜 reuse existing script.json sections=%d", len(script_art.sections))
            except Exception:
                spaths = export_script_inputs(
                    thread_id=thread_id,
                    run_id=run_id,
                    story_outline=story_outline,
                    analysis=analysis_data,
                    user_input=user_input or "",
                    characters=characters_data,
                )
                script_art, script_msgs = await generate_script_via_deep_agent(
                    thread_id=thread_id,
                    run_id=run_id,
                    input_paths=spaths,
                    detected_language=detected_language,
                )
                all_messages.extend(list(script_msgs or []))
                logger.info("📜 script stage OK sections=%d", len(script_art.sections))
            for sec in script_art.sections:
                cid = sec.chapter_id or ""
                row = sec.model_dump(mode="json")
                if cid:
                    script_by_chapter.setdefault(cid, []).append(row)
                else:
                    # fallback: put unbound sections on first chapter
                    first_id = story_outline.structure.chapters[0].id
                    script_by_chapter.setdefault(first_id, []).append(row)
        except Exception as e:
            # Video-driven: hard-stop — do not generate unbound scenes (no silent continue).
            logger.error("❌ script stage failed (video-driven hard-stop): %s", e)
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                f"script stage failed (video-driven hard-stop): {e}",
            ) from e
        if not script_by_chapter and not is_audio_driven:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "script stage produced no bindable sections (video-driven hard-stop)",
            )

    # 创建信号量限制并发数
    semaphore = asyncio.Semaphore(max_concurrent)
    all_scenes = []
    chapters = story_outline.structure.chapters
    n_chapters = len(chapters)
    
    async def generate_chapter_scenes_with_semaphore(chapter: StoryChapter, chapter_index: int):
        """带信号量限制的章节场景生成"""
        # 添加随机延迟避免并发请求过于集中
        await add_random_delay()
        
        async with semaphore:
            try:
                ch_id = getattr(chapter, "id", None) or ""
                return await _generate_scenes_for_chapter_with_llm(
                    story_outline, characters_data, chapter, images,
                    audio_transcription, user_option, detected_language, user_input, content_category,
                    allow_lipsync=allow_lipsync,
                    thread_id=thread_id,
                    run_id=run_id,
                    chapter_index=chapter_index,
                    total_chapters=n_chapters,
                    script_sections_for_chapter=script_by_chapter.get(ch_id) or script_by_chapter.get(str(chapter_index)),
                )
            except Exception as e:
                logger.error(f"章节 '{chapter.title}' 生成失败: {e}")
                return None, []
    
    # 并行执行所有章节的场景生成
    tasks = [
        generate_chapter_scenes_with_semaphore(chapter, i)
        for i, chapter in enumerate(chapters)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 协作式取消：若因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
    await raise_if_cancelled()
    
    # 收集结果，保持章节顺序
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            logger.error(f"章节 {i} 生成异常: {result}")
            continue
        
        if result is None:
            continue
            
        result_obj, messages = result
        if result_obj and result_obj.scenes:
            all_scenes.extend(result_obj.scenes)
        if messages:
            all_messages.extend(messages)
    
    # 统一分配场景编号，确保全局范围内连续
    for i, scene in enumerate(all_scenes):
        scene.scene_number = i + 1
    
    logger.info(f"✅ 并行生成完成，共生成 {len(all_scenes)} 个场景")
    return all_scenes, all_messages


async def scene_generation_node(state: VideoAgentState,
                                runtime: Runtime[VideoContextSchema],
                                send_event_func: Callable[..., None]) -> Union[VideoAgentState, Dict[str, Any]]:
    """场景生成节点 - 使用层次化并行生成方法"""
    try:
        # 获取故事大纲和角色信息
        story_outline_uuid = state.get("story_outline_uuid")
        character_uuids = state.get("character_uuids", [])

        if not story_outline_uuid:
            raise BusinessException(
                BusinessExceptionCode.VIDEO_ANALYSIS_UUID_MISSING,
                "缺少故事梗概UUID"
            )

        # 加载数据（使用asyncpg CRUD）
        story_outline = await get_story_outline_from_db(story_outline_uuid)
        user_input_data = state.get("user_input_data")
        images = user_input_data.images if user_input_data else []
        user_option = user_input_data.user_option if user_input_data else None
        
        # 获取角色信息（使用asyncpg CRUD）
        characters_data = await get_characters_from_db(character_uuids)
        
        # 获取音频转录信息（如果有）
        audio_transcription_uuids = state.get("audio_transcription_uuids", [])
        audio_transcription = await get_audio_transcription_from_db(audio_transcription_uuids)
        
        logger.info(f"🎬 开始层次化场景生成，已有角色: {len(characters_data)} 个")

        conversation_id_str = str(state["conversation_id"])
        thread_id_str = str(state["thread_id"])
        existing_scenes_all = await get_scenes_by_conversation(conversation_id_str, thread_id_str)
        chapter_id_set = (
            {c.id for c in story_outline.structure.chapters}
            if story_outline.structure and story_outline.structure.chapters
            else set()
        )
        is_audio_driven = audio_transcription is not None

        # 业务幂等（音频驱动）：槽位数确定，若库中已有完整 1..N 则跳过 LLM 与插入
        if is_audio_driven and chapter_id_set and audio_transcription is not None:
            expected_n = _expected_audio_driven_scene_count(
                story_outline, audio_transcription, user_option
            )
            if expected_n > 0:
                relevant = [s for s in existing_scenes_all if s.chapter_id in chapter_id_set]
                by_sn_existing = _dedupe_scenes_by_scene_number_first_wins(relevant)
                if by_sn_existing and all(i in by_sn_existing for i in range(1, expected_n + 1)):
                    ordered_uuids = [by_sn_existing[i] for i in range(1, expected_n + 1)]
                    logger.info(
                        "scene_generation idempotency (audio-driven): slots=%s already satisfied; "
                        "skip LLM and inserts, reuse %s scene uuids",
                        expected_n,
                        len(ordered_uuids),
                    )
                    all_messages: List[BaseMessage] = []
                    from ....services.agent.utils.prompt_utils import generate_completion_message_stream
                    user_message, completion_message = await generate_completion_message_stream(
                        event_type=MessageType.SCENES_GENERATED,
                        messages=all_messages,
                        send_event_func=send_event_func,
                        conversation_id=state.get("conversation_id"),
                        lang=state.get("detected_language")
                    )
                    if completion_message:
                        all_messages.append(completion_message)
                    await send_event_func(
                        conversation_id=state["conversation_id"],
                        event_type=MessageType.SCENES_GENERATED,
                        message=user_message,
                        extra_data={
                            "scene_uuids": ordered_uuids,
                            "run_id": state.get("run_id"),
                            "thread_id": state.get("thread_id")
                        }
                    )
                    logger.info(f"✅ 层次化场景生成完成（幂等复用），共 {len(ordered_uuids)} 个场景")
                    return {
                        "scene_uuids": ordered_uuids,
                        "messages": all_messages
                    }

        # 使用简化的并行生成方法（直接对章节生成场景）
        logger.info("🚀 使用简化的并行生成方法（直接对章节生成场景）")
        user_input = user_input_data.user_input
        # video_analysis 已写入 user_option.content_category（enum），模板要 str 用 .value
        content_category = state["user_input_data"].user_option.content_category.value
        allow_lipsync = should_enable_lipsync_for_run(
            user_option,
            music_intent=state.get("music_intent"),
            music_workflow_mode=state.get("music_workflow_mode"),
            audio_transcription=audio_transcription,
        )
        if not allow_lipsync:
            logger.info(
                "🎭 scene_generation: allow_lipsync=False（BGM/纯音乐/无人声），场景不得标 lipsync"
            )
        analysis_for_script = None
        analysis_uuid = state.get("analysis_uuid")
        if analysis_uuid:
            try:
                from ....services.agent.utils.database_utils import get_video_analysis_from_db

                analysis_for_script = await get_video_analysis_from_db(analysis_uuid)
            except Exception as e:
                logger.warning("script stage: load analysis_uuid=%s failed: %s", analysis_uuid, e)

        all_scenes, all_messages = await _generate_scenes_parallel(
            story_outline, characters_data, images, audio_transcription, user_option,
            state.get("detected_language"), user_input=user_input, content_category=content_category,
            allow_lipsync=allow_lipsync,
            thread_id=str(state.get("thread_id") or ""),
            run_id=str(state.get("run_id") or ""),
            analysis_data=analysis_for_script,
        )

        # 保存前再读一次，减少与并发执行的插入竞态窗口
        existing_before_insert = await get_scenes_by_conversation(conversation_id_str, thread_id_str)
        existing_uuid_by_sn: Dict[int, str] = {}
        if is_audio_driven:
            existing_uuid_by_sn = _dedupe_scenes_by_scene_number_first_wins(existing_before_insert)

        # 保存所有场景到数据库（使用asyncpg CRUD）
        all_scene_uuids = []
        for scene in all_scenes:
            try:
                if is_audio_driven and scene.scene_number in existing_uuid_by_sn:
                    scene_uuid = existing_uuid_by_sn[scene.scene_number]
                    all_scene_uuids.append(scene_uuid)
                    logger.info(
                        "scene_generation idempotency: skip create_scene scene_number=%s uuid=%s",
                        scene.scene_number,
                        scene_uuid,
                    )
                    continue
                scene_uuid = await create_scene(
                    scene_data={
                        "user_id": state["user_id"],
                        "conversation_id": str(state["conversation_id"]),
                        "thread_id": str(state["thread_id"]),
                        "run_id": state["run_id"],
                        "scene_number": scene.scene_number,
                        "title": scene.title,
                        "description": scene.description,
                        "duration": scene.duration,
                        "camera_angle": scene.camera_angle,
                        "character_action": scene.character_action,
                        "visual_style": scene.visual_style,
                        "transition_style": scene.transition_style,
                        "is_bridge": scene.is_bridge,
                        "character_ids": scene.character_ids,
                        "audio_segment_ids": scene.audio_segment_ids,
                        "chapter_id": scene.chapter_id,
                        "generation_mode": scene.generation_mode,
                        "additional_data": scene.additional_data,
                    }
                )
                all_scene_uuids.append(scene_uuid)
                logger.info(f"✅ 场景 {scene.title} 保存成功: {scene_uuid}")
            except Exception as e:
                logger.error(f"❌ 场景 {scene.title} 保存失败: {str(e)}")

        # 发送场景生成完成事件
        from ....services.agent.utils.prompt_utils import generate_completion_message_stream
        user_message, completion_message = await generate_completion_message_stream(
            event_type=MessageType.SCENES_GENERATED,
            messages=all_messages,
            send_event_func=send_event_func,
            conversation_id=state.get("conversation_id"),
            lang=state.get("detected_language")
        )
        if completion_message:
            all_messages.append(completion_message)
        
        await send_event_func(
            conversation_id=state["conversation_id"],
            event_type=MessageType.SCENES_GENERATED,
            message=user_message,
            extra_data={
                "scene_uuids": all_scene_uuids,
                "run_id": state.get("run_id"),
                "thread_id": state.get("thread_id")
            }
        )

        logger.info(f"✅ 层次化场景生成完成，共生成 {len(all_scene_uuids)} 个场景")
        return {
            "scene_uuids": all_scene_uuids,
            "messages": all_messages
        }

    except BusinessException as e:
        logger.error(f"场景生成失败: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"场景生成失败: {e}")
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"场景生成失败: {str(e)}"
        )
