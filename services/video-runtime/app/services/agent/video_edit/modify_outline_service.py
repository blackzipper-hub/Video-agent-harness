"""modify_outline 核心逻辑：LLM 选择逻辑字段，后端负责 style 组 DB 展开与章节联动。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set

from ..video.regenerate.instruction_merge_prompt import instruction_merge_to_full_prompt

logger = logging.getLogger(__name__)

VALID_OUTLINE_FIELDS = frozenset({"title", "description", "theme", "key_message", "style", "chapters"})

# 改这些字段时，后端自动融合同步各章节文本（用户视角：故事/风格/章节是一体的）
_CHAPTER_CASCADE_FIELDS = frozenset({"style", "description", "theme"})

_TEXT_FIELD_ASSET_KIND = {
    "title": "故事大纲标题",
    "description": "故事大纲整体描述",
    "theme": "故事主题",
    "key_message": "故事核心信息",
    "style_guide": "视觉风格指南",
    "style_preferences": "风格偏好短标签",
    "chapter_title": "故事章节标题",
    "chapter_description": "故事章节描述",
}


@dataclass
class ChapterUpdate:
    uuid: str
    order: int
    title: Optional[str] = None
    description: Optional[str] = None


@dataclass
class ModifyOutlineResult:
    success: bool
    fields_changed: List[str] = field(default_factory=list)
    message: str = ""
    previews: Dict[str, str] = field(default_factory=dict)
    outline_updates: Dict[str, Any] = field(default_factory=dict)
    analysis_updates: Dict[str, Any] = field(default_factory=dict)
    chapter_updates: List[ChapterUpdate] = field(default_factory=list)


def normalize_outline_fields(fields: Optional[Sequence[str]]) -> List[str]:
    """规范化 LLM 传入的逻辑字段列表，去重并保持顺序。"""
    if not fields:
        return []
    normalized: List[str] = []
    for item in fields:
        key = (item or "").strip()
        if key in VALID_OUTLINE_FIELDS and key not in normalized:
            normalized.append(key)
    return normalized


def should_sync_chapters(requested_fields: Sequence[str]) -> bool:
    """是否应同步更新各章节（显式 chapters 或叙事/风格级字段变更）。"""
    requested = set(normalize_outline_fields(requested_fields))
    if "chapters" in requested:
        return True
    return bool(requested & _CHAPTER_CASCADE_FIELDS)


def instruction_implies_style_change(instruction: str) -> bool:
    """修改说明是否明显在改视觉风格（用于校验 fields 是否漏传 style）。"""
    text = (instruction or "").strip()
    if not text:
        return False
    markers = ("风格", "画风", "片风", "视觉风格", "美学")
    return any(marker in text for marker in markers)


# 显式整换风动词（"改成赛博朋克"）——最强信号
_STYLE_SWAP_MARKERS = (
    "改成", "换成", "换画风", "换风格", "改画风", "整体风格", "全片风格",
    "整换风", "风格改成", "风格换成",
)
# 流派名——仅在"要变成它"时才算整换风；单纯提及（如"保持XX风不变"）不算
_STYLE_GENRE_MARKERS = (
    "恐怖", "惊悚", "赛博", "朋克", "末日", "未来科技", "科幻", "暗黑",
    "哥特", "蒸汽波", "黑色电影", "霓虹", "废土",
)
# 保留意图——出现这些且无显式整换风动词，说明是想"维持现状只微调"，不是整换风
_STYLE_PRESERVE_MARKERS = (
    "不变", "别动", "别的别", "维持", "保持", "原有", "不要动", "不用改", "不改", "保留",
)
_VISUAL_TWEAK_MARKERS = (
    "色调", "调色", "稍微", "一点", "暗一点", "亮一点", "冷一点", "暖一点",
    "更冷", "更暖", "微调", "压暗", "提亮",
)


def instruction_implies_full_style_swap(instruction: str) -> bool:
    """整风格切换（改成XX风/换画风/命名流派），通常牵动 title/theme/description。"""
    text = (instruction or "").strip()
    if not text:
        return False
    # 显式整换风动词优先（即使句中也说"标题保持不变"，仍是整换风）
    if any(m in text for m in _STYLE_SWAP_MARKERS):
        return True
    # 仅提到某流派用于"保留/不变"（如"保持末日废土风格不变"）→ 不是整换风
    if any(m in text for m in _STYLE_PRESERVE_MARKERS):
        return False
    if instruction_implies_style_change(text) and any(m in text for m in _STYLE_GENRE_MARKERS):
        return True
    return False


def instruction_implies_visual_tweak_only(instruction: str) -> bool:
    """微调视觉（色调/明暗）且不像整换风：允许只改 style。"""
    text = (instruction or "").strip()
    if not text:
        return False
    # 只在"显式整换风动词"时才排除微调；不再依赖脆弱的流派词启发式
    # （否则"色调冷一点，保持末日废土不变"会因提到流派被误判为整换风）
    if any(m in text for m in _STYLE_SWAP_MARKERS):
        return False
    return any(m in text for m in _VISUAL_TWEAK_MARKERS)


def validate_outline_fields_for_instruction(
    instruction: str,
    fields: Sequence[str],
) -> Optional[str]:
    """校验 fields 与修改说明是否匹配；不匹配时返回可重试提示（不静默补字段）。"""
    normalized = normalize_outline_fields(fields)
    if not normalized:
        return None

    if "style" not in normalized and (
        instruction_implies_style_change(instruction)
        or instruction_implies_full_style_swap(instruction)
    ):
        return (
            "❌ 修改说明涉及视觉风格，但 fields 未包含 style。"
            "请重新调用 modify_outline：涉及风格时至少含 [\"style\"]；"
            "整换风时再按牵动情况加上 description/theme/title/key_message。"
            "含 style/description/theme 时章节会自动联动；场景冲突用 update_scene；只改 description 不会更新风格标签。"
        )

    # 整换风却只改 style → 半改（标题/主题/描述常仍是旧风）；要求模型补齐
    if (
        instruction_implies_full_style_swap(instruction)
        and not instruction_implies_visual_tweak_only(instruction)
        and set(normalized) <= {"style", "chapters"}
    ):
        return (
            "❌ 整风格切换时只传 style 容易半改：标题/主题/描述常仍是旧氛围。"
            "请对照快照里的当前标题·主题·描述，把被牵动的字段一并放进 fields 后重试；"
            "典型 [\"style\",\"description\",\"theme\",\"title\"]（key_message 冲突再加）。"
            "章节随 style/description/theme 自动联动，不必手写 chapters。"
            "若确为仅视觉微调且文案不用动，请把 instruction 写成「色调冷一点」这类微调意图。"
        )

    return None


def _parse_style_preferences(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
        return [part.strip() for part in re.split(r"[,，、]", text) if part.strip()]
    return []


def _outline_text_value(outline: Any, logical_field: str) -> str:
    if logical_field == "theme":
        return (getattr(outline, "theme", None) or "").strip()
    if logical_field == "key_message":
        return (getattr(outline, "key_message", None) or "").strip()
    return (getattr(outline, logical_field, None) or "").strip()


def _outline_style_guide(outline: Any) -> str:
    return (getattr(outline, "style_guide", None) or "").strip()


def _parse_style_preferences_text(text: str, *, max_tags: int = 4) -> List[str]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    parts = re.split(r"[,，、\n]", cleaned)
    tags = [p.strip().strip('"\'') for p in parts if p.strip()]
    return tags[:max_tags]


async def _merge_text_field(
    *,
    base_value: str,
    instruction: str,
    asset_kind: str,
) -> str:
    base = (base_value or "").strip()
    if base:
        merged = await instruction_merge_to_full_prompt(
            base_prompt=base,
            instruction=instruction,
            asset_kind=asset_kind,
        )
        return (merged or "").strip()
    return instruction.strip()


def _chapter_description_instruction(
    *,
    instruction: str,
    chapter: Any,
    chapter_index: int,
    total_chapters: int,
    effective_description: str,
    effective_style_guide: str,
    effective_theme: str,
) -> str:
    title = (getattr(chapter, "title", None) or "").strip()
    return (
        f"{instruction}\n"
        f"这是第 {chapter_index + 1}/{total_chapters} 章（当前标题：{title or '无'}）。\n"
        f"整体故事描述：{effective_description or '无'}\n"
        f"整体主题：{effective_theme or '无'}\n"
        f"整体视觉风格：{effective_style_guide or '无'}\n"
        "请在本章描述原文基础上融合上述整体变化：保留本章在叙事中的情节功能、时长与顺序，"
        "替换与新风/新基调冲突的环境、氛围与画面表述；输出连贯完整的一段，不要句尾打补丁。"
    )


def _chapter_title_instruction(
    *,
    instruction: str,
    chapter: Any,
    new_description: str,
    effective_style_guide: str,
) -> str:
    old_title = (getattr(chapter, "title", None) or "").strip()
    desc_preview = (new_description or "")[:120]
    return (
        f"{instruction}\n"
        f"当前章节标题：{old_title or '无'}\n"
        f"本章更新后的描述（仅供理解语境，禁止写入标题输出）：{desc_preview}\n"
        f"整体视觉风格：{effective_style_guide or '无'}\n"
        "【标题输出规则 — 必须严格遵守】\n"
        "1. 只输出一行章节标题，不超过 30 个汉字（或相当长度）；\n"
        "2. 禁止输出描述正文、禁止多句、禁止列表、禁止解释或前后缀；\n"
        "3. 若原标题与更新后内容仍贴切，原样输出原标题；仅当明显不符时才给出更短、更贴切的新标题；\n"
        "4. 不要引号、不要 Markdown、不要冒号后的副标题说明。"
    )


def _normalize_chapter_title(raw: str, *, fallback: str = "") -> str:
    """兜底：prompt 已约束单行 ≤30 字；若 LLM 仍输出描述级长文，保留原标题。"""
    text = (raw or "").strip().strip('"\'""''')
    if not text:
        return fallback
    if "\n" in text or len(text) > 30 or text.count("。") > 0:
        return fallback
    return text


async def _merge_one_chapter(
    *,
    chapter: Any,
    chapter_index: int,
    total_chapters: int,
    instruction: str,
    effective_description: str,
    effective_style_guide: str,
    effective_theme: str,
    sync_title: bool,
) -> Optional[ChapterUpdate]:
    chapter_uuid = getattr(chapter, "uuid", None)
    if not chapter_uuid:
        return None

    desc_instruction = _chapter_description_instruction(
        instruction=instruction,
        chapter=chapter,
        chapter_index=chapter_index,
        total_chapters=total_chapters,
        effective_description=effective_description,
        effective_style_guide=effective_style_guide,
        effective_theme=effective_theme,
    )
    new_description = await _merge_text_field(
        base_value=(getattr(chapter, "description", None) or ""),
        instruction=desc_instruction,
        asset_kind=_TEXT_FIELD_ASSET_KIND["chapter_description"],
    )
    if not new_description:
        return None

    update = ChapterUpdate(
        uuid=chapter_uuid,
        order=int(getattr(chapter, "order", chapter_index) or chapter_index),
        description=new_description,
    )

    if sync_title:
        title_instruction = _chapter_title_instruction(
            instruction=instruction,
            chapter=chapter,
            new_description=new_description,
            effective_style_guide=effective_style_guide,
        )
        new_title = await _merge_text_field(
            base_value=(getattr(chapter, "title", None) or ""),
            instruction=title_instruction,
            asset_kind=_TEXT_FIELD_ASSET_KIND["chapter_title"],
        )
        old_title = (getattr(chapter, "title", None) or "").strip()
        new_title = _normalize_chapter_title(new_title or "", fallback=old_title)
        if new_title and new_title != old_title:
            update.title = new_title

    return update


async def _sync_chapters_with_outline_change(
    *,
    chapters: Sequence[Any],
    instruction: str,
    effective_description: str,
    effective_style_guide: str,
    effective_theme: str,
    sync_titles: bool,
) -> List[ChapterUpdate]:
    if not chapters:
        return []
    total = len(chapters)
    tasks = [
        _merge_one_chapter(
            chapter=ch,
            chapter_index=idx,
            total_chapters=total,
            instruction=instruction,
            effective_description=effective_description,
            effective_style_guide=effective_style_guide,
            effective_theme=effective_theme,
            sync_title=sync_titles,
        )
        for idx, ch in enumerate(chapters)
    ]
    results = await asyncio.gather(*tasks)
    return [u for u in results if u is not None]


async def apply_modify_outline(
    *,
    outline: Any,
    instruction: str,
    fields: Sequence[str],
    analysis: Any = None,
    chapters: Optional[Sequence[Any]] = None,
) -> ModifyOutlineResult:
    """按 LLM 指定的逻辑字段批量更新 outline（style 组会展开写多个 DB 字段）。"""
    ins = (instruction or "").strip()
    if not ins:
        return ModifyOutlineResult(success=False, message="❌ 修改指令不能为空。")

    target_fields = normalize_outline_fields(fields)
    if not target_fields:
        return ModifyOutlineResult(
            success=False,
            message="❌ fields 不能为空，请指定要修改的逻辑字段：title/description/theme/key_message/style/chapters。",
        )

    field_error = validate_outline_fields_for_instruction(ins, target_fields)
    if field_error:
        return ModifyOutlineResult(success=False, message=field_error)

    outline_updates: Dict[str, Any] = {}
    analysis_updates: Dict[str, Any] = {}
    changed: List[str] = []
    previews: Dict[str, str] = {}

    requested: Set[str] = set(target_fields)

    if "style" in requested:
        current_guide = _outline_style_guide(outline)
        current_tags = _parse_style_preferences(getattr(analysis, "style_preferences", None) if analysis else None)

        guide_instruction = (
            f"{ins}\n"
            "请按修改意图重写整段视觉风格指南：在原文基础上融合，保留仍成立的信息，"
            "替换或删除与新风冲突的旧风格词（如「真实摄影」「微电影」「明快色彩」等）；"
            "输出应是一段连贯完整的风格描述，不要只在原文末尾追加几个词。"
        )
        new_guide = await _merge_text_field(
            base_value=current_guide,
            instruction=guide_instruction,
            asset_kind=_TEXT_FIELD_ASSET_KIND["style_guide"],
        )
        if not new_guide:
            return ModifyOutlineResult(success=False, message="❌ 生成的新风格指南为空，未修改。")

        base_tags_text = ", ".join(current_tags) if current_tags else "无"
        tags_instruction = (
            f"{ins}\n"
            "只输出 2-4 个彼此独立的短标签，用英文逗号分隔；"
            "标签语言与当前标签保持一致（当前多为中文则输出中文）；"
            "标签应反映新风格；若新旧风格冲突，丢弃旧标签，不要把两个风格词粘成一个标签。"
            "不要保留与新风冲突的旧标签（如「真实摄影」「微电影风格」）。"
        )
        tags_text = await _merge_text_field(
            base_value=base_tags_text,
            instruction=tags_instruction,
            asset_kind=_TEXT_FIELD_ASSET_KIND["style_preferences"],
        )
        new_tags = _parse_style_preferences_text(tags_text)
        if not new_tags:
            new_tags = _parse_style_preferences_text(ins)

        outline_updates["style_guide"] = new_guide
        changed.append("style")
        previews["style_guide"] = new_guide[:120]
        previews["style_preferences"] = ", ".join(new_tags)

        if analysis is not None and new_tags:
            analysis_updates["style_preferences"] = new_tags

    for logical in [f for f in target_fields if f in {"title", "description", "theme", "key_message"}]:
        current = _outline_text_value(outline, logical)
        merge_instruction = ins
        if logical == "description":
            merge_instruction = (
                f"{ins}\n"
                "修订故事整体描述：在原文基础上融合修改意图，删除或替换冲突的旧表述，"
                "保留主线情节骨架；输出应是一段连贯完整的描述，不要只在句尾追加新词。"
            )
        new_text = await _merge_text_field(
            base_value=current,
            instruction=merge_instruction,
            asset_kind=_TEXT_FIELD_ASSET_KIND[logical],
        )
        if not new_text:
            return ModifyOutlineResult(success=False, message=f"❌ 生成的新 {logical} 为空，未修改。")
        outline_updates[logical] = new_text
        if logical not in changed:
            changed.append(logical)
        previews[logical] = new_text[:120]

    chapter_updates: List[ChapterUpdate] = []
    if should_sync_chapters(target_fields) and chapters:
        effective_description = outline_updates.get("description") or _outline_text_value(outline, "description")
        effective_style = outline_updates.get("style_guide") or _outline_style_guide(outline)
        effective_theme = outline_updates.get("theme") or _outline_text_value(outline, "theme")
        sync_titles = bool(requested & {"style", "theme"})
        chapter_updates = await _sync_chapters_with_outline_change(
            chapters=chapters,
            instruction=ins,
            effective_description=effective_description,
            effective_style_guide=effective_style,
            effective_theme=effective_theme,
            sync_titles=sync_titles,
        )
        if chapter_updates and "chapters" not in changed:
            changed.append("chapters")
            previews["chapters"] = f"{len(chapter_updates)} 章已同步"

    return ModifyOutlineResult(
        success=True,
        fields_changed=list(dict.fromkeys(changed)),
        previews=previews,
        outline_updates=outline_updates,
        analysis_updates=analysis_updates,
        chapter_updates=chapter_updates,
    )


def format_modify_outline_success(result: ModifyOutlineResult, outline_uuid: str) -> str:
    lines = [
        "✅ 大纲已更新",
        f"  UUID: {outline_uuid[-8:]}",
        f"  修改字段: {', '.join(result.fields_changed)}",
    ]
    for key in result.fields_changed:
        if key == "chapters":
            continue
        preview = result.previews.get(key) or result.previews.get("style_guide") or result.previews.get("style_preferences")
        if preview:
            suffix = "..." if len(preview) >= 120 else ""
            lines.append(f"  {key}: {preview}{suffix}")
    if "style" in result.fields_changed:
        tags = result.previews.get("style_preferences")
        if tags:
            lines.append(f"  style_preferences: {tags}")
    if result.chapter_updates:
        lines.append(f"  章节已同步: {len(result.chapter_updates)} 个")
        for ch in result.chapter_updates[:3]:
            desc_preview = (ch.description or "")[:80]
            suffix = "..." if ch.description and len(ch.description) > 80 else ""
            title_note = f"（标题→{ch.title}）" if ch.title else ""
            lines.append(f"    第 {ch.order + 1} 章{title_note}: {desc_preview}{suffix}")
        if len(result.chapter_updates) > 3:
            lines.append(f"    ... 共 {len(result.chapter_updates)} 章")
    return "\n".join(lines)
