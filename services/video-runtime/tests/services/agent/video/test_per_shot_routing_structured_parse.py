"""CREATE_AGENT 返回 `structured_response` 时须能解析（与仅 `parsed` 的旧假设不同）。"""

from app.schemas.per_shot_routing import (
    PerShotGenerationRoutingOutput,
    PerShotRoutingItem,
    parse_per_shot_generation_routing_agent_result,
)


def test_parse_structured_response_key():
    model = PerShotGenerationRoutingOutput(
        shots=[
            PerShotRoutingItem(
                shot_number=1,
                image_generation_tool="auto",
                normal_video_tool="auto",
                lipsync_video_tool="auto",
            )
        ]
    )
    assert parse_per_shot_generation_routing_agent_result({"structured_response": model}) is model


def test_parse_parsed_key_still_works():
    model = PerShotGenerationRoutingOutput(shots=[PerShotRoutingItem(shot_number=1)])
    assert parse_per_shot_generation_routing_agent_result({"parsed": model}) is model


def test_parse_dict_validates():
    d = {"shots": [{"shot_number": 1, "image_generation_tool": "auto", "normal_video_tool": "auto"}]}
    out = parse_per_shot_generation_routing_agent_result({"structured_response": d})
    assert out is not None
    assert len(out.shots) == 1
