"""Mustache 渲染分支测试：video_audio_transcription.mustache

防止后续误删 / 重排 granularity 三个分支块，导致 PHRASE / SENTENCE / BEAT 输出
互相串入或全部消失。

直接通过 langchain ChatPromptTemplate 渲染（与生产路径一致），不引入 mcp/redis 等重依赖。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from langchain_core.prompts import ChatPromptTemplate

_TPL_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "video"
    / "music_generation"
    / "video_audio_transcription.mustache"
)


def _build_template() -> ChatPromptTemplate:
    """重现 prompts/prompt_loader.load_local_mustache_template 的核心逻辑，
    避免拉取 prompts.__init__ 的全量依赖图（redis / account_router ...）。"""
    content = _TPL_PATH.read_text(encoding="utf-8")
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            content = parts[2]
    if "## System Message" in content and "## Human Message" in content:
        _, rest = content.split("## System Message", 1)
        sys_c, hum_c = rest.split("## Human Message", 1)
    elif "## Human Message" in content:
        sys_c, hum_c = "", content.split("## Human Message", 1)[1]
    else:
        sys_c, hum_c = "", content
    # 生产环境会替换 {{AUDIO:audio_content}} 为多模态锚点，单测里替成纯文本即可
    hum_c = hum_c.replace("{{AUDIO:audio_content}}", "[AUDIO_PLACEHOLDER]")
    msgs = []
    if sys_c.strip():
        msgs.append(("system", sys_c.strip()))
    msgs.append(("human", hum_c.strip()))
    return ChatPromptTemplate.from_messages(msgs, template_format="mustache")


@pytest.fixture(scope="module")
def template() -> ChatPromptTemplate:
    return _build_template()


_BASE = {
    "has_user_input": False,
    "user_input": "",
    "has_generated_lyrics": False,
    "generated_lyrics": "",
    "has_suno_alignment_context": False,
    "suno_alignment_context": "",
    "has_any_reference": False,
    "actual_duration": "60.00",
    "has_lipsync_duration_constraint": False,
    "max_segment_duration": None,
}


async def _render(template: ChatPromptTemplate, **overrides) -> str:
    data = {**_BASE, **overrides}
    msgs = (await template.ainvoke(data)).messages
    return "\n".join(str(m.content) for m in msgs)


async def test_phrase_mode_no_override_blocks(template):
    text = await _render(
        template,
        granularity_phrase=True,
        granularity_sentence=False,
        granularity_beat=False,
    )
    assert "sentence（按完整语义句切）" not in text
    assert "4/4 小节切" not in text
    assert "phrase 模式" in text
    assert "完整语义句" not in text


async def test_sentence_mode_emits_only_sentence_blocks(template):
    text = await _render(
        template,
        granularity_phrase=False,
        granularity_sentence=True,
        granularity_beat=False,
    )
    assert "sentence（按完整语义句切）" in text
    assert "Make your music video with Cutie" in text
    assert "4/4 小节切" not in text
    assert "phrase 模式" not in text
    assert "不要把多句合并" not in text
    assert "比 phrase 模式更多" not in text
    # 提醒块（人类消息侧）
    assert "**sentence**" in text


async def test_beat_mode_emits_only_beat_blocks(template):
    text = await _render(
        template,
        granularity_phrase=False,
        granularity_sentence=False,
        granularity_beat=True,
    )
    assert "4/4 小节切" in text
    assert "sentence（按完整语义句切）" not in text
    assert "**beat**" in text


async def test_lipsync_constraint_block_still_works_in_sentence_mode(template):
    """sentence override 块中引用 max_segment_duration，与 lipsync 约束块互不冲突。"""
    text = await _render(
        template,
        granularity_phrase=False,
        granularity_sentence=True,
        granularity_beat=False,
        has_lipsync_duration_constraint=True,
        max_segment_duration=10,
    )
    assert "10" in text
    assert "sentence（按完整语义句切）" in text
