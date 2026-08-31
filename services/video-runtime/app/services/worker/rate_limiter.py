"""
限流器 - 控制任务并发数（所有worker共享）

功能：
- 使用Redis信号量控制全局并发数
- 多个worker共享同一个信号量计数器
- 确保所有worker的总并发数不超过max_concurrent

实现：
- 使用Redis INCR/DECR操作
- 使用原子操作避免race condition
"""
import logging
import redis.asyncio as aioredis
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    限流器 - 控制任务并发数
    
    工作原理：
    - 使用Redis计数器作为信号量
    - acquire(): 如果计数器 < max_concurrent，则INCR并返回True
    - release(): DECR计数器
    - 所有worker共享同一个计数器，确保全局并发控制
    
    多Worker支持：
    - 使用Redis原子操作（INCR/DECR）避免race condition
    - 多个worker同时调用acquire时，Redis确保原子性
    """
    
    def __init__(self, redis_client: aioredis.Redis, max_concurrent: int = 1000, environment: Optional[str] = None):
        """
        初始化限流器
        
        Args:
            redis_client: Redis客户端
            max_concurrent: 最大并发数（所有worker共享）
            environment: 环境名称（用于key前缀，实现环境隔离）
        """
        self.redis = redis_client
        self.max_concurrent = max_concurrent
        
        # 获取环境前缀
        if environment is None:
            from ...config import get_settings
            settings = get_settings()
            env = settings.ENVIRONMENT.value
        else:
            env = environment
        
        env_prefix_map = {
            "local": "local",
            "development": "dev",
            "production": "prod"
        }
        env_prefix = env_prefix_map.get(env, "dev")
        
        # 信号量key也加上环境前缀（实现环境隔离）
        self.semaphore_key = f"cuti-videoagent:{env_prefix}:worker:semaphore"
        
        logger.info(f"🔧 RateLimiter初始化: 环境={env} (前缀={env_prefix}), 信号量key={self.semaphore_key}, 最大并发={max_concurrent}")
    
    async def acquire(self) -> bool:
        """
        获取执行权限（原子操作，避免race condition）
        
        工作流程：
        1. 使用Redis Lua脚本原子地检查并增加计数器
        2. 如果增加后的值 <= max_concurrent，返回True
        3. 否则回滚（DECR）并返回False
        
        多Worker安全性：
        - 使用Lua脚本确保原子性（GET + INCR + 检查 + 条件回滚）
        - 多个worker同时调用时，Redis确保只有一个能成功
        - 避免race condition：不会超过max_concurrent
        
        Returns:
            bool: True=获取成功，False=达到并发限制
        """
        # 使用Lua脚本实现原子操作，避免race condition
        # 脚本逻辑：
        # 1. INCR计数器
        # 2. 如果新值 <= max_concurrent，返回1（成功）
        # 3. 否则DECR回滚，返回0（失败）
        lua_script = """
        local current = redis.call('INCR', KEYS[1])
        redis.call('EXPIRE', KEYS[1], ARGV[2])
        if current <= tonumber(ARGV[1]) then
            return 1
        else
            redis.call('DECR', KEYS[1])
            return 0
        end
        """
        
        try:
            result = await self.redis.eval(
                lua_script,
                1,  # 1个key
                self.semaphore_key,
                str(self.max_concurrent),
                "60"  # 过期时间60秒
            )
            
            success = result == 1
            if success:
                # 获取当前值用于日志（str模式，直接转换）
                current = await self.redis.get(self.semaphore_key)
                current = int(current) if current else 0
                logger.debug(f"✅ 获取执行权限: {current}/{self.max_concurrent}")
            else:
                current = await self.redis.get(self.semaphore_key)
                current = int(current) if current else 0
                logger.debug(f"⏸️ 达到并发限制: {current}/{self.max_concurrent}")
            
            return success
        except Exception as e:
            logger.error(f"❌ 获取执行权限失败: {e}", exc_info=True)
            # 失败时返回False，避免无限重试
            return False
    
    async def release(self):
        """
        释放执行权限
        
        功能：
        - DECR计数器（原子操作）
        - 释放一个并发槽位
        
        多Worker安全性：
        - DECR是原子操作，多个worker同时调用是安全的
        - 即使计数器变为负数（不应该发生），也不会影响功能
        
        注意：
        - 必须在任务完成后调用，否则会导致并发数泄漏
        - 即使任务失败，也要在finally中调用
        """
        try:
            current = await self.redis.decr(self.semaphore_key)
            # 确保计数器不为负数（防御性编程）
            if current < 0:
                logger.warning(f"⚠️ 信号量计数器为负数: {current}，重置为0")
                await self.redis.set(self.semaphore_key, 0)
                current = 0
            logger.debug(f"✅ 释放执行权限: {current}/{self.max_concurrent}")
        except Exception as e:
            logger.error(f"❌ 释放执行权限失败: {e}", exc_info=True)
