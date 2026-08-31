"""Keyframe regenerate 复用旧 reference_image_urls 路径的单元测试。

覆盖三处接入点（显式 `reuse_ref_image_urls` 参数传递，而非 ContextVar）：
  1) resolve_use_venue_ref_image：reuse 模式（参数非 None）强制返回 True，保证 character labels 与槽位对齐
  2) _get_per_shot_ref_image_urls：reuse 模式直接返回 reuse_urls，不再调 get_character_ref_images
  3) resolve_keyframe_prompt_reference_images：reuse 模式直接把 reuse_urls 转 RefImageInfo

入口接入点（regenerate_single_keyframe / _regenerate_keyframe_version 的 PROMPT_REGENERATE 分支）
由集成测试覆盖，这里仅测纯函数行为。
"""
from __future__ import annotations

import pytest

from app.models.user_options import ImageGenerationTool, UserOption
from app.services.agent.video.keyframe_generation_service import (
    KeyframePromptResult,
    RefImageRole,
    _get_per_shot_ref_image_urls,
    resolve_keyframe_prompt_reference_images,
    resolve_use_venue_ref_image,
)


def _make_shot(shot_number: int = 1, character_ids=None):
    """构造最小可用 DetailedShot 实例。"""
    from app.models.video_state import DetailedShot
    return DetailedShot(
        uuid="shot-uuid",
        shot_number=shot_number,
        shot_type="medium",
        camera_angle="wide",
        duration=5.0,
        description="d",
        visual_notes="v",
        character_action="a",
        transition="cut",
        is_bridge=False,
        character_ids=character_ids or [],
        scene_id="scene-uuid",
        storyboard_detail_id="sb-uuid",
        run_id="run-uuid",
    )


# ---------------------------------------------------------------------------
# resolve_use_venue_ref_image
# ---------------------------------------------------------------------------


def test_resolve_use_venue_ref_image_returns_true_in_reuse_mode():
    """reuse 模式（reuse_ref_image_urls 非 None）：不论 user_option / 模型能力，都强制 True。"""
    # nano_banana 模型本来 supports_venue_ref_image=False
    uo = UserOption(image_generation_tool=ImageGenerationTool.NANO_BANANA)
    assert resolve_use_venue_ref_image(uo, reuse_ref_image_urls=["https://x/1.webp"]) is True


def test_resolve_use_venue_ref_image_gpt2_returns_true_when_no_reuse():
    """非 reuse 模式 + gpt_image_2：按模型能力表 → True。"""
    uo = UserOption(image_generation_tool=ImageGenerationTool.GPT_IMAGE_2)
    assert resolve_use_venue_ref_image(uo) is True
    assert resolve_use_venue_ref_image(uo, reuse_ref_image_urls=None) is True


def test_resolve_use_venue_ref_image_nano_returns_false_when_no_reuse():
    """非 reuse 模式 + nano_banana：按模型能力表 → False（保持原有"巨人问题"防护）。"""
    uo = UserOption(image_generation_tool=ImageGenerationTool.NANO_BANANA)
    assert resolve_use_venue_ref_image(uo) is False
    assert resolve_use_venue_ref_image(uo, reuse_ref_image_urls=None) is False


def test_resolve_use_venue_ref_image_seedream_returns_false_when_no_reuse():
    """非 reuse + seedream：按模型能力表 → False（与 nano 同样不支持 location 图）。"""
    uo = UserOption(image_generation_tool=ImageGenerationTool.SEEDREAM)
    assert resolve_use_venue_ref_image(uo) is False


def test_resolve_use_venue_ref_image_reuse_overrides_nano_capability():
    """reuse 模式优先级高于模型能力表：即便模型本来不支持 location，仍强制 True。"""
    uo = UserOption(image_generation_tool=ImageGenerationTool.NANO_BANANA)
    assert resolve_use_venue_ref_image(
        uo, reuse_ref_image_urls=["https://x/1.webp", "https://x/2.webp"]
    ) is True


def test_resolve_use_venue_ref_image_reuse_empty_list_still_triggers_reuse():
    """reuse_ref_image_urls=[]（空 list 但非 None）也视为 reuse 模式 → True，与 set([]) 行为一致。
    注：入口处会把空 list 归一化为 None，但纯函数自身不做归一化。"""
    uo = UserOption(image_generation_tool=ImageGenerationTool.NANO_BANANA)
    assert resolve_use_venue_ref_image(uo, reuse_ref_image_urls=[]) is True


# ---------------------------------------------------------------------------
# _get_per_shot_ref_image_urls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_per_shot_ref_image_urls_reuse_mode_skips_match(monkeypatch):
    """reuse 模式：返回 [reuse_urls] * len(shots_batch)，不调 get_character_ref_images。"""
    import app.services.agent.video.keyframe_generation_service as svc

    call_count = {"n": 0}

    async def _fail(*args, **kwargs):
        call_count["n"] += 1
        raise AssertionError("reuse 模式下不应调用 get_character_ref_images")

    monkeypatch.setattr(svc, "get_character_ref_images", _fail)

    shots = [_make_shot(1), _make_shot(2)]
    reuse = ["https://x/a.webp", "https://x/b.webp", "https://x/c.webp"]
    out = await _get_per_shot_ref_image_urls(
        shots_batch=shots,
        character_images={},
        user_option=UserOption(),
        user_id="u",
        generate_last_frame=False,
        first_frame_images=None,
        reuse_ref_image_urls=reuse,
    )

    assert len(out) == 2
    assert out[0] == reuse
    assert out[1] == reuse
    assert out[0] is not out[1], "应是独立 list，避免下游修改互相污染"
    assert call_count["n"] == 0


@pytest.mark.asyncio
async def test_get_per_shot_ref_image_urls_no_reuse_calls_match(monkeypatch):
    """非 reuse 模式（参数 None / 缺省）：仍调 get_character_ref_images 做匹配。"""
    import app.services.agent.video.keyframe_generation_service as svc

    called = {"n": 0}

    async def _fake_match(**kwargs):
        called["n"] += 1
        return ["https://m/x.webp"]

    monkeypatch.setattr(svc, "get_character_ref_images", _fake_match)

    shots = [_make_shot(1, character_ids=["c1"])]
    out = await _get_per_shot_ref_image_urls(
        shots_batch=shots,
        character_images={},
        user_option=UserOption(),
        user_id="u",
        generate_last_frame=False,
        first_frame_images=None,
    )
    assert called["n"] == 1
    assert out == [["https://m/x.webp"]]


# ---------------------------------------------------------------------------
# resolve_keyframe_prompt_reference_images
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_keyframe_prompt_reference_images_reuse_mode_uses_param_urls(monkeypatch):
    """reuse 模式：prompt.ref_images 为空时直接按 reuse_ref_image_urls 填，不调 get_character_ref_images。"""
    import app.services.agent.video.keyframe_generation_service as svc

    async def _fail(*args, **kwargs):
        raise AssertionError("reuse 模式下不应调用 get_character_ref_images")

    monkeypatch.setattr(svc, "get_character_ref_images", _fail)

    shot = _make_shot(7, character_ids=["c1", "loc1"])
    prompt = KeyframePromptResult(shot_number=7, t2i_prompt="hello")
    reuse = [
        "https://x/char.webp",
        "https://x/location.webp",
    ]
    out = await resolve_keyframe_prompt_reference_images(
        shots_batch=[shot],
        character_images={},
        prompts=[prompt],
        user_option=UserOption(),
        user_id="u",
        reuse_ref_image_urls=reuse,
    )

    assert len(out) == 1
    rp = out[0]
    assert len(rp.ref_images) == 2
    assert [ri.url for ri in rp.ref_images] == reuse
    assert all(ri.role == RefImageRole.CHARACTER for ri in rp.ref_images)
    assert rp.final_prompt == "hello"


@pytest.mark.asyncio
async def test_resolve_keyframe_prompt_reference_images_reuse_with_sheet_tag(monkeypatch):
    """reuse 模式：保留 Reference Sheet tag，has_reference_sheet=True 时加 PREFIX。"""
    import app.services.agent.video.keyframe_generation_service as svc

    async def _fail(*args, **kwargs):
        raise AssertionError("reuse 模式下不应调用 get_character_ref_images")

    monkeypatch.setattr(svc, "get_character_ref_images", _fail)

    shot = _make_shot(3, character_ids=["c1"])
    prompt = KeyframePromptResult(shot_number=3, t2i_prompt="base")
    sheet_url = svc.REFERENCE_SHEET_URL_TAG + "https://x/sheet.webp"
    reuse = ["https://x/char1.webp", sheet_url]
    out = await resolve_keyframe_prompt_reference_images(
        shots_batch=[shot],
        character_images={},
        prompts=[prompt],
        user_option=UserOption(),
        user_id="u",
        reuse_ref_image_urls=reuse,
    )

    rp = out[0]
    assert len(rp.ref_images) == 2
    assert rp.ref_images[0].role == RefImageRole.CHARACTER
    assert rp.ref_images[1].role == RefImageRole.SHEET
    assert rp.ref_images[1].url == "https://x/sheet.webp", "tag 前缀应已 strip"
    assert rp.has_reference_sheet is True
    assert rp.final_prompt == prompt.t2i_prompt


@pytest.mark.asyncio
async def test_resolve_keyframe_prompt_reference_images_no_reuse_calls_match(monkeypatch):
    """非 reuse 模式（参数 None / 缺省）：走 get_character_ref_images 重新匹配。"""
    import app.services.agent.video.keyframe_generation_service as svc

    called = {"n": 0}

    async def _fake_match(**kwargs):
        called["n"] += 1
        return ["https://m/refreshed.webp"]

    monkeypatch.setattr(svc, "get_character_ref_images", _fake_match)

    shot = _make_shot(5, character_ids=["c1"])
    prompt = KeyframePromptResult(shot_number=5, t2i_prompt="x")
    out = await resolve_keyframe_prompt_reference_images(
        shots_batch=[shot],
        character_images={},
        prompts=[prompt],
        user_option=UserOption(),
        user_id="u",
    )
    assert called["n"] == 1
    assert len(out[0].ref_images) == 1
    assert out[0].ref_images[0].url == "https://m/refreshed.webp"


@pytest.mark.asyncio
async def test_resolve_keyframe_prompt_reference_images_pipeline_path_unaffected_by_reuse_param():
    """Pipeline 路径：prompt.ref_images 已填好时，即便传入 reuse_ref_image_urls 也不应覆盖（continue 优先生效）。"""
    from app.services.agent.video.keyframe_generation_service import RefImageInfo

    shot = _make_shot(2, character_ids=["c1"])
    pipeline_ref = RefImageInfo(url="https://pipeline/x.webp", role=RefImageRole.CHARACTER)
    prompt = KeyframePromptResult(shot_number=2, t2i_prompt="t", ref_images=[pipeline_ref])

    out = await resolve_keyframe_prompt_reference_images(
        shots_batch=[shot],
        character_images={},
        prompts=[prompt],
        user_option=UserOption(),
        user_id="u",
        reuse_ref_image_urls=["https://reuse/y.webp"],
    )

    assert len(out[0].ref_images) == 1
    assert out[0].ref_images[0].url == "https://pipeline/x.webp", \
        "pipeline 路径已填好的 ref_images 不应被 reuse_ref_image_urls 覆盖"
