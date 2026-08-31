"""
Google 文件上传工具
用于上传大文件到 Google 并获取 file_uri，绕过 LangSmith 大小限制
"""
import logging
import os
import asyncio
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Google Genai 客户端（延迟初始化）
_genai_client = None


def _get_genai_client():
    """获取或创建 Google Genai 客户端"""
    global _genai_client
    if _genai_client is None:
        try:
            from google import genai
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            _genai_client = genai.Client(api_key=api_key) if api_key else genai.Client()
            logger.info("✅ Google GenAI 客户端初始化成功")
        except Exception as e:
            logger.error(f"❌ Google GenAI 客户端初始化失败: {e}")
            raise
    return _genai_client


async def upload_video_to_google(video_path: str, max_wait_time: int = 300) -> tuple[Optional[str], Optional[str]]:
    """
    上传视频到 Google 服务器并等待处理完成（异步版本）
    
    Args:
        video_path: 本地视频文件路径
        max_wait_time: 最大等待时间（秒），默认300秒（5分钟）
        
    Returns:
        (file_uri, mime_type) 元组。
        - file_uri: Google 文件 URI
        - mime_type: Google 实际识别的 MIME 类型
        使用 file_uri 发送 Gemini 请求时，mime_type 必须与 Google 记录的一致，
        否则 Gemini 会返回 400 Invalid argument。
        如果失败返回 (None, None)。
        
    注意：
        - 使用 file_uri 可以绕过 LangSmith 的大小限制（26MB）
        - 上传后需要等待处理完成才能使用
        - 等待时间会根据文件大小自动调整，但不会超过 max_wait_time
    """
    try:
        # 获取 genai 客户端
        client = _get_genai_client()
        
        # 检查文件大小，根据大小调整等待时间
        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        logger.info(f"📤 上传视频到 Google: {video_path}, 大小: {file_size_mb:.2f} MB")
        
        # 根据文件大小动态调整等待时间（大文件需要更长时间处理）
        # 基础等待时间 60秒，每增加 10MB 增加 30秒，但不超过 max_wait_time
        base_wait_time = 60
        additional_wait = min(int(file_size_mb / 10) * 30, max_wait_time - base_wait_time)
        adjusted_wait_time = min(base_wait_time + additional_wait, max_wait_time)
        logger.info(f"⏱️ 根据文件大小调整等待时间: {adjusted_wait_time}秒（最大: {max_wait_time}秒）")
        
        # 上传文件（同步操作，在 executor 中运行）
        loop = asyncio.get_event_loop()
        uploaded_file = await loop.run_in_executor(
            None,
            lambda: client.files.upload(file=video_path)
        )
        logger.info(f"✅ 视频已上传，URI: {uploaded_file.uri}")
        
        # 等待处理完成（异步轮询）
        start_time = time.time()
        check_interval = 2  # 每2秒检查一次
        
        while uploaded_file.state.name == "PROCESSING":
            elapsed = time.time() - start_time
            if elapsed > adjusted_wait_time:
                logger.warning(f"⚠️ 视频处理超时（{adjusted_wait_time}秒），但继续等待直到最大时间（{max_wait_time}秒）")
                # 如果超过调整后的等待时间，但还没到最大时间，继续等待
                if elapsed > max_wait_time:
                    logger.error(f"❌ 视频处理超时（{max_wait_time}秒）")
                    return None, None
            
            await asyncio.sleep(check_interval)
            
            # 获取最新状态（同步操作，在 executor 中运行）
            uploaded_file = await loop.run_in_executor(
                None,
                lambda: client.files.get(name=uploaded_file.name)
            )
            logger.debug(f"⏳ 视频处理状态: {uploaded_file.state.name}, 已等待: {elapsed:.0f}秒")
        
        if uploaded_file.state.name == "ACTIVE":
            elapsed = time.time() - start_time
            actual_mime = getattr(uploaded_file, "mime_type", None)
            logger.info(f"✅ 视频处理完成，URI: {uploaded_file.uri}, mime_type: {actual_mime}, 耗时: {elapsed:.0f}秒")
            return uploaded_file.uri, actual_mime
        else:
            logger.error(f"❌ 视频处理失败，状态: {uploaded_file.state.name}")
            return None, None
            
    except Exception as e:
        logger.error(f"❌ 上传视频到 Google 失败: {e}", exc_info=True)
        return None, None


async def upload_audio_to_google(audio_path: str, max_wait_time: int = 300) -> tuple[Optional[str], Optional[str]]:
    """
    上传音频到 Google 服务器并等待处理完成（异步版本）。
    与 upload_video_to_google 相同流程，用于大音频绕过 Gemini 20MB inline 限制。

    Args:
        audio_path: 本地音频文件路径
        max_wait_time: 最大等待时间（秒），默认300秒（5分钟）

    Returns:
        (file_uri, mime_type) 元组。
        - file_uri: Google 文件 URI（如 https://generativelanguage.googleapis.com/v1beta/files/xxx）
        - mime_type: Google 实际识别的 MIME 类型（如 audio/x-wav）
        使用 file_uri 发送 Gemini 请求时，mime_type 必须与 Google 记录的一致，
        否则 Gemini 会返回 400 Invalid argument。
        如果失败返回 (None, None)。

    注意：
        - 使用 file_uri 可绕过 Gemini 请求体 20MB 限制
        - 上传后需等待处理完成才能使用
    """
    try:
        client = _get_genai_client()
        file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        logger.info(f"📤 上传音频到 Google: {audio_path}, 大小: {file_size_mb:.2f} MB")

        base_wait_time = 60
        additional_wait = min(int(file_size_mb / 10) * 30, max_wait_time - base_wait_time)
        adjusted_wait_time = min(base_wait_time + additional_wait, max_wait_time)
        logger.info(f"⏱️ 音频处理等待时间: {adjusted_wait_time}秒（最大: {max_wait_time}秒）")

        loop = asyncio.get_event_loop()
        uploaded_file = await loop.run_in_executor(
            None,
            lambda: client.files.upload(file=audio_path)
        )
        logger.info(f"✅ 音频已上传，URI: {uploaded_file.uri}")

        start_time = time.time()
        check_interval = 2
        while uploaded_file.state.name == "PROCESSING":
            elapsed = time.time() - start_time
            if elapsed > max_wait_time:
                logger.error(f"❌ 音频处理超时（{max_wait_time}秒）")
                return None, None
            await asyncio.sleep(check_interval)
            uploaded_file = await loop.run_in_executor(
                None,
                lambda: client.files.get(name=uploaded_file.name)
            )
            logger.debug(f"⏳ 音频处理状态: {uploaded_file.state.name}, 已等待: {elapsed:.0f}秒")

        if uploaded_file.state.name == "ACTIVE":
            elapsed = time.time() - start_time
            actual_mime = getattr(uploaded_file, "mime_type", None)
            logger.info(f"✅ 音频处理完成，URI: {uploaded_file.uri}, mime_type: {actual_mime}, 耗时: {elapsed:.0f}秒")
            return uploaded_file.uri, actual_mime
        logger.error(f"❌ 音频处理失败，状态: {uploaded_file.state.name}")
        return None, None
    except Exception as e:
        logger.error(f"❌ 上传音频到 Google 失败: {e}", exc_info=True)
        return None, None
