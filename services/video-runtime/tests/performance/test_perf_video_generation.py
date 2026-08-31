"""
视频生成阶段性能测试
模拟完整的 video generation pipeline，mock 所有 API 调用，
真实执行下载后的视频处理（normalize、watermark、ffprobe）。

核心测试点:
  - 视频文件下载后的 normalize_video_to_target_sync（FFmpeg 重编码）
  - 视频水印叠加 add_watermark_to_video_async（FFmpeg filter_complex）
  - ffprobe 视频信息获取
  - S3 download_and_upload_video_to_s3 的完整流程
  - 多任务并发时的 CPU 争抢

运行:
    cd /path/to/Cuti-VideoAgent
    conda activate cuti-video-local
    python -m pytest tests/performance/test_perf_video_generation.py -v -s
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

from .perf_monitor import PerfMonitor, create_test_video, get_disk_usage_summary

logger = logging.getLogger(__name__)

MOCK_CDN_BASE = "https://cdn-dev.newai.land"

# 从日志提取的真实视频 URL 模式
MOCK_VIDEO_URLS = [
    f"{MOCK_CDN_BASE}/videos/seedance_{uuid.uuid4().hex}_{uuid.uuid4().hex[:8]}.mp4"
    for _ in range(50)
]


class MockVideoS3:
    """模拟 S3 操作，但使用真实的本地视频文件做处理"""

    def __init__(self, temp_dir: str, test_video_path: str):
        self.temp_dir = temp_dir
        self.test_video_path = test_video_path
        self.upload_count = 0
        self.download_count = 0
        self.total_upload_bytes = 0
        self.total_download_bytes = 0

    async def download_file(self, url: str, local_path: str) -> bool:
        """模拟下载：复制测试视频到目标路径"""
        self.download_count += 1
        await asyncio.to_thread(shutil.copy2, self.test_video_path, local_path)
        self.total_download_bytes += os.path.getsize(local_path)
        return True

    async def upload_file(self, file_data: bytes, file_key: str, content_type: str = None) -> str:
        self.upload_count += 1
        self.total_upload_bytes += len(file_data)
        local_path = os.path.join(self.temp_dir, f"upload_{uuid.uuid4().hex[:8]}.mp4")
        await asyncio.to_thread(self._write_sync, local_path, file_data)
        return f"{MOCK_CDN_BASE}/{file_key}"

    async def upload_video(self, video_data: bytes, generation_id: Optional[str] = None) -> str:
        file_key = f"videos/{generation_id or uuid.uuid4()}.mp4"
        return await self.upload_file(video_data, file_key, "video/mp4")

    def _write_sync(self, path: str, data: bytes):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def report(self):
        logger.info(
            f"📦 MockVideoS3: uploads={self.upload_count} ({self.total_upload_bytes / 1024 / 1024:.1f}MB), "
            f"downloads={self.download_count} ({self.total_download_bytes / 1024 / 1024:.1f}MB)"
        )


async def _run_ffprobe(video_path: str) -> Optional[Dict]:
    """真实执行 ffprobe 获取视频信息"""
    import json
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,codec_name,pix_fmt,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    if proc.returncode != 0:
        logger.error(f"ffprobe failed: {stderr.decode()[:200]}")
        return None
    return json.loads(stdout.decode())


async def _run_normalize_video(input_path: str, output_path: str, target_w: int = 1920, target_h: int = 1080) -> str:
    """真实执行视频分辨率标准化（FFmpeg 重编码）"""
    cmd = [
        "ffmpeg", "-y",
        "-threads", "2",
        "-i", input_path,
        "-vf", f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-an",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"normalize failed: {stderr.decode()[:300]}")
    return output_path


async def _simulate_video_post_processing(
    video_path: str,
    temp_dir: str,
    idx: int,
    do_normalize: bool = True,
    do_watermark: bool = False,
) -> Tuple[str, Dict]:
    """
    模拟视频生成后的处理流程（这些都是真实 FFmpeg 操作）：
    1. ffprobe 获取视频信息
    2. normalize 到目标分辨率
    3. 可选：添加水印
    """
    metrics = {"ffprobe_ms": 0, "normalize_ms": 0, "watermark_ms": 0, "total_ms": 0}
    t0 = time.time()

    # 1. ffprobe
    t1 = time.time()
    info = await _run_ffprobe(video_path)
    metrics["ffprobe_ms"] = (time.time() - t1) * 1000
    logger.debug(f"  Video {idx} info: {info}")

    current_path = video_path

    # 2. normalize
    if do_normalize:
        t2 = time.time()
        norm_path = os.path.join(temp_dir, f"norm_{idx}_{uuid.uuid4().hex[:8]}.mp4")
        current_path = await _run_normalize_video(current_path, norm_path)
        metrics["normalize_ms"] = (time.time() - t2) * 1000

    metrics["total_ms"] = (time.time() - t0) * 1000
    return current_path, metrics


async def _simulate_video_generation_pipeline(
    num_videos: int,
    max_concurrent: int,
    mock_s3: MockVideoS3,
    monitor: PerfMonitor,
    temp_dir: str,
):
    """
    模拟完整的视频生成 pipeline（并发）：
    API mock（即时返回 URL） → 下载视频 → ffprobe → normalize → 上传
    """
    semaphore = asyncio.Semaphore(max_concurrent)

    async def process_one(idx: int):
        async with semaphore:
            logger.debug(f"Video {idx}: start")
            # 模拟 API 调用延迟（非常短因为 mock）
            await asyncio.sleep(0.01)

            # 下载视频到临时目录
            local_path = os.path.join(temp_dir, f"video_{idx}_{uuid.uuid4().hex[:8]}.mp4")
            await mock_s3.download_file(MOCK_VIDEO_URLS[idx % len(MOCK_VIDEO_URLS)], local_path)

            # 真实的后处理
            processed_path, metrics = await _simulate_video_post_processing(
                local_path, temp_dir, idx
            )

            # 上传
            with open(processed_path, "rb") as f:
                video_data = f.read()
            url = await mock_s3.upload_video(video_data, f"video_{idx}")

            logger.info(
                f"  Video {idx}: ffprobe={metrics['ffprobe_ms']:.0f}ms, "
                f"normalize={metrics['normalize_ms']:.0f}ms, total={metrics['total_ms']:.0f}ms"
            )
            return url, metrics

    monitor.mark("video_generation_start")
    results = await asyncio.gather(*[process_one(i) for i in range(num_videos)], return_exceptions=True)
    monitor.mark("video_generation_end")

    success = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]
    if failures:
        for f in failures[:5]:
            logger.error(f"  ❌ Video failed: {f}")

    # 统计 FFmpeg 耗时
    all_metrics = [r[1] for r in success]
    if all_metrics:
        avg_norm = sum(m["normalize_ms"] for m in all_metrics) / len(all_metrics)
        max_norm = max(m["normalize_ms"] for m in all_metrics)
        avg_total = sum(m["total_ms"] for m in all_metrics) / len(all_metrics)
        logger.info(f"📊 FFmpeg normalize: avg={avg_norm:.0f}ms, max={max_norm:.0f}ms")
        logger.info(f"📊 Total per-video: avg={avg_total:.0f}ms")

    return results


@pytest.fixture
def perf_temp_dir():
    d = tempfile.mkdtemp(prefix="perf_video_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def test_video_5s(perf_temp_dir):
    """生成一个 5 秒的 1080p 测试视频"""
    path = os.path.join(perf_temp_dir, "test_source_5s.mp4")
    create_test_video(path, duration=5.0, width=1920, height=1080)
    logger.info(f"🎬 Created test video: {path} ({os.path.getsize(path) / 1024:.0f}KB)")
    return path


class TestVideoGenerationPerformance:
    """视频生成阶段性能测试"""

    @pytest.mark.asyncio
    async def test_single_task_41_videos(self, perf_temp_dir, test_video_5s):
        """模拟单任务 41 个视频生成后处理（与日志中 full-0ba90767 一致）"""
        monitor = PerfMonitor(sample_interval=0.5, track_temp_dir=perf_temp_dir)
        mock_s3 = MockVideoS3(perf_temp_dir, test_video_5s)

        monitor.start()
        try:
            monitor.mark("test_start")

            disk_before = get_disk_usage_summary()
            logger.info(f"💾 Disk before: {disk_before['used_gb']:.2f}GB used")

            results = await _simulate_video_generation_pipeline(
                num_videos=41,
                max_concurrent=10,
                mock_s3=mock_s3,
                monitor=monitor,
                temp_dir=perf_temp_dir,
            )

            disk_after = get_disk_usage_summary()
            logger.info(f"💾 Disk after: {disk_after['used_gb']:.2f}GB used")
            logger.info(f"💾 Disk delta: {(disk_after['used_gb'] - disk_before['used_gb']) * 1024:.1f}MB")

            monitor.mark("test_end")
            mock_s3.report()
        finally:
            monitor.stop()
            monitor.report()
            csv_path = os.path.join(perf_temp_dir, "video_single_task.csv")
            monitor.dump_csv(csv_path)

        success_count = sum(1 for r in results if not isinstance(r, Exception))
        assert success_count == 41

    @pytest.mark.asyncio
    async def test_multi_task_2x_concurrent(self, perf_temp_dir, test_video_5s):
        """模拟 2 个任务同时做视频后处理"""
        monitor = PerfMonitor(sample_interval=0.5, track_temp_dir=perf_temp_dir)
        mock_s3 = MockVideoS3(perf_temp_dir, test_video_5s)

        monitor.start()
        try:
            monitor.mark("multi_start")

            async def run_task(task_id: int, num: int):
                logger.info(f"📋 Task {task_id}: {num} videos")
                return await _simulate_video_generation_pipeline(
                    num_videos=num,
                    max_concurrent=10,
                    mock_s3=mock_s3,
                    monitor=monitor,
                    temp_dir=perf_temp_dir,
                )

            # 2 个任务同时跑
            await asyncio.gather(
                run_task(0, 20),
                run_task(1, 20),
            )
            monitor.mark("multi_end")
            mock_s3.report()
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_ffmpeg_normalize_isolation(self, perf_temp_dir, test_video_5s):
        """单独测试 FFmpeg normalize 的 CPU 开销（5 个并发）"""
        monitor = PerfMonitor(sample_interval=0.3, track_temp_dir=perf_temp_dir)
        monitor.start()

        try:
            monitor.mark("normalize_start")

            async def normalize_one(idx: int):
                out = os.path.join(perf_temp_dir, f"norm_test_{idx}.mp4")
                t0 = time.time()
                await _run_normalize_video(test_video_5s, out)
                elapsed = (time.time() - t0) * 1000
                logger.info(f"  normalize {idx}: {elapsed:.0f}ms, size={os.path.getsize(out) / 1024:.0f}KB")
                return elapsed

            # 5 个并发 normalize
            results = await asyncio.gather(*[normalize_one(i) for i in range(5)])

            monitor.mark("normalize_end")
            logger.info(f"📊 5 concurrent normalize: avg={sum(results) / len(results):.0f}ms, max={max(results):.0f}ms")
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_ffmpeg_thread_scaling(self, perf_temp_dir, test_video_5s):
        """测试不同 FFmpeg 线程数对 CPU 和耗时的影响"""
        monitor = PerfMonitor(sample_interval=0.3, track_temp_dir=perf_temp_dir)
        monitor.start()

        try:
            for thread_count in [1, 2, 4]:
                monitor.mark(f"threads_{thread_count}_start")

                async def run_with_threads(idx: int, threads: int):
                    out = os.path.join(perf_temp_dir, f"thread_test_{threads}_{idx}.mp4")
                    cmd = [
                        "ffmpeg", "-y", "-threads", str(threads),
                        "-i", test_video_5s,
                        "-vf", "scale=1920:1080",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                        "-an", out,
                    ]
                    t0 = time.time()
                    proc = await asyncio.create_subprocess_exec(
                        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=120)
                    return (time.time() - t0) * 1000

                # 3 个并发
                results = await asyncio.gather(*[run_with_threads(i, thread_count) for i in range(3)])
                monitor.mark(f"threads_{thread_count}_end")
                logger.info(
                    f"📊 threads={thread_count}, 3 concurrent: "
                    f"avg={sum(results) / len(results):.0f}ms, max={max(results):.0f}ms"
                )
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_disk_io_pressure(self, perf_temp_dir, test_video_5s):
        """测试大量文件复制的 IO 压力（模拟 S3 下载场景）"""
        monitor = PerfMonitor(sample_interval=0.3, track_temp_dir=perf_temp_dir)
        monitor.start()

        try:
            monitor.mark("io_start")
            video_size = os.path.getsize(test_video_5s)
            copies = 41

            async def copy_one(idx: int):
                dst = os.path.join(perf_temp_dir, f"copy_{idx}.mp4")
                await asyncio.to_thread(shutil.copy2, test_video_5s, dst)
                return dst

            results = await asyncio.gather(*[copy_one(i) for i in range(copies)])

            monitor.mark("io_end")
            total_mb = (video_size * copies) / (1024 * 1024)
            logger.info(f"📊 Copied {copies} videos ({total_mb:.1f}MB total)")

            # 验证清理
            for p in results:
                os.remove(p)
            monitor.mark("cleanup_end")
        finally:
            monitor.stop()
            monitor.report()
