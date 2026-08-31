"""上传音频 Smart Clip 推荐 API（仅 recommend，裁剪仍在前端本地完成）。"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, File, Form, Security, UploadFile

from ...exceptions import BusinessException, BusinessExceptionCode
from ...schemas import ResponseModel
from ...services.auth_service import auth_service
from ...services.agent.video.audio_smart_clip_recommend_service import (
    recommend_upload_audio_smart_clip,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audio", tags=["audio-smart-clip"])


@router.post("/smart-clip/recommend", response_model=ResponseModel[Dict[str, Any]])
async def recommend_upload_smart_clip(
    file: UploadFile = File(..., description="用户上传的音频文件"),
    target_duration_sec: float = Form(..., description="目标视频时长（秒）"),
    user_id: str = Security(auth_service.get_current_user_or_service_user),
):
    """上传音频并返回 Smart Clip 推荐裁剪区间；前端仍用 cropAudioFile 本地裁剪。"""
    if target_duration_sec <= 0 or target_duration_sec > 600:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            "target_duration_sec 须在 (0, 600] 范围内",
        )

    content = await file.read()
    try:
        payload = await recommend_upload_audio_smart_clip(
            content,
            filename=file.filename,
            content_type=file.content_type,
            target_duration_sec=target_duration_sec,
        )
    except BusinessException:
        raise
    except Exception as e:
        logger.exception("upload smart clip recommend failed: %s", e)
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"智能裁剪推荐失败: {e}",
        ) from e

    return ResponseModel.success(data=payload)
