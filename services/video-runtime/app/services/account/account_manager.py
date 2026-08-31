"""
账号配置加载器 - 负责从 AppConfig 加载账号配置

功能：
- 从 AWS AppConfig 加载账号配置
- 提供 Redis 缓存机制（减少 AppConfig 调用）
- 解析配置并创建 Account 对象

注意：
- 不再负责账号选择、状态管理（由 AccountRouter + ModelRateLimiter 处理）
- 只负责配置加载和解析
"""
from typing import Dict, List
from dataclasses import dataclass
import asyncio
import logging
import json

from ..aws.appconfig_service import get_appconfig_service
from ..redis.connection import get_redis_client
from ...config import get_settings

logger = logging.getLogger(__name__)

# AppConfig Profile名称
ACCOUNT_CONFIG_PROFILE = "account-config"

# 常量配置
ACCOUNT_CONFIG_CACHE_TTL = 60  # AppConfig配置缓存时间（秒），默认1分钟


@dataclass
class Account:
    """
    账号信息（配置信息）
    
    注意：限流由 ModelRateLimiter 处理，不再使用 max_concurrent 字段
    """
    provider: str  # AppConfig中的provider名称（如"google", "wavespeed"）
    name: str
    api_key: str
    priority: int = 0


class AccountConfigLoader:
    """
    账号配置加载器 - 单例模式，负责从 AppConfig 加载账号配置
    
    功能：
    - 从 AWS AppConfig 加载账号配置
    - 提供 Redis 缓存机制（减少 AppConfig 调用）
    - 解析配置并创建 Account 对象
    
    注意：
    - 不再负责账号选择、状态管理（由 AccountRouter + ModelRateLimiter 处理）
    - 只负责配置加载和解析
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        # 单例模式：只在第一次创建时初始化，后续调用不重置状态
        if not hasattr(self, '_initialized'):
            self.redis = None  # 使用共享的Redis客户端
            self._initialized = False
            # 环境前缀（在initialize中设置）
            self._env_prefix = None
    
    async def initialize(self):
        """
        初始化（启动时调用）
        
        功能：
        - 连接 Redis（用于配置缓存）
        - 设置环境前缀
        """
        if self._initialized:
            return

        backend = (getattr(get_settings(), "ACCOUNT_BACKEND", "appconfig") or "appconfig").lower()
        if backend == "env":
            self.redis = None
            self._env_prefix = self._get_env_prefix()
            self._initialized = True
            logger.info("AccountConfigLoader initialized without Redis (ACCOUNT_BACKEND=env)")
            return
        
        # 使用共享的Redis客户端（使用str模式，避免bytes转换）
        try:
            self.redis = await get_redis_client(decode_responses=True)
            logger.info("AccountConfigLoader: Using shared Redis client (str mode)")
        except Exception as e:
            logger.error(f"AccountConfigLoader: Failed to get Redis client: {e}")
            raise
        
        # 初始化环境前缀
        self._env_prefix = self._get_env_prefix()
        
        self._initialized = True
        logger.info("AccountConfigLoader: Initialized")
    
    async def shutdown(self):
        """
        关闭（停止时调用）
        
        注意：不关闭Redis连接，因为它是共享的，由connection.py管理
        """
        if not self._initialized:
            return
        
        self.redis = None
        self._initialized = False
        logger.info("AccountConfigLoader: Shutdown complete")
    
    def _get_env_prefix(self) -> str:
        """获取环境前缀（在initialize中调用）"""
        settings = get_settings()
        env = settings.ENVIRONMENT.value  # "local", "development", "production"
        
        env_prefix_map = {
            "local": "local",
            "development": "dev",
            "production": "prod"
        }
        return env_prefix_map.get(env, "dev")
    
    # provider 名称 -> 环境变量名（ACCOUNT_BACKEND=env 时直接读环境变量里的各家 Key）
    _ENV_KEY_MAP = {
        "openai": "OPENAI_API_KEY",
        "google": "GOOGLE_API_KEY",
        "wavespeed": "WAVESPEED_API_KEY",
        "suno": "SUNO_API_KEY",
        "pollo": "POLLO_API_KEY",
    }

    def _build_config_from_env(self) -> dict:
        """从环境变量构造账号配置（单用户自托管）。每个有 Key 的 provider 生成一个高优先级账号。"""
        import os
        providers = {}
        for provider, env_name in self._ENV_KEY_MAP.items():
            api_key = os.getenv(env_name)
            if api_key:
                providers[provider] = {
                    "accounts": [
                        {"name": f"{provider}-env", "api_key": api_key, "priority": 100, "status": "active"}
                    ]
                }
        return {"providers": providers}

    async def _load_accounts_from_appconfig(self) -> Dict[str, List[Account]]:
        """
        加载账号配置。
        - ACCOUNT_BACKEND=env：直接读环境变量里的各家 Key（自托管，无需 AWS AppConfig）
        - 否则（默认 appconfig）：从 AWS AppConfig 加载（带 Redis 缓存）
        """
        backend = (getattr(get_settings(), "ACCOUNT_BACKEND", "appconfig") or "appconfig").lower()
        if backend == "env":
            return self._parse_accounts_from_config(self._build_config_from_env())

        if not self.redis:
            # 如果没有Redis，直接加载（不应该发生，但防御性编程）
            return await self._load_from_appconfig_direct()
        
        cache_key = f"cuti-videoagent:{self._env_prefix}:account:config"
        
        try:
            # 1. 尝试从Redis缓存获取
            cached_config = await self.redis.get(cache_key)
            if cached_config:
                import json
                try:
                    config = json.loads(cached_config)
                    logger.debug("AccountConfigLoader: Using cached config from Redis")
                    return self._parse_accounts_from_config(config)
                except json.JSONDecodeError:
                    logger.warning("AccountConfigLoader: Failed to parse cached config, reloading from AppConfig")
            
            # 2. 缓存不存在或无效，从AppConfig加载
            accounts_dict = await self._load_from_appconfig_direct()
            
            # 3. 更新Redis缓存
            import json
            config_json = json.dumps({
                "providers": {
                    provider: {
                        "accounts": [
                            {
                                "name": acc.name,
                                "api_key": acc.api_key,
                                "priority": acc.priority
                            }
                            for acc in accounts
                        ]
                    }
                    for provider, accounts in accounts_dict.items()
                }
            })
            await self.redis.setex(cache_key, ACCOUNT_CONFIG_CACHE_TTL, config_json)
            logger.debug(f"AccountConfigLoader: Updated config cache (TTL: {ACCOUNT_CONFIG_CACHE_TTL}s)")
            
            return accounts_dict
            
        except Exception as e:
            logger.error(f"Failed to load accounts from AppConfig: {e}")
            # 如果缓存失败，尝试直接加载（降级策略）
            try:
                return await self._load_from_appconfig_direct()
            except:
                raise
    
    async def _load_from_appconfig_direct(self) -> Dict[str, List[Account]]:
        """直接从AppConfig加载配置（不经过缓存）"""
        appconfig_service = get_appconfig_service()
        config = await asyncio.to_thread(
            appconfig_service.get_configuration,
            ACCOUNT_CONFIG_PROFILE
        )
        return self._parse_accounts_from_config(config)
    
    def _parse_accounts_from_config(self, config: dict) -> Dict[str, List[Account]]:
        """解析配置并创建Account对象"""
        accounts: Dict[str, List[Account]] = {}
        for provider_name, provider_config in config.get("providers", {}).items():
            accounts[provider_name] = []
            for acc_config in provider_config.get("accounts", []):
                # 检查status，只加载active的账号（如果配置了status字段）
                status = acc_config.get("status", "active")
                if status != "active":
                    logger.debug(f"Skipping account {acc_config['name']} with status: {status}")
                    continue
                
                api_key = acc_config.get("api_key")
                if not api_key:
                    logger.warning(f"API key not found for {acc_config['name']} in AppConfig")
                    continue
                
                account = Account(
                    provider=provider_name,
                    name=acc_config["name"],
                    api_key=api_key,
                    priority=acc_config.get("priority", 0)
                )
                accounts[provider_name].append(account)
        
        # 按优先级排序
        for provider in accounts:
            accounts[provider].sort(key=lambda x: x.priority, reverse=True)
        
        return accounts
    
