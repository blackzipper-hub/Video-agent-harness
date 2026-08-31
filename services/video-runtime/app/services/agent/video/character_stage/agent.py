"""Character profiles deep agent (no image tools)."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from app.contracts.artifacts.character import (
    CharactersAgentDraft,
    CharactersArtifact,
)
from app.models.video_state import CharacterProfile, CharacterProfiles, VisualElementType
from app.services.agent.stage_runtime.deep_agent_factory import create_stage_deep_agent
from app.services.agent.stage_runtime.generic_stage import (
    ensure_artifact_from_result,
    _log_write_tool_retries,
)
from app.services.agent.stage_runtime.paths import virtual_skills_stage
from app.services.agent.stage_runtime.workspace import (
    artifact_relpath,
    read_artifact_json,
    write_artifact_json,
)
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "character": VisualElementType.CHARACTER,
    "object": VisualElementType.OBJECT,
    "location": VisualElementType.LOCATION,
}


def _make_write_tool(*, thread_id: str, run_id: str):
    def write_characters_artifact(**kwargs: Any) -> str:
        """Validate character profiles and write artifacts/characters.json."""
        try:
            draft = CharactersAgentDraft.model_validate(kwargs)
        except ValidationError as e:
            return f"VALIDATION_ERROR: {e}"

        if not draft.characters:
            return "VALIDATION_ERROR: characters empty"
        for ch in draft.characters:
            t = (ch.type or "character").lower().strip()
            if t not in _TYPE_MAP:
                return f"VALIDATION_ERROR: character {ch.id} type={ch.type} invalid"
            if not (ch.name and ch.appearance):
                return f"VALIDATION_ERROR: character {ch.id} needs name + appearance"
            if t == "character" and not (ch.personality or "").strip():
                return f"VALIDATION_ERROR: character {ch.id} needs personality"

        art = CharactersArtifact(
            thread_id=thread_id,
            run_id=run_id,
            characters=draft.characters,
            user_message=draft.user_message or "",
        )
        write_artifact_json(thread_id, run_id, "characters.json", art.model_dump(mode="json"))
        return f"OK wrote {artifact_relpath(thread_id, run_id, 'characters.json')} n={len(art.characters)}"

    return StructuredTool.from_function(
        func=write_characters_artifact,
        name="write_characters_artifact",
        description=(
            "Validate and persist characters.json. Args match CharactersAgentDraft. "
            "On VALIDATION_ERROR, fix and call again in the same turn."
        ),
        args_schema=CharactersAgentDraft,
    )


def artifact_to_profiles(art: CharactersArtifact) -> CharacterProfiles:
    profiles: List[CharacterProfile] = []
    for ch in art.characters:
        t = _TYPE_MAP.get((ch.type or "character").lower().strip(), VisualElementType.CHARACTER)
        cid = ch.id or f"char_{uuid.uuid4().hex[:8]}"
        profiles.append(
            CharacterProfile(
                id=cid,
                type=t,
                name=ch.name,
                description=ch.description or "",
                personality=ch.personality or "",
                appearance=ch.appearance or "",
                role=ch.role or "",
                style=ch.style or "",
                body_type=ch.body_type or "",
            )
        )
    return CharacterProfiles(characters=profiles, user_message=art.user_message or "")


async def generate_characters_via_deep_agent(
    *,
    thread_id: str,
    run_id: str,
    input_paths: Dict[str, Any],
    detected_language: Optional[str] = None,
) -> Tuple[CharacterProfiles, List[Any]]:
    from prompts.prompt_config import PROMPTS_CONFIG, PromptName
    from prompts.prompt_loader import create_llm_from_model_config

    # Keep multimodal (Gemini under LLM_QUALITY); do not switch to tool/gpt-5.6 here.
    write_tool = _make_write_tool(thread_id=thread_id, run_id=run_id)
    entry = PROMPTS_CONFIG[PromptName.VIDEO_MAIN_CHARACTER_GENERATION]
    mc = dict(entry.get("model_config") or {"model": "gpt-4.1-mini"})
    llm = create_llm_from_model_config(mc)

    agent = create_stage_deep_agent(
        model=llm,
        tools=[write_tool],
        skills_virtual_paths=[virtual_skills_stage("character")],
        system_prompt=(
            "You are a video production stage agent for character design. "
            "Activate the character-director skill. "
            "Read briefs with read_file limit=2000. "
            "Persist via write_characters_artifact (args match CharactersAgentDraft). "
            "If write returns VALIDATION_ERROR, fix and call again in the same turn. "
            "Do not generate images."
        ),
        name="character_stage",
    )

    lang_note = f" Write creative text in language: {detected_language}." if detected_language else ""
    paths = [input_paths["outline_brief"]]
    if input_paths.get("character_brief"):
        paths.append(input_paths["character_brief"])
    if input_paths.get("analysis_brief"):
        paths.append(input_paths["analysis_brief"])
    text = (
        f"Run the character stage.{lang_note} "
        f"Read {' and '.join(paths)} with read_file limit=2000. "
        f"Follow character-director. Call write_characters_artifact."
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
    _log_write_tool_retries("character", messages)
    ensure_artifact_from_result(
        result=result if isinstance(result, dict) else dict(result),
        write_tool=write_tool,
        thread_id=thread_id,
        run_id=run_id,
        artifact_name="characters.json",
        stage="character",
    )

    raw = read_artifact_json(thread_id, run_id, "characters.json")
    artifact = CharactersArtifact.model_validate(raw)
    profiles = artifact_to_profiles(artifact)
    logger.info("character deep agent OK n=%d", len(profiles.characters))
    return profiles, messages
