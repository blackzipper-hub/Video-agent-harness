"""版本再生 ``regenerate_strategy``：关键帧 / 角色 / 视频 **各用独立 StrEnum**（HTTP / MCP 字段值字符串一致处可同名）。

命名规则：**prompt_** = 以整段 ``custom_prompt`` 为主；**instruction_** = 以自然语言 ``instruction`` 为主。

- **prompt_regenerate**：用 ``custom_prompt`` 走完整出图或 I2V（``instruction`` 可选作附言）。
- **instruction_regenerate**：仅 **关键帧** — 用 ``instruction`` 走完整出图（基底读 DB 当前 t2i）；角色与视频不提供该策略。
- **instruction_merge_prompt**：只把 ``instruction`` 融进全文 prompt 并 **仅通过 API 返回**（不写新版本行），不出新图 / 不跑 I2V。
- **instruction_edit_image**：仅 **关键帧与角色** — ``instruction`` + 当前成图走 I2I；**视频枚举不包含此项**。
- **角色 / 视频** 的完整出图：使用 ``prompt_regenerate``，可只传 ``instruction``（基底读 DB 当前 prompt / motion）或传 ``custom_prompt``，或两者兼有。
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Optional


class KeyframeRegenerateStrategy(StrEnum):
    PROMPT_REGENERATE = "prompt_regenerate"
    INSTRUCTION_REGENERATE = "instruction_regenerate"
    INSTRUCTION_MERGE_PROMPT = "instruction_merge_prompt"
    INSTRUCTION_EDIT_IMAGE = "instruction_edit_image"


class CharacterRegenerateStrategy(StrEnum):
    PROMPT_REGENERATE = "prompt_regenerate"
    INSTRUCTION_MERGE_PROMPT = "instruction_merge_prompt"
    INSTRUCTION_EDIT_IMAGE = "instruction_edit_image"


class VideoRegenerateStrategy(StrEnum):
    PROMPT_REGENERATE = "prompt_regenerate"
    INSTRUCTION_MERGE_PROMPT = "instruction_merge_prompt"


def parse_keyframe_regenerate_strategy(raw: Optional[Any]) -> KeyframeRegenerateStrategy:
    """未传或空串 → ``prompt_regenerate``；否则按 ``KeyframeRegenerateStrategy`` 解析（非法值抛 ``ValueError``）。"""
    if raw is None:
        return KeyframeRegenerateStrategy.PROMPT_REGENERATE
    if isinstance(raw, KeyframeRegenerateStrategy):
        return raw
    s = str(raw).strip()
    if not s:
        return KeyframeRegenerateStrategy.PROMPT_REGENERATE
    return KeyframeRegenerateStrategy(s)


def parse_character_regenerate_strategy(raw: Optional[Any]) -> CharacterRegenerateStrategy:
    """未传或空串 → ``prompt_regenerate``；否则按 ``CharacterRegenerateStrategy`` 解析。"""
    if raw is None:
        return CharacterRegenerateStrategy.PROMPT_REGENERATE
    if isinstance(raw, CharacterRegenerateStrategy):
        return raw
    s = str(raw).strip()
    if not s:
        return CharacterRegenerateStrategy.PROMPT_REGENERATE
    return CharacterRegenerateStrategy(s)


def parse_video_regenerate_strategy(raw: Optional[Any]) -> VideoRegenerateStrategy:
    """未传或空串 → ``prompt_regenerate``；否则按 ``VideoRegenerateStrategy`` 解析。"""
    if raw is None:
        return VideoRegenerateStrategy.PROMPT_REGENERATE
    if isinstance(raw, VideoRegenerateStrategy):
        return raw
    s = str(raw).strip()
    if not s:
        return VideoRegenerateStrategy.PROMPT_REGENERATE
    return VideoRegenerateStrategy(s)
