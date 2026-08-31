"""patch_tool_metrics_from_last_tool_message 必须从 artifact 回填 preview_video_url。"""
from types import SimpleNamespace

from app.models.image_result import VideoGenerationResult, VideoProvider
from app.services.agent.utils.message_utils import (
    patch_tool_metrics_from_last_tool_message,
)


def test_preview_video_url_copied_from_artifact_when_llm_omitted():
    sr = VideoGenerationResult.success_result(
        video_url="https://cdn.example/muted.mp4",
        generated_prompt="x",
        provider=VideoProvider.WAVESPEED.value,
    )
    assert sr.preview_video_url is None

    artifact = VideoGenerationResult.success_result(
        video_url="https://cdn.example/muted.mp4",
        generated_prompt="x",
        provider=VideoProvider.WAVESPEED.value,
    ).model_copy(
        update={
            "preview_video_url": "https://cdn.example/with_audio.mp4",
            "tool_duration_sec": 12.5,
            "tool_cost": 0.1,
        }
    )
    messages = [SimpleNamespace(type="tool", artifact=artifact)]

    patched = patch_tool_metrics_from_last_tool_message(messages, sr)
    assert patched.preview_video_url == "https://cdn.example/with_audio.mp4"
    assert patched.tool_duration_sec == 12.5
    assert patched.tool_cost == 0.1
