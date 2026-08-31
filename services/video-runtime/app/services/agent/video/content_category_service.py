"""content_category 早判：user_input_analysis 末尾、music 之前，从用户文案推断模板类型。"""
import logging
from typing import Any, Dict, Optional

from langchain_core.messages import SystemMessage, HumanMessage

from ....models.tool_enums import ContentCategory
from ....models.user_options import UserOption
from ....models.video_state import ContentCategoryEarlyOutput
from prompts.prompt_config import PROMPTS_CONFIG, PromptName
from app.orchestration.skills.prompt_context import (
    facts_human_message,
    skill_system_message,
)

logger = logging.getLogger(__name__)


async def infer_content_category_from_user_input(
    user_input: str,
    panel_content_category: Optional[ContentCategory] = None,
    detected_language: Optional[str] = None,
    log_context: Optional[Dict[str, Any]] = None,
) -> ContentCategory:
    """从用户文案推断 content_category；用户意图优先于配置卡片。"""
    from ....services.agent.utils.prompt_utils import apply_language_suffix_to_system_message_in_messages
    from ....services.agent.utils.llm_resilience import StructuredResilienceKind, ainvoke_structured_resilient

    system = skill_system_message(
        "content-category-director",
        lead="Follow content-category-director.",
    )
    facts = {
        "user_input": user_input.strip(),
        "panel_content_category": panel_content_category.value if panel_content_category else None,
        "detected_language": detected_language or "",
    }
    messages = [SystemMessage(content=system), HumanMessage(content=facts_human_message(facts))]
    apply_language_suffix_to_system_message_in_messages(messages, detected_language)

    result = await ainvoke_structured_resilient(
        prompt_entry=PROMPTS_CONFIG[PromptName.VIDEO_CONTENT_CATEGORY_EARLY_INFERENCE],
        kind=StructuredResilienceKind.CREATE_AGENT,
        agent_inputs={"messages": messages},
        log_context=log_context,
    )
    structured = result.get("structured_response")
    if not isinstance(structured, ContentCategoryEarlyOutput):
        logger.warning("early content_category 无 structured_response，回退 panel/default")
        return panel_content_category or ContentCategory.DEFAULT
    return ContentCategory.from_value(structured.content_category)


def _effective_user_input_data(state: Dict[str, Any], result: Optional[Dict[str, Any]]):
    if isinstance(result, dict) and result.get("user_input_data") is not None:
        return result["user_input_data"]
    return state.get("user_input_data")


async def apply_early_content_category_to_result(
    state: Dict[str, Any],
    result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """在 resolve_video_workflow 之前写回 user_option.content_category。"""
    if not isinstance(result, dict):
        result = {}

    user_input_data = _effective_user_input_data(state, result)
    if not user_input_data:
        return result

    user_text = (user_input_data.user_input or "").strip()
    if not user_text:
        return result

    panel_cc = (
        user_input_data.user_option.content_category
        if user_input_data.user_option and user_input_data.user_option.content_category
        else None
    )
    panel_label = panel_cc.value if panel_cc else ContentCategory.DEFAULT.value

    inferred = await infer_content_category_from_user_input(
        user_input=user_text,
        panel_content_category=panel_cc,
        detected_language=state.get("detected_language"),
        log_context={
            "run_id": state.get("run_id"),
            "thread_id": state.get("thread_id"),
            "conversation_id": state.get("conversation_id"),
            "caller": "content_category_early",
            "panel_content_category": panel_label,
        },
    )
    if inferred.value != panel_label:
        logger.info(
            "📋 early content_category: panel=%s → inferred=%s (user_input 优先)",
            panel_label,
            inferred.value,
        )
    else:
        logger.info("📋 early content_category: %s", inferred.value)

    uo = user_input_data.user_option or UserOption.default()
    updated_uo = uo.model_copy(update={"content_category": inferred})
    updated_uid = user_input_data.model_copy(update={"user_option": updated_uo})
    merged = {**result, "user_input_data": updated_uid}

    conv_id = state.get("conversation_id")
    if conv_id is not None and inferred.value != panel_label:
        try:
            from ....crud.conversation import async_merge_conversation_user_option_content_category

            await async_merge_conversation_user_option_content_category(
                int(conv_id),
                inferred.value,
                run_id=str(state.get("run_id") or "") or None,
            )
            logger.info(
                "📋 content_category 已同步 conversations.user_option: %s (panel=%s)",
                inferred.value,
                panel_label,
            )
        except Exception as e:
            logger.warning("content_category 同步 conversations.user_option 失败: %s", e)

    return merged
