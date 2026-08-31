"""
I2I 角色一致性校验：

1. VLM 输出 has_character、per_character、整图 **severe_abnormality**（轻伪影 + 离谱事故合并一档，与视频 severe 同分级）
2. 人设维度与整图 severe 由后端聚合 passed（见 ConsistencyCheckResult）
3. 返回 ConsistencyCheckResult，由 wrapper 做重试与降级决策

ConsistencyLevel 使用 LLM 分类（而非数值评分）。整图与 per_character 的变形档使用 VideoConsistencyLevel。
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Any

from app.schemas.video_llm import VideoConsistencyLevel, CharacterConsistencyResult

logger = logging.getLogger(__name__)


# ==================== 一致性等级 ====================


class ConsistencyLevel(str, Enum):
    """角色一致性等级 — LLM 输出分类，比数值评分更稳定"""

    IDENTICAL = "identical"  # 完全一致，几乎看不出区别
    VERY_SIMILAR = "very_similar"  # 非常相似，同一角色但细节略有差异
    SOMEWHAT_SIMILAR = "somewhat_similar"  # 有些相似，能认出同一角色但差异明显
    NOT_SIMILAR = "not_similar"  # 不太像，差异较大
    VERY_DIFFERENT = "very_different"  # 完全不同的角色
    N_A = "n_a"  # 未检查/不适用，视为通过


CONSISTENCY_SCORE: Dict[ConsistencyLevel, int] = {
    ConsistencyLevel.IDENTICAL: 5,
    ConsistencyLevel.VERY_SIMILAR: 4,
    ConsistencyLevel.SOMEWHAT_SIMILAR: 3,
    ConsistencyLevel.NOT_SIMILAR: 2,
    ConsistencyLevel.VERY_DIFFERENT: 1,
    ConsistencyLevel.N_A: 5,  # n_a 视为通过
}

# VERY_SIMILAR 及以上视为通过
_PASS_THRESHOLD = CONSISTENCY_SCORE[ConsistencyLevel.VERY_SIMILAR]  # 4


@dataclass
class ConsistencyCheckResult:
    """一致性校验结果：per_character + 整图 severe_abnormality；passed = 角色维与整图维均通过"""

    has_character: bool
    reason: Optional[str] = None
    severe_abnormality: Optional[VideoConsistencyLevel] = None
    severe_abnormality_reason: Optional[str] = None
    suggested_prompt: Optional[str] = None
    per_character: Optional[List[Dict[str, Any]]] = None  # 按角色拆分的结果列表（每项含 name, face_level, ..., artifact, reason, passed）
    passed_override: Optional[bool] = None  # 由调用方聚合 severe、per_character

    def _severe_pass(self) -> bool:
        if self.severe_abnormality is None:
            return True
        return self.severe_abnormality.is_pass()

    @property
    def score(self) -> int:
        """数值评分（1-5），用于多候选排序。取所有角色 face_level 的最低分。"""
        if not self.per_character:
            return 5 if self._severe_pass() else 3
        scores = []
        for pc in self.per_character:
            fl = pc.get("face_level", "n_a")
            for member in ConsistencyLevel:
                if member.value == fl:
                    scores.append(CONSISTENCY_SCORE.get(member, 3))
                    break
            else:
                scores.append(3)
        return min(scores) if scores else 3

    @property
    def passed(self) -> bool:
        if self.passed_override is not None:
            return self.passed_override
        if not self.has_character:
            return self._severe_pass()
        if not self.per_character:
            return self._severe_pass()
        return (
            all(p.get("passed", False) for p in self.per_character)
            and self._severe_pass()
        )


# ==================== 内部工具函数 ====================


def _parse_optional_level(raw: Optional[str]) -> Optional[ConsistencyLevel]:
    """解析可选维度（配饰/服装）；n_a 或空返回 N_A（视为通过）。"""
    if not raw:
        return ConsistencyLevel.N_A
    raw = raw.strip().lower()
    if raw == ConsistencyLevel.N_A.value:
        return ConsistencyLevel.N_A
    for member in ConsistencyLevel:
        if member.value == raw:
            return member
    return ConsistencyLevel.N_A


# ==================== 主函数 ====================


def _format_reference_labels_text(reference_labels: Optional[List[Dict[str, Any]]]) -> str:
    """将 reference_labels 格式化为 prompt 中的参考图说明文本。"""
    if not reference_labels:
        return ""
    lines = []
    for lb in reference_labels:
        idx = lb.get("index") or lb.get("image_number") or (len(lines) + 1)
        name = lb.get("name") or ""
        type_label = lb.get("type_label") or ""
        desc = lb.get("description") or ""
        appearance = lb.get("appearance") or ""
        style = lb.get("style") or ""
        body_type = lb.get("body_type") or ""
        role = lb.get("role") or ""
        parts = [f"参考图 {idx}（image {idx}）：{name}" + (f"（{type_label}）" if type_label else "")]
        if desc:
            parts.append(f"；描述：{desc}")
        if appearance:
            parts.append(f"；外观：{appearance}")
        if style:
            parts.append(f"；风格：{style}")
        if body_type:
            parts.append(f"；体型：{body_type}")
        if role:
            parts.append(f"；作用：{role}")
        lines.append("".join(p for p in parts if p))
    return "\n".join(lines) if lines else ""


def _level_pass(lv: Optional[ConsistencyLevel]) -> bool:
    """None 或 N_A 视为通过；否则 VERY_SIMILAR 及以上通过。"""
    if lv is None or lv == ConsistencyLevel.N_A:
        return True
    return CONSISTENCY_SCORE.get(lv, 0) >= _PASS_THRESHOLD


def _per_char_passed(pc: Any) -> bool:
    """单角色通过：六维 >= very_similar（或 n_a）且 artifact 通过。"""
    level = _parse_optional_level(getattr(pc, "consistency_level", None))
    acc = _parse_optional_level(getattr(pc, "accessories_level", None))
    cloth = _parse_optional_level(getattr(pc, "clothing_level", None))
    body = _parse_optional_level(getattr(pc, "body_level", None))
    hair = _parse_optional_level(getattr(pc, "hair_level", None))
    style = _parse_optional_level(getattr(pc, "style_level", None))
    artifact = getattr(pc, "artifact", None)
    if not _level_pass(level) or not _level_pass(acc) or not _level_pass(cloth) or not _level_pass(body) or not _level_pass(hair) or not _level_pass(style):
        return False
    if artifact is not None and not artifact.is_pass():
        return False
    return True


async def check_character_consistency_llm(
    reference_urls: List[str],
    result_image_url: str,
    prompt_used: Optional[str] = None,
    reference_labels: Optional[List[Dict[str, Any]]] = None,
) -> ConsistencyCheckResult:
    """
    用 VLM 判断角色一致性，返回 ConsistencyCheckResult（含等级分类）。

    - reference_urls 为空或 result_image_url 为空 → 默认 IDENTICAL（不阻塞）
    - reference_labels：与 reference_urls 一一对应，每项可含 index/name/type_label/description/appearance/style/body_type/role，供 prompt 使用
    - 生成图全黑/空白 → 直接不通过（不调用 VLM）
    - schema 未 rebuild 类异常 → 不通过；其它瞬态异常 → 默认通过（不阻塞）
    """
    if not reference_urls or not result_image_url:
        return ConsistencyCheckResult(
            has_character=False,
            reason="无参考图或无生成图，跳过校验",
            passed_override=True,
        )
    try:
        import uuid
        from app.schemas.video_llm import PerCharacterConsistencyResult
        from app.services.agent.video.image_consistency_stage import (
            check_image_consistency_via_deep_agent,
            export_image_consistency_inputs,
        )

        reference_labels_text = _format_reference_labels_text(reference_labels)
        image_urls = list(reference_urls) + [result_image_url]
        tid = f"icons_{uuid.uuid4().hex[:8]}"
        rid = f"run_{uuid.uuid4().hex[:8]}"
        paths = export_image_consistency_inputs(
            thread_id=tid,
            run_id=rid,
            generation_prompt=prompt_used or "",
            reference_labels_text=reference_labels_text,
            image_urls=image_urls,
        )
        art, _msgs = await check_image_consistency_via_deep_agent(
            thread_id=tid,
            run_id=rid,
            input_paths=paths,
        )
        parsed = CharacterConsistencyResult.model_validate(
            art.model_dump(
                mode="json",
                exclude={"schema_version", "artifact", "thread_id", "run_id"},
            )
        )

        per_character_list: List[Dict[str, Any]] = []
        for pc in getattr(parsed, "per_character", None) or []:
            if not isinstance(pc, PerCharacterConsistencyResult):
                continue
            lv = _parse_optional_level(getattr(pc, "consistency_level", None))
            acc = _parse_optional_level(getattr(pc, "accessories_level", None))
            cloth = _parse_optional_level(getattr(pc, "clothing_level", None))
            body = _parse_optional_level(getattr(pc, "body_level", None))
            hair = _parse_optional_level(getattr(pc, "hair_level", None))
            st = _parse_optional_level(getattr(pc, "style_level", None))
            art = getattr(pc, "artifact", None)
            char_passed = _per_char_passed(pc)
            per_character_list.append({
                "name": getattr(pc, "name", None),
                "face_level": lv.value if lv else "n_a",
                "accessories_level": acc.value if acc else "n_a",
                "clothing_level": cloth.value if cloth else "n_a",
                "body_level": body.value if body else "n_a",
                "hair_level": hair.value if hair else "n_a",
                "style_level": st.value if st else "n_a",
                "artifact": art.value if art else VideoConsistencyLevel.N_A.value,
                "reason": getattr(pc, "reason", "") or "",
                "passed": char_passed,
            })
        severe = getattr(parsed, "severe_abnormality", None)
        severe_reason = getattr(parsed, "severe_abnormality_reason", None)
        suggested_prompt = getattr(parsed, "suggested_prompt", None)
        result = ConsistencyCheckResult(
            has_character=parsed.has_character,
            reason=parsed.reason,
            severe_abnormality=severe,
            severe_abnormality_reason=severe_reason if severe_reason else None,
            suggested_prompt=suggested_prompt if suggested_prompt and str(suggested_prompt).strip() else None,
            per_character=per_character_list if per_character_list else None,
        )
        char_ok = (all(p.get("passed", False) for p in per_character_list) if per_character_list else True)
        if not parsed.has_character:
            char_ok = True
        result.passed_override = char_ok and (severe is None or severe.is_pass())

        logger.info(
            "🔍 角色一致性校验: reference_urls=%s result_image_url=%s has_character=%s severe=%s passed=%s reason=%s has_suggested=%s per_character_count=%s",
            reference_urls,
            result_image_url,
            result.has_character,
            result.severe_abnormality.value if result.severe_abnormality else "n_a",
            result.passed,
            result.reason or "",
            bool(result.suggested_prompt),
            len(per_character_list),
        )
        return result
    except Exception as e:
        err = str(e)
        # Schema forward-ref bugs must not silently greenlight bad frames.
        if "not fully defined" in err or "model_rebuild" in err:
            logger.error("⚠️ 角色一致性 schema 异常，默认不通过: %s", e)
            return ConsistencyCheckResult(
                has_character=True,
                reason=f"校验异常，默认不通过: {e}",
                severe_abnormality=VideoConsistencyLevel.FAIL,
                severe_abnormality_reason="consistency_schema_error",
                passed_override=False,
            )
        logger.warning("⚠️ 角色一致性校验调用异常，默认视为一致: %s", e)
        return ConsistencyCheckResult(
            has_character=True,
            reason=f"校验异常，默认通过: {e}",
            severe_abnormality=VideoConsistencyLevel.N_A,
            passed_override=True,
        )
