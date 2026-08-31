"""上传音频 Smart Clip 推荐服务（仅 recommend，不 apply）。

供前端 AudioCropDialog 在上传裁剪前调用：上传 → Gemini 转录（快路径，无 Suno）→ analyze → 返回 start/end。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from ....exceptions import BusinessException, BusinessExceptionCode
from ....utils.s3_utils import s3_utils
from .smart_clip_flow import (
    build_upload_crop_recommend_payload,
    log_step_timing,
    run_smart_clip_analysis,
    transcribe_audio_for_analysis,
)

logger = logging.getLogger(__name__)


async def recommend_upload_audio_smart_clip(
    file_content: bytes,
    *,
    filename: Optional[str],
    content_type: Optional[str],
    target_duration_sec: float,
) -> Dict[str, Any]:
    """对用户上传的本地音频给出 Smart Clip 推荐区间（不落库、无 peaks、无 reasoning）。"""
    if not file_content:
        raise BusinessException(BusinessExceptionCode.BUSINESS_ERROR, "文件为空")
    if target_duration_sec <= 0:
        raise BusinessException(BusinessExceptionCode.BUSINESS_ERROR, "target_duration_sec 必须大于 0")

    ct = (content_type or "").lower()
    name = (filename or "").lower()
    if not (ct.startswith("audio/") or name.endswith((".mp3", ".wav", ".m4a", ".ogg", ".aac", ".flac"))):
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "仅支持音频文件（audio/* 或 mp3/wav/m4a/ogg 等）",
        )

    t0 = time.monotonic()

    t_upload = time.monotonic()
    audio_url = await s3_utils.upload_audio(
        file_content,
        filename=filename,
        content_type=ct or "audio/mpeg",
    )
    log_step_timing("upload_s3", t_upload, filename=filename)

    t_transcribe = time.monotonic()
    audio_transcription = await transcribe_audio_for_analysis(
        audio_url,
        filename=filename,
        fast_path=True,
    )
    log_step_timing("transcribe_gemini", t_transcribe, filename=filename)

    if not audio_transcription:
        log_step_timing("total_failed_transcribe", t0)
        return {
            "status": "failed",
            "recommended": None,
            "error": "音频转录失败",
        }

    t_analyze = time.monotonic()
    sc_analysis = await run_smart_clip_analysis(
        audio_url=audio_url,
        target_duration_sec=float(target_duration_sec),
        transcription=audio_transcription,
        audio_duration_sec=float(audio_transcription.duration),
    )
    log_step_timing("analyze_smart_clip", t_analyze, method=sc_analysis.method)

    payload = build_upload_crop_recommend_payload(sc_analysis)
    log_step_timing("total", t0, status=payload["status"])
    return payload
