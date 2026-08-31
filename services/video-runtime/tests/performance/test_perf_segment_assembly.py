"""
Segment + Assembly 阶段性能测试
这是 CPU 最密集的阶段 —— 大量 FFmpeg 操作同时执行：
  - 5 个并发 segment 处理，每个 segment 内可能有多次 ffmpeg 调用
  - 下载 → ffprobe → 归一化 → 拼接 → 截断/垫黑 → 上传
  - Assembly: 下载所有 segment → 拼接 → 音频替换 → 水印 → 上传

核心测试:
  1. 单任务 41 segments 并发处理
  2. 多任务并发 segment 处理（CPU 压力极限）
  3. Assembly 完整流程
  4. FFmpeg 进程数峰值监控
  5. 临时文件生命周期追踪
  6. 存储分析：冗余下载检测

运行:
    cd /path/to/Cuti-VideoAgent
    conda activate cuti-video-local
    python -m pytest tests/performance/test_perf_segment_assembly.py -v -s
"""

import asyncio
import logging
import os
import shutil
import tempfile
import time
import uuid
from typing import Dict, List, Optional, Tuple

import psutil
import pytest

from .perf_monitor import PerfMonitor, create_test_video, get_disk_usage_summary

logger = logging.getLogger(__name__)

MOCK_CDN_BASE = "https://cdn-dev.newai.land"


# ═══════════════ Helpers ═══════════════


async def ffmpeg_concat_copy(input_paths: List[str], output_path: str, temp_dir: str) -> str:
    """用 -c copy 模式拼接视频（真实 FFmpeg）"""
    filelist_path = os.path.join(temp_dir, f"filelist_{uuid.uuid4().hex[:8]}.txt")
    lines = [f"file '{os.path.abspath(p)}'\n" for p in input_paths]
    await asyncio.to_thread(_write_filelist_sync, filelist_path, lines)

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", filelist_path, "-c", "copy", output_path]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"concat failed: {stderr.decode()[:300]}")

    await asyncio.to_thread(os.remove, filelist_path)
    return output_path


async def ffmpeg_trim(input_path: str, output_path: str, duration: float) -> str:
    """截断视频到指定时长（真实 FFmpeg）"""
    cmd = [
        "ffmpeg", "-y", "-threads", "2",
        "-i", input_path,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-an", output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"trim failed: {stderr.decode()[:300]}")
    return output_path


async def ffmpeg_normalize(input_path: str, output_path: str, width: int = 1920, height: int = 1080) -> str:
    """归一化到目标分辨率（真实 FFmpeg）"""
    cmd = [
        "ffmpeg", "-y", "-threads", "2",
        "-i", input_path,
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=25,format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"normalize failed: {stderr.decode()[:300]}")
    return output_path


async def ffmpeg_add_audio(video_path: str, audio_path: str, output_path: str) -> str:
    """为视频添加音频（真实 FFmpeg）"""
    cmd = [
        "ffmpeg", "-y", "-threads", "2",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy", "-c:a", "aac", "-shortest",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"add_audio failed: {stderr.decode()[:300]}")
    return output_path


async def ffmpeg_speed_adjust(input_path: str, output_path: str, speed_factor: float) -> str:
    """调速（真实 FFmpeg）"""
    cmd = [
        "ffmpeg", "-y", "-threads", "2",
        "-i", input_path,
        "-filter:v", f"setpts={1 / speed_factor:.6f}*PTS",
        "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"speed_adjust failed: {stderr.decode()[:300]}")
    return output_path


async def create_test_audio(output_path: str, duration: float = 10.0) -> str:
    """生成测试音频（真实 FFmpeg）"""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={duration}",
        "-c:a", "aac", "-b:a", "64k",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"create_audio failed: {stderr.decode()[:300]}")
    return output_path


def _write_filelist_sync(path: str, lines: list):
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ═══════════════ Segment Simulation ═══════════════


async def _process_single_segment(
    segment_number: int,
    video_paths: List[str],
    target_duration: float,
    temp_dir: str,
    is_lipsync: bool = False,
) -> Tuple[str, Dict]:
    """
    模拟真实的 segment 处理流程 (process_and_merge_videos / merge_and_trim_lipsync_videos):
    1. 每个视频 ffprobe
    2. 归一化（如果多个视频）
    3. 拼接
    4. 截断到目标时长 / 垫黑
    """
    metrics = {
        "segment_number": segment_number,
        "ffprobe_count": 0,
        "normalize_count": 0,
        "concat_ms": 0,
        "trim_ms": 0,
        "total_ms": 0,
        "ffmpeg_calls": 0,
    }
    t0 = time.time()

    if len(video_paths) == 1:
        current_path = video_paths[0]
    else:
        # 多个视频：归一化 + 拼接
        normalized = []
        for i, vp in enumerate(video_paths):
            metrics["ffprobe_count"] += 1
            norm_out = os.path.join(temp_dir, f"norm_seg{segment_number}_{i}_{uuid.uuid4().hex[:8]}.mp4")
            await ffmpeg_normalize(vp, norm_out)
            normalized.append(norm_out)
            metrics["normalize_count"] += 1
            metrics["ffmpeg_calls"] += 1

        t_concat = time.time()
        concat_out = os.path.join(temp_dir, f"concat_seg{segment_number}_{uuid.uuid4().hex[:8]}.mp4")
        await ffmpeg_concat_copy(normalized, concat_out, temp_dir)
        metrics["concat_ms"] = (time.time() - t_concat) * 1000
        metrics["ffmpeg_calls"] += 1
        current_path = concat_out

    # 截断到目标时长
    if target_duration > 0:
        t_trim = time.time()
        trimmed_out = os.path.join(temp_dir, f"trimmed_seg{segment_number}_{uuid.uuid4().hex[:8]}.mp4")
        await ffmpeg_trim(current_path, trimmed_out, target_duration)
        metrics["trim_ms"] = (time.time() - t_trim) * 1000
        metrics["ffmpeg_calls"] += 1
        current_path = trimmed_out

    metrics["total_ms"] = (time.time() - t0) * 1000
    return current_path, metrics


async def _simulate_segments_parallel(
    num_segments: int,
    videos_per_segment: int,
    max_concurrent: int,
    test_video_path: str,
    temp_dir: str,
    monitor: PerfMonitor,
) -> List[Tuple[str, Dict]]:
    """模拟并行 segment 处理"""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def process_with_semaphore(seg_num: int):
        async with semaphore:
            # 复制测试视频到临时目录（模拟 S3 下载）
            video_paths = []
            for i in range(videos_per_segment):
                dst = os.path.join(temp_dir, f"dl_seg{seg_num}_v{i}_{uuid.uuid4().hex[:8]}.mp4")
                await asyncio.to_thread(shutil.copy2, test_video_path, dst)
                video_paths.append(dst)

            target_duration = 4.5  # 典型的 segment 时长
            result = await _process_single_segment(
                seg_num, video_paths, target_duration, temp_dir
            )
            logger.info(
                f"  Segment {seg_num}: ffmpeg_calls={result[1]['ffmpeg_calls']}, "
                f"total={result[1]['total_ms']:.0f}ms"
            )
            return result

    monitor.mark("segments_start")
    results = await asyncio.gather(
        *[process_with_semaphore(i) for i in range(num_segments)],
        return_exceptions=True,
    )
    monitor.mark("segments_end")

    successful = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]
    if failures:
        for f in failures[:5]:
            logger.error(f"  ❌ Segment failed: {f}")
    return successful


async def _simulate_assembly(
    segment_video_paths: List[str],
    temp_dir: str,
    monitor: PerfMonitor,
    audio_duration: float = 180.0,
) -> str:
    """
    模拟 Assembly 完整流程:
    1. 拼接所有 segment
    2. 生成并添加音频
    3. 速度微调（如果需要）
    """
    monitor.mark("assembly_start")

    # 1. 拼接所有 segment
    t0 = time.time()
    merged_path = os.path.join(temp_dir, f"merged_all_{uuid.uuid4().hex[:8]}.mp4")
    await ffmpeg_concat_copy(segment_video_paths, merged_path, temp_dir)
    concat_ms = (time.time() - t0) * 1000
    logger.info(f"  Assembly concat: {concat_ms:.0f}ms")

    # 2. 生成测试音频
    audio_path = os.path.join(temp_dir, f"test_audio_{uuid.uuid4().hex[:8]}.m4a")
    await create_test_audio(audio_path, duration=min(audio_duration, 30.0))  # 限制为 30s 避免太慢

    # 3. 添加音频
    t1 = time.time()
    with_audio_path = os.path.join(temp_dir, f"with_audio_{uuid.uuid4().hex[:8]}.mp4")
    await ffmpeg_add_audio(merged_path, audio_path, with_audio_path)
    audio_ms = (time.time() - t1) * 1000
    logger.info(f"  Assembly add_audio: {audio_ms:.0f}ms")

    monitor.mark("assembly_end")

    final_size = os.path.getsize(with_audio_path) / (1024 * 1024)
    logger.info(f"  Assembly final: {final_size:.1f}MB")
    return with_audio_path


# ═══════════════ Fixtures ═══════════════


@pytest.fixture
def perf_temp_dir():
    d = tempfile.mkdtemp(prefix="perf_segment_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def test_video_5s(perf_temp_dir):
    """5 秒 1080p 测试视频"""
    path = os.path.join(perf_temp_dir, "source_5s.mp4")
    create_test_video(path, duration=5.0, width=1920, height=1080)
    logger.info(f"🎬 Test video: {path} ({os.path.getsize(path) / 1024:.0f}KB)")
    return path


@pytest.fixture
def test_video_3s(perf_temp_dir):
    """3 秒 1080p 测试视频"""
    path = os.path.join(perf_temp_dir, "source_3s.mp4")
    create_test_video(path, duration=3.0, width=1920, height=1080)
    return path


# ═══════════════ Tests ═══════════════


class TestSegmentPerformance:
    """Segment 阶段性能测试"""

    @pytest.mark.asyncio
    async def test_single_task_41_segments_1video_each(self, perf_temp_dir, test_video_5s):
        """模拟 41 个 segment，每个 1 个视频（最简场景，仅截断）"""
        monitor = PerfMonitor(sample_interval=0.5, track_temp_dir=perf_temp_dir)
        monitor.start()

        try:
            monitor.mark("test_start")
            disk_before = get_disk_usage_summary()

            results = await _simulate_segments_parallel(
                num_segments=41,
                videos_per_segment=1,
                max_concurrent=5,
                test_video_path=test_video_5s,
                temp_dir=perf_temp_dir,
                monitor=monitor,
            )

            disk_after = get_disk_usage_summary()
            logger.info(f"💾 Disk delta: {(disk_after['used_gb'] - disk_before['used_gb']) * 1024:.1f}MB")

            # 统计
            all_metrics = [r[1] for r in results]
            total_ffmpeg = sum(m["ffmpeg_calls"] for m in all_metrics)
            avg_total = sum(m["total_ms"] for m in all_metrics) / len(all_metrics) if all_metrics else 0
            logger.info(f"📊 41 segments (1 video each): total_ffmpeg_calls={total_ffmpeg}, avg_per_segment={avg_total:.0f}ms")

            monitor.mark("test_end")
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_single_task_41_segments_2videos_each(self, perf_temp_dir, test_video_5s):
        """模拟 41 个 segment，每个 2 个视频（需要归一化+拼接+截断）"""
        monitor = PerfMonitor(sample_interval=0.5, track_temp_dir=perf_temp_dir)
        monitor.start()

        try:
            monitor.mark("test_start")

            results = await _simulate_segments_parallel(
                num_segments=41,
                videos_per_segment=2,
                max_concurrent=5,
                test_video_path=test_video_5s,
                temp_dir=perf_temp_dir,
                monitor=monitor,
            )

            all_metrics = [r[1] for r in results]
            total_ffmpeg = sum(m["ffmpeg_calls"] for m in all_metrics)
            total_normalize = sum(m["normalize_count"] for m in all_metrics)
            avg_total = sum(m["total_ms"] for m in all_metrics) / len(all_metrics) if all_metrics else 0
            logger.info(
                f"📊 41 segments (2 videos each): total_ffmpeg_calls={total_ffmpeg}, "
                f"total_normalizes={total_normalize}, avg_per_segment={avg_total:.0f}ms"
            )

            monitor.mark("test_end")
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_multi_task_segments_concurrent(self, perf_temp_dir, test_video_5s):
        """模拟 2 个任务同时做 segment 处理"""
        monitor = PerfMonitor(sample_interval=0.5, track_temp_dir=perf_temp_dir)
        monitor.start()

        try:
            monitor.mark("multi_seg_start")

            async def run_task(task_id: int, num_segs: int):
                task_dir = os.path.join(perf_temp_dir, f"task_{task_id}")
                os.makedirs(task_dir, exist_ok=True)
                return await _simulate_segments_parallel(
                    num_segments=num_segs,
                    videos_per_segment=1,
                    max_concurrent=5,
                    test_video_path=test_video_5s,
                    temp_dir=task_dir,
                    monitor=monitor,
                )

            results = await asyncio.gather(
                run_task(0, 20),
                run_task(1, 20),
            )

            monitor.mark("multi_seg_end")

            for i, task_results in enumerate(results):
                total_ffmpeg = sum(r[1]["ffmpeg_calls"] for r in task_results)
                logger.info(f"  Task {i}: {len(task_results)} segments, {total_ffmpeg} ffmpeg calls")

        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_segment_concurrency_scaling(self, perf_temp_dir, test_video_5s):
        """测试不同并发数对 segment 处理的影响"""
        for max_concurrent in [2, 5, 10]:
            sub_dir = os.path.join(perf_temp_dir, f"scale_{max_concurrent}")
            os.makedirs(sub_dir, exist_ok=True)

            monitor = PerfMonitor(sample_interval=0.3, track_temp_dir=sub_dir)
            monitor.start()
            try:
                monitor.mark(f"scale_{max_concurrent}_start")

                results = await _simulate_segments_parallel(
                    num_segments=10,
                    videos_per_segment=1,
                    max_concurrent=max_concurrent,
                    test_video_path=test_video_5s,
                    temp_dir=sub_dir,
                    monitor=monitor,
                )

                monitor.mark(f"scale_{max_concurrent}_end")
            finally:
                monitor.stop()
                summaries = monitor.get_stage_summaries()
                if summaries:
                    s = summaries[0]
                    logger.info(
                        f"📊 max_concurrent={max_concurrent}: duration={s.duration_sec:.1f}s, "
                        f"avg_cpu={s.avg_cpu:.1f}%, max_cpu={s.max_cpu:.1f}%, "
                        f"peak_ffmpeg={s.max_ffmpeg_procs}, disk_w={s.disk_write_delta_mb:.1f}MB"
                    )


class TestAssemblyPerformance:
    """Assembly 阶段性能测试"""

    @pytest.mark.asyncio
    async def test_full_pipeline_segment_then_assembly(self, perf_temp_dir, test_video_5s):
        """完整 pipeline: 10 segments → assembly（端到端）"""
        monitor = PerfMonitor(sample_interval=0.5, track_temp_dir=perf_temp_dir)
        monitor.start()

        try:
            monitor.mark("pipeline_start")

            # Phase 1: Segments
            segment_results = await _simulate_segments_parallel(
                num_segments=10,
                videos_per_segment=1,
                max_concurrent=5,
                test_video_path=test_video_5s,
                temp_dir=perf_temp_dir,
                monitor=monitor,
            )
            segment_paths = [r[0] for r in segment_results]

            # Phase 2: Assembly
            final_path = await _simulate_assembly(
                segment_paths, perf_temp_dir, monitor, audio_duration=50.0
            )

            monitor.mark("pipeline_end")
            logger.info(f"✅ Final video: {final_path} ({os.path.getsize(final_path) / 1024 / 1024:.1f}MB)")
        finally:
            monitor.stop()
            monitor.report()


class TestStorageAnalysis:
    """存储分析测试"""

    @pytest.mark.asyncio
    async def test_temp_file_accumulation(self, perf_temp_dir, test_video_5s):
        """跟踪 segment 处理过程中临时文件的累积"""
        monitor = PerfMonitor(sample_interval=0.3, track_temp_dir=perf_temp_dir)
        monitor.start()

        try:
            monitor.mark("accum_start")

            for i in range(5):
                sub = os.path.join(perf_temp_dir, f"seg_{i}")
                os.makedirs(sub, exist_ok=True)

                # 模拟下载
                dl_path = os.path.join(sub, f"download_{i}.mp4")
                await asyncio.to_thread(shutil.copy2, test_video_5s, dl_path)
                monitor.mark(f"seg_{i}_downloaded")

                # 模拟处理
                norm_path = os.path.join(sub, f"norm_{i}.mp4")
                await ffmpeg_normalize(dl_path, norm_path)
                monitor.mark(f"seg_{i}_normalized")

                trim_path = os.path.join(sub, f"trim_{i}.mp4")
                await ffmpeg_trim(norm_path, trim_path, 4.5)
                monitor.mark(f"seg_{i}_trimmed")

            # 不清理 —— 看累积了多少
            total_size = 0
            total_files = 0
            for root, dirs, files in os.walk(perf_temp_dir):
                for f in files:
                    fp = os.path.join(root, f)
                    total_size += os.path.getsize(fp)
                    total_files += 1

            monitor.mark("accum_end")
            logger.info(f"📊 After 5 segments: {total_files} files, {total_size / 1024 / 1024:.1f}MB")
            logger.info("⚠️  Without cleanup, each segment leaves ~3 temp files (download + normalize + trim)")
            logger.info("💡 Recommendation: use task-scoped temp dir and delete on task completion")
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_redundant_download_detection(self, perf_temp_dir, test_video_5s):
        """
        检测冗余下载模式：
        在真实流程中，同一个视频会在 segment 处理时下载一次，
        又在 assembly 的 process_single_segment_download 中再次下载。
        """
        video_url = f"{MOCK_CDN_BASE}/videos/example_segment_1.mp4"

        download_log = []

        # 模拟 segment 阶段下载
        dl1 = os.path.join(perf_temp_dir, "segment_download_1.mp4")
        await asyncio.to_thread(shutil.copy2, test_video_5s, dl1)
        download_log.append(("segment_process", video_url, dl1))

        # 模拟 segment 处理后的结果上传到 S3
        uploaded_url = f"{MOCK_CDN_BASE}/videos/segment_1_result.mp4"

        # 模拟 assembly 阶段重新下载已处理的 segment
        dl2 = os.path.join(perf_temp_dir, "assembly_download_1.mp4")
        await asyncio.to_thread(shutil.copy2, test_video_5s, dl2)
        download_log.append(("assembly", uploaded_url, dl2))

        logger.info("\n📊 REDUNDANT DOWNLOAD ANALYSIS:")
        logger.info("=" * 80)
        logger.info(f"  Downloads logged: {len(download_log)}")
        for stage, url, path in download_log:
            size = os.path.getsize(path) / 1024
            logger.info(f"    [{stage}] {url[:60]}... → {path} ({size:.0f}KB)")

        logger.info("\n⚠️  FINDINGS:")
        logger.info("  1. Segment processing downloads video from S3 → processes → uploads result back to S3")
        logger.info("  2. Assembly then downloads the same processed video from S3 again")
        logger.info("  3. This doubles S3 bandwidth and disk I/O for every segment")
        logger.info("\n💡 RECOMMENDATIONS:")
        logger.info("  1. Keep segment results in local temp dir, pass paths to assembly (skip re-download)")
        logger.info("  2. Use task-scoped temp dir: /tmp/{user_id}/{thread_id}/{run_id}/")
        logger.info("  3. Only upload final assembly result to S3, not intermediate segments")
        logger.info("  4. Or: assembly reads from local cache before falling back to S3 download")

    @pytest.mark.asyncio
    async def test_temp_dir_cleanup_simulation(self, perf_temp_dir, test_video_5s):
        """模拟任务级别的临时目录管理"""
        user_id = "user_123"
        thread_id = "full-0ba90767-eff-eff_2"
        run_id = "391f2a25-559b-48a7-9bf0-0ed335356814"

        # 提议的目录结构
        task_temp_dir = os.path.join(perf_temp_dir, user_id, thread_id, run_id)
        os.makedirs(task_temp_dir, exist_ok=True)

        # 模拟各阶段的文件
        stages = {
            "keyframe": 41,     # 41 个关键帧图片
            "video": 41,        # 41 个视频
            "segment": 41 * 3,  # 每个 segment: download + normalize + trim
            "assembly": 5,      # assembly 阶段的中间文件
        }

        created_files = []
        for stage, count in stages.items():
            stage_dir = os.path.join(task_temp_dir, stage)
            os.makedirs(stage_dir, exist_ok=True)
            for i in range(count):
                f = os.path.join(stage_dir, f"{stage}_{i}.tmp")
                with open(f, "wb") as fh:
                    fh.write(b"x" * 1024)  # 1KB placeholder
                created_files.append(f)

        # 统计
        total_before = sum(os.path.getsize(f) for f in created_files)
        logger.info(f"\n📊 TASK TEMP DIR STRUCTURE:")
        logger.info(f"  Path: {task_temp_dir}")
        logger.info(f"  Total files: {len(created_files)}")
        logger.info(f"  Total size: {total_before / 1024:.0f}KB (placeholder; real would be ~5GB)")

        for stage, count in stages.items():
            logger.info(f"    {stage}/: {count} files")

        # 模拟任务完成后清理
        shutil.rmtree(task_temp_dir)
        assert not os.path.exists(task_temp_dir)

        logger.info("\n✅ Task temp dir cleaned up successfully")
        logger.info("\n💡 PROPOSED CLEANUP STRATEGY:")
        logger.info("  1. Create /tmp/{user_id}/{thread_id}/{run_id}/ at task start")
        logger.info("  2. All stages write to subdirectories within this dir")
        logger.info("  3. shutil.rmtree() the entire dir at task completion (success or failure)")
        logger.info("  4. Add periodic cleanup cron for abandoned dirs (>24h old)")
        logger.info("  5. Estimated savings per task: ~5GB disk space")

    @pytest.mark.asyncio
    async def test_ffmpeg_process_peak(self, perf_temp_dir, test_video_5s):
        """监测 FFmpeg 进程数峰值"""
        monitor = PerfMonitor(sample_interval=0.2, track_temp_dir=perf_temp_dir)
        monitor.start()

        try:
            monitor.mark("peak_start")

            # 启动 10 个并发 FFmpeg normalize（模拟极限场景）
            async def run_one(idx: int):
                out = os.path.join(perf_temp_dir, f"peak_{idx}.mp4")
                return await ffmpeg_normalize(test_video_5s, out)

            results = await asyncio.gather(
                *[run_one(i) for i in range(10)],
                return_exceptions=True,
            )

            monitor.mark("peak_end")

            # 分析 FFmpeg 进程峰值
            ffmpeg_samples = [s.ffmpeg_process_count for s in monitor._samples]
            if ffmpeg_samples:
                peak = max(ffmpeg_samples)
                avg = sum(ffmpeg_samples) / len(ffmpeg_samples)
                logger.info(f"\n📊 FFmpeg PROCESS ANALYSIS:")
                logger.info(f"  Peak concurrent FFmpeg: {peak}")
                logger.info(f"  Average concurrent FFmpeg: {avg:.1f}")
                logger.info(f"  CPU cores available: {psutil.cpu_count()}")
                logger.info(f"  FFmpeg threads per process: 2 (configured)")
                logger.info(f"  Effective thread usage: {peak * 2} / {psutil.cpu_count()} cores")

                if peak * 2 > psutil.cpu_count():
                    logger.warning(
                        f"⚠️  FFmpeg threads ({peak * 2}) exceed CPU cores ({psutil.cpu_count()})! "
                        f"This causes CPU contention."
                    )
                    logger.info("💡 RECOMMENDATIONS:")
                    logger.info(f"  1. Reduce video_segments_processing from 5 to {max(1, psutil.cpu_count() // 4)}")
                    logger.info(f"  2. Or reduce ffmpeg_threads from 2 to 1")
                    logger.info(f"  3. Consider using ProcessPoolExecutor with max_workers={psutil.cpu_count() // 2}")
        finally:
            monitor.stop()
            monitor.report()
