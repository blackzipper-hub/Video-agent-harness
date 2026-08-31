"""Smart Clip 与音频转录的共享流程（workflow + 上传裁剪 recommend 复用）。

职责划分：
- ``transcribe_audio_for_analysis``：转录（workflow 走配置；upload recommend 走 gemini 快路径）
- ``run_smart_clip_analysis``：调用 ``analyze_music_smart_clip``
- ``build_smart_clip_ready_payload``：workflow interrupt / DB 落库用完整 payload（含 peaks）
- ``build_upload_crop_recommend_payload``：上传裁剪弹窗用精简 payload（仅 start/end）
- ``fetch_smart_clip_peaks``：可选波形（仅 workflow SmartClipPanel 需要）
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from ....models.video_state import UserOption
from .music_smart_clip_service import SmartClipAnalysis, analyze_music_smart_clip

logger = logging.getLogger(__name__)


async def transcribe_audio_for_analysis(
    audio_url: str,
    *,
    filename: Optional[str] = None,
    suno_clip_id: Optional[str] = None,
    user_option: Optional[UserOption] = None,
    user_input: Optional[str] = None,
    generated_lyrics: Optional[str] = None,
    music_intent: Optional[str] = None,
    music_workflow_mode: Optional[str] = None,
    fast_path: bool = False,
):
    """转录音频；``fast_path=True`` 时跳过 hybrid/Suno，仅 Gemini（upload crop recommend 专用）。"""
    from app.agent_config import DefaultValues, get_audio_segment_granularity_for_method

    if fast_path:
        transcription_method = "gemini"
    else:
        transcription_method = DefaultValues.TRANSCRIPTION_METHOD

    transcribe_granularity = get_audio_segment_granularity_for_method(transcription_method)

    if transcription_method == "hybrid":
        from ....tools.transcribe.hybrid import transcribe_audio_with_hybrid

        logger.info(
            "🎵 [transcribe_for_analysis] hybrid | audio_source=%s | clip_id=%s | filename=%s",
            "suno_generated" if suno_clip_id else "user_upload",
            suno_clip_id or "(none)",
            filename,
        )
        return await transcribe_audio_with_hybrid(
            audio_url,
            clip_id=suno_clip_id,
            user_option=user_option,
            user_input=user_input,
            filename=filename,
            generated_lyrics=generated_lyrics,
            granularity=transcribe_granularity,
            music_intent=music_intent,
            music_workflow_mode=music_workflow_mode,
        )

    from ....tools.transcribe.gemini import transcribe_audio_with_gemini

    logger.info(
        "🎵 [transcribe_for_analysis] %s | fast_path=%s | filename=%s",
        transcription_method,
        fast_path,
        filename,
    )
    return await transcribe_audio_with_gemini(
        audio_url,
        user_option=user_option,
        user_input=user_input,
        filename=filename,
        generated_lyrics=generated_lyrics,
        granularity=transcribe_granularity,
        music_intent=music_intent,
        music_workflow_mode=music_workflow_mode,
    )


async def run_smart_clip_analysis(
    audio_url: str,
    target_duration_sec: float,
    transcription: Any,
    *,
    audio_duration_sec: Optional[float] = None,
) -> SmartClipAnalysis:
    return await analyze_music_smart_clip(
        audio_url=audio_url,
        target_duration_sec=float(target_duration_sec),
        transcription=transcription,
        audio_duration_sec=audio_duration_sec,
    )


def build_smart_clip_ready_payload(
    sc_analysis: SmartClipAnalysis,
    audio_url: str,
    *,
    peaks: Optional[dict] = None,
) -> Dict[str, Any]:
    """Workflow gate_after_music / SmartClipPanel 用的完整 payload。"""
    return {
        "status": "ready",
        "audio_url": audio_url,
        "audio_duration_sec": sc_analysis.audio_duration_sec,
        "target_duration_sec": sc_analysis.target_duration_sec,
        "recommended": sc_analysis.recommended.model_dump(),
        "fallback_used": sc_analysis.fallback_used,
        "method": sc_analysis.method,
        "peaks": peaks,
        "user_confirmed": None,
    }


def build_upload_crop_recommend_payload(sc_analysis: SmartClipAnalysis) -> Dict[str, Any]:
    """上传裁剪弹窗：只返回起止秒，不含 reasoning/method/peaks。"""
    return {
        "status": "ready",
        "recommended": {
            "start_sec": sc_analysis.recommended.start_sec,
            "end_sec": sc_analysis.recommended.end_sec,
        },
    }


async def fetch_smart_clip_peaks(audio_url: str, run_id: str) -> Optional[dict]:
    try:
        from ....utils import media_service_client as msc

        return await msc.audio_peaks(
            audio_url=audio_url,
            sample_count=512,
            run_id=run_id,
        )
    except Exception as e:
        logger.warning("🎵 [smart_clip] peaks 失败（前端可退化为本地波形）：%s", e)
        return None


def log_step_timing(step: str, started_at: float, **extra: Any) -> None:
    elapsed = time.monotonic() - started_at
    parts = " ".join(f"{k}={v}" for k, v in extra.items())
    logger.info("🎵 [smart_clip_timing] %s %.2fs %s", step, elapsed, parts)
