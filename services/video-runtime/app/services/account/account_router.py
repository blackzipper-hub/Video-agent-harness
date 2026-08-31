"""
账号路由器 - 负责请求路由、故障转移、限流
"""
import asyncio
from typing import Callable, Any
import logging

from ...models.tool_enums import ToolProvider, ToolType
from .account_manager import AccountConfigLoader
from .rate_limiter import ModelRateLimiter, RateLimitExceededError

logger = logging.getLogger(__name__)


class AccountRouter:
    """
    账号路由器 - 集成限流检查
    
    功能：
    - 路由 API 请求到最优账号（按优先级和限流状态）
    - 集成 ModelRateLimiter 进行限流检查
    - 支持多账号故障转移（如果某个账号限流，尝试下一个）
    - 自动处理异常和配额释放
    
    工作流程：
    1. 从 AppConfig 加载账号列表
    2. 对每个账号（按优先级）：
       a. 检查限流状态（check_rate_limit）
       b. 如果通过，获取限流配额（acquire_rate_limit）
       c. 执行 API 请求
       d. 更新 TPM 使用量（如果适用）
       e. 释放 CONCURRENT 配额（如果失败或取消）
    3. 如果所有账号都限流，抛出 RateLimitExceededError
    """
    
    def __init__(self, account_config_loader: AccountConfigLoader, rate_limiter: ModelRateLimiter):
        """
        初始化账号路由器
        
        Args:
            account_config_loader: 账号配置加载器（负责从 AppConfig 加载账号配置）
            rate_limiter: 限流器（必须提供）
        """
        self.account_config_loader = account_config_loader
        self.rate_limiter = rate_limiter
    
    def _get_provider_name(self, provider: ToolProvider) -> str:
        """
        将 ToolProvider enum 转换为 AppConfig 中的 provider 名称
        
        Args:
            provider: ToolProvider 枚举值
        
        Returns:
            AppConfig 中的 provider 名称（如 "google", "wavespeed"）
        """
        provider_name_map = {
            ToolProvider.GOOGLE: "google",
            ToolProvider.WAVESPEED: "wavespeed",
            ToolProvider.SUNO: "suno",
            ToolProvider.OPENAI: "openai",
        }
        return provider_name_map.get(provider)
    
    async def route_tool_request(
        self,
        provider: ToolProvider,
        tool_type: ToolType,
        request_func: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        路由Tool请求到最优账号，考虑 Model 级别限流
        
        流程：
        1. 从 AppConfig 加载账号列表
        2. 对每个账号（按优先级）：
           a. 检查限流状态
           b. 如果通过，获取限流配额
           c. 执行请求
           d. 释放限流配额（如果失败）
        3. 如果所有账号都限流，抛出异常
        
        Args:
            provider: 服务提供商（ToolProvider enum）
            tool_type: 工具类型（ToolType enum），必须传入
            request_func: 实际的请求函数，签名: async def func(api_key, *args, **kwargs)
            *args, **kwargs: 传递给request_func的其他参数
        
        Returns:
            请求函数的返回结果
        """
        # 加载账号列表
        accounts_dict = await self.account_config_loader._load_accounts_from_appconfig()
        provider_name = self._get_provider_name(provider)
        
        if not provider_name or provider_name not in accounts_dict:
            raise ValueError(f"Provider {provider.value} not configured")
        
        accounts = accounts_dict[provider_name]
        
        # 尝试每个账号（按优先级）
        for account in accounts:
            # 检查限流
            can_proceed = await self.rate_limiter.check_rate_limit(
                provider, tool_type, account.name
            )

            if not can_proceed:
                logger.debug(
                    f"Account {account.name} rate limited for {tool_type.value}, "
                    f"trying next account"
                )
                continue

            # 获取限流配额（CONCURRENT 等策略会占用配额）
            acquired = await self.rate_limiter.acquire_rate_limit(
                provider, tool_type, account.name
            )

            if not acquired:
                logger.debug(
                    f"Failed to acquire rate limit for {account.name} "
                    f"({tool_type.value}), trying next account"
                )
                continue

            # 执行请求；无论成功、失败、取消，都在 finally 中释放 CONCURRENT 配额，避免 dev 只增不释
            # ⚠️ 限流配额在请求开始时已占用；请求结束后必须释放（否则 CONCURRENT 计数会一直涨）
            try:
                result = await request_func(account.api_key, *args, **kwargs)

                # 如果是 Google API，提取 token 使用量并更新 TPM 计数
                if provider == ToolProvider.GOOGLE and hasattr(result, 'usage_metadata'):
                    usage_metadata = result.usage_metadata
                    input_tokens = usage_metadata.prompt_token_count or 0
                    await self.rate_limiter.update_tpm_usage(
                        provider, tool_type, account.name, input_tokens
                    )

                return result
            finally:
                # CONCURRENT 策略：任务结束（成功/失败/取消）都要释放，否则计数只增不降
                await self.rate_limiter.release_rate_limit(
                    provider, tool_type, account.name
                )

        # 所有账号都限流
        raise RateLimitExceededError(
            f"All accounts rate limited for provider {provider.value}, tool_type {tool_type.value}"
        )


# 全局账号路由器实例
_account_router = None


async def get_account_router() -> AccountRouter:
    """
    获取账号路由器（单例）
    
    注意：
    - AccountConfigLoader 必须在应用启动时通过 lifespan 初始化
    - ModelRateLimiter 会自动初始化并加载限流规则
    - 如果 AccountConfigLoader 未初始化，会尝试自动初始化（降级策略）
    
    Returns:
        AccountRouter 实例
    """
    global _account_router
    if _account_router is None:
        from .account_manager import AccountConfigLoader
        from .rate_limiter import ModelRateLimiter
        
        account_config_loader = AccountConfigLoader()
        # 检查 AccountConfigLoader 是否已初始化
        if not account_config_loader._initialized:
            # 尝试自动初始化（降级策略）
            # 这可以处理启动时初始化失败的情况
            logger.warning(
                "AccountConfigLoader not initialized. "
                "Attempting to initialize automatically (fallback strategy)..."
            )
            try:
                await account_config_loader.initialize()
                logger.info("✅ AccountConfigLoader auto-initialized successfully")
            except Exception as e:
                logger.error(
                    f"❌ Failed to auto-initialize AccountConfigLoader: {e}",
                    exc_info=True
                )
                raise RuntimeError(
                    "AccountConfigLoader not initialized and auto-initialization failed. "
                    "Please ensure AccountConfigLoader.initialize() is called in application lifespan, "
                    "or check Redis/AppConfig configuration. "
                    f"Error: {str(e)}"
                )
        
        # 初始化 ModelRateLimiter
        rate_limiter = ModelRateLimiter()
        await rate_limiter.initialize()
        
        # 加载限流规则：
        # - ACCOUNT_BACKEND=env（开源自托管）：不依赖 AWS AppConfig，使用空规则
        #   （单用户场景无需跨账号限流；否则 boto3 会因缺少 AWS 凭据抛 NoCredentialsError）。
        # - 否则（默认 appconfig）：从 AWS AppConfig 加载。
        from ...config import settings
        backend = (getattr(settings, "ACCOUNT_BACKEND", "appconfig") or "appconfig").lower()
        if backend == "env":
            await rate_limiter.load_rules_from_config({"providers": {}})
            logger.info("ModelRateLimiter initialized with empty rules (ACCOUNT_BACKEND=env, self-hosted)")
        else:
            from ..aws.appconfig_service import get_appconfig_service
            appconfig_service = get_appconfig_service()
            config = await asyncio.to_thread(
                appconfig_service.get_configuration,
                "account-config"
            )
            await rate_limiter.load_rules_from_config(config)
            logger.info("ModelRateLimiter initialized and rules loaded from AppConfig")
        
        _account_router = AccountRouter(account_config_loader, rate_limiter)
    return _account_router
