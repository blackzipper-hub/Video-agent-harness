import logging
import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlmodel import Session, create_engine
from sqlmodel.pool import StaticPool
from ..config import get_settings

# ==================== SQLAlchemy部分 (用于User/Auth等非video功能) ====================

database_url = get_settings().DATABASE_URL

# 异步引擎（用于User/Auth）
if database_url.startswith("postgresql://"):
    sqlalchemy_url = database_url.replace("postgresql://", "postgresql+asyncpg://")
else:
    sqlalchemy_url = database_url

async_engine = create_async_engine(
    sqlalchemy_url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_async_db():
    """用于User/Auth等非video功能"""
    async with AsyncSessionLocal() as session:
        yield session

# 同步引擎（兼容）
sync_engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False} if database_url.startswith("sqlite") else {},
    poolclass=StaticPool if database_url.startswith("sqlite") else None,
)

def get_db():
    """同步数据库session（兼容）"""
    with Session(sync_engine) as session:
        yield session

# ==================== asyncpg 连接池 (用于video功能) ====================
# 🚀 直接使用 asyncpg 原生连接池，适用于video相关的高并发操作

_asyncpg_pool: asyncpg.Pool = None
_asyncpg_pool_logger = logging.getLogger("app.models.database.asyncpg_pool")
_asyncpg_pool_logger.setLevel(logging.INFO)

async def init_asyncpg_pool():
    """初始化 asyncpg 连接池（固定10个连接）
    
    在应用启动时调用，用于所有数据库操作
    """
    global _asyncpg_pool
    
    if _asyncpg_pool is not None:
        _asyncpg_pool_logger.warning("⚠️  asyncpg pool already initialized")
        return
    
    database_url = get_settings().DATABASE_URL
    
    # asyncpg 使用原始的 postgresql:// URL
    if not database_url.startswith("postgresql://"):
        raise ValueError("Only PostgreSQL databases are supported for asyncpg pool")
    
    # 🔥 固定配置：10个连接，min=max=10（去掉冷启动）
    pool_size = 10
    _asyncpg_pool_logger.info(f"🔥 Initializing asyncpg pool with min={pool_size}, max={pool_size}")
    
    _asyncpg_pool = await asyncpg.create_pool(
        database_url,
        min_size=pool_size,      # 🔥 固定为 10（去掉冷启动）
        max_size=pool_size,      # 🔥 固定为 10
        max_queries=50000,
        max_inactive_connection_lifetime=300,
        command_timeout=60,
    )
    
    _asyncpg_pool_logger.info("✅ asyncpg pool initialized successfully")

async def close_asyncpg_pool():
    """关闭 asyncpg 连接池
    
    在应用关闭时调用
    """
    global _asyncpg_pool
    
    if _asyncpg_pool is not None:
        _asyncpg_pool_logger.info("🔌 Closing asyncpg pool...")
        await _asyncpg_pool.close()
        _asyncpg_pool = None
        _asyncpg_pool_logger.info("✅ asyncpg pool closed")

def get_asyncpg_pool() -> asyncpg.Pool:
    """获取 asyncpg 连接池
    
    Returns:
        asyncpg.Pool: 连接池实例
        
    Raises:
        RuntimeError: 如果连接池未初始化
    """
    if _asyncpg_pool is None:
        raise RuntimeError("asyncpg pool not initialized. Call init_asyncpg_pool() first.")
    return _asyncpg_pool

# 注意：建表请使用 init/init_database_async.py 脚本
