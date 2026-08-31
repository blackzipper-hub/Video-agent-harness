"""reference-to-video 开关与 reference_images 组装。"""
from app.models.user_options import (
    UserOption,
    VideoGenerationTool,
    should_use_reference_to_video,
    build_reference_to_video_images,
)
from app.services.agent.video import agent_video_constants


def test_should_use_reference_to_video_default_off_for_sd2():
    uo = UserOption(video_generation_tool=VideoGenerationTool.SEEDANCE_2_I2V)
    assert should_use_reference_to_video(uo) is False


def test_should_use_reference_to_video_on_when_constant_enabled(monkeypatch):
    monkeypatch.setattr(agent_video_constants, "USE_REFERENCE_TO_VIDEO", True)
    uo = UserOption(video_generation_tool=VideoGenerationTool.SEEDANCE_2_I2V)
    assert should_use_reference_to_video(uo) is True


def test_should_use_reference_to_video_false_for_non_capable_tool():
    uo = UserOption(video_generation_tool=VideoGenerationTool.POLLO_SEEDANCE)
    assert should_use_reference_to_video(uo) is False


def test_build_reference_to_video_images_order_and_cap():
    refs = build_reference_to_video_images(
        "https://example.com/start.jpg",
        "https://example.com/end.jpg",
        ["https://example.com/char1.jpg", "https://example.com/char2.jpg"],
        agent_video_constants.MAX_REFERENCE_IMAGES_FOR_VIDEO,
    )
    assert refs == [
        "https://example.com/start.jpg",
        "https://example.com/end.jpg",
        "https://example.com/char1.jpg",
    ]
