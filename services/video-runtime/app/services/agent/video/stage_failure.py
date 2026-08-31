"""阶段失败兜底 — 统一构建「失败暂停」信息、发对话事件、生成失败 interrupt payload。

设计：
- generation 节点检测到失败后，用 build_stage_failure() 聚合 per-item 原因（官方类别，不泄密），
  写入 state["stage_failure"]，并用 emit_stage_failure_event() 在对话里告知用户。
- 对应的 gate 节点读取 state["stage_failure"]，若 stage 匹配且 blocking，用
  build_failure_interrupt_payload() 触发带 disable_auto_resume 的中断（worker 据此停掉 15s 自动继续）。

失败原因一律走 utils.error_classification 的官方类别文案，绝不暴露 raw_error_msg / 厂商 / 模型 / 堆栈。
"""
import logging
from typing import Any, Dict, List, Optional, Callable, Awaitable

from app.utils.error_classification import (
    FailureCategory,
    classify_failure,
    user_facing_reason,
    sanitize_user_facing_reason,
)
from app.utils.i18n import get_i18n_message

logger = logging.getLogger(__name__)

# 任意失败即阻断的阶段（上游公共依赖）
BLOCKING_ANY_FAILURE = {"music", "outline", "character", "character_fusion", "scene", "narration"}
# 逐镜头阶段：有失败即暂停，但支持只重试失败项
BLOCKING_PER_SHOT = {"keyframe", "video"}


def _dominant_category(categories: List[str]) -> FailureCategory:
    """从多个失败类别里挑一个最具代表性的（审核/额度优先于限流/服务）。"""
    if not categories:
        return FailureCategory.UNKNOWN
    priority = [
        FailureCategory.CONTENT_MODERATION,
        FailureCategory.QUOTA_EXCEEDED,
        FailureCategory.RATE_LIMITED,
        FailureCategory.SERVICE_UNAVAILABLE,
        FailureCategory.INVALID_INPUT,
        FailureCategory.UNKNOWN,
    ]
    present = set(categories)
    for cat in priority:
        if cat.value in present:
            return cat
    return FailureCategory.UNKNOWN


def build_stage_failure(
    stage: str,
    *,
    total: int,
    failed_items: List[Dict[str, Any]],
    lang: Optional[str] = None,
) -> Dict[str, Any]:
    """构建 state["stage_failure"] 负载。

    Args:
        stage: 阶段名（music/outline/character/character_fusion/scene/narration/keyframe/video）
        total: 该阶段总条目数
        failed_items: 失败项列表，每项 {index, category} 或 {index, raw_error}；
                      index 可为 shot_number / 角色名 / 1-based 序号
        lang: 语言

    Returns:
        dict 负载，含用户友好的整体 message 与 per-item 原因（均为官方类别，不泄密）
    """
    # 归一化每个失败项的官方类别 + 文案
    normalized: List[Dict[str, Any]] = []
    categories: List[str] = []
    for item in failed_items or []:
        cat = item.get("category")
        if cat is None:
            cat = classify_failure(item.get("raw_error") or item.get("user_msg")).value
        categories.append(cat)
        category_reason = user_facing_reason(category=FailureCategory(cat), lang=lang)
        # 优先用 tool LLM 生成的脱敏 error_msg（user_msg），命中泄露黑名单/为空/过长则回退到官方类别文案
        reason_text = sanitize_user_facing_reason(item.get("user_msg"), fallback=category_reason)
        normalized.append({
            "index": item.get("index"),
            "failure_category": cat,
            "reason": reason_text,
        })

    failed_count = len(normalized)
    dominant = _dominant_category(categories)
    dominant_reason = user_facing_reason(category=dominant, lang=lang)

    message_key = f"generation_failed.stage_blocked.{stage}"
    message = get_i18n_message(
        message_key,
        default=None,
        params={"reason": dominant_reason, "failed": failed_count, "total": total},
        lang=lang,
    )

    return {
        "stage": stage,
        "blocking": True,
        "failure_category": dominant.value,
        "total": total,
        "failed": failed_count,
        "message_key": message_key,
        "message": message,
        "failed_items": normalized,
    }


async def emit_stage_failure_event(
    send_event_func: Callable[..., Awaitable[Any]],
    failure: Dict[str, Any],
    conversation_id: Optional[Any] = None,
) -> None:
    """在对话里发 GENERATION_FAILED 事件，告知用户哪个环节失败、官方原因、失败项明细。

    需传 conversation_id 才能落库（async_send_event 的 save_to_db 依赖它），否则前端读 DB 拿不到失败原因。
    """
    from app.services.agent.base_agent import MessageType

    kwargs: Dict[str, Any] = {}
    if conversation_id is not None:
        kwargs["conversation_id"] = conversation_id

    await send_event_func(
        event_type=MessageType.GENERATION_FAILED,
        message=failure.get("message"),
        **kwargs,
        extra_data={
            "stage": failure.get("stage"),
            "blocking": failure.get("blocking", True),
            "failure_category": failure.get("failure_category"),
            "message_key": failure.get("message_key"),
            "total": failure.get("total"),
            "failed": failure.get("failed"),
            "failed_items": failure.get("failed_items", []),
            "disable_auto_resume": True,
        },
    )


def build_failure_interrupt_payload(failure: Dict[str, Any]) -> Dict[str, Any]:
    """gate 节点据此 interrupt：带 blocking_failure / disable_auto_resume 标记，
    worker 读到后不排 15s 自动继续。"""
    stage = failure.get("stage")
    return {
        "step": f"failed_{stage}",
        "blocking_failure": True,
        "disable_auto_resume": True,
        "stage": stage,
        "failure_category": failure.get("failure_category"),
        "message_key": failure.get("message_key"),
        "message_default": failure.get("message"),
        "failed": failure.get("failed"),
        "total": failure.get("total"),
        "failed_items": failure.get("failed_items", []),
    }


def detect_stage_failure(
    stage: str,
    *,
    total: int,
    failed_items: List[Dict[str, Any]],
    lang: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """统一失败判定：有真实失败项则聚合，否则返回 None。

    用于各 generation 节点：返回非 None 时，节点应发 GENERATION_FAILED 事件并把它写入 state["stage_failure"]。
    """
    if failed_items:
        return build_stage_failure(stage, total=total, failed_items=failed_items, lang=lang)
    return None


def get_blocking_failure_for_stage(
    state: Dict[str, Any],
    stage: str,
) -> Optional[Dict[str, Any]]:
    """gate 节点用：若 state 里有匹配本 stage 且 blocking 的失败，返回之，否则 None。"""
    failure = state.get("stage_failure")
    if not failure or not isinstance(failure, dict):
        return None
    if failure.get("stage") != stage:
        return None
    if not failure.get("blocking"):
        return None
    return failure
