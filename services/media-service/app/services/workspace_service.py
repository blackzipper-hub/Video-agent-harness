import asyncio
import logging
import os
import shutil
import time
from pathlib import Path

from app.config import Settings

logger = logging.getLogger(__name__)


class WorkspaceService:
    """Manages per-run_id workspace directories with TTL-based cleanup."""

    def __init__(self, settings: Settings):
        self.base = Path(settings.workspace_base)
        self.ttl_hours = settings.workspace_ttl_hours
        self.auto_cleanup = settings.workspace_auto_cleanup
        self.base.mkdir(parents=True, exist_ok=True)

    def get_workspace(self, run_id: str) -> Path:
        ws = self.base / run_id
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "inputs").mkdir(exist_ok=True)
        (ws / "intermediates").mkdir(exist_ok=True)
        (ws / "outputs").mkdir(exist_ok=True)
        return ws

    def get_input_path(self, run_id: str, filename: str) -> Path:
        return self.get_workspace(run_id) / "inputs" / filename

    def get_intermediate_path(self, run_id: str, filename: str) -> Path:
        return self.get_workspace(run_id) / "intermediates" / filename

    def get_output_path(self, run_id: str, filename: str) -> Path:
        return self.get_workspace(run_id) / "outputs" / filename

    async def cleanup(self, run_id: str) -> tuple[int, float]:
        ws = self.base / run_id
        if not ws.exists():
            return 0, 0.0

        file_count = 0
        total_bytes = 0
        for f in ws.rglob("*"):
            if f.is_file():
                file_count += 1
                total_bytes += f.stat().st_size

        await asyncio.to_thread(shutil.rmtree, ws, ignore_errors=True)
        freed_mb = round(total_bytes / (1024 * 1024), 2)
        logger.info("Cleaned workspace %s: %d files, %.2f MB", run_id, file_count, freed_mb)
        return file_count, freed_mb

    async def cleanup_if_enabled(self, run_id: str) -> None:
        """写操作完成（结果已上传 S3）后立即清理该 run_id 工作区。

        跳过共享的 "_probe" 探测目录；失败仅告警，不影响请求返回（TTL 仍会兜底）。
        """
        if not self.auto_cleanup or not run_id or run_id == "_probe":
            return
        try:
            await self.cleanup(run_id)
        except Exception as e:
            logger.warning("Auto cleanup failed for %s: %s", run_id, e)

    async def _ttl_cleanup_once(self):
        cutoff = time.time() - self.ttl_hours * 3600
        if not self.base.exists():
            return
        for entry in self.base.iterdir():
            if not entry.is_dir():
                continue
            try:
                mtime = entry.stat().st_mtime
                if mtime < cutoff:
                    file_count, freed_mb = await self.cleanup(entry.name)
                    if file_count > 0:
                        logger.info(
                            "TTL cleanup %s: %d files, %.2f MB freed",
                            entry.name, file_count, freed_mb,
                        )
            except Exception as e:
                logger.warning("TTL cleanup error for %s: %s", entry.name, e)

    async def start_ttl_cleanup_loop(self):
        while True:
            try:
                await self._ttl_cleanup_once()
            except Exception as e:
                logger.error("TTL cleanup loop error: %s", e)
            await asyncio.sleep(600)
