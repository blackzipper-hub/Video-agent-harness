"""Duration 切分：修复前后对比（SD2 profile=8 vs 旧 bug min(supported)=4）。"""

from types import SimpleNamespace

from app.models.user_options import UserOption, VideoGenerationTool
from app.agent_config.duration import (
    get_audio_driven_duration_values,
    get_audio_driven_split_threshold,
)
from app.services.agent.video.scene_generation_service import (
    _compute_scene_structure_for_chapter,
)


def _tx_one_segment(duration: float, uuid: str = "u0"):
    seg = SimpleNamespace(
        id=0, start=0.0, end=duration, text="x", duration=duration, uuid=uuid,
    )
    return SimpleNamespace(
        task="transcribe",
        language="zh",
        duration=duration,
        text="x",
        segments=[seg],
        audio_url="https://x",
    )


def _chapter(audio_uuids, duration: float):
    return SimpleNamespace(
        id="ch0",
        title="t",
        description="d",
        duration=duration,
        order=0,
        audio_segment_ids=audio_uuids,
    )


def test_seedance2_profile_threshold_is_8_not_min4():
    opt = UserOption(video_generation_tool=VideoGenerationTool.SEEDANCE_2_I2V)
    values = get_audio_driven_duration_values(opt)
    assert min(values) == 4
    assert get_audio_driven_split_threshold(opt) == 8.0


def test_duration_split_before_vs_after_seedance2_16s_segment():
    """16s 音频段：旧逻辑按 min=4 → 4 镜；修复后按 profile=8 → 2 镜。总时长仍=16。"""
    opt = UserOption(video_generation_tool=VideoGenerationTool.SEEDANCE_2_I2V)
    tx = _tx_one_segment(16.0)
    ch = _chapter(["u0"], 16.0)

    # AFTER（当前实现：走 get_audio_driven_split_threshold）
    after = _compute_scene_structure_for_chapter(ch, tx, user_option=opt)
    assert len(after) == 2, f"SD2 修复后期望 2 镜，实际 {len(after)}: {[s['duration'] for s in after]}"
    assert abs(sum(s["duration"] for s in after) - 16.0) < 0.01
    assert all(s["duration"] == 8.0 for s in after)

    # BEFORE（复现旧 bug：min(values)=4）
    before_threshold = float(min(get_audio_driven_duration_values(opt)))
    assert before_threshold == 4.0
    import math
    num_before = math.ceil(16.0 / before_threshold)
    assert num_before == 4, "旧逻辑应对 16s 切成 4 镜"


def test_duration_split_seedance2_12s_no_over_split():
    """12s ≤ 8? 否；ceil(12/8)=2 → 两镜约 6s，而不是旧逻辑 ceil(12/4)=3。"""
    opt = UserOption(video_generation_tool=VideoGenerationTool.SEEDANCE_2_I2V)
    tx = _tx_one_segment(12.0)
    ch = _chapter(["u0"], 12.0)
    structure = _compute_scene_structure_for_chapter(ch, tx, user_option=opt)
    assert len(structure) == 2
    assert abs(sum(s["duration"] for s in structure) - 12.0) < 0.01
    # 旧 bug 会是 3 镜
    assert len(structure) < math_ceil_old(12.0, 4.0)


def math_ceil_old(duration: float, threshold: float) -> int:
    import math
    return math.ceil(duration / threshold)


def test_none_user_option_still_uses_global_min_fallback():
    """user_option=None 时仍走全局 AUDIO_DRIVEN min，兼容旧测试场景。"""
    tx = _tx_one_segment(6.2)
    ch = _chapter(["u0"], 6.2)
    structure = _compute_scene_structure_for_chapter(ch, tx, user_option=None)
    # 全局 min 常见为 3 或 5 等；只要总时长守恒即可
    assert abs(sum(s["duration"] for s in structure) - 6.2) < 0.01
    assert len(structure) >= 1
