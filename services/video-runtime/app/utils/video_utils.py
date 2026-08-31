"""
视频处理工具函数
包含视频创建、音轨处理、视频合成、音效整合等功能

功能模块：
1. 视频创建: 生成黑屏占位视频
2. 音轨处理: 为视频添加音频轨道
3. 视频合成: 拼接视频片段，整合旁白和音效
4. 速度调整: 根据旁白时长调整视频速度
5. 音乐混合: 添加背景音乐，调整音量

使用场景：
- Video Agent 合成最终视频
- Lipsync Service 调整音频
- Video Assembly 整合所有元素
"""
import asyncio
import os
import logging
import tempfile
import subprocess
import json
import shutil
import uuid
from typing import Optional, List, Dict, Tuple, TYPE_CHECKING

# 与图片下采样一致：差异 < 3% 时强制到目标尺寸（含轻微放大）
from .image_utils import DOWNSCALE_FORCE_EXACT_THRESHOLD

# 导入合并自 video_merge_utils.py 需要的依赖
from .file_utils import merge_audio_with_video, MediaType
from . import media_service_client as msc
from .s3_utils import s3_utils
from .temp_file_utils import download_media_to_temp

# 类型提示导入（避免循环导入；VideoGenerationVersion 为 video_state 的 DTO）
if TYPE_CHECKING:
    from ..models.video_state import VideoGenerationVersion

logger = logging.getLogger(__name__)

_NARRATION_AV_ALIGN_SKIP_SEC = 0.02
_NARRATION_AV_LIGHT_ALIGN_MAX_SEC = 2.0
_SHOT_ALIGN_SKIP_SEC = 0.02


def _run_ffmpeg_sync(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[:500] if proc.stderr else "ffmpeg failed")


async def _build_shot_timeline_audio_file(
    timeline_parts: List[Tuple[str, str]],
    output_path: str,
    temp_dir: str,
) -> None:
    """按镜头顺序拼接旁白：('audio', local_path) 或 ('silence', duration_seconds)。"""
    seg_paths: List[str] = []
    for i, (kind, val) in enumerate(timeline_parts):
        seg_out = os.path.join(temp_dir, f"tl_{i}_{uuid.uuid4().hex[:6]}.mp3")
        if kind == "audio":
            cmd = ["ffmpeg", "-y", "-i", val, "-c:a", "libmp3lame", "-b:a", "192k", seg_out]
        else:
            cmd = [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                "-t", f"{float(val):.3f}", "-c:a", "libmp3lame", "-b:a", "192k", seg_out,
            ]
        await asyncio.to_thread(_run_ffmpeg_sync, cmd)
        seg_paths.append(seg_out)
    list_path = os.path.join(temp_dir, f"audio_list_{uuid.uuid4().hex[:6]}.txt")
    with open(list_path, "w") as f:
        for p in seg_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path]
    await asyncio.to_thread(_run_ffmpeg_sync, cmd)


async def _make_silence_mp3(duration: float, output_path: str) -> None:
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{float(duration):.3f}", "-c:a", "libmp3lame", "-b:a", "192k", output_path,
    ]
    await asyncio.to_thread(_run_ffmpeg_sync, cmd)


async def _pad_or_trim_audio_to_duration(
    audio_path: str,
    duration: float,
    output_path: str,
) -> None:
    """Pad with silence or trim so audio length ~= duration (wall-clock shot)."""
    cmd = [
        "ffmpeg", "-y", "-i", audio_path,
        "-af", f"apad=whole_dur={float(duration):.3f}",
        "-t", f"{float(duration):.3f}",
        "-c:a", "libmp3lame", "-b:a", "192k",
        output_path,
    ]
    await asyncio.to_thread(_run_ffmpeg_sync, cmd)


async def _mix_overlay_onto_base_audio(
    base_path: str,
    overlay_path: str,
    output_path: str,
    *,
    base_volume: float = 1.0,
    overlay_volume: float = 1.0,
) -> None:
    """amix base + overlay; duration follows base (shot wall-clock)."""
    cmd = [
        "ffmpeg", "-y", "-i", base_path, "-i", overlay_path,
        "-filter_complex",
        (
            f"[0:a]volume={base_volume}[a0];"
            f"[1:a]volume={overlay_volume}[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0.1[out]"
        ),
        "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k",
        output_path,
    ]
    await asyncio.to_thread(_run_ffmpeg_sync, cmd)


async def _shot_audio_preserving_video(
    video_url: str,
    seg_dur: float,
    narration_audio_url: Optional[str],
    temp_dir: str,
    shot_number: int,
    run_id: str,
) -> str:
    """Keep Seedance in-clip audio; optionally overlay TTS narration on that shot."""
    base_path = os.path.join(temp_dir, f"va_base_{shot_number}_{uuid.uuid4().hex[:6]}.mp3")
    extracted = await msc.audio_extract(video_url, run_id=f"{run_id}_xa{shot_number}", fmt="mp3")
    extracted_url = (extracted or {}).get("result_url")
    if extracted_url:
        local_ext = await download_media_to_temp(
            extracted_url, temp_dir, MediaType.AUDIO, f"va_ext_{shot_number}",
        )
        if not local_ext:
            raise RuntimeError(f"无法下载片内音轨 shot={shot_number}")
        await _pad_or_trim_audio_to_duration(local_ext, seg_dur, base_path)
    else:
        await _make_silence_mp3(seg_dur, base_path)

    if not (narration_audio_url or "").strip():
        return base_path

    local_nar = await download_media_to_temp(
        narration_audio_url, temp_dir, MediaType.AUDIO, f"nar_{shot_number}",
    )
    if not local_nar:
        raise RuntimeError(f"无法下载旁白音频 shot={shot_number}")
    mixed = os.path.join(temp_dir, f"va_mix_{shot_number}_{uuid.uuid4().hex[:6]}.mp3")
    await _mix_overlay_onto_base_audio(base_path, local_nar, mixed)
    return mixed


async def align_video_to_reference_audio(
    video_url: str,
    reference_audio_url: Optional[str] = None,
    *,
    target_duration: Optional[float] = None,
    allow_speed_adjust: bool = False,
    run_id: Optional[str] = None,
) -> str:
    """将视频对齐到 reference audio 时长（probe）或显式 target_duration。

    lipsync / narration 场景默认 allow_speed_adjust=False，仅 trim / freeze，与 audio-driven segment 一致。
    """
    if reference_audio_url:
        target = await get_audio_duration_from_url(reference_audio_url)
    elif target_duration is not None and target_duration > 0:
        target = float(target_duration)
    else:
        return video_url
    if target is None or target <= 0:
        return video_url

    video_info = await msc.video_info(video_url)
    current = float(video_info.get("duration") or 0)
    if current <= 0:
        return video_url
    diff = current - target
    if abs(diff) <= _SHOT_ALIGN_SKIP_SEC:
        return video_url

    rid = run_id or uuid.uuid4().hex[:12]
    if allow_speed_adjust and abs(diff) > _NARRATION_AV_LIGHT_ALIGN_MAX_SEC:
        result = await msc.video_speed_adjust(video_url, target, run_id=rid)
        logger.info(
            "✅ align_video speed_adjust: %.2fs → %.2fs (Δ=%+.2fs)",
            current, target, diff,
        )
        return result["result_url"]

    mode = "trim_only" if diff > 0 else "freeze_or_tail_slow"
    result = await msc.video_trim(
        video_url, target, run_id=rid, mode=mode, tolerance=0.0,
    )
    logger.info(
        "✅ align_video %s: %.2fs → %.2fs (Δ=%+.2fs)",
        mode, current, target, diff,
    )
    return result["result_url"]


async def concatenate_videos_silent_with_narration_at_end(
    video_urls: List[str],
    video_generations: List["VideoGenerationVersion"],
    narrations_data: Dict[int, Dict],
    title: str,
    *,
    preserve_video_audio: bool = False,
) -> Tuple[str, List[Tuple[int, float, float]]]:
    """画面硬切 + 按镜时间窗叠旁白。

    Product Launch 默认 preserve_video_audio=False（video_volume=0，整轨旁白替换）。
    短剧/片内对白：preserve_video_audio=True，保留片内对白声，旁白叠加。

    注意：MSC video_concat 在有音轨时会 normalize 并丢弃音频（a=0），因此
    preserve 路径会先抽出各镜片内声，与 TTS 旁白混成整轨，再挂回静音 concat 画面。

    Returns:
        (final_video_url, shot_timeline) 其中 shot_timeline 为 [(shot_number, start_time, duration), ...]
    """
    run_id = uuid.uuid4().hex[:12]
    temp_dir = tempfile.mkdtemp()
    processed_videos: List[str] = []
    timeline_parts: List[Tuple[str, str]] = []
    shot_timeline: List[Tuple[int, float, float]] = []
    timeline_cursor = 0.0
    try:
        for video_url, video_gen in zip(video_urls, video_generations):
            shot_number = video_gen.shot_number
            narration_data = narrations_data.get(shot_number)
            version = narration_data.get("version") if narration_data else None
            has_nar = bool(version and version.success and (version.audio_url or "").strip())

            if preserve_video_audio:
                seg_info = await msc.video_info(video_url)
                seg_dur = float(seg_info.get("duration") or 0)
                processed_videos.append(video_url)
                shot_timeline.append((shot_number, timeline_cursor, seg_dur))
                timeline_cursor += seg_dur
                shot_audio = await _shot_audio_preserving_video(
                    video_url,
                    seg_dur,
                    version.audio_url if has_nar else None,
                    temp_dir,
                    shot_number,
                    run_id,
                )
                timeline_parts.append(("audio", shot_audio))
                logger.info(
                    f"🎙️ 镜头 {shot_number}: 保留片内声"
                    f"{'+旁白叠' if has_nar else ''} {seg_dur:.2f}s "
                    f"(start={shot_timeline[-1][1]:.2f}s)"
                )
            elif has_nar:
                adjusted_video_url = await align_video_to_reference_audio(
                    video_url,
                    version.audio_url,
                    allow_speed_adjust=False,
                    run_id=f"{run_id}_shot{shot_number}",
                )
                seg_info = await msc.video_info(adjusted_video_url)
                seg_dur = float(seg_info.get("duration") or 0)
                processed_videos.append(adjusted_video_url)
                shot_timeline.append((shot_number, timeline_cursor, seg_dur))
                timeline_cursor += seg_dur
                local_audio = await download_media_to_temp(
                    version.audio_url, temp_dir, MediaType.AUDIO, f"nar_{shot_number}",
                )
                if not local_audio:
                    raise RuntimeError(f"无法下载旁白音频 shot={shot_number}")
                timeline_parts.append(("audio", local_audio))
                logger.info(
                    f"🎙️ 镜头 {shot_number}: 旁白窗 {seg_dur:.2f}s "
                    f"(start={shot_timeline[-1][1]:.2f}s) preserve_video_audio=False"
                )
            else:
                seg_info = await msc.video_info(video_url)
                seg_dur = float(seg_info.get("duration") or 0)
                processed_videos.append(video_url)
                shot_timeline.append((shot_number, timeline_cursor, seg_dur))
                timeline_cursor += seg_dur
                timeline_parts.append(("silence", str(seg_dur)))
                logger.info(f"🎬 镜头 {shot_number}: 无旁白，保留视频 {seg_dur:.2f}s")

        concat_result = await msc.video_concat(processed_videos, run_id=run_id, normalize=False)
        merged_video_url = concat_result["result_url"]
        logger.info(f"✅ 无声/画面 concat 完成: {merged_video_url}")

        local_full_audio = os.path.join(temp_dir, f"narration_full_{run_id}.mp3")
        await _build_shot_timeline_audio_file(timeline_parts, local_full_audio, temp_dir)
        with open(local_full_audio, "rb") as f:
            audio_bytes = f.read()
        full_narration_url = await s3_utils.upload_audio(
            audio_bytes, generation_id=f"narration_full_{run_id}", content_type="audio/mpeg",
        )
        audio_info = await msc.audio_info(full_narration_url)
        audio_duration = float(audio_info.get("duration") or 0)
        logger.info(f"🎙️ 整轨旁白时间线 {audio_duration:.2f}s: {full_narration_url}")

        if preserve_video_audio:
            # 画面轨已无声（MSC concat 丢弃音轨）；整轨已含片内声+旁白，直接挂轨
            mix_result = await msc.video_mix_audio(
                video_url=merged_video_url,
                audio_url=full_narration_url,
                run_id=run_id,
                video_volume=1.0,
                audio_volume=1.0,
            )
        else:
            aligned_video_url = await align_video_to_reference_audio(
                merged_video_url,
                target_duration=audio_duration,
                allow_speed_adjust=False,
                run_id=run_id,
            )
            mix_result = await msc.video_mix_audio(
                video_url=aligned_video_url,
                audio_url=full_narration_url,
                run_id=run_id,
                video_volume=0.0,
                audio_volume=1.0,
            )
        final_url = mix_result["result_url"]
        logger.info(f"✅ 旁白混音完成 (preserve_video_audio={preserve_video_audio}): {final_url}")
        return final_url, shot_timeline
    finally:
        try:
            if os.path.exists(temp_dir):
                await asyncio.to_thread(shutil.rmtree, temp_dir)
        except Exception:
            pass


async def get_video_stream_info_async(video_path: str) -> Optional[Dict]:
    """获取视频流参数：宽、高、帧率、编码等。用于水印缩放等。"""
    result = await msc.video_info(video_path)
    fps_val = result.get("fps")
    return {
        "width": result.get("width"),
        "height": result.get("height"),
        "r_frame_rate": str(fps_val) if fps_val is not None else "",
        "codec_name": result.get("codec"),
        "pix_fmt": result.get("pix_fmt"),
    }


def parse_frame_rate(r_frame_rate_str: str) -> float:
    """解析 ffprobe 返回的 r_frame_rate（如 '25/1' 或 '30000/1001'）为 fps。"""
    from fractions import Fraction
    s = (r_frame_rate_str or "").strip()
    if not s:
        return 0.0
    try:
        return float(Fraction(s))
    except (ValueError, ZeroDivisionError):
        return 0.0


def _normalize_pix_fmt_for_compat(pix_fmt: Optional[str]) -> str:
    """归一化 pixel format：yuv420p 与 yuvj420p 等视为同族。"""
    if not pix_fmt:
        return ""
    p = (pix_fmt or "").strip().lower()
    if p in ("yuv420p", "yuvj420p"):
        return "yuv420p"
    return p


def _stream_info_matches_reference(info: Optional[Dict], ref: Dict) -> bool:
    """单条流是否与参考流兼容（resolution/fps/codec/pix_fmt）。"""
    if not info:
        return False
    ref_fps = parse_frame_rate(ref.get("r_frame_rate") or "") if ref.get("r_frame_rate") else 0.0
    ref_pix = _normalize_pix_fmt_for_compat(ref.get("pix_fmt"))
    if info.get("width") != ref.get("width") or info.get("height") != ref.get("height"):
        return False
    if info.get("codec_name") != ref.get("codec_name"):
        return False
    other_fps = parse_frame_rate(info.get("r_frame_rate") or "") if info.get("r_frame_rate") else 0.0
    if ref_fps <= 0 or other_fps <= 0:
        if (info.get("r_frame_rate") or "") != (ref.get("r_frame_rate") or ""):
            return False
    elif abs(other_fps - ref_fps) > 0.05:
        return False
    if _normalize_pix_fmt_for_compat(info.get("pix_fmt")) != ref_pix:
        return False
    return True


def video_streams_compatible(infos: List[Optional[Dict]]) -> bool:
    """判断一组视频流参数是否兼容（可安全用 -c copy 拼接）。"""
    valid = [i for i in infos if i]
    if len(valid) < 2:
        return True
    ref = valid[0]
    for other in valid[1:]:
        if not _stream_info_matches_reference(other, ref):
            return False
    return True


async def check_video_has_audio_async(video_path: str) -> bool:
    """检查视频是否有音频轨道。"""
    result = await msc.video_info(video_path)
    return bool(result.get("has_audio"))


async def normalize_single_video_to_match_async(
    video_path: str,
    target_w: int,
    target_h: int,
    target_fps: float,
    target_codec: str,
    temp_dir: str,
    index: int,
) -> str:
    """将单个视频归一化到目标分辨率/帧率/pix_fmt/编码，便于后续 -c copy 拼接。"""
    out_path = os.path.join(temp_dir, f"normalized_{index}_{uuid.uuid4().hex[:8]}.mp4")
    codec_name = (target_codec or "").strip().lower()
    enc = "libx265" if codec_name in ("hevc", "h265") else "libx264"
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={target_fps:.6f},format=yuv420p"
        ),
        "-c:v", enc, "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
        out_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"归一化片段失败: {(stderr or b'').decode()[:300]}")
    return out_path


async def concat_local_videos_with_normalize(
    local_paths: List[str],
    temp_dir: str,
    output_path: str,
) -> None:
    """
    合并多个本地视频：先 probe 流信息，不兼容则按参考流（最低 fps）归一化，再 -c copy 拼接。
    与 assembly 的「流兼容 + 归一化」逻辑一致，供 segment 合并与 assembly 共用。
    """
    if not local_paths:
        raise ValueError("local_paths 为空")
    if len(local_paths) == 1:
        await asyncio.to_thread(shutil.copy2, local_paths[0], output_path)
        logger.info("concat_local_videos_with_normalize: 单文件复制")
        return
    infos: List[Optional[Dict]] = []
    for p in local_paths:
        info = await get_video_stream_info_async(p)
        infos.append(info)
        if info:
            logger.info(
                "   📐 %s: %sx%s, fps=%s, codec=%s",
                os.path.basename(p),
                info.get("width"), info.get("height"),
                info.get("r_frame_rate"), info.get("codec_name"),
            )
    if video_streams_compatible(infos):
        await _concat_copy_mode_impl(local_paths, output_path, temp_dir)
        return
    valid_infos = [(i, inf) for i, inf in enumerate(infos) if inf]
    if not valid_infos:
        raise RuntimeError("无法获取任一视频流信息，无法合并")
    def _fps_of(item):
        i, inf = item
        r = (inf.get("r_frame_rate") or "")
        f = parse_frame_rate(r)
        return f if f > 0 else 999.0
    _, target_info = min(valid_infos, key=_fps_of)
    target_w = int(target_info.get("width", 1280))
    target_h = int(target_info.get("height", 720))
    target_fps_str = target_info.get("r_frame_rate", "25/1")
    target_fps = parse_frame_rate(target_fps_str) if target_fps_str else 25.0
    if target_fps <= 0:
        target_fps = 25.0
    target_codec = (target_info.get("codec_name") or "h264").strip()
    logger.info("   📐 参考流（高→低）: %sx%s, fps=%s, codec=%s", target_w, target_h, target_fps_str, target_codec)
    to_normalize = [i for i, (path, info) in enumerate(zip(local_paths, infos)) if not info or not _stream_info_matches_reference(info, target_info)]
    paths_ordered = list(local_paths)
    if to_normalize:
        logger.info("   🔧 仅对 %s 个不兼容片段做归一化，其余 -c copy 拼接", len(to_normalize))
        done = await asyncio.gather(*[
            normalize_single_video_to_match_async(local_paths[i], target_w, target_h, target_fps, target_codec, temp_dir, i)
            for i in to_normalize
        ])
        for i, path in zip(to_normalize, done):
            paths_ordered[i] = path
    await _concat_copy_mode_impl(paths_ordered, output_path, temp_dir)


async def _concat_copy_mode_impl(
    valid_video_paths: List[str],
    output_path: str,
    temp_dir: str,
) -> None:
    """格式一致时用 concat demuxer + -c copy 拼接（含无音频时静音轨道补齐）。"""
    audio_checks = []
    for path in valid_video_paths:
        audio_checks.append(await check_video_has_audio_async(path))
    all_have_audio = all(audio_checks)
    any_have_audio = any(audio_checks)
    paths_for_concat = list(valid_video_paths)
    if any_have_audio and not all_have_audio:
        logger.info("🎵 部分视频有音频，使用静音轨道补齐模式")
        normalized = []
        for i, video_path in enumerate(valid_video_paths):
            if audio_checks[i]:
                normalized.append(video_path)
            else:
                silent_output = os.path.join(temp_dir, f"silent_{i}_{uuid.uuid4().hex[:8]}.mp4")
                silent_cmd = [
                    "ffmpeg", "-y", "-i", video_path,
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", silent_output,
                ]
                proc = await asyncio.create_subprocess_exec(*silent_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await asyncio.wait_for(proc.communicate(), timeout=60)
                normalized.append(silent_output if proc.returncode == 0 and os.path.exists(silent_output) else video_path)
        paths_for_concat = normalized
    filelist_path = os.path.join(temp_dir, f"filelist_{uuid.uuid4().hex[:8]}.txt")
    lines = []
    for p in paths_for_concat:
        abs_path = os.path.abspath(p)
        escaped = abs_path.replace("'", "'\"'\"'")
        lines.append(f"file '{escaped}'\n")

    def _write_filelist():
        with open(filelist_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    def _cleanup_filelist():
        if os.path.exists(filelist_path):
            try:
                os.remove(filelist_path)
            except OSError:
                pass

    await asyncio.to_thread(_write_filelist)
    try:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", filelist_path, "-c", "copy", output_path]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg合并失败: {(stderr or b'').decode()[:500]}")
        logger.info("   ✅ 视频合并成功: %s", output_path)
    finally:
        await asyncio.to_thread(_cleanup_filelist)


async def add_watermark_to_video_async(input_path: str, output_path: str) -> bool:
    """在视频右下角叠加 logo 水印。失败返回 False，调用方用原视频。
    logo 按短边约 8% 缩放，距右下角 10px。依赖配置 VIDEO_WATERMARK_IMAGE_PATH。"""
    from pathlib import Path
    try:
        from ..config import settings
    except Exception:
        from app.config import settings
    watermark_path = (getattr(settings, "VIDEO_WATERMARK_IMAGE_PATH", None) or "").strip()
    if not watermark_path:
        return False
    if not os.path.isabs(watermark_path):
        # 相对路径按项目根（Cuti-VideoAgent）解析，与 config 说明一致
        project_root = Path(__file__).resolve().parent.parent.parent
        watermark_path = str(project_root / watermark_path)
    if not os.path.isfile(watermark_path):
        logger.warning("水印图片不存在，跳过: %s", watermark_path)
        return False
    try:
        stream_info = await get_video_stream_info_async(input_path)
        if stream_info and stream_info.get("width") and stream_info.get("height"):
            w = int(stream_info["width"])
            h = int(stream_info["height"])
            logo_size = max(32, int(min(w, h) * 0.08))
            scale_filter = f"[1]scale=-1:{logo_size}[logo]"
        else:
            scale_filter = "[1]scale=-1:120[logo]"
        filter_complex = f"{scale_filter};[0][logo]overlay=W-w-10:H-h-10"
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-i", watermark_path,
            "-filter_complex", filter_complex,
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        if process.returncode != 0:
            err = (stderr.decode(errors="replace") if stderr else "").strip()
            logger.warning("视频加水印失败，使用原视频。stderr 尾部: %s", err[-500:] if len(err) > 500 else err)
            return False
        logger.info("✅ 视频水印已叠加")
        return True
    except Exception as e:
        logger.warning("视频加水印异常，使用原视频: %s", e)
        return False


def normalize_video_to_target_sync(
    input_path: str,
    target_width: int,
    target_height: int,
) -> Tuple[str, dict]:
    """视频成片对齐到标准分辨率（与图片 downsample_to_target 策略一致）。

    只做等比例缩放；与目标差异 < 3% 时强制到 (target_width, target_height)（含轻微放大）。
    成片任一边 < 目标且差异 >= 3% 时保持原尺寸不放大。

    Returns:
        (output_path, meta): 若无需重编码则 output_path 为 input_path；否则为新建的临时文件路径，调用方上传后需删除。
    """
    probe_cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "json", input_path,
    ]
    try:
        result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15, check=True)
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            logger.warning("normalize_video_to_target_sync: 无视频流，跳过")
            return input_path, {"orig_w": None, "orig_h": None, "out_w": None, "out_h": None}
        w = int(streams[0].get("width", 0))
        h = int(streams[0].get("height", 0))
        if w <= 0 or h <= 0:
            return input_path, {"orig_w": w, "orig_h": h, "out_w": w, "out_h": h}
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as e:
        logger.warning("normalize_video_to_target_sync: ffprobe 失败 %s，跳过", e)
        return input_path, {}

    tw, th = target_width, target_height
    if w >= tw and h >= th:
        scale = min(tw / w, th / h)
        new_w = max(1, round(w * scale))
        new_h = max(1, round(h * scale))
        diff_w = abs(new_w - tw) / tw if tw else 0
        diff_h = abs(new_h - th) / th if th else 0
        rel_diff = max(diff_w, diff_h)
        if rel_diff < DOWNSCALE_FORCE_EXACT_THRESHOLD:
            out_w, out_h = tw, th
            force_exact = True
        else:
            out_w, out_h = new_w, new_h
            force_exact = False
    else:
        # 成片小于目标：仅当与目标差 < 3% 时放大到标准，否则保持原尺寸
        diff_w = abs(w - tw) / tw if tw else 0
        diff_h = abs(h - th) / th if th else 0
        rel_diff = max(diff_w, diff_h)
        if rel_diff < DOWNSCALE_FORCE_EXACT_THRESHOLD:
            out_w, out_h = tw, th
            force_exact = True
        else:
            out_w, out_h = w, h
            force_exact = False

    if (out_w, out_h) == (w, h):
        logger.info(
            "视频归一化: 原图 %dx%d 已为目标 %dx%d，跳过",
            w, h, tw, th,
        )
        return input_path, {"orig_w": w, "orig_h": h, "out_w": w, "out_h": h, "force_exact": False}

    # libx264 要求宽高均为偶数，等比缩放可能得到奇数（如 720x405）
    out_w = max(2, out_w & ~1)
    out_h = max(2, out_h & ~1)

    logger.info(
        "视频归一化: 原图 %dx%d → 目标 %dx%d → 输出 %dx%d (force_exact=%s)",
        w, h, tw, th, out_w, out_h, force_exact,
    )
    fd, output_path = tempfile.mkstemp(suffix=".mp4", prefix="video_norm_")
    os.close(fd)
    try:
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", f"scale={out_w}:{out_h}",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, timeout=300, check=True)
    except subprocess.CalledProcessError as e:
        try:
            os.unlink(output_path)
        except FileNotFoundError:
            pass
        logger.warning("normalize_video_to_target_sync: ffmpeg 失败 %s", e.stderr.decode() if e.stderr else e)
        return input_path, {"orig_w": w, "orig_h": h, "out_w": w, "out_h": h}

    return output_path, {"orig_w": w, "orig_h": h, "out_w": out_w, "out_h": out_h, "force_exact": force_exact}


async def create_black_placeholder_video(
    duration: float,
    output_path: str,
    width: int = 1280,
    height: int = 720,
    fps: int = 30
) -> str:
    """生成黑屏占位视频
    
    Args:
        duration: 视频时长（秒）
        output_path: 输出路径
        width: 视频宽度
        height: 视频高度
        fps: 帧率
        
    Returns:
        生成的黑屏视频路径
    """
    try:
        logger.info(f"🎬 生成黑屏占位视频: {duration:.2f}s, {width}x{height}")
        
        # 确保输出目录存在
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # FFmpeg命令：生成纯黑色视频（无音频）
        cmd = [
            'ffmpeg', '-y',
            '-f', 'lavfi',
            '-i', f'color=c=black:s={width}x{height}:r={fps}:d={duration}',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',  # 最快速度
            '-crf', '28',  # 低质量（占位视频不需要高质量）
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            '-an',  # 无音频轨道
            output_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        
        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise RuntimeError(f"生成黑屏视频失败: {error_msg}")
        
        if not os.path.exists(output_path):
            raise RuntimeError("黑屏视频文件未生成")
        
        logger.info(f"✅ 黑屏占位视频生成成功: {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"❌ 生成黑屏占位视频失败: {e}")
        raise


async def add_audio_track_to_video(
    video_path: str,
    audio_segments: list,  # [(audio_url, start_time, duration)]
    output_path: str,
    audio_volume: float = 0.8
) -> str:
    """为静音视频添加音频轨道（支持多个音频片段）
    
    Args:
        video_path: 输入视频路径（无音频或静音）
        audio_segments: 音频片段列表，格式: [(audio_path, start_time, duration), ...]
        output_path: 输出视频路径
        audio_volume: 音频音量 (0.0-1.0)
        
    Returns:
        处理后的视频路径
    """
    try:
        logger.info(f"🎵 为视频添加音频轨道: {len(audio_segments)} 个音频片段")
        
        if not audio_segments:
            logger.warning("⚠️ 没有音频片段，直接返回原视频")
            return video_path
        
        # 确保输出目录存在
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # 构建FFmpeg filter_complex，将多个音频片段合并到对应时间点
        filter_parts = []
        input_files = ['-i', video_path]
        
        # 添加所有音频文件作为输入
        for idx, (audio_path, start_time, duration) in enumerate(audio_segments):
            input_files.extend(['-i', audio_path])
            # 为每个音频添加延迟和音量控制
            filter_parts.append(
                f'[{idx+1}:a]adelay={int(start_time*1000)}|{int(start_time*1000)},'
                f'volume={audio_volume}[a{idx}]'
            )
        
        # 混合所有音频
        if len(audio_segments) == 1:
            audio_mix = '[a0]'
        else:
            audio_inputs = ''.join(f'[a{i}]' for i in range(len(audio_segments)))
            filter_parts.append(f'{audio_inputs}amix=inputs={len(audio_segments)}:duration=first[aout]')
            audio_mix = '[aout]'
        
        filter_complex = ';'.join(filter_parts)
        
        # FFmpeg命令
        cmd = [
            'ffmpeg', '-y',
            *input_files,
            '-filter_complex', filter_complex,
            '-map', '0:v',  # 使用第一个输入的视频
            '-map', audio_mix,  # 使用混合后的音频
            '-c:v', 'copy',  # 视频流直接复制，不重新编码
            '-c:a', 'aac',
            '-b:a', '256k',
            '-ar', '48000',
            '-shortest',  # 以最短的流为准
            output_path
        ]
        
        logger.info(f"🔧 FFmpeg命令: {' '.join(cmd[:20])}...")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # 根据视频总时长设置超时
        total_duration = sum(duration for _, _, duration in audio_segments)
        timeout = max(120, int(total_duration * 10))
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        
        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise RuntimeError(f"添加音频轨道失败: {error_msg}")
        
        logger.info(f"✅ 音频轨道添加成功: {output_path}")
        return output_path
        
    except Exception as e:
        logger.error(f"❌ 添加音频轨道失败: {e}")
        raise


# ============================================================================
# 视频合成工具 (原 video_merge_utils.py 的功能)
# ============================================================================


async def concatenate_videos_with_narration_and_effects(
    video_urls: List[str], 
    video_generations: List["VideoGenerationVersion"],
    narrations_data: Dict[str, Dict], 
    audio_effects_data: Dict[str, Dict], 
    title: str,
) -> str:
    """
    以旁白为主进行视频合成，整合音效
    
    逻辑：
    1. 获取每个视频片段对应的旁白时长
    2. 调整视频速度以匹配旁白时长
    3. 调整音效时长以匹配旁白时长
    4. 合成每个片段（视频+旁白+音效）
    5. 拼接所有片段
    """
    try:
        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        processed_videos = []
        
        try:
            for i, (video_url, video_gen) in enumerate(zip(video_urls, video_generations)):
                shot_number = video_gen.shot_number
                logger.info(f"🎬 处理镜头 {shot_number} 的视频: {video_url}")
                
                # 获取该镜头的旁白和音效
                narration_data = narrations_data.get(shot_number)
                audio_effect_data = audio_effects_data.get(shot_number)
                
                processed_video_path = None
                
                if narration_data and narration_data['version'].audio_url and narration_data['version'].duration:
                    # 有旁白：以旁白时长为准
                    narration_duration = narration_data['version'].duration
                    narration_audio_url = narration_data['version'].audio_url
                    
                    logger.info(f"🎙️ 镜头 {shot_number} 有旁白，时长: {narration_duration}s")
                    
                    # 检查是否有音效（音效是单独的音频文件）
                    if audio_effect_data and audio_effect_data['version'].success and audio_effect_data['version'].audio_url:
                        logger.info(f"🎵 镜头 {shot_number} 有音效，将音效和旁白混合到视频中")
                        
                        # 调整视频时长匹配旁白
                        adjusted_video_path = await adjust_video_speed_to_duration(
                            video_url, narration_duration, f"adjusted_video_{shot_number}_{i}"
                        )
                        
                        # 将音效添加到视频中
                        video_with_effect_path = await merge_audio_with_video(
                            video_url=adjusted_video_path,
                            audio_url=audio_effect_data['version'].audio_url,
                            output_filename=f"video_with_effect_{shot_number}_{i}"
                        )
                        
                        # 将旁白添加到视频+音效中（混合音频）
                        processed_video_path = await merge_video_with_mixed_audio(
                            video_url=video_with_effect_path,
                            additional_audio_url=narration_audio_url,
                            output_filename=f"processed_segment_{shot_number}_{i}_mixed_audio"
                        )
                        
                    else:
                        # 只有旁白，没有音效：调整纯视频时长
                        logger.info(f"🎬 镜头 {shot_number} 只有旁白，调整视频时长")
                        
                        adjusted_video_path = await adjust_video_speed_to_duration(
                            video_url, narration_duration, f"adjusted_video_{shot_number}_{i}"
                        )
                        
                        # 将旁白添加到调整后的视频中
                        processed_video_path = await merge_audio_with_video(
                            video_url=adjusted_video_path,
                            audio_url=narration_audio_url,
                            output_filename=f"processed_segment_{shot_number}_{i}"
                        )
                    
                elif audio_effect_data and audio_effect_data['version'].success and audio_effect_data['version'].audio_url:
                    # 只有音效，没有旁白：将音效添加到原视频中（音量30%）
                    logger.info(f"🎵 镜头 {shot_number} 只有音效，添加到原视频中（音量30%）")
                    
                    processed_video_path = await merge_audio_with_video(
                        video_url=video_url,
                        audio_url=audio_effect_data['version'].audio_url,
                        output_filename=f"video_with_effect_{shot_number}_{i}",
                        audio_volume=0.3  # 设置音效音量为30%
                    )
                else:
                    # 既没有旁白也没有音效：直接使用原视频
                    logger.info(f"🎬 镜头 {shot_number} 无旁白和音效，使用原视频")
                    processed_video_path = video_url
                
                processed_videos.append(processed_video_path)
            
            # 拼接所有处理后的视频片段
            logger.info(f"🎬 开始拼接 {len(processed_videos)} 个处理后的视频片段")
            final_video_url = await concatenate_video_segments_by_urls(processed_videos, title)
            
            return final_video_url
            
        finally:
            # 清理临时文件（在线程中执行，避免阻塞事件循环）
            try:
                if os.path.exists(temp_dir):
                    await asyncio.to_thread(shutil.rmtree, temp_dir)
            except Exception:
                pass
                
    except Exception as e:
        logger.error(f"❌ 旁白驱动视频合成失败: {e}")
        raise


async def adjust_video_speed_to_duration(video_url: str, target_duration: float, output_filename: str) -> str:
    """调整视频速度以匹配目标时长（legacy Suno 旁白逐镜 merge 路径，优先用 align_video_to_reference_audio）。"""
    return await align_video_to_reference_audio(
        video_url,
        target_duration=target_duration,
        allow_speed_adjust=True,
        run_id=uuid.uuid4().hex[:12],
    )


async def get_video_duration(video_input: str) -> Optional[float]:
    """获取视频时长
    
    Args:
        video_input: 视频URL或本地路径
    """
    result = await msc.video_info(video_input)
    return result["duration"]


async def force_video_to_duration_local(
    video_path: str,
    target_duration: float,
    output_path: str,
    tolerance: float = 0.2
) -> str:
    """将本地视频（无音轨或静音）强制为指定时长：不足垫黑，过长则调速。
    用于 assembly 时保证每段拼接长度与 slot duration 一致。
    """
    if not os.path.exists(video_path) or target_duration <= 0:
        return video_path
    try:
        current = await get_video_duration(video_path)
        logger.info(
            "[时长] force_video_to_duration_local: current=%s (type=%s), target_duration=%s (type=%s), tolerance=%s",
            current, type(current).__name__ if current is not None else "None",
            target_duration, type(target_duration).__name__, tolerance
        )
        if current is None:
            return video_path
        if abs(current - target_duration) <= tolerance:
            if video_path != output_path:
                await asyncio.to_thread(shutil.copy2, video_path, output_path)
            return output_path
        loop = asyncio.get_event_loop()
        if current < target_duration:
            pad_duration = target_duration - current
            black_path = output_path + "_pad_black.mp4"
            await create_black_placeholder_video(pad_duration, black_path)
            # concat video + black
            list_path = output_path + "_list.txt"

            def _write_concat_list():
                with open(list_path, "w") as f:
                    f.write(f"file '{os.path.abspath(video_path)}'\nfile '{os.path.abspath(black_path)}'\n")

            await asyncio.to_thread(_write_concat_list)
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
                "-c", "copy", output_path
            ]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await proc.communicate()
            def _cleanup_tmp():
                for p in (black_path, list_path):
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
            await asyncio.to_thread(_cleanup_tmp)
            if proc.returncode != 0 or not await asyncio.to_thread(os.path.exists, output_path):
                return video_path
            return output_path
        else:
            speed_factor = current / target_duration
            _ffmpeg_threads = 2  # 多 agent/多并发时避免单进程占满 CPU
            def _run():
                subprocess.run([
                    "ffmpeg", "-y", "-threads", str(_ffmpeg_threads), "-i", video_path,
                    "-filter:v", f"setpts={1/speed_factor:.6f}*PTS",
                    "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25",
                    output_path
                ], capture_output=True, check=True, timeout=120)
            await loop.run_in_executor(None, _run)
            return output_path
    except Exception as e:
        logging.getLogger(__name__).warning("force_video_to_duration_local failed: %s", e)
        return video_path


async def trim_audio_clip(
    audio_url: str,
    start_time: float,
    duration: float,
) -> str:
    """从音频 URL 裁切一段子片段，上传到 S3 并返回新 URL。
    与 create_music_from_audio_transcription_segments 同源：先下到本地，用 extract_audio_segment_from_local 裁切，再 upload_audio。
    """
    result = await msc.audio_trim(audio_url, start_time, duration, run_id=uuid.uuid4().hex[:12])
    logger.info(f"✅ Media service audio_trim: {audio_url} -> {result['result_url']}")
    return result["result_url"]


async def trim_video_to_duration_exact_local(
    local_path: str,
    target_duration: float,
    output_path: str,
) -> None:
    """按目标时长精确截断（重编码），得到 exactly target_duration，避免 -c copy 在关键帧处多出一截导致多段累加偏长。"""
    cmd = [
        "ffmpeg", "-y",
        "-i", local_path,
        "-t", str(target_duration),
        "-map", "0:v", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output_path,
    ]
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=120),
    )
    if result.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"ffmpeg 精确截断失败: {(result.stderr or '')[:500]}")


async def align_video_to_duration_exact_local(
    local_path: str,
    target_duration: float,
    output_path: str,
    tolerance: float = 0.05,
) -> str:
    """将本地视频对齐到精确时长：不足垫黑，过长截断（不调速）。用于 audio-driven 每 shot 按 shot.duration 对齐。"""
    if not os.path.exists(local_path) or target_duration <= 0:
        return local_path
    current = await get_video_duration(local_path)
    if current is None:
        return local_path
    if abs(current - target_duration) <= tolerance:
        if local_path != output_path:
            await asyncio.to_thread(shutil.copy2, local_path, output_path)
        return output_path
    if current < target_duration:
        pad_duration = target_duration - current
        black_path = output_path + "_pad_black.mp4"
        info = await get_video_stream_info_async(local_path)
        if info:
            w = int(info.get("width") or 1280)
            h = int(info.get("height") or 720)
            fps_val = parse_frame_rate(info.get("r_frame_rate") or "")
            fps_val = int(round(fps_val)) if fps_val > 0 else 30
        else:
            w, h, fps_val = 1280, 720, 30
        await create_black_placeholder_video(pad_duration, black_path, width=w, height=h, fps=fps_val)
        list_path = output_path + "_list.txt"

        def _write_concat_list():
            with open(list_path, "w") as f:
                f.write(f"file '{os.path.abspath(local_path)}'\nfile '{os.path.abspath(black_path)}'\n")

        await asyncio.to_thread(_write_concat_list)
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
            "-c", "copy", output_path
        ]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        for p in (black_path, list_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        exists = await asyncio.to_thread(os.path.exists, output_path)
        if proc.returncode != 0 or not exists:
            return local_path
        return output_path
    await trim_video_to_duration_exact_local(local_path, target_duration, output_path)
    return output_path


async def trim_video_to_duration(video_url: str, target_duration: float) -> str:
    """将视频按目标时长精确截断（重编码得到 exactly target_duration），上传到 S3 并返回新 URL。
    用于 lipsync：与音频时长严格对齐，避免 -c copy 关键帧截断导致多段累加偏长、成片与音频错位。
    """
    result = await msc.video_trim(
        video_url,
        target_duration,
        run_id=uuid.uuid4().hex[:12],
        tolerance=0.0,
    )
    logger.info(f"✅ Media service video_trim: {video_url} -> {result['result_url']}")
    return result["result_url"]


async def strip_audio_from_video(video_url: str) -> str:
    """去掉视频音轨，生成静音视频并上传 S3，返回新 URL。
    用于无 audio 的 wan25 输出：接口可能返回带默认音轨的视频，后处理去音轨便于 assembly 只加 BGM。
    """
    result = await msc.video_strip_audio(video_url, run_id=uuid.uuid4().hex[:12])
    logger.info(f"✅ Media service strip_audio: {video_url} -> {result['result_url']}")
    return result["result_url"]


async def ensure_lipsync_video_with_preview(
    remote_video_url: str,
    *,
    generation_id: str,
    target_width: Optional[int] = None,
    target_height: Optional[int] = None,
) -> tuple[str, str]:
    """上传 lipsync 视频：preview 保留音轨，pipeline 去音轨供后续 concat。

    Returns:
        (preview_video_url, muted_video_url)
    """
    from ..utils.s3_utils import s3_utils

    preview_url = await s3_utils.ensure_video_on_our_s3(
        remote_video_url,
        generation_id=f"{generation_id}_preview",
        target_width=target_width,
        target_height=target_height,
        strip_audio=False,
    )
    try:
        muted_url = await strip_audio_from_video(preview_url)
    except Exception as e:
        logger.warning("strip_audio 失败，回退 ensure strip_audio=True: %s", e)
        muted_url = await s3_utils.ensure_video_on_our_s3(
            remote_video_url,
            generation_id=generation_id,
            target_width=target_width,
            target_height=target_height,
            strip_audio=True,
        )
    return preview_url, muted_url


async def finalize_pipeline_video_upload(
    remote_video_url: str,
    *,
    generation_id: str,
    target_width: Optional[int] = None,
    target_height: Optional[int] = None,
    lipsync_preview: bool = False,
) -> tuple[str, Optional[str]]:
    """上传厂商出片到 CDN：普通 I2V 仅无声成片；lipsync 额外保留带声 preview。

    Returns:
        (pipeline_video_url, preview_video_url_or_none)
    """
    if lipsync_preview:
        preview_url, muted_url = await ensure_lipsync_video_with_preview(
            remote_video_url,
            generation_id=generation_id,
            target_width=target_width,
            target_height=target_height,
        )
        return muted_url, preview_url
    from ..utils.s3_utils import s3_utils

    pipeline_url = await s3_utils.ensure_video_on_our_s3(
        remote_video_url,
        generation_id=generation_id,
        target_width=target_width,
        target_height=target_height,
    )
    return pipeline_url, None


async def get_audio_duration_from_url(audio_input: str) -> Optional[float]:
    """从音频 URL 或本地路径实时获取实际时长。
    用于合成时以真实音频时长为准，避免使用陈旧或错误的 DB 存储值。
    """
    result = await msc.audio_info(audio_input)
    return result["duration"]


async def concatenate_video_segments_by_urls(video_urls: List[str], title: str) -> str:
    """标准视频拼接：优先 -c copy。音轨在各镜统一有/无时由 media concat 保留。"""
    result = await msc.video_concat(video_urls, run_id=uuid.uuid4().hex[:12], normalize=False)
    logger.info(f"✅ Media service video_concat: {len(video_urls)} segments -> {result['result_url']}")
    return result["result_url"]


async def merge_video_with_mixed_audio(
    video_url: str,
    additional_audio_url: str,
    output_filename: Optional[str] = None
) -> str:
    """
    将额外音频与视频现有音频混合（音效音量降低到30%）
    
    Args:
        video_url: 视频文件URL（已经包含音效）
        additional_audio_url: 要添加的音频URL（旁白）
        output_filename: 输出文件名（不含扩展名）
        
    Returns:
        合并后视频的URL路径
        
    Note:
        音效音量会被降低到30%，以确保旁白清晰可听
    """
    result = await msc.video_mix_audio(
        video_url, additional_audio_url,
        run_id=uuid.uuid4().hex[:12],
        video_volume=0.3, audio_volume=1.0,
    )
    logger.info(f"✅ Media service video_mix_audio: {video_url} -> {result['result_url']}")
    return result["result_url"]


async def _process_music_duration(
    music_path: str,
    target_seconds: float,
    mode: str = "loop",
    crossfade_ms: int = 1200,
    temp_dir: str = None
) -> str:
    """
    处理音乐时长以匹配目标时长
    参考 Suno 脚本的 ensure_duration 逻辑
    
    Args:
        music_path: 原始音乐文件路径
        target_seconds: 目标时长（秒）
        mode: 处理模式 ("loop"=循环拼接+交叉淡入淡出, "truncate"=截断)
        crossfade_ms: loop 模式下的交叉淡入淡出时长（毫秒）
        temp_dir: 临时目录
        
    Returns:
        处理后的音乐文件路径
    """
    from pydub import AudioSegment
    import asyncio
    
    loop = asyncio.get_event_loop()
    
    def _process_sync():
        import uuid
        
        # 加载音频
        audio = AudioSegment.from_file(music_path)
        audio_duration_ms = len(audio)
        target_ms = int(target_seconds * 1000)
        
        logger.info(f"🎵 音乐处理 - 原始时长: {audio_duration_ms/1000:.2f}s, 目标时长: {target_seconds:.2f}s, 模式: {mode}")
        
        # 如果音乐时长已经匹配或接近（允许±0.5秒误差），直接返回
        if abs(audio_duration_ms - target_ms) <= 500:
            logger.info(f"✅ 音乐时长已匹配，无需处理")
            return music_path
        
        # 如果音乐长于目标，直接截断
        if audio_duration_ms >= target_ms:
            logger.info(f"✂️ 音乐过长，截断到 {target_seconds:.2f}秒")
            out = audio[:target_ms]
        else:
            # 音乐短于目标
            if mode == "truncate":
                # truncate 模式：直接使用原音乐，不补静音（让 ffmpeg 的 -shortest 处理）
                logger.info(f"⏸️ truncate 模式：保持原音乐长度 {audio_duration_ms/1000:.2f}s")
                return music_path
            else:
                # loop 模式：循环拼接直到超过目标长度，带交叉淡入淡出
                logger.info(f"🔁 loop 模式：循环拼接音乐到 {target_seconds:.2f}秒（交叉淡入淡出 {crossfade_ms}ms）")
                segments = [audio]
                total = audio_duration_ms
                
                # 循环添加音频段直到超过目标长度
                while total < target_ms + crossfade_ms:
                    segments.append(audio)
                    total += audio_duration_ms
                
                # 拼接所有段，带交叉淡入淡出
                out = segments[0]
                for seg in segments[1:]:
                    out = out.append(seg, crossfade=crossfade_ms)
                
                # 截断到目标长度
                out = out[:target_ms]
        
        # 导出处理后的音频
        output_path = os.path.join(temp_dir or "/tmp", f"processed_music_{uuid.uuid4().hex[:8]}.mp3")
        out.export(output_path, format="mp3")
        logger.info(f"✅ 音乐处理完成: {output_path}")
        
        return output_path
    
    # 在线程池中执行同步操作
    return await loop.run_in_executor(None, _process_sync)


async def adjust_lipsync_audio_volume(
    video_url: str,
    music_volume: float = 0.3
) -> str:
    """调整 lipsync 视频的整体音频音量
    
    用于统一 lipsync 视频和非 lipsync 视频的音量，确保音乐音量一致（通常为 30%）
    
    Args:
        video_url: lipsync 视频的 URL
        music_volume: 目标音乐音量（0.0-1.0），默认 0.3
        
    Returns:
        调整后的视频 URL
    """
    result = await msc.video_mix_audio(
        video_url, video_url,
        run_id=uuid.uuid4().hex[:12],
        video_volume=music_volume, audio_volume=0.0,
    )
    logger.info(f"✅ Media service lipsync volume adjust: {video_url} -> {result['result_url']}")
    return result["result_url"]


async def add_background_music_to_video(
    video_url: str, 
    music_url: str, 
    title: str,
    music_mode: str = "loop",  # "loop" 或 "truncate"
    crossfade_ms: int = 1200   # loop 模式下的交叉淡入淡出时长（毫秒）
) -> str:
    """
    为视频添加背景音乐，支持音乐时长处理
    
    Args:
        video_url: 视频文件URL
        music_url: 音乐文件URL
        title: 输出视频标题
        music_mode: 音乐长度处理模式 ("loop"=循环拼接+交叉淡入淡出, "truncate"=截断或补静音)
        crossfade_ms: loop 模式下的交叉淡入淡出时长（毫秒）
        
    Returns:
        合成后的视频URL
    """
    result = await msc.video_mix_audio(
        video_url, music_url,
        run_id=uuid.uuid4().hex[:12],
        video_volume=1.0, audio_volume=0.3,
        loop_audio=True,
    )
    logger.info(f"✅ Media service add_bg_music: {video_url} -> {result['result_url']}")
    return result["result_url"]
