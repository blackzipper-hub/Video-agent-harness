"""
LangSmith 可观测性：tag / metadata / phase 集中管理。

为什么需要：视频 / keyframe / 角色等生成的关键参数（镜头号、audio_url、duration 等）
走 runtime context（``agent.ainvoke(context=...)``），不进 LangSmith trace 的 inputs，
所以 UI 上既看不到、也无法按镜头/阶段筛选。

这里统一定义三组枚举 + 一个 ``build_ls_run_config()``，把这些信息以 metadata / tag 附到
「本次 invoke 新建的 run」上（通过 RunnableConfig 传播，而不是改当前 run）。这样每次
调用各自带自己的 shot_number 等字段，互不覆盖，且可在 LangSmith 直接查看与筛选。

扩展方式（保持通用、简单）：
- 新增阶段 → 往 ``LSPhase`` 加一项；
- 新增可筛字段 → 往 ``LSMeta`` 加一项；
- 新增粗分类 → 往 ``LSTag`` 加一项。
调用方只需在 ``log_context`` 里用这些枚举的 ``.value`` 当 key，无需关心注入细节。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional


class LSPhase(str, Enum):
    """LLM / 工具调用阶段。既写入 ``metadata['phase']``，也作为一个 tag。"""

    VIDEO_TOOL_EXEC = "video_tool_execution"
    VIDEO_PROMPT_GEN = "video_prompt_generation"
    VIDEO_PROMPT_EVAL = "video_prompt_eval_fix"
    KEYFRAME_TOOL_EXEC = "keyframe_tool_execution"
    KEYFRAME_PROMPT_GEN = "keyframe_batch_prompts"
    KEYFRAME_PROMPT_EVAL = "keyframe_prompt_eval_fix"
    CHARACTER_IMAGE = "main_character_image"
    CHARACTER_IMAGE_MATCH = "main_character_image_matching"
    MUSIC_GEN = "music_generation"
    OUTLINE_GEN = "outline_generation"
    SCENE_GEN = "scene_generation"
    REGENERATE_VIDEO = "regenerate_video"
    REGENERATE_KEYFRAME = "regenerate_keyframe"
    REGENERATE_CHARACTER = "regenerate_character"


class LSTag(str, Enum):
    """粗分类标签（固定枚举，便于 LangSmith UI 的 tags 过滤）。"""

    VIDEO = "video"
    KEYFRAME = "keyframe"
    CHARACTER = "character"
    MUSIC = "music"
    OUTLINE = "outline"
    SCENE = "scene"
    LIPSYNC = "lipsync"
    REGENERATE = "regenerate"


class LSMeta(str, Enum):
    """可筛选的 metadata key（统一命名，避免各处写散字符串）。"""

    PHASE = "phase"
    SHOT_NUMBER = "shot_number"
    BATCH_INDEX = "batch_index"
    RUN_ID = "run_id"
    THREAD_ID = "thread_id"
    CONVERSATION_ID = "conversation_id"
    GENERATION_MODE = "generation_mode"
    VIDEO_TOOL = "video_generation_tool"
    HAS_AUDIO = "has_audio"
    AUDIO_URL_TAIL = "audio_url_tail"
    HAS_END_IMAGE = "has_end_image"
    REF_IMAGE_COUNT = "ref_image_count"
    DURATION = "duration"


# log_context 里用这个 key 携带额外粗分类 tag（list[LSTag|str]）；其值不会写入 metadata。
LS_TAGS_KEY = "ls_tags"


def _is_scalar(v: Any) -> bool:
    return isinstance(v, (bool, int, float, str))


def extract_context_metadata(context: Any) -> dict[str, Any]:
    """从 create_agent 的 context（``VideoGenerationContext`` / ``ImageGenerationContext`` 等）
    抽取可观测字段。这些参数走 runtime context，不在 trace inputs 里，需主动提取。
    只取轻量标量；URL 仅保留文件名尾段，避免 metadata 过大。
    """
    if context is None:
        return {}
    meta: dict[str, Any] = {}
    audio_url = getattr(context, "audio_url", None)
    if audio_url:
        meta[LSMeta.HAS_AUDIO.value] = True
        meta[LSMeta.AUDIO_URL_TAIL.value] = str(audio_url).split("/")[-1][:80]
    duration = getattr(context, "duration", None)
    if duration is not None:
        meta[LSMeta.DURATION.value] = duration
    if getattr(context, "end_image_url", None):
        meta[LSMeta.HAS_END_IMAGE.value] = True
    ref_urls = getattr(context, "reference_image_urls", None) or getattr(
        context, "character_ref_image_urls", None
    )
    if ref_urls:
        meta[LSMeta.REF_IMAGE_COUNT.value] = len(ref_urls)
    return meta


def build_ls_run_config(
    log_context: Mapping[str, Any] | None = None,
    invoke_context: Any | None = None,
    base_config: Mapping[str, Any] | None = None,
) -> Optional[dict]:
    """把 ``log_context`` + ``invoke_context`` 合成 RunnableConfig 的 metadata / tags，
    附到「本次 ainvoke 新建的 run」上（区别于改当前 run 的 ``get_current_run_tree``，
    后者在并发场景会把不同镜头的字段写到同一个父 run 上互相覆盖）。

    - ``log_context`` 里的标量逐个进 metadata；其中 ``phase`` 同时作为 tag。
    - ``log_context[LS_TAGS_KEY]``（list）里的项作为额外粗分类 tag，不进 metadata。
    - ``invoke_context`` 经 ``extract_context_metadata`` 补 audio / duration 等 runtime 参数。
    - 有 ``shot_number`` 时自动设 ``run_name=f'{phase}_shot_{n}'``，方便 UI 区分。
    - 合并而非覆盖 ``base_config``（保留调用方已设的 recursion_limit / callbacks 等）。

    返回合并后的 dict；若无任何可加信息，返回 ``base_config`` 的副本或 ``None``。
    """
    meta: dict[str, Any] = {}
    if log_context:
        for k, v in log_context.items():
            if k == LS_TAGS_KEY:
                continue
            if v is not None and _is_scalar(v):
                meta[str(k)] = v
    meta.update(extract_context_metadata(invoke_context))

    extra_tags: list[str] = []
    if log_context:
        for t in log_context.get(LS_TAGS_KEY) or []:
            tv = t.value if isinstance(t, Enum) else str(t)
            if tv:
                extra_tags.append(tv)

    if not meta and not extra_tags:
        return dict(base_config) if base_config else None

    cfg: dict[str, Any] = dict(base_config or {})

    if meta:
        merged_meta = dict(cfg.get("metadata") or {})
        merged_meta.update(meta)
        cfg["metadata"] = merged_meta

    tags = list(cfg.get("tags") or [])
    phase = meta.get(LSMeta.PHASE.value)
    if phase:
        phase = str(phase)
        if phase not in tags:
            tags.append(phase)
    for tv in extra_tags:
        if tv not in tags:
            tags.append(tv)
    if tags:
        cfg["tags"] = tags

    if "run_name" not in cfg and phase:
        sn = meta.get(LSMeta.SHOT_NUMBER.value)
        cfg["run_name"] = f"{phase}_shot_{sn}" if sn is not None else phase

    return cfg
