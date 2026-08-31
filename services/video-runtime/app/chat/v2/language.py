from __future__ import annotations

import re


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_EXPLICIT_SKILL_RE = re.compile(
    r"(?<![A-Za-z0-9_-])[$/]([a-z0-9][a-z0-9-]{0,63})(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_LANGUAGE_SKILL_ALIASES = {
    "language-zh": "language-zh",
    "zh": "language-zh",
    "language-en": "language-en",
    "eng": "language-en",
    "en": "language-en",
}
_LANGUAGE_SKILL_OUTPUT = {"language-zh": "zh", "language-en": "en"}

_SPOKEN_EN_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"(?:对话|对白|台词|旁白|口播|配音|语音|角色说话).{0,12}(?:保持|使用|采用|说|为|用)?\s*(?:英文|英语)",
    r"(?:英文|英语).{0,8}(?:对话|对白|台词|旁白|口播|配音|语音)",
    r"(?:dialogue|dialog|narration|voice[ -]?over|spoken language|speech).{0,16}(?:in|use|remain|keep|be)?\s*english",
    r"english.{0,12}(?:dialogue|dialog|narration|voice[ -]?over|speech)",
))
_SPOKEN_ZH_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"(?:对话|对白|台词|旁白|口播|配音|语音|角色说话).{0,12}(?:保持|使用|采用|说|为|用)?\s*(?:中文|汉语|普通话)",
    r"(?:中文|汉语|普通话).{0,8}(?:对话|对白|台词|旁白|口播|配音|语音)",
    r"(?:dialogue|dialog|narration|voice[ -]?over|spoken language|speech).{0,16}(?:in|use|remain|keep|be)?\s*(?:chinese|mandarin)",
    r"(?:chinese|mandarin).{0,12}(?:dialogue|dialog|narration|voice[ -]?over|speech)",
))


class LanguageSkillConflictError(ValueError):
    """Raised when one message explicitly selects incompatible languages."""


def canonical_language_skill_name(name: str | None) -> str | None:
    return _LANGUAGE_SKILL_ALIASES.get((name or "").strip().lower())


def explicit_language_skill(text: str) -> str | None:
    selected: list[str] = []
    for raw_name in _EXPLICIT_SKILL_RE.findall(text or ""):
        name = canonical_language_skill_name(raw_name)
        if name and name not in selected:
            selected.append(name)
    if len(selected) > 1:
        raise LanguageSkillConflictError(
            "同一条消息只能选择一种语言 Skill；请保留 $language-zh（或 $zh）"
            "与 $language-en（或 $eng）中的一个。"
        )
    return selected[0] if selected else None


def active_language_skill(skills: list[str] | None) -> str | None:
    selected = {
        canonical for name in (skills or [])
        if (canonical := canonical_language_skill_name(name)) is not None
    }
    if len(selected) > 1:
        raise LanguageSkillConflictError("当前任务同时激活了中文和英文语言 Skill。")
    return next(iter(selected), None)


def normalize_output_language(value: str | None) -> str | None:
    """Normalize UI/browser language values to the languages supported by V2."""
    normalized = (value or "").strip().lower().replace("_", "-")
    if normalized.startswith("zh"):
        return "zh"
    if normalized.startswith("en"):
        return "en"
    return None


def resolve_output_language(
    text: str,
    *,
    requested: str | None = None,
    current: str | None = None,
    language_skill: str | None = None,
) -> str:
    """Resolve visible language; an explicit run-level language Skill wins."""
    canonical = canonical_language_skill_name(language_skill)
    if canonical:
        return _LANGUAGE_SKILL_OUTPUT[canonical]
    if _CJK_RE.search(text or ""):
        return "zh"
    return (
        normalize_output_language(requested)
        or normalize_output_language(current)
        or "en"
    )


def resolve_spoken_language(
    text: str,
    *,
    output_language: str | None = None,
    language_skill: str | None = None,
) -> str:
    """Resolve dialogue/narration language independently from the UI language.

    A language Skill controls the whole run. Without one, an explicit spoken-language
    request wins over the language inferred for user-visible planning output.
    """
    canonical = canonical_language_skill_name(language_skill)
    if canonical:
        return "zh-CN" if _LANGUAGE_SKILL_OUTPUT[canonical] == "zh" else "en-US"
    source = text or ""
    if any(pattern.search(source) for pattern in _SPOKEN_EN_PATTERNS):
        return "en-US"
    if any(pattern.search(source) for pattern in _SPOKEN_ZH_PATTERNS):
        return "zh-CN"
    return "zh-CN" if normalize_output_language(output_language) == "zh" else "en-US"


def language_contract(
    language: str | None,
    *,
    provider_prompt_language: str = "auto",
    spoken_language: str | None = None,
) -> dict[str, str]:
    if normalize_output_language(language) == "zh":
        visible = on_screen = "zh-CN"
    else:
        visible = on_screen = "en-US"
    spoken = spoken_language or visible
    return {
        "user_visible_language": visible,
        "spoken_language": spoken,
        "on_screen_text_language": on_screen,
        "provider_prompt_language": provider_prompt_language,
    }


def output_language_instruction(
    language: str | None,
    *,
    provider_prompt_language: str = "auto",
    user_request: str = "",
    language_skill: str | None = None,
) -> str:
    spoken_language = resolve_spoken_language(
        user_request,
        output_language=language,
        language_skill=language_skill,
    )
    contract = language_contract(
        language,
        provider_prompt_language=provider_prompt_language,
        spoken_language=spoken_language,
    )
    spoken_override = (
        " The user's explicit dialogue/narration language is authoritative and "
        "independent from the UI and planning-output language. Preserve it in every "
        "stage, provider prompt, and capability parameter; never translate spoken lines "
        "to the user-visible language."
        if user_request and spoken_language != contract["user_visible_language"]
        else ""
    )
    if normalize_output_language(language) == "zh":
        return (
            "Output language contract: write all user-visible responses, plans, task "
            "titles, artifact titles, summaries, briefs, scripts, shot plans, and review "
            "reports in Simplified Chinese. Keep JSON keys, capability IDs, model IDs, "
            "and other machine-readable identifiers unchanged. A vendor generation prompt "
            "may use another language only when the active Skill or model explicitly requires it. "
            f"Spoken narration/dialogue language: {contract['spoken_language']}."
            f"{spoken_override} "
            f"Requested on-screen copy language: {contract['on_screen_text_language']}. "
            "Do not add narration, dialogue, captions, or on-screen text unless the task calls for them."
        )
    return (
        "Output language contract: write all user-visible responses, plans, task titles, "
        "artifact titles, summaries, briefs, scripts, shot plans, and review reports in "
        "English. Keep JSON keys, capability IDs, model IDs, and other machine-readable "
        "identifiers unchanged. A vendor generation prompt may use another language only "
        "when the active Skill or model explicitly requires it. "
        f"Spoken narration/dialogue language: {contract['spoken_language']}."
        f"{spoken_override} "
        f"Requested on-screen copy language: {contract['on_screen_text_language']}. "
        "Do not add narration, dialogue, captions, or on-screen text unless the task calls for them."
    )
