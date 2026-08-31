"""
VideoAgent 积分推算单元测试

- scene:shot = 1:1
- lipsync 走 Wan 2.6/2.5 定价（非 LIPSYNC_2_PRO）
- estimate_video_agent_credits 返回 CreditEstimateResult
- get_credit_estimate_for_gate 按暂停阶段返回剩余预估
"""
import pytest

from app.models.user_options import UserOption
from app.services.agent.video.cost_estimation import (
    estimate_scene_and_shot_count_from_initial_params,
    estimate_scene_and_shot_count,
    estimate_video_agent_credits,
    get_credit_estimate_for_gate,
    CreditEstimateResult,
    DEFAULT_SHOTS_PER_SCENE,
)
from app.agent_config.duration import (
    get_audio_driven_duration_values,
    get_audio_driven_split_threshold,
    get_video_driven_duration_values,
    get_video_driven_split_threshold,
)


def test_default_shots_per_scene_is_one():
    """scene:shot 必须是 1:1"""
    assert DEFAULT_SHOTS_PER_SCENE == 1


def test_split_threshold_audio_driven_default():
    """Audio-driven 拆分阈值：默认 POLLO profile = 8s（避免链 min=3 碎切）。"""
    opt = UserOption.default()
    assert get_audio_driven_split_threshold(opt, None) == 8.0


def test_split_threshold_video_driven():
    """Video-driven 规划 prefer = profile/8，禁止落到 API min=3。"""
    opt = UserOption.default()
    values = get_video_driven_duration_values(opt)
    thr = get_video_driven_split_threshold(opt)
    assert thr == 8.0
    assert thr >= float(min(values))  # prefer must not be below api min accidentally as "target"


def test_estimate_scene_and_shot_count_from_initial_params_audio_driven_total():
    """Audio-driven 仅总时长：场景数 = ceil(audio_duration_sec / threshold), shots = scenes"""
    opt = UserOption.default()
    scenes, shots = estimate_scene_and_shot_count_from_initial_params(
        user_option=opt,
        is_audio_driven=True,
        audio_duration_sec=30.0,
        duration_sec=None,
        content_category=None,
    )
    assert scenes >= 1
    assert shots == scenes * DEFAULT_SHOTS_PER_SCENE


def test_estimate_scene_and_shot_count_from_initial_params_audio_driven_segments():
    """Audio-driven 按片段时长"""
    opt = UserOption.default()
    segments = [5.0, 5.0, 12.0]
    scenes, shots = estimate_scene_and_shot_count_from_initial_params(
        user_option=opt,
        is_audio_driven=True,
        audio_duration_sec=None,
        audio_segment_durations=segments,
        content_category=None,
    )
    assert scenes >= 3
    assert shots == scenes * DEFAULT_SHOTS_PER_SCENE


def test_estimate_scene_and_shot_count_from_initial_params_video_driven():
    """Video-driven 用 duration_sec / threshold"""
    opt = UserOption.default()
    scenes, shots = estimate_scene_and_shot_count_from_initial_params(
        user_option=opt,
        is_audio_driven=False,
        duration_sec=60,
        content_category=None,
    )
    assert scenes >= 1
    assert shots == scenes * DEFAULT_SHOTS_PER_SCENE


def test_estimate_scene_and_shot_count_known():
    """已知 scene_count/shot_count 时直接返回"""
    opt = UserOption.default()
    s, sh = estimate_scene_and_shot_count(
        user_option=opt,
        scene_count=10,
        shot_count=10,
    )
    assert s == 10 and sh == 10


def test_estimate_scene_and_shot_count_initial_audio():
    """无已知数量时用初始参数（audio-driven）"""
    opt = UserOption.default()
    s, sh = estimate_scene_and_shot_count(
        user_option=opt,
        scene_count=None,
        shot_count=None,
        duration_sec=30,
        is_audio_driven=True,
        audio_duration_sec=30.0,
    )
    assert s >= 1 and sh == s * DEFAULT_SHOTS_PER_SCENE


def test_estimate_video_agent_credits_returns_pydantic():
    """estimate_video_agent_credits 返回 CreditEstimateResult（Pydantic）"""
    opt = UserOption.default()
    result = estimate_video_agent_credits(
        user_option=opt,
        shot_count=10,
        scene_count=10,
    )
    assert isinstance(result, CreditEstimateResult)
    assert result.breakdown.scene_count == 10
    assert result.breakdown.shot_count == 10
    assert result.total_credits_estimate == result.keyframe_credits_estimate + result.video_credits_estimate
    assert result.total_credits_estimate > 0


def test_estimate_video_agent_credits_initial_params():
    """初始参数（无 scene/shot）用 is_audio_driven + duration 估算"""
    opt = UserOption.default()
    result = estimate_video_agent_credits(
        user_option=opt,
        scene_count=None,
        shot_count=None,
        duration_sec=24,
        is_audio_driven=False,
    )
    assert result.breakdown.scene_count >= 1
    assert result.breakdown.shot_count >= 1
    assert result.total_credits_estimate > 0


def test_estimate_video_agent_credits_lipsync_uses_wan_pricing():
    """lipsync 镜头走 Wan 2.6 定价，不走 LIPSYNC_2_PRO ($0.08/s)"""
    opt = UserOption.default()
    opt.lipsync_coverage = 100
    result = estimate_video_agent_credits(
        user_option=opt,
        shot_count=1,
        scene_count=1,
    )
    # Shot duration estimate follows planning prefer (profile 8s), not API min=3
    assert result.breakdown.estimated_shot_duration_sec == 8
    # Wan 2.6 @ ~8s 1080p is higher than old 3s estimate, but still finite
    assert 50 <= result.video_credits_estimate < 200


@pytest.mark.asyncio
async def test_gate_after_character_returns_full():
    """暂停1（after_character）返回 keyframe + video 全量预估"""
    state = {"user_input_data": _make_user_input_data(), "scene_uuids": ["s1"] * 5, "shot_uuids": ["sh1"] * 5}
    est = await get_credit_estimate_for_gate(state, "after_character")
    assert est["remaining_credits_estimate"] == est["keyframe_credits_estimate"] + est["video_credits_estimate"]
    assert est["remaining_credits_estimate"] > 0


@pytest.mark.asyncio
async def test_gate_after_keyframe_reflection_returns_video_only():
    """暂停2（after_keyframe_reflection）只返回 video 预估"""
    state = {"user_input_data": _make_user_input_data(), "scene_uuids": ["s1"] * 5, "shot_uuids": ["sh1"] * 5}
    est = await get_credit_estimate_for_gate(state, "after_keyframe_reflection")
    assert est["keyframe_credits_estimate"] == 0
    assert est["remaining_credits_estimate"] == est["video_credits_estimate"]
    assert est["remaining_credits_estimate"] > 0


@pytest.mark.asyncio
async def test_gate_after_shots_returns_zero():
    """暂停3（after_shots）返回 0"""
    state = {"user_input_data": _make_user_input_data(), "scene_uuids": ["s1"] * 5, "shot_uuids": ["sh1"] * 5}
    est = await get_credit_estimate_for_gate(state, "after_shots")
    assert est["remaining_credits_estimate"] == 0


def _make_user_input_data():
    """构造一个最小可用的 user_input_data mock"""
    from app.models.video_state import UserInput
    return UserInput(user_input="test", user_option=UserOption.default())
