"""旁白讲解者性别：在 storyboard detail 节点与 narration 一并产出，TTS 只读不写。"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ....models.video_state import CharacterProfile, DetailedShot, DetailedShotLLMOutput

logger = logging.getLogger(__name__)

NARRATION_GENDER_KEY = "narration_gender"

_FEMALE_GENDER_HINTS = (
    "女性", "女人", "女孩", "女生", "女士", "少女", "女主", " heroine", " actress",
    "female", "woman", "girl", "lady", "she ", " her ",
)
_MALE_GENDER_HINTS = (
    "男性", "男人", "男孩", "男生", "男士", "少年", "男主", " hero", " actor",
    "male", "man", "boy", "gentleman", "he ", " his ", "男士", "男子", "帅哥", "男主播",
)


def normalize_narration_gender(value: Optional[str]) -> Optional[str]:
    """规范为 'f' / 'm' / None。"""
    if not value:
        return None
    key = str(value).strip().lower()
    if key in ("f", "female", "女", "女声"):
        return "f"
    if key in ("m", "male", "男", "男声"):
        return "m"
    return None


def infer_gender_from_character(character: "CharacterProfile") -> Optional[str]:
    """detail 节点落库时的兜底：从角色档案启发式推断（仅 LLM 未填 narration_gender 时用）。"""
    from ....models.video_state import VisualElementType

    if character.type != VisualElementType.CHARACTER:
        return None
    text = " ".join(
        part for part in (
            character.name,
            character.appearance,
            character.description,
            character.role or "",
        ) if part
    ).lower()
    female_score = sum(1 for hint in _FEMALE_GENDER_HINTS if hint.lower() in text)
    male_score = sum(1 for hint in _MALE_GENDER_HINTS if hint.lower() in text)
    if female_score > male_score:
        return "f"
    if male_score > female_score:
        return "m"
    return None


def infer_gender_from_character_ids(
    character_ids: Optional[List[str]],
    characters_by_id: Dict[str, "CharacterProfile"],
) -> Optional[str]:
    """detail 节点落库兜底：按镜头 character_ids 推断讲解者性别。"""
    from ....models.video_state import VisualElementType

    genders: List[str] = []
    for character_id in character_ids or []:
        character = characters_by_id.get(character_id)
        if not character or character.type != VisualElementType.CHARACTER:
            continue
        gender = infer_gender_from_character(character)
        if gender:
            genders.append(gender)
    if not genders:
        return None
    return genders[0]


def resolve_narration_gender_for_detail_shot(
    shot_llm: "DetailedShotLLMOutput",
    character_ids: Optional[List[str]],
    characters_by_id: Dict[str, "CharacterProfile"],
    narration_text: Optional[str],
) -> Optional[str]:
    """detail 节点写入 DB 前确定 narration_gender（优先 LLM 字段，无旁白则为 None）。"""
    if not (narration_text or "").strip():
        return None
    explicit = normalize_narration_gender(getattr(shot_llm, "narration_gender", None))
    if explicit:
        return explicit
    fallback = infer_gender_from_character_ids(character_ids, characters_by_id)
    if fallback:
        logger.warning(
            "镜头%d narration_gender 未由 detail LLM 填写，落库兜底推断为 %s",
            shot_llm.shot_number,
            fallback,
        )
    return fallback


def narration_gender_from_shot(shot: "DetailedShot") -> Optional[str]:
    """TTS 节点读取：优先 shot.narration_gender，其次 additional_data.narration_gender。"""
    explicit = normalize_narration_gender(getattr(shot, "narration_gender", None))
    if explicit:
        return explicit
    ad = getattr(shot, "additional_data", None) or {}
    return normalize_narration_gender(ad.get(NARRATION_GENDER_KEY))


def narration_gender_label(gender: Optional[str]) -> str:
    return {"f": "女", "m": "男"}.get(gender or "", "未知")
