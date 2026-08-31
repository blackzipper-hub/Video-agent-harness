"""
AWS AppConfig服务
用于从AWS AppConfig获取配置
"""
import os
import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.exceptions import ClientError
    AWS_AVAILABLE = True
except ImportError:
    AWS_AVAILABLE = False

# AppConfig常量配置
APPCONFIG_APPLICATION = "cuti-videoagent"


class AppConfigService:
    """AWS AppConfig服务 - 单例模式"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not AWS_AVAILABLE:
            logger.warning("boto3 not available, AppConfigService will not work")
        self._client = None
    
    @property
    def client(self):
        """懒加载boto3 client"""
        if self._client is None and AWS_AVAILABLE:
            from app.config import get_settings
            self._client = boto3.client('appconfigdata', region_name=get_settings().AWS_REGION)
        return self._client
    
    def _get_environment(self) -> str:
        """
        根据ENVIRONMENT环境变量获取AppConfig Environment名称
        
        Returns:
            AppConfig Environment名称: "dev" 或 "prod"
        """
        environment = os.getenv("ENVIRONMENT", "development")
        # 将ENVIRONMENT值转换为AppConfig的environment名称
        # development -> dev, production -> prod
        if environment == "production":
            return "prod"
        else:
            return "dev"
    
    def get_configuration(
        self,
        profile: str,
        environment: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        从AWS AppConfig获取配置（同步方法，因为boto3是同步的）
        
        Args:
            profile: Configuration Profile名称（如 "account-config"）
            environment: AppConfig Environment名称（可选，默认根据ENVIRONMENT环境变量自动确定）
        
        Returns:
            配置字典（JSON解析后的内容）
        
        Raises:
            RuntimeError: 如果boto3不可用
            ClientError: 如果AWS API调用失败
            json.JSONDecodeError: 如果配置不是有效的JSON
        """
        if not AWS_AVAILABLE:
            raise RuntimeError("boto3 not available, cannot use AppConfig")
        
        if environment is None:
            environment = self._get_environment()
        
        try:
            # 获取配置token
            token_response = self.client.start_configuration_session(
                ApplicationIdentifier=APPCONFIG_APPLICATION,
                EnvironmentIdentifier=environment,
                ConfigurationProfileIdentifier=profile
            )
            token = token_response['InitialConfigurationToken']
            
            # 获取配置内容
            config_response = self.client.get_latest_configuration(
                ConfigurationToken=token
            )
            config_content = config_response['Configuration'].read()
            
            # 解析JSON配置
            config = json.loads(config_content.decode('utf-8'))
            
            logger.info(f"AppConfigService: Loaded configuration from profile '{profile}' (environment: {environment})")
            return config
            
        except ClientError as e:
            logger.error(f"Failed to load config from AppConfig: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON config: {e}")
            raise
    


def get_appconfig_service() -> AppConfigService:
    """获取AppConfigService单例实例"""
    return AppConfigService()
