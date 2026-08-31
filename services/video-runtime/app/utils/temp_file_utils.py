"""
临时文件管理工具
提供临时目录创建、媒体文件下载、批量处理等功能

功能模块：
1. 临时目录管理: 自动清理的临时目录上下文管理器
2. 媒体下载: 从 URL 下载图片/音频/视频到临时文件
3. 批量处理: 批量下载多个媒体文件
4. S3 上传: 从临时文件上传到 S3

使用场景：
- 视频合成前下载所有素材到临时目录
- FFmpeg 处理需要本地文件路径
- 处理完成后自动清理临时文件
"""
import os
import uuid
import tempfile
import logging
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
import aiofiles
from ..utils.s3_utils import s3_utils
from ..utils.file_utils import MediaType

logger = logging.getLogger(__name__)


@asynccontextmanager
async def temp_directory():
    """
    异步上下文管理器，提供临时目录
    自动清理，确保 delete=True
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.debug(f"📁 创建临时目录: {temp_dir}")
        try:
            yield temp_dir
        finally:
            logger.debug(f"🗑️ 清理临时目录: {temp_dir}")


async def download_media_to_temp(
    media_url: str, 
    temp_dir: str, 
    media_type: MediaType,
    filename_prefix: str = "temp"
) -> Optional[str]:
    """
    下载媒体文件到临时目录
    
    Args:
        media_url: 媒体文件URL
        temp_dir: 临时目录路径
        media_type: 媒体类型
        filename_prefix: 文件名前缀
        
    Returns:
        本地文件路径，失败返回None
    """
    try:
        file_extension = ".mp4" if media_type == MediaType.VIDEO else ".mp3"
        temp_filename = f"{filename_prefix}_{uuid.uuid4().hex[:8]}{file_extension}"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        success = await s3_utils.download_file(media_url, temp_path)
        
        if success and os.path.exists(temp_path):
            logger.debug(f"📁 媒体文件下载到temp: {temp_path}")
            return temp_path
        else:
            logger.error(f"❌ 无法下载媒体文件: {media_url}")
            return None
    
    except Exception as e:
        logger.error(f"❌ 下载媒体失败: {e}")
        return None


async def upload_file_from_temp(
    local_path: str, 
    s3_key: str,
    description: str = "文件"
) -> str:
    """
    从临时目录上传文件到S3
    
    Args:
        local_path: 本地文件路径
        s3_key: S3存储键
        description: 文件描述（用于日志）
        
    Returns:
        S3 URL
        
    Raises:
        Exception: 上传失败时抛出异常
    """
    try:
        # 异步读取文件内容，避免阻塞事件循环
        async with aiofiles.open(local_path, 'rb') as f:
            file_data = await f.read()
        
        # 根据文件扩展名确定 Content-Type
        content_type = None
        if s3_key.lower().endswith('.mp4'):
            content_type = "video/mp4"
        elif s3_key.lower().endswith('.mp3'):
            content_type = "audio/mpeg"
        elif s3_key.lower().endswith('.jpg') or s3_key.lower().endswith('.jpeg'):
            content_type = "image/jpeg"
        elif s3_key.lower().endswith('.png'):
            content_type = "image/png"
        elif s3_key.lower().endswith('.webp'):
            content_type = "image/webp"
        
        final_url = await s3_utils.upload_file(file_data, s3_key, content_type)
        
        if final_url:
            logger.info(f"✅ {description}上传成功: {final_url}")
            return final_url
        else:
            raise Exception(f"{description}上传到S3失败")
    
    except Exception as e:
        logger.error(f"❌ 上传{description}失败: {e}")
        raise


async def batch_download_media_to_temp(
    media_urls: List[str],
    temp_dir: str,
    media_type: MediaType,
    filename_prefix: str = "batch"
) -> List[str]:
    """
    批量下载媒体文件到临时目录
    
    Args:
        media_urls: 媒体文件URL列表
        temp_dir: 临时目录路径
        media_type: 媒体类型
        filename_prefix: 文件名前缀
        
    Returns:
        本地文件路径列表（跳过失败的文件）
    """
    local_paths = []
    
    for i, media_url in enumerate(media_urls):
        local_path = await download_media_to_temp(
            media_url, 
            temp_dir, 
            media_type, 
            f"{filename_prefix}_{i+1}"
        )
        if local_path:
            local_paths.append(local_path)
            logger.debug(f"   ✅ 文件{i+1}下载成功: {local_path}")
        else:
            logger.error(f"   ❌ 文件{i+1}下载失败: {media_url}")
    
    return local_paths


