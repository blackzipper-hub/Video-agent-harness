"""
视频片段处理服务
负责将音频驱动的多个视频合并成片段，并处理唇形同步
"""

import logging
import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import List, Any, Optional, Union, Tuple
from datetime import datetime

from ....models.video_state import (
    VideoAgentState, 
    VideoAssemblyData
)
from ....schemas.video.video_segment import (
    VideoSegmentDB,
    VideoSegmentVersionDB,
)
from ....crud.video.video_segment import (
    create_video_segment,
    create_video_segment_version
)
from ....utils.temp_file_utils import temp_directory, upload_file_from_temp
from ....utils import media_service_client as msc
from ....models.video_state import VideoSegmentResult, AudioVideoMapping, VideoSegmentStatus
from ....exceptions import BusinessException, BusinessExceptionCode
from ....services.agent.base_agent import MessageType
from langgraph.runtime import Runtime
from ..schemas import VideoContextSchema
from ..utils.cancellation import raise_if_cancelled

logger = logging.getLogger(__name__)

# 与 video_assembly_service 成片音画对齐一致：小偏差跳过 MSC；中等 freeze_or_tail_slow；大偏差整片调速（lipsync 不调速）
SEGMENT_ALIGN_SKIP_SEC = 0.02
SEGMENT_LIGHT_ALIGN_MAX_SEC = 2.0
SEGMENT_ALIGN_PROBE_RETRY_MAX = 1


def _predict_align_strategy(
    duration_diff: float,
    *,
    allow_speed_adjust: bool,
) -> str:
    """根据 probe 与 target 差值预测对齐策略（仅用于日志，便于排查「重复/定格」）。"""
    ad = abs(duration_diff)
    if ad <= SEGMENT_ALIGN_SKIP_SEC:
        return "skip"
    if ad <= SEGMENT_LIGHT_ALIGN_MAX_SEC:
        if duration_diff < 0:
            return "extend_freeze_or_tail_slow_then_trim"
        return "trim_only"
    if allow_speed_adjust:
        return "speed_adjust"
    if duration_diff < 0:
        return "extend_freeze_or_tail_slow_then_trim_large"
    return "trim_only_large"


async def _finalize_segment_align_probe(
    result_url: str,
    target_duration: float,
    run_id: str,
    segment_number: int,
    probe_before: float,
) -> str:
    """对齐后 probe；仍偏长则再 trim_only（弥补 MSC hybrid 关键帧误差）。"""
    after_d = probe_before
    for retry_idx in range(SEGMENT_ALIGN_PROBE_RETRY_MAX + 1):
        try:
            _after_vi = await msc.video_info(result_url)
            after_d = float(_after_vi.get("duration", 0))
        except Exception as _e:
            logger.warning("   [时长诊断] 片段 %s 对齐后 video_info 失败: %s", segment_number, _e)
            return result_url
        after_delta = after_d - target_duration
        if abs(after_delta) <= SEGMENT_ALIGN_SKIP_SEC:
            logger.info(
                "   [时长诊断] 片段 %s 对齐结果: probe_before=%.3fs"
                " probe_after=%.3fs target=%.3fs Δ=%+.3fs",
                segment_number, probe_before, after_d, target_duration, after_delta,
            )
            return result_url
        if after_d > target_duration + SEGMENT_ALIGN_SKIP_SEC and retry_idx < SEGMENT_ALIGN_PROBE_RETRY_MAX:
            logger.info(
                "   [对齐诊断] 片段 %s: 二次 trim_only probe=%.4fs -> target=%.4fs Δ=%+.4fs",
                segment_number, after_d, target_duration, after_delta,
            )
            _trim_retry = await msc.video_trim(
                result_url,
                target_duration,
                f"{run_id}_retry{retry_idx}",
                mode="trim_only",
                tolerance=0.0,
            )
            result_url = _trim_retry["result_url"]
            continue
        if abs(after_delta) > SEGMENT_ALIGN_SKIP_SEC:
            logger.warning(
                "   ⚠️ 片段 %s: 对齐后仍有偏差 probe=%.3fs target=%.3fs Δ=%+.3fs",
                segment_number, after_d, target_duration, after_delta,
            )
        else:
            logger.info(
                "   [时长诊断] 片段 %s 对齐结果: probe_before=%.3fs"
                " probe_after=%.3fs target=%.3fs Δ=%+.3fs",
                segment_number, probe_before, after_d, target_duration, after_delta,
            )
        return result_url
    return result_url


async def _log_shot_probes_before_merge(
    segment_number: int,
    video_urls: List[Optional[str]],
    shot_durations: Optional[List[float]],
    target_duration: float,
    is_lipsync: bool,
) -> None:
    """merge 前记录每镜 probe vs 目标，便于排查段间黑屏（短镜 → segment freeze 补时）。"""
    n = len(video_urls)
    logger.info(
        "[黑屏诊断] 片段 %s merge 输入: shots=%d segment_target=%.4fs lipsync=%s",
        segment_number,
        n,
        float(target_duration),
        is_lipsync,
    )
    tasks: List[Any] = []
    indices: List[int] = []
    for i, url in enumerate(video_urls):
        if url:
            tasks.append(msc.video_info(url))
            indices.append(i)
    if not tasks:
        logger.info("[黑屏诊断] 片段 %s: 无可用视频 URL，跳过镜级 probe", segment_number)
        return
    results = await asyncio.gather(*tasks, return_exceptions=True)
    short_count = 0
    for idx, res in zip(indices, results):
        shot_n = idx + 1
        if isinstance(res, BaseException):
            logger.warning(
                "[黑屏诊断] 片段 %s shot%d: video_info 失败: %s",
                segment_number,
                shot_n,
                res,
            )
            continue
        probe = float(res.get("duration", 0))
        if shot_durations and idx < len(shot_durations) and shot_durations[idx] and shot_durations[idx] > 0:
            tgt = float(shot_durations[idx])
            delta = probe - tgt
            if delta < -0.02:
                short_count += 1
            if delta < -0.02:
                hint = "SHORT→segment层可能freeze末帧"
            elif delta > 0.02:
                hint = "LONG→segment层可能trim"
            else:
                hint = "ok"
            logger.info(
                "[黑屏诊断] 片段 %s shot%d: probe=%.4fs shot_tgt=%.4fs Δ=%+.4fs %s",
                segment_number,
                shot_n,
                probe,
                tgt,
                delta,
                hint,
            )
        else:
            logger.info(
                "[黑屏诊断] 片段 %s shot%d: probe=%.4fs (无 per-shot 目标)",
                segment_number,
                shot_n,
                probe,
            )
    if short_count:
        logger.warning(
            "[黑屏诊断] 片段 %s: %d/%d 镜短于 shot 目标；若 segment 仍短，将走 freeze_or_tail_slow"
            "（段尾静帧，观感可能像段间黑屏）",
            segment_number,
            short_count,
            n,
        )


async def _log_segment_merge_output_probe(
    segment_number: int,
    merged_url: str,
    target_duration: float,
    *,
    merge_path: str,
) -> None:
    """merge 完成后 probe 成片，对照 segment 目标。"""
    try:
        vi = await msc.video_info(merged_url)
        probe = float(vi.get("duration", 0))
        delta = probe - float(target_duration)
        level = logger.warning if abs(delta) > 0.02 else logger.info
        level(
            "[黑屏诊断] 片段 %s merge 输出(%s): probe=%.4fs target=%.4fs Δ=%+.4fs",
            segment_number,
            merge_path,
            probe,
            float(target_duration),
            delta,
        )
    except Exception as e:
        logger.warning(
            "[黑屏诊断] 片段 %s merge 输出(%s) video_info 失败: %s",
            segment_number,
            merge_path,
            e,
        )


async def _align_segment_video_to_target_duration(
    video_url: str,
    target_duration: float,
    run_id: str,
    *,
    segment_number: int,
    allow_speed_adjust: bool,
    shot_index: Optional[int] = None,
) -> str:
    """合并/拼接后的 segment 视频对齐到目标时长（probe 为准）。"""
    if target_duration <= 0:
        return video_url
    video_info = await msc.video_info(video_url)
    current = float(video_info.get("duration", 0))
    duration_diff = current - target_duration
    ad = abs(duration_diff)
    _shot_label = f"shot{shot_index}" if shot_index is not None else "segment"
    _strategy = _predict_align_strategy(duration_diff, allow_speed_adjust=allow_speed_adjust)
    logger.info(
        "   [对齐诊断] 片段 %s %s: probe=%.4fs target=%.4fs Δ=%+.4fs | 策略=%s | lipsync=%s",
        segment_number,
        _shot_label,
        current,
        target_duration,
        duration_diff,
        _strategy,
        not allow_speed_adjust,
    )
    if _strategy.startswith("extend_freeze"):
        logger.info(
            "   [对齐诊断] 片段 %s %s: 视频短于目标 → 静帧/尾慢延长（非循环）；"
            " gap≤0.2s 静帧末帧，gap>0.2s 尾部减速",
            segment_number,
            _shot_label,
        )
        logger.warning(
            "[黑屏诊断] 片段 %s %s: 执行 freeze_or_tail_slow 补 %.3fs（末帧静帧，段尾/段间接缝可能发黑）",
            segment_number,
            _shot_label,
            -duration_diff,
        )
    if ad <= SEGMENT_ALIGN_SKIP_SEC:
        logger.info(
            "   ✅ 片段 %s: 时长在容差内 probe=%.3f target=%.3f |diff|=%.3fs，跳过对齐",
            segment_number, current, target_duration, ad,
        )
        return video_url
    if ad <= SEGMENT_LIGHT_ALIGN_MAX_SEC:
        if duration_diff < 0:
            # 延长场景：两步法 freeze_or_tail_slow(target+0.1) → trim_only(target)
            # freeze 先过冲保证视频一定够长，trim_only 精确截到 target
            _overshoot = target_duration + 0.1
            logger.info(
                "   🎬 片段 %s: 轻量对齐·延长 %.3fs -> %.3fs (Δ=%+.3fs)"
                " 两步法: freeze→%.3fs + trim_only→%.3fs",
                segment_number, current, target_duration, duration_diff, _overshoot, target_duration,
            )
            _freeze_result = await msc.video_trim(
                video_url, _overshoot, run_id,
                mode="freeze_or_tail_slow", tolerance=0.0,
            )
            _freeze_url = _freeze_result["result_url"]
            _trim_result = await msc.video_trim(
                _freeze_url, target_duration, run_id,
                mode="trim_only", tolerance=0.0,
            )
            _result_url = _trim_result["result_url"]
        else:
            # 截短场景：直接 trim_only（精确且快速）
            logger.info(
                "   🎬 片段 %s: 轻量对齐·截短 %.3fs -> %.3fs (Δ=%+.3fs) trim_only",
                segment_number, current, target_duration, duration_diff,
            )
            _trim_result = await msc.video_trim(
                video_url, target_duration, run_id,
                mode="trim_only", tolerance=0.0,
            )
            _result_url = _trim_result["result_url"]
        return await _finalize_segment_align_probe(
            _result_url, target_duration, run_id, segment_number, current,
        )
    if allow_speed_adjust:
        speed_factor = current / target_duration if target_duration > 0 else 1.0
        logger.info(
            "   🎬 片段 %s: 整片调速对齐 %.2fs -> %.2fs (Δ=%+.2fs, 因子≈%.4fx)",
            segment_number, current, target_duration, duration_diff, speed_factor,
        )
        speed_result = await msc.video_speed_adjust(
            video_url, target_duration, run_id=run_id
        )
        return speed_result["result_url"]
    if duration_diff < 0:
        # lipsync 大偏差延长：同样用两步法 freeze(target+0.1) → trim_only(target)
        _overshoot2 = target_duration + 0.1
        logger.info(
            "   🎬 片段 %s: lipsync 大偏差(>%ss) 不调速·延长 %.3fs -> %.3fs"
            " 两步法: freeze→%.3fs + trim_only→%.3fs",
            segment_number, SEGMENT_LIGHT_ALIGN_MAX_SEC, current, target_duration,
            _overshoot2, target_duration,
        )
        _freeze_result2 = await msc.video_trim(
            video_url, _overshoot2, run_id,
            mode="freeze_or_tail_slow", tolerance=0.0,
        )
        _trim_result2 = await msc.video_trim(
            _freeze_result2["result_url"], target_duration, run_id,
            mode="trim_only", tolerance=0.0,
        )
        _result_url2 = _trim_result2["result_url"]
    else:
        # 大偏差截短：直接 trim_only
        logger.info(
            "   🎬 片段 %s: lipsync 大偏差(>%ss) 不调速·截短 %.3fs -> %.3fs trim_only",
            segment_number, SEGMENT_LIGHT_ALIGN_MAX_SEC, current, target_duration,
        )
        _trim_result2 = await msc.video_trim(
            video_url, target_duration, run_id,
            mode="trim_only", tolerance=0.0,
        )
        _result_url2 = _trim_result2["result_url"]
    return await _finalize_segment_align_probe(
        _result_url2, target_duration, run_id, segment_number, current,
    )


@dataclass
class SegmentProcessRequest:
    """单片段处理请求：pipeline 与 Sync 共用同一入口 process_segment_by_request。"""
    segment_number: int
    video_urls: List[str]
    target_duration: float
    shot_durations: Optional[List[float]] = None
    is_lipsync: bool = False
    placeholder_duration: Optional[float] = None


@dataclass
class SegmentMergeResult:
    """merge_segment_videos 的返回结果，Pipeline 和 Sync 共用。"""
    merged_video_url: Optional[str]
    status: VideoSegmentStatus
    success: bool
    error_msg: Optional[str]
    failed_video_count: int
    total_video_count: int
    shot_durations: Optional[List[float]]


async def resolve_shot_durations(
    detailed_shot_ids: List[Optional[str]],
    segment_number: int,
) -> Optional[List[float]]:
    """从 detailed_shot uuid 列表解析 shot durations。Pipeline 和 Sync 共用。"""
    if not detailed_shot_ids:
        return None
    valid_ids = [sid for sid in detailed_shot_ids if sid]
    if not valid_ids:
        return None
    from ....crud.video.video_story import get_detailed_shots_by_uuids
    unique_ids = list(dict.fromkeys(valid_ids))
    shots_db = await get_detailed_shots_by_uuids(unique_ids)
    duration_map = {getattr(s, 'uuid', None): getattr(s, 'duration', None) for s in shots_db}
    durations = [duration_map.get(sid) if sid else None for sid in detailed_shot_ids]
    if all(d is not None and d > 0 for d in durations):
        logger.info(f"   [时长] 片段 {segment_number} shot 时长: {durations}")
        return durations
    logger.warning(
        "   [时长] 片段 %s 无法解析 per-shot 时长 (ids=%s resolved=%s)",
        segment_number,
        [sid[:8] + "..." if sid else None for sid in detailed_shot_ids],
        durations,
    )
    return None


async def _generate_black_placeholder_url(segment_number: int, duration: float) -> str:
    """生成黑屏占位视频并上传，返回 URL。"""
    logger.warning(
        "[黑屏诊断] 片段 %s: 生成黑场占位视频 duration=%.4fs（整段或失败 shot 垫黑）",
        segment_number,
        float(duration),
    )
    req = SegmentProcessRequest(
        segment_number=segment_number,
        video_urls=[],
        target_duration=0.0,
        placeholder_duration=duration,
    )
    return await process_segment_by_request(req)


async def merge_segment_videos(
    segment_number: int,
    all_video_urls: List[Optional[str]],
    target_duration: float,
    all_detailed_shot_ids: List[Optional[str]],
    is_lipsync: bool,
) -> SegmentMergeResult:
    """
    Pipeline / Sync 共用入口：合并视频，失败的 shot 用黑屏占位保持时间位置正确。

    Args:
        all_video_urls: 按 shot 顺序，成功的为 URL，失败的为 None
        all_detailed_shot_ids: 按 shot 顺序，所有 shot 的 detailed_shot_id
        target_duration: 目标总时长（= 音频时长）
        is_lipsync: 是否 lipsync 模式

    处理逻辑:
        - 全部成功：正常合并
        - 部分失败 + 有 shot durations：失败 shot 用黑屏占位（保持位置），合并
        - 部分失败 + 无 shot durations：只用成功视频合并，末尾 pad（兜底）
        - 全部失败 + duration > 0：整段黑屏占位
    """
    total_count = len(all_video_urls)
    failed_count = sum(1 for u in all_video_urls if not u)
    successful_urls = [u for u in all_video_urls if u]

    shot_durations = await resolve_shot_durations(all_detailed_shot_ids, segment_number)
    if shot_durations:
        _sum_sd = sum(shot_durations)
        logger.info(
            "[时长诊断] 片段 %s merge: target_duration(music映射)=%.6f | detailed_shot 列表=%s sum=%.6f | Δ(sum−target)=%+.6f | lipsync=%s",
            segment_number,
            float(target_duration),
            shot_durations,
            _sum_sd,
            _sum_sd - float(target_duration),
            is_lipsync,
        )
    else:
        logger.info(
            "[时长诊断] 片段 %s merge: target_duration=%.6f | 无 per-shot 时长解析 | lipsync=%s",
            segment_number,
            float(target_duration),
            is_lipsync,
        )

    merged_video_url = None
    merge_error_msg = None

    if shot_durations and successful_urls:
        await _log_shot_probes_before_merge(
            segment_number,
            all_video_urls,
            shot_durations,
            target_duration,
            is_lipsync,
        )

    if not successful_urls:
        # ═══ 全部失败：整段黑屏占位 ═══
        if target_duration > 0:
            logger.warning(
                "[黑屏诊断] 片段 %s: 全部 %d shot 失败 → 整段黑场占位 %.2fs",
                segment_number,
                total_count,
                target_duration,
            )
            logger.warning(f"⚠️ 片段 {segment_number}: 全部 {total_count} shot 失败，生成整段黑屏占位（{target_duration:.2f}s）")
            try:
                merged_video_url = await _generate_black_placeholder_url(segment_number, target_duration)
                logger.info(f"✅ 片段 {segment_number}: 黑屏占位已上传")
            except Exception as e:
                logger.error(f"❌ 片段 {segment_number}: 黑屏占位失败: {e}")
                merge_error_msg = str(e)
    elif failed_count > 0 and shot_durations:
        # ═══ 部分失败 + 有 shot durations：per-shot 黑屏占位（保持位置） ═══
        logger.warning(
            "[黑屏诊断] 片段 %s: %d/%d shot 失败 → per-shot 黑场占位后合并",
            segment_number,
            failed_count,
            total_count,
        )
        logger.info(f"📹 片段 {segment_number}: {failed_count}/{total_count} shot 失败，生成 per-shot 黑屏占位")
        try:
            max_failed_dur = max(
                shot_durations[i] for i in range(total_count)
                if not all_video_urls[i] and shot_durations[i] > 0
            )
            black_url = await _generate_black_placeholder_url(segment_number, max_failed_dur)
            final_urls = [u if u else black_url for u in all_video_urls]
            req = SegmentProcessRequest(
                segment_number=segment_number,
                video_urls=final_urls,
                target_duration=target_duration,
                shot_durations=shot_durations,
                is_lipsync=is_lipsync,
            )
            merged_video_url = await process_segment_by_request(req)
            if merged_video_url:
                await _log_segment_merge_output_probe(
                    segment_number, merged_video_url, target_duration, merge_path="per-shot黑场占位",
                )
            logger.info(f"✅ 片段 {segment_number}: per-shot 黑屏占位合并完成")
        except Exception as e:
            logger.error(f"❌ 片段 {segment_number} per-shot 占位合并失败: {e}")
            merge_error_msg = str(e)
    else:
        # ═══ 全部成功 / 部分失败但无 per-shot 时长（兜底：只用成功视频，末尾 pad） ═══
        try:
            effective_durations = shot_durations if (shot_durations and failed_count == 0) else None
            req = SegmentProcessRequest(
                segment_number=segment_number,
                video_urls=successful_urls,
                target_duration=target_duration,
                shot_durations=effective_durations,
                is_lipsync=is_lipsync,
            )
            merged_video_url = await process_segment_by_request(req)
            if merged_video_url:
                _path = "lipsync" if is_lipsync else "normal"
                await _log_segment_merge_output_probe(
                    segment_number, merged_video_url, target_duration, merge_path=_path,
                )
            if is_lipsync:
                logger.info(f"🎭 片段 {segment_number}: lipsync 模式合并完成")
            else:
                logger.info(f"📹 片段 {segment_number}: 合并完成")
        except Exception as e:
            logger.error(f"❌ 片段 {segment_number} 合并失败: {e}")
            merge_error_msg = str(e)

    # ═══ 计算合成状态 ═══
    is_full_placeholder = not successful_urls and merged_video_url is not None
    if is_full_placeholder or (failed_count == 0 and merged_video_url is not None):
        status = VideoSegmentStatus.SUCCESS
        success = True
        error_msg = None
    elif merged_video_url is not None and failed_count > 0:
        status = VideoSegmentStatus.PARTIAL_SUCCESS
        success = True
        error_msg = f"{failed_count}/{total_count} shot 失败（已用黑屏占位）"
    else:
        status = VideoSegmentStatus.FAILED
        success = False
        error_msg = merge_error_msg or f"{failed_count}/{total_count} 视频生成失败"

    return SegmentMergeResult(
        merged_video_url=merged_video_url,
        status=status,
        success=success,
        error_msg=error_msg,
        failed_video_count=failed_count,
        total_video_count=total_count,
        shot_durations=shot_durations,
    )


async def process_segment_by_request(request: SegmentProcessRequest) -> str:
    """
    单片段处理统一入口：无视频时黑屏占位；lipsync 时只截断不调速；有 shot_durations 时按 shot 截断/垫黑后合并；
    否则合并后按目标时长截断/垫黑（不调速）。
    """
    if not request.video_urls:
        if request.placeholder_duration is not None and request.placeholder_duration > 0:
            from ....utils.video_utils import create_black_placeholder_video
            async with temp_directory() as temp_dir:
                placeholder_path = os.path.join(
                    temp_dir, f"black_segment_{request.segment_number}_{uuid.uuid4().hex[:8]}.mp4"
                )
                await create_black_placeholder_video(
                    duration=request.placeholder_duration, output_path=placeholder_path
                )
                return await upload_file_from_temp(
                    placeholder_path,
                    f"videos/black_segment_{request.segment_number}_{uuid.uuid4().hex[:8]}.mp4",
                    f"片段{request.segment_number}黑屏占位",
                )
        raise ValueError("no video_urls and no placeholder_duration")
    logger.info(
        "[黑屏诊断] 片段 %s process_segment: urls=%d target=%.4fs lipsync=%s shot_durations=%s",
        request.segment_number,
        len(request.video_urls),
        float(request.target_duration),
        request.is_lipsync,
        "有" if request.shot_durations else "无",
    )
    if request.is_lipsync:
        if len(request.video_urls) > 1 and request.shot_durations:
            logger.warning(
                "[对齐诊断] 片段 %s: lipsync 多镜头(%d) 已有 shot_durations=%s，"
                "但当前仍走整段 concat→trim（未做 per-shot 对齐）",
                request.segment_number,
                len(request.video_urls),
                request.shot_durations,
            )
            logger.warning(
                "[黑屏诊断] 片段 %s: lipsync 多镜整段 concat，镜头边界可能对不齐",
                request.segment_number,
            )
        return await merge_and_trim_lipsync_videos(
            video_urls=request.video_urls,
            target_duration=request.target_duration,
            segment_number=request.segment_number,
        )
    return await process_and_merge_videos(
        video_urls=request.video_urls,
        target_duration=request.target_duration,
        segment_number=request.segment_number,
        shot_durations=request.shot_durations,
    )


async def video_segments_node(
    state: VideoAgentState,
    runtime: Runtime[VideoContextSchema],
    send_event_func: Any
) -> dict:
    """
    视频片段处理节点
    
    功能：
    1. 将音频驱动的多个视频合并成片段
    2. 为每个片段生成唇形同步视频（如果需要）
    3. 将结果存储到数据库
    4. 更新状态中的 video_segments_uuids
    """
    logger.info("🎬 开始视频片段处理...")
    
    try:
        # 从 state 中的 UUID 重新构建 VideoAssemblyData（参考 video_assembly_service 的做法）
        story_outline_uuid = state.get("story_outline_uuid")
        if not story_outline_uuid:
            raise BusinessException(
                BusinessExceptionCode.VIDEO_ANALYSIS_UUID_MISSING,
                "缺少故事梗概UUID"
            )
        
        # 从数据库获取最新数据
        from ..utils.video_data_utils import get_video_assembly_data_from_state
        video_assembly_data = await get_video_assembly_data_from_state(state, runtime)
        if not video_assembly_data:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                "无法获取视频组装数据"
            )
        
        # 从 state 获取上下文信息
        conversation_id = state.get("conversation_id")  # 保持原始类型（int）
        thread_id = state.get("thread_id")
        run_id = state.get("run_id")
        user_id = state.get("user_id")
        
        # 根据模式选择处理方式
        # 🔧 使用 audio_transcription 来判断是否是 audio-driven 模式
        is_audio_driven = bool(video_assembly_data.audio_transcription)
        logger.info(f"📋 视频组装模式: {'Audio-driven' if is_audio_driven else 'Video-driven'} (audio_transcription: {is_audio_driven})")
        
        # 🎯 报告初始进度（0%）
        total_segments = len(video_assembly_data.video_generations)
        # await send_event_func(
        #     event_type=MessageType.VIDEO_SEGMENTS_PROGRESS,
        #     conversation_id=conversation_id,
        #     extra_data={
        #         "completed": 0,
        #         "total": total_segments
        #     },
        #     hidden=True,
        #     save_to_db=False
        # )
        
        if is_audio_driven:
            # Audio-driven 模式（music_driven）：按音频片段分组
            audio_segments_mapping = await _analyze_audio_video_mapping(video_assembly_data)
            # 🔧 补全：转录中有但未参与任何镜头的 audio segment 也建 segment（与 assembly 黑屏占位一致，1:1 覆盖全量音频）
            sorted_audio_segments = _build_ordered_audio_segments(
                video_assembly_data,
                audio_segments_mapping,
            )
            logger.info(f"📊 音频片段处理顺序: 共 {len(sorted_audio_segments)} 段（含未参与镜头的占位）")
            
            # 并行处理每个音频片段
            segment_results = await _process_segments_parallel(
                sorted_audio_segments,
                video_assembly_data,
                send_event_func
            )
        else:
            # Video-driven 模式：shot 一一对应到 video segment
            segment_results = await _process_segments_video_driven(
                video_assembly_data,
                send_event_func,
                runtime,  # 传递 runtime 而不是 db，让函数内部获取
                conversation_id  # 传递 conversation_id 用于进度报告
            )
        
        # 存储视频片段到数据库并获取 UUIDs
        video_segments_uuids = await _save_video_segments_to_db(
            segment_results,
            video_assembly_data,
            str(conversation_id),  # 转换为 str，数据库函数需要
            thread_id,
            run_id,
            user_id,
            runtime
        )
        
        logger.info(f"✅ 视频片段处理完成: {video_segments_uuids}")
        
        # 不发送 VIDEO_SEGMENTS_ASSEMBLED（前端不展示 lyrics section 时不依赖此事件）
        
        return {
            "video_segments_uuids": video_segments_uuids,
            "video_segments_data": segment_results
        }
        
    except Exception as e:
        logger.error(f"❌ 视频片段处理失败: {e}")
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"视频片段处理失败: {str(e)}"
        )


async def _process_segments_video_driven(
    video_assembly_data: VideoAssemblyData,
    send_event_func: Any,
    runtime: Any,
    conversation_id: int
) -> List[VideoSegmentResult]:
    """
    Video-driven 模式处理：shot 一一对应到 video segment
    
    特点：
    - 一个 shot 对应一个 video segment
    - music_generation 只有一个，取第一个版本
    - 不需要 audio_segments_mapping
    
    Args:
        video_assembly_data: 视频组装数据
        send_event_func: 发送事件函数
        runtime: Runtime 对象（用于获取数据库会话）
        conversation_id: 对话ID（用于进度报告）
    """
    total_shots = len(video_assembly_data.video_generations)
    logger.info(f"🎬 Video-driven 模式：处理 {total_shots} 个 shots...")
    
    segment_results = []
    
    # 获取唯一的 music_generation（只有一个）
    music_generation_id = None
    if video_assembly_data.music_data:
        # 取第一个 music_data
        first_key = next(iter(video_assembly_data.music_data.keys()))
        music_info = video_assembly_data.music_data[first_key]
        if music_info and music_info.get('music_generation'):
            music_generation_id = music_info['music_generation'].uuid
            logger.info(f"🎵 使用 music_generation_id: {music_generation_id}")
    
    # 🔧 批量获取所有 video_generation_db 以获取关联的 IDs（scene_id, keyframe_id 等）
    # 从 video_generations 中提取 video_generation_id 列表
    video_gen_ids = [vg.video_generation_id for vg in video_assembly_data.video_generations if hasattr(vg, 'video_generation_id') and vg.video_generation_id]
    
    # 批量查询 VideoGenerationDB
    video_gen_db_map = {}
    if video_gen_ids:
        from ....crud.video.video_generation import get_video_generations_by_uuids
        # ✅ 使用asyncpg CRUD，不需要数据库连接
        video_gen_dbs = await get_video_generations_by_uuids(video_gen_ids)
        video_gen_db_map = {vg.uuid: vg for vg in video_gen_dbs}
        logger.info(f"📦 批量获取 {len(video_gen_db_map)} 个 VideoGenerationDB 以获取关联 IDs")
    
    # 遍历每个 video_generation，创建对应的 segment
    for segment_number, video_gen in enumerate(video_assembly_data.video_generations, 1):
        shot_number = video_gen.shot_number
        video_url = video_gen.video_url
        duration = video_gen.duration
        
        logger.info(f"📹 处理 segment {segment_number}: shot {shot_number}")
        
        # 🔧 从 video_assembly_data 中提取对应的资源 IDs
        video_generation_ids = [video_gen.video_generation_id] if hasattr(video_gen, 'video_generation_id') and video_gen.video_generation_id else []
        
        # 从 narrations_data 获取 narration_ids（按 shot_number 匹配）
        narration_ids = []
        if shot_number in video_assembly_data.narrations_data:
            narration_data = video_assembly_data.narrations_data[shot_number]
            if 'narration' in narration_data:
                narration_ids.append(narration_data['narration'].uuid)
        
        # 🔧 从 VideoGenerationDB 中获取关联的 keyframe_ids, scene_id, storyboard_detail_id
        keyframe_ids = []
        scene_ids = []
        storyboard_detail_ids = []
        
        if video_gen.video_generation_id and video_gen.video_generation_id in video_gen_db_map:
            video_gen_db = video_gen_db_map[video_gen.video_generation_id]
            # ⭐ 优先使用新字段 keyframe_ids，降级到旧字段 keyframe_id
            if video_gen_db.keyframe_ids:
                keyframe_ids.extend(video_gen_db.keyframe_ids)
            elif video_gen_db.keyframe_id:
                keyframe_ids.append(video_gen_db.keyframe_id)
            
            if video_gen_db.scene_id:
                scene_ids.append(video_gen_db.scene_id)
            if video_gen_db.storyboard_detail_id:
                storyboard_detail_ids.append(video_gen_db.storyboard_detail_id)
        
        logger.info(f"   📊 Segment {segment_number}: {len(video_generation_ids)} video_gens, {len(narration_ids)} narrations, {len(keyframe_ids)} keyframes, {len(scene_ids)} scenes")
        
        # 🔧 计算合成状态
        from ....models.video_state import VideoSegmentStatus
        
        success = video_gen.success and bool(video_url)
        if success:
            status = VideoSegmentStatus.SUCCESS
            failed_count = 0
        else:
            status = VideoSegmentStatus.FAILED
            failed_count = 1
        
        # 创建 VideoSegmentResult
        segment_result = VideoSegmentResult(
            segment_number=segment_number,
            success=success,
            status=status,
            failed_video_count=failed_count,
            total_video_count=1,  # video-driven模式每个segment只有一个视频
            video_generation_ids=video_generation_ids,
            music_generation_id=music_generation_id,
            narration_ids=narration_ids,
            keyframe_ids=keyframe_ids,
            scene_ids=scene_ids,
            storyboard_detail_ids=storyboard_detail_ids,
            original_video_urls=[video_url] if video_url else [],
            merged_video_url=video_url if video_url else None,  # video-driven 不需要合并
            audio_url=None,
            duration=duration or 0.0,
            shot_numbers=[shot_number]
        )
        
        segment_results.append(segment_result)
        
        if segment_result.success:
            logger.info(f"   ✅ Segment {segment_number} 创建成功: {video_url}")
        else:
            logger.warning(f"   ⚠️ Segment {segment_number} 创建失败")
        
        # 🎯 发送进度更新
        # await send_event_func(
        #     event_type=MessageType.VIDEO_SEGMENTS_PROGRESS,
        #     conversation_id=conversation_id,
        #     extra_data={
        #         "completed": segment_number,
        #         "total": total_shots
        #     },
        #     hidden=True,
        #     save_to_db=False
        # )
    
    logger.info(f"🎬 Video-driven 模式处理完成: {len(segment_results)} 个 segments")
    return segment_results


def _build_ordered_audio_segments(
    video_assembly_data: VideoAssemblyData,
    audio_segments_mapping: dict[str, AudioVideoMapping],
) -> List[Tuple[str, AudioVideoMapping]]:
    """
    仅用于 audio-driven：构建按顺序排列的音频片段列表（1:1 覆盖转录，未参与镜头的用占位）。
    若存在 audio_transcription.segments，则按转录顺序输出，缺失的用空 videos + 转录时长占位；
    否则按 shot 顺序输出（仅 mapping 中的项）。Video-driven 不调用此函数。
    """
    if not video_assembly_data.audio_transcription or not video_assembly_data.audio_transcription.segments:
        # 无转录：按原逻辑按 shot 顺序
        sorted_items = sorted(
            audio_segments_mapping.items(),
            key=lambda x: min(x[1].shot_numbers) if x[1].shot_numbers else 999999,
        )
        return sorted_items

    # 按转录顺序：每条转录 segment 一条，缺失的用占位
    segments = video_assembly_data.audio_transcription.segments
    seg_by_uuid = {getattr(s, "uuid", None): s for s in segments if getattr(s, "uuid", None)}
    ordered = []
    for seg in segments:
        uid = getattr(seg, "uuid", None)
        if not uid:
            continue
        duration = getattr(seg, "duration", 0) or 0
        if uid in audio_segments_mapping:
            ordered.append((uid, audio_segments_mapping[uid]))
        else:
            # 未参与任何镜头：占位（videos 为空，后续会生成黑屏）
            ordered.append((uid, AudioVideoMapping(
                videos=[],
                audio_url="",
                duration=duration,
                shot_numbers=[],
            )))
            logger.info(f"📋 补全占位 segment: audio_segment_id={uid[:8]}..., duration={duration:.2f}s（无对应镜头）")
    return ordered


async def _analyze_audio_video_mapping(video_assembly_data: VideoAssemblyData) -> dict[str, AudioVideoMapping]:
    """
    分析音频片段和对应的视频映射关系
    
    🔧 关键逻辑：参考 concatenate_segments_with_music_pieces
    - 从 video_generations 获取 audio_segment_ids（不是从 audio_transcription）
    - 从 music_data 获取音频URL和时长
    - 支持一对多：多个视频片段可以对应同一个音频片段
    
    Returns:
        Dict[audio_segment_id, AudioVideoMapping]
    """
    logger.info("🔍 分析音频视频映射关系...")
    
    audio_segments_mapping = {}  # {audio_segment_id: AudioVideoMapping}
    
    # 🔧 从 video_generations 中获取 audio_segment_ids（参考 concatenate_segments_with_music_pieces）
    for i, video_generation in enumerate(video_assembly_data.video_generations):
        video_url = video_generation.video_url
        shot_number = video_generation.shot_number
        
        # 从 video_generation 获取 audio_segment_ids
        audio_segment_ids = video_generation.audio_segment_ids
        
        if not audio_segment_ids or len(audio_segment_ids) == 0:
            logger.warning(f"⚠️ Shot {shot_number} 没有 audio_segment_ids，跳过")
            continue
        
        # 获取该 shot 的音频片段UUID（通常只有一个）
        audio_segment_id = audio_segment_ids[0]
        
        # 🔧 从 music_data 通过 audio_segment_id 获取音乐信息；duration/audio_url 以 music_version 为准（与 segment 的 create_music 产出一致）
        music_info = video_assembly_data.music_data.get(audio_segment_id)
        if not music_info or 'version' not in music_info:
            logger.warning(f"⚠️ Audio segment {audio_segment_id[:8]}... 没有对应的音乐版本，跳过")
            continue
        
        music_version = music_info['version']
        
        # 按 audio_segment_id 分组（duration/audio_url 使用 music_version，为 segment 音乐的真实时长与 URL）
        if audio_segment_id not in audio_segments_mapping:
            audio_segments_mapping[audio_segment_id] = AudioVideoMapping(
                videos=[],
                audio_url=music_version.music_url if music_version.success else '',
                duration=music_version.duration,
                shot_numbers=[]
            )
            logger.info(
                "[时长诊断] 映射新建 audio_segment_id=%s music_version.duration=%.6f "
                "(segment 合并 target_duration 用此值；与各 shot detailed_shot.duration 之和可能不一致)",
                audio_segment_id,
                float(music_version.duration or 0),
            )
        
        # 🔧 关键修复：即使视频生成失败，也要记录 shot_number（用于追踪关联关系）
        # 但只有成功的视频才加入 videos 列表（用于实际视频处理）
        audio_segments_mapping[audio_segment_id].shot_numbers.append(shot_number)
        
        if video_generation.success and video_url:
            # 成功的视频：加入 videos 列表
            audio_segments_mapping[audio_segment_id].videos.append(video_url)
            logger.info(f"   ✅ Shot {shot_number} → Audio Segment {audio_segment_id[:8]}... (video: {video_url.split('/')[-1][:30]}...)")
        else:
            # 失败的视频：只记录关联关系，不加入 videos 列表
            logger.warning(f"   ⚠️ Shot {shot_number} → Audio Segment {audio_segment_id[:8]}... (视频生成失败，success={video_generation.success}, video_url={video_url})")
    
    logger.info(f"📊 音频片段映射完成，共 {len(audio_segments_mapping)} 个音频片段")
    return audio_segments_mapping


async def _process_segments_parallel(
    sorted_audio_segments: List[Tuple[str, AudioVideoMapping]],
    video_assembly_data: VideoAssemblyData,
    send_event_func: Any,
    max_concurrent: Optional[int] = None
) -> List[VideoSegmentResult]:
    """并行处理所有视频片段（限制并发数量避免FFmpeg资源耗尽）"""
    from ..utils.prompt_utils import get_concurrency_limit, add_random_delay
    
    # 使用配置的并发限制
    if max_concurrent is None:
        max_concurrent = get_concurrency_limit("video_segments_processing")
    
    logger.info(f"🔄 开始并行处理 {len(sorted_audio_segments)} 个视频片段（最大并发: {max_concurrent}）...")
    
    # 记录每个片段的详细信息
    for segment_number, (audio_segment_id, mapping_info) in enumerate(sorted_audio_segments, 1):
        logger.info(f"📋 片段 {segment_number}:")
        logger.info(f"   audio_segment_id: {audio_segment_id}")
        logger.info(f"   视频数量: {len(mapping_info.videos)}")
        logger.info(f"   音频时长: {mapping_info.duration:.2f}s")
        logger.info(f"   shot_numbers: {mapping_info.shot_numbers}")
        
        # 检查对应的 music_generation_id
        music_info = video_assembly_data.music_data.get(audio_segment_id)
        if music_info and music_info.get('music_generation'):
            music_generation_id = music_info['music_generation'].uuid
            logger.info(f"   对应的 music_generation_id: {music_generation_id}")
        else:
            logger.warning(f"   ⚠️ 未找到对应的 music_generation_id，将保持为 None")
    
    # 创建信号量限制并发数量
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def process_segment_with_semaphore(
        segment_number: int,
        audio_segment_id: str,
        mapping_info: AudioVideoMapping
    ) -> VideoSegmentResult:
        """带信号量限制的片段处理"""
        # 添加随机延迟避免并发请求过于集中
        await add_random_delay()
        
        async with semaphore:
            logger.info(f"🎬 开始处理片段 {segment_number}（当前并发槽位已占用）")
            try:
                result = await _process_single_segment(
                    segment_number,
                    audio_segment_id,
                    mapping_info,
                    video_assembly_data,
                    send_event_func
                )
                logger.info(f"✅ 片段 {segment_number} 处理完成（释放并发槽位）")
                return result
            except Exception as e:
                logger.error(f"❌ 片段 {segment_number} 处理异常（释放并发槽位）: {e}")
                raise e
    
    # 创建并行任务
    tasks = []
    for segment_number, (audio_segment_id, mapping_info) in enumerate(sorted_audio_segments, 1):
        task = process_segment_with_semaphore(
            segment_number,
            audio_segment_id,
            mapping_info
        )
        tasks.append(task)
    
    # 并行执行所有任务（受信号量限制）
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 协作式取消：若因取消而中断，及时抛出退出节点（由 task_worker 统一落 CANCELLED 状态）
    await raise_if_cancelled()
    
    # 处理结果（BaseException 兜底：取消时子任务返回的 CancelledError 不是 Exception，避免按成功结果取值崩溃）
    segment_results = []
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            logger.error(f"❌ 片段 {i+1} 处理失败: {result}")
            # 获取对应的 audio_segment_id
            audio_segment_id = sorted_audio_segments[i][0] if i < len(sorted_audio_segments) else f"unknown_segment_{i}"
            
            # 🔧 修复：获取正确的 music_generation_id
            music_generation_id = None
            music_info = video_assembly_data.music_data.get(audio_segment_id)
            if music_info and music_info.get('music_generation'):
                music_generation_id = music_info['music_generation'].uuid
            
            # 创建失败结果
            segment_results.append(VideoSegmentResult(
                segment_number=i + 1,
                success=False,
                error=str(result),
                video_generation_ids=[],  # 空列表，因为处理失败
                music_generation_id=music_generation_id,  # 🔧 修复：使用正确的 music_generation_id 或 None
                # 其他可选字段使用默认值
                narration_ids=[],
                keyframe_ids=[],
                scene_ids=[],
                storyboard_detail_ids=[]
            ))
        else:
            segment_results.append(result)
    
    logger.info(f"✅ 并行处理完成，成功: {sum(1 for r in segment_results if r.success)}/{len(segment_results)}")
    return segment_results


async def process_and_merge_videos(
    video_urls: List[str],
    target_duration: float,
    segment_number: int,
    shot_durations: Optional[List[float]] = None,
) -> str:
    """
    合并多个视频后按 probe 对齐 target_duration：|diff|≤SEGMENT_ALIGN_SKIP_SEC 跳过；≤SEGMENT_LIGHT_ALIGN_MAX_SEC 用
    freeze_or_tail_slow；更大则整片 video_speed_adjust（无黑场垫片）。
    当传入 shot_durations 且与 video_urls 等长时：按每个 shot 的目标时长经 pipeline 截断/对齐后再合并，最后再对齐 segment 总时长。

    Args:
        video_urls: 视频URL列表（仅成功的；失败的已在 mapping 中排除）
        target_duration: 目标时长（秒）
        segment_number: 片段编号（用于日志和文件命名）
        shot_durations: 可选；每个 shot 的目标时长（与 video_urls 一一对应）。若提供且等长，则按 shot 时长截断后合并，不做整段调速。

    Returns:
        merged_video_url: 合并并对齐目标时长后的视频URL

    Raises:
        Exception: 如果视频处理失败
    """
    logger.info(
        f"🎬 开始处理片段 {segment_number}: {len(video_urls)} 个视频, 目标时长 {target_duration:.2f}s "
        f"[时长] target_duration={target_duration!r} 类型={type(target_duration).__name__}"
    )
    run_id = f"seg_{segment_number}_{uuid.uuid4().hex[:8]}"

    use_per_shot = (
        shot_durations is not None
        and len(shot_durations) == len(video_urls)
        and all(d is not None and d > 0 for d in shot_durations)
    )

    if use_per_shot:
        logger.info(
            "[黑屏诊断] 片段 %s: 走 pipeline_segment_process（per-shot 对齐后 concat）",
            segment_number,
        )
        result = await msc.pipeline_segment_process(
            video_urls=video_urls,
            target_durations=shot_durations,
            run_id=run_id,
            normalize=False,
        )
        merged_url = result["result_url"]
        if target_duration > 0:
            merged_url = await _align_segment_video_to_target_duration(
                merged_url,
                target_duration,
                run_id,
                segment_number=segment_number,
                allow_speed_adjust=True,
            )
        logger.info(f"✅ 片段 {segment_number}: media service 处理完成 → {merged_url}")
        return merged_url

    if len(video_urls) == 1:
        logger.info(f"   📹 片段 {segment_number}: 单个视频，按阈值对齐目标时长")
        if target_duration > 0:
            return await _align_segment_video_to_target_duration(
                video_urls[0],
                target_duration,
                run_id,
                segment_number=segment_number,
                allow_speed_adjust=True,
            )
        return video_urls[0]

    # 多个视频：concat → 阈值对齐
    logger.info(f"   🔗 片段 {segment_number}: 合并 {len(video_urls)} 个视频")
    logger.info(
        "[黑屏诊断] 片段 %s: 多镜 concat→整段对齐（非 per-shot pipeline）",
        segment_number,
    )
    concat_result = await msc.video_concat(video_urls, run_id, normalize=False)
    if target_duration > 0:
        return await _align_segment_video_to_target_duration(
            concat_result["result_url"],
            target_duration,
            run_id,
            segment_number=segment_number,
            allow_speed_adjust=True,
        )
    return concat_result["result_url"]


async def merge_and_trim_lipsync_videos(
    video_urls: List[str],
    target_duration: float,
    segment_number: int,
) -> str:
    """合并 lipsync 视频片段并对齐目标时长（不调速：不做整片 speed_adjust）
    
    与 process_and_merge_videos 的区别：
    - 不做 video_speed_adjust，避免破坏唇形同步
    - 使用与成片一致的容差；需调整时用 freeze_or_tail_slow（无黑场垫片）；大偏差仍不调速
    
    Args:
        video_urls: 视频URL列表（1:N 场景下可能有多个）
        target_duration: 精确目标时长（= audio segment duration）
        segment_number: 片段编号
    
    Returns:
        截断后的视频 S3 URL
    """
    logger.info(f"🎭 Lipsync 片段 {segment_number}: {len(video_urls)} 个视频, 目标时长 {target_duration:.2f}s")
    run_id = f"lipsync_{segment_number}_{uuid.uuid4().hex[:8]}"

    if len(video_urls) == 1:
        out_url = await _align_segment_video_to_target_duration(
            video_urls[0],
            target_duration,
            run_id,
            segment_number=segment_number,
            allow_speed_adjust=False,
        )
        try:
            _vi = await msc.video_info(out_url)
            _pd = float(_vi.get("duration", 0))
            logger.info(
                "[时长诊断] Lipsync 片段 %s 对齐后: MSC video_info.duration=%.4f 请求target=%.4f Δ=%+.4f",
                segment_number, _pd, float(target_duration), _pd - float(target_duration),
            )
        except Exception as _e:
            logger.warning("[时长诊断] Lipsync 片段 %s 对齐后 video_info 失败: %s", segment_number, _e)
        return out_url

    for i, url in enumerate(video_urls):
        try:
            _pre = await msc.video_info(url)
            logger.info(
                "[对齐诊断] Lipsync 片段 %s 合并前 shot%d: probe=%.4fs",
                segment_number,
                i + 1,
                float(_pre.get("duration", 0)),
            )
        except Exception as _pre_e:
            logger.warning(
                "[对齐诊断] Lipsync 片段 %s 合并前 shot%d video_info 失败: %s",
                segment_number,
                i + 1,
                _pre_e,
            )
    concat_result = await msc.video_concat(video_urls, run_id, normalize=False)
    try:
        _concat_pre = await msc.video_info(concat_result["result_url"])
        _concat_d = float(_concat_pre.get("duration", 0))
        logger.warning(
            "[对齐诊断] Lipsync 片段 %s: 多镜头(%d) 整段 concat 后 probe=%.4fs → 将对齐到 target=%.4fs"
            "（从尾部裁/延长；多镜头时镜头边界可能错位）",
            segment_number,
            len(video_urls),
            _concat_d,
            float(target_duration),
        )
    except Exception as _concat_e:
        logger.warning(
            "[对齐诊断] Lipsync 片段 %s: concat 后 video_info 失败: %s",
            segment_number,
            _concat_e,
        )
    out_url = await _align_segment_video_to_target_duration(
        concat_result["result_url"],
        target_duration,
        run_id,
        segment_number=segment_number,
        allow_speed_adjust=False,
    )
    try:
        _vi = await msc.video_info(out_url)
        _pd = float(_vi.get("duration", 0))
        logger.info(
            "[时长诊断] Lipsync 片段 %s concat+对齐后: MSC video_info.duration=%.4f 请求target=%.4f Δ=%+.4f",
            segment_number, _pd, float(target_duration), _pd - float(target_duration),
        )
    except Exception as _e:
        logger.warning(
            "[时长诊断] Lipsync 片段 %s concat+对齐后 video_info 失败: %s", segment_number, _e
        )
    return out_url


async def _process_single_segment(
    segment_number: int,
    audio_segment_id: str,
    mapping_info: AudioVideoMapping,
    video_assembly_data: VideoAssemblyData,
    send_event_func: Any
) -> VideoSegmentResult:
    """处理单个视频片段"""
    logger.info(f"🎬 处理片段 {segment_number}: {audio_segment_id}")
    
    # 🔧 数据提取部分：不应该失败，放在 try-catch 外面
    videos = mapping_info.videos
    audio_url = mapping_info.audio_url
    duration = mapping_info.duration
    shot_numbers = mapping_info.shot_numbers
    
    # 从 video_assembly_data 中找到对应的 video_generation_ids 和相关资源
    video_generation_ids = []
    narration_ids = []
    keyframe_ids = []
    scene_ids = []
    storyboard_detail_ids = []
    
    # 根据 audio_segment_id 找到对应的 video_generations（包括失败的）
    for video_gen in video_assembly_data.video_generations:
        if (video_gen.audio_segment_ids and 
            audio_segment_id in video_gen.audio_segment_ids):
            video_generation_ids.append(video_gen.video_generation_id)
    
    # 根据 audio_segment_id 找到对应的 music_generation_id
    music_generation_id = None
    music_info = video_assembly_data.music_data.get(audio_segment_id)
    if music_info and music_info.get('music_generation'):
        music_generation_id = music_info['music_generation'].uuid
        logger.info(f"   🎵 片段 {segment_number}: 找到对应的 music_generation_id: {music_generation_id}")
    else:
        logger.warning(f"   ⚠️ 片段 {segment_number}: 未找到对应的 music_generation_id for audio_segment_id: {audio_segment_id}")
    
    # 从 narrations_data 获取 narration_ids
    for shot_num, narration_data in video_assembly_data.narrations_data.items():
        if ('narration' in narration_data and 
            'audio_segment_ids' in narration_data and
            audio_segment_id in narration_data['audio_segment_ids']):
            narration_ids.append(narration_data['narration'].uuid)
    
    logger.info(f"   📊 片段 {segment_number}: {len(video_generation_ids)} 个video_generations, {len(narration_ids)} 个narrations, {len(videos)} 个可用视频")

    # 🔧 判断是否为 lipsync 片段（根据 video_generation 的 generation_mode）
    from ....models.tool_enums import GenerationMode
    _is_lipsync_segment = False
    for video_gen in video_assembly_data.video_generations:
        if (video_gen.audio_segment_ids and 
            audio_segment_id in video_gen.audio_segment_ids and
            video_gen.generation_mode == GenerationMode.LIPSYNC.value):
            _is_lipsync_segment = True
            break

    # 🔧 构建 per-shot 信息：ALL shots（包括失败的），保持顺序
    all_video_gen_ids: List[Optional[str]] = []
    all_shot_video_urls: List[Optional[str]] = []
    for video_gen in video_assembly_data.video_generations:
        if video_gen.audio_segment_ids and audio_segment_id in video_gen.audio_segment_ids:
            all_video_gen_ids.append(video_gen.video_generation_id)
            if video_gen.success and video_gen.video_url:
                all_shot_video_urls.append(video_gen.video_url)
            else:
                all_shot_video_urls.append(None)

    all_detailed_shot_ids: List[Optional[str]] = []
    valid_gen_ids = [vid for vid in all_video_gen_ids if vid]
    if valid_gen_ids:
        from ....crud.video.video_generation import get_video_generations_by_uuids
        video_gen_dbs = await get_video_generations_by_uuids(valid_gen_ids)
        video_gen_db_map = {vg.uuid: vg for vg in video_gen_dbs}
        all_detailed_shot_ids = [
            getattr(video_gen_db_map.get(vid), "detailed_shot_id", None) if vid and video_gen_db_map.get(vid) else None
            for vid in all_video_gen_ids
        ]

    # 🔧 统一入口：resolve shot durations + per-shot 黑屏占位 + 合并 + 计算状态
    merge_result = await merge_segment_videos(
        segment_number=segment_number,
        all_video_urls=all_shot_video_urls,
        target_duration=duration,
        all_detailed_shot_ids=all_detailed_shot_ids,
        is_lipsync=_is_lipsync_segment,
    )

    # [Layer 3] probe 合并结果实际时长，写 DB 用真实 probe 值而非 target_duration
    # 这样 Assembly 读到的 duration 与实际视频时长一致，消除 target vs actual 误差累积
    actual_duration = float(duration)  # 默认回退到 target（probe 失败时使用）
    if merge_result.success and merge_result.merged_video_url:
        try:
            _merged_vi = await msc.video_info(merge_result.merged_video_url)
            _merged_probe = float(_merged_vi.get("duration", 0))
            _merged_delta = _merged_probe - float(duration)
            actual_duration = _merged_probe
            if abs(_merged_delta) > 0.02:
                logger.warning(
                    "[时长诊断·Layer3] 片段 %s 写DB: target=%.4fs actual_probe=%.4fs"
                    " Δ=%+.4fs → DB写入实测时长（⚠️ 偏差>0.02s，Layer2对齐可能未生效）",
                    segment_number, float(duration), _merged_probe, _merged_delta,
                )
            else:
                logger.info(
                    "[时长诊断·Layer3] 片段 %s 写DB: target=%.4fs actual_probe=%.4fs"
                    " Δ=%+.4fs → DB写入实测时长（误差在0.02s内）",
                    segment_number, float(duration), _merged_probe, _merged_delta,
                )
        except Exception as _probe_e:
            logger.warning(
                "[时长诊断·Layer3] 片段 %s video_info 失败，DB回退写target %.4fs: %s",
                segment_number, float(duration), _probe_e,
            )

    return VideoSegmentResult(
        segment_number=segment_number,
        success=merge_result.success,
        status=merge_result.status,
        error=merge_result.error_msg,
        failed_video_count=merge_result.failed_video_count,
        total_video_count=merge_result.total_video_count,
        video_generation_ids=video_generation_ids,
        narration_ids=narration_ids,
        keyframe_ids=keyframe_ids,
        scene_ids=scene_ids,
        storyboard_detail_ids=storyboard_detail_ids,
        music_generation_id=music_generation_id,
        original_video_urls=videos,
        merged_video_url=merge_result.merged_video_url,
        audio_url=audio_url,
        duration=actual_duration,
        shot_numbers=shot_numbers,
    )


async def _save_video_segments_to_db(
    segment_results: List[VideoSegmentResult],
    video_assembly_data: VideoAssemblyData,
    conversation_id: str,
    thread_id: str,
    run_id: str,
    user_id: str,
    runtime: Runtime[VideoContextSchema]
) -> List[str]:
    """将视频片段结果保存到数据库 - 每个音频片段对应一个video_segment"""
    logger.info("💾 保存视频片段到数据库...")
    
    # ✅ 使用asyncpg CRUD，不需要数据库连接
    video_segment_uuids = []
    
    # 🔧 从数据库获取video_generation信息，用于获取关联ID（包括失败的）
    all_video_generation_ids = [vg.video_generation_id for vg in video_assembly_data.video_generations]
    video_generation_db_map = {}
    
    if all_video_generation_ids:
        from ....crud.video.video_generation import get_video_generations_by_uuids
        video_generation_dbs = await get_video_generations_by_uuids(all_video_generation_ids)
        video_generation_db_map = {vg_db.uuid: vg_db for vg_db in video_generation_dbs}
    
    # 🔧 为每个视频片段创建一个video_segment（无论成功或失败）
    for segment_result in segment_results:
        # VideoSegmentResult 已经包含了所有需要的信息
        segment_video_gen_ids = segment_result.video_generation_ids
        segment_keyframe_ids = segment_result.keyframe_ids or []
        segment_scene_ids = segment_result.scene_ids or []
        segment_storyboard_detail_ids = segment_result.storyboard_detail_ids or []
        segment_narration_ids = segment_result.narration_ids or []
        music_generation_id = segment_result.music_generation_id
        
        # 从数据库获取额外的关联信息（keyframe_ids, scene_ids等）
        for video_gen_id in segment_video_gen_ids:
            if video_gen_id in video_generation_db_map:
                vg_db = video_generation_db_map[video_gen_id]
                # ⭐ 优先使用新字段 keyframe_ids，降级到旧字段 keyframe_id
                if vg_db.keyframe_ids:
                    for kf_id in vg_db.keyframe_ids:
                        if kf_id not in segment_keyframe_ids:
                            segment_keyframe_ids.append(kf_id)
                elif vg_db.keyframe_id and vg_db.keyframe_id not in segment_keyframe_ids:
                    segment_keyframe_ids.append(vg_db.keyframe_id)
                    
                if vg_db.scene_id and vg_db.scene_id not in segment_scene_ids:
                    segment_scene_ids.append(vg_db.scene_id)
                if vg_db.storyboard_detail_id and vg_db.storyboard_detail_id not in segment_storyboard_detail_ids:
                    segment_storyboard_detail_ids.append(vg_db.storyboard_detail_id)
        
        # 创建video_segment记录
        video_segment_db = await create_video_segment(
            conversation_id=conversation_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            story_outline_id=video_assembly_data.story_outline.uuid,  # 修复：使用正确的字段
            music_generation_id=music_generation_id,
            video_generation_ids=segment_video_gen_ids,
            narration_ids=segment_narration_ids,
            keyframe_ids=segment_keyframe_ids,
            scene_ids=segment_scene_ids,
            storyboard_detail_ids=segment_storyboard_detail_ids,
            segment_number=segment_result.segment_number
        )
        
        # 创建该segment的version记录
        await create_video_segment_version(
            video_segment_id=video_segment_db.uuid,
            segment_result=segment_result,
            conversation_id=conversation_id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id
        )
        
        # 为segment_result添加关联信息
        segment_result.video_segment_uuid = video_segment_db.uuid
        
        video_segment_uuids.append(video_segment_db.uuid)
        
        # 区分成功和失败的日志
        if segment_result.success:
            logger.info(f"✅ 创建video_segment: {video_segment_db.uuid}, 包含{len(segment_video_gen_ids)}个视频生成, 片段{segment_result.segment_number}处理成功")
        else:
            logger.warning(f"⚠️ 创建video_segment: {video_segment_db.uuid}, 包含{len(segment_video_gen_ids)}个视频生成, 片段{segment_result.segment_number}处理失败: {segment_result.error}")
    
    # 统计成功和失败的片段
    success_count = sum(1 for r in segment_results if r.success)
    failed_count = len(segment_results) - success_count
    logger.info(f"💾 数据库保存完成: {len(video_segment_uuids)} 个video_segment (成功: {success_count}, 失败: {failed_count})")
    
    return video_segment_uuids


# ==================== 辅助函数 ====================

async def _adjust_video_speed_to_duration(
    video_path: str,
    target_duration: float,
    temp_dir: str,
    segment_number: int
) -> str:
    # DEPRECATED: replaced by media service — use msc.video_speed_adjust() instead
    """调整视频速度匹配目标时长（不混合音频）
    
    Args:
        video_path: 视频本地路径
        target_duration: 目标时长
        temp_dir: 临时目录
        segment_number: 片段编号
        
    Returns:
        处理后的视频本地路径
    """
    try:
        # 1. 获取视频实际时长
        probe_cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            video_path
        ]
        probe_result = await asyncio.create_subprocess_exec(
            *probe_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(probe_result.communicate(), timeout=30)
        except asyncio.TimeoutError:
            probe_result.kill()
            await probe_result.wait()
            raise RuntimeError(f"❌ ffprobe获取视频时长超时")
        
        if probe_result.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise RuntimeError(f"❌ ffprobe失败: {error_msg}")
        
        video_duration = float(stdout.decode().strip())
        speed_factor = video_duration / target_duration
        
        logger.info(f"🎵 视频速度调整 - 片段{segment_number}:")
        logger.info(f"   视频时长: {video_duration:.2f}s")
        logger.info(f"   目标时长: {target_duration:.2f}s")
        logger.info(f"   速度因子: {speed_factor:.3f}x")
        
        # 2. 生成输出路径
        output_path = os.path.join(temp_dir, f"speed_adjusted_segment_{segment_number}_{uuid.uuid4().hex[:8]}.mp4")
        
        from ..utils.prompt_utils import get_concurrency_limit
        ffmpeg_threads = get_concurrency_limit("ffmpeg_threads")
        # 3. FFmpeg命令只调整视频速度（不处理音频）- 限制线程数避免多任务时 CPU 占满
        cmd = [
            'ffmpeg', '-y',
            '-threads', str(ffmpeg_threads),
            '-i', video_path,
            '-filter:v', f'setpts={1/speed_factor:.6f}*PTS',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',  # 最大编码速度（体积略大）
            '-crf', '25',
            '-movflags', '+faststart',
            '-an',  # 移除音频轨道
            output_path
        ]
        
        # 修复超时计算：根据文件大小和目标时长动态调整
        file_size_mb = os.path.getsize(video_path) / (1024 * 1024) if os.path.exists(video_path) else 0
        # 基础超时30秒，大文件每MB增加2秒，目标时长每秒增加1秒
        timeout = min(120, max(30, int(30 + file_size_mb * 2 + target_duration)))
        
        logger.info(f"🔧 执行FFmpeg速度调整:")
        logger.info(f"   完整命令: {' '.join(cmd)}")
        logger.info(f"   超时设置: {timeout}秒 (目标时长: {target_duration:.2f}s)")
        logger.info(f"   输入文件: {video_path}")
        logger.info(f"   输出文件: {output_path}")
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        start_time = asyncio.get_event_loop().time()
        logger.info(f"⏳ FFmpeg进程启动，PID: {process.pid}，开始时间: {start_time:.2f}")
        
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            end_time = asyncio.get_event_loop().time()
            execution_time = end_time - start_time
            logger.info(f"✅ FFmpeg进程完成，耗时: {execution_time:.2f}秒，返回码: {process.returncode}")
            
            if stderr:
                stderr_text = stderr.decode()
                if stderr_text.strip():
                    logger.info(f"📄 FFmpeg stderr输出: {stderr_text[:500]}...")
                    
        except asyncio.TimeoutError:
            end_time = asyncio.get_event_loop().time()
            execution_time = end_time - start_time
            logger.error(f"❌ FFmpeg进程超时！")
            logger.error(f"   PID: {process.pid}")
            logger.error(f"   超时设置: {timeout}秒")
            logger.error(f"   实际运行时间: {execution_time:.2f}秒")
            logger.error(f"   输入文件大小: {os.path.getsize(video_path) if os.path.exists(video_path) else 'N/A'} bytes")
            logger.error(f"   完整命令: {' '.join(cmd)}")
            
            try:
                process.kill()
                await process.wait()
                logger.info(f"🔪 FFmpeg进程已强制终止")
            except Exception as kill_error:
                logger.error(f"❌ 强制终止FFmpeg进程失败: {kill_error}")
            
            raise RuntimeError(f"FFmpeg处理超时（{timeout}秒，实际运行{execution_time:.2f}秒）")
        
        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise RuntimeError(f"FFmpeg速度调整失败: {error_msg}")
        
        if os.path.exists(output_path):
            output_size = os.path.getsize(output_path)
            logger.info(f"✅ 视频速度调整成功: {output_path} (大小: {output_size} bytes)")
            return output_path
        else:
            raise Exception("视频速度调整失败，输出文件不存在")
        
    except Exception as e:
        logger.error(f"❌ 视频速度调整失败: {e}")
        raise



async def _concatenate_videos_in_temp(
    video_paths: List[str], 
    temp_dir: str, 
    filename_prefix: str
) -> str:
    # DEPRECATED: replaced by media service — use msc.video_concat() instead
    """在临时目录中合并视频文件
    
    Args:
        video_paths: 本地视频文件路径列表
        temp_dir: 临时目录
        filename_prefix: 输出文件名前缀
        
    Returns:
        合并后的视频本地路径
    """
    import asyncio
    import subprocess
    
    try:
        output_filename = f"{filename_prefix}_{uuid.uuid4().hex[:8]}.mp4"
        output_path = os.path.join(temp_dir, output_filename)
        
        if len(video_paths) == 1:
            # 只有一个视频，直接复制（在线程中执行，避免阻塞事件循环）
            import shutil
            await asyncio.to_thread(shutil.copy2, video_paths[0], output_path)
            logger.info(f"✅ 单个视频，直接复制: {output_path}")
            return output_path
        
        # 多个视频，使用FFmpeg合并
        logger.info(f"🔗 合并 {len(video_paths)} 个视频文件")
        
        # 创建文件列表（异步写，不阻塞事件循环）
        filelist_path = os.path.join(temp_dir, f"filelist_{uuid.uuid4().hex[:8]}.txt")
        import aiofiles
        lines = []
        for video_path in video_paths:
            abs_path = os.path.abspath(video_path)
            escaped_path = abs_path.replace("'", "'\"'\"'")
            lines.append(f"file '{escaped_path}'\n")
        async with aiofiles.open(filelist_path, 'w', encoding='utf-8') as f:
            await f.writelines(lines)

        try:
            # 使用FFmpeg拼接（使用异步subprocess，参考video_assembly的实现）
            cmd = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', filelist_path,
                '-c', 'copy',
                output_path
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
            
            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                raise Exception(f"FFmpeg拼接失败: {error_msg}")
            
            logger.info(f"✅ 视频拼接成功: {output_path}")
            
        finally:
            # 清理临时文件
            if os.path.exists(filelist_path):
                await asyncio.to_thread(os.remove, filelist_path)
        
        if os.path.exists(output_path):
            return output_path
        else:
            raise Exception("视频合并失败，输出文件不存在")
    
    except Exception as e:
        logger.error(f"❌ 视频合并失败: {e}")
        raise
