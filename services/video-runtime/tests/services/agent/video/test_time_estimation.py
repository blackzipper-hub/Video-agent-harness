"""
VideoAgent 阶段耗时推算单元测试

- estimate_video_agent_time 返回各 step 秒数 + 总计
- attach_step_seconds_to_path 注入 est_seconds；bgm_parallel 时 music 不计入串行 total
- get_time_estimate_for_gate 剩余耗时随 gate 推进而单调减少
- timing_profile 查表函数：分辨率/时长/turbo 单调性
"""
import pytest

from app.models.user_options import UserOption
from app.models.tool_enums import ContentCategory
from app.agent_config import timing_profile as tp
from app.services.agent.video.time_estimation import (
    estimate_video_agent_time,
    attach_step_seconds_to_path,
    get_time_estimate_for_gate,
    TimeEstimateResult,
)


def test_estimate_returns_all_steps_and_positive_total():
    opt = UserOption.default()
    est = estimate_video_agent_time(opt, duration_sec=30, include_music=True)
    assert isinstance(est, TimeEstimateResult)
    for sid in ("music", "analysis", "story_style", "visual", "scenes", "storyboards", "shots", "final"):
        assert sid in est.step_seconds
        assert est.step_seconds[sid] >= 0
    assert est.total_est_seconds > 0
    assert est.shot_count >= 1


def test_include_music_false_drops_music_step():
    opt = UserOption.default()
    est = estimate_video_agent_time(opt, duration_sec=30, include_music=False)
    assert "music" not in est.step_seconds


def test_longer_video_costs_more_time():
    opt = UserOption.default()
    short = estimate_video_agent_time(opt, duration_sec=15, include_music=False)
    long = estimate_video_agent_time(opt, duration_sec=60, include_music=False)
    # 更长 → 更多镜头 → storyboards + shots 更久
    assert long.shot_count >= short.shot_count
    assert long.total_est_seconds >= short.total_est_seconds


def test_attach_step_seconds_injects_est_and_total():
    opt = UserOption.default()
    est = estimate_video_agent_time(opt, duration_sec=30, include_music=True)
    path = [
        {"id": "music", "label_key": "workflow.node.music"},
        {"id": "analysis", "label_key": "x"},
        {"id": "storyboards", "label_key": "x"},
        {"id": "shots", "label_key": "x"},
        {"id": "final", "label_key": "x"},
    ]
    out = attach_step_seconds_to_path(path, est, music_mode="suno_then_transcribe")
    assert all("est_seconds" in e for e in out["path"])
    assert out["total_est_seconds"] > 0
    assert out["estimate_confidence"] in ("low", "medium", "high")


def test_bgm_parallel_music_not_in_serial_total():
    opt = UserOption.default()
    est = estimate_video_agent_time(opt, duration_sec=30, include_music=True)
    path = [
        {"id": "analysis", "label_key": "x"},
        {"id": "music", "label_key": "x"},
        {"id": "storyboards", "label_key": "x"},
    ]
    serial = attach_step_seconds_to_path(path, est, music_mode="suno_then_transcribe")
    parallel = attach_step_seconds_to_path(path, est, music_mode="bgm_parallel")
    music_secs = est.step_seconds.get("music", 0)
    assert serial["total_est_seconds"] - parallel["total_est_seconds"] == music_secs


@pytest.mark.asyncio
async def test_gate_remaining_monotonic_decreasing():
    class _UID:
        user_option = UserOption.default()
        audio_files = None

    state = {"user_input_data": _UID(), "scene_uuids": [], "shot_uuids": []}
    after_character = await get_time_estimate_for_gate(state, "after_character")
    after_kf = await get_time_estimate_for_gate(state, "after_keyframe_reflection")
    after_shots = await get_time_estimate_for_gate(state, "after_shots")
    assert after_character["remaining_est_seconds"] >= after_kf["remaining_est_seconds"]
    assert after_kf["remaining_est_seconds"] >= after_shots["remaining_est_seconds"]


def test_timing_profile_video_unit_monotonic():
    # 分辨率越高越久
    s480 = tp.get_video_unit_seconds(tool_type="seedance_v1", resolution="480p", clip_duration_sec=5)
    s1080 = tp.get_video_unit_seconds(tool_type="seedance_v1", resolution="1080p", clip_duration_sec=5)
    assert s1080 > s480
    # 成片时长越长越久
    short = tp.get_video_unit_seconds(tool_type="seedance_v1", resolution="720p", clip_duration_sec=4)
    long = tp.get_video_unit_seconds(tool_type="seedance_v1", resolution="720p", clip_duration_sec=12)
    assert long > short
    # turbo 更快（兜底路径：未入表工具按命名打折）
    normal = tp.get_video_unit_seconds(tool_type="seedance_2_i2v", resolution="720p", clip_duration_sec=5)
    turbo = tp.get_video_unit_seconds(tool_type="seedance_2_i2v_turbo", resolution="720p", clip_duration_sec=5)
    assert turbo < normal


def test_timing_profile_video_unit_per_tool_table():
    """按 ToolType 查表：实测基准体现工具间真实差异（与 _PRICING_CONFIG 同构）。"""
    from app.models.tool_enums import ToolType
    # 已入表工具走实测基准（720p,5s）
    v1 = tp.get_video_unit_seconds(tool_type=ToolType.SEEDANCE_V1_PRO_FAST, resolution="720p", clip_duration_sec=5)
    sora_pro = tp.get_video_unit_seconds(tool_type=ToolType.SORA_2_PRO, resolution="720p", clip_duration_sec=5)
    assert v1 == pytest.approx(35.0, abs=1.0)
    assert sora_pro == pytest.approx(165.0, abs=1.0)
    # Sora 2 Pro 明显慢于 Seedance v1（单一公式无法表达的工具间差异）
    assert sora_pro > v1
    # 未入表工具（排队污染的 Seedance 2.0 非 fast）走兜底基准
    s2 = tp.get_video_unit_seconds(tool_type=ToolType.SEEDANCE_2_I2V, resolution="720p", clip_duration_sec=5)
    assert s2 == pytest.approx(tp._VIDEO_FALLBACK_REF_SEC, abs=1.0)


def test_timing_profile_node_to_step_covers_user_steps():
    steps = set(tp.NODE_TO_STEP.values())
    for sid in ("music", "analysis", "story_style", "visual", "scenes", "storyboards", "narration", "shots", "final"):
        assert sid in steps


def test_product_launch_includes_narration_step_seconds():
    opt = UserOption.default()
    opt.content_category = ContentCategory.PRODUCT_LAUNCH
    est = estimate_video_agent_time(opt, duration_sec=30, include_music=False)
    assert "narration" in est.step_seconds
    assert est.step_seconds["narration"] > 0
    assert "music" not in est.step_seconds
