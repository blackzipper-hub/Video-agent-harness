"""
Redis连接管理
"""
import redis.asyncio as aioredis
from typing import Optional
import logging
from urllib.parse import urlparse

from ...config import get_settings
from .stream_service import RedisStreamService

logger = logging.getLogger(__name__)

# 全局Redis客户端实例（支持两种模式）
_redis_client_bytes: Optional[aioredis.Redis] = None  # bytes模式（默认，兼容现有代码）
_redis_client_str: Optional[aioredis.Redis] = None    # str模式（用于AccountManager等新代码）
_redis_stream_service: Optional[RedisStreamService] = None


def parse_redis_url(url: str) -> dict:
    """解析Redis URL"""
    parsed = urlparse(url)
    
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 6379,
        "db": int(parsed.path.lstrip("/")) if parsed.path else 0,
        "password": parsed.password,
    }


async def get_redis_client(decode_responses: bool = False) -> aioredis.Redis:
    """
    获取Redis客户端（单例模式，支持两种模式）
    
    Args:
        decode_responses: 是否自动解码响应为字符串
            - False: 返回bytes（默认，兼容现有代码如RedisStreamService）
            - True: 返回str（用于AccountManager等新代码，避免bytes转换）
    
    Returns:
        Redis客户端实例
    """
    global _redis_client_bytes, _redis_client_str
    
    settings = get_settings()
    redis_url = settings.REDIS_URL
    
    # 根据模式返回对应的客户端
    if decode_responses:
        # str模式
        if _redis_client_str is None:
            try:
                redis_config = parse_redis_url(redis_url)
                _redis_client_str = aioredis.Redis(
                    host=redis_config["host"],
                    port=redis_config["port"],
                    db=redis_config["db"],
                    password=redis_config["password"],
                    decode_responses=True,  # str模式
                    socket_connect_timeout=5,
                    socket_keepalive=True,
                )
                await _redis_client_str.ping()
                logger.info(f"✅ Redis连接成功(str模式): {redis_config['host']}:{redis_config['port']}/{redis_config['db']}")
            except Exception as e:
                logger.error(f"❌ Redis连接失败(str模式): {e}")
                raise
        return _redis_client_str
    else:
        # bytes模式（默认，兼容现有代码）
        if _redis_client_bytes is None:
            try:
                redis_config = parse_redis_url(redis_url)
                _redis_client_bytes = aioredis.Redis(
                    host=redis_config["host"],
                    port=redis_config["port"],
                    db=redis_config["db"],
                    password=redis_config["password"],
                    decode_responses=False,  # bytes模式
                    socket_connect_timeout=5,
                    socket_keepalive=True,
                )
                await _redis_client_bytes.ping()
                logger.info(f"✅ Redis连接成功(bytes模式): {redis_config['host']}:{redis_config['port']}/{redis_config['db']}")
            except Exception as e:
                logger.error(f"❌ Redis连接失败(bytes模式): {e}")
                raise
        return _redis_client_bytes


async def get_redis_stream_service() -> RedisStreamService:
    """获取Redis Stream服务（单例模式）"""
    return await init_redis_stream_service()


async def init_redis_stream_service():
    """初始化Redis Stream服务（使用str模式）"""
    global _redis_stream_service
    
    if _redis_stream_service is None:
        # 使用str模式的Redis客户端（避免bytes转换）
        redis_client = await get_redis_client(decode_responses=True)
        # 从配置获取环境
        settings = get_settings()
        environment = settings.ENVIRONMENT.value
        _redis_stream_service = RedisStreamService(redis_client, environment=environment)
        logger.info(f"✅ Redis Stream服务初始化成功: 环境={environment}")
    
    return _redis_stream_service


async def close_redis_client():
    """关闭Redis客户端（两种模式都关闭）"""
    global _redis_client_bytes, _redis_client_str, _redis_stream_service
    
    if _redis_client_bytes:
        await _redis_client_bytes.close()
        _redis_client_bytes = None
        logger.info("✅ Redis客户端已关闭(bytes模式)")
    
    if _redis_client_str:
        await _redis_client_str.close()
        _redis_client_str = None
        logger.info("✅ Redis客户端已关闭(str模式)")
    
    _redis_stream_service = None
