"""Run the asynchronous task consumer outside the HTTP server process.

Local development used to host ``TaskWorker`` in the same asyncio loop as
Uvicorn.  A provider/client that blocks despite exposing an async interface
then made every HTTP endpoint unresponsive for the duration of the call.  This
runner gives task execution its own process and event loop so the UI can keep
polling progress, listing conversations, and cancelling work.
"""

from __future__ import annotations

import asyncio
import signal


async def run() -> None:
    from ..account.account_manager import AccountConfigLoader
    from ..agent.agent_router_service import get_agent_router_service
    from ...chat.config import get_settings as get_chat_settings
    from ...chat.v2.container import close_v2, initialize_v2
    from ...models.database import init_asyncpg_pool
    from .task_worker import TaskWorker

    account_loader = AccountConfigLoader()
    worker = TaskWorker(max_concurrent=1000)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop_event.set)

    await account_loader.initialize()
    await init_asyncpg_pool()
    await initialize_v2(get_chat_settings())
    # Build the router before consuming so startup failures are visible and no
    # queued run is moved to RUNNING without an executable agent graph.
    get_agent_router_service()
    await worker.initialize()
    worker_task = asyncio.create_task(worker.start(), name="cuti-task-worker")

    try:
        await stop_event.wait()
    finally:
        worker_task.cancel()
        try:
            await worker.stop()
        finally:
            await close_v2()
            await account_loader.shutdown()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(run())
