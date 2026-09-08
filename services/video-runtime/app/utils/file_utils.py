"""
文件工具函数
包含图片、音频、视频的保存、下载、转换等功能

功能模块：
1. 图片处理: 保存、格式转换、WebP压缩
2. 音频处理: 保存、格式转换、混合
3. 视频处理: 下载、音频混合、格式转换
4. 文件上传: 处理 FastAPI UploadFile，上传到 S3

使用场景：
- Tools 保存生成的媒体文件
- Agent 处理用户上传的文件
- 视频合成过程中的媒体处理
"""
import os
import logging
import asyncio
import base64
import uuid
import pathlib
from typing import Dict, Any, Optional
from enum import Enum
from app.config import settings
from app.exceptions import BusinessException, BusinessExceptionCode
from PIL import Image
import io
import aiohttp
import aiofiles
from . import media_service_client as msc


class ImageFormat(str, Enum):
    """图像格式枚举"""
    URL = "url"              # 带domain的完整URL
    BASE64 = "base64"        # base64编码数据
    LOCAL_PATH = "local_path" # 本地文件路径


class MediaType(str, Enum):
    """媒体类型枚举"""
    IMAGE = "image"
    AUDIO = "audio" 
    VIDEO = "video"


class MediaFormat(str, Enum):
    """媒体格式枚举"""
    URL = "url"              # 带domain的完整URL
    BASE64 = "base64"        # base64编码数据
    LOCAL_PATH = "local_path" # 本地文件路径

logger = logging.getLogger(__name__)

def convert_image_url_to_local_path(image_url: str) -> str:
    """
    将 image_url 转换为本地文件系统路径
    支持：/api/photos/xxx.png, api/photos/xxx.png, https://domain.com/api/photos/xxx.png
    """
    # 如果是完整URL，提取路径部分
    if image_url.startswith(('http://', 'https://')):
        from urllib.parse import urlparse
        parsed = urlparse(image_url)
        path_part = parsed.path.lstrip('/')
    else:
        # 处理以 / 开头或不以 / 开头的情况
        path_part = image_url.lstrip('/')
    
    if path_part.startswith("api/photos/"):
        filename = path_part.replace("api/photos/", "")
        return os.path.join(settings.STATIC_PHOTOS_DIR, filename)
    
    # 如果已经是本地路径，直接返回
    if os.path.isabs(image_url) and os.path.exists(image_url):
        return image_url
    
    # 如果是相对路径，尝试在static目录中查找
    if not image_url.startswith(('http://', 'https://')):
        potential_path = os.path.join(settings.STATIC_PHOTOS_DIR, image_url)
        if os.path.exists(potential_path):
            return potential_path
    
    return image_url


async def convert_image_url(image_url: str, target_format: ImageFormat) -> Optional[str]:
    """
    通用图像URL转换方法
    
    Args:
        image_url: 输入的图像URL（支持 /api/photos 相对路径和完整URL）
        target_format: 目标格式（URL/BASE64/LOCAL_PATH）
        
    Returns:
        转换后的图像数据，失败返回None
    """
    if not image_url:
        return None
        
    try:
        # 如果是 /api/photos 开头的相对路径，先转换为完整URL
        if image_url.startswith("/api/photos/") or image_url.startswith("api/photos/"):
            # 构建完整URL
            if settings.BASE_URL and settings.BASE_URL != "http://localhost:8000":
                base_url = settings.BASE_URL.rstrip('/')
                if image_url.startswith("/"):
                    full_url = f"{base_url}{image_url}"
                else:
                    full_url = f"{base_url}/{image_url}"
            else:
                # 开发环境，使用相对路径构建完整URL
                if image_url.startswith("/"):
                    full_url = f"http://localhost:8000{image_url}"
                else:
                    full_url = f"http://localhost:8000/{image_url}"
        else:
            # 已经是完整URL或其他格式
            full_url = image_url
        
        # 根据目标格式进行转换
        if target_format == ImageFormat.URL:
            return full_url
            
        elif target_format == ImageFormat.LOCAL_PATH:
            return convert_image_url_to_local_path(image_url)
            
        elif target_format == ImageFormat.BASE64:
            return await convert_image_url_to_base64(image_url)
            
        else:
            logger.error(f"不支持的目标格式: {target_format}")
            return None
            
    except Exception as e:
        logger.error(f"图像URL转换失败: {image_url} -> {target_format}, 错误: {e}")
        return None


_IMAGE_MIME_BY_EXT = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


async def inline_local_image_url_for_llm(image_url: str) -> str:
    """
    将本地存储的图片 URL 转为 data URI，避免 LLM SDK 同步回拉 http://localhost:8000/files/...

    本地 STORAGE_BACKEND=local 时，Gemini/LangChain 会用 urllib3 同步下载 message 里的 image_url；
    若该 URL 指向本进程正在占用的 uvicorn，会堵死事件循环（自调用死锁）。读磁盘内联可绕开 HTTP。
    非本地 URL / 已是 data: / 读盘失败时原样返回。
    """
    if not image_url or not isinstance(image_url, str):
        return image_url
    url = image_url.strip()
    if not url or url.startswith("data:"):
        return image_url
    try:
        from app.utils.s3_utils import s3_utils, is_our_cdn_url, _storage_is_local

        if not _storage_is_local():
            return image_url
        if not is_our_cdn_url(url):
            return image_url
        file_key = s3_utils.cdn_url_to_s3_key(url)
        if not file_key:
            return image_url
        local_path = os.path.join(s3_utils._local_dir, file_key)
        if not os.path.isfile(local_path):
            logger.warning("inline_local_image_url_for_llm: file missing %s", local_path)
            return image_url
        raw = await asyncio.to_thread(pathlib.Path(local_path).read_bytes)
        ext = pathlib.Path(local_path).suffix.lower()
        mime = _IMAGE_MIME_BY_EXT.get(ext, "image/webp")
        b64 = base64.b64encode(raw).decode("ascii")
        logger.info(
            "inline_local_image_url_for_llm: inlined %s (%d bytes, %s)",
            file_key,
            len(raw),
            mime,
        )
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        logger.warning("inline_local_image_url_for_llm failed url=%s err=%s", image_url[:120], e)
        return image_url


async def convert_image_url_to_base64(image_url: str) -> Optional[str]:
    """
    将图像URL转换为base64编码
    
    Args:
        image_url: 图像URL（支持相对路径和绝对路径）
        
    Returns:
        base64编码的图像数据，失败返回None
    """
    import aiofiles
    
    try:
        # 转换URL到本地路径
        local_path = convert_image_url_to_local_path(image_url)
        if not local_path or not os.path.exists(local_path):
            logger.warning(f"⚠️ 无法转换图像URL到本地路径或文件不存在: {image_url}")
            return None
            
        # 使用 aiofiles 进行异步文件读取
        async with aiofiles.open(local_path, 'rb') as img_file:
            image_data = await img_file.read()
            base64_data = await asyncio.to_thread(base64.b64encode, image_data)
            base64_str = base64_data.decode('utf-8')
            logger.debug(f"✅ 图像转换为base64成功: {image_url}")
            return base64_str
            
    except Exception as e:
        logger.error(f"❌ 图像转换base64失败 {image_url}: {e}")
        return None


def get_media_dir(media_type: MediaType) -> str:
    """获取媒体目录路径"""
    if media_type == MediaType.IMAGE:
        return settings.STATIC_PHOTOS_DIR
    elif media_type == MediaType.AUDIO:
        return os.path.join(settings.STATIC_PHOTOS_DIR, "../audios")
    elif media_type == MediaType.VIDEO:
        return os.path.join(settings.STATIC_PHOTOS_DIR, "../videos")
    else:
        raise ValueError(f"不支持的媒体类型: {media_type}")


def _safe_unlink(*paths: str):
    """安全删除本地文件（处理完上传S3后清理，防止存储泄漏）"""
    for p in paths:
        if p and os.path.isfile(p):
            try:
                os.unlink(p)
            except OSError:
                pass


async def extract_audio_from_video(
    video_url: str,
    output_filename: Optional[str] = None
) -> Optional[str]:
    """
    从视频文件中提取音频，上传到 S3，返回 CDN URL。
    使用临时目录，处理完自动清理，不再写入持久化本地目录。
    
    Args:
        video_url: 视频文件URL（S3 CDN URL 或 /api/videos/ 格式）
        output_filename: 输出文件名（不含扩展名），如果为None则使用UUID
        
    Returns:
        提取的音频 S3 CDN URL；视频无音轨时 MSC 返回 result_url=null，此处为 None
    """
    result = await msc.audio_extract(video_url, run_id=uuid.uuid4().hex[:12])
    return result.get("result_url")


SUPPORTED_VIDEO_EXTENSIONS = ('.mp4', '.mpeg', '.mov', '.avi', '.flv', '.mpg', '.webm', '.wmv', '.3gpp')
SUPPORTED_VIDEO_MIMETYPES = ('video/mp4', 'video/mpeg', 'video/quicktime', 'video/avi', 'video/x-msvideo', 'video/x-flv', 'video/mpg', 'video/webm', 'video/wmv', 'video/3gpp')


class VideoContentForLLM:
    """视频内容用于 LLM 的结构化数据"""
    def __init__(self, use_file_uri: bool, file_uri: Optional[str] = None, base64_data: Optional[str] = None, mime_type: str = "video/mp4"):
        self.use_file_uri = use_file_uri
        self.file_uri = file_uri
        self.base64_data = base64_data
        self.mime_type = mime_type
    
    def to_media_content(self) -> Dict[str, Any]:
        """转换为 LLM 消息中的 media content 格式"""
        if self.use_file_uri and self.file_uri:
            return {
                "type": "media",
                "file_uri": self.file_uri,
                "mime_type": self.mime_type
            }
        elif self.base64_data:
            return {
                "type": "media",
                "data": self.base64_data,
                "mime_type": self.mime_type
            }
        else:
            raise ValueError("VideoContentForLLM: 既没有 file_uri 也没有 base64_data")


async def prepare_video_for_llm(
    video_path: Optional[str] = None,
    video_url: Optional[str] = None,
    mime_type: Optional[str] = None,
    max_wait_time: int = 180,
    max_base64_size_mb: float = 20.0
) -> VideoContentForLLM:
    """
    准备视频内容用于 LLM（优先使用 file_uri，降级使用 base64）
    
    功能：
    - 如果提供了 video_path，直接使用
    - 如果提供了 video_url，先下载到临时文件
    - 尝试上传到 Google 获取 file_uri（绕过 LangSmith 大小限制）
    - 如果上传失败，降级使用 base64（但会检查大小限制）
    
    Args:
        video_path: 本地视频文件路径
        video_url: 视频 URL（需要先下载）
        mime_type: 视频 MIME 类型，如果不提供会根据文件扩展名推断
        max_wait_time: 上传到 Google 的最大等待时间（秒），默认 300 秒（5分钟）
        max_base64_size_mb: base64 降级时的最大文件大小（MB），默认 20MB（base64 后约 26MB）
    
    Returns:
        VideoContentForLLM: 包含 file_uri 或 base64_data 的对象
    
    Raises:
        BusinessException: 如果视频文件过大或处理失败
    """
    import tempfile
    import os
    from pathlib import Path
    from app.utils.s3_utils import s3_utils
    from app.utils.google_file_upload import upload_video_to_google
    from app.exceptions import BusinessException, BusinessExceptionCode
    
    temp_file_path = None
    need_cleanup = False
    
    try:
        # 1. 确定视频文件路径
        if video_path:
            if not Path(video_path).exists():
                raise BusinessException(
                    BusinessExceptionCode.FILE_UPLOAD_FAILED,
                    f"视频文件不存在: {video_path}"
                )
            actual_video_path = video_path
            logger.info(f"📁 处理本地视频文件: {video_path}")
        elif video_url:
            # 下载视频到临时文件
            video_ext = Path(video_url).suffix.lower() or '.mp4'
            temp_file = tempfile.NamedTemporaryFile(suffix=video_ext, delete=False)
            temp_file_path = temp_file.name
            temp_file.close()
            need_cleanup = True
            
            success = await s3_utils.download_file(video_url, temp_file_path)
            if not success:
                raise BusinessException(
                    BusinessExceptionCode.FILE_UPLOAD_FAILED,
                    f"无法下载视频文件: {video_url}"
                )
            actual_video_path = temp_file_path
            logger.info(f"📹 处理视频URL: {video_url}, 下载到: {temp_file_path}")
        else:
            raise ValueError("必须提供 video_path 或 video_url 之一")
        
        # 2. 确定 MIME 类型
        if not mime_type:
            video_ext = Path(actual_video_path).suffix.lower()
            ext_to_mime_map = dict(zip(SUPPORTED_VIDEO_EXTENSIONS, SUPPORTED_VIDEO_MIMETYPES))
            mime_type = ext_to_mime_map.get(video_ext, 'video/mp4')
        
        # 3. 检查文件大小
        file_size_mb = os.path.getsize(actual_video_path) / (1024 * 1024)
        logger.info(f"📊 视频文件大小: {file_size_mb:.2f} MB")
        
        # 4. 尝试上传到 Google 获取 file_uri（绕过 LangSmith 大小限制）
        file_uri, google_mime = await upload_video_to_google(actual_video_path, max_wait_time=max_wait_time)
        
        if file_uri:
            # ⚠️ 使用 file_uri 时，mime_type 必须用 Google Files API 识别的实际类型，
            # 否则 Gemini 返回 400 Invalid argument（音频已验证此问题）
            effective_mime = google_mime or mime_type
            logger.info(f"✅ 视频已上传到 Google，使用 file_uri: {file_uri}, mime_type: {effective_mime}")
            return VideoContentForLLM(
                use_file_uri=True,
                file_uri=file_uri,
                mime_type=effective_mime
            )
        else:
            # 5. 降级：使用 base64（如果文件不太大）
            logger.warning(f"⚠️ 上传到 Google 失败，降级使用 base64")
            if file_size_mb > max_base64_size_mb:
                raise BusinessException(
                    BusinessExceptionCode.FILE_UPLOAD_FAILED,
                    f"视频文件过大（{file_size_mb:.2f}MB），无法处理（超过 {max_base64_size_mb}MB 限制）"
                )
            
            async with aiofiles.open(actual_video_path, 'rb') as f:
                video_bytes = await f.read()
            base64_raw = await asyncio.to_thread(base64.b64encode, video_bytes)
            base64_data = base64_raw.decode("utf-8")
            
            logger.info(f"✅ 使用 base64 编码（大小: {file_size_mb:.2f}MB）")
            return VideoContentForLLM(
                use_file_uri=False,
                base64_data=base64_data,
                mime_type=mime_type
            )
    
    finally:
        # 清理临时文件
        if need_cleanup and temp_file_path:
            try:
                os.unlink(temp_file_path)
            except:
                pass

