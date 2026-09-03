import os
import logging
import shutil
import tempfile
import uuid
import base64
import io
import boto3
import asyncio
import aiohttp
from typing import Optional, Tuple
from urllib.parse import urlparse


def _local_public_base() -> str:
    """本地存储对外访问基址（PUBLIC_BASE_URL，缺省 http://localhost:8000）。"""
    base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip()
    return (base or "http://localhost:8000").rstrip("/")


def _storage_is_local() -> bool:
    return (getattr(settings, "STORAGE_BACKEND", "s3") or "s3").lower() == "local"


def is_our_cdn_url(url: str) -> bool:
    """判断 URL 是否为「我们的」存储地址。
    - s3 后端：与 settings.CDN_DOMAIN 的 netloc 一致；
    - local 后端：额外识别 PUBLIC_BASE_URL 的 netloc（本地 /files 服务）。
    """
    if not url or not url.strip():
        return False
    try:
        netloc = urlparse(url.strip()).netloc
        if netloc == urlparse(settings.CDN_DOMAIN).netloc:
            return True
        if _storage_is_local() and netloc == urlparse(_local_public_base()).netloc:
            return True
        return False
    except Exception:
        return False
from PIL import Image
from app.config import settings
from app.crud.video.video_generation import update_video_generation_version_video_url
from app.exceptions import BusinessException, BusinessExceptionCode
from app.utils import media_service_client as msc

logger = logging.getLogger(__name__)

class S3Utils:
    """S3工具类，用于处理文件上传到S3"""
    
    def __init__(self):
        self._is_local = _storage_is_local()
        self.bucket_name = settings.S3_BUCKET_NAME
        if self._is_local:
            # 本地文件系统后端：无需 S3 客户端 / AWS 凭据
            self.s3_client = None
            self._local_dir = os.path.abspath(getattr(settings, "LOCAL_STORAGE_DIR", "./data/uploads"))
            os.makedirs(self._local_dir, exist_ok=True)
            # 对外 URL 前缀：{PUBLIC_BASE_URL}/files —— 与 main.py 的 /files 静态挂载一致
            self.cdn_domain = f"{_local_public_base()}/files"
            logger.info(f"🗂️ 存储后端=local，落地目录={self._local_dir}，URL 前缀={self.cdn_domain}")
        else:
            self.s3_client = self._build_s3_client()
            self.cdn_domain = settings.CDN_DOMAIN  # https://cdn-dev.newai.land

    @staticmethod
    def _build_s3_client():
        """构建 S3 客户端。默认走真实 AWS S3（IAM role）；
        当配置 S3_ENDPOINT_URL 时切到 S3 兼容存储（如 MinIO），使用 path-style 寻址。"""
        endpoint_url = getattr(settings, "S3_ENDPOINT_URL", None)
        if not endpoint_url:
            return boto3.client('s3', region_name=settings.AWS_REGION)
        from botocore.config import Config as _BotoConfig
        return boto3.client(
            's3',
            region_name=settings.AWS_REGION,
            endpoint_url=endpoint_url,
            aws_access_key_id=getattr(settings, "S3_ACCESS_KEY_ID", None),
            aws_secret_access_key=getattr(settings, "S3_SECRET_ACCESS_KEY", None),
            config=_BotoConfig(s3={"addressing_style": "path"}),
        )
        
    _IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"

    def _upload_file_sync(self, file_data: bytes, file_key: str, content_type: str = None) -> str:
        """同步上传，供 run_in_executor 调用。local 后端写本地磁盘，s3 后端 put_object。"""
        try:
            if self._is_local:
                dest = os.path.join(self._local_dir, file_key)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(file_data)
                url = f"{self.cdn_domain}/{file_key}"
                logger.info(f"Successfully saved file to local storage: {dest}, URL: {url}")
                return url
            extra_args = {"CacheControl": self._IMMUTABLE_CACHE_CONTROL}
            if content_type:
                extra_args['ContentType'] = content_type
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=file_key,
                Body=file_data,
                **extra_args
            )
            cdn_url = f"{self.cdn_domain}/{file_key}"
            logger.info(f"Successfully uploaded file to S3: {file_key}, CDN URL: {cdn_url}")
            return cdn_url
        except Exception as e:
            logger.error(f"Error uploading file to storage: {e}")
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                f"文件上传失败: {str(e)}"
            )

    def upload_file_sync(self, file_data: bytes, file_key: str, content_type: str = None) -> str:
        """同步上传（仅用于已在 run_in_executor 内的代码，如 openai_sora._download_and_save_video_sync）。"""
        return self._upload_file_sync(file_data, file_key, content_type)

    async def upload_file(self, file_data: bytes, file_key: str, content_type: str = None) -> str:
        """
        上传文件到 S3（异步，内部 run_in_executor，不阻塞事件循环）。调用方使用: await s3_utils.upload_file(...)
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._upload_file_sync(file_data, file_key, content_type),
        )

    def _upload_image_sync(self, image_data: bytes, generation_id: Optional[str] = None,
                           content_type: str = "image/webp", resize_to: Optional[Tuple[int, int]] = None) -> str:
        """同步处理图片并上传，供 run_in_executor 调用（含 PIL 解码）。"""
        try:
            image_bytes = image_data
            with Image.open(io.BytesIO(image_bytes)) as img:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                if resize_to:
                    target_width, target_height = resize_to
                    if img.width != target_width or img.height != target_height:
                        logger.info(f"调整图片尺寸: {img.width}x{img.height} -> {target_width}x{target_height}")
                        img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                img.save(output, 'webp', quality=85)
                processed_image_bytes = output.getvalue()
            filename = f"{generation_id}.webp" if generation_id else f"{uuid.uuid4()}.webp"
            file_key = f"images/{filename}"
            return self._upload_file_sync(processed_image_bytes, file_key, content_type)
        except Exception as e:
            logger.error(f"Error processing and uploading image: {e}")
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                f"图片处理或上传失败: {str(e)}"
            )

    async def upload_image(self, image_data: bytes, generation_id: Optional[str] = None,
                           content_type: str = "image/webp", resize_to: Optional[Tuple[int, int]] = None) -> str:
        """
        上传图片到 S3（异步，内部 run_in_executor 含 PIL，不阻塞事件循环）。调用方使用: await s3_utils.upload_image(...)
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._upload_image_sync(image_data, generation_id, content_type, resize_to),
        )

    def _upload_audio_sync(self, audio_data: bytes, filename: str = None,
                           generation_id: Optional[str] = None, content_type: str = "audio/mpeg") -> str:
        """同步上传音频，供 run_in_executor 调用。"""
        if filename:
            _, ext = os.path.splitext(filename)
            ext = ext or '.mp3'
        else:
            ext = '.mp3'
        name = f"{generation_id}{ext}" if generation_id else f"{uuid.uuid4()}{ext}"
        file_key = f"audios/{name}"
        return self._upload_file_sync(audio_data, file_key, content_type)

    async def upload_audio(self, audio_data: bytes, filename: str = None,
                           generation_id: Optional[str] = None, content_type: str = "audio/mpeg") -> str:
        """
        上传音频到 S3（异步，内部 run_in_executor）。调用方使用: await s3_utils.upload_audio(...)
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._upload_audio_sync(audio_data, filename, generation_id, content_type),
        )

    def _upload_video_sync(self, video_data: bytes, filename: str = None,
                           generation_id: Optional[str] = None, content_type: str = "video/mp4") -> str:
        """同步上传视频，供 run_in_executor 调用。"""
        if filename:
            _, ext = os.path.splitext(filename)
            ext = ext or '.mp4'
        else:
            ext = '.mp4'
        name = f"{generation_id}{ext}" if generation_id else f"{uuid.uuid4()}{ext}"
        file_key = f"videos/{name}"
        return self._upload_file_sync(video_data, file_key, content_type)

    async def upload_video(self, video_data: bytes, filename: str = None,
                           generation_id: Optional[str] = None, content_type: str = "video/mp4") -> str:
        """
        上传视频到 S3（异步，内部 run_in_executor）。调用方使用: await s3_utils.upload_video(...)
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._upload_video_sync(video_data, filename, generation_id, content_type),
        )
    
    def _download_file_sync(self, file_key_or_url: str, local_path: str) -> bool:
        """同步下载实现，供 download_file 在 executor 中调用，不阻塞事件循环。"""
        try:
            parsed_input = urlparse(file_key_or_url)
            if self._is_local:
                # local 后端：仅支持「我们自己的」URL/键；外链由调用方 aiohttp 处理
                if parsed_input.scheme in ('http', 'https'):
                    file_key = self.cdn_url_to_s3_key(file_key_or_url)
                    if not file_key:
                        logger.error(f"无法从URL获取本地存储键: {file_key_or_url}")
                        return False
                else:
                    file_key = file_key_or_url
                src = os.path.join(self._local_dir, file_key)
                if not os.path.exists(src):
                    logger.error(f"本地存储文件不存在: {src}")
                    return False
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                shutil.copyfile(src, local_path)
                logger.info(f"Successfully copied file from local storage: {file_key} -> {local_path}")
                return True

            if parsed_input.scheme in ('http', 'https'):
                expected_domain = urlparse(settings.CDN_DOMAIN).netloc
                if parsed_input.netloc != expected_domain:
                    logger.error(f"Invalid CDN domain: {parsed_input.netloc}, expected: {expected_domain}")
                    return False
                file_key = self.cdn_url_to_s3_key(file_key_or_url)
                if not file_key:
                    logger.error(f"无法从URL获取S3键: {file_key_or_url}")
                    return False
            else:
                file_key = file_key_or_url

            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            self.s3_client.download_file(
                Bucket=self.bucket_name,
                Key=file_key,
                Filename=local_path
            )
            logger.info(f"Successfully downloaded file from S3: {file_key} -> {local_path}")
            return True
        except Exception as e:
            logger.error(f"Error downloading file from storage: {e}")
            return False

    async def download_file(self, file_key_or_url: str, local_path: str) -> bool:
        """
        从S3下载文件到本地路径（异步，内部用 run_in_executor 执行 boto3，不阻塞事件循环）

        调用方统一使用: await s3_utils.download_file(...)

        Args:
            file_key_or_url: S3文件键或CDN URL
            local_path: 本地保存路径

        Returns:
            bool: 下载是否成功
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._download_file_sync(file_key_or_url, local_path),
        )

    def cdn_url_to_s3_key(self, cdn_url: str) -> Optional[str]:
        """
        将CDN URL转换为S3文件键
        
        Args:
            cdn_url: CDN URL，如 https://cdn-dev.newai.land/images/uuid.webp
            
        Returns:
            Optional[str]: S3文件键，如 images/uuid.webp，失败返回None
        """
        try:
            if not cdn_url:
                logger.error("Empty CDN URL")
                return None
            
            # 使用urllib.parse专业解析URL
            parsed = urlparse(cdn_url)

            if self._is_local:
                # local 后端：URL 形如 {PUBLIC_BASE_URL}/files/<key>；剥掉 files/ 前缀得到 key
                expected_domain = urlparse(_local_public_base()).netloc
                if parsed.netloc != expected_domain:
                    logger.error(f"Invalid local storage domain: {parsed.netloc}, expected: {expected_domain}")
                    return None
                file_key = parsed.path.lstrip('/')
                if file_key.startswith('files/'):
                    file_key = file_key[len('files/'):]
                logger.info(f"Converted local URL to storage key: {cdn_url} -> {file_key}")
                return file_key

            # 检查域名是否匹配
            expected_domain = urlparse(settings.CDN_DOMAIN).netloc
            if parsed.netloc != expected_domain:
                logger.error(f"Invalid CDN domain: {parsed.netloc}, expected: {expected_domain}")
                return None
            
            # 提取路径部分作为S3文件键
            file_key = parsed.path.lstrip('/')
            
            logger.info(f"Converted CDN URL to S3 key: {cdn_url} -> {file_key}")
            return file_key
            
        except Exception as e:
            logger.error(f"Error converting CDN URL to S3 key: {e}")
            return None
    
    async def download_and_upload_video_to_s3(
        self,
        video_url: str,
        generation_id: Optional[str] = None,
        max_retries: int = 3,
        target_width: Optional[int] = None,
        target_height: Optional[int] = None,
        target_fps: Optional[int] = None,
        target_duration: Optional[float] = None,
        strip_audio: bool = False,
        watermark: bool = False,
    ) -> str:
        """
        从URL下载视频并上传到S3（带重试机制）。
        Media Service 一次 FFmpeg 完成: resize + fps + strip audio + trim + watermark。
        """
        # The extracted self-hosted Video Runtime deliberately does not require
        # Cuti Media Service. Provider outputs already use the requested
        # resolution/duration, so local storage can preserve the immutable MP4
        # directly; later Media Plugin steps still perform concat/export checks.
        if self._is_local:
            timeout = aiohttp.ClientTimeout(total=1200.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(video_url) as response:
                    response.raise_for_status()
                    video_data = await response.read()
            if len(video_data) < 100:
                raise BusinessException(
                    BusinessExceptionCode.BUSINESS_ERROR,
                    "下载视频失败：Provider 返回空文件",
                )
            filename = os.path.basename(urlparse(video_url).path) or "provider-output.mp4"
            return await self.upload_video(
                video_data,
                filename=filename,
                generation_id=generation_id,
            )
        result = await msc.pipeline_ensure_on_s3(
            video_url=video_url,
            run_id=generation_id or str(uuid.uuid4()),
            generation_id=generation_id,
            target_width=target_width,
            target_height=target_height,
            target_fps=target_fps,
            target_duration=target_duration,
            strip_audio=strip_audio,
            watermark=watermark,
        )
        cdn_url = result["result_url"]
        logger.info(f"✅ Media service pipeline_ensure_on_s3 succeeded: {cdn_url}")
        return cdn_url

    async def ensure_video_on_our_s3(
        self,
        video_url: str,
        generation_id: Optional[str] = None,
        target_width: Optional[int] = None,
        target_height: Optional[int] = None,
        *,
        target_fps: int = 24,
        target_duration: Optional[float] = None,
        strip_audio: bool = True,
        watermark: bool = True,
        update_version_uuid: Optional[str] = None,
    ) -> str:
        """统一入口：若已是本 CDN 直接返回；否则下载→单次 FFmpeg 归一化
        (resize+fps+strip audio+trim+watermark)→上传。
        默认统一到 24fps、去除音轨、叠加水印，确保后续 -c copy concat 安全。"""
        if not video_url or not video_url.strip():
            return video_url or ""
        if is_our_cdn_url(video_url):
            return video_url
        tw = target_width if target_width is not None and target_width > 0 else 1920
        th = target_height if target_height is not None and target_height > 0 else 1080
        new_cdn_url = await self.download_and_upload_video_to_s3(
            video_url,
            generation_id=generation_id,
            target_width=tw,
            target_height=th,
            target_fps=target_fps,
            target_duration=target_duration,
            strip_audio=strip_audio,
            watermark=watermark,
        )
        if update_version_uuid:
            await update_video_generation_version_video_url(update_version_uuid, new_cdn_url)
        return new_cdn_url

    async def download_and_upload_audio_to_s3(
        self, 
        audio_url: str, 
        generation_id: Optional[str] = None,
        max_retries: int = 3
    ) -> str:
        """
        从URL下载音频并上传到S3（带重试机制）
        
        Args:
            audio_url: 音频文件URL
            generation_id: 生成ID，如果为None则使用UUID
            max_retries: 最大重试次数
            
        Returns:
            str: S3 CDN URL
        """
        # 确定文件名
        if generation_id:
            base_filename = generation_id
        else:
            base_filename = str(uuid.uuid4())
        
        # 从URL获取文件扩展名，默认为mp3
        file_extension = ".mp3"
        if audio_url:
            try:
                parsed = urlparse(audio_url)
                path = parsed.path.lower()
                if path.endswith(('.mp3', '.wav', '.ogg', '.aac', '.flac', '.m4a')):
                    file_extension = path[path.rfind('.'):]
            except:
                pass
        
        # 重试下载
        audio_data = None
        for attempt in range(max_retries):
            try:
                logger.info(f"📥 开始下载音频 (尝试 {attempt + 1}/{max_retries}): {audio_url}")
                
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300.0)) as session:
                    async with session.get(audio_url) as response:
                        response.raise_for_status()
                        
                        # 检查内容长度
                        content_length = response.headers.get('content-length')
                        if content_length and int(content_length) < 100:  # 少于100B可能是错误响应
                            raise Exception(f"响应内容过小: {content_length} bytes，可能是无效音频")
                        
                        # 读取音频数据
                        audio_data = await response.read()
                        
                        # 验证数据
                        if not audio_data or len(audio_data) < 100:
                            raise Exception(f"下载数据过小: {len(audio_data)} bytes，可能是无效音频")
                
                logger.info(f"📥 音频文件下载成功: {audio_url} ({len(audio_data)} bytes)")
                break
                        
            except Exception as e:
                error_msg = f"下载尝试 {attempt + 1} 失败: {e}"
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2  # 递增等待时间: 2s, 4s, 6s
                    logger.warning(f"❌ {error_msg}，{wait_time}秒后重试...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"❌ 音频下载完全失败: {audio_url}, 最终错误: {e}")
                    raise BusinessException(
                        BusinessExceptionCode.BUSINESS_ERROR,
                        f"下载音频失败（重试{max_retries}次）: {e}"
                    )
        
        # 上传到S3
        return await self.upload_audio(audio_data, generation_id=base_filename)

async def convert_media_url_to_s3(media_url: str, media_type, target_format) -> Optional[str]:
    """
    通用媒体URL转换方法 - 使用S3处理
    
    Args:
        media_url: 输入的媒体URL（完整URL）
        media_type: 媒体类型（MediaType.IMAGE/AUDIO/VIDEO）
        target_format: 目标格式（MediaFormat.URL/BASE64/LOCAL_PATH）
        
    Returns:
        转换后的媒体数据，失败返回None
    """
    if not media_url:
        return None
        
    try:
        from .file_utils import MediaType, MediaFormat, convert_image_url_to_base64, get_media_dir
        import os
        import uuid
        
        # 已经是完整URL或其他格式（不支持 /api/photos, /api/audios, /api/videos）
        full_url = media_url
        
        # 根据目标格式进行转换
        if target_format == MediaFormat.URL:
            return full_url
            
        elif target_format == MediaFormat.LOCAL_PATH:
            # 生成本地文件名
            filename = f"{uuid.uuid4().hex[:8]}_{os.path.basename(full_url)}"
            
            # 确定本地目录
            local_dir = get_media_dir(media_type)
            os.makedirs(local_dir, exist_ok=True)
            local_path = os.path.join(local_dir, filename)
            
            # 使用S3Utils异步下载文件（支持所有URL类型，不阻塞事件循环）
            success = await s3_utils.download_file(full_url, local_path)
            
            if success and os.path.exists(local_path):
                logger.info(f"✅ 文件下载成功: {full_url} -> {local_path}")
                return local_path
            else:
                logger.error(f"❌ 文件下载失败: {full_url}")
                return None
            
        elif target_format == MediaFormat.BASE64:
            # 目前只支持图像的base64转换
            if media_type == MediaType.IMAGE:
                return await convert_image_url_to_base64(media_url)
            else:
                logger.warning(f"不支持 {media_type} 的 base64 转换")
                return None
            
        else:
            logger.error(f"不支持的目标格式: {target_format}")
            return None
            
    except Exception as e:
        logger.error(f"媒体URL转换失败: {media_url} -> {target_format}, 错误: {e}")
        return None

# 创建全局实例
s3_utils = S3Utils()
