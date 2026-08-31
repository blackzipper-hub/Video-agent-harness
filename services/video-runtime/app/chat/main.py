"""
Cuti-VideoChatAgent 入口：根据 ENVIRONMENT 加载 env，启动 FastAPI API。
借鉴 Cuti-VideoAgent 的 main 与部署脚本。
"""
import os
import sys
from pathlib import Path

# ==================== 环境配置（先于其他导入） ====================
from dotenv import load_dotenv

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DEFAULT_ENV = "development"
VALID_ENVS = ["local", "development", "production"]

environment = os.getenv("ENVIRONMENT", DEFAULT_ENV)
# Local multi-service development keeps shared provider credentials in the
# repository root.  Load it as a fallback only: service-specific environment
# files and process variables still take precedence and are never overwritten.
_agent_root = Path(__file__).resolve().parents[2]
_repository_root = _agent_root.parents[1]
if environment in ("local", "development"):
    load_dotenv(_repository_root / ".env")
if environment == "local":
    # 先加载 development 基底（含 OPENAI_API_KEY 等），避免仅存在 .env.development 时 local 无密钥
    load_dotenv(".env.development")
    load_dotenv(".env.local", override=True)
elif environment == "development":
    load_dotenv(".env.development")
elif environment == "production":
    load_dotenv(".env.production")
else:
    load_dotenv(".env.development")

import logging
import logging.handlers
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.chat.config import get_settings, EnvironmentType
from app.chat.exceptions import BusinessException
from app.chat.utils.i18n import set_current_language

# 日志：控制台 + logs/ 文件（midnight rotate）。
# 原先只有 basicConfig(stderr)，IDE terminal 滚动后业务日志丢失，无法事后排查。
# 与 Cuti-VideoAgent 的 logs/app.chat.log + logs/error.log 范式对齐。
_log_level = getattr(logging, (get_settings().LOG_LEVEL or "INFO").upper(), logging.INFO)
_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
os.makedirs(_log_dir, exist_ok=True)
_log_fmt = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_root_logger = logging.getLogger()
_root_logger.setLevel(_log_level)
for _h in list(_root_logger.handlers):
    _root_logger.removeHandler(_h)
_console_handler = logging.StreamHandler()
_console_handler.setLevel(_log_level)
_console_handler.setFormatter(_log_fmt)
_root_logger.addHandler(_console_handler)
_file_handler = logging.handlers.TimedRotatingFileHandler(
    filename=os.path.join(_log_dir, "app.chat.log"),
    when="midnight", interval=1, backupCount=7, encoding="utf-8",
)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_log_fmt)
_root_logger.addHandler(_file_handler)
_error_handler = logging.handlers.TimedRotatingFileHandler(
    filename=os.path.join(_log_dir, "error.log"),
    when="midnight", interval=1, backupCount=30, encoding="utf-8",
)
_error_handler.setLevel(logging.ERROR)
_error_handler.setFormatter(_log_fmt)
_root_logger.addHandler(_error_handler)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
# uvicorn --reload 用 watchfiles 监控目录变化。DEBUG 级别下每次写日志都会触发它
# 再打一行 DEBUG → 那行又写进 log → 再触发 → 自激循环（实测占总日志 93%）。
# 调到 WARNING 切断反馈环（仍保留 reload 失败时的告警）。
logging.getLogger("watchfiles").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
SERVICE_PREFIX = "/chat-v1/service"


@asynccontextmanager
async def service_lifespan(_app: FastAPI):
    """Initialize V2 and the legacy stage-service database pool it reuses."""
    from app.chat.v2.container import close_v2, initialize_v2
    from app.models.database import close_asyncpg_pool, init_asyncpg_pool

    settings = get_settings()
    v2 = await initialize_v2(settings)
    legacy_stage_pool_initialized = False
    try:
        # Deep Agent V2 uses the legacy outline/character/scene/shot services
        # as atomic capabilities. Those services use app.models.database's
        # asyncpg pool, which is separate from the V2 repository pool.
        if v2 is not None:
            await init_asyncpg_pool()
            legacy_stage_pool_initialized = True
        yield
    finally:
        await close_v2()
        if legacy_stage_pool_initialized:
            await close_asyncpg_pool()


service_app = FastAPI(
    title="Cuti-VideoChatAgent API",
    description="Agent Router + 对话层 API",
    version="0.1.0",
)

service_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@service_app.middleware("http")
async def language_middleware(request: Request, call_next):
    """语言中间件：从 X-App-Language / X-Language 设置当前请求语言（与 VideoAgent 一致）。"""
    language = request.headers.get("X-App-Language") or request.headers.get("X-Language", "en")
    set_current_language(language)
    response = await call_next(request)
    return response


@service_app.get("/health")
def health_check():
    """健康检查（部署与探活用）"""
    return {"status": "healthy", "service": "videochat-agent"}


@service_app.get("/")
def root():
    return {
        "message": "Cuti-VideoChatAgent API",
        "docs": f"{SERVICE_PREFIX}/docs",
    }


@service_app.exception_handler(BusinessException)
async def business_exception_handler(request: Request, exc: BusinessException):
    """业务异常：与 VideoAgent 一致，返回 200 + code/message（如 INVALID_TOKEN 10110）。"""
    lang = "en" if request.headers.get("Accept-Language", "zh").startswith("en") else "zh"
    if exc.lang != lang:
        exc.detail = exc.error_code.message(lang)
    return JSONResponse(
        status_code=200,
        content={"code": exc.error_code.code, "message": exc.detail, "data": None},
    )


from app.chat.api.v2 import v2_api
from app.chat.api.studio import studio_api

service_app.include_router(v2_api)
service_app.include_router(studio_api)

app = FastAPI(
    title="Cuti-VideoChatAgent Gateway",
    description="Mounts chat service under /chat-v1/service",
    version="0.1.0",
    lifespan=service_lifespan,
)


@app.get("/")
def gateway_root():
    return {
        "message": "Cuti-VideoChatAgent Gateway",
        "service_root": SERVICE_PREFIX,
        "docs": f"{SERVICE_PREFIX}/docs",
    }


app.mount(SERVICE_PREFIX, service_app)


# ==================== 开发/生产启动 ====================
if __name__ == "__main__":
    import uvicorn

    env = DEFAULT_ENV
    if len(sys.argv) > 1:
        env_arg = sys.argv[1].lower()
        if env_arg in VALID_ENVS:
            env = env_arg
        else:
            print(f"Invalid environment: {env_arg}. Valid: {', '.join(VALID_ENVS)}")
            sys.exit(1)

    os.environ["ENVIRONMENT"] = env
    settings = get_settings()
    logger.info("Starting Cuti-VideoChatAgent in %s", env.upper())
    uvicorn.run(
        "app.chat.main:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=(env != "production"),
        log_level=(settings.LOG_LEVEL or "INFO").lower(),
    )
