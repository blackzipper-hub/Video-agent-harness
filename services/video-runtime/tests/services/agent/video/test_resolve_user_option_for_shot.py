"""`resolve_user_option_for_shot`：per-shot `generation_routing` 与用户 `UserOption` 合并（真实逻辑单测，不调 LLM）。"""

import pytest

from app.models.user_options import ImageGenerationTool, UserOption, VideoGenerationTool
from app.services.agent.video.per_shot_user_option_resolve import (
    resolve_user_option_for_shot,
)


class _Shot:
    def __init__(self, routing):
        self.generation_routing = routing


@pytest.fixture
def base_user_option():
    return UserOption(
        image_generation_tool=ImageGenerationTool.NANO_BANANA,
        video_generation_tool=VideoGenerationTool.POLLO_SEEDANCE,
        lipsync_video_tool=VideoGenerationTool.LTX_2_3,
    )


def test_no_routing_returns_original(base_user_option):
    out = resolve_user_option_for_shot(base_user_option, _Shot(None), "all")
    assert out is base_user_option


def test_empty_routing_dict_returns_original(base_user_option):
    out = resolve_user_option_for_shot(base_user_option, _Shot({}), "all")
    assert out is base_user_option


def test_overrides_lipsync_tool_only_when_global_lipsync_auto():
    base = UserOption(
        image_generation_tool=ImageGenerationTool.NANO_BANANA,
        video_generation_tool=VideoGenerationTool.POLLO_SEEDANCE,
        lipsync_video_tool=VideoGenerationTool.AUTO,
    )
    shot = _Shot({"lipsync_video_tool": "wan_2_5"})
    out = resolve_user_option_for_shot(base, shot, "all")
    assert out is not base
    assert out.lipsync_video_tool == VideoGenerationTool.WAN_2_5


def test_does_not_override_lipsync_when_global_fixed():
    base = UserOption(
        image_generation_tool=ImageGenerationTool.NANO_BANANA,
        video_generation_tool=VideoGenerationTool.POLLO_SEEDANCE,
        lipsync_video_tool=VideoGenerationTool.LTX_2_3,
    )
    shot = _Shot({"lipsync_video_tool": "wan_2_5"})
    out = resolve_user_option_for_shot(base, shot, "all")
    assert out is base
    assert out.lipsync_video_tool == VideoGenerationTool.LTX_2_3


def test_overrides_normal_video_tool_only_when_global_video_auto():
    base = UserOption(
        image_generation_tool=ImageGenerationTool.NANO_BANANA,
        video_generation_tool=VideoGenerationTool.AUTO,
        lipsync_video_tool=VideoGenerationTool.LTX_2_3,
    )
    shot = _Shot({"normal_video_tool": "kling_v3_std"})
    out = resolve_user_option_for_shot(base, shot, "all")
    assert out.video_generation_tool == VideoGenerationTool.KLING_V3_STD


def test_does_not_override_normal_video_when_global_fixed(base_user_option):
    shot = _Shot({"normal_video_tool": "kling_v3_std"})
    out = resolve_user_option_for_shot(base_user_option, shot, "all")
    assert out is base_user_option
    assert out.video_generation_tool == VideoGenerationTool.POLLO_SEEDANCE


def test_overrides_image_tool_only_when_global_image_auto():
    base = UserOption(
        image_generation_tool=ImageGenerationTool.AUTO,
        video_generation_tool=VideoGenerationTool.POLLO_SEEDANCE,
        lipsync_video_tool=VideoGenerationTool.LTX_2_3,
    )
    shot = _Shot({"image_generation_tool": "seedream"})
    out = resolve_user_option_for_shot(base, shot, "all")
    assert out.image_generation_tool == ImageGenerationTool.SEEDREAM


def test_does_not_override_image_when_global_fixed(base_user_option):
    shot = _Shot({"image_generation_tool": "seedream"})
    out = resolve_user_option_for_shot(base_user_option, shot, "all")
    assert out is base_user_option
    assert out.image_generation_tool == ImageGenerationTool.NANO_BANANA


def test_routing_aspect_filters():
    shot = _Shot(
        {
            "lipsync_video_tool": "wan_2_5",
            "normal_video_tool": "kling_v3_std",
            "image_generation_tool": "seedream",
        }
    )
    base_img = UserOption(
        image_generation_tool=ImageGenerationTool.AUTO,
        video_generation_tool=VideoGenerationTool.POLLO_SEEDANCE,
        lipsync_video_tool=VideoGenerationTool.LTX_2_3,
    )
    out_img = resolve_user_option_for_shot(base_img, shot, "image")
    assert out_img.image_generation_tool == ImageGenerationTool.SEEDREAM
    assert out_img.video_generation_tool == base_img.video_generation_tool
    assert out_img.lipsync_video_tool == base_img.lipsync_video_tool

    base_v = UserOption(
        image_generation_tool=ImageGenerationTool.NANO_BANANA,
        video_generation_tool=VideoGenerationTool.AUTO,
        lipsync_video_tool=VideoGenerationTool.LTX_2_3,
    )
    out_v = resolve_user_option_for_shot(base_v, shot, "normal_video")
    assert out_v.video_generation_tool == VideoGenerationTool.KLING_V3_STD
    assert out_v.image_generation_tool == base_v.image_generation_tool
