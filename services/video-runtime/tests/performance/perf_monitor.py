"""
性能监控工具
提供 CPU / Memory / Disk IO / 临时文件 的实时采样和阶段对比

用法:
    monitor = PerfMonitor(sample_interval=0.5)
    monitor.start()

    monitor.mark("keyframe_start")
    # ... do keyframe work ...
    monitor.mark("keyframe_end")

    monitor.stop()
    monitor.report()          # 打印阶段摘要
    monitor.dump_csv(path)    # 可选，写入 CSV 供后续分析
"""

import asyncio
import csv
import logging
import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import psutil

logger = logging.getLogger(__name__)

TEMP_DIR = tempfile.gettempdir()


@dataclass
class Sample:
    ts: float
    cpu_percent: float
    cpu_per_core: List[float]
    mem_rss_mb: float
    mem_vms_mb: float
    mem_system_percent: float
    mem_system_total_mb: float
    mem_system_available_mb: float
    mem_system_used_mb: float
    swap_total_mb: float
    swap_used_mb: float
    swap_percent: float
    disk_read_mb: float
    disk_write_mb: float
    io_read_count: int
    io_write_count: int
    temp_dir_size_mb: float
    temp_file_count: int
    open_fd_count: int
    thread_count: int
    child_process_count: int
    ffmpeg_process_count: int


@dataclass
class StageSnapshot:
    """mark() 时记录的瞬时快照"""
    ts: float
    cpu_percent: float
    mem_rss_mb: float
    disk_read_mb: float
    disk_write_mb: float
    temp_dir_size_mb: float
    temp_file_count: int


@dataclass
class StageSummary:
    """两个 mark 之间的统计"""
    name: str
    duration_sec: float
    avg_cpu: float
    max_cpu: float
    avg_mem_rss_mb: float
    max_mem_rss_mb: float
    max_mem_vms_mb: float
    avg_sys_mem_used_mb: float
    max_sys_mem_used_mb: float
    sys_mem_total_mb: float
    avg_swap_used_mb: float
    max_swap_used_mb: float
    swap_total_mb: float
    disk_read_delta_mb: float
    disk_write_delta_mb: float
    temp_file_delta: int
    temp_size_delta_mb: float
    max_ffmpeg_procs: int
    max_threads: int


class PerfMonitor:
    def __init__(self, sample_interval: float = 0.5, track_temp_dir: Optional[str] = None):
        self._interval = sample_interval
        self._track_temp_dir = track_temp_dir or TEMP_DIR
        self._samples: List[Sample] = []
        self._marks: Dict[str, StageSnapshot] = {}
        self._mark_order: List[str] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._process = psutil.Process(os.getpid())
        self._disk_io_base: Optional[psutil._common.sdiskio] = None

    def start(self):
        """启动后台采样线程"""
        if self._running:
            return
        self._running = True
        try:
            self._disk_io_base = psutil.disk_io_counters()
        except Exception:
            self._disk_io_base = None
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()
        logger.info("📊 PerfMonitor started (interval=%.1fs, temp_dir=%s)", self._interval, self._track_temp_dir)

    def stop(self):
        """停止采样"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("📊 PerfMonitor stopped, %d samples collected", len(self._samples))

    def mark(self, label: str):
        """标记一个时间点（阶段边界），记录瞬时快照"""
        snap = self._take_snapshot()
        self._marks[label] = snap
        self._mark_order.append(label)
        logger.info(
            "📍 MARK [%s] cpu=%.1f%% mem=%.1fMB disk_r=%.1fMB disk_w=%.1fMB temp_files=%d (%.1fMB)",
            label, snap.cpu_percent, snap.mem_rss_mb,
            snap.disk_read_mb, snap.disk_write_mb,
            snap.temp_file_count, snap.temp_dir_size_mb,
        )

    def get_stage_summaries(self) -> List[StageSummary]:
        """根据 mark 对计算阶段摘要"""
        summaries = []
        for i in range(len(self._mark_order) - 1):
            start_label = self._mark_order[i]
            end_label = self._mark_order[i + 1]
            start_snap = self._marks[start_label]
            end_snap = self._marks[end_label]
            stage_name = f"{start_label} → {end_label}"

            stage_samples = [s for s in self._samples if start_snap.ts <= s.ts <= end_snap.ts]
            if not stage_samples:
                continue

            summaries.append(StageSummary(
                name=stage_name,
                duration_sec=end_snap.ts - start_snap.ts,
                avg_cpu=sum(s.cpu_percent for s in stage_samples) / len(stage_samples),
                max_cpu=max(s.cpu_percent for s in stage_samples),
                avg_mem_rss_mb=sum(s.mem_rss_mb for s in stage_samples) / len(stage_samples),
                max_mem_rss_mb=max(s.mem_rss_mb for s in stage_samples),
                max_mem_vms_mb=max(s.mem_vms_mb for s in stage_samples),
                avg_sys_mem_used_mb=sum(s.mem_system_used_mb for s in stage_samples) / len(stage_samples),
                max_sys_mem_used_mb=max(s.mem_system_used_mb for s in stage_samples),
                sys_mem_total_mb=stage_samples[0].mem_system_total_mb,
                avg_swap_used_mb=sum(s.swap_used_mb for s in stage_samples) / len(stage_samples),
                max_swap_used_mb=max(s.swap_used_mb for s in stage_samples),
                swap_total_mb=stage_samples[0].swap_total_mb,
                disk_read_delta_mb=end_snap.disk_read_mb - start_snap.disk_read_mb,
                disk_write_delta_mb=end_snap.disk_write_mb - start_snap.disk_write_mb,
                temp_file_delta=end_snap.temp_file_count - start_snap.temp_file_count,
                temp_size_delta_mb=end_snap.temp_dir_size_mb - start_snap.temp_dir_size_mb,
                max_ffmpeg_procs=max(s.ffmpeg_process_count for s in stage_samples),
                max_threads=max(s.thread_count for s in stage_samples),
            ))
        return summaries

    def report(self):
        """打印阶段对比报告"""
        summaries = self.get_stage_summaries()
        if not summaries:
            logger.info("📊 No stage data to report")
            return

        logger.info("\n" + "=" * 120)
        logger.info("📊 PERFORMANCE REPORT")
        logger.info("=" * 120)
        header = f"{'Stage':<45} {'Duration':>8} {'AvgCPU':>8} {'MaxCPU':>8} {'AvgMem':>8} {'MaxMem':>8} {'DiskR':>8} {'DiskW':>8} {'TmpΔ':>6} {'FFmpeg':>6} {'Thr':>5}"
        logger.info(header)
        logger.info("-" * 120)
        for s in summaries:
            logger.info(
                f"{s.name:<45} {s.duration_sec:>7.1f}s {s.avg_cpu:>7.1f}% {s.max_cpu:>7.1f}% "
                f"{s.avg_mem_rss_mb:>7.1f}M {s.max_mem_rss_mb:>7.1f}M "
                f"{s.disk_read_delta_mb:>7.1f}M {s.disk_write_delta_mb:>7.1f}M "
                f"{s.temp_file_delta:>+5d} {s.max_ffmpeg_procs:>5d} {s.max_threads:>5d}"
            )
        logger.info("=" * 120)

        # 汇总分析
        total_duration = sum(s.duration_sec for s in summaries)
        peak_cpu = max(s.max_cpu for s in summaries) if summaries else 0
        peak_mem_rss = max(s.max_mem_rss_mb for s in summaries) if summaries else 0
        peak_mem_vms = max(s.max_mem_vms_mb for s in summaries) if summaries else 0
        total_disk_r = sum(s.disk_read_delta_mb for s in summaries)
        total_disk_w = sum(s.disk_write_delta_mb for s in summaries)
        peak_ffmpeg = max(s.max_ffmpeg_procs for s in summaries) if summaries else 0
        peak_sys_mem = max(s.max_sys_mem_used_mb for s in summaries) if summaries else 0
        sys_total = summaries[0].sys_mem_total_mb if summaries else 0
        peak_swap = max(s.max_swap_used_mb for s in summaries) if summaries else 0
        swap_total = summaries[0].swap_total_mb if summaries else 0

        logger.info("\n📈 SUMMARY:")
        logger.info(f"  Total duration:      {total_duration:.1f}s")
        logger.info(f"  Peak CPU:            {peak_cpu:.1f}%")
        logger.info(f"  Peak FFmpeg procs:   {peak_ffmpeg}")
        logger.info(f"  --- Memory (Process) ---")
        logger.info(f"  Peak RSS:            {peak_mem_rss:.1f} MB")
        logger.info(f"  Peak VMS:            {peak_mem_vms:.1f} MB")
        logger.info(f"  --- Memory (System) ---")
        logger.info(f"  System Total:        {sys_total:.0f} MB")
        logger.info(f"  Peak System Used:    {peak_sys_mem:.0f} MB ({peak_sys_mem/sys_total*100:.1f}%)" if sys_total else "  Peak System Used:    N/A")
        logger.info(f"  --- Swap ---")
        logger.info(f"  Swap Total:          {swap_total:.0f} MB")
        logger.info(f"  Peak Swap Used:      {peak_swap:.0f} MB ({peak_swap/swap_total*100:.1f}%)" if swap_total else "  Peak Swap Used:      0 MB (no swap)")
        logger.info(f"  --- Disk I/O ---")
        logger.info(f"  Total Disk Read:     {total_disk_r:.1f} MB")
        logger.info(f"  Total Disk Write:    {total_disk_w:.1f} MB")

        if peak_cpu > 90:
            logger.warning("⚠️  CPU peaked above 90%% — likely FFmpeg/PIL contention")
        if peak_ffmpeg > 3:
            logger.warning("⚠️  %d concurrent FFmpeg processes detected — reduce video_segments_processing or ffmpeg_threads", peak_ffmpeg)
        if swap_total and peak_swap > swap_total * 0.5:
            logger.warning("⚠️  Swap usage above 50%% (%d/%d MB) — memory pressure detected", int(peak_swap), int(swap_total))

    def dump_csv(self, path: str):
        """将所有采样数据写入 CSV"""
        if not self._samples:
            return
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "cpu_percent", "cpu_per_core", "mem_rss_mb", "mem_vms_mb",
                "mem_system_percent", "mem_system_total_mb", "mem_system_available_mb",
                "mem_system_used_mb", "swap_total_mb", "swap_used_mb", "swap_percent",
                "disk_read_mb", "disk_write_mb",
                "io_read_count", "io_write_count", "temp_dir_size_mb", "temp_file_count",
                "open_fd_count", "thread_count", "child_process_count", "ffmpeg_process_count",
            ])
            for s in self._samples:
                writer.writerow([
                    f"{s.ts:.3f}", f"{s.cpu_percent:.1f}", str(s.cpu_per_core),
                    f"{s.mem_rss_mb:.1f}", f"{s.mem_vms_mb:.1f}",
                    f"{s.mem_system_percent:.1f}", f"{s.mem_system_total_mb:.0f}",
                    f"{s.mem_system_available_mb:.0f}", f"{s.mem_system_used_mb:.0f}",
                    f"{s.swap_total_mb:.0f}", f"{s.swap_used_mb:.0f}", f"{s.swap_percent:.1f}",
                    f"{s.disk_read_mb:.1f}", f"{s.disk_write_mb:.1f}",
                    s.io_read_count, s.io_write_count,
                    f"{s.temp_dir_size_mb:.1f}", s.temp_file_count,
                    s.open_fd_count, s.thread_count, s.child_process_count, s.ffmpeg_process_count,
                ])
        logger.info("📊 Samples written to %s (%d rows)", path, len(self._samples))

    # ── internal ──

    def _sample_loop(self):
        while self._running:
            try:
                sample = self._collect_sample()
                self._samples.append(sample)
            except Exception as e:
                logger.debug("Sample error: %s", e)
            time.sleep(self._interval)

    def _collect_sample(self) -> Sample:
        proc = self._process

        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)

        mem_info = proc.memory_info()
        mem_system = psutil.virtual_memory()
        swap_info = psutil.swap_memory()

        disk_read_mb = 0.0
        disk_write_mb = 0.0
        io_read_count = 0
        io_write_count = 0
        try:
            io = proc.io_counters()
            disk_read_mb = io.read_bytes / (1024 * 1024)
            disk_write_mb = io.write_bytes / (1024 * 1024)
            io_read_count = io.read_count
            io_write_count = io.write_count
        except (psutil.AccessDenied, AttributeError):
            pass

        temp_size_mb, temp_count = self._get_temp_stats()

        open_fd = 0
        try:
            open_fd = proc.num_fds()
        except (psutil.AccessDenied, AttributeError):
            pass

        thread_count = proc.num_threads()

        child_count = 0
        ffmpeg_count = 0
        try:
            children = proc.children(recursive=True)
            child_count = len(children)
            for child in children:
                try:
                    if "ffmpeg" in child.name().lower() or "ffprobe" in child.name().lower():
                        ffmpeg_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        return Sample(
            ts=time.time(),
            cpu_percent=cpu_percent,
            cpu_per_core=cpu_per_core,
            mem_rss_mb=mem_info.rss / (1024 * 1024),
            mem_vms_mb=mem_info.vms / (1024 * 1024),
            mem_system_percent=mem_system.percent,
            mem_system_total_mb=mem_system.total / (1024 * 1024),
            mem_system_available_mb=mem_system.available / (1024 * 1024),
            mem_system_used_mb=mem_system.used / (1024 * 1024),
            swap_total_mb=swap_info.total / (1024 * 1024),
            swap_used_mb=swap_info.used / (1024 * 1024),
            swap_percent=swap_info.percent,
            disk_read_mb=disk_read_mb,
            disk_write_mb=disk_write_mb,
            io_read_count=io_read_count,
            io_write_count=io_write_count,
            temp_dir_size_mb=temp_size_mb,
            temp_file_count=temp_count,
            open_fd_count=open_fd,
            thread_count=thread_count,
            child_process_count=child_count,
            ffmpeg_process_count=ffmpeg_count,
        )

    def _get_temp_stats(self) -> Tuple[float, int]:
        """统计临时目录的大小和文件数（浅层扫描避免自身成为瓶颈）"""
        total_size = 0
        count = 0
        try:
            for entry in os.scandir(self._track_temp_dir):
                try:
                    if entry.is_file(follow_symlinks=False):
                        total_size += entry.stat().st_size
                        count += 1
                    elif entry.is_dir(follow_symlinks=False):
                        count += 1
                        # 只统计一级子目录大小（避免递归耗时）
                        for sub in os.scandir(entry.path):
                            if sub.is_file(follow_symlinks=False):
                                total_size += sub.stat().st_size
                                count += 1
                except (PermissionError, OSError):
                    pass
        except (PermissionError, OSError):
            pass
        return total_size / (1024 * 1024), count

    def _take_snapshot(self) -> StageSnapshot:
        s = self._collect_sample()
        return StageSnapshot(
            ts=s.ts,
            cpu_percent=s.cpu_percent,
            mem_rss_mb=s.mem_rss_mb,
            disk_read_mb=s.disk_read_mb,
            disk_write_mb=s.disk_write_mb,
            temp_dir_size_mb=s.temp_dir_size_mb,
            temp_file_count=s.temp_file_count,
        )


def create_test_video(output_path: str, duration: float = 5.0, width: int = 1920, height: int = 1080) -> str:
    """用 FFmpeg 生成纯黑色测试视频（用于模拟真实的视频处理流程）"""
    import subprocess
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=black:s={width}x{height}:d={duration}:r=25",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True, timeout=30)
    return output_path


def create_test_image(output_path: str, width: int = 1920, height: int = 1080) -> str:
    """创建纯色测试图片"""
    from PIL import Image
    img = Image.new("RGB", (width, height), color=(0, 0, 0))
    img.save(output_path, "WEBP")
    return output_path


def get_disk_usage_summary(path: str = "/tmp") -> Dict:
    """磁盘使用汇总"""
    usage = shutil.disk_usage(path)
    return {
        "total_gb": usage.total / (1024 ** 3),
        "used_gb": usage.used / (1024 ** 3),
        "free_gb": usage.free / (1024 ** 3),
        "percent_used": (usage.used / usage.total) * 100,
    }
