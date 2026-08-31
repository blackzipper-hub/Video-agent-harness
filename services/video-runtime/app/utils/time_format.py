"""
时间格式化工具

统一项目内"秒数 → 人类可读字符串"的展示格式。
与 Gemini transcribe 输出契约 (`MM:SS.mmm`) 保持一致，便于
LLM 端的时间引用可以直接 copy-paste 到下游 prompt。

底层存储 / 计算保持 float 秒（见 AudioSegment）；本工具只用于
**对外/对 LLM 展示层**。
"""
from __future__ import annotations

from typing import Optional, Union


def format_sec_to_mmss(sec: Optional[Union[int, float]]) -> str:
    """秒数 → MM:SS.mmm（如 80.5 → "1:20.500"、12.25 → "0:12.250"）。

    - None / 非法 / 负数：统一返回 "0:00.000"，避免 prompt 出现 ``None``
    - 不补零分钟（与 hybrid `_seconds_to_mmss` 历史输出一致），但秒位补零
      到两位整数 + 三位毫秒，方便与 Gemini 输出对照
    """
    try:
        s = float(sec) if sec is not None else 0.0
    except (TypeError, ValueError):
        s = 0.0
    if s < 0 or s != s:  # 负数 / NaN
        s = 0.0
    minutes = int(s // 60)
    seconds = s - minutes * 60
    return f"{minutes}:{seconds:06.3f}"


def format_sec_range(start: Optional[Union[int, float]], end: Optional[Union[int, float]]) -> str:
    """秒数区间 → "MM:SS.mmm-MM:SS.mmm"（专给 prompt 展示用）。"""
    return f"{format_sec_to_mmss(start)}-{format_sec_to_mmss(end)}"
