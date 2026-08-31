"""
OpenAI Sora 2 视频生成服务
"""
import os
import logging
import asyncio
import mimetypes
import tempfile
from pathlib import Path
from typing import Optional
from PIL import Image
from openai import AsyncOpenAI
from tenacity import (
    retry, stop_after_delay, wait_fixed,
    retry_if_exception_type
)

from ..config import get_settings
from ..models.image_result import VideoGenerationResult, VideoProvider
from ..utils.s3_utils import s3_utils
from ..services.agent.utils.cancellation import raise_if_cancelled

logger = logging.getLogger(__name__)


class OpenAISoraRetryException(Exception):
    """OpenAI Sora API 可重试异常"""
    pass


class OpenAISoraServerErrorException(OpenAISoraRetryException):
    """OpenAI Sora API 服务器错误异常"""
    pass


class OpenAISoraFinalException(Exception):
    """OpenAI Sora API 最终失败异常 - 不需要重试"""
    pass


class OpenAISoraService:
    """OpenAI Sora 2 视频生成服务"""
    
    def __init__(self):
        self.settings = get_settings()
        self.api_key = os.getenv("OPENAI_API_KEY")
        
        if not self.api_key:
            logger.warning("⚠️ OPENAI_API_KEY 未设置")
            self.client = None
        else:
            self.client = AsyncOpenAI(api_key=self.api_key)

    def _prepare_input_image_sync(self, local_path: str, target_size: str) -> tuple:
        """同步：PIL 解码 + resize + 读文件，供 run_in_executor 调用。返回 (filename, image_bytes, mimetype, temp_file_path_or_none)。"""
        target_width, target_height = map(int, target_size.split('x'))
        temp_file_path = None
        with Image.open(local_path) as img:
            current_size = img.size
            logger.info(f"📐 当前图像尺寸: {current_size[0]}x{current_size[1]}")
            if current_size != (target_width, target_height):
                logger.info(f"🔄 调整图像尺寸: {current_size[0]}x{current_size[1]} -> {target_width}x{target_height}")
                if img.mode in ('RGBA', 'LA', 'P'):
                    rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    if img.mode == 'RGBA':
                        rgb_img.paste(img, mask=img.split()[-1])
                    else:
                        rgb_img.paste(img)
                    img = rgb_img
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
                resized_img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
                tf = tempfile.NamedTemporaryFile(delete=False, suffix='.png', prefix='sora_resized_')
                temp_file_path = tf.name
                tf.close()
                resized_img.save(temp_file_path, 'PNG', quality=95)
                logger.info(f"✅ 图像已调整并保存到: {temp_file_path}")
                with open(temp_file_path, "rb") as f:
                    image_bytes = f.read()
                return (f"resized_{target_size}.png", image_bytes, 'image/png', temp_file_path)
            logger.info(f"✅ 图像尺寸已匹配，无需调整")
            with open(local_path, "rb") as f:
                image_bytes = f.read()
            mimetype, _ = mimetypes.guess_type(local_path)
            if not mimetype:
                ext = os.path.splitext(local_path)[1].lower()
                mimetype = {'.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}.get(ext, 'application/octet-stream')
            return (os.path.basename(local_path), image_bytes, mimetype, None)

    async def _prepare_input_image(self, image_url: str, target_size: str) -> tuple:
        """
        准备输入图像，自动调整到目标尺寸并返回图像字节和 MIME 类型
        参考 generate_image_edit 的实现
        
        Args:
            image_url: 图像URL (CDN URL)
            target_size: 目标尺寸，格式如 "1280x720"
            
        Returns:
            (filename, image_bytes, mimetype) 元组
        """
        logger.info(f"📥 准备输入图像: {image_url}")
        logger.info(f"🎯 目标尺寸: {target_size}")
        
        temp_file_path = None
        temp_download_path = None
        
        try:
            # 🎯 从 S3/CDN 下载图像到临时文件
            temp_download_file = await asyncio.to_thread(
                tempfile.NamedTemporaryFile,
                delete=False,
                suffix='.png',
                prefix='sora_input_',
            )
            temp_download_path = temp_download_file.name
            local_path = temp_download_path
            temp_download_file.close()
            
            # 使用 S3Utils 异步下载文件（不阻塞事件循环）
            success = await s3_utils.download_file(image_url, local_path)
            if not success or not os.path.exists(local_path):
                raise OpenAISoraFinalException(f"从 S3/CDN 下载图像失败: {image_url}")
            
            logger.info(f"📁 图像已下载到本地: {local_path}")

            # PIL 解码 + resize + 读文件 放入线程池，避免阻塞事件循环
            filename, image_bytes, mimetype, temp_file_path_out = await asyncio.to_thread(
                self._prepare_input_image_sync, local_path, target_size,
            )
            if temp_file_path_out:
                temp_file_path = temp_file_path_out

            logger.info(f"✅ 图像已准备，MIME类型: {mimetype}, 大小: {len(image_bytes)} bytes")
            
            # 返回 OpenAI SDK 兼容的元组格式 (filename, content, mimetype)
            return (filename, image_bytes, mimetype)
                
        except Exception as e:
            logger.error(f"❌ 准备输入图像失败: {e}")
            raise OpenAISoraFinalException(f"准备输入图像失败: {str(e)}")
        finally:
            # 清理所有临时文件
            for path in [temp_file_path, temp_download_path]:
                if path and os.path.exists(path):
                    try:
                        await asyncio.to_thread(os.unlink, path)
                        logger.info(f"🗑️ 已清理临时文件: {path}")
                    except Exception as e:
                        logger.warning(f"⚠️ 清理临时文件失败: {e}")
    
    async def generate_video(
        self,
        prompt: str,
        input_image: Optional[str] = None,
        model: str = "sora-2",
        size: str = "1280x720",
        seconds: str = "4",
        target_width: Optional[int] = None,
        target_height: Optional[int] = None,
    ) -> VideoGenerationResult:
        """
        生成视频 - 包含创建和轮询
        
        Args:
            prompt: 视频描述提示词
            input_image: 输入图像URL（可选），用于I2V模式
            model: 模型名称，默认 "sora-2"
            size: 视频分辨率（宽x高），默认 "1280x720"
            seconds: 视频时长（秒），默认 "4"，支持 '4', '8', '12'
            
        Returns:
            VideoGenerationResult对象
        """
        try:
            # 创建视频生成任务
            video_id = await self.create_video_task(prompt, input_image, model, size, seconds)
            
            # 轮询直到完成
            return await self.poll_video_task_until_complete(
                video_id=video_id,
                original_prompt=prompt,
                size=size,
                seconds=seconds,
                target_width=target_width,
                target_height=target_height,
            )
            
        except OpenAISoraFinalException as e:
            logger.error(f"🎬 OpenAI Sora 视频生成最终失败: {e}")
            return VideoGenerationResult.error_result(
                error_message=f"OpenAI Sora 视频生成失败: {str(e)}",
                provider=VideoProvider.OPENAI_SORA
            )
        except Exception as e:
            logger.error(f"🎬 OpenAI Sora 视频生成异常: {e}")
            return VideoGenerationResult.error_result(
                error_message=f"OpenAI Sora 视频生成异常: {str(e)}",
                provider=VideoProvider.OPENAI_SORA
            )
    
    async def create_video_task(
        self,
        prompt: str,
        input_image: Optional[str] = None,
        model: str = "sora-2",
        size: str = "1280x720",
        seconds: str = "4"
    ) -> str:
        """
        创建视频生成任务
        
        Args:
            prompt: 视频描述提示词（必需）
            input_image: 输入图像URL（可选），用于I2V模式
            model: 模型名称（可选，默认 sora-2）
            size: 视频分辨率（可选，默认 1280x720）
            seconds: 视频时长（可选，默认 4 秒），支持 '4', '8', '12'
            
        Returns:
            视频任务ID
        """
        if not self.client:
            raise OpenAISoraFinalException("OpenAI API key 未配置")
        
        mode = "I2V" if input_image else "T2V"
        logger.info(f"🎬 创建 OpenAI Sora 视频任务（{mode}模式）")
        logger.info(f"🎬 模型: {model}, 分辨率: {size}, 时长: {seconds}秒")
        logger.info(f"🎬 提示词: {prompt[:100]}...")
        if input_image:
            logger.info(f"🎬 输入图像: {input_image[:50]}...")
        
        try:
            # 使用 OpenAI SDK 创建视频
            create_params = {
                "prompt": prompt,
                "model": model,
                "size": size,
                "seconds": seconds
            }
            
            # 添加输入图像（如果提供）
            if input_image:
                # 准备输入图像（读取字节和 MIME 类型，自动调整尺寸）
                image_tuple = await self._prepare_input_image(input_image, size)
                # image_tuple 格式: (filename, image_bytes, mimetype)
                create_params["input_reference"] = image_tuple
                video = await self.client.videos.create(**create_params)
            else:
                # T2V 模式，直接创建
                video = await self.client.videos.create(**create_params)
            
            video_id = video.id
            logger.info(f"✅ 视频任务已创建，ID: {video_id}")
            logger.info(f"🎬 任务状态: {video.status}")
            
            return video_id
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"🎬 创建视频任务失败: {error_msg}")
            
            # 判断是否是可重试的错误
            if "500" in error_msg or "503" in error_msg or "timeout" in error_msg.lower():
                raise OpenAISoraServerErrorException(error_msg)
            else:
                raise OpenAISoraFinalException(error_msg)
    
    @retry(
        retry=retry_if_exception_type(OpenAISoraRetryException),
        stop=stop_after_delay(600),  # 最多轮询10分钟
        wait=wait_fixed(5),  # 每5秒轮询一次
        sleep=asyncio.sleep,
    )
    async def poll_video_task_until_complete(
        self,
        video_id: str,
        original_prompt: str,
        size: str,
        seconds: str,
        target_width: Optional[int] = None,
        target_height: Optional[int] = None,
    ) -> VideoGenerationResult:
        """
        轮询视频任务直到完成
        
        Args:
            video_id: 视频任务ID
            original_prompt: 原始提示词
            size: 视频分辨率
            seconds: 视频时长（秒）
            
        Returns:
            VideoGenerationResult对象
        """
        if not self.client:
            raise OpenAISoraFinalException("OpenAI API key 未配置")
        
        # 协作式取消：每次轮询前检查用户是否已取消，及时放弃在跑的视频生成
        await raise_if_cancelled()
        logger.info(f"🎬 轮询视频任务状态: {video_id}")
        
        try:
            # 使用 OpenAI SDK 检索视频
            video = await self.client.videos.retrieve(video_id)
            
            status = video.status
            logger.info(f"🎬 视频任务状态: {status}")
            
            # 处理不同的状态
            if status == "completed":
                # 任务完成 - 下载视频内容
                logger.info(f"✅ OpenAI Sora 视频生成完成，video_id: {video_id}")
                logger.info(f"🎬 Video 对象: {video}")
                
                # 下载视频内容并保存到本地
                try:
                    video_url = await self.download_and_save_video(
                        video_id,
                        target_width=target_width,
                        target_height=target_height,
                    )
                    logger.info(f"✅ 视频已下载并保存: {video_url}")
                except Exception as e:
                    logger.error(f"❌ 下载视频失败: {e}")
                    raise OpenAISoraFinalException(f"下载视频失败: {e}")
                
                # 解析时长
                duration_seconds = float(seconds) if seconds else None
                
                return VideoGenerationResult.success_result(
                    video_url=video_url,
                    generated_prompt=original_prompt,
                    provider=VideoProvider.OPENAI_SORA,
                    duration=duration_seconds,
                    resolution=size,
                    message=f"✅ OpenAI Sora 视频生成成功"
                )
            
            elif status == "failed":
                # 任务失败
                error_msg = getattr(video, 'error', '未知错误')
                logger.error(f"❌ OpenAI Sora 视频生成失败: {error_msg}")
                raise OpenAISoraFinalException(f"视频生成失败: {error_msg}")
            
            elif status in ["queued", "processing", "in_progress"]:
                # 任务进行中，继续轮询
                logger.info(f"⏳ 视频生成中，继续等待...")
                raise OpenAISoraRetryException(f"视频任务进行中: {status}")
            
            else:
                # 未知状态
                logger.warning(f"⚠️ 未知状态: {status}，继续轮询...")
                raise OpenAISoraRetryException(f"未知状态: {status}")
                
        except Exception as e:
            if isinstance(e, (OpenAISoraRetryException, OpenAISoraFinalException)):
                raise
            
            error_msg = str(e)
            logger.error(f"🎬 轮询视频任务失败: {error_msg}")
            
            # 判断是否是可重试的错误
            if "500" in error_msg or "503" in error_msg or "timeout" in error_msg.lower():
                logger.warning(f"🎬 可重试错误，继续重试...")
                raise OpenAISoraServerErrorException(error_msg)
            else:
                raise OpenAISoraFinalException(error_msg)
    
    def _download_and_save_video_sync(
        self,
        video_id: str,
        target_width: Optional[int] = None,
        target_height: Optional[int] = None,
    ) -> str:
        """同步下载视频并上传到 S3，供 run_in_executor 调用。若提供 target 则先归一化到标准分辨率再上传。"""
        import uuid
        from openai import OpenAI
        sync_client = OpenAI(api_key=self.api_key)
        response = sync_client.videos.download_content(video_id)
        content = response.read()
        logger.info(f"📥 视频下载完成，大小: {len(content)} bytes")
        if target_width and target_height and target_width > 0 and target_height > 0:
            tmp_in = None
            try:
                fd, tmp_in = tempfile.mkstemp(suffix=".mp4", prefix="sora_dl_")
                os.write(fd, content)
                os.close(fd)
                from ..utils.video_utils import normalize_video_to_target_sync
                out_path, _ = normalize_video_to_target_sync(tmp_in, target_width, target_height)
                if out_path != tmp_in:
                    with open(out_path, "rb") as f:
                        content = f.read()
                    try:
                        os.unlink(out_path)
                    except FileNotFoundError:
                        pass
            finally:
                if tmp_in and os.path.exists(tmp_in):
                    try:
                        os.unlink(tmp_in)
                    except Exception:
                        pass
        filename = f"openai_sora_{video_id}_{uuid.uuid4().hex[:8]}.mp4"
        file_key = f"videos/{filename}"
        video_url = s3_utils.upload_file_sync(
            file_data=content,
            file_key=file_key,
            content_type="video/mp4"
        )
        logger.info(f"☁️ 视频已上传到 S3: {video_url}")
        return video_url

    async def download_and_save_video(
        self,
        video_id: str,
        target_width: Optional[int] = None,
        target_height: Optional[int] = None,
    ) -> str:
        """
        下载视频并上传到 S3（同步 SDK 在线程池执行）。若提供 target 则先归一化到标准分辨率再上传。
        """
        if not self.client:
            raise OpenAISoraFinalException("OpenAI API key 未配置")
        logger.info(f"📥 开始下载视频: {video_id}")
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: self._download_and_save_video_sync(
                    video_id,
                    target_width=target_width,
                    target_height=target_height,
                ),
            )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ 下载视频失败: {error_msg}")
            raise


# 全局服务实例
_openai_sora_service = None

def get_openai_sora_service() -> OpenAISoraService:
    """获取 OpenAI Sora 服务单例"""
    global _openai_sora_service
    if _openai_sora_service is None:
        _openai_sora_service = OpenAISoraService()
    return _openai_sora_service

