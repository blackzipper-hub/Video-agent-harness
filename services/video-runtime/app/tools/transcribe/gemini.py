"""
音频转录工具模块
提供高级音频转录功能，包括从 S3 下载和转录
"""

import logging
import tempfile
import base64
import bisect
import subprocess
import json
import os
import asyncio
from pathlib import Path
from typing import Optional, List, Union, Dict, Any, Tuple
from pydantic import BaseModel, Field
import aiofiles

from app.utils.s3_utils import s3_utils
from app.utils import media_service_client as msc
from app.models.video_state import AudioTranscription, AudioSegment
from app.models.user_options import UserOption
from app.models.tool_enums import AudioSegmentGranularity, DefaultValues

logger = logging.getLogger(__name__)


def should_apply_lipsync_constraint(
    user_option: Optional[UserOption],
    *,
    music_intent: Optional[str] = None,
    music_workflow_mode: Optional[str] = None,
) -> bool:
    """是否需要对 audio segment 施加 lipsync duration 约束"""
    from app.models.user_options import should_enable_lipsync_for_run

    return should_enable_lipsync_for_run(
        user_option,
        music_intent=music_intent,
        music_workflow_mode=music_workflow_mode,
    )


def get_lipsync_max_segment_duration(user_option: Optional[UserOption]) -> int:
    """lipsync 模式下 segment 最大时长（秒），与 audio-driven 规划离散秒数上界一致。"""
    from app.agent_config.duration import get_audio_driven_duration_values

    return max(get_audio_driven_duration_values(user_option) or [15])


def _resolve_granularity(
    explicit: Optional[AudioSegmentGranularity],
) -> AudioSegmentGranularity:
    """统一解析切分粒度：显式入参 > DefaultValues 兜底。

    granularity 不挂在 UserOption 上 —— 它是 transcribe **engine 配置**，
    与 `DefaultValues.TRANSCRIPTION_METHOD` 同级；由 caller
    （music_generation_service）按 `TRANSCRIPTION_METHOD_CONFIG[method]["granularity"]`
    解析后显式传入；本函数只负责"入参缺失时兜底"。
    """
    if explicit is not None:
        return AudioSegmentGranularity.from_value(explicit)
    return AudioSegmentGranularity.from_value(DefaultValues.AUDIO_SEGMENT_GRANULARITY)


def parse_time_to_seconds(time_str: str) -> float:
    """
    将时间字符串转换为秒数。transcribe 输出统一为 MM:SS.mmm 格式。

    支持格式：
    - MM:SS.mmm（如 "1:20.500" -> 80.5、"0:12.250" -> 12.25）
    - MM:SS、MM:SS.SS（兼容）
    - 纯秒数（如 "80.5"）
    """
    try:
        time_str = time_str.strip()
        if ":" in time_str:
            parts = time_str.split(":")
            if len(parts) == 2:
                minutes = float(parts[0])
                seconds = float(parts[1])
                return minutes * 60 + seconds
            logger.warning(f"无法解析时间格式: {time_str}")
            return 0.0
        return float(time_str)
    except (ValueError, IndexError) as e:
        logger.error(f"时间格式解析失败: {time_str}, 错误: {e}")
        return 0.0


# ==================== Gemini音频转录结构化输出模型 ====================
class GeminiAudioWord(BaseModel):
    """Gemini音频转录词汇（word-level 时间戳，MM:SS.mmm 格式）"""
    id: int = Field(description="词汇ID（序号）")
    word: str = Field(description="词汇文本")
    start: str = Field(description="开始时间（MM:SS.mmm 格式，如 0:12.250）")
    end: str = Field(description="结束时间（MM:SS.mmm 格式，如 0:12.850）")

class GeminiAudioSegment(BaseModel):
    """Gemini音频转录片段。时间字段均为 MM:SS.mmm 格式（分:秒.毫秒，如 0:12.250、1:20.500）。"""
    id: int = Field(description="片段ID（序号）")
    start: str = Field(description="开始时间（MM:SS.mmm 格式，如 0:12.250）")
    end: str = Field(description="结束时间（MM:SS.mmm 格式，如 1:20.500）")
    text: str = Field(description="转录文本或音乐段落描述")
    duration: str = Field(description="片段时长（MM:SS.mmm 格式，如 0:05.300）")
    emotion: Optional[str] = Field(default=None, description="情感/调性")
    tempo: Optional[str] = Field(default=None, description="速度/动态")
    vocal_presence: Optional[bool] = Field(default=None, description="是否有人声/演唱，用于 lipsync 是否张嘴")
    vocal_gender: Optional[str] = Field(default=None, description="人声性别：'f' 女声，'m' 男声；仅能听辨时填，否则不填")
    section_index: Optional[int] = Field(default=None, description="所属段落在 sections 中的下标，用于与曲式对齐")


class GeminiSection(BaseModel):
    """Gemini 曲式段落（Song Structure），时间均为 MM:SS.mmm 格式"""
    section_type: str = Field(description="段落类型，如 Intro, Verse 1, Chorus 1, Bridge, Outro")
    start_seconds: str = Field(description="开始时间（MM:SS.mmm 格式，如 0:00.000）")
    end_seconds: str = Field(description="结束时间（MM:SS.mmm 格式，如 0:32.500）")
    musical_features: Optional[str] = Field(default=None, description="该段音乐特征描述")
    section_emotion: Optional[str] = Field(default=None, description="该段情绪")
    suggested_visual_intensity: Optional[str] = Field(default=None, description="建议视觉强度")
    suggested_rhythmic_strategy: Optional[str] = Field(default=None, description="建议节奏策略")
    suggested_visual_theme: Optional[str] = Field(default=None, description="建议视觉主题")
    suggested_context: Optional[str] = Field(default=None, description="建议场景/上下文")


class GeminiTranscriptionResult(BaseModel):
    """Gemini音频转录结果（含整曲 Global、段落 sections、切片 segments）。整曲 duration 为 MM:SS.mmm 格式。"""
    task: str = Field(description="任务类型", default="transcribe")
    language: str = Field(description="识别语言")
    duration: str = Field(description="总时长（MM:SS.mmm 格式，如 2:05.120）")
    text: str = Field(description="完整转录文本或音乐整体描述")
    is_instrumental: bool = Field(description="是否为纯音乐（无歌词）")
    # 整曲级 Global（对应 video_audio_transcription 表）
    song_name: Optional[str] = Field(default=None, description="歌曲名称")
    global_bpm: Optional[Union[int, float]] = Field(default=None, description="整曲 BPM")
    genre: Optional[str] = Field(default=None, description="音乐流派")
    global_emotion: Optional[str] = Field(default=None, description="整曲听觉情绪/基调")
    suggested_global_theme: Optional[str] = Field(default=None, description="建议核心设计理念")
    suggested_color_palette: Optional[str] = Field(default=None, description="建议整体色彩倾向")
    segments: List[GeminiAudioSegment] = Field(description="音频片段列表，每段含 emotion/tempo")
    sections: List[GeminiSection] = Field(default_factory=list, description="Song Structure 段落列表，须输出")


# 空文本段并入上一段：时长 < 此阈值（秒）
SHORT_EMPTY_MERGE_THRESHOLD_SEC = 5.0


def prune_sections_without_segment_overlap(
    sections: List[Dict[str, Any]],
    segments: List[AudioSegment],
) -> List[Dict[str, Any]]:
    """删除与任意 segment 无时间重叠的曲式段落（merge 后空窗直接删掉，不保留占位）。"""
    if not sections:
        return sections
    kept: List[Dict[str, Any]] = []
    for sec in sorted(sections, key=lambda s: float(s.get("start_time") or 0)):
        st = float(sec.get("start_time") or 0)
        et = float(sec.get("end_time") or 0)
        if et <= st:
            logger.info(
                "🗑️ 删除无效曲式段落（end<=start）: %s [%.3f-%.3f]",
                sec.get("section_type", "?"),
                st,
                et,
            )
            continue
        has_overlap = any(
            float(seg.start) < et - 1e-9 and float(seg.end) > st + 1e-9
            for seg in segments
        )
        if has_overlap:
            kept.append(sec)
        else:
            logger.info(
                "🗑️ 删除无 segment 重叠的曲式段落: %s [%.3f-%.3f]",
                sec.get("section_type", "?"),
                st,
                et,
            )
    return kept


def merge_short_empty_segments(
    segments: List[AudioSegment],
    duration_threshold: float = SHORT_EMPTY_MERGE_THRESHOLD_SEC,
) -> List[AudioSegment]:
    """
    合并短时空文本片段到前一个片段
    
    在 audio-driven 模式下，Whisper word-level 转录 + LLM 字幕组装会产生很多短时停顿（0.5-1.5s）。
    这些短停顿会被 fill_gaps 识别为独立的空文本片段，导致过多的场景切换。
    
    策略：将短时空文本片段（text为空 且 duration < threshold）合并到前一个片段
    
    为什么合并到前面而不是后面？
    1. 视觉连续性：短停顿通常是歌词间的呼吸/节奏停顿，应延续前一个片段的视觉
    2. 自然过渡：场景以歌词结束后自然淡出，而不是以静音开场
    3. 符合音乐视频节奏：动作/表演在歌词时进行，停顿是余韵/定格
    4. LLM 场景描述更自然：\"唱XX，然后停顿看向远方\" vs \"停顿，然后开始唱XX\"
    
    Args:
        segments: 音频片段列表（已按 start 排序）
        duration_threshold: 时长阈值（秒），小于此值的空文本片段会被合并到前一个
        
    Returns:
        合并后的音频片段列表
        
    Notes:
        - 只合并有前置片段的空文本片段（第一个片段不合并）
        - 保持时间连续性，更新合并后片段的 end 和 duration
    """
    if not segments:
        return segments
    
    merged_segments = []
    sorted_segments = sorted(segments, key=lambda s: s.start)
    
    for i, segment in enumerate(sorted_segments):
        # 判断是否是短时空文本片段
        is_short_empty = (not segment.text.strip()) and (segment.duration < duration_threshold)

        # 如果是短时空文本片段 且 前面有片段，则合并到前一个片段
        if is_short_empty and merged_segments:
            prev_segment = merged_segments[-1]
            # 扩展前一个片段的结束时间
            merged_segment = AudioSegment(
                id=prev_segment.id,
                start=prev_segment.start,
                end=segment.end,  # 扩展到当前片段的结束时间
                text=prev_segment.text,  # 保持前一个片段的文本
                duration=segment.end - prev_segment.start,  # 更新时长
                emotion=prev_segment.emotion,  # 保持前一个片段的情感
                tempo=prev_segment.tempo,  # 保持前一个片段的速度
                vocal_presence=getattr(prev_segment, "vocal_presence", None),
                vocal_gender=getattr(prev_segment, "vocal_gender", None),
            )
            merged_segments[-1] = merged_segment  # 替换前一个片段
            logger.info(f"📝 合并短时空文本片段 {segment.id} ({segment.duration:.2f}s) 到前一个片段 {prev_segment.id}")
        else:
            # 正常添加片段
            merged_segments.append(segment)
    
    # 重新分配 id
    for i, segment in enumerate(merged_segments):
        segment.id = i
    
    return merged_segments


# 任意过短切片并入邻段：时长 < 此阈值（秒）；在 section 切分 + 空段合并之后执行
MICRO_SEGMENT_MIN_DURATION_SEC = 3.0


def merge_micro_duration_segments(
    segments: List[AudioSegment],
    min_duration: float = MICRO_SEGMENT_MIN_DURATION_SEC,
) -> List[AudioSegment]:
    """
    合并时长过短的切片（间奏碎段、模型亚秒废段、section 切分产生的 1ms 碎段等）。

    - 优先并入**上一段**（扩展 end），与 merge_short_empty_segments 一致。
    - 若无上一段则并入**下一段**（扩展 start）。

    可能多轮合并，直到没有亚阈值段。
    """
    if not segments:
        return segments
    segs: List[AudioSegment] = list(segments)
    while True:
        segs.sort(key=lambda s: float(s.start))
        merged: List[AudioSegment] = []
        i = 0
        made_change = False
        while i < len(segs):
            seg = segs[i]
            d = max(0.0, float(seg.end) - float(seg.start))
            if d + 1e-9 >= min_duration:
                merged.append(seg)
                i += 1
                continue
            if merged:
                prev = merged[-1]
                made_change = True
                _text_parts = [p for p in [(prev.text or "").strip(), (seg.text or "").strip()] if p]
                merged[-1] = AudioSegment(
                    id=prev.id,
                    start=float(prev.start),
                    end=float(seg.end),
                    text=" ".join(_text_parts),
                    duration=float(seg.end) - float(prev.start),
                    emotion=prev.emotion,
                    tempo=prev.tempo,
                    vocal_presence=getattr(prev, "vocal_presence", None),
                    vocal_gender=getattr(prev, "vocal_gender", None),
                )
                logger.info(
                    "📝 合并过短切片 %.4fs 入上一段 → [%.3f, %.3f]",
                    d,
                    float(prev.start),
                    float(seg.end),
                )
                i += 1
            elif i + 1 < len(segs):
                made_change = True
                nxt = segs[i + 1]
                _text_parts = [p for p in [(seg.text or "").strip(), (nxt.text or "").strip()] if p]
                segs[i + 1] = AudioSegment(
                    id=0,
                    start=float(seg.start),
                    end=float(nxt.end),
                    text=" ".join(_text_parts),
                    duration=float(nxt.end) - float(seg.start),
                    emotion=nxt.emotion,
                    tempo=nxt.tempo,
                    vocal_presence=getattr(nxt, "vocal_presence", None),
                    vocal_gender=getattr(nxt, "vocal_gender", None),
                )
                logger.info(
                    "📝 合并过短片头 %.4fs 入下一段 → [%.3f, %.3f]",
                    d,
                    float(seg.start),
                    float(nxt.end),
                )
                i += 1
            else:
                merged.append(seg)
                i += 1
        for j, s in enumerate(merged):
            s.id = j
        segs = merged
        if not made_change:
            break
    return segs


def snap_segment_edges_to_bar_grid(
    segments: List[AudioSegment],
    bar_dur_sec: float,
    tolerance_sec: float = 0.20,
    word_edges_sec: Optional[List[float]] = None,
    total_duration: Optional[float] = None,
) -> List[AudioSegment]:
    """**Beat 模式**专用后处理：把段端点吸附到 4/4 小节线 (t_n = bar_dur × n)。

    设计要点：
      - 仅在 |edge − nearest_bar| ≤ tolerance 时吸附；否则不动（避免把 LLM 的合理切点拉乱）
      - **唱词保护**：吸附点若落在某词内部 (`word_edges_sec`)，自动漂移到最近的词边界；
        当 `word_edges_sec` 为空（纯器乐 / 未提供词）则不触发该保护
      - 严格保持相邻段端点连续：seg[i].end == seg[i+1].start
      - 不改 sections；不引入新段也不删段，只修 start/end
      - 首段 start ≥ 0；末段 end ≤ total_duration（若提供）

    与 `merge_micro_duration_segments` 兼容：吸附后若产生 < MICRO 阈值的段，
    可由调用方再跑一次 micro-merge（本函数不主动合并以保证最小副作用）。
    """
    if not segments or bar_dur_sec <= 0:
        return list(segments)

    edges_sorted = sorted(set(float(x) for x in (word_edges_sec or []) if x is not None))

    def _is_inside_word(t: float) -> bool:
        if not edges_sorted:
            return False
        idx = bisect.bisect_right(edges_sorted, t)
        if idx <= 0 or idx >= len(edges_sorted):
            return False
        return (t - edges_sorted[idx - 1] > 1e-3) and (edges_sorted[idx] - t > 1e-3)

    def _nearest_word_edge(t: float) -> Optional[float]:
        if not edges_sorted:
            return None
        idx = bisect.bisect_left(edges_sorted, t)
        candidates = []
        if idx < len(edges_sorted):
            candidates.append(edges_sorted[idx])
        if idx > 0:
            candidates.append(edges_sorted[idx - 1])
        return min(candidates, key=lambda x: abs(x - t)) if candidates else None

    def _snap(t: float) -> float:
        if t < 0:
            return 0.0
        n = round(t / bar_dur_sec)
        snapped = n * bar_dur_sec
        if abs(snapped - t) > tolerance_sec:
            return t
        if _is_inside_word(snapped):
            nearest = _nearest_word_edge(snapped)
            if nearest is not None and abs(nearest - snapped) <= tolerance_sec:
                return nearest
            return t
        return snapped

    sorted_segs = sorted(segments, key=lambda s: float(s.start))
    new_starts: List[float] = []
    for i, seg in enumerate(sorted_segs):
        if i == 0:
            new_starts.append(0.0 if float(seg.start) <= 1e-3 else _snap(float(seg.start)))
        else:
            new_starts.append(_snap(float(seg.start)))

    out: List[AudioSegment] = []
    n = len(sorted_segs)
    for i, seg in enumerate(sorted_segs):
        s_new = new_starts[i]
        if i + 1 < n:
            e_new = new_starts[i + 1]
        else:
            e_new = _snap(float(seg.end))
            if total_duration is not None and e_new > float(total_duration):
                e_new = float(total_duration)
        if e_new <= s_new:
            e_new = float(seg.end)
            s_new = min(s_new, e_new - 1e-3)
        out.append(AudioSegment(
            id=i,
            start=s_new,
            end=e_new,
            text=seg.text,
            duration=e_new - s_new,
            emotion=seg.emotion,
            tempo=seg.tempo,
            vocal_presence=getattr(seg, "vocal_presence", None),
            vocal_gender=getattr(seg, "vocal_gender", None),
        ))
    return out


async def get_audio_duration(audio_url: str) -> Optional[float]:
    """获取音频文件时长"""
    result = await msc.audio_info(audio_url)
    duration = result.get("duration")
    if duration is not None:
        logger.info(f"✅ Media service audio_info duration: {duration}")
        return float(duration)
    return None


def fill_gaps_in_segments(segments: List[AudioSegment], total_duration: float, gap_threshold: float = 0.1) -> List[AudioSegment]:
    """
    填补音频片段之间的缺失时间段
    
    Args:
        segments: 原始音频片段列表（已按 start 排序）
        total_duration: 音频总时长
        gap_threshold: 缺失时间段的阈值（秒），大于此值才填补
        
    Returns:
        填补后的音频片段列表
    """
    if not segments:
        return segments
    
    filled_segments = []
    sorted_segments = sorted(segments, key=lambda s: s.start)
    segment_id_counter = 0
    
    # 检查开头缺失
    if sorted_segments[0].start > gap_threshold:
        gap_segment = AudioSegment(
            id=segment_id_counter,
            start=0.0,
            end=sorted_segments[0].start,
            text="",  # Gap 片段使用空文本
            duration=sorted_segments[0].start,
            emotion=None,  # Gap 片段无情感
            tempo=None  # Gap 片段无速度
        )
        filled_segments.append(gap_segment)
        segment_id_counter += 1
        logger.info(f"📝 填补开头缺失片段: 0.0s - {sorted_segments[0].start:.2f}s")
    
    # 保存原有片段并检查中间缺失
    for i, segment in enumerate(sorted_segments):
        # 创建新片段，更新 id
        filled_segment = AudioSegment(
            id=segment_id_counter,
            start=segment.start,
            end=segment.end,
            text=segment.text,
            duration=segment.duration,
            emotion=segment.emotion,  # 保持原有情感
            tempo=segment.tempo,  # 保持原有速度
            vocal_presence=getattr(segment, "vocal_presence", None),
            vocal_gender=getattr(segment, "vocal_gender", None),
        )
        filled_segments.append(filled_segment)
        segment_id_counter += 1
        
        # 检查与下一个片段之间的 gap
        if i < len(sorted_segments) - 1:
            next_segment = sorted_segments[i + 1]
            gap = next_segment.start - segment.end
            if gap > gap_threshold:
                gap_segment = AudioSegment(
                    id=segment_id_counter,
                    start=segment.end,
                    end=next_segment.start,
                    text="",  # Gap 片段使用空文本
                    duration=gap,
                    emotion=None,  # Gap 片段无情感
                    tempo=None  # Gap 片段无速度
                )
                filled_segments.append(gap_segment)
                segment_id_counter += 1
                logger.info(f"📝 填补中间缺失片段: {segment.end:.2f}s - {next_segment.start:.2f}s")
    
    # 检查结尾缺失
    if sorted_segments:
        last_segment = sorted_segments[-1]
        if last_segment.end < total_duration - gap_threshold:
            gap_segment = AudioSegment(
                id=segment_id_counter,
                start=last_segment.end,
                end=total_duration,
                text="",  # Gap 片段使用空文本
                duration=total_duration - last_segment.end,
                emotion=None,  # Gap 片段无情感
                tempo=None  # Gap 片段无速度
            )
            filled_segments.append(gap_segment)
            logger.info(f"📝 填补结尾缺失片段: {last_segment.end:.2f}s - {total_duration:.2f}s")
    
    return filled_segments


def merge_leading_gap_fill_into_next(
    segments: List[AudioSegment],
    *,
    max_gap_duration: float = SHORT_EMPTY_MERGE_THRESHOLD_SEC,
) -> List[AudioSegment]:
    """fill_gaps 补出的片头空段（0 → 首句 start）并入下一段，避免留下 ~0.48s 废段。

    与曲式边界 Outro 尾巴不同：片头 gap 的 start 为 0，应跟首句歌词同属一段。"""
    if len(segments) < 2:
        return segments
    sorted_segs = sorted(segments, key=lambda s: float(s.start))
    head, nxt = sorted_segs[0], sorted_segs[1]
    head_d = float(head.end) - float(head.start)
    if (
        float(head.start) > 0.05
        or (head.text or "").strip()
        or head_d <= 0
        or head_d >= max_gap_duration
    ):
        return segments
    merged = AudioSegment(
        id=nxt.id,
        start=0.0,
        end=float(nxt.end),
        text=nxt.text,
        duration=float(nxt.end),
        emotion=nxt.emotion,
        tempo=nxt.tempo,
        vocal_presence=getattr(nxt, "vocal_presence", None),
        vocal_gender=getattr(nxt, "vocal_gender", None),
    )
    rest = sorted_segs[2:]
    out = [merged] + rest
    for i, s in enumerate(out):
        s.id = i
    logger.info(
        "📝 片头填补空隙 %.3fs 并入下一段 → [0.000, %.3f]",
        head_d,
        float(nxt.end),
    )
    return out


def postprocess_transcription_segments(
    segments: List[AudioSegment],
    *,
    total_duration: float = 0.0,
    sections_for_split: Optional[List[dict]] = None,
    fill_gaps_enabled: bool = True,
) -> List[AudioSegment]:
    """转录 segments 统一后处理（hybrid / 纯 Gemini 共用）。

    顺序（跨 section 必须先切，再在切后收尾巴）：
      1. fill_gaps — 补时间轴空洞
      1b. merge_leading_gap_fill — 片头 0→首句 的空段并入下一段
      2. split_at_section_boundaries — 跨曲式边界必切
      3. merge_short_empty — 空文本且 < 5s 并入上一段
      4. merge_micro — 任意 < 3s 并入邻段
    """
    if not segments:
        return segments
    out = list(segments)
    if fill_gaps_enabled and total_duration > 0:
        n0 = len(out)
        out = fill_gaps_in_segments(out, total_duration)
        if len(out) != n0:
            logger.info("📝 填补时间段完成: %d -> %d 个片段", n0, len(out))
    n_lead = len(out)
    out = merge_leading_gap_fill_into_next(out)
    if len(out) != n_lead:
        logger.info("📝 片头空隙并入下一段: %d -> %d 个片段", n_lead, len(out))
    if sections_for_split:
        n1 = len(out)
        out = split_segments_at_section_boundaries(out, sections_for_split)
        if len(out) != n1:
            logger.info("✂️ section 边界对齐完成: %d -> %d 个片段", n1, len(out))
    n2 = len(out)
    out = merge_short_empty_segments(
        out,
        duration_threshold=SHORT_EMPTY_MERGE_THRESHOLD_SEC,
    )
    if len(out) != n2:
        logger.info("📝 合并短时空文本片段完成: %d -> %d 个片段", n2, len(out))
    n3 = len(out)
    out = merge_micro_duration_segments(
        out,
        min_duration=MICRO_SEGMENT_MIN_DURATION_SEC,
    )
    if len(out) != n3:
        logger.info(
            "📝 合并过短切片完成: %d -> %d 个片段（阈值=%ss）",
            n3,
            len(out),
            MICRO_SEGMENT_MIN_DURATION_SEC,
        )
    return out


def postprocess_transcription(
    segments: List[AudioSegment],
    sections: Optional[List[Dict[str, Any]]] = None,
    *,
    total_duration: float = 0.0,
    fill_gaps_enabled: bool = True,
) -> Tuple[List[AudioSegment], List[Dict[str, Any]]]:
    """segments 后处理 + 按最终 segment 时间轴修剪曲式段落（无重叠则删除）。"""
    secs = list(sections or [])
    split_input = (
        [{"start_time": float(s["start_time"]), "end_time": float(s["end_time"])} for s in secs]
        if secs
        else None
    )
    out_segs = postprocess_transcription_segments(
        segments,
        total_duration=total_duration,
        sections_for_split=split_input,
        fill_gaps_enabled=fill_gaps_enabled,
    )
    if secs:
        n_sec = len(secs)
        secs = prune_sections_without_segment_overlap(secs, out_segs)
        if len(secs) != n_sec:
            logger.info("🎵 曲式修剪完成: %d -> %d 个段落", n_sec, len(secs))
        _sync_section_end_times_to_segments(secs, out_segs, total_duration)
    return out_segs, secs


def _sync_section_end_times_to_segments(
    sections_list: List[Dict[str, Any]],
    audio_segments: List[AudioSegment],
    actual_duration: float,
) -> None:
    """Gemini 原始曲式 end_time 可能短于 fill_gaps/切分后的时间轴；将「按 start_time 排序的最后一节」
    end_time 至少拉到 max(actual_duration, max(seg.end))，与落库的 video_audio_segment 一致。"""
    if not sections_list:
        return
    timeline_end = float(actual_duration)
    if audio_segments:
        timeline_end = max(timeline_end, max(float(s.end) for s in audio_segments))
    ordered = sorted(
        sections_list,
        key=lambda s: float(s.get("start_time") or 0),
    )
    last_sec = ordered[-1]
    old_et = float(last_sec.get("end_time") or 0)
    new_et = max(old_et, timeline_end)
    if new_et > old_et:
        last_sec["end_time"] = new_et
        logger.info(
            "🎵 曲式最后一节 end_time 已与最终切片对齐: %.3fs -> %.3fs",
            old_et,
            new_et,
        )


def fallback_full_track_sections(
    actual_duration: float,
    audio_segments: List[AudioSegment],
    *,
    global_emotion: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Gemini 未返回曲式时：单段覆盖整轨，供 additional_data / video_audio_section 使用。"""
    end_bound = float(actual_duration)
    if audio_segments:
        end_bound = max(end_bound, max(float(s.end) for s in audio_segments))
    return [
        {
            "section_type": "整曲",
            "start_time": 0.0,
            "end_time": end_bound,
            "musical_features": None,
            "section_emotion": global_emotion,
            "suggested_visual_intensity": None,
            "suggested_rhythmic_strategy": None,
            "suggested_visual_theme": None,
            "suggested_context": "模型未返回曲式段落，系统默认整轨一段。",
        }
    ]


def split_segments_at_section_boundaries(
    segments: List[AudioSegment],
    sections: List[dict],
) -> List[AudioSegment]:
    """
    将跨越 section 边界的 segment 拆分，确保每个 segment 只属于一个 section。
    与 fill_gaps / merge_short_empty_segments 同级的后处理步骤。

    当 segment [start, end] 跨越某个 section 边界点时，在该边界处切分为两个子 segment，
    歌词(text)归第一段，后续子段 text 为空，emotion/tempo/vocal_presence 继承原 segment。

    Args:
        segments: 音频片段列表（已按 start 排序）
        sections: 段落列表，每个 dict 含 start_time / end_time（秒）

    Returns:
        拆分后的音频片段列表（按 start 排序，id 重新编号）
    """
    if not sections or not segments:
        return segments

    boundaries = sorted(set(
        [float(s["start_time"]) for s in sections] + [float(s["end_time"]) for s in sections]
    ))

    result: List[AudioSegment] = []
    for seg in segments:
        split_points = [b for b in boundaries if seg.start + 0.05 < b < seg.end - 0.05]

        if not split_points:
            result.append(seg)
            continue

        all_points = [seg.start] + split_points + [seg.end]
        for i in range(len(all_points) - 1):
            sub_start = round(all_points[i], 3)
            sub_end = round(all_points[i + 1], 3)
            result.append(AudioSegment(
                id=0,
                start=sub_start,
                end=sub_end,
                text=seg.text if i == 0 else "",
                duration=round(sub_end - sub_start, 3),
                emotion=seg.emotion,
                tempo=seg.tempo,
                vocal_presence=getattr(seg, "vocal_presence", None),
            ))
            if i == 0 and seg.text:
                logger.info(
                    f"✂️ segment 跨 section 边界拆分: [{seg.start:.2f}-{seg.end:.2f}] "
                    f"→ [{sub_start:.2f}-{sub_end:.2f}] (text) + 后续 {len(split_points)} 段"
                )

    for i, s in enumerate(result):
        s.id = i
    return result


async def transcribe_audio_with_gemini(
    audio_url: str,
    user_option: Optional[UserOption] = None,
    fill_gaps: bool = True,
    user_input: Optional[str] = None,
    filename: Optional[str] = None,
    generated_lyrics: Optional[str] = None,
    suno_alignment_context: Optional[str] = None,
    granularity: Optional[AudioSegmentGranularity] = None,
    music_intent: Optional[str] = None,
    music_workflow_mode: Optional[str] = None,
) -> Optional[AudioTranscription]:
    """
    使用 Gemini 转录音频文件
    
    Args:
        audio_url: 音频文件的 CDN URL
        user_option: 用户选项配置（暂时保留参数，但不再用于合并片段）
        fill_gaps: 是否填补缺失的时间段（默认 True）
        user_input: 用户输入的内容（可能包含歌词），用于修正识别结果
        filename: 原始音频文件名
        generated_lyrics: AI生成音乐时的歌词（最高准确度，如果是Suno生成的歌曲）
        
    Returns:
        Optional[AudioTranscription]: 转录结果，失败返回 None
        
    Notes:
        - 使用 Gemini 2.5 Flash 模型进行音频转录
        - 支持多种音频格式
        - 返回格式与 Whisper 转录保持一致
        - 歌词参考准确度: generated_lyrics > user_input > 自动识别
    """
    try:
        logger.info(f"🎵 开始使用 Gemini 转录音频文件: {audio_url}")
        
        # 从URL获取文件扩展名
        ext = Path(audio_url).suffix
        if not ext:
            ext = '.mp3'  # 默认音频扩展名
        
        # 确定MIME类型
        mime_type_map = {
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.m4a': 'audio/mp4',
            '.aac': 'audio/aac',
            '.ogg': 'audio/ogg',
            '.flac': 'audio/flac'
        }
        audio_mime_type = mime_type_map.get(ext.lower(), 'audio/mpeg')
        
        # Use a directory-owned path rather than an open NamedTemporaryFile.
        # Windows locks the latter and prevents the storage adapter from
        # replacing it during download.
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_file_path = os.path.join(temp_dir, f"audio{ext}")
            # 从S3下载音频文件到临时文件
            success = await s3_utils.download_file(audio_url, temp_file_path)
            if not success:
                logger.error(f"无法下载音频文件: {audio_url}")
                return None
            
            # 异步读取音频文件
            async with aiofiles.open(temp_file_path, "rb") as audio_file:
                audio_bytes = await audio_file.read()

            # 获取实际音频时长用于验证
            logger.info("🎵 获取音频实际时长...")
            actual_duration = await get_audio_duration(audio_url)
            if actual_duration is None:
                logger.error("🎵 无法获取音频实际时长")
                return None

            logger.info(f"🎵 音频实际时长: {actual_duration:.2f}s")

            # Gemini 请求体 inline 约 20MB 限制，base64 后约 4/3，故原始 >15MB 时改用 file_uri
            _AUDIO_INLINE_SIZE_LIMIT = 15 * 1024 * 1024  # 15MB
            if len(audio_bytes) > _AUDIO_INLINE_SIZE_LIMIT:
                from app.utils.google_file_upload import upload_audio_to_google
                file_uri, google_mime = await upload_audio_to_google(temp_file_path, max_wait_time=300)
                if file_uri:
                    # ⚠️ 使用 file_uri 时，mime_type 必须用 Google Files API 识别的实际类型
                    # （如 audio/x-wav），而非我们自己映射的 audio/wav，否则 Gemini 返回 400
                    effective_mime = google_mime or audio_mime_type
                    audio_content = {"file_uri": file_uri, "mime_type": effective_mime}
                    logger.info(f"🎵 大音频使用 file_uri 发送（{len(audio_bytes) / (1024*1024):.2f} MB, mime={effective_mime}）")
                else:
                    logger.warning("🎵 上传 Google 失败，降级为 base64（可能超 20MB 被 Gemini 拒绝）")
                    encoded_audio = base64.b64encode(audio_bytes).decode("utf-8")
                    audio_content = {"data": encoded_audio, "mime_type": audio_mime_type}
            else:
                encoded_audio = base64.b64encode(audio_bytes).decode("utf-8")
                audio_content = {"data": encoded_audio, "mime_type": audio_mime_type}

            from google import genai
            from google.genai import types
            from app.models.tool_enums import ToolProvider, ToolType
            from app.services.account.account_router import get_account_router

            _apply_lipsync = should_apply_lipsync_constraint(
                user_option,
                music_intent=music_intent,
                music_workflow_mode=music_workflow_mode,
            )
            _granularity = _resolve_granularity(granularity)

            facts = {
                "audio_duration_sec": round(float(actual_duration), 2),
                "user_input": (user_input or "").strip(),
                "generated_lyrics": (generated_lyrics or "").strip(),
                "suno_alignment_context": (suno_alignment_context or "").strip(),
                "apply_lipsync_constraint": bool(_apply_lipsync),
                "lipsync_max_segment_duration": (
                    get_lipsync_max_segment_duration(user_option) if _apply_lipsync else None
                ),
                "granularity": _granularity.value,
            }
            prompt = (
                "Transcribe and analyze the attached audio. Return JSON matching the "
                "provided schema exactly. Use MM:SS.mmm timestamps, keep every segment "
                "within the measured audio duration, identify song sections, vocals, "
                "tempo, emotion, and word timestamps when audible. Runtime facts:\n"
                + json.dumps(facts, ensure_ascii=False)
            )
            if "file_uri" in audio_content:
                audio_part = types.Part.from_uri(
                    file_uri=audio_content["file_uri"],
                    mime_type=audio_content["mime_type"],
                )
            else:
                audio_part = types.Part.from_bytes(
                    data=audio_bytes,
                    mime_type=audio_content["mime_type"],
                )

            async def _transcribe_request(api_key: str):
                client = genai.Client(
                    api_key=api_key,
                    http_options=types.HttpOptions(timeout=300_000),
                )
                return await client.aio.models.generate_content(
                    model=ToolType.GEMINI_2_5_FLASH.value,
                    contents=types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=prompt), audio_part],
                    ),
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=GeminiTranscriptionResult,
                    ),
                )
            
            # 重试逻辑：最多重试3次，收集所有结果后选择最佳的
            max_retries = 3
            retry_count = 0
            gemini_result = None
            all_results = []  # 保存所有尝试的结果
            
            while retry_count < max_retries:
                logger.info(f"🎵 调用 Gemini 模型进行音频转录... (尝试 {retry_count + 1}/{max_retries})")
                router = await get_account_router()
                response = await router.route_tool_request(
                    provider=ToolProvider.GOOGLE,
                    tool_type=ToolType.GEMINI_2_5_FLASH,
                    request_func=_transcribe_request,
                )
                raw_result = getattr(response, "parsed", None)
                if raw_result is None and getattr(response, "text", None):
                    raw_result = json.loads(response.text)
                if raw_result is None:
                    logger.error(f"🎵 Gemini 转录失败：无法解析结果 (尝试 {retry_count + 1})")
                    retry_count += 1
                    continue
                temp_result = (
                    raw_result
                    if isinstance(raw_result, GeminiTranscriptionResult)
                    else GeminiTranscriptionResult.model_validate(raw_result)
                )
                
                # 解析 Gemini 返回的时长（MM:SS.mmm 转为秒数）
                gemini_duration_seconds = parse_time_to_seconds(temp_result.duration)
                
                # 验证总时长
                duration_diff = abs(gemini_duration_seconds - actual_duration)
                
                # 验证所有片段的时间戳是否超过实际音频时长（硬性标准）
                invalid_segments_actual = []
                for i, segment in enumerate(temp_result.segments):
                    segment_start = parse_time_to_seconds(segment.start)
                    segment_end = parse_time_to_seconds(segment.end)
                    if segment_start > actual_duration or segment_end > actual_duration:
                        invalid_segments_actual.append(f"片段{i+1}({segment.start}-{segment.end})")
                
                # 验证所有片段的时间戳是否超过Gemini自己给出的总时长
                invalid_segments_gemini = []
                for i, segment in enumerate(temp_result.segments):
                    segment_start = parse_time_to_seconds(segment.start)
                    segment_end = parse_time_to_seconds(segment.end)
                    if segment_start > gemini_duration_seconds or segment_end > gemini_duration_seconds:
                        invalid_segments_gemini.append(f"片段{i+1}({segment.start}-{segment.end})")
                
                logger.info(f"🎵 Gemini 转录完成 (尝试 {retry_count + 1}): 语言={temp_result.language}, 时长={temp_result.duration}({gemini_duration_seconds:.2f}s), 片段数={len(temp_result.segments)}")
                logger.info(f"🎵 时长对比: Gemini={gemini_duration_seconds:.2f}s, 实际={actual_duration:.2f}s, 误差={duration_diff:.2f}s")
                
                if invalid_segments_actual:
                    logger.warning(f"⚠️ 发现{len(invalid_segments_actual)}个时间戳超出实际音频时长的片段: {', '.join(invalid_segments_actual)}")
                if invalid_segments_gemini:
                    logger.warning(f"⚠️ 发现{len(invalid_segments_gemini)}个时间戳超出Gemini自己给出的时长的片段: {', '.join(invalid_segments_gemini)}")
                
                # 保存结果和相关信息
                result_info = {
                    'result': temp_result,
                    'duration_diff': duration_diff,
                    'invalid_segments_actual': invalid_segments_actual,
                    'invalid_segments_gemini': invalid_segments_gemini,
                    'attempt': retry_count + 1
                }
                all_results.append(result_info)
                
                # 验证通过条件：时长误差<=2秒 且 没有超出Gemini自己给出时长的时间戳
                if duration_diff <= 2.0 and not invalid_segments_gemini:
                    gemini_result = temp_result
                    logger.info(f"✅ 验证通过：时长误差 {duration_diff:.2f}s <= 2.0s，所有时间戳都在Gemini给出的时长范围内")
                    break
                else:
                    if duration_diff > 2.0:
                        logger.warning(f"⚠️ 时长误差过大 {duration_diff:.2f}s > 2.0s")
                    if invalid_segments_gemini:
                        logger.warning(f"⚠️ 有{len(invalid_segments_gemini)}个片段时间戳超出Gemini自己给出的时长")
                    logger.warning("重试...")
                    retry_count += 1
            
            # 如果所有重试都没有满足条件的，选择最佳结果
            if gemini_result is None and all_results:
                logger.warning(f"🎵 {max_retries} 次尝试后都未完全通过验证，开始选择最佳结果")
                
                # 首先过滤掉时间戳超过实际音频时长的结果（硬性标准）
                valid_results = [r for r in all_results if not r['invalid_segments_actual']]
                
                if valid_results:
                    # 在符合硬性标准的结果中，选择duration差异最小的
                    best_result = min(valid_results, key=lambda x: x['duration_diff'])
                    gemini_result = best_result['result']
                    logger.info(f"✅ 选择最佳结果：尝试{best_result['attempt']}，时长误差{best_result['duration_diff']:.2f}s，无时间戳超出实际音频时长")
                else:
                    # 如果所有结果都有时间戳超出实际时长，选择duration差异最小的
                    best_result = min(all_results, key=lambda x: x['duration_diff'])
                    gemini_result = best_result['result']
                    logger.warning(f"⚠️ 所有结果都有时间戳问题，选择时长误差最小的：尝试{best_result['attempt']}，时长误差{best_result['duration_diff']:.2f}s")
            elif gemini_result is None:
                logger.error("🎵 Gemini 转录完全失败：无法获得任何结果")
                return None
        
        # 转换为 AudioSegment 格式，将 MM:SS.mmm 转为秒数
        audio_segments = []
        for i, segment in enumerate(gemini_result.segments):
            start_seconds = parse_time_to_seconds(segment.start)
            end_seconds = parse_time_to_seconds(segment.end)
            duration_seconds = end_seconds - start_seconds
            
            audio_segments.append(AudioSegment(
                id=i,
                start=start_seconds,
                end=end_seconds,
                text=segment.text,
                duration=duration_seconds,
                emotion=segment.emotion,
                tempo=segment.tempo,
                vocal_presence=getattr(segment, "vocal_presence", None),
                vocal_gender=getattr(segment, "vocal_gender", None) if getattr(segment, "vocal_gender", None) in ("f", "m") else None,
            ))
        
        # 暂时不处理words数据，但保留处理逻辑以便将来恢复
        words_data = []
        # 使用getattr方式获取words，如果没有则为None
        gemini_words = getattr(gemini_result, 'words', None)
        if gemini_words:
            from app.models.video_state import AudioWord
            for word in gemini_words:
                words_data.append(AudioWord(
                    id=word.id,
                    word=word.word,
                    start=parse_time_to_seconds(word.start),
                    end=parse_time_to_seconds(word.end)
                ))
            logger.info(f"🎵 Gemini提取了 {len(words_data)} 个 words")
        else:
            logger.info("🎵 Gemini 未返回 words 数据（已暂时禁用）")
        
        sections_list: List[Dict[str, Any]] = []
        if gemini_result.sections and len(gemini_result.sections) > 0:
            sections_list = [
                {
                    "section_type": s.section_type,
                    "start_time": parse_time_to_seconds(s.start_seconds),
                    "end_time": parse_time_to_seconds(s.end_seconds),
                    "musical_features": s.musical_features,
                    "section_emotion": s.section_emotion,
                    "suggested_visual_intensity": s.suggested_visual_intensity,
                    "suggested_rhythmic_strategy": s.suggested_rhythmic_strategy,
                    "suggested_visual_theme": s.suggested_visual_theme,
                    "suggested_context": s.suggested_context,
                }
                for s in gemini_result.sections
            ]
        if fill_gaps and audio_segments:
            audio_segments, sections_list = postprocess_transcription(
                audio_segments,
                sections_list or None,
                total_duration=actual_duration,
                fill_gaps_enabled=True,
            )

        # 切分粒度后处理（仅在 beat 模式触发；sentence/phrase 完全靠 prompt 引导）
        if audio_segments and _granularity == AudioSegmentGranularity.BEAT:
            bpm_val = getattr(gemini_result, "global_bpm", None)
            if bpm_val and float(bpm_val) > 0:
                bar_dur = 240.0 / float(bpm_val)
                word_edges_sec: List[float] = []
                if getattr(gemini_result, "words", None):
                    for w in gemini_result.words:
                        try:
                            word_edges_sec.append(parse_time_to_seconds(w.start))
                            word_edges_sec.append(parse_time_to_seconds(w.end))
                        except Exception:
                            pass
                snapped = snap_segment_edges_to_bar_grid(
                    audio_segments,
                    bar_dur_sec=bar_dur,
                    tolerance_sec=min(0.20, bar_dur * 0.25),
                    word_edges_sec=word_edges_sec,
                    total_duration=actual_duration,
                )
                logger.info(
                    "🥁 beat 后处理：BPM=%.1f, bar_dur=%.3fs, segments=%d → %d（端点吸附到小节线，遇词内自动漂移）",
                    float(bpm_val), bar_dur, len(audio_segments), len(snapped),
                )
                audio_segments = snapped
            else:
                logger.warning(
                    "🥁 beat 模式但 Gemini 未给出 global_bpm，跳过小节吸附（segments 保留原 LLM 输出）"
                )

        # 构建 additional_data，存储 Gemini 原始数据（保持原始 MM:SS.mmm 格式）
        additional_data = {
            "gemini_segments": [
                {
                    "id": i,
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text,
                    "duration": seg.duration,
                    "emotion": seg.emotion,
                    "tempo": seg.tempo,
                    "vocal_presence": getattr(seg, "vocal_presence", None),
                    "vocal_gender": getattr(seg, "vocal_gender", None),
                    "section_index": getattr(seg, "section_index", None),
                    "start_seconds": parse_time_to_seconds(seg.start),
                    "end_seconds": parse_time_to_seconds(seg.end),
                    "duration_seconds": parse_time_to_seconds(seg.end) - parse_time_to_seconds(seg.start)
                }
                for i, seg in enumerate(gemini_result.segments)
            ],
            "transcription_method": "gemini",
            "is_instrumental": gemini_result.is_instrumental,  # 是否为纯音乐
            "original_duration": gemini_result.duration,  # Gemini 给出的时长（MM:SS.mmm）
            "duration_seconds": parse_time_to_seconds(gemini_result.duration),  # Gemini给出的时长（秒数）
            "actual_duration_seconds": actual_duration,  # 实际音频时长（秒数）
            "audio_segment_granularity": _granularity.value,  # 本次切分粒度（phrase/sentence/beat），供下游审计
        }
        # 整曲级 Global，供 music_generation_service 写入 video_audio_transcription
        if gemini_result.song_name is not None:
            additional_data["song_name"] = gemini_result.song_name
        if gemini_result.global_bpm is not None:
            additional_data["global_bpm"] = float(gemini_result.global_bpm)
        if gemini_result.genre is not None:
            additional_data["genre"] = gemini_result.genre
        if gemini_result.global_emotion is not None:
            additional_data["global_emotion"] = gemini_result.global_emotion
        if gemini_result.suggested_global_theme is not None:
            additional_data["suggested_global_theme"] = gemini_result.suggested_global_theme
        if gemini_result.suggested_color_palette is not None:
            additional_data["suggested_color_palette"] = gemini_result.suggested_color_palette
        # Song Structure 段落，供 music_generation_service 写入 video_audio_section
        if sections_list:
            additional_data["sections"] = sections_list
        elif gemini_result.sections and len(gemini_result.sections) > 0:
            additional_data["sections"] = [
                {
                    "section_type": s.section_type,
                    "start_time": parse_time_to_seconds(s.start_seconds),
                    "end_time": parse_time_to_seconds(s.end_seconds),
                    "musical_features": s.musical_features,
                    "section_emotion": s.section_emotion,
                    "suggested_visual_intensity": s.suggested_visual_intensity,
                    "suggested_rhythmic_strategy": s.suggested_rhythmic_strategy,
                    "suggested_visual_theme": s.suggested_visual_theme,
                    "suggested_context": s.suggested_context,
                }
                for s in gemini_result.sections
            ]
            _sync_section_end_times_to_segments(
                additional_data["sections"],
                audio_segments,
                actual_duration,
            )
        else:
            additional_data["sections"] = fallback_full_track_sections(
                actual_duration,
                audio_segments,
                global_emotion=gemini_result.global_emotion,
            )
            logger.info(
                "🎵 未返回曲式段落，已后置注入默认整轨 section [0, %.2f)s",
                additional_data["sections"][0]["end_time"],
            )
        
        # 暂时不保存words数据，但保留处理逻辑以便将来恢复
        gemini_words = getattr(gemini_result, 'words', None)
        if gemini_words:
            additional_data["gemini_words"] = [
                {
                    "id": word.id,
                    "word": word.word,
                    "start": word.start,
                    "end": word.end
                }
                for word in gemini_words
            ]
            # 为了与Whisper兼容，也将words数据存储在标准的"words"字段中
            additional_data["words"] = [
                {
                    "id": word.id,
                    "word": word.word,
                    "start": word.start,
                    "end": word.end
                }
                for word in gemini_words
            ]
        
        result = AudioTranscription(
            task=gemini_result.task,
            language=gemini_result.language,
            duration=actual_duration,  # 使用实际音频时长而非Gemini给出的时长
            text=gemini_result.text,
            segments=audio_segments,  # 处理后的片段（已 fill gaps 和合并）
            audio_url=audio_url,
            filename=filename,  # 原始文件名
            is_instrumental=gemini_result.is_instrumental,  # 是否为纯音乐
            additional_data=additional_data  # Gemini 原始数据（包含Gemini给出的时长作为参考）
        )
        
        logger.info(f"🎵 Gemini 音频转录完成: 语言={result.language}, 时长={result.duration:.2f}s, 片段数={len(result.segments)}")
        
        return result
        
    except Exception as e:
        logger.error(f"Gemini 音频转录失败: {audio_url}, 错误: {e}")
        return None


async def transcribe_audio(
    audio_url: str,
    method: str = "gemini",
    user_option: Optional[UserOption] = None,
    fill_gaps: bool = True,
    user_input: Optional[str] = None,
    filename: Optional[str] = None
) -> Optional[AudioTranscription]:
    """
    统一的音频转录接口（仅支持 Gemini）
    
    Args:
        audio_url: 音频文件的 CDN URL
        method: 转录方法，仅支持 "gemini"（默认）
        user_option: 用户选项配置
        fill_gaps: 是否填补缺失的时间段
        user_input: 用户输入的内容（可能包含歌词）
        filename: 原始音频文件名
        
    Returns:
        Optional[AudioTranscription]: 转录结果，失败返回 None
    """
    logger.info("🎵 使用 Gemini 进行音频转录")
    return await transcribe_audio_with_gemini(
        audio_url=audio_url,
        user_option=user_option,
        fill_gaps=fill_gaps,
        user_input=user_input,
        filename=filename
    )
