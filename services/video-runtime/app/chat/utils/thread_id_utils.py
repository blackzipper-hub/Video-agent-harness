"""
Thread ID 工具模块
提供统一的thread_id生成和管理功能
"""
import logging
import uuid
from typing import Optional, Union

logger = logging.getLogger(__name__)


def generate_new_thread_id(user_id: str) -> str:
    """
    生成新的thread_id（简单的user_id + uuid格式）
    
    Args:
        user_id: 用户ID
        
    Returns:
        str: 新生成的thread_id，格式为 "thread_{user_id}_{uuid}"
        
    Examples:
        >>> generate_new_thread_id("user123")
        "thread_user123_abc123def456"
    """
    unique_id = str(uuid.uuid4())
    thread_id = f"thread_{user_id}_{unique_id}"
    logger.debug(f"🆕 生成新 thread_id: {thread_id}")
    return thread_id
