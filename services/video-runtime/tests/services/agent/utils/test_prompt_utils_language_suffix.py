"""
测试 apply_language_suffix_to_system_message_in_messages 对多模态 content（list）的保留。

关键：HumanMessage.content 为 list（text + image_url 块）时，不得被 str(content) 转成字符串，
否则 LLM 无法识别图片，且 _url_from_human_message 等解析会失败。

运行（conda env cuti-video-local）：
  cd Cuti-VideoAgent && python -m pytest tests/services/agent/utils/test_prompt_utils_language_suffix.py -v -s
"""
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.services.agent.utils.prompt_utils import (
    apply_language_suffix_to_system_message_in_messages,
)


def test_apply_language_suffix_preserves_multimodal_content_list():
    """apply_language_suffix 后 HumanMessage.content 仍为 list，且可解析出 image_url。"""
    content_list = [
        {"type": "text", "text": "请为以下 4 个连续镜头批量生成连贯的关键帧提示词。\n<batch_shots_info>\n"},
        {"type": "image_url", "image_url": {"url": "https://cdn.example.com/img1.webp"}},
        {"type": "image_url", "image_url": {"url": "https://cdn.example.com/img2.webp"}},
        {"type": "text", "text": "\n\n<shot number=\"29\">...</shot>\n</batch_shots_info>"},
    ]
    messages = [HumanMessage(content=content_list)]
    apply_language_suffix_to_system_message_in_messages(messages, detected_language="zh")

    assert len(messages) == 1
    msg = messages[0]
    assert isinstance(msg, HumanMessage)
    assert isinstance(msg.content, list), "content 必须保持为 list，不能变成 str"
    assert len(msg.content) == 4
    assert msg.content[0].get("type") == "text"
    assert msg.content[1].get("type") == "image_url"
    assert msg.content[2].get("type") == "image_url"
    assert msg.content[3].get("type") == "text"

    urls = [
        block["image_url"]["url"]
        for block in msg.content
        if block.get("type") == "image_url"
    ]
    assert len(urls) == 2
    assert urls[0] == "https://cdn.example.com/img1.webp"
    assert urls[1] == "https://cdn.example.com/img2.webp"

    # HumanMessage multimodal content must stay a list; language suffix is System-only.
    last_text = msg.content[3]["text"]
    assert "<language_requirements>" not in last_text
    assert last_text.endswith("</batch_shots_info>")


def test_apply_language_suffix_string_content_unchanged_behavior():
    """HumanMessage string content is left unchanged."""
    messages = [HumanMessage(content="Generate keyframe prompts.")]
    apply_language_suffix_to_system_message_in_messages(messages, detected_language="en")

    assert len(messages) == 1
    assert isinstance(messages[0].content, str)
    assert messages[0].content == "Generate keyframe prompts."


def test_apply_language_suffix_system_message_list():
    """SystemMessage 的 content 为 list 时也保持 list 结构。"""
    content_list = [
        {"type": "text", "text": "You are a helpful assistant."},
    ]
    messages = [SystemMessage(content=content_list)]
    apply_language_suffix_to_system_message_in_messages(messages, detected_language="ja")

    assert len(messages) == 1
    assert isinstance(messages[0].content, list)
    assert messages[0].content[0]["type"] == "text"
    assert "ja" in messages[0].content[0]["text"]
