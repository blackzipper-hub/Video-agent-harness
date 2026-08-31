"""
API 限流器 - 按 Model 维度进行限流

功能：
- 控制 API 调用频率（按 Provider + Model + Account 维度）
- 支持多种限流策略：RPM、TPM、RPD、FIXED_WINDOW、CONCURRENT
- 支持共享限流（如 WaveSpeed 的 Images/min、Videos/min）
- 所有限流规则从 AppConfig 加载，代码中不提供默认规则

与 worker/rate_limiter.py 的区别：
- worker/rate_limiter: 控制 worker 任务的并发数（所有 worker 共享）
- account/rate_limiter: 控制 API 调用的限流（按 Model 维度）
"""
import time
import uuid
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

from ...models.tool_enums import ToolProvider, ToolType
from ..redis.connection import get_redis_client
from ...config import get_settings

logger = logging.getLogger(__name__)


def _get_env_prefix_for_clear() -> str:
    """获取环境前缀（与 ModelRateLimiter._get_env_prefix 一致）"""
    settings = get_settings()
    env = settings.ENVIRONMENT.value
    env_prefix_map = {
        "local": "local",
        "development": "dev",
        "production": "prod"
    }
    return env_prefix_map.get(env, "dev")


async def clear_wavespeed_concurrent_limits(
    redis=None,
    env_prefix: Optional[str] = None
) -> int:
    """
    清理 WaveSpeed 的 CONCURRENT 限流计数（应用关闭时调用，避免下次启动时残留）。
    使用 Redis SCAN 匹配 cuti-videoagent:{env_prefix}:ratelimit:wavespeed:*:concurrent 并删除。

    Args:
        redis: Redis 客户端（若为 None 则临时获取，调用方在关闭前调用时建议传入已持有的客户端）
        env_prefix: 环境前缀，如 local/dev/prod（若为 None 则从 get_settings() 推导）

    Returns:
        删除的 key 数量
    """
    if redis is None:
        redis = await get_redis_client(decode_responses=True)
    if env_prefix is None:
        env_prefix = _get_env_prefix_for_clear()
    pattern = f"cuti-videoagent:{env_prefix}:ratelimit:wavespeed:*:concurrent"
    deleted = 0
    async for key in redis.scan_iter(match=pattern):
        await redis.delete(key)
        deleted += 1
    if deleted:
        logger.info("Cleared WaveSpeed CONCURRENT rate limit keys: %d keys (pattern=%s)", deleted, pattern)
    return deleted


class RateLimitStrategy(str, Enum):
    """
    限流策略枚举
    
    支持的策略：
    - RPM: Requests Per Minute（滑动窗口，60秒）
    - TPM: Tokens Per Minute（滑动窗口，60秒，只有 input tokens）
    - RPD: Requests Per Day（滑动窗口，24小时）
    - FIXED_WINDOW: 固定窗口（窗口大小可配置，如 Suno 的 3 秒）
    - CONCURRENT: 并发任务数（同时进行的任务数）
    """
    RPM = "RPM"
    TPM = "TPM"
    RPD = "RPD"
    FIXED_WINDOW = "FIXED_WINDOW"
    CONCURRENT = "CONCURRENT"


@dataclass
class RateLimitRule:
    """
    限流规则
    
    属性：
    - tool_type: 工具类型（模型）
    - strategy: 限流策略
    - limit: 限制值
    - window_seconds: 时间窗口（RPM/TPM/RPD/FIXED_WINDOW 使用，CONCURRENT 为 0）
    - shared_key: 共享限流 key（如 WaveSpeed 的 "images", "videos", "all"）
    
    注意：
    - 一个 Model 可能有多个限流规则（例如：RPM + TPM + CONCURRENT）
    - shared_key 用于共享限流（多个模型共享同一个限流 key）
    """
    tool_type: ToolType
    strategy: RateLimitStrategy
    limit: int
    window_seconds: int
    shared_key: Optional[str] = None


class RateLimitExceededError(Exception):
    """
    限流超出异常
    
    当所有账号都达到限流限制时抛出此异常
    """
    pass


class ModelRateLimiter:
    """
    API 限流器 - 按 Model 维度进行限流
    
    功能：
    - 控制 API 调用频率（按 Provider + Model + Account 维度）
    - 支持多种限流策略：RPM、TPM、RPD、FIXED_WINDOW、CONCURRENT
    - 支持共享限流（如 WaveSpeed 的 Images/min、Videos/min）
    - 所有限流规则从 AppConfig 加载
    
    使用示例：
        rate_limiter = ModelRateLimiter()
        await rate_limiter.initialize()
        await rate_limiter.load_rules_from_config(config)
        
        # 检查限流
        can_proceed = await rate_limiter.check_rate_limit(
            ToolProvider.GOOGLE, ToolType.GEMINI_2_5_FLASH_IMAGE, "google-main"
        )
        
        # 获取配额
        acquired = await rate_limiter.acquire_rate_limit(...)
        
        # 执行请求后释放（仅 CONCURRENT 需要）
        await rate_limiter.release_rate_limit(...)
    """
    
    def __init__(self, redis_client=None, env_prefix: Optional[str] = None):
        self.redis = redis_client
        self.env_prefix = env_prefix or self._get_env_prefix()
        self._rules_cache: Dict[str, List[RateLimitRule]] = {}  # 每个 model 可能有多个规则
    
    def _get_env_prefix(self) -> str:
        """
        获取环境前缀（用于 Redis key 前缀，实现环境隔离）
        
        Returns:
            环境前缀（"local", "dev", "prod"）
        """
        settings = get_settings()
        env = settings.ENVIRONMENT.value
        env_prefix_map = {
            "local": "local",
            "development": "dev",
            "production": "prod"
        }
        return env_prefix_map.get(env, "dev")
    
    async def initialize(self):
        """
        初始化 Redis 客户端
        
        注意：如果构造时已传入 redis_client，则不会重新创建
        """
        backend = (getattr(get_settings(), "ACCOUNT_BACKEND", "appconfig") or "appconfig").lower()
        if backend == "env":
            logger.info("ModelRateLimiter initialized without Redis (ACCOUNT_BACKEND=env)")
            return
        if self.redis is None:
            self.redis = await get_redis_client(decode_responses=True)
        logger.info("ModelRateLimiter initialized")
    
    async def load_rules_from_config(self, config: dict):
        """
        从 AppConfig 加载限流规则
        
        配置格式：
        {
            "providers": {
                "google": {
                    "rate_limits": {
                        "gemini-2.5-flash-image": [
                            {
                                "strategy": "RPM",
                                "limit": 500,
                                "window_seconds": 60
                            },
                            {
                                "strategy": "TPM",
                                "limit": 500000,
                                "window_seconds": 60
                            }
                        ]
                    }
                },
                "wavespeed": {
                    "rate_limits": {
                        "seedream-v4.5": [
                            {
                                "strategy": "RPM",
                                "limit": 500,
                                "window_seconds": 60,
                                "shared_key": "images"
                            },
                            {
                                "strategy": "CONCURRENT",
                                "limit": 100,
                                "window_seconds": 0,
                                "shared_key": "all"
                            }
                        ]
                    }
                }
            }
        }
        
        Args:
            config: AppConfig 配置字典
        """
        self._rules_cache.clear()
        
        for provider_name, provider_config in config.get("providers", {}).items():
            rate_limits = provider_config.get("rate_limits", {})
            
            for tool_type_str, rules_config in rate_limits.items():
                # 将字符串转换为 ToolType
                try:
                    tool_type = ToolType(tool_type_str)
                except ValueError:
                    logger.warning(f"Unknown tool_type: {tool_type_str}, skipping")
                    continue
                
                # 将 provider_name 转换为 ToolProvider
                try:
                    provider = ToolProvider(provider_name)
                except ValueError:
                    logger.warning(f"Unknown provider: {provider_name}, skipping")
                    continue
                
                # 解析规则（可能是单个 dict 或 list）
                if isinstance(rules_config, dict):
                    rules_config = [rules_config]
                
                rules = []
                for rule_config in rules_config:
                    try:
                        strategy = RateLimitStrategy(rule_config["strategy"])
                        rule = RateLimitRule(
                            tool_type=tool_type,
                            strategy=strategy,
                            limit=rule_config["limit"],
                            window_seconds=rule_config.get("window_seconds", 0),
                            shared_key=rule_config.get("shared_key")
                        )
                        rules.append(rule)
                    except (KeyError, ValueError) as e:
                        logger.warning(f"Invalid rate limit rule for {tool_type_str}: {e}")
                        continue
                
                if rules:
                    key = f"{provider.value}:{tool_type.value}"
                    self._rules_cache[key] = rules
                    logger.debug(f"Loaded {len(rules)} rate limit rules for {key}")
    
    def _get_rules(self, provider: ToolProvider, tool_type: ToolType) -> List[RateLimitRule]:
        """
        获取限流规则（可能返回多个规则）
        
        一个 Model 可能有多个限流规则，例如：
        - RPM = 500（每分钟 500 个请求）
        - TPM = 500K（每分钟 500K tokens）
        - CONCURRENT = 100（100 个并发任务）
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
        
        Returns:
            限流规则列表（可能为空）
        """
        key = f"{provider.value}:{tool_type.value}"
        rules = self._rules_cache.get(key, [])
        return rules if isinstance(rules, list) else [rules] if rules else []
    
    def _get_rpm_key(self, provider: ToolProvider, tool_type: ToolType, account_name: str) -> str:
        """获取 RPM key（模型级别）"""
        return f"cuti-videoagent:{self.env_prefix}:ratelimit:{provider.value}:{tool_type.value}:{account_name}:rpm"
    
    def _get_shared_rpm_key(self, provider: ToolProvider, shared_key: str, account_name: str) -> str:
        """获取共享 RPM key（用于 WaveSpeed 的共享限制，如 images/videos）"""
        return f"cuti-videoagent:{self.env_prefix}:ratelimit:{provider.value}:shared:{shared_key}:{account_name}:rpm"
    
    def _get_tpm_key(self, provider: ToolProvider, tool_type: ToolType, account_name: str) -> str:
        """获取 TPM key（模型级别）"""
        return f"cuti-videoagent:{self.env_prefix}:ratelimit:{provider.value}:{tool_type.value}:{account_name}:tpm"
    
    def _get_rpd_key(self, provider: ToolProvider, tool_type: ToolType, account_name: str) -> str:
        """获取 RPD key（模型级别）"""
        return f"cuti-videoagent:{self.env_prefix}:ratelimit:{provider.value}:{tool_type.value}:{account_name}:rpd"
    
    def _get_fixed_window_key(self, provider: ToolProvider, tool_type: ToolType, account_name: str) -> str:
        """获取 FIXED_WINDOW key（模型级别）"""
        return f"cuti-videoagent:{self.env_prefix}:ratelimit:{provider.value}:{tool_type.value}:{account_name}:fixed"
    
    def _get_concurrent_key(self, provider: ToolProvider, tool_type: ToolType, account_name: str) -> str:
        """获取 CONCURRENT key（模型级别）"""
        return f"cuti-videoagent:{self.env_prefix}:ratelimit:{provider.value}:{tool_type.value}:{account_name}:concurrent"
    
    def _get_shared_concurrent_key(self, provider: ToolProvider, shared_key: str, account_name: str) -> str:
        """获取共享 CONCURRENT key（用于 WaveSpeed 的共享限制，如 all）"""
        return f"cuti-videoagent:{self.env_prefix}:ratelimit:{provider.value}:shared:{shared_key}:{account_name}:concurrent"
    
    async def check_rate_limit(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str
    ) -> bool:
        """
        检查是否超过限流（只检查，不占用配额）
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型（模型）
            account_name: 账号名称
        
        Returns:
            True: 可以继续请求
            False: 超过限流
        """
        rules = self._get_rules(provider, tool_type)
        if not rules:
            return True  # 没有规则，允许通过
        
        # 策略检查函数映射
        check_funcs = {
            RateLimitStrategy.RPM: self._check_rpm,
            RateLimitStrategy.TPM: self._check_tpm,
            RateLimitStrategy.RPD: self._check_rpd,
            RateLimitStrategy.FIXED_WINDOW: self._check_fixed_window,
            RateLimitStrategy.CONCURRENT: self._check_concurrent,
        }
        
        # 检查所有规则，必须全部通过
        for rule in rules:
            check_func = check_funcs.get(rule.strategy)
            if check_func and not await check_func(provider, tool_type, account_name, rule):
                return False
        
        return True
    
    async def acquire_rate_limit(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str
    ) -> bool:
        """
        获取限流配额（原子操作）
        
        流程：
        1. 先检查所有规则（快速失败）
        2. 如果全部通过，获取配额（原子操作）
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型（模型）
            account_name: 账号名称
        
        Returns:
            True: 成功获取配额
            False: 超过限流或获取失败
        """
        rules = self._get_rules(provider, tool_type)
        if not rules:
            return True  # 没有规则，允许通过
        
        # 策略检查函数映射
        check_funcs = {
            RateLimitStrategy.RPM: self._check_rpm,
            RateLimitStrategy.TPM: self._check_tpm,
            RateLimitStrategy.RPD: self._check_rpd,
            RateLimitStrategy.FIXED_WINDOW: self._check_fixed_window,
            RateLimitStrategy.CONCURRENT: self._check_concurrent,
        }
        
        # 先检查所有规则（快速失败）
        for rule in rules:
            check_func = check_funcs.get(rule.strategy)
            if check_func and not await check_func(provider, tool_type, account_name, rule):
                return False
        
        # 策略获取函数映射（TPM 不需要在 acquire 时占用配额）
        acquire_funcs = {
            RateLimitStrategy.RPM: self._acquire_rpm,
            RateLimitStrategy.RPD: self._acquire_rpd,
            RateLimitStrategy.FIXED_WINDOW: self._acquire_fixed_window,
            RateLimitStrategy.CONCURRENT: self._acquire_concurrent,
        }
        
        # 所有检查通过，获取配额（原子操作）
        for rule in rules:
            acquire_func = acquire_funcs.get(rule.strategy)
            if acquire_func and not await acquire_func(provider, tool_type, account_name, rule):
                return False
            # TPM 不需要在 acquire 时占用配额，在请求完成后更新
        
        return True
    
    async def release_rate_limit(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str
    ):
        """
        释放限流配额（统一接口，内部根据策略类型决定是否需要释放）
        
        策略分类：
        - CONCURRENT: 需要释放（基于运行状态，不是基于时间窗口）
        - RPM/TPM/RPD/FIXED_WINDOW: 不需要释放（基于时间窗口，自动过期）
        
        调用场景：
        - 任务正常完成
        - 任务失败（API 错误、网络错误等）
        - 任务被取消（用户主动取消、超时取消等）
        
        注意：
        - 实现是幂等的（可以安全地多次调用）
        - 必须在 try-finally 中调用，确保即使异常也能释放
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
            account_name: 账号名称
        """
        rules = self._get_rules(provider, tool_type)
        for rule in rules:
            # 只有 CONCURRENT 策略需要释放
            if rule.strategy == RateLimitStrategy.CONCURRENT:
                # 根据是否有 shared_key 选择不同的 key
                key = (self._get_shared_concurrent_key(provider, rule.shared_key, account_name)
                       if rule.shared_key
                       else self._get_concurrent_key(provider, tool_type, account_name))
                
                # TTL 设置为最大任务执行时间（与 acquire 时保持一致）
                max_task_duration = 3600
                
                # 使用 Lua Script 保证原子性，并防止计数器变为负数
                # 同时刷新 TTL（如果计数器 > 0），确保只要还有任务在运行，TTL 就保持有效
                lua_script = """
                local key = KEYS[1]
                local ttl = tonumber(ARGV[1])
                local count = redis.call('GET', key) or 0
                count = tonumber(count)
                
                if count > 0 then
                    redis.call('DECR', key)
                    -- 如果释放后计数器 > 0，刷新 TTL（说明还有其他任务在运行）
                    local new_count = redis.call('GET', key) or 0
                    if tonumber(new_count) > 0 then
                        redis.call('EXPIRE', key, ttl)
                    end
                    return 1
                else
                    -- 已经是 0 或负数，不操作（幂等性）
                    return 0
                end
                """
                
                await self.redis.eval(lua_script, 1, key, max_task_duration)
    
    async def update_tpm_usage(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str,
        input_tokens: int
    ) -> bool:
        """
        更新 TPM 使用量（在请求完成后调用）
        
        调用时机：
        - 在 API 请求完成后调用
        - 根据实际使用的 input tokens 更新计数
        
        注意：
        - 只有 input tokens，没有 output tokens
        - 必须在请求完成后调用，因为 token 数只有在 API 返回后才知道
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
            account_name: 账号名称
            input_tokens: 实际使用的 input tokens 数
        
        Returns:
            True: 更新成功
        """
        rules = self._get_rules(provider, tool_type)
        for rule in rules:
            if rule.strategy == RateLimitStrategy.TPM:
                await self._update_tpm_count(provider, tool_type, account_name, rule, input_tokens)
        return True
    
    # ==================== 各策略的具体实现 ====================
    
    async def _check_rpm(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str,
        rule: RateLimitRule
    ) -> bool:
        """
        检查 RPM 限流（滑动窗口，60秒）
        
        工作原理：
        - 使用 Redis Sorted Set 存储请求记录
        - Score = 请求时间戳（秒），Value = 请求ID
        - 清理过期记录（score < now - 60秒）
        - 统计当前窗口内的请求数
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
            account_name: 账号名称
            rule: 限流规则
        
        Returns:
            True: 未超过限制
            False: 超过限制
        """
        # 根据是否有 shared_key 选择不同的 key
        key = (self._get_shared_rpm_key(provider, rule.shared_key, account_name)
               if rule.shared_key
               else self._get_rpm_key(provider, tool_type, account_name))
        
        now = int(time.time())
        window_start = now - rule.window_seconds
        
        # 清理过期请求（score < window_start）
        await self.redis.zremrangebyscore(key, 0, window_start)
        
        # 获取当前窗口内的请求数
        count = await self.redis.zcard(key)
        return count < rule.limit
    
    async def _acquire_rpm(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str,
        rule: RateLimitRule
    ) -> bool:
        """
        获取 RPM 限流配额（原子操作）
        
        使用 Lua Script 保证原子性：
        1. 清理过期请求
        2. 检查当前请求数
        3. 如果未超限，添加新请求
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
            account_name: 账号名称
            rule: 限流规则
        
        Returns:
            True: 成功获取配额
            False: 超过限制或获取失败
        """
        key = (self._get_shared_rpm_key(provider, rule.shared_key, account_name)
               if rule.shared_key
               else self._get_rpm_key(provider, tool_type, account_name))
        
        now = int(time.time())
        request_id = str(uuid.uuid4())
        
        lua_script = """
        local key = KEYS[1]
        local window = tonumber(ARGV[1])
        local limit = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])
        local request_id = ARGV[4]
        
        -- 清理过期请求（score < now - window）
        redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
        
        -- 获取当前窗口内的请求数
        local count = redis.call('ZCARD', key)
        
        -- 如果未超过限制，添加新请求
        if count < limit then
            redis.call('ZADD', key, now, request_id)  -- score=时间戳，value=请求ID
            redis.call('EXPIRE', key, window)  -- 设置过期时间
            return 1
        else
            return 0
        end
        """
        
        result = await self.redis.eval(
            lua_script, 1, key, rule.window_seconds, rule.limit, now, request_id
        )
        return result == 1
    
    async def _check_tpm(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str,
        rule: RateLimitRule
    ) -> bool:
        """
        检查 TPM 限流（滑动窗口，60秒，只有 input tokens）
        
        工作原理：
        - 使用 Redis Sorted Set 存储 token 使用记录
        - Score = 请求时间戳（秒），Value = token 数（字符串）
        - 清理过期记录，累加当前窗口内的所有 token 数
        
        注意：
        - 只能检查已使用的 tokens，无法预知新请求会使用多少
        - 实际 token 数在请求完成后通过 update_tpm_usage 更新
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
            account_name: 账号名称
            rule: 限流规则
        
        Returns:
            True: 未超过限制（保守检查）
            False: 超过限制
        """
        key = self._get_tpm_key(provider, tool_type, account_name)
        now = int(time.time())
        window_start = now - rule.window_seconds
        
        # 清理过期记录
        await self.redis.zremrangebyscore(key, 0, window_start)
        
        # 累加当前窗口内的所有 token 数
        total_tokens = 0
        records = await self.redis.zrangebyscore(key, window_start, now, withscores=True)
        for token_count_str, _ in records:
            total_tokens += int(token_count_str)
        
        return total_tokens < rule.limit
    
    async def _update_tpm_count(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str,
        rule: RateLimitRule,
        token_count: int
    ):
        """
        更新 TPM 计数（在请求完成后调用，只有 input tokens）
        
        调用时机：
        - 在 API 请求完成后调用
        - 根据实际使用的 input tokens 更新计数
        
        工作原理：
        - 清理过期记录
        - 添加新的 token 使用记录（score=当前时间，value=token数）
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
            account_name: 账号名称
            rule: 限流规则
            token_count: 实际使用的 input tokens 数
        """
        key = self._get_tpm_key(provider, tool_type, account_name)
        now = int(time.time())
        
        lua_script = """
        local key = KEYS[1]
        local token_count = tonumber(ARGV[1])
        local now = tonumber(ARGV[2])
        local window = tonumber(ARGV[3])
        
        -- 清理过期记录
        redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
        
        -- 添加新的 token 使用记录（score=时间戳，value=token数）
        redis.call('ZADD', key, now, token_count)
        redis.call('EXPIRE', key, window)
        
        return 1
        """
        
        await self.redis.eval(lua_script, 1, key, token_count, now, rule.window_seconds)
    
    async def _check_rpd(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str,
        rule: RateLimitRule
    ) -> bool:
        """
        检查 RPD 限流（滑动窗口，24小时）
        
        工作原理：与 RPM 相同，但窗口大小为 24 小时（86400秒）
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
            account_name: 账号名称
            rule: 限流规则
        
        Returns:
            True: 未超过限制
            False: 超过限制
        """
        key = self._get_rpd_key(provider, tool_type, account_name)
        now = int(time.time())
        window_start = now - rule.window_seconds
        
        # 清理过期请求（score < window_start）
        await self.redis.zremrangebyscore(key, 0, window_start)
        
        # 获取当前窗口内的请求数
        count = await self.redis.zcard(key)
        return count < rule.limit
    
    async def _acquire_rpd(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str,
        rule: RateLimitRule
    ) -> bool:
        """
        获取 RPD 限流配额（滑动窗口，24小时）
        
        工作原理：与 RPM 相同，但窗口大小为 24 小时（86400秒）
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
            account_name: 账号名称
            rule: 限流规则
        
        Returns:
            True: 成功获取配额
            False: 超过限制或获取失败
        """
        key = self._get_rpd_key(provider, tool_type, account_name)
        now = int(time.time())
        request_id = str(uuid.uuid4())
        
        lua_script = """
        local key = KEYS[1]
        local window = tonumber(ARGV[1])
        local limit = tonumber(ARGV[2])
        local now = tonumber(ARGV[3])
        local request_id = ARGV[4]
        
        -- 清理过期请求（score < now - window）
        redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
        
        -- 获取当前窗口内的请求数
        local count = redis.call('ZCARD', key)
        
        -- 如果未超过限制，添加新请求
        if count < limit then
            redis.call('ZADD', key, now, request_id)  -- score=时间戳，value=请求ID
            redis.call('EXPIRE', key, window)  -- 设置过期时间
            return 1
        else
            return 0
        end
        """
        
        result = await self.redis.eval(
            lua_script, 1, key, rule.window_seconds, rule.limit, now, request_id
        )
        return result == 1
    
    async def _check_fixed_window(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str,
        rule: RateLimitRule
    ) -> bool:
        """
        检查固定窗口限流
        
        工作原理：
        - 使用 Redis Counter + TTL 实现固定窗口
        - 窗口大小由 window_seconds 决定（如 Suno 的 3 秒）
        - TTL 到期后，计数器自动重置
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
            account_name: 账号名称
            rule: 限流规则
        
        Returns:
            True: 未超过限制
            False: 超过限制
        """
        key = self._get_fixed_window_key(provider, tool_type, account_name)
        count = await self.redis.get(key)
        count = int(count) if count else 0
        return count < rule.limit
    
    async def _acquire_fixed_window(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str,
        rule: RateLimitRule
    ) -> bool:
        """
        获取固定窗口限流配额（原子操作）
        
        工作原理：
        - 使用 Lua Script 保证原子性
        - INCR 计数器
        - 如果 count == 1，设置 TTL（窗口到期自动重置）
        - 如果 count <= limit，返回成功
        - 如果 count > limit，回滚（DECR）并返回失败
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
            account_name: 账号名称
            rule: 限流规则
        
        Returns:
            True: 成功获取配额
            False: 超过限制或获取失败
        """
        key = self._get_fixed_window_key(provider, tool_type, account_name)
        
        lua_script = """
        local key = KEYS[1]
        local limit = tonumber(ARGV[1])
        local ttl = tonumber(ARGV[2])
        
        -- 计数器 +1
        local count = redis.call('INCR', key)
        
        -- 如果是第一次（count == 1），设置过期时间
        if count == 1 then
            redis.call('EXPIRE', key, ttl)
        end
        
        -- 判断是否超限
        if count <= limit then
            return 1  -- 成功
        else
            redis.call('DECR', key)  -- 回滚
            return 0  -- 失败
        end
        """
        
        result = await self.redis.eval(
            lua_script, 1, key, rule.limit, rule.window_seconds
        )
        return result == 1
    
    async def _check_concurrent(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str,
        rule: RateLimitRule
    ) -> bool:
        """
        检查并发限流
        
        工作原理：
        - 使用 Redis Counter 存储当前正在运行的任务数
        - 检查当前计数是否小于限制
        
        注意：
        - 不是基于时间窗口，而是基于运行状态
        - 任务完成时必须调用 release_rate_limit 释放配额
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
            account_name: 账号名称
            rule: 限流规则
        
        Returns:
            True: 未超过限制
            False: 超过限制
        """
        key = (self._get_shared_concurrent_key(provider, rule.shared_key, account_name)
               if rule.shared_key
               else self._get_concurrent_key(provider, tool_type, account_name))
        
        count = await self.redis.get(key)
        count = int(count) if count else 0
        return count < rule.limit
    
    async def _acquire_concurrent(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        account_name: str,
        rule: RateLimitRule
    ) -> bool:
        """
        获取并发限流配额（原子操作）
        
        工作原理：
        - 使用 Lua Script 保证原子性
        - 检查当前计数，如果未超限则 INCR
        - 每次 acquire 时刷新 TTL（确保只要还有任务在运行，TTL 就保持有效）
        
        TTL 机制：
        - TTL = 3600秒（1小时）作为安全网，防止计数器泄漏
        - 多用户、多任务场景：统一在 acquire/release 时刷新，不需要每个任务单独刷新
        
        Args:
            provider: 服务提供商
            tool_type: 工具类型
            account_name: 账号名称
            rule: 限流规则
        
        Returns:
            True: 成功获取配额
            False: 超过限制或获取失败
        """
        key = (self._get_shared_concurrent_key(provider, rule.shared_key, account_name)
               if rule.shared_key
               else self._get_concurrent_key(provider, tool_type, account_name))
        
        # TTL 设置为最大任务执行时间（1小时），作为安全网
        max_task_duration = 3600
        
        lua_script = """
        local key = KEYS[1]
        local limit = tonumber(ARGV[1])
        local ttl = tonumber(ARGV[2])
        
        local count = redis.call('GET', key) or 0
        count = tonumber(count)
        
        if count < limit then
            redis.call('INCR', key)
            -- 每次 acquire 时刷新 TTL，确保只要还有任务在运行，TTL 就保持有效
            -- 多用户、多任务场景：统一在 acquire/release 时刷新，不需要每个任务单独刷新
            redis.call('EXPIRE', key, ttl)
            return 1
        else
            return 0
        end
        """
        
        result = await self.redis.eval(lua_script, 1, key, rule.limit, max_task_duration)
        return result == 1
