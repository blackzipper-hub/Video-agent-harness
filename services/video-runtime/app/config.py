import os
from enum import Enum
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
import secrets
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

class EnvironmentType(str, Enum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    PRODUCTION = "production"

class Settings(BaseSettings):
    """Base settings class with common configuration."""
    
    # Environment settings
    ENVIRONMENT: EnvironmentType = Field(
        default=EnvironmentType.DEVELOPMENT,
        description="Current environment"
    )
    DEBUG: bool = Field(
        default=False, 
        description="Debug mode flag"
    )
    
    # Server settings
    SERVER_HOST: str = Field(
        default="0.0.0.0",
        description="Server host address"
    )
    SERVER_PORT: int = Field(
        default=8000,
        description="Server port number"
    )
    

    
    # OpenAI API settings
    OPENAI_API_KEY: str = Field(
        default="", 
        description="OpenAI API key"
    )
    OPENAI_API_KEY_FALLBACK: str = Field(
        default="",
        description="Backup OpenAI API key used for one retry after a primary-key failure",
    )
    SUBTITLE_TRANSCRIPTION_MODEL: str = Field(
        default="whisper-1",
        description="OpenAI speech-to-text model used by media.transcribe",
    )
    SUBTITLE_TRANSCRIPTION_MAX_BYTES: int = Field(
        default=25 * 1024 * 1024,
        description="Maximum extracted audio size accepted for subtitle transcription",
    )
    # LLM quality tier for role-based model selection (prompts/llm_model_profiles.py)
    LLM_QUALITY: str = Field(
        default="best",
        description="LLM quality tier: best | common | economy",
    )
    
    # Pollo AI settings
    POLLO_API_KEY: str = Field(
        default="", 
        description="Pollo AI API key for video generation"
    )
    
    # Google GenAI settings
    GOOGLE_API_KEY: str = Field(
        default="", 
        description="Google GenAI API key"
    )

    WAVESPEED_API_KEY: str = Field(
        default="", 
        description="WaveSpeed API key"
    )

    SUNO_API_KEY: str = Field(
        default="", 
        description="Suno API key"
    )
    # LangSmith settings
    LANGSMITH_API_KEY: Optional[str] = Field(
        default=None,
        description="LangSmith API key for tracing"
    )
    LANGSMITH_ENDPOINT: str = Field(
        default="https://api.smith.langchain.com",
        description="LangSmith API endpoint"
    )
    LANGSMITH_PROJECT: Optional[str] = Field(
        default="cartoonbook",
        description="LangSmith project name"
    )
    LANGSMITH_TRACING: bool = Field(
        default=False,
        description="Enable LangSmith tracing. Default False so open-source/self-hosted deployments do not phone home; our envs set LANGSMITH_TRACING=true explicitly in .env."
    )
    # Logging settings
    LOG_LEVEL: str = Field(
        default="INFO", 
        description="Logging level"
    )

    # Static files settings
    STATIC_PHOTOS_DIR: str = Field(
        default="static/photos",
        description="Directory for storing static photos"
    )
    # 视频水印：shot 入库由 Cuti-Media-Service ensure-on-s3 叠加（assets/watermark.png）；本路径仅用于本地 add_watermark_to_video_async
    VIDEO_WATERMARK_IMAGE_PATH: str = Field(
        default="app/assets/watermark.png",
        description="Local FFmpeg watermark path (MSC ensure-on-s3 uses Media Service WATERMARK_IMAGE_PATH)"
    )
    
    # S3 settings
    S3_BUCKET_NAME: str = Field(
        default="",
        description="S3 bucket name for file storage (set via env/.env)"
    )
    
    # CDN settings
    CDN_DOMAIN: str = Field(
        default="",
        description="CDN / public base URL for serving stored files (set via env/.env)"
    )

    # JWT settings
    JWT_SECRET_KEY: str = Field(
        default="your-super-secret-jwt-key-change-this-in-production-please-make-it-long-and-random",
        description="JWT secret key"
    )
    JWT_ALGORITHM: str = Field(
        default="HS256",
        description="JWT algorithm"
    )
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(
        default=60 * 24 * 7,  # 7 days
        description="JWT access token expiration time in minutes"
    )

    # Service-to-service auth settings
    CUTI_SERVICE_ENABLED: bool = Field(
        default=True,
        description="Enable bearer-token service access for trusted callers like OpenClaw"
    )
    CUTI_SERVICE_TOKEN: str = Field(
        default="",
        description="Bearer token for trusted service callers (set via env/.env; empty disables service auth)"
    )
    CUTI_SERVICE_DEFAULT_USER_ID: Optional[str] = Field(
        default=None,
        description="Fallback user_id for service callers / local single-user mode (set via env/.env)"
    )

    # In-process Chat Agent (merged monorepo). Default False keeps this service byte-identical
    # to the standalone VideoAgent; the monorepo deployment sets True so the single process also
    # serves the Chat SSE router at /chat-v1/service (no separate VideoChatAgent process/HTTP hop).
    ENABLE_CHAT_AGENT: bool = Field(
        default=False,
        description="Mount the in-process Chat Agent router at /chat-v1/service. Default False = standalone VideoAgent behavior; True = merged single-process monorepo."
    )

    VIDEO_AGENT_BACKEND: Literal["deepseek"] = Field(
        default="deepseek",
        description="DeepSeek is the only Agent coordinator; Cuti owns video execution.",
    )
    VIDEO_INCREMENTAL_ENGINE_ENABLED: bool = Field(
        default=True,
        description="Enable project versions, dependency impact preview, and atomic partial rebuilds.",
    )

    # 自托管默认「自动继续」：ChatAgent 委托入队时写 user_option.full_auto=True。
    # 门闩仍 interrupt，后端排 15s（智能裁切 60s）延迟 auto_resume；前端展示倒计时与「取消自动继续」。
    # 默认 False = 仅手动点继续。不要与 state.full_auto（SmartTest 跳过门闩）混淆。
    VIDEO_FULL_AUTO: bool = Field(
        default=False,
        description="When True, enqueue with user_option.full_auto so interrupt gates auto-resume after countdown (15s / 60s smart-clip). Default False requires manual Continue."
    )

    # Single-user / local mode (for open-source / self-hosted deployments)
    # 默认 False = 鉴权行为不变（仍需 cookie 或 service token）；
    # 置 True 时，无 token 的请求回退为 CUTI_SERVICE_DEFAULT_USER_ID，免登录本地自托管即可用。
    LOCAL_SINGLE_USER_MODE: bool = Field(
        default=False,
        description="When True, requests without auth fall back to CUTI_SERVICE_DEFAULT_USER_ID (no login needed). Default False keeps existing auth behavior."
    )

    # Base URL for external access
    BASE_URL: str = Field(
        default="http://localhost:8000",
        description="Base URL for external access to the application (set via env/.env)"
    )
    
    # Database URL
    DATABASE_URL: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/storybook",
        description="Database connection URL"
    )

    # Redis settings
    REDIS_URL: str = Field(
        default="redis://127.0.0.1:6379/0",
        description="Redis connection URL (e.g., redis://:password@host:port/db or redis://host:port/db)"
    )
    
    # AWS settings
    AWS_REGION: str = Field(
        default="ap-southeast-2",
        description="AWS region (uses IAM role on EC2, no credentials needed)"
    )

    # S3-compatible storage endpoint (for open-source / self-hosted: point to MinIO)
    # 默认 None = 使用真实 AWS S3（IAM role），行为不变；
    # 设为如 http://minio:9000 即切到 S3 兼容存储（MinIO），配合下面的 access key + path-style。
    S3_ENDPOINT_URL: Optional[str] = Field(
        default=None,
        description="Custom S3-compatible endpoint (e.g. http://minio:9000). None = real AWS S3."
    )
    S3_ACCESS_KEY_ID: Optional[str] = Field(
        default=None,
        description="Access key for S3-compatible storage (only used when S3_ENDPOINT_URL is set)."
    )
    S3_SECRET_ACCESS_KEY: Optional[str] = Field(
        default=None,
        description="Secret key for S3-compatible storage (only used when S3_ENDPOINT_URL is set)."
    )
    
    # ==================== Provider 后端开关 ====================
    # 对象存储后端：s3（集群成品）| local（自托管 / compose 本地盘）
    STORAGE_BACKEND: str = Field(
        default="s3",
        description="Object storage backend: 's3' (AWS/MinIO) or 'local' (filesystem; no object store needed)."
    )
    # 账号/Key 只从环境变量读。保留该字段是为了兼容已有 Helm / compose（值只能是 env）。
    ACCOUNT_BACKEND: str = Field(
        default="env",
        description="Provider key backend. Only 'env' is supported (reads keys from environment variables).",
    )

    @field_validator("ACCOUNT_BACKEND")
    @classmethod
    def _account_backend_is_env(cls, value: str) -> str:
        backend = (value or "env").strip().lower()
        if backend != "env":
            return "env"
        return backend
    # 本地存储（STORAGE_BACKEND=local）：文件落地目录 + 对外访问基址
    LOCAL_STORAGE_DIR: str = Field(
        default="./data/uploads",
        description="Directory for local file storage when STORAGE_BACKEND=local."
    )
    # 对外访问基址（用于生成本地存储文件的可访问 URL，如 http://localhost:8000）。留空则回退到 CDN_DOMAIN。
    PUBLIC_BASE_URL: str = Field(
        default="",
        description="Public base URL for locally-stored files (e.g. http://localhost:8000). Files served at {PUBLIC_BASE_URL}/files/*."
    )

    # Cuti-Media-Service (EKS internal ALB)
    MEDIA_SERVICE_URL: str = Field(
        default="http://localhost:8080",
        description="Cuti-Media-Service base URL (EKS internal ALB or local)"
    )

    # ==================== Prompt Shield（对外响应脱敏） ====================
    # 所有 shield 能力默认关闭，确保代码上线不改变既有行为。
    # 详见 Cuti-Agent-Learning/PROMPT_SECURITY_CUTI_ANALYSIS.md
    SHIELD_COMPANION_SSE_PUBLIC_SHAPE_ENABLED: bool = Field(
        default=False,
        description="VA companion SSE 使用公开形状（tool→message_key，不下发 args/raw result）P0-A"
    )
    SHIELD_CONVERSATION_DETAIL_SANITIZE_ENABLED: bool = Field(
        default=False,
        description="VA /conversation/detail 返回消息走字段白名单（P0-D）"
    )

    # ==================== Music Smart Clip（智能剪辑灰度开关） ====================
    # 默认开启；线上回滚/排障时可设为 false 让 gate_after_music 不再带 smart_clip payload。
    # 注：music_generation_node 仍会同步分析并把 smart_clip 写入 additional_data（DB 留痕），
    # 仅控制是否在 interrupt 给前端。前端拿不到 smart_clip 时 SmartClipPanel 不渲染（保留原版）。
    MUSIC_SMART_CLIP_ENABLED: bool = Field(
        default=True,
        description="智能剪辑总开关：False 时 gate_after_music interrupt 不带 smart_clip payload",
    )

    # ==================== Stage runtime（deep agent + 本地 artifact JSON） ====================
    # Creative LLM stages always use create_deep_agent + skills (no VIA_DEEP_AGENT flags).
    # 详见 docs/stage-artifact-layout.md、stage_runtime/registry.py。媒体仍走 S3/CDN。
    STAGE_ARTIFACT_ROOT: str = Field(
        default="data/run_workspaces",
        description="Per-run workspace root relative to services/agent (not under kit/). Absolute paths allowed.",
    )
    STAGE_ARTIFACT_MIRROR_S3: bool = Field(
        default=False,
        description="When True, optionally mirror stage JSON artifacts to S3 (not used on agent hot path). Default False.",
    )

    model_config = SettingsConfigDict(
        env_file=[".env.production", ".env.development", ".env.local", ".env"],  # 优先级顺序
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

class LocalSettings(Settings):
    """Local development environment settings."""
    
    ENVIRONMENT: EnvironmentType = EnvironmentType.LOCAL
    DEBUG: bool = True
    LOG_LEVEL: str = "DEBUG"
    SERVER_PORT: int = 9002  # 本地环境使用不同端口，避免与其他环境冲突
    
    # 本地环境数据库配置
    DATABASE_URL: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/storybook_dev",
        description="Local database connection URL"
    )
    
    class Config:
        env_file = ".env.local"

class DevelopmentSettings(Settings):
    """Development environment settings."""
    
    DEBUG: bool = True
    LOG_LEVEL: str = "DEBUG"
    SERVER_PORT: int = 9001  # 开发环境使用不同端口，避免与生产环境冲突
    
    # 开发环境数据库配置
    DATABASE_URL: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/storybook_dev",
        description="Development database connection URL"
    )
    
    class Config:
        env_file = ".env"

class ProductionSettings(Settings):
    """Production environment settings."""
    
    ENVIRONMENT: EnvironmentType = EnvironmentType.PRODUCTION
    DEBUG: bool = False
    LOG_LEVEL: str = "DEBUG"  # 与 dev 一致，便于排查问题；可通过 .env.production 覆盖
    SERVER_PORT: int = 8000  # 生产环境使用标准端口
    
    class Config:
        env_file = ".env.production"

@lru_cache()
def get_settings() -> Settings:
    """
    Get the appropriate settings based on the ENVIRONMENT variable.
    Uses LRU cache to prevent reloading the settings multiple times.
    """
    environment = os.getenv("ENVIRONMENT", EnvironmentType.DEVELOPMENT)
    
    settings_map: Dict[str, Any] = {
        EnvironmentType.LOCAL: LocalSettings,
        EnvironmentType.DEVELOPMENT: DevelopmentSettings,
        EnvironmentType.PRODUCTION: ProductionSettings,
    }
    
    settings_class = settings_map.get(environment, DevelopmentSettings)
    return settings_class()

# Create a singleton settings instance
settings = get_settings()
