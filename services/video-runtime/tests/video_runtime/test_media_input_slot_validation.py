import pytest

from app.video_runtime.builtin_plugins.media_inputs import resolve_media_parameters


def test_text_blueprint_can_describe_future_image_slots():
    parameters = {"prompt": "下一阶段提示词应声明 @图片1 控制角色身份"}

    resolve_media_parameters(parameters, [], validate_prompt_slots=False)

    assert parameters["prompt"].startswith("下一阶段")


def test_media_provider_still_rejects_a_missing_image_slot():
    parameters = {"prompt": "使用 @图片1 生成视频"}

    with pytest.raises(ValueError, match="missing images slot 1"):
        resolve_media_parameters(parameters, [], validate_prompt_slots=True)
