import os
from dotenv import load_dotenv
# ==================== 环境配置 ====================
# 环境常量
DEFAULT_ENV = "development"
VALID_ENVS = ["local", "development", "production"]

# 根据ENVIRONMENT变量加载对应的环境文件
environment = os.getenv("ENVIRONMENT", DEFAULT_ENV)
if environment == "local":
    load_dotenv(".env.local")
elif environment == "development":
    load_dotenv(".env.development")
elif environment == "production":
    load_dotenv(".env.production")
else:
    load_dotenv(".env.development")

import sys
import time
import logging
import mimetypes

# 第三方库导入
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

# 本地导入
from .api.endpoints import router as api_router
from .video_runtime.api import (
    get_runtime,
    router as video_runtime_router,
    set_identity_resolver,
)
from .video_runtime.identity import CutiIdentityResolver
from .api.agent.conversation_routes import router as conversation_router
from .api.agent.agent_router_endpoints import router as agent_router_api
# 运营/测试 admin（ops-admin）：闭源(ee)能力，开源快照不包含这些文件。
# 用 try/except 保证在这些文件缺失（开源版）时应用仍能正常启动。
try:
    from ee.admin_api.error_tracking_endpoints import router as admin_error_tracking_router
    from ee.admin_api.curated_style_prompts_endpoints import router as admin_curated_style_prompts_router
    from ee.admin_api.monitoring_endpoints import router as admin_monitoring_router
    from ee.admin_api.run_content_endpoints import router as admin_run_content_router
    from ee.admin_api.smart_testing_endpoints import router as admin_smart_testing_router
    from ee.admin_api.shield_events_endpoints import router as admin_shield_events_router
    _OPS_ADMIN_ROUTERS = [
        admin_error_tracking_router,
        admin_curated_style_prompts_router,
        admin_monitoring_router,
        admin_run_content_router,
        admin_smart_testing_router,
        admin_shield_events_router,
    ]
except ImportError:
    _OPS_ADMIN_ROUTERS = []

from .config import settings, EnvironmentType
from .exceptions import BusinessException
from .utils.logging_config import setup_logging, get_logger
from .utils.i18n import set_current_language

# ==================== 应用初始化 ====================
# 添加WebP MIME类型支持
mimetypes.add_type('image/webp', '.webp')


# 设置增强的日志系统
setup_logging()
logger = get_logger(__name__)
set_identity_resolver(CutiIdentityResolver())


# ==================== 应用生命周期管理 ====================
# 全局 worker 实例（用于 cancel 端点访问）
_global_worker = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理
    
    启动流程：
    1. 初始化AccountManager（账号路由系统）
       - 从AWS AppConfig加载账号配置
       - 连接Redis（复用共享连接）
       - 清理过期的请求计数
    2. 初始化并启动Task Worker
       - 创建TaskWorker实例
       - 在后台任务中启动worker
    
    关闭流程：
    1. 停止AccountManager
       - 清理所有请求计数
       - 释放Redis资源（不关闭连接，由connection.py统一管理）
    2. 停止 Billing Worker（取消 billing_task）
    3. 停止Task Worker（分3步优雅关闭）
       - 步骤1: 取消worker_task（设置停止标志）
       - 步骤2: 调用stop()方法（关闭Pub/Sub、优雅关闭任务等，最多等待5分钟）
       - 步骤3: 等待worker_task完成（最多等待10秒）
    """
    global _global_worker
    video_runtime_repository = None
    deepseek_harness_client = None
    checkpoint_coordinator = None
    
    # ==================== 启动流程 ====================
    
    # 1. 启动时：初始化AccountConfigLoader（账号配置加载器）
    #    - 从AWS AppConfig加载账号配置（通过AppConfigService）
    #    - 连接Redis（复用共享连接，用于配置缓存）
    try:
        from .services.account.account_manager import AccountConfigLoader
        account_config_loader = AccountConfigLoader()
        await account_config_loader.initialize()
        logger.info("✅ AccountConfigLoader已初始化")
    except Exception as e:
        logger.error(f"❌ AccountConfigLoader初始化失败: {e}", exc_info=True)
        logger.warning("⚠️ 账号配置加载功能将不可用，工具调用可能会失败。请检查AppConfig配置。")
        # 不阻止应用启动，但会记录错误
        # 注意：如果AccountConfigLoader未初始化，工具调用时会报错
    
    # 2. 启动时：初始化 asyncpg 连接池
    try:
        from .models.database import init_asyncpg_pool
        await init_asyncpg_pool()
        logger.info("✅ asyncpg连接池已初始化")
    except Exception as e:
        logger.error(f"❌ asyncpg连接池初始化失败: {e}", exc_info=True)

    if getattr(settings, "VIDEO_INCREMENTAL_ENGINE_ENABLED", False):
        try:
            from .video_runtime.api import set_runtime
            from .video_runtime.postgres_repository import PostgresVideoProjectRepository
            from .video_runtime.runtime import VideoBuildRuntime
            video_runtime_repository = await PostgresVideoProjectRepository.connect(
                os.getenv("VIDEO_RUNTIME_DATABASE_URL", settings.DATABASE_URL),
                os.getenv("VIDEO_RUNTIME_DATABASE_SCHEMA", "cuti_video_runtime"),
            )
            build_runtime = VideoBuildRuntime(repository=video_runtime_repository)
            from .video_runtime.plugins.registry import configured_plugin_roots
            await build_runtime.plugins.load_directories(configured_plugin_roots())
            from .video_runtime.skill_workflows import load_workflow_skills
            await load_workflow_skills(build_runtime.plugins, build_runtime.skills)
            if any(
                callable(getattr(item.implementation, "capability_handlers", None))
                for item in build_runtime.plugins.loaded
            ):
                from .video_runtime.security import CapabilityGrantSigner
                secret = os.getenv("VIDEO_CAPABILITY_GRANT_SECRET", "").encode()
                if len(secret) < 32:
                    raise RuntimeError(
                        "VIDEO_CAPABILITY_GRANT_SECRET must contain at least 32 bytes "
                        "when executable plugin handlers are loaded",
                    )
                build_runtime.configure_plugin_execution(CapabilityGrantSigner(secret))
                await build_runtime.recover_active_builds()
            set_runtime(build_runtime)
            logger.info("✅ Incremental Video Runtime 已连接 Postgres")
        except Exception as e:
            logger.error("❌ Incremental Video Runtime 初始化失败", exc_info=True)
            raise
    
    # 3. 启动时：初始化并启动Task Worker（可通过 ENABLE_TASK_WORKER=false 关闭：无 SQS 队列的自托管场景）
    _global_worker = None
    worker_task = None
    if getattr(settings, "ENABLE_TASK_WORKER", True):
        try:
            from .services.worker.task_worker import TaskWorker

            _global_worker = TaskWorker(max_concurrent=1000)
            await _global_worker.initialize()
            worker_task = asyncio.create_task(_global_worker.start())
            logger.info("🚀 Task Worker已启动")
        except Exception as e:
            _global_worker = None
            worker_task = None
            logger.error(f"❌ Task Worker 启动失败（将不处理异步任务）: {e}", exc_info=True)
    else:
        logger.info("⏸️ Task Worker 未启动（ENABLE_TASK_WORKER=false，无异步任务处理）")
    
    # 4. 启动时：初始化并启动 Billing Worker
    #    local 环境与 dev 共用同一数据库，两个 billing worker 同时扫 pending 会冲突，
    #    因此 local 默认不启动；可通过 ENABLE_BILLING_WORKER=true 强制开启
    from .services.worker.billing_worker import BillingWorker
    
    billing_task = None
    _global_billing_worker = None
    _enable_billing = os.getenv("ENABLE_BILLING_WORKER", "").lower() in ("1", "true", "yes")
    _is_local = settings.ENVIRONMENT.value == "local"
    # Skip the billing worker when running local, or when billing is disabled entirely
    # (self-hosted DISABLE_BILLING=true): with no billing there is nothing to verify/cost-fill,
    # and running it just spams LangSmith cost lookups. ENABLE_BILLING_WORKER=true forces it on.
    _billing_disabled = bool(getattr(settings, "DISABLE_BILLING", False))
    if (_is_local or _billing_disabled) and not _enable_billing:
        logger.info("⏸️ Billing Worker not started (local or DISABLE_BILLING=true; set ENABLE_BILLING_WORKER=true to force on)")
    else:
        _global_billing_worker = BillingWorker(interval_seconds=1)
        await _global_billing_worker.initialize()
        billing_task = asyncio.create_task(_global_billing_worker.start())
        logger.info("🚀 Billing Worker已启动")

    # 5. Deep Agent V2（in-process chat）：挂载子应用时 lifespan 不一定触发，故在主进程初始化
    if (
        getattr(settings, "ENABLE_CHAT_AGENT", False)
    ):
        try:
            from .video_runtime.deepseek_bff import set_deepseek_client
            from .video_runtime.deepseek_client import DeepSeekHarnessClient
            deepseek_harness_client = DeepSeekHarnessClient(
                os.getenv("DEEPSEEK_HARNESS_URL", "http://127.0.0.1:3080"),
                authorization=os.getenv("DEEPSEEK_HARNESS_AUTHORIZATION") or None,
            )
            set_deepseek_client(deepseek_harness_client)
            if getattr(settings, "VIDEO_INCREMENTAL_ENGINE_ENABLED", False):
                from .video_runtime.checkpoint_coordinator import CheckpointCoordinator
                checkpoint_coordinator = CheckpointCoordinator(
                    get_runtime(), deepseek_harness_client,
                )
                checkpoint_coordinator.start()
            logger.info("✅ DeepSeek compatibility BFF 已初始化")
        except Exception as e:
            logger.error(f"❌ DeepSeek compatibility BFF 初始化失败: {e}", exc_info=True)
            raise
    
    yield  # 应用运行期间
    
    # ==================== 关闭流程 ====================

    if checkpoint_coordinator is not None:
        await checkpoint_coordinator.close()

    if deepseek_harness_client is not None:
        from .video_runtime.deepseek_bff import set_deepseek_client
        set_deepseek_client(None)
        await deepseek_harness_client.close()
        logger.info("✅ DeepSeek compatibility BFF 已关闭")

    if video_runtime_repository is not None:
        await get_runtime().close()
        await video_runtime_repository.close()
        logger.info("✅ Incremental Video Runtime 已关闭")
    
    # 1. 关闭时：停止AccountConfigLoader
    #    - 不关闭Redis连接（由connection.py统一管理）
    try:
        from .services.account.account_manager import AccountConfigLoader
        account_config_loader = AccountConfigLoader()
        await account_config_loader.shutdown()
        logger.info("✅ AccountConfigLoader已关闭")
    except Exception as e:
        logger.error(f"❌ AccountConfigLoader关闭失败: {e}", exc_info=True)
    
    # 2. 关闭时：停止 Billing Worker（取消任务即可）
    if billing_task is not None:
        try:
            billing_task.cancel()
            await asyncio.wait_for(billing_task, timeout=10.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        logger.info("✅ Billing Worker已停止")
    
    # 3. 关闭时：停止Task Worker（分3步优雅关闭）——仅当 Worker 实际启动时执行
    import time
    if worker_task is not None and _global_worker is not None:
        shutdown_start_time = time.time()
        logger.info("⏹️ 正在停止Task Worker...")

        # 步骤1: 先取消 worker_task（设置停止标志）
        logger.info("📝 步骤1: 取消 worker_task...")
        cancel_start_time = time.time()
        worker_task.cancel()
        logger.info(f"✅ worker_task 已取消 (耗时: {time.time() - cancel_start_time:.2f}秒)")

        # 步骤2: 调用 stop() 方法（会关闭 Pub/Sub、优雅关闭任务等）
        # 注意：stop() 内部等待最多 10500s（2h55m），与 K8s terminationGracePeriodSeconds: 10800 一致
        logger.info("📝 步骤2: 调用 stop() 方法...")
        stop_start_time = time.time()
        try:
            await asyncio.wait_for(_global_worker.stop(), timeout=10600.0)  # 与 stop() 内 10500s 一致，留 100s 余量
            logger.info(f"✅ stop() 完成 (耗时: {time.time() - stop_start_time:.2f}秒)")
        except asyncio.TimeoutError:
            logger.warning(f"⏸️ Worker停止超时（2h55m），强制继续 (已耗时: {time.time() - stop_start_time:.2f}秒)")
        except Exception as e:
            logger.error(f"❌ Worker停止失败: {e} (已耗时: {time.time() - stop_start_time:.2f}秒)", exc_info=True)

        # 步骤3: 等待 worker_task 完成（设置超时）
        logger.info("📝 步骤3: 等待 worker_task 完成...")
        wait_task_start_time = time.time()
        try:
            await asyncio.wait_for(worker_task, timeout=10.0)
            logger.info(f"✅ worker_task 完成 (耗时: {time.time() - wait_task_start_time:.2f}秒)")
        except (asyncio.CancelledError, asyncio.TimeoutError) as e:
            logger.warning(f"⚠️ worker_task 等待超时或已取消: {type(e).__name__} (已耗时: {time.time() - wait_task_start_time:.2f}秒)")

        total_shutdown_time = time.time() - shutdown_start_time
        logger.info(f"✅ Task Worker 停止流程完成，总耗时: {total_shutdown_time:.2f}秒")
    else:
        logger.info("⏭️ Task Worker 未运行，跳过其关闭流程")
    
    # 4. 关闭 asyncpg 连接池
    try:
        from .models.database import close_asyncpg_pool
        await close_asyncpg_pool()
        logger.info("✅ asyncpg连接池已关闭")
    except Exception as e:
        logger.error(f"❌ asyncpg连接池关闭失败: {e}", exc_info=True)
    
    # 4.5 清理 WaveSpeed CONCURRENT 限流计数（避免下次启动时残留）
    try:
        from .services.account.rate_limiter import clear_wavespeed_concurrent_limits
        from .services.redis.connection import get_redis_client
        redis = await get_redis_client(decode_responses=True)
        n = await clear_wavespeed_concurrent_limits(redis=redis)
        logger.info(f"✅ WaveSpeed CONCURRENT 限流已清理，删除 {n} 个 key")
    except Exception as e:
        logger.error(f"❌ 清理 WaveSpeed 限流失败: {e}", exc_info=True)
    
    # 5. 关闭 Redis 连接
    try:
        from .services.redis.connection import close_redis_client
        await close_redis_client()
        logger.info("✅ Redis连接已关闭")
    except Exception as e:
        logger.error(f"❌ 关闭Redis连接失败: {e}", exc_info=True)
    
    _global_worker = None
    logger.info("✅ Task Worker已停止")

def get_task_worker():
    """获取全局 TaskWorker 实例（用于 cancel 端点）"""
    return _global_worker


# ==================== FastAPI应用配置 ====================
app = FastAPI(
    title="Children's Storybook Generator",
    description="API for generating customized children's storybooks",
    version="0.1.0",
    lifespan=lifespan  # 使用新的生命周期管理
)

# ==================== 中间件配置 ====================
# 流式响应时，若客户端在同一连接上发送新请求，Starlette 会收到 http.request 而非 http.disconnect，导致
# RuntimeError: Unexpected message received: http.request。请求体可合法分成多个 more_body chunk；
# 仅在完整请求体结束后，将同一 scope 下额外收到的 http.request 转为 http.disconnect。
class StreamReceiveFixMiddleware:
    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        request_complete = [False]

        async def wrapped_receive():
            try:
                msg = await receive()
            except RuntimeError as e:
                if "Unexpected message received: http.request" in str(e):
                    return {"type": "http.disconnect"}
                raise
            if msg.get("type") == "http.request":
                if request_complete[0]:
                    return {"type": "http.disconnect"}
                request_complete[0] = not msg.get("more_body", False)
            return msg

        await self._app(scope, wrapped_receive, send)


app.add_middleware(StreamReceiveFixMiddleware)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境中应指定具体的域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 语言处理中间件
@app.middleware("http")
async def language_middleware(request: Request, call_next):
    """语言处理中间件 - 处理 X-App-Language / X-Language header，前端传 X-App-Language"""
    language = request.headers.get("X-App-Language") or request.headers.get("X-Language", "en")
    
    # 设置当前请求的语言上下文
    set_current_language(language)
    
    # 处理请求
    response = await call_next(request)
    
    return response

# 请求/响应参数与结果日志（使用现有异步日志 QueueHandler，不阻塞）
# 说明：所有请求都会记录 method、path、query；仅 POST/PUT/PATCH 有请求体（HTTP 规范中 GET/HEAD/OPTIONS 无 body），故只对带体请求读 body 并打日志。
_LOG_BODY_MAX = 2000


async def _read_request_body_cached(request: Request) -> bytes:
    """读取请求体并重放，使下游仍能正常读取 body/json。"""
    body = await request.body()
    received = {"done": False}

    async def _receive():
        if received["done"]:
            return {"type": "http.disconnect"}
        received["done"] = True
        return {"type": "http.request", "body": body}

    request._receive = _receive
    return body


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """请求日志中间件：记录请求参数与响应结果（异步写入日志队列）。"""
    start_time = time.time()
    path = request.url.path
    method = request.method
    query = str(request.query_params) if request.query_params else ""

    # 请求参数：method, path, query
    logger.info(f"🌐 请求: {method} {path} query={query[:500] if query else '(none)'}")

    # /api/cuti：读 body 后重放（替换 _receive）。
    # chat SSE：只 await body() 交给 Starlette 缓存，绝不替换 _receive（会弄坏 listen_for_disconnect）。
    req_body_preview = None
    if method in ("POST", "PUT", "PATCH") and "/messages/stream" not in path:
        try:
            if path.startswith("/api/cuti"):
                body = await _read_request_body_cached(request)
                if body:
                    req_body_preview = body.decode("utf-8", errors="replace")[:_LOG_BODY_MAX]
                    if len(body) > _LOG_BODY_MAX:
                        req_body_preview += "...(truncated)"
        except Exception:
            req_body_preview = "(read error)"
    if req_body_preview is not None:
        logger.info(f"🌐 请求体: {req_body_preview}")

    response = await call_next(request)
    process_time = time.time() - start_time

    # 响应结果：status, 对 JSON 响应记录 body 预览
    status = response.status_code
    res_body_preview = None
    if path.startswith("/api/cuti") and status == 200:
        ct = response.headers.get("content-type", "")
        if "application/json" in ct:
            try:
                body = b""
                async for chunk in response.body_iterator:
                    body += chunk
                res_body_preview = body.decode("utf-8", errors="replace")[:_LOG_BODY_MAX]
                if len(body) > _LOG_BODY_MAX:
                    res_body_preview += "...(truncated)"
                from starlette.responses import Response as StarletteResponse
                response = StarletteResponse(content=body, status_code=status, headers=response.headers, media_type=response.media_type)
            except Exception:
                res_body_preview = "(read error)"
    if res_body_preview is not None:
        logger.info(f"🌐 响应体: {res_body_preview}")

    if status >= 400:
        logger.warning(f"❌ 请求完成: {method} {path} - {status} ({process_time:.3f}s)")
    else:
        logger.info(f"✅ 请求完成: {method} {path} - {status} ({process_time:.3f}s)")

    response.headers["X-Process-Time"] = str(process_time)
    return response

# ==================== 路由配置 ====================
# API路由 - Cuti-VideoAgent 使用 /api/cuti 前缀作为独立服务
app.include_router(api_router, prefix="/api/cuti", tags=["cuti-videoagent"])
app.include_router(video_runtime_router)
# 运营/测试 admin 仅在 ENABLE_OPS_ADMIN=True 时挂载（开源自托管默认关闭）
if getattr(settings, "ENABLE_OPS_ADMIN", True):
    for _ops_router in _OPS_ADMIN_ROUTERS:
        app.include_router(_ops_router, prefix="/api/cuti")
    logger.info(f"✅ Ops-admin 路由已挂载（{len(_OPS_ADMIN_ROUTERS)} 个）")
else:
    logger.info("⏭️  ENABLE_OPS_ADMIN=False，跳过 ops-admin 路由挂载")

# In-process Chat Agent（合并单体）：仅 ENABLE_CHAT_AGENT=True 时挂载。
# 默认 False = 与独立 VideoAgent 行为完全一致；True = 单进程同时对外提供 Chat SSE 路由
# V2 API 挂载在 /chat-v1/service/v2/*，省去独立进程与 HTTP 跳转。
if getattr(settings, "ENABLE_CHAT_AGENT", False):
    try:
        from fastapi import FastAPI as _FastAPI
        from .video_runtime.deepseek_bff import (
            router as _chat_v2_api,
            studio_router as _chat_studio_api,
        )

        _CHAT_SERVICE_PREFIX = "/chat-v1/service"
        _chat_service_app = _FastAPI(title="Cuti Chat Agent (in-process)")

        async def _chat_business_exception_handler(request: Request, exc: BusinessException):
            lang = "en" if request.headers.get("Accept-Language", "zh").startswith("en") else "zh"
            detail = exc.detail if exc.lang == lang else exc.error_code.message(lang)
            return JSONResponse(
                status_code=200,
                content={"code": exc.error_code.code, "message": detail, "data": None},
            )

        async def _chat_unexpected_exception_handler(request: Request, exc: Exception):
            logger.error("Unexpected in-process chat error: %s", exc, exc_info=True)
            lang = "en" if request.headers.get("Accept-Language", "zh").startswith("en") else "zh"
            message = (
                "Service is temporarily busy, please try again later"
                if lang == "en"
                else "服务暂时繁忙，请稍后重试"
            )
            return JSONResponse(
                status_code=500,
                content={"code": 10020, "message": message, "data": None},
            )

        _chat_service_app.add_exception_handler(
            BusinessException,
            _chat_business_exception_handler,
        )
        _chat_service_app.add_exception_handler(
            Exception,
            _chat_unexpected_exception_handler,
        )
        _chat_service_app.include_router(_chat_v2_api)
        _chat_service_app.include_router(_chat_studio_api)
        app.mount(_CHAT_SERVICE_PREFIX, _chat_service_app)
        logger.info(f"✅ In-process Chat Agent 路由已挂载: {_CHAT_SERVICE_PREFIX}")
    except Exception as e:
        logger.warning(f"⚠️ ENABLE_CHAT_AGENT=True 但挂载 Chat Agent 失败: {e}", exc_info=True)
else:
    logger.info("⏭️  ENABLE_CHAT_AGENT=False，跳过 In-process Chat Agent 挂载")

# 本地存储（STORAGE_BACKEND=local）：把 LOCAL_STORAGE_DIR 挂到 /files，
# 供前端直接取本地生成的图片/音频/视频（对齐 s3_utils 生成的 {PUBLIC_BASE_URL}/files/<key> URL）。
if str(getattr(settings, "STORAGE_BACKEND", "s3") or "s3").lower() == "local":
    _files_dir = os.path.abspath(getattr(settings, "LOCAL_STORAGE_DIR", "./data/uploads"))
    os.makedirs(_files_dir, exist_ok=True)
    app.mount("/files", StaticFiles(directory=_files_dir), name="local-files")
    logger.info(f"✅ 本地存储已挂载(/files): {_files_dir}")

# MCP — video-edit tools（通过 MCP 协议暴露给 Chat Agent）
try:
    from .services.agent.video_edit.mcp_server import mcp as video_edit_mcp
    from starlette.routing import Mount

    class _MCPApp:
        """Thin ASGI wrapper around StreamableHTTPSessionManager."""
        def __init__(self):
            self.mgr = None

        async def __call__(self, scope, receive, send):
            if scope["type"] == "lifespan":
                return
            await self.mgr.handle_request(scope, receive, send)

    _mcp_app = _MCPApp()
    app.router.routes.append(Mount("/mcp", app=_mcp_app))

    _orig_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _mcp_lifespan(a):
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        async with _orig_lifespan(a) as state:
            mgr = StreamableHTTPSessionManager(
                app=video_edit_mcp._mcp_server,
                stateless=True,
            )
            async with mgr.run():
                _mcp_app.mgr = mgr
                logger.info("✅ Video Edit MCP SessionManager 已初始化")
                yield state

    app.router.lifespan_context = _mcp_lifespan
    logger.info("✅ Video Edit MCP server 已挂载到 /mcp")
except Exception as e:
    logger.warning(f"⚠️ Video Edit MCP server 挂载失败: {e}", exc_info=True)


# ==================== 异常处理器 ====================
@app.exception_handler(BusinessException)
async def business_exception_handler(request: Request, exc: BusinessException):
    """业务异常处理器"""
    # 从请求头中获取语言设置
    lang = request.headers.get("Accept-Language", "zh")
    if lang.startswith("en"):
        lang = "en"
    else:
        lang = "zh"
    
    # 如果异常没有设置语言，使用从请求中获取的语言重新生成消息
    if exc.lang != lang:
        exc.detail = exc.error_code.message(lang)
    
    logger.warning(f"Business exception: {exc.error_code.code} - {exc.detail}")
    
    return JSONResponse(
        status_code=200,  # 业务异常也返回200状态码
        content={
            "code": exc.error_code.code,
            "message": exc.detail,
            "data": None
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTP异常处理器"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            "data": None
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """全局异常处理器"""
    # 从请求头中获取语言设置
    lang = request.headers.get("Accept-Language", "zh")
    if lang.startswith("en"):
        lang = "en"
    else:
        lang = "zh"
    
    logger.error(f"Unexpected error: {str(exc)}")
    
    # 根据语言返回友好提示
    if lang == "en":
        message = "Service is temporarily busy, please try again later"
    else:
        message = "服务暂时繁忙，请稍后重试"
    
    return JSONResponse(
        status_code=200,  # 返回200状态码更友好
        content={
            "code": 10020,  # 使用内部服务器错误代码
            "message": message,
            "data": None
        }
    )


# ==================== 基础路由 ====================
@app.get("/health")
def health_check():
    """健康检查端点"""
    return {"status": "healthy"}

@app.get("/")
def root():
    """根端点，提供 API 信息。"""
    return {
        "message": "Welcome to the Children's Storybook Generator API",
        "documentation": "/docs",
    }

# ==================== 开发服务器启动 ====================
if __name__ == "__main__":
    import uvicorn
    
    logger.info("Starting Storybook Generator API")
    
    # 从命令行参数获取环境
    env = DEFAULT_ENV
    if len(sys.argv) > 1:
        env_arg = sys.argv[1].lower()
        if env_arg in VALID_ENVS:
            env = env_arg
        else:
            print(f"Invalid environment: {env_arg}")
            print(f"Valid environments are: {', '.join(VALID_ENVS)}")
            sys.exit(1)
    
    # 设置环境变量
    os.environ["ENVIRONMENT"] = env
    
    print(f"Starting server in {env.upper()} environment...")
    
    # 重新加载设置以获取正确的端口配置
    from .config import get_settings
    current_settings = get_settings()
    
    print(f"Starting server on {current_settings.SERVER_HOST}:{current_settings.SERVER_PORT}")
    
    # 启动服务器（uvicorn 日志级别与 settings.LOG_LEVEL 一致，prod 也可用 DEBUG 便于排查）
    log_level = (current_settings.LOG_LEVEL or "INFO").lower()
    if log_level not in ("debug", "info", "warning", "error", "critical"):
        log_level = "info"
    uvicorn.run(
        "app.main:app",
        host=current_settings.SERVER_HOST,
        port=current_settings.SERVER_PORT,
        reload=(env != "production"),  # 非生产环境启用热重载
        log_level=log_level,
        timeout_keep_alive=65,  # 增加保持连接超时时间
        backlog=2048  # 增加连接队列大小
    )

