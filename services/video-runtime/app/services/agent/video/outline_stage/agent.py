"""Outline stage deep agent: tools + invoke (no mustache)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from app.contracts.artifacts.outline import OutlineAgentDraft, OutlineArtifact
from app.services.agent.stage_runtime.deep_agent_factory import create_stage_deep_agent
from app.services.agent.stage_runtime.generic_stage import (
    ensure_artifact_from_result,
    _log_write_tool_retries,
)
from app.services.agent.stage_runtime.paths import (
    is_story_narrative_category,
    narrative_skills_paths,
    virtual_run_prefix,
)
from app.services.agent.stage_runtime.workspace import (
    artifact_relpath,
    read_artifact_json,
    write_artifact_json,
)

logger = logging.getLogger(__name__)


def _make_write_outline_tool(
    *,
    thread_id: str,
    run_id: str,
    mode: str,
    target_duration: float,
    expect_section_count: Optional[int],
):
    def write_outline_artifact(**kwargs: Any) -> str:
        """Validate outline draft and write artifacts/outline.json under this run workspace."""
        try:
            draft = OutlineAgentDraft.model_validate(kwargs)
        except ValidationError as e:
            return f"VALIDATION_ERROR: {e}"

        # Normalize 1-based order → 0-based if agent slips
        orders = [ch.order for ch in draft.chapters]
        if orders and min(orders) >= 1 and 0 not in orders:
            for i, ch in enumerate(sorted(draft.chapters, key=lambda c: c.order)):
                ch.order = i

        if mode == "video_driven":
            target = int(round(target_duration))
            if draft.total_duration != target:
                return (
                    f"VALIDATION_ERROR: total_duration={draft.total_duration} "
                    f"!= target_duration_seconds={target}"
                )
            chapter_sum = sum(float(ch.duration or 0) for ch in draft.chapters)
            if abs(chapter_sum - target) > 0.51:
                return (
                    f"VALIDATION_ERROR: chapter duration sum={chapter_sum} "
                    f"!= target_duration_seconds={target}"
                )
            for ch in draft.chapters:
                if ch.duration is None or float(ch.duration) <= 0:
                    return f"VALIDATION_ERROR: chapter {ch.id} missing positive duration"
        elif expect_section_count is not None and len(draft.chapters) != expect_section_count:
            return (
                f"VALIDATION_ERROR: audio mode expects {expect_section_count} chapters "
                f"(1:1 sections), got {len(draft.chapters)}"
            )
        # enhancement_cues are optional (story clarity over cue count).

        art = OutlineArtifact(
            mode=mode,  # type: ignore[arg-type]
            thread_id=thread_id,
            run_id=run_id,
            title=draft.title,
            theme=draft.theme,
            description=draft.description,
            key_message=draft.key_message,
            total_duration=int(draft.total_duration),
            style_guide=draft.style_guide,
            user_message=draft.user_message or "",
            chapters=draft.chapters,
        )
        write_artifact_json(thread_id, run_id, "outline.json", art.model_dump(mode="json"))
        rel = artifact_relpath(thread_id, run_id, "outline.json")
        return f"OK wrote {rel} chapters={len(art.chapters)}"

    return StructuredTool.from_function(
        func=write_outline_artifact,
        name="write_outline_artifact",
        description=(
            "Validate and persist outline.json. Args match OutlineAgentDraft schema. "
            "On VALIDATION_ERROR, fix and call again in the same turn."
        ),
        args_schema=OutlineAgentDraft,
    )


async def generate_outline_via_deep_agent(
    *,
    thread_id: str,
    run_id: str,
    input_paths: Dict[str, Any],
    detected_language: Optional[str] = None,
) -> Tuple[OutlineArtifact, List[Any]]:
    """
    Spawn outline-director deep agent. Returns validated OutlineArtifact + message trail.
    """
    from prompts.prompt_config import PROMPTS_CONFIG, PromptName
    from prompts.prompt_loader import create_llm_from_model_config

    mode = input_paths["mode"]
    target = float(input_paths["target_duration_seconds"])
    expect_sections = input_paths.get("expect_section_count")
    content_category = str(input_paths.get("content_category") or "")

    write_tool = _make_write_outline_tool(
        thread_id=thread_id,
        run_id=run_id,
        mode=mode,
        target_duration=target,
        expect_section_count=expect_sections,
    )

    is_audio = mode == "audio_driven"
    prompt_name = (
        PromptName.VIDEO_OUTLINE_GENERATION_AUDIO_DRIVEN
        if is_audio
        else PromptName.VIDEO_OUTLINE_GENERATION_VIDEO_DRIVEN
    )
    entry = PROMPTS_CONFIG[prompt_name]
    mc = dict(entry.get("model_config") or {"model": "gpt-4.1-mini"})
    llm = create_llm_from_model_config(mc)

    prefix = virtual_run_prefix(thread_id, run_id)
    brief_path = input_paths["analysis_brief"]
    audio_path = input_paths.get("audio_context")

    story_narrative = is_story_narrative_category(content_category)
    craft_note = (
        "Follow outline-director only; do NOT activate Explainer storytelling templates."
        if story_narrative
        else "Activate outline-director; use creative/storytelling and short-form as needed."
    )
    system_prompt = (
        "You are a video production stage agent for outline generation. "
        f"{craft_note} "
        "Filesystem is virtual under the agent service root: read JSON inputs with leading slash. "
        "When calling read_file on briefs or skills, always pass limit=2000. "
        "Persist via write_outline_artifact (args match OutlineAgentDraft). "
        "If write returns VALIDATION_ERROR, fix and call write again in the same turn."
    )

    agent = create_stage_deep_agent(
        model=llm,
        tools=[write_tool],
        skills_virtual_paths=narrative_skills_paths(
            "outline", content_category=content_category
        ),
        system_prompt=system_prompt,
        name="outline_stage",
    )

    lang_note = f" Write all creative text in language: {detected_language}." if detected_language else ""
    text = (
        f"Run the outline stage.{lang_note} "
        f"Read {brief_path} with read_file limit=2000"
        + (f" and {audio_path} with read_file limit=2000" if audio_path else "")
        + ". Follow outline-director. "
        + "Call write_outline_artifact. "
        + "Do not stop after only reading files."
    )

    content: Any = text
    image_urls = input_paths.get("reference_image_urls") or []
    if image_urls:
        parts: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        for url in image_urls[:8]:
            parts.append({"type": "image_url", "image_url": {"url": url}})
        content = parts

    result = await agent.ainvoke({"messages": [HumanMessage(content=content)]})
    messages = list(result.get("messages") or [])
    _log_write_tool_retries("outline", messages)
    ensure_artifact_from_result(
        result=result if isinstance(result, dict) else dict(result),
        write_tool=write_tool,
        thread_id=thread_id,
        run_id=run_id,
        artifact_name="outline.json",
        stage="outline",
    )
    raw = read_artifact_json(thread_id, run_id, "outline.json")
    artifact = OutlineArtifact.model_validate(raw)

    logger.info(
        "outline deep agent OK thread=%s run=%s chapters=%d path=%s",
        thread_id,
        run_id,
        len(artifact.chapters),
        f"{prefix}/artifacts/outline.json",
    )
    return artifact, messages
