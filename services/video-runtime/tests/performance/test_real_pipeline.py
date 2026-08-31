"""
真实 Pipeline 性能瓶颈排查 — 直接调用 node 入口函数

策略:
  1. 从 DB 读取真实 run 的所有 UUID
  2. 构建 VideoAgentState
  3. Mock S3（用本地文件替代网络 I/O）
  4. Mock DB 写入（避免产生脏数据）
  5. 直接调用 video_segments_node — 真实 FFmpeg

Phase A: BASELINE — 真实 FFmpeg + 真实临时文件 + mock S3
Phase B: mock FFmpeg（所有 video_utils FFmpeg 函数替换为文件拷贝）
  → DELTA(A-B) = FFmpeg 的准确 CPU 贡献
Phase C: mock 文件 I/O（连本地拷贝也跳过）
  → DELTA(B-C) = 文件 I/O 的准确 CPU 贡献

运行:
    cd /home/songsong/local/Cuti-VideoAgent
    conda activate cuti-video-local
    # 跑全部对比
    python -m pytest tests/performance/test_real_pipeline.py::TestCompare -v -s --tb=long
    # 单独跑 Phase A
    python -m pytest tests/performance/test_real_pipeline.py::TestPhaseA -v -s --tb=long
"""

import asyncio
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional, Union
from unittest.mock import AsyncMock, MagicMock, patch

import psutil
import pytest

from .perf_monitor import PerfMonitor, create_test_video

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
# 目标 run — 从日志提取
# ════════════════════════════════════════════════════════════════════

TARGET_RUN_ID = "391f2a25-559b-48a7-9bf0-0ed335356814"
TARGET_THREAD_ID = "full-0ba90767-eff-eff_2"


# ════════════════════════════════════════════════════════════════════
# DB 初始化 / 清理
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
async def db_pool():
    """初始化 asyncpg 连接池（每个 test 函数独立，确保同一 event loop）"""
    from app.models.database import init_asyncpg_pool, close_asyncpg_pool, get_asyncpg_pool
    from app.models.database import _asyncpg_pool
    import app.models.database as db_module
    # 如果已经初始化过（上一个 test 留下的），先重置
    if db_module._asyncpg_pool is not None:
        try:
            await close_asyncpg_pool()
        except Exception:
            pass
        db_module._asyncpg_pool = None
    await init_asyncpg_pool()
    yield get_asyncpg_pool()
    await close_asyncpg_pool()


@pytest.fixture
async def task_record(db_pool):
    """从 DB 读取真实 run 的 task record"""
    from app.crud.error_tracking import get_task_record_by_run_id
    record = await get_task_record_by_run_id(TARGET_RUN_ID)
    assert record is not None, f"找不到 run_id={TARGET_RUN_ID} 的 task record，请确认 DB 中有该数据"
    logger.info(f"✅ 已加载 task record: run_id={TARGET_RUN_ID}")
    logger.info(f"   shots={len(record.shot_uuids or [])}, keyframes={len(record.keyframe_uuids or [])}, "
                f"video_gens={len(record.video_generation_uuids or [])}, segments={len(record.video_segments_uuids or [])}")
    return record


@pytest.fixture
def real_state(task_record) -> Dict[str, Any]:
    """从 task_record 构建真实的 VideoAgentState"""
    return {
        "user_id": task_record.user_id or "test_user",
        "conversation_id": str(task_record.conversation_id or "0"),
        "thread_id": task_record.thread_id or TARGET_THREAD_ID,
        "run_id": task_record.task_id or TARGET_RUN_ID,
        "analysis_uuid": task_record.analysis_uuid,
        "audio_transcription_uuids": task_record.audio_transcription_uuids or [],
        "story_outline_uuid": task_record.story_outline_uuid,
        "character_uuids": task_record.character_uuids or [],
        "scene_uuids": task_record.scene_uuids or [],
        "shot_uuids": task_record.shot_uuids or [],
        "keyframe_uuids": task_record.keyframe_uuids or [],
        "narration_uuids": task_record.narration_uuids or [],
        "audio_effect_uuids": task_record.audio_effect_uuids or [],
        "video_generation_uuids": task_record.video_generation_uuids or [],
        "music_generation_uuids": task_record.music_generation_uuids or [],
        "video_segments_uuids": task_record.video_segments_uuids or [],
        "audio_files": [],
        "user_input_data": None,
        "detected_language": "zh",
    }


@pytest.fixture
def work_dir():
    d = tempfile.mkdtemp(prefix="perf_real_node_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def test_video(work_dir):
    """生成 5 秒 1080p 测试视频（真实 H.264）"""
    path = os.path.join(work_dir, "source_5s.mp4")
    create_test_video(path, duration=5.0, width=1920, height=1080)
    logger.info(f"🎬 Test video: {path} ({os.path.getsize(path) / 1024:.0f}KB)")
    return path


# ════════════════════════════════════════════════════════════════════
# Mock 层
# ════════════════════════════════════════════════════════════════════


def make_s3_mock(test_video_path: str, upload_dir: str):
    """
    创建 S3 mock 对象，替换 s3_utils 单例。
    download_file: 复制测试视频到目标路径（保留真实磁盘 I/O）
    upload_file/upload_video: 写到本地目录，返回 fake CDN URL
    """
    mock = MagicMock()
    download_count = {"n": 0, "bytes": 0}
    upload_count = {"n": 0, "bytes": 0}

    async def mock_download(file_key_or_url: str, local_path: str) -> bool:
        download_count["n"] += 1
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        await asyncio.to_thread(shutil.copy2, test_video_path, local_path)
        download_count["bytes"] += os.path.getsize(local_path)
        return True

    async def mock_upload_file(file_data: bytes, file_key: str, content_type=None) -> str:
        upload_count["n"] += 1
        upload_count["bytes"] += len(file_data)
        out = os.path.join(upload_dir, file_key.replace("/", "_"))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(file_data)
        return f"https://cdn-dev.newai.land/{file_key}"

    async def mock_upload_video(video_data: bytes, filename=None, generation_id=None, content_type="video/mp4") -> str:
        name = f"{generation_id or uuid.uuid4()}.mp4"
        return await mock_upload_file(video_data, f"videos/{name}", content_type)

    def mock_cdn_url_to_s3_key(cdn_url: str):
        if not cdn_url:
            return None
        from urllib.parse import urlparse
        return urlparse(cdn_url).path.lstrip("/")

    mock.download_file = mock_download
    mock.upload_file = mock_upload_file
    mock.upload_video = mock_upload_video
    mock.cdn_url_to_s3_key = mock_cdn_url_to_s3_key
    mock._stats = {"download": download_count, "upload": upload_count}
    return mock


def make_noop_send_event():
    """空的 send_event_func"""
    async def noop(**kwargs):
        pass
    return noop


def make_mock_runtime():
    """mock Runtime[VideoContextSchema]"""
    return MagicMock()


# ════════════════════════════════════════════════════════════════════
# Phase B: FFmpeg 函数级 mock
# ════════════════════════════════════════════════════════════════════

VIDEO_UTILS_MODULE = "app.utils.video_utils"


def ffmpeg_function_mocks(test_video_path: str):
    """返回一组 patch，把所有 FFmpeg 函数替换为文件拷贝"""

    async def mock_get_duration(video_input):
        return 5.0

    async def mock_get_stream_info(video_path):
        return {"width": 1920, "height": 1080, "r_frame_rate": "25/1",
                "codec_name": "h264", "pix_fmt": "yuv420p"}

    async def mock_check_audio(video_path):
        return False

    async def mock_align(local_path, target_dur, output_path, tolerance=0.05):
        await asyncio.to_thread(shutil.copy2, local_path, output_path)
        return output_path

    async def mock_concat(local_paths, temp_dir, output_path):
        if local_paths:
            await asyncio.to_thread(shutil.copy2, local_paths[0], output_path)

    async def mock_trim(input_path, target_dur, output_path):
        await asyncio.to_thread(shutil.copy2, input_path, output_path)

    async def mock_create_black(duration, output_path, width=1920, height=1080, fps=25):
        await asyncio.to_thread(shutil.copy2, test_video_path, output_path)

    return [
        patch(f"{VIDEO_UTILS_MODULE}.get_video_duration", side_effect=mock_get_duration),
        patch(f"{VIDEO_UTILS_MODULE}.get_video_stream_info_async", side_effect=mock_get_stream_info),
        patch(f"{VIDEO_UTILS_MODULE}.check_video_has_audio_async", side_effect=mock_check_audio),
        patch(f"{VIDEO_UTILS_MODULE}.align_video_to_duration_exact_local", side_effect=mock_align),
        patch(f"{VIDEO_UTILS_MODULE}.concat_local_videos_with_normalize", side_effect=mock_concat),
        patch(f"{VIDEO_UTILS_MODULE}.trim_video_to_duration_exact_local", side_effect=mock_trim),
        patch(f"{VIDEO_UTILS_MODULE}.create_black_placeholder_video", side_effect=mock_create_black),
    ]


# ════════════════════════════════════════════════════════════════════
# 调用真实 node 的统一入口
# ════════════════════════════════════════════════════════════════════


async def run_segments_node(state, s3_mock, extra_patches=None):
    """
    直接调用真实的 video_segments_node
    mock: S3、DB 写入、send_event_func
    real: FFmpeg、temp 文件管理、DB 读取
    """
    from app.services.agent.video.video_segments_service import video_segments_node

    runtime = make_mock_runtime()
    send_event = make_noop_send_event()

    patches = [
        # S3 mock
        patch("app.utils.temp_file_utils.s3_utils", s3_mock),
        # DB 写入 mock — 避免产生脏数据
        patch(
            "app.services.agent.video.video_segments_service._save_video_segments_to_db",
            new_callable=AsyncMock,
            return_value=[f"mock-seg-uuid-{i}" for i in range(50)],
        ),
    ]
    if extra_patches:
        patches.extend(extra_patches)

    ctx = [p.__enter__() if hasattr(p, '__enter__') else p.start() for p in patches]
    try:
        result = await video_segments_node(state, runtime, send_event)
        return result
    finally:
        for p in patches:
            if hasattr(p, '__exit__'):
                p.__exit__(None, None, None)
            else:
                p.stop()


# ════════════════════════════════════════════════════════════════════
# Tests
# ════════════════════════════════════════════════════════════════════


class TestPhaseA:
    """Phase A: 真实 FFmpeg + mock S3 → BASELINE"""

    @pytest.mark.asyncio
    async def test_segments_baseline(self, db_pool, real_state, work_dir, test_video):
        s3 = make_s3_mock(test_video, os.path.join(work_dir, "s3_uploads"))
        monitor = PerfMonitor(sample_interval=0.5)
        monitor.start()
        try:
            monitor.mark("A_start")
            result = await run_segments_node(real_state, s3)
            monitor.mark("A_end")
            logger.info(f"✅ Segments node 返回: {list(result.keys()) if isinstance(result, dict) else type(result)}")
            logger.info(f"📦 S3 stats: downloads={s3._stats['download']['n']} "
                        f"({s3._stats['download']['bytes']/1024/1024:.1f}MB), "
                        f"uploads={s3._stats['upload']['n']} "
                        f"({s3._stats['upload']['bytes']/1024/1024:.1f}MB)")
        finally:
            monitor.stop()
            monitor.report()
            monitor.dump_csv(os.path.join(work_dir, "phase_a.csv"))


class TestPhaseB:
    """Phase B: mock FFmpeg → 对比 CPU delta"""

    @pytest.mark.asyncio
    async def test_segments_no_ffmpeg(self, db_pool, real_state, work_dir, test_video):
        s3 = make_s3_mock(test_video, os.path.join(work_dir, "s3_uploads_b"))
        ff_patches = ffmpeg_function_mocks(test_video)
        monitor = PerfMonitor(sample_interval=0.5)
        monitor.start()
        try:
            monitor.mark("B_start")
            result = await run_segments_node(real_state, s3, extra_patches=ff_patches)
            monitor.mark("B_end")
            logger.info(f"✅ Segments node (no FFmpeg) 返回: {list(result.keys()) if isinstance(result, dict) else type(result)}")
        finally:
            monitor.stop()
            monitor.report()
            monitor.dump_csv(os.path.join(work_dir, "phase_b.csv"))


class TestPhaseC:
    """Phase C: mock FFmpeg + mock 文件 I/O"""

    @pytest.mark.asyncio
    async def test_segments_no_ffmpeg_no_io(self, db_pool, real_state, work_dir, test_video):
        # S3 mock: instant — 不写入真实文件
        s3 = MagicMock()
        async def instant_download(url, path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"\x00" * 1024)
            return True
        async def instant_upload(data, key, ct=None):
            return f"https://cdn-dev.newai.land/{key}"
        async def instant_upload_video(data, filename=None, generation_id=None, content_type="video/mp4"):
            return f"https://cdn-dev.newai.land/videos/{generation_id or 'x'}.mp4"
        def mock_cdn_key(url):
            if not url: return None
            from urllib.parse import urlparse
            return urlparse(url).path.lstrip("/")
        s3.download_file = instant_download
        s3.upload_file = instant_upload
        s3.upload_video = instant_upload_video
        s3.cdn_url_to_s3_key = mock_cdn_key

        ff_patches = ffmpeg_function_mocks(test_video)

        monitor = PerfMonitor(sample_interval=0.5)
        monitor.start()
        try:
            monitor.mark("C_start")
            result = await run_segments_node(real_state, s3, extra_patches=ff_patches)
            monitor.mark("C_end")
            logger.info(f"✅ Segments node (no FFmpeg, no I/O) 返回: {list(result.keys()) if isinstance(result, dict) else type(result)}")
        finally:
            monitor.stop()
            monitor.report()
            monitor.dump_csv(os.path.join(work_dir, "phase_c.csv"))


# ════════════════════════════════════════════════════════════════════
# Keyframe node 测试
# ════════════════════════════════════════════════════════════════════

KF_MODULE = "app.services.agent.video.keyframe_generation_service"
VG_MODULE = "app.services.agent.video.video_generation_service"


def _build_user_input_data():
    """构建 UserInput 供 keyframe/video node 使用"""
    from app.models.user_options import UserOption
    from app.models.tool_enums import ContentCategory
    from app.models.video_state import UserInput
    user_option = UserOption()
    user_option.content_category = ContentCategory.DEFAULT
    return UserInput(user_input="测试输入", user_option=user_option)


class TestKeyframeNode:
    """Keyframe node: mock 批量生成，测量 DB 读取 + 调度开销"""

    @pytest.mark.asyncio
    async def test_keyframe_node_overhead(self, db_pool, real_state, work_dir, test_video):
        """直接调用 keyframe_generation_node，mock generate_batch_keyframes"""
        from app.services.agent.video.keyframe_generation_service import keyframe_generation_node

        state = {**real_state, "user_input_data": _build_user_input_data()}
        runtime = make_mock_runtime()
        send_event = make_noop_send_event()

        async def mock_generate_batch(*args, **kwargs):
            await asyncio.sleep(0.01)
            return ([], [])

        monitor = PerfMonitor(sample_interval=0.5)
        monitor.start()
        try:
            monitor.mark("kf_start")
            with patch(f"{KF_MODULE}.generate_batch_keyframes", side_effect=mock_generate_batch), \
                 patch(f"{KF_MODULE}.save_keyframe_to_db", new_callable=AsyncMock, return_value="mock-kf-uuid"):
                result = await keyframe_generation_node(state, runtime, send_event)
            monitor.mark("kf_end")
            logger.info(f"✅ Keyframe node 返回: {list(result.keys()) if isinstance(result, dict) else type(result)}")
        except Exception as e:
            monitor.mark("kf_end")
            logger.error(f"❌ Keyframe node error (expected — no real keyframes): {e}")
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_pil_cpu_impact(self, work_dir):
        """独立测试 PIL upload_image 的 CPU 影响（模拟 41 张关键帧上传）"""
        from PIL import Image
        import io

        # 创建 41 张 1024x1024 测试图片
        test_images = []
        for i in range(41):
            img = Image.new("RGB", (1024, 1024), color=(i * 6 % 256, 100, 200))
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=90)
            test_images.append(buf.getvalue())

        upload_dir = os.path.join(work_dir, "pil_uploads")
        os.makedirs(upload_dir, exist_ok=True)
        counter = {"n": 0}

        async def pil_resize_and_save(image_data: bytes, idx: int):
            """模拟 s3_utils.upload_image 的 PIL 处理路径"""
            def _sync():
                img = Image.open(io.BytesIO(image_data))
                if img.size != (1920, 1080):
                    img = img.resize((1920, 1080), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="WEBP", quality=90)
                out_path = os.path.join(upload_dir, f"keyframe_{idx}.webp")
                with open(out_path, "wb") as f:
                    f.write(buf.getvalue())
            await asyncio.to_thread(_sync)
            counter["n"] += 1

        # Phase A: 真实 PIL 处理
        monitor_a = PerfMonitor(sample_interval=0.3)
        monitor_a.start()
        try:
            monitor_a.mark("pil_start")
            sem = asyncio.Semaphore(5)
            async def bounded(data, idx):
                async with sem:
                    await pil_resize_and_save(data, idx)
            await asyncio.gather(*[bounded(d, i) for i, d in enumerate(test_images)])
            monitor_a.mark("pil_end")
        finally:
            monitor_a.stop()
            monitor_a.report()

        # Phase B: 跳过 PIL（直接写文件）
        counter["n"] = 0
        monitor_b = PerfMonitor(sample_interval=0.3)
        monitor_b.start()
        try:
            monitor_b.mark("nopil_start")
            async def write_only(data, idx):
                async def _w():
                    out = os.path.join(upload_dir, f"nopil_{idx}.webp")
                    with open(out, "wb") as f:
                        f.write(data)
                async with sem:
                    await asyncio.to_thread(_w)
            await asyncio.gather(*[write_only(d, i) for i, d in enumerate(test_images)])
            monitor_b.mark("nopil_end")
        finally:
            monitor_b.stop()
            monitor_b.report()

        sa = monitor_a.get_stage_summaries()
        sb = monitor_b.get_stage_summaries()
        if sa and sb:
            a, b = sa[0], sb[0]
            logger.info(f"\n📊 PIL CPU 对比 (41 张 1024→1920 resize + WEBP encode):")
            logger.info(f"   真实 PIL:  {a.duration_sec:.1f}s, avg CPU {a.avg_cpu:.1f}%, max CPU {a.max_cpu:.1f}%")
            logger.info(f"   跳过 PIL:  {b.duration_sec:.1f}s, avg CPU {b.avg_cpu:.1f}%, max CPU {b.max_cpu:.1f}%")
            logger.info(f"   🔥 PIL 贡献: CPU +{a.avg_cpu - b.avg_cpu:.1f}%, 时间 +{a.duration_sec - b.duration_sec:.1f}s")


# ════════════════════════════════════════════════════════════════════
# Video generation node 测试
# ════════════════════════════════════════════════════════════════════


class TestVideoGenNode:
    """Video generation node: mock 批量生成，测量 DB 读取 + 调度开销 + FFmpeg 后处理"""

    @pytest.mark.asyncio
    async def test_video_gen_node_overhead(self, db_pool, real_state, work_dir, test_video):
        """直接调用 video_generation_node，mock generate_batch_videos"""
        from app.services.agent.video.video_generation_service import video_generation_node

        state = {**real_state, "user_input_data": _build_user_input_data()}
        runtime = make_mock_runtime()
        send_event = make_noop_send_event()

        async def mock_generate_batch(*args, **kwargs):
            await asyncio.sleep(0.01)
            return ([], [])

        monitor = PerfMonitor(sample_interval=0.5)
        monitor.start()
        try:
            monitor.mark("vg_start")
            with patch(f"{VG_MODULE}.generate_batch_videos", side_effect=mock_generate_batch), \
                 patch(f"{VG_MODULE}.save_video_generation_to_db", new_callable=AsyncMock, return_value="mock-vg-uuid"):
                result = await video_generation_node(state, runtime, send_event)
            monitor.mark("vg_end")
            logger.info(f"✅ Video gen node 返回: {list(result.keys()) if isinstance(result, dict) else type(result)}")
        except Exception as e:
            monitor.mark("vg_end")
            logger.error(f"❌ Video gen node error (expected — no real videos): {e}")
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_ffmpeg_normalize_watermark_cpu(self, work_dir, test_video):
        """独立测试 FFmpeg normalize + watermark 的 CPU 影响（模拟 41 个视频后处理）"""
        from app.utils.video_utils import (
            normalize_single_video_to_match_async,
            add_watermark_to_video_async,
        )

        temp_dir = os.path.join(work_dir, "ffmpeg_test")
        os.makedirs(temp_dir, exist_ok=True)

        # Phase A: 真实 FFmpeg normalize × 41
        monitor_a = PerfMonitor(sample_interval=0.5)
        monitor_a.start()
        try:
            monitor_a.mark("norm_start")
            sem = asyncio.Semaphore(5)
            async def normalize_one(idx):
                async with sem:
                    await normalize_single_video_to_match_async(
                        test_video, 1920, 1080, 25.0, "h264", temp_dir, idx
                    )
            await asyncio.gather(*[normalize_one(i) for i in range(41)])
            monitor_a.mark("norm_end")
        finally:
            monitor_a.stop()
            monitor_a.report()

        # Phase B: mock FFmpeg（文件拷贝）
        monitor_b = PerfMonitor(sample_interval=0.3)
        monitor_b.start()
        try:
            monitor_b.mark("nonorm_start")
            async def copy_one(idx):
                out = os.path.join(temp_dir, f"copy_{idx}.mp4")
                async with sem:
                    await asyncio.to_thread(shutil.copy2, test_video, out)
            await asyncio.gather(*[copy_one(i) for i in range(41)])
            monitor_b.mark("nonorm_end")
        finally:
            monitor_b.stop()
            monitor_b.report()

        sa = monitor_a.get_stage_summaries()
        sb = monitor_b.get_stage_summaries()
        if sa and sb:
            a, b = sa[0], sb[0]
            logger.info(f"\n📊 FFmpeg normalize CPU 对比 (41 × 1080p normalize):")
            logger.info(f"   真实 FFmpeg: {a.duration_sec:.1f}s, avg CPU {a.avg_cpu:.1f}%, max CPU {a.max_cpu:.1f}%")
            logger.info(f"   文件拷贝:   {b.duration_sec:.1f}s, avg CPU {b.avg_cpu:.1f}%, max CPU {b.max_cpu:.1f}%")
            logger.info(f"   🔥 FFmpeg normalize 贡献: CPU +{a.avg_cpu - b.avg_cpu:.1f}%, 时间 +{a.duration_sec - b.duration_sec:.1f}s")


# ════════════════════════════════════════════════════════════════════
# 全量对比报告
# ════════════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════════════
# 真实 LLM 调用测试: 只 mock 图片/视频生成 API，保留所有 LLM 调用
# ════════════════════════════════════════════════════════════════════

IMAGE_WRAPPER_MODULE = "app.tools.image.image_tool_wrapper"
VIDEO_WRAPPER_MODULE = "app.tools.video.video_tool_wrapper"


def image_gen_api_mocks():
    """
    Mock _run_i2i_loop / _run_t2i_loop — 跳过 Gemini API + PIL + S3 上传。
    工具 wrapper 仍由 agent 正常调用（LLM 决策 → 调用工具 → 获得 mock 结果）。
    """
    from app.models.image_result import ImageGenerationResult

    async def mock_i2i(prompt, reference_image_urls, runtime, chain):
        await asyncio.sleep(0.3)
        return ImageGenerationResult(
            success=True,
            image_url=f"https://mock-cdn.test/kf_{uuid.uuid4()}.webp",
            provider="google", model="mock",
        )

    async def mock_t2i(prompt, runtime, chain):
        await asyncio.sleep(0.3)
        return ImageGenerationResult(
            success=True,
            image_url=f"https://mock-cdn.test/kf_{uuid.uuid4()}.webp",
            provider="google", model="mock",
        )

    return [
        patch(f"{IMAGE_WRAPPER_MODULE}._run_i2i_loop", side_effect=mock_i2i),
        patch(f"{IMAGE_WRAPPER_MODULE}._run_t2i_loop", side_effect=mock_t2i),
    ]


def video_gen_api_mocks():
    """
    Mock _run_video_loop — 跳过 WaveSpeed/Sora API + FFmpeg normalize/watermark。
    """
    from app.models.image_result import VideoGenerationResult

    async def mock_video(i2v_prompt, start_image_url, duration, runtime, chain):
        await asyncio.sleep(0.3)
        return VideoGenerationResult(
            success=True,
            video_url=f"https://mock-cdn.test/vid_{uuid.uuid4()}.mp4",
            provider="wavespeed", duration=5,
        )

    return [
        patch(f"{VIDEO_WRAPPER_MODULE}._run_video_loop", side_effect=mock_video),
    ]


class TestKeyframeNodeWithLLM:
    """Keyframe node: 真实 LLM 调用 + mock 图片生成 API
    
    REAL: LLM prompt 生成, LLM 评估, LLM agent 工具选择, DB 读取, prompt 模板加载
    MOCK: 图片生成 API (Gemini), PIL, S3, DB 写入
    """

    @pytest.mark.asyncio
    async def test_keyframe_with_real_llm(self, db_pool, real_state, work_dir, test_video):
        from app.services.agent.video.keyframe_generation_service import keyframe_generation_node

        state = {**real_state, "user_input_data": _build_user_input_data()}
        runtime = make_mock_runtime()
        send_event = make_noop_send_event()

        s3 = make_s3_mock(test_video, os.path.join(work_dir, "s3_kf"))
        patches = [
            patch("app.utils.temp_file_utils.s3_utils", s3),
            patch(f"{KF_MODULE}.save_keyframe_to_db", new_callable=AsyncMock, return_value="mock-kf-uuid"),
            *image_gen_api_mocks(),
        ]

        monitor = PerfMonitor(sample_interval=0.5)
        monitor.start()
        started = [p.start() for p in patches]
        try:
            monitor.mark("kf_llm_start")
            result = await keyframe_generation_node(state, runtime, send_event)
            monitor.mark("kf_llm_end")
            kf_uuids = result.get("keyframe_uuids", []) if isinstance(result, dict) else []
            logger.info(f"✅ Keyframe node (real LLM) 完成: {len(kf_uuids)} keyframes")
        except Exception as e:
            monitor.mark("kf_llm_end")
            logger.error(f"❌ Keyframe node (real LLM) error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            for p in patches:
                p.stop()
            monitor.stop()
            monitor.report()


class TestVideoGenNodeWithLLM:
    """Video gen node: 真实 LLM 调用 + mock 视频生成 API
    
    REAL: LLM prompt 生成, LLM 评估, LLM agent 工具选择, DB 读取, prompt 模板加载
    MOCK: 视频生成 API (WaveSpeed/Sora), FFmpeg normalize/watermark, S3, DB 写入
    """

    @pytest.mark.asyncio
    async def test_video_gen_with_real_llm(self, db_pool, real_state, work_dir, test_video):
        from app.services.agent.video.video_generation_service import video_generation_node

        state = {**real_state, "user_input_data": _build_user_input_data()}
        runtime = make_mock_runtime()
        send_event = make_noop_send_event()

        s3 = make_s3_mock(test_video, os.path.join(work_dir, "s3_vg"))
        patches = [
            patch("app.utils.temp_file_utils.s3_utils", s3),
            patch(f"{VG_MODULE}.save_video_generation_to_db", new_callable=AsyncMock, return_value="mock-vg-uuid"),
            *video_gen_api_mocks(),
        ]

        monitor = PerfMonitor(sample_interval=0.5)
        monitor.start()
        started = [p.start() for p in patches]
        try:
            monitor.mark("vg_llm_start")
            result = await video_generation_node(state, runtime, send_event)
            monitor.mark("vg_llm_end")
            vg_uuids = result.get("video_generation_uuids", []) if isinstance(result, dict) else []
            logger.info(f"✅ Video gen node (real LLM) 完成: {len(vg_uuids)} videos")
        except Exception as e:
            monitor.mark("vg_llm_end")
            logger.error(f"❌ Video gen node (real LLM) error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            for p in patches:
                p.stop()
            monitor.stop()
            monitor.report()


class TestCompare:
    """三个 Phase 顺序跑，输出对比报告"""

    @pytest.mark.asyncio
    async def test_full_comparison(self, db_pool, real_state, work_dir, test_video):
        results = {}

        for phase, label, use_ff_mock, use_io_mock in [
            ("A", "BASELINE (真实 FFmpeg + mock S3)", False, False),
            ("B", "mock FFmpeg (无编码, 文件拷贝)", True, False),
            ("C", "mock FFmpeg + mock I/O (零开销)", True, True),
        ]:
            logger.info(f"\n{'='*80}\n📊 PHASE {phase}: {label}\n{'='*80}")

            if use_io_mock:
                s3 = MagicMock()
                async def instant_download(url, path):
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(b"\x00" * 1024)
                    return True
                async def instant_upload(data, key, ct=None):
                    return f"https://cdn-dev.newai.land/{key}"
                async def instant_upload_video(data, filename=None, generation_id=None, content_type="video/mp4"):
                    return f"https://cdn-dev.newai.land/videos/{generation_id or 'x'}.mp4"
                def mock_cdn_key(url):
                    if not url: return None
                    from urllib.parse import urlparse
                    return urlparse(url).path.lstrip("/")
                s3.download_file = instant_download
                s3.upload_file = instant_upload
                s3.upload_video = instant_upload_video
                s3.cdn_url_to_s3_key = mock_cdn_key
            else:
                s3 = make_s3_mock(test_video, os.path.join(work_dir, f"s3_{phase}"))

            extra = ffmpeg_function_mocks(test_video) if use_ff_mock else None

            monitor = PerfMonitor(sample_interval=0.5)
            monitor.start()
            t0 = time.time()
            try:
                monitor.mark(f"{phase}_start")
                await run_segments_node(real_state, s3, extra_patches=extra)
                monitor.mark(f"{phase}_end")
            finally:
                elapsed = time.time() - t0
                monitor.stop()
                summaries = monitor.get_stage_summaries()
                if summaries:
                    results[phase] = summaries[0]
                monitor.report()

        # ── 对比报告 ──
        logger.info(f"\n{'='*100}")
        logger.info("📊 COMPARISON REPORT — video_segments_node 直接调用")
        logger.info(f"{'='*100}")
        header = f"{'Phase':<55} {'Duration':>8} {'AvgCPU':>8} {'MaxCPU':>8} {'MaxMem':>8} {'DiskW':>8} {'FFmpeg':>6}"
        logger.info(header)
        logger.info("-" * 100)

        for phase_name, phase_label in [
            ("A", "A: BASELINE (真实 FFmpeg)"),
            ("B", "B: mock FFmpeg (文件拷贝)"),
            ("C", "C: mock FFmpeg + mock I/O"),
        ]:
            if phase_name in results:
                s = results[phase_name]
                logger.info(
                    f"{phase_label:<55} {s.duration_sec:>7.1f}s {s.avg_cpu:>7.1f}% {s.max_cpu:>7.1f}% "
                    f"{s.max_mem_rss_mb:>7.1f}M {s.disk_write_delta_mb:>7.1f}M {s.max_ffmpeg_procs:>5d}"
                )

        logger.info("-" * 100)

        if "A" in results and "B" in results:
            a, b = results["A"], results["B"]
            logger.info(f"🔥 FFmpeg CPU 贡献:  avg CPU delta = {a.avg_cpu - b.avg_cpu:+.1f}%,  时间 delta = {a.duration_sec - b.duration_sec:+.1f}s")

        if "B" in results and "C" in results:
            b, c = results["B"], results["C"]
            logger.info(f"💾 文件 I/O 贡献:    avg CPU delta = {b.avg_cpu - c.avg_cpu:+.1f}%,  时间 delta = {b.duration_sec - c.duration_sec:+.1f}s")

        if "C" in results:
            c = results["C"]
            logger.info(f"⚡ 纯调度开销:       avg CPU = {c.avg_cpu:.1f}%,  时间 = {c.duration_sec:.1f}s")

        logger.info("=" * 100)


# ════════════════════════════════════════════════════════════════════
# 并发任务测试: 模拟 N 个任务同时跑 segment node（真实 FFmpeg）
# ════════════════════════════════════════════════════════════════════


class TestConcurrentTasks:
    """并发任务压测 — 模拟多个视频任务同时运行 segment node

    运行:
        # 2 个并发任务
        python -m pytest tests/performance/test_real_pipeline.py::TestConcurrentTasks::test_concurrent_2 -v -s --tb=long
        # 5 个并发任务
        python -m pytest tests/performance/test_real_pipeline.py::TestConcurrentTasks::test_concurrent_5 -v -s --tb=long
    """

    async def _run_n_segment_tasks(self, n: int, db_pool, real_state, work_dir, test_video):
        """并发跑 N 个 segment node"""
        from app.services.agent.video.video_segments_service import video_segments_node

        monitor = PerfMonitor(sample_interval=0.3)
        monitor.start()

        async def one_task(task_id: int):
            task_dir = os.path.join(work_dir, f"task_{task_id}")
            os.makedirs(task_dir, exist_ok=True)
            s3 = make_s3_mock(test_video, os.path.join(task_dir, "s3"))
            runtime = make_mock_runtime()
            send_event = make_noop_send_event()
            patches = [
                patch("app.utils.temp_file_utils.s3_utils", s3),
                patch(
                    "app.services.agent.video.video_segments_service._save_video_segments_to_db",
                    new_callable=AsyncMock,
                    return_value=[f"seg-{task_id}-{i}" for i in range(50)],
                ),
            ]
            started = [p.start() for p in patches]
            try:
                t0 = time.time()
                result = await video_segments_node(real_state, runtime, send_event)
                elapsed = time.time() - t0
                logger.info(f"  Task {task_id} done in {elapsed:.1f}s")
                return elapsed
            except Exception as e:
                logger.error(f"  Task {task_id} error: {e}")
                return -1
            finally:
                for p in patches:
                    p.stop()

        try:
            monitor.mark(f"concurrent_{n}_start")
            tasks = [one_task(i) for i in range(n)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            monitor.mark(f"concurrent_{n}_end")

            elapsed_list = [r for r in results if isinstance(r, (int, float)) and r > 0]
            logger.info(f"\n{'='*100}")
            logger.info(f"📊 并发 {n} 任务 segment node 结果:")
            logger.info(f"   完成: {len(elapsed_list)}/{n}")
            if elapsed_list:
                logger.info(f"   最快: {min(elapsed_list):.1f}s, 最慢: {max(elapsed_list):.1f}s, 平均: {sum(elapsed_list)/len(elapsed_list):.1f}s")
            logger.info(f"{'='*100}")
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_concurrent_2(self, db_pool, real_state, work_dir, test_video):
        """2 个任务并发跑 segment node"""
        await self._run_n_segment_tasks(2, db_pool, real_state, work_dir, test_video)

    @pytest.mark.asyncio
    async def test_concurrent_5(self, db_pool, real_state, work_dir, test_video):
        """5 个任务并发跑 segment node"""
        await self._run_n_segment_tasks(5, db_pool, real_state, work_dir, test_video)

    @pytest.mark.asyncio
    async def test_concurrent_10(self, db_pool, real_state, work_dir, test_video):
        """10 个任务并发跑 segment node"""
        await self._run_n_segment_tasks(10, db_pool, real_state, work_dir, test_video)


# ════════════════════════════════════════════════════════════════════
# 并发 LLM 测试: N 个 keyframe/video gen 任务同时跑 (真实 LLM, mock 生成 API)
# ════════════════════════════════════════════════════════════════════


class TestConcurrentKeyframeLLM:
    """并发 keyframe node (真实 LLM) 压测

    运行:
        python -m pytest tests/performance/test_real_pipeline.py::TestConcurrentKeyframeLLM -v -s --tb=long
    """

    async def _run_n_keyframe_tasks(self, n: int, db_pool, real_state, work_dir, test_video):
        from app.services.agent.video.keyframe_generation_service import keyframe_generation_node

        monitor = PerfMonitor(sample_interval=0.3)
        monitor.start()
        errors = []

        async def one_task(task_id: int):
            task_dir = os.path.join(work_dir, f"kf_task_{task_id}")
            os.makedirs(task_dir, exist_ok=True)
            state = {**real_state, "user_input_data": _build_user_input_data()}
            s3 = make_s3_mock(test_video, os.path.join(task_dir, "s3"))
            runtime = make_mock_runtime()
            send_event = make_noop_send_event()
            patches = [
                patch("app.utils.temp_file_utils.s3_utils", s3),
                patch(f"{KF_MODULE}.save_keyframe_to_db", new_callable=AsyncMock, return_value=f"kf-{task_id}"),
                *image_gen_api_mocks(),
            ]
            started = [p.start() for p in patches]
            try:
                t0 = time.time()
                result = await keyframe_generation_node(state, runtime, send_event)
                elapsed = time.time() - t0
                kf_count = len(result.get("keyframe_uuids", [])) if isinstance(result, dict) else 0
                logger.info(f"  KF Task {task_id} done in {elapsed:.1f}s ({kf_count} keyframes)")
                return elapsed
            except Exception as e:
                logger.error(f"  KF Task {task_id} error: {e}")
                errors.append(str(e))
                return -1
            finally:
                for p in patches:
                    p.stop()

        try:
            monitor.mark(f"kf_concurrent_{n}_start")
            tasks = [one_task(i) for i in range(n)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            monitor.mark(f"kf_concurrent_{n}_end")

            elapsed_list = [r for r in results if isinstance(r, (int, float)) and r > 0]
            logger.info(f"\n{'='*100}")
            logger.info(f"📊 并发 {n} 任务 keyframe node (真实 LLM) 结果:")
            logger.info(f"   完成: {len(elapsed_list)}/{n}, 失败: {n - len(elapsed_list)}")
            if elapsed_list:
                logger.info(f"   最快: {min(elapsed_list):.1f}s, 最慢: {max(elapsed_list):.1f}s, 平均: {sum(elapsed_list)/len(elapsed_list):.1f}s")
            if errors:
                logger.warning(f"   错误: {errors[:3]}")
            logger.info(f"{'='*100}")
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_kf_concurrent_2(self, db_pool, real_state, work_dir, test_video):
        await self._run_n_keyframe_tasks(2, db_pool, real_state, work_dir, test_video)

    @pytest.mark.asyncio
    async def test_kf_concurrent_3(self, db_pool, real_state, work_dir, test_video):
        await self._run_n_keyframe_tasks(3, db_pool, real_state, work_dir, test_video)

    @pytest.mark.asyncio
    async def test_kf_concurrent_5(self, db_pool, real_state, work_dir, test_video):
        await self._run_n_keyframe_tasks(5, db_pool, real_state, work_dir, test_video)


class TestConcurrentVideoGenLLM:
    """并发 video gen node (真实 LLM) 压测

    运行:
        python -m pytest tests/performance/test_real_pipeline.py::TestConcurrentVideoGenLLM -v -s --tb=long
    """

    async def _run_n_videogen_tasks(self, n: int, db_pool, real_state, work_dir, test_video):
        from app.services.agent.video.video_generation_service import video_generation_node

        monitor = PerfMonitor(sample_interval=0.3)
        monitor.start()
        errors = []

        async def one_task(task_id: int):
            task_dir = os.path.join(work_dir, f"vg_task_{task_id}")
            os.makedirs(task_dir, exist_ok=True)
            state = {**real_state, "user_input_data": _build_user_input_data()}
            s3 = make_s3_mock(test_video, os.path.join(task_dir, "s3"))
            runtime = make_mock_runtime()
            send_event = make_noop_send_event()
            patches = [
                patch("app.utils.temp_file_utils.s3_utils", s3),
                patch(f"{VG_MODULE}.save_video_generation_to_db", new_callable=AsyncMock, return_value=f"vg-{task_id}"),
                *video_gen_api_mocks(),
            ]
            started = [p.start() for p in patches]
            try:
                t0 = time.time()
                result = await video_generation_node(state, runtime, send_event)
                elapsed = time.time() - t0
                vg_count = len(result.get("video_generation_uuids", [])) if isinstance(result, dict) else 0
                logger.info(f"  VG Task {task_id} done in {elapsed:.1f}s ({vg_count} videos)")
                return elapsed
            except Exception as e:
                logger.error(f"  VG Task {task_id} error: {e}")
                errors.append(str(e))
                return -1
            finally:
                for p in patches:
                    p.stop()

        try:
            monitor.mark(f"vg_concurrent_{n}_start")
            tasks = [one_task(i) for i in range(n)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            monitor.mark(f"vg_concurrent_{n}_end")

            elapsed_list = [r for r in results if isinstance(r, (int, float)) and r > 0]
            logger.info(f"\n{'='*100}")
            logger.info(f"📊 并发 {n} 任务 video gen node (真实 LLM) 结果:")
            logger.info(f"   完成: {len(elapsed_list)}/{n}, 失败: {n - len(elapsed_list)}")
            if elapsed_list:
                logger.info(f"   最快: {min(elapsed_list):.1f}s, 最慢: {max(elapsed_list):.1f}s, 平均: {sum(elapsed_list)/len(elapsed_list):.1f}s")
            if errors:
                logger.warning(f"   错误: {errors[:3]}")
            logger.info(f"{'='*100}")
        finally:
            monitor.stop()
            monitor.report()

    @pytest.mark.asyncio
    async def test_vg_concurrent_2(self, db_pool, real_state, work_dir, test_video):
        await self._run_n_videogen_tasks(2, db_pool, real_state, work_dir, test_video)

    @pytest.mark.asyncio
    async def test_vg_concurrent_3(self, db_pool, real_state, work_dir, test_video):
        await self._run_n_videogen_tasks(3, db_pool, real_state, work_dir, test_video)

    @pytest.mark.asyncio
    async def test_vg_concurrent_5(self, db_pool, real_state, work_dir, test_video):
        await self._run_n_videogen_tasks(5, db_pool, real_state, work_dir, test_video)

    @pytest.mark.asyncio
    async def test_vg_concurrent_20(self, db_pool, real_state, work_dir, test_video):
        await self._run_n_videogen_tasks(20, db_pool, real_state, work_dir, test_video)

    @pytest.mark.asyncio
    async def test_vg_concurrent_50(self, db_pool, real_state, work_dir, test_video):
        await self._run_n_videogen_tasks(50, db_pool, real_state, work_dir, test_video)

    @pytest.mark.asyncio
    async def test_vg_concurrent_100(self, db_pool, real_state, work_dir, test_video):
        await self._run_n_videogen_tasks(100, db_pool, real_state, work_dir, test_video)
