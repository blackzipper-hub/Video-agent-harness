"""
视频等多媒体工具（从 Cuti-VideoAgent 迁移）。

``prepare_video_for_llm``：下载 URL 或读本地文件 → 优先上传 Google 拿 ``file_uri``，失败则小文件走 base64。
HTTP 下载使用 ``aiohttp``（不依赖 ``s3_utils``）。

``process_uploaded_files``：与 Cuti-VideoAgent ``app.chat.utils.file_utils`` 一致，
将 FastAPI ``UploadFile`` 读入内存并上传 S3，返回 ``ImageUserInput`` / ``AudioFileUserInput`` / ``VideoFileUserInput`` 列表。
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import pathlib
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
import aiohttp

from app.chat.exceptions import BusinessException, BusinessExceptionCode
from app.chat.utils.google_file_upload import upload_video_to_google
from app.chat.utils import media_service_client as msc

logger = logging.getLogger(__name__)


async def _maybe_audio_duration_from_msc(audio_url: str) -> Optional[float]:
    """上传 S3 后调用 Cuti-Media-Service ``audio/info``（与 Cuti-VideoAgent 一致）。"""
    try:
        info = await msc.audio_info(audio_url)
        if not isinstance(info, dict):
            return None
        for key in ("duration", "duration_sec", "duration_seconds", "length"):
            v = info.get(key)
            if v is not None:
                try:
                    return round(float(v), 2)
                except (TypeError, ValueError):
                    continue
    except Exception as exc:
        logger.warning("audio_info failed url=%s err=%s", (audio_url or "")[:80], exc)
    return None


SUPPORTED_VIDEO_EXTENSIONS = (
    ".mp4",
    ".mpeg",
    ".mov",
    ".avi",
    ".flv",
    ".mpg",
    ".webm",
    ".wmv",
    ".3gpp",
)
SUPPORTED_VIDEO_MIMETYPES = (
    "video/mp4",
    "video/mpeg",
    "video/quicktime",
    "video/avi",
    "video/x-msvideo",
    "video/x-flv",
    "video/mpg",
    "video/webm",
    "video/wmv",
    "video/3gpp",
)


class VideoContentForLLM:
    """视频内容用于 LLM 的结构化数据"""

    def __init__(
        self,
        use_file_uri: bool,
        file_uri: Optional[str] = None,
        base64_data: Optional[str] = None,
        mime_type: str = "video/mp4",
    ):
        self.use_file_uri = use_file_uri
        self.file_uri = file_uri
        self.base64_data = base64_data
        self.mime_type = mime_type

    def to_media_content(self) -> Dict[str, Any]:
        if self.use_file_uri and self.file_uri:
            return {"type": "media", "file_uri": self.file_uri, "mime_type": self.mime_type}
        if self.base64_data:
            return {"type": "media", "data": self.base64_data, "mime_type": self.mime_type}
        raise ValueError("VideoContentForLLM: 既没有 file_uri 也没有 base64_data")


async def _download_url_to_path(url: str, dest_path: str) -> bool:
    try:
        timeout = aiohttp.ClientTimeout(total=600)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning("prepare_video_for_llm: GET %s -> %s", url[:120], resp.status)
                    return False
                data = await resp.read()
        async with aiofiles.open(dest_path, "wb") as f:
            await f.write(data)
        return True
    except Exception as e:
        logger.warning("prepare_video_for_llm: 下载失败 %s", e)
        return False


async def prepare_video_for_llm(
    video_path: Optional[str] = None,
    video_url: Optional[str] = None,
    mime_type: Optional[str] = None,
    max_wait_time: int = 180,
    max_base64_size_mb: float = 20.0,
) -> VideoContentForLLM:
    """
    准备视频内容用于 LLM（优先 ``file_uri``，降级 base64）。行为与 Cuti-VideoAgent 一致。
    """
    temp_file_path: Optional[str] = None
    need_cleanup = False

    try:
        if video_path:
            if not Path(video_path).exists():
                raise BusinessException(
                    BusinessExceptionCode.FILE_UPLOAD_FAILED,
                    f"视频文件不存在: {video_path}",
                )
            actual_video_path = video_path
            logger.info("prepare_video_for_llm: 本地文件 %s", video_path)
        elif video_url:
            video_ext = Path(video_url).suffix.lower() or ".mp4"
            tmp = tempfile.NamedTemporaryFile(suffix=video_ext, delete=False)
            temp_file_path = tmp.name
            tmp.close()
            need_cleanup = True
            ok = await _download_url_to_path(video_url, temp_file_path)
            if not ok:
                raise BusinessException(
                    BusinessExceptionCode.FILE_UPLOAD_FAILED,
                    f"无法下载视频: {video_url}",
                )
            actual_video_path = temp_file_path
            logger.info("prepare_video_for_llm: 已下载 URL 到 %s", temp_file_path)
        else:
            raise ValueError("必须提供 video_path 或 video_url 之一")

        if not mime_type:
            video_ext = Path(actual_video_path).suffix.lower()
            ext_to_mime = dict(zip(SUPPORTED_VIDEO_EXTENSIONS, SUPPORTED_VIDEO_MIMETYPES))
            mime_type = ext_to_mime.get(video_ext, "video/mp4")

        file_size_mb = os.path.getsize(actual_video_path) / (1024 * 1024)
        logger.info("prepare_video_for_llm: 文件大小 %.2f MB", file_size_mb)

        file_uri, google_mime = await upload_video_to_google(actual_video_path, max_wait_time=max_wait_time)

        if file_uri:
            effective_mime = google_mime or mime_type
            logger.info("prepare_video_for_llm: 使用 Google file_uri, mime=%s", effective_mime)
            return VideoContentForLLM(
                use_file_uri=True,
                file_uri=file_uri,
                mime_type=effective_mime,
            )

        logger.warning("prepare_video_for_llm: Google 上传失败，尝试 base64 降级")
        if file_size_mb > max_base64_size_mb:
            raise BusinessException(
                BusinessExceptionCode.FILE_UPLOAD_FAILED,
                f"视频过大（{file_size_mb:.2f}MB），超过 {max_base64_size_mb}MB 且无法使用 file_uri",
            )

        async with aiofiles.open(actual_video_path, "rb") as f:
            video_bytes = await f.read()
        b64 = (await asyncio.to_thread(base64.b64encode, video_bytes)).decode("utf-8")
        return VideoContentForLLM(use_file_uri=False, base64_data=b64, mime_type=mime_type or "video/mp4")

    finally:
        if need_cleanup and temp_file_path:
            try:
                os.unlink(temp_file_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 用户 multipart 上传（与 Cuti-VideoAgent file_utils 对齐，供 agent-router /stream）
# ---------------------------------------------------------------------------

SUPPORTED_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
SUPPORTED_AUDIO_EXTENSIONS = (".wav", ".mp3", ".aiff", ".aac", ".ogg", ".flac")
SUPPORTED_VIDEO_EXTENSIONS = (".mp4", ".mpeg", ".mov", ".avi", ".flv", ".mpg", ".webm", ".wmv", ".3gpp")

SUPPORTED_IMAGE_MIMETYPES = ("image/png", "image/jpeg", "image/webp")
SUPPORTED_AUDIO_MIMETYPES = (
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/aiff",
    "audio/aac",
    "audio/ogg",
    "audio/flac",
)
SUPPORTED_VIDEO_MIMETYPES = (
    "video/mp4",
    "video/mpeg",
    "video/quicktime",
    "video/avi",
    "video/x-msvideo",
    "video/x-flv",
    "video/mpg",
    "video/webm",
    "video/wmv",
    "video/3gpp",
)


def get_file_type(filename: str, content_type: str = None) -> str:
    """
    根据文件名和 content_type 判断文件类型。
    与 Cuti-VideoAgent ``get_file_type`` 行为一致。
    """
    file_ext = pathlib.Path(filename).suffix.lower()
    content_type = content_type or ""

    if content_type.startswith("image/"):
        if file_ext in SUPPORTED_IMAGE_EXTENSIONS and content_type in SUPPORTED_IMAGE_MIMETYPES:
            return "image"
        return "unknown"
    if content_type.startswith("audio/"):
        if file_ext in SUPPORTED_AUDIO_EXTENSIONS and content_type in SUPPORTED_AUDIO_MIMETYPES:
            return "audio"
        return "unknown"
    if content_type.startswith("video/"):
        if file_ext in SUPPORTED_VIDEO_EXTENSIONS and content_type in SUPPORTED_VIDEO_MIMETYPES:
            return "video"
        return "unknown"
    if file_ext in SUPPORTED_IMAGE_EXTENSIONS:
        return "image"
    if file_ext in SUPPORTED_AUDIO_EXTENSIONS:
        return "audio"
    if file_ext in SUPPORTED_VIDEO_EXTENSIONS:
        return "video"
    return "unknown"


async def process_uploaded_files(files: List) -> tuple:
    """
    处理上传的文件，上传到 S3 并返回结构化对象列表（与 Cuti-VideoAgent 一致）。
    """
    from app.chat.models.video_state import AudioFileUserInput, ImageUserInput, VideoFileUserInput
    from app.chat.utils.s3_utils import s3_utils

    images = []
    audio_files = []
    video_files = []

    if not files or not files[0].filename:
        return images, audio_files, video_files

    try:
        for file in files:
            if not file.filename:
                continue

            content = await file.read()
            if not content or len(content) == 0:
                continue

            content_type = file.content_type or ""
            filename = file.filename
            file_type = get_file_type(filename, content_type)

            if file_type == "image":
                url = await s3_utils.upload_image(content, content_type=content_type)
                images.append(ImageUserInput(url=url, filename=filename))
                logger.info("Image uploaded: %s -> %s", filename, url)

            elif file_type == "audio":
                url = await s3_utils.upload_audio(content, filename=filename, content_type=content_type)
                audio_duration = await _maybe_audio_duration_from_msc(url)
                audio_files.append(AudioFileUserInput(url=url, filename=filename, duration=audio_duration))
                logger.info("Audio uploaded: %s -> %s duration=%s", filename, url, audio_duration)

            elif file_type == "video":
                url = await s3_utils.upload_video(content, filename=filename, content_type=content_type)
                video_files.append(VideoFileUserInput(url=url, filename=filename))
                logger.info("Video uploaded: %s -> %s", filename, url)

            else:
                logger.warning("Unsupported file: %s (content_type=%s)", filename, content_type)
                raise BusinessException(BusinessExceptionCode.UNSUPPORTED_FILE_FORMAT)

    except BusinessException:
        raise
    except Exception:
        raise BusinessException(BusinessExceptionCode.FILE_UPLOAD_FAILED)

    logger.info(
        "process_uploaded_files done: %s images, %s audio, %s video",
        len(images),
        len(audio_files),
        len(video_files),
    )
    return images, audio_files, video_files
