"""
关键帧生成阶段性能测试
模拟完整的 keyframe pipeline，mock 所有 API 调用（LLM + 图像生成），
测量真实的 CPU / IO / 存储开销：
  - PIL 图片解码/缩放/编码（s3_utils.upload_image）
  - S3 下载/上传的文件 I/O
  - 并发 asyncio.gather + Semaphore 的线程调度开销
  - 临时文件创建和清理

运行:
    cd /path/to/Cuti-VideoAgent
    conda activate cuti-video-local
    python -m pytest tests/performance/test_perf_keyframe.py -v -s
"""

import asyncio
import logging
import os
import shutil
import tempfile
import time
import uuid
from typing import Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import psutil
import pytest

from .perf_monitor import PerfMonitor, create_test_image, get_disk_usage_summary

logger = logging.getLogger(__name__)

# ── 从日志中提取的真实 URL 模板 ──
MOCK_CDN_BASE = "https://cdn-dev.newai.land"
MOCK_IMAGE_URLS = [
    f"{MOCK_CDN_BASE}/images/{uuid.uuid4()}.webp" for _ in range(50)
]


def _make_fake_image_bytes(width: int = 1920, height: int = 1080) -> bytes:
    """生成真实的图片字节数据（用于测试 PIL 解码/编码开销）"""
    from PIL import Image
    import io
    img = Image.new("RGB", (width, height), color=(42, 42, 42))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=80)
    return buf.getvalue()


class MockS3Utils:
    """替代 s3_utils，不做真正的网络 I/O，但执行真实的 PIL 操作"""

    def __init__(self, temp_dir: str):
        self.temp_dir = temp_dir
        self.upload_count = 0
        self.download_count = 0
        self.total_upload_bytes = 0
        self.total_download_bytes = 0
        self._fake_image_bytes = _make_fake_image_bytes()

    async def upload_file(self, file_data: bytes, file_key: str, content_type: str = None) -> str:
        self.upload_count += 1
        self.total_upload_bytes += len(file_data)
        # 模拟写到本地（模拟 S3 上传的 I/O 开销）
        local_path = os.path.join(self.temp_dir, f"upload_{uuid.uuid4().hex[:8]}")
        await asyncio.to_thread(self._write_file_sync, local_path, file_data)
        return f"{MOCK_CDN_BASE}/{file_key}"

    async def upload_image(self, image_data: bytes, generation_id: Optional[str] = None,
                           content_type: str = "image/webp", resize_to=None) -> str:
        """真实执行 PIL 解码/缩放/编码 —— 这是 CPU 热点之一"""
        from PIL import Image
        import io

        self.upload_count += 1
        img = Image.open(io.BytesIO(image_data))
        if img.mode != "RGB":
            img = img.convert("RGB")
        if resize_to:
            img = img.resize(resize_to, Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=80)
        output_bytes = buf.getvalue()
        self.total_upload_bytes += len(output_bytes)

        file_key = f"images/{generation_id or uuid.uuid4()}.webp"
        local_path = os.path.join(self.temp_dir, f"img_{uuid.uuid4().hex[:8]}.webp")
        await asyncio.to_thread(self._write_file_sync, local_path, output_bytes)
        return f"{MOCK_CDN_BASE}/{file_key}"

    async def download_file(self, url: str, local_path: str) -> bool:
        """模拟下载：生成一个真实图片文件到本地路径"""
        self.download_count += 1
        self.total_download_bytes += len(self._fake_image_bytes)
        await asyncio.to_thread(self._write_file_sync, local_path, self._fake_image_bytes)
        return True

    def _write_file_sync(self, path: str, data: bytes):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def report(self):
        logger.info(
            f"📦 MockS3: uploads={self.upload_count} ({self.total_upload_bytes / 1024 / 1024:.1f}MB), "
            f"downloads={self.download_count} ({self.total_download_bytes / 1024 / 1024:.1f}MB)"
        )


async def _simulate_keyframe_generation_with_pil(
    num_keyframes: int,
    max_concurrent: int,
    mock_s3: MockS3Utils,
    monitor: PerfMonitor,
    resolution: Tuple[int, int] = (1920, 1080),
):
    """
    模拟关键帧生成的核心流程（不调用 LLM/API）：
    1. 生成 fake image bytes（模拟 API 返回）
    2. PIL 解码 + 缩放 + 编码（模拟 s3_utils.upload_image 的 CPU 开销）
    3. S3 上传（mock I/O）
    4. 并发控制使用真实的 asyncio.Semaphore
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    fake_image_data = _make_fake_image_bytes(*resolution)

    async def generate_single(idx: int):
        async with semaphore:
            logger.debug(f"Keyframe {idx}: start")
            # 模拟 LLM 调用延迟（很短，因为 mock）
            await asyncio.sleep(0.01)

            # 真实的 PIL 处理（这是 CPU 热点）
            url = await mock_s3.upload_image(
                fake_image_data,
                generation_id=f"kf_{idx}_{uuid.uuid4().hex[:8]}",
                resize_to=resolution,
            )
            logger.debug(f"Keyframe {idx}: done → {url[:50]}...")
            return url

    monitor.mark("keyframe_generation_start")

    tasks = [generate_single(i) for i in range(num_keyframes)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    monitor.mark("keyframe_generation_end")

    success_count = sum(1 for r in results if isinstance(r, str))
    fail_count = sum(1 for r in results if isinstance(r, Exception))
    logger.info(f"✅ Keyframe generation: {success_count}/{num_keyframes} success, {fail_count} failures")
    return results


async def _simulate_multi_task_keyframe(
    num_tasks: int,
    keyframes_per_task: int,
    max_concurrent_per_task: int,
    mock_s3: MockS3Utils,
    monitor: PerfMonitor,
):
    """模拟多个任务同时进行 keyframe 生成（并发压力测试）"""
    monitor.mark("multi_task_start")

    async def run_one_task(task_id: int):
        logger.info(f"📋 Task {task_id}: generating {keyframes_per_task} keyframes (max_concurrent={max_concurrent_per_task})")
        return await _simulate_keyframe_generation_with_pil(
            num_keyframes=keyframes_per_task,
            max_concurrent=max_concurrent_per_task,
            mock_s3=mock_s3,
            monitor=monitor,  # 共享 monitor
        )

    # 所有任务同时启动
    task_results = await asyncio.gather(
        *[run_one_task(i) for i in range(num_tasks)],
        return_exceptions=True,
    )

    monitor.mark("multi_task_end")
    return task_results


@pytest.fixture
def perf_temp_dir():
    d = tempfile.mkdtemp(prefix="perf_kf_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestKeyframePerformance:
    """关键帧生成性能测试"""

    @pytest.mark.asyncio
    async def test_single_task_keyframe_41_shots(self, perf_temp_dir):
        """模拟单任务 41 个关键帧生成（与日志中 full-0ba90767 一致）"""
        monitor = PerfMonitor(sample_interval=0.3, track_temp_dir=perf_temp_dir)
        mock_s3 = MockS3Utils(perf_temp_dir)

        monitor.start()
        try:
            monitor.mark("test_start")

            disk_before = get_disk_usage_summary()
            logger.info(f"💾 Disk before: {disk_before['used_gb']:.2f}GB used / {disk_before['free_gb']:.2f}GB free")

            results = await _simulate_keyframe_generation_with_pil(
                num_keyframes=41,
                max_concurrent=10,
                mock_s3=mock_s3,
                monitor=monitor,
            )

            disk_after = get_disk_usage_summary()
            logger.info(f"💾 Disk after: {disk_after['used_gb']:.2f}GB used / {disk_after['free_gb']:.2f}GB free")
            logger.info(f"💾 Disk delta: {(disk_after['used_gb'] - disk_before['used_gb']) * 1024:.1f}MB")

            monitor.mark("test_end")
            mock_s3.report()
        finally:
            monitor.stop()
            monitor.report()
            csv_path = os.path.join(perf_temp_dir, "keyframe_single_task.csv")
            monitor.dump_csv(csv_path)
            logger.info(f"📊 CSV: {csv_path}")

        assert sum(1 for r in results if isinstance(r, str)) == 41

    @pytest.mark.asyncio
    async def test_multi_task_3x_concurrent(self, perf_temp_dir):
        """模拟 3 个任务同时做 keyframe 生成（并发压力测试）"""
        monitor = PerfMonitor(sample_interval=0.3, track_temp_dir=perf_temp_dir)
        mock_s3 = MockS3Utils(perf_temp_dir)

        monitor.start()
        try:
            results = await _simulate_multi_task_keyframe(
                num_tasks=3,
                keyframes_per_task=20,
                max_concurrent_per_task=10,
                mock_s3=mock_s3,
                monitor=monitor,
            )
            monitor.mark("test_done")
            mock_s3.report()
        finally:
            monitor.stop()
            monitor.report()
            csv_path = os.path.join(perf_temp_dir, "keyframe_multi_task.csv")
            monitor.dump_csv(csv_path)

    @pytest.mark.asyncio
    async def test_pil_resize_cpu_impact(self, perf_temp_dir):
        """单独测试 PIL resize 的 CPU 开销（隔离瓶颈）"""
        from PIL import Image
        import io

        monitor = PerfMonitor(sample_interval=0.2, track_temp_dir=perf_temp_dir)
        monitor.start()

        try:
            monitor.mark("pil_start")

            # 模拟 41 张 1920x1080 图片的 decode → resize → encode
            fake_data = _make_fake_image_bytes(1920, 1080)
            results = []

            for i in range(41):
                img = Image.open(io.BytesIO(fake_data))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img = img.resize((1920, 1080), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=80)
                results.append(len(buf.getvalue()))

            monitor.mark("pil_end")
            total_output_mb = sum(results) / (1024 * 1024)
            logger.info(f"📊 PIL processed 41 images, total output: {total_output_mb:.1f}MB")
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_pil_concurrent_cpu_impact(self, perf_temp_dir):
        """并发 PIL resize —— 多线程下 CPU 争抢"""
        from PIL import Image
        import io

        monitor = PerfMonitor(sample_interval=0.2, track_temp_dir=perf_temp_dir)
        monitor.start()

        fake_data = _make_fake_image_bytes(1920, 1080)

        async def pil_one(idx: int):
            def _do():
                img = Image.open(io.BytesIO(fake_data))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img = img.resize((1920, 1080), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=80)
                return len(buf.getvalue())
            return await asyncio.to_thread(_do)

        try:
            monitor.mark("pil_concurrent_start")
            results = await asyncio.gather(*[pil_one(i) for i in range(41)])
            monitor.mark("pil_concurrent_end")
            logger.info(f"📊 Concurrent PIL: 41 images, total output: {sum(results) / 1024 / 1024:.1f}MB")
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_temp_file_leak_detection(self, perf_temp_dir):
        """验证临时文件是否正确清理"""
        monitor = PerfMonitor(sample_interval=0.3, track_temp_dir=perf_temp_dir)
        mock_s3 = MockS3Utils(perf_temp_dir)

        files_before = set()
        for root, dirs, files in os.walk(tempfile.gettempdir()):
            for f in files:
                files_before.add(os.path.join(root, f))

        monitor.start()
        try:
            await _simulate_keyframe_generation_with_pil(
                num_keyframes=10,
                max_concurrent=5,
                mock_s3=mock_s3,
                monitor=monitor,
            )
        finally:
            monitor.stop()

        files_after = set()
        for root, dirs, files in os.walk(tempfile.gettempdir()):
            for f in files:
                files_after.add(os.path.join(root, f))

        leaked = files_after - files_before
        # 排除 perf_temp_dir 下的文件（那是我们的测试输出）
        leaked = {f for f in leaked if not f.startswith(perf_temp_dir)}

        if leaked:
            logger.warning(f"⚠️  LEAKED temp files ({len(leaked)}):")
            for f in list(leaked)[:20]:
                try:
                    size = os.path.getsize(f)
                    logger.warning(f"  {f} ({size / 1024:.1f}KB)")
                except OSError:
                    pass
        else:
            logger.info("✅ No temp file leaks detected")
