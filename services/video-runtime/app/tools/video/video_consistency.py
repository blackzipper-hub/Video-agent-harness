"""
I2V 视频一致性校验（多维度）— craft in video-consistency-director SKILL.md.

1. 首帧图 + 生成视频 + I2V 提示词，用 Gemini VLM 打分
2. 后端按通过条件计算 passed（camera_movement 不参与）
3. 返回 VideoConsistencyCheckResult
"""
import logging
import uuid
from typing import Optional, List, Any, Dict, TYPE_CHECKING

from app.services.agent.utils.prompt_utils import log_video_spin_prompt_trace
from app.schemas.video_llm import VideoConsistencyLevel

if TYPE_CHECKING:
    from app.schemas.video_llm import VideoConsistencyCheckResult

logger = logging.getLogger(__name__)


def _format_character_ref_labels_text(character_ref_labels: Optional[List[Dict[str, Any]]]) -> str:
    """将 character_ref_labels 格式化为 prompt 中的角色参考图说明文本。"""
    if not character_ref_labels:
        return ""
    lines = []
    for lb in character_ref_labels:
        idx = lb.get("index") or (len(lines) + 1)
        name = lb.get("name") or ""
        type_label = lb.get("type_label") or "角色"
        desc = lb.get("description") or ""
        appearance = lb.get("appearance") or ""
        style = lb.get("style") or ""
        body_type = lb.get("body_type") or ""
        role = lb.get("role") or ""
        parts = [f"角色参考图 {idx}：{name}" + (f"（{type_label}）" if type_label else "")]
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


def _compute_passed(c: "VideoConsistencyCheckResult") -> bool:
    """
    后端 pass 条件：first_frame_consistency、severe_abnormality 须 good/acceptable/n_a；
    style_consistency 不得为 fail。不包含 camera_movement。
    """
    ffc = c.first_frame_consistency
    return (
        (ffc is None or ffc.is_pass())
        and c.severe_abnormality.is_pass()
        and c.style_consistency != VideoConsistencyLevel.FAIL
    )


def _default_pass_result(reason: str) -> "VideoConsistencyCheckResult":
    from app.schemas.video_llm import VideoConsistencyCheckResult, VideoConsistencyLevel
    return VideoConsistencyCheckResult(
        passed=True,
        reason_overall=reason,
        first_frame_consistency=VideoConsistencyLevel.N_A,
    )


def _aggregate_first_frame_from_per_character(parsed: "VideoConsistencyCheckResult") -> VideoConsistencyLevel:
    from app.schemas.video_llm import PerCharacterFirstFrameResult
    per_list = getattr(parsed, "per_character_first_frame", None) or []
    worst = VideoConsistencyLevel.GOOD
    for pc in per_list:
        if not isinstance(pc, PerCharacterFirstFrameResult):
            continue
        for attr in (
            "face_consistency",
            "accessories_consistency",
            "clothing_consistency",
            "body_consistency",
            "hair_consistency",
            "framing_consistency",
            "no_new_primary_subjects",
        ):
            v = getattr(pc, attr, None)
            if v is not None and not v.is_pass():
                if v == VideoConsistencyLevel.FAIL:
                    return VideoConsistencyLevel.FAIL
                worst = VideoConsistencyLevel.POOR
    return worst


async def check_video_consistency_llm(
    start_image_url: str,
    video_url: str,
    i2v_prompt: str,
    character_ref_image_urls: Optional[List[str]] = None,
    character_ref_labels: Optional[List[Dict[str, Any]]] = None,
    run_config: Optional[dict] = None,
) -> "VideoConsistencyCheckResult":
    """用 deep-agent + video-consistency-director 判断 I2V 一致性。"""
    if not start_image_url or not video_url:
        return _default_pass_result("无首帧或生成视频，跳过校验")
    try:
        from app.schemas.video_llm import VideoConsistencyCheckResult
        from app.utils.file_utils import prepare_video_for_llm
        from app.services.agent.video.video_consistency_stage import (
            check_video_consistency_via_deep_agent,
            export_video_consistency_inputs,
        )

        ref_urls = list(character_ref_image_urls or [])
        character_ref_labels_text = _format_character_ref_labels_text(character_ref_labels)
        image_urls = [start_image_url, *ref_urls]

        video_content = await prepare_video_for_llm(
            video_url=video_url if "://" in str(video_url) else None,
            video_path=video_url if "://" not in str(video_url) else None,
            mime_type="video/mp4",
            max_wait_time=300,
            max_base64_size_mb=20.0,
        )
        media_parts = [video_content.to_media_content()]

        tid = f"vcons_{uuid.uuid4().hex[:8]}"
        rid = f"run_{uuid.uuid4().hex[:8]}"
        paths = export_video_consistency_inputs(
            thread_id=tid,
            run_id=rid,
            i2v_prompt=i2v_prompt or "",
            character_ref_labels_text=character_ref_labels_text,
            image_urls=image_urls,
        )
        log_video_spin_prompt_trace(
            "video_consistency_check_llm_input",
            text=(
                f"deep_agent video-consistency-director\n"
                f"i2v_prompt_under_review=\n{i2v_prompt or ''}\n"
                f"start_image_url_prefix={(start_image_url or '')[:160]}\n"
                f"video_url_prefix={(video_url or '')[:160]}"
            ),
            log_context=run_config,
        )
        art, _msgs = await check_video_consistency_via_deep_agent(
            thread_id=tid,
            run_id=rid,
            input_paths=paths,
            media_parts=media_parts,
        )
        parsed = VideoConsistencyCheckResult.model_validate(
            art.model_dump(
                mode="json",
                exclude={"schema_version", "artifact", "thread_id", "run_id"},
            )
        )
        update = {"first_frame_consistency": _aggregate_first_frame_from_per_character(parsed)}
        passed = _compute_passed(parsed.model_copy(update=update))
        result = parsed.model_copy(update={**update, "passed": passed})

        log_video_spin_prompt_trace(
            "video_consistency_check_llm_output_summary",
            text=(
                f"passed={result.passed}\n"
                f"first_frame_consistency={getattr(result.first_frame_consistency, 'value', result.first_frame_consistency)}\n"
                f"camera_movement={getattr(result.camera_movement, 'value', result.camera_movement)}\n"
                f"style_consistency={getattr(result.style_consistency, 'value', result.style_consistency)}\n"
                f"severe_abnormality={getattr(result.severe_abnormality, 'value', result.severe_abnormality)}\n"
                f"reason_overall=\n{result.reason_overall or ''}\n"
                f"suggested_prompt=\n{result.suggested_prompt or ''}"
            ),
            log_context=run_config,
        )

        logger.info(
            "🔍 视频一致性校验: passed=%s ffc=%s cam=%s style=%s severe_abn=%s reason_overall=%s has_suggested=%s per_character_first_frame_count=%s",
            result.passed,
            result.first_frame_consistency.value if result.first_frame_consistency else None,
            result.camera_movement.value,
            result.style_consistency.value,
            result.severe_abnormality.value,
            result.reason_overall[:60],
            bool(result.suggested_prompt),
            len(getattr(result, "per_character_first_frame", None) or []),
        )
        return result
    except Exception as e:
        logger.warning("⚠️ 视频一致性校验调用异常，默认视为通过: %s", e)
        return _default_pass_result(f"校验异常，默认通过: {e}")
