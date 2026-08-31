"""DB 初始化 fixture — 每个测试函数重建 pool 绑到当前 event loop。

asyncpg pool 绑定到创建它的 event loop，而 pytest-asyncio (function scope)
每个测试都有自己的 loop，所以必须每次重建 pool。
"""
import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def ensure_db_pool():
    """关闭旧 pool → 在当前 loop 重建。"""
    import app.models.database as db_mod
    # 关闭上一轮遗留的 pool（绑在了已关闭的 loop 上）
    if db_mod._asyncpg_pool is not None:
        try:
            await db_mod._asyncpg_pool.close()
        except Exception:
            pass
        db_mod._asyncpg_pool = None

    await db_mod.init_asyncpg_pool()
