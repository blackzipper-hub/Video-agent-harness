"""音乐智能剪辑（Smart Clip）分析服务。

目标：当 Suno 生成的歌曲实际时长与用户目标时长偏差较大时，
基于已有的转录 sections / vocal_presence 推理出最佳裁切点 + 推荐选区。

关键设计：
1. 仅在 lyrics_provided / auto_lyrics_song 路径调用（纯 BGM 不做智能剪辑，由后端 mix loop 处理）。
2. 输入：transcription（已含 sections / segments / vocal_presence / global_bpm）+ target_duration。
3. **不**重新上传整段音频给 Gemini —— 走纯文本结构化推理，省钱、快、确定性更好。
4. AI 失败时回退 ``_heuristic_section_based_analysis``，仍能给可用结果。
5. 结果被写入 ``music_generations.additional_data.smart_clip``，由 ``gate_after_music_node`` 在
   ``interrupt()`` payload 中带给前端，用户在 after_music 暂停 UI 内确认 → resume 时一并 trim。
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from ....utils.time_format import format_sec_to_mmss

logger = logging.getLogger(__name__)


# ==================== Pydantic schemas ====================


class SmartCutKind(str, Enum):
    """候选裁切点类型。"""
    PHRASE_END = "phrase_end"            # 乐句结束
    BAR_LINE = "bar_line"                # 小节线（基于 BPM 推断）
    LOW_ENERGY = "low_energy"            # 能量低谷（适合 fade-out 锚点）
    NATURAL_FADEOUT = "natural_fadeout"  # 歌曲自然淡出 / 段落收束
    SECTION_BOUNDARY = "section_boundary"  # Verse/Chorus/Outro 边界


class SmartCutCandidate(BaseModel):
    """候选裁切点。"""
    time_sec: float = Field(description="候选裁切点（秒，相对音频起点）")
    kind: SmartCutKind = Field(description="候选点类型")
    vocal_safe: bool = Field(description="VAD：该点附近 ±300ms 是否无人声")
    energy_score: float = Field(ge=0.0, le=1.0, description="能量分（0=最静，1=爆音）；越低越适合 fade-out")
    score: float = Field(ge=0.0, le=1.0, description="综合推荐分；越高越优")
    rationale: str = Field(description="为何选此点（音乐学角度，简短）")


class SmartClipSelection(BaseModel):
    """推荐选区：start ~ end 是裁切后片段的时间范围。"""
    start_sec: float = Field(ge=0.0, description="起点（秒）")
    end_sec: float = Field(description="终点（秒）")
    fade_in_sec: float = Field(ge=0.0, le=8.0, description="淡入时长（秒）")
    fade_out_sec: float = Field(ge=0.0, le=8.0, description="淡出时长（秒）")
    target_duration_sec: float = Field(description="用户目标时长")
    actual_duration_sec: float = Field(description="选区实际时长 = end_sec - start_sec")
    duration_error_sec: float = Field(description="actual - target 的绝对差")
    reasoning: str = Field(description="为何选这一段（章节命中、人声完整、能量起伏等）")


class SmartClipAnalysis(BaseModel):
    """智能剪辑分析输出。"""
    audio_duration_sec: float
    target_duration_sec: float
    candidates: List[SmartCutCandidate] = Field(
        default_factory=list,
        description="候选裁切点（按 score 降序）",
    )
    recommended: SmartClipSelection
    fallback_used: bool = Field(default=False, description="AI 失败启用启发式")
    method: str = Field(description="ai_reuse_transcription / heuristic_section / heuristic_center / passthrough")


# ==================== 主入口 ====================


_PASSTHROUGH_GAP_SEC = 1.0  # 音频已经短于 target+gap 时直接全段


async def analyze_music_smart_clip(
    audio_url: str,
    target_duration_sec: float,
    transcription: Any,
    *,
    audio_duration_sec: Optional[float] = None,
) -> SmartClipAnalysis:
    """复用已有转录的 sections / vocal_presence 做裁切候选分析。

    Args:
        audio_url: 音乐 URL（仅用于日志 / 落库引用，不再二次上传给 Gemini）
        target_duration_sec: 用户希望的目标时长（秒）
        transcription: ``transcribe_audio_with_gemini`` 返回的 ``AudioTranscription``，
            **必须**包含 ``sections``（曲式段落）和 ``segments`` 的 ``vocal_presence``。
        audio_duration_sec: 可选，未传时尝试从 transcription / media service 推算。

    Returns:
        ``SmartClipAnalysis``，含候选点列表 + 推荐选区。
    """
    # —— 1. 兜底拿到 audio_duration_sec
    if audio_duration_sec is None or audio_duration_sec <= 0.0:
        td = getattr(transcription, "duration", None)
        if isinstance(td, (int, float)) and td > 0:
            audio_duration_sec = float(td)
    if audio_duration_sec is None or audio_duration_sec <= 0.0:
        try:
            from app.utils.media_service_client import audio_info
            info = await audio_info(audio_url)
            audio_duration_sec = float(info.get("duration") or 0.0)
        except Exception as e:
            logger.warning("smart_clip: audio_info 失败 %s，回退使用 transcription.segments 末尾", e)
            audio_duration_sec = _max_segment_end(transcription)

    if not audio_duration_sec or audio_duration_sec <= 0.0:
        raise ValueError("smart_clip: 无法确定 audio_duration_sec")

    # —— 2. 已经短于 target：直通
    if audio_duration_sec <= float(target_duration_sec) + _PASSTHROUGH_GAP_SEC:
        return _passthrough_analysis(audio_duration_sec, target_duration_sec)

    # —— 3. 走 AI（纯文本结构化推理；不重传 audio）
    try:
        result = await _ai_analyze_with_transcription(
            audio_duration_sec, target_duration_sec, transcription,
        )
    except Exception as e:
        logger.warning("smart_clip AI 失败，回退启发式: %s", e)
        try:
            result = _heuristic_section_based_analysis(
                audio_duration_sec, target_duration_sec, transcription,
            )
        except Exception as e2:
            logger.warning("smart_clip 基于 sections 启发式失败，回退中点：%s", e2)
            result = _heuristic_center_analysis(audio_duration_sec, target_duration_sec)
    # 任意路径返回前都做一次 sanity normalize（clamp 越界 / 重算 duration_error）
    return _normalize_analysis(result, audio_duration_sec, target_duration_sec)


# ==================== AI 路径 ====================


async def _ai_analyze_with_transcription(
    audio_duration_sec: float,
    target_duration_sec: float,
    transcription: Any,
) -> SmartClipAnalysis:
    """纯文本结构化推理（不上传 audio）。"""
    from langchain_core.messages import SystemMessage, HumanMessage
    from app.orchestration.skills.prompt_context import (
        facts_human_message,
        skill_system_message,
    )
    from prompts.prompt_config import PROMPTS_CONFIG, PromptName
    from app.services.agent.utils.llm_resilience import (
        StructuredResilienceKind, ainvoke_structured_resilient,
    )

    sections_view = _build_sections_view(transcription)
    segments_view = _build_segments_view(transcription)

    system = skill_system_message(
        "music-smart-clip-director",
        lead="Follow music-smart-clip-director.",
    )
    facts = {
        "audio_duration_sec": round(float(audio_duration_sec), 2),
        "target_duration_sec": round(float(target_duration_sec), 2),
        "global_bpm": _safe_str(
            getattr(transcription, "global_bpm", None) or _extra_get(transcription, "global_bpm")
        ),
        "global_emotion": _safe_str(
            getattr(transcription, "global_emotion", None)
            or _extra_get(transcription, "global_emotion")
        ),
        "genre": _safe_str(_extra_get(transcription, "genre")),
        "sections": sections_view or "",
        "segments": segments_view or "",
    }
    messages = [
        SystemMessage(content=system),
        HumanMessage(content=facts_human_message(facts)),
    ]
    parsed = await ainvoke_structured_resilient(
        prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_MUSIC_SMART_CLIP_ANALYSIS],
        kind=StructuredResilienceKind.STRUCTURED_CHAT_MESSAGES,
        structured_chat_messages=messages,
        include_raw=False,
        structured_schema=SmartClipAnalysis,
    )
    # ainvoke_structured_resilient 在 include_raw=False 时直接返回结构化对象
    if isinstance(parsed, SmartClipAnalysis):
        result = parsed
    else:
        result = SmartClipAnalysis.model_validate(parsed)

    # 主入口出口处会统一 _normalize_analysis，这里仅修补 method 字段缺失
    if not result.method:
        result = result.model_copy(update={"method": "ai_reuse_transcription"})
    return result


# ==================== 启发式回退 ====================


def _passthrough_analysis(audio_duration_sec: float, target_duration_sec: float) -> SmartClipAnalysis:
    """音频已经短于目标：不裁，全段。"""
    return SmartClipAnalysis(
        audio_duration_sec=float(audio_duration_sec),
        target_duration_sec=float(target_duration_sec),
        candidates=[],
        recommended=SmartClipSelection(
            start_sec=0.0,
            end_sec=float(audio_duration_sec),
            fade_in_sec=0.0,
            fade_out_sec=0.0,
            target_duration_sec=float(target_duration_sec),
            actual_duration_sec=float(audio_duration_sec),
            duration_error_sec=abs(float(audio_duration_sec) - float(target_duration_sec)),
            reasoning="音频已短于（或接近）目标时长，无需裁切。",
        ),
        fallback_used=False,
        method="passthrough",
    )


def _heuristic_section_based_analysis(
    audio_duration_sec: float,
    target_duration_sec: float,
    transcription: Any,
) -> SmartClipAnalysis:
    """无 AI 时基于 sections 找时长接近 target 的连续段落组合。"""
    sections = _build_sections_view(transcription)
    if not sections:
        return _heuristic_center_analysis(audio_duration_sec, target_duration_sec)

    # 候选：每条 section 的 start / end 都是潜在裁切点
    candidates: List[SmartCutCandidate] = []
    for s in sections:
        for ts_str in (s.get("end_time"),):
            try:
                t = float(ts_str)
            except (TypeError, ValueError):
                continue
            if t <= 0.0 or t >= audio_duration_sec - 0.1:
                continue
            candidates.append(SmartCutCandidate(
                time_sec=t,
                kind=SmartCutKind.SECTION_BOUNDARY,
                vocal_safe=True,
                energy_score=0.3,
                score=0.7,
                rationale=f"section {s.get('section_type')} 结束",
            ))

    # 推荐选区：从 0 开始累加 sections，找 |actual - target| 最小的累加点
    target = float(target_duration_sec)
    best_end = None
    best_err = None
    cum = 0.0
    for s in sections:
        try:
            sec_dur = max(0.0, float(s.get("end_time")) - float(s.get("start_time")))
        except (TypeError, ValueError):
            continue
        cum += sec_dur
        err = abs(cum - target)
        if best_err is None or err < best_err:
            best_err = err
            best_end = cum
    if best_end is None or best_end <= 0.0:
        return _heuristic_center_analysis(audio_duration_sec, target_duration_sec)

    end_sec = min(best_end, audio_duration_sec)
    duration = end_sec
    fade_in = 0.5 if duration >= 4.0 else 0.0
    fade_out = min(1.5, max(0.5, duration * 0.05))
    return SmartClipAnalysis(
        audio_duration_sec=float(audio_duration_sec),
        target_duration_sec=target,
        candidates=sorted(candidates, key=lambda c: c.score, reverse=True)[:12],
        recommended=SmartClipSelection(
            start_sec=0.0,
            end_sec=float(end_sec),
            fade_in_sec=float(fade_in),
            fade_out_sec=float(fade_out),
            target_duration_sec=target,
            actual_duration_sec=float(duration),
            duration_error_sec=float(abs(duration - target)),
            reasoning="按曲式段落组合时长最接近目标的节点裁切。",
        ),
        fallback_used=True,
        method="heuristic_section",
    )


def _heuristic_center_analysis(
    audio_duration_sec: float,
    target_duration_sec: float,
) -> SmartClipAnalysis:
    """终极兜底：取居中 target 长度。"""
    target = float(target_duration_sec)
    half = target / 2.0
    mid = float(audio_duration_sec) / 2.0
    start = max(0.0, mid - half)
    end = min(float(audio_duration_sec), start + target)
    duration = end - start
    return SmartClipAnalysis(
        audio_duration_sec=float(audio_duration_sec),
        target_duration_sec=target,
        candidates=[],
        recommended=SmartClipSelection(
            start_sec=float(start),
            end_sec=float(end),
            fade_in_sec=1.0,
            fade_out_sec=2.0,
            target_duration_sec=target,
            actual_duration_sec=float(duration),
            duration_error_sec=float(abs(duration - target)),
            reasoning="居中截取目标时长。",
        ),
        fallback_used=True,
        method="heuristic_center",
    )


# ==================== helpers ====================


def _max_segment_end(transcription: Any) -> float:
    segs = getattr(transcription, "segments", None) or []
    end_max = 0.0
    for s in segs:
        try:
            e = float(getattr(s, "end", 0.0))
        except (TypeError, ValueError):
            continue
        if e > end_max:
            end_max = e
    return end_max


def _extra_get(transcription: Any, key: str) -> Any:
    extra = getattr(transcription, "additional_data", None) or {}
    if isinstance(extra, dict):
        return extra.get(key)
    return None


def _safe_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _build_sections_view(transcription: Any) -> List[dict]:
    """从 transcription 的 ``additional_data["sections"]`` 或自身 ``sections`` 字段
    生成纯字符串的 prompt 视图（避免序列化 datetime / pydantic 等复杂对象）。"""
    raw = _extra_get(transcription, "sections")
    if not raw:
        raw = getattr(transcription, "sections", None) or []
    out: List[dict] = []
    if not isinstance(raw, list):
        return out
    for s in raw:
        if not isinstance(s, dict):
            continue
        try:
            st = float(s.get("start_time") or 0.0)
            et = float(s.get("end_time") or 0.0)
        except (TypeError, ValueError):
            continue
        if et <= st:
            continue
        out.append({
            "section_type": _safe_str(s.get("section_type")),
            "start_time": f"{st:.2f}",
            "end_time": f"{et:.2f}",
            "start_display": format_sec_to_mmss(st),
            "end_display": format_sec_to_mmss(et),
            "section_emotion": _safe_str(s.get("section_emotion")),
            "musical_features": _safe_str(s.get("musical_features")),
        })
    return out


def _build_segments_view(transcription: Any) -> List[dict]:
    """生成 segments 的简化视图：start, end, vocal_presence。"""
    segs = getattr(transcription, "segments", None) or []
    out: List[dict] = []
    for seg in segs:
        try:
            st = float(getattr(seg, "start", 0.0))
            et = float(getattr(seg, "end", 0.0))
        except (TypeError, ValueError):
            continue
        if et <= st:
            continue
        vp = getattr(seg, "vocal_presence", None)
        if vp is None:
            vp = bool((getattr(seg, "text", "") or "").strip())
        out.append({
            "start": f"{st:.2f}",
            "end": f"{et:.2f}",
            "start_display": format_sec_to_mmss(st),
            "end_display": format_sec_to_mmss(et),
            "vocal_presence": bool(vp),
        })
    return out


def _normalize_analysis(
    analysis: SmartClipAnalysis,
    audio_duration_sec: float,
    target_duration_sec: float,
) -> SmartClipAnalysis:
    """对 AI 输出做 clamp / 一致性修正：时间不超出音频时长、duration_error 重算。"""
    rec = analysis.recommended
    start = max(0.0, min(float(rec.start_sec), float(audio_duration_sec)))
    end = max(start + 0.1, min(float(rec.end_sec), float(audio_duration_sec)))
    duration = end - start
    fi = max(0.0, min(float(rec.fade_in_sec or 0.0), 8.0))
    fo = max(0.0, min(float(rec.fade_out_sec or 0.0), 8.0))
    rec_fixed = rec.model_copy(update={
        "start_sec": start,
        "end_sec": end,
        "fade_in_sec": fi,
        "fade_out_sec": fo,
        "actual_duration_sec": duration,
        "target_duration_sec": float(target_duration_sec),
        "duration_error_sec": abs(duration - float(target_duration_sec)),
    })
    # 候选：clamp time_sec 到 [0, audio_duration]
    new_cands: List[SmartCutCandidate] = []
    for c in analysis.candidates or []:
        t = max(0.0, min(float(c.time_sec), float(audio_duration_sec)))
        new_cands.append(c.model_copy(update={"time_sec": t}))
    return analysis.model_copy(update={
        "audio_duration_sec": float(audio_duration_sec),
        "target_duration_sec": float(target_duration_sec),
        "recommended": rec_fixed,
        "candidates": new_cands,
    })
