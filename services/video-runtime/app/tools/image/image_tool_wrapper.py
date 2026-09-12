"""
图像生成工具 Wrapper — 统一入口。

Chain 在 get_image_generation_tools(user_option) 时选定，通过 create_image_wrapper_tools(mode, user_option)
传入并绑定到工具实例；运行时不再依赖 context.image_generation_tool 选链。

Chain 返回 List[ToolInfo]:
  每个 chain 条目包含完整的 ToolInfo（tool 对象、tool_type、provider 等），
  参考各 tool 模块的 get_xxx_tools() 模式。

I2I 流程:
  截断参考图 → 执行生成 → 可重试错误同模型重试一次 → 降级下一模型。

T2I 流程:
  执行生成 → 可重试错误同模型重试一次 → 降级下一模型。

Metrics:
  每次 wrapper 调用的统计信息（尝试次数、各模型次数、成功率、一致性分布等）
  通过 ImageToolMetrics 写入 LangSmith run metadata。
"""
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Annotated, Any, Dict, TYPE_CHECKING

from pydantic import BaseModel, Field, SkipValidation
from pydantic.json_schema import SkipJsonSchema
from app.tools.runtime import tool
from app.tools.runtime import ToolRuntime

from app.models.image_result import ImageGenerationResult
from app.models.tool_enums import ToolType, ToolMode, DefaultValues, ToolName
from app.tools.context_schemas import ImageGenerationContext
from app.services.agent.video.agent_video_constants import (
    IMAGE_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS,
)

if TYPE_CHECKING:
    from app.services.tool_service import ToolInfo

logger = logging.getLogger(__name__)

# ==================== 依赖导入 ====================

from app.models.user_options import ImageGenerationTool, DEFAULT_IMAGE_TOOL

from app.utils.i18n import get_i18n_message_async
from app.utils.error_classification import (
    classify_failure,
    user_facing_reason_async,
    FailureCategory,
)
from app.tools.image.ref_utils import truncate_reference_urls_for_model
from app.tools.image.seedream import (
    edit_image_with_wavespeed_seedream,
    generate_image_with_wavespeed_seedream_t2i,
)
from app.tools.image.gpt_image_2 import (
    edit_image_with_wavespeed_gpt_image_2,
    generate_image_with_wavespeed_gpt_image_2_t2i,
)
from app.tools.image.nano_banana import (
    generate_image_with_nano_banana_i2i,
    generate_image_with_nano_banana_t2i,
)


# ==================== Metrics（LangSmith metadata） ====================


@dataclass
class ImageToolMetrics:
    """Wrapper 执行统计，写入 LangSmith run metadata；并可 to_dict 写入 result 供落库 version。"""

    total_attempts: int = 0
    per_model_attempts: Dict[str, int] = field(default_factory=dict)
    success: bool = False
    final_model: Optional[str] = None
    failure_reasons: List[str] = field(default_factory=list)
    best_effort_selected: bool = False

    def record_attempt(
        self,
        model: str,
        success: bool,
        failure_reason: Optional[str] = None,
    ):
        self.total_attempts += 1
        self.per_model_attempts[model] = self.per_model_attempts.get(model, 0) + 1
        if failure_reason:
            self.failure_reasons.append(f"{model}:{failure_reason}")
        if success:
            self.success = True
            self.final_model = model

    def to_metadata(self) -> dict:
        return {
            "image_wrapper_metrics": {
                "total_attempts": self.total_attempts,
                "per_model_attempts": dict(self.per_model_attempts),
                "success": self.success,
                "final_model": self.final_model,
                "failure_reasons": list(self.failure_reasons),
                "best_effort_selected": self.best_effort_selected,
            }
        }

    def to_dict(self) -> Dict[str, Any]:
        """供 result.image_tool_metrics 落库 version 使用"""
        return {
            "total_attempts": self.total_attempts,
            "per_model_attempts": dict(self.per_model_attempts),
            "success": self.success,
            "final_model": self.final_model,
            "failure_reasons": list(self.failure_reasons),
            "best_effort_selected": self.best_effort_selected,
        }

    def flush_to_langsmith(self):
        """将 metrics 写入当前 LangSmith run 的 metadata，并 patch 到 API（同步/async 均可用）。"""
        try:
            from langsmith import get_current_run_tree

            run = get_current_run_tree()
            if run:
                run.add_metadata(self.to_metadata())
                run.patch()
        except Exception:
            pass


# ==================== 候选记录 ====================


@dataclass
class _AttemptRecord:
    """单次尝试记录，用于 best-effort 选择"""

    model: str
    attempt_num: int  # 1=首次, 2=重试
    result: ImageGenerationResult
    failure_type: Optional[str] = None

    @property
    def has_image(self) -> bool:
        return bool(self.result.image_url)

# ==================== 工具辅助函数 ====================


def _is_retryable_error(result: ImageGenerationResult) -> bool:
    """判断 API 层面的瞬态错误是否可重试（限流、超时等）。

    Provider 返回的限流或瞬时故障允许重试。
    """
    if result.success:
        return False
    error_msg = (result.raw_error_msg or result.error_msg or "").lower()
    retryable_keywords = [
        "504",
        "deadline_exceeded",
        "rate limit",
        "too many requests",
        "quota",
        "capacity",
        "temporarily unavailable",
        "service unavailable",
        "timeout",
        "暂时不可用",
        "使用量",
        "限流",
    ]
    return any(kw in error_msg for kw in retryable_keywords)


def _pick_best_candidate(candidates: List[_AttemptRecord]) -> Optional[_AttemptRecord]:
    """Return the latest candidate that contains a generated image."""
    if not candidates:
        return None
    with_image = [c for c in candidates if c.has_image]
    if not with_image:
        return candidates[-1]  # 全部 API 失败，返回最后一个
    return with_image[-1]


async def _maybe_add_switch_info(
    result: ImageGenerationResult,
    actual_tool_type: ToolType,
    requested_model: ToolType,
    lang: Optional[str] = None,
) -> ImageGenerationResult:
    """如果发生了模型降级/切换，补充切换信息；文案按 runtime.context.language 做 i18n"""
    if actual_tool_type != requested_model:
        msg = await get_i18n_message_async(
            "image_model_switched.fallback_default",
            default="The requested model was temporarily unavailable; another model was used to complete generation.",
            lang=lang,
        )
        data = result.model_dump()
        data.update(
            model_switched=True,
            requested_model=requested_model.value,
            actual_model=actual_tool_type.value,
            user_facing_message=msg,
        )
        return ImageGenerationResult(**data)
    return result


def _inject_metrics(
    result: ImageGenerationResult,
    metrics: ImageToolMetrics,
    duration_sec: float,
    accumulated_tool_cost: float,
) -> ImageGenerationResult:
    """将 metrics、耗时、成本写入 result，供落库 version 使用。accumulated_tool_cost 为 fallback 多轮尝试的累计成本，与扣积分一致。"""
    data = result.model_dump()
    metric_data = metrics.to_dict()
    if result.applied_skill_ids or result.constraint_coverage or result.final_prompt:
        metric_data["skill_constraint_audit"] = {
            "applied_skill_ids": result.applied_skill_ids,
            "constraint_coverage": result.constraint_coverage,
            "final_prompt": result.final_prompt,
        }
    data["image_tool_metrics"] = metric_data
    data["tool_duration_sec"] = round(duration_sec, 3)
    data["tool_cost"] = accumulated_tool_cost or None
    out = ImageGenerationResult(**data)
    logger.info(
        "[image_wrapper] _inject_metrics 写入: tool_duration_sec=%s, tool_cost=%s",
        out.tool_duration_sec,
        out.tool_cost,
    )
    return out


# ==================== Chain 配置 ====================


def _get_i2i_chain(tool: Optional[ImageGenerationTool]) -> List["ToolInfo"]:
    """按用户选择返回 I2I 尝试顺序。

    规则：fallback 须为主模型能力的超集（如 Flash 不支持 1080p，不能作为 Flash2/Pro/Seedream 的 fallback）。
    返回 List[ToolInfo]，每个条目包含完整的工具信息（tool 对象、tool_type、provider）。
    """
    from app.services.tool_service import ToolInfo
    from app.models.tool_enums import ToolProvider, ToolCategory

    def _i2i(tool_fn, tt: ToolType, prov: ToolProvider) -> ToolInfo:
        # 设置 tool.metadata 供 callback 精准获取 ToolType / Provider
        if hasattr(tool_fn, "metadata"):
            if tool_fn.metadata is None:
                tool_fn.metadata = {}
            tool_fn.metadata.update(
                tool_type=tt.value,
                provider=prov.value,
                category=ToolCategory.IMAGE_GENERATION.value,
            )
        return ToolInfo(
            tool=tool_fn,
            tool_name=tool_fn.name,
            tool_type=tt,
            provider=prov,
            category=ToolCategory.IMAGE_GENERATION,
            mode=ToolMode.I2I,
        )

    seedream = _i2i(edit_image_with_wavespeed_seedream, ToolType.SEEDREAM_V4_5, ToolProvider.WAVESPEED)
    gpt2     = _i2i(edit_image_with_wavespeed_gpt_image_2, ToolType.GPT_IMAGE_2, ToolProvider.WAVESPEED)
    flash    = _i2i(generate_image_with_nano_banana_i2i, ToolType.GEMINI_2_5_FLASH_IMAGE, ToolProvider.GOOGLE)
    flash2   = _i2i(generate_image_with_nano_banana_i2i, ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW, ToolProvider.GOOGLE)
    pro      = _i2i(generate_image_with_nano_banana_i2i, ToolType.GEMINI_3_PRO_IMAGE_PREVIEW, ToolProvider.GOOGLE)

    if tool == ImageGenerationTool.NANO_BANANA:
        return [flash, flash2, seedream]
    elif tool == ImageGenerationTool.NANO_BANANA_2:
        # 用户选 Banana 2：banana 2 -> pro -> seedream（flash 不支持 1080p，不能作 fallback）
        return [flash2, pro, seedream]
    elif tool == ImageGenerationTool.SEEDREAM:
        return [seedream, flash2, pro]   # fallback 时 2 优先于 pro
    elif tool == ImageGenerationTool.GPT_IMAGE_2:
        return [gpt2, flash2, pro, seedream]
    elif tool == ImageGenerationTool.NANO_BANANA_PRO:
        # 用户选 Pro：pro -> banana 2 -> seedream（flash 不支持 1080p，不能作 fallback）
        return [pro, flash2, seedream]
    else:
        # None / 未知：优先 banana 2 -> pro -> flash -> seedream
        return [flash2, pro, seedream]


def _get_t2i_chain(tool: Optional[ImageGenerationTool]) -> List["ToolInfo"]:
    """按用户选择返回 T2I 尝试顺序。

    规则：fallback 须为主模型能力的超集（Flash 不支持 1080p，不能作为 Flash2/Pro/Seedream 的 fallback）。
    返回 List[ToolInfo]，每个条目包含完整的工具信息（tool 对象、tool_type、provider）。
    """
    from app.services.tool_service import ToolInfo
    from app.models.tool_enums import ToolProvider, ToolCategory

    def _t2i(tool_fn, tt: ToolType, prov: ToolProvider) -> ToolInfo:
        # 设置 tool.metadata 供 callback 精准获取 ToolType / Provider
        if hasattr(tool_fn, "metadata"):
            if tool_fn.metadata is None:
                tool_fn.metadata = {}
            tool_fn.metadata.update(
                tool_type=tt.value,
                provider=prov.value,
                category=ToolCategory.IMAGE_GENERATION.value,
            )
        return ToolInfo(
            tool=tool_fn,
            tool_name=tool_fn.name,
            tool_type=tt,
            provider=prov,
            category=ToolCategory.IMAGE_GENERATION,
            mode=ToolMode.T2I,
        )

    seedream = _t2i(generate_image_with_wavespeed_seedream_t2i, ToolType.SEEDREAM_V4_5, ToolProvider.WAVESPEED)
    gpt2     = _t2i(generate_image_with_wavespeed_gpt_image_2_t2i, ToolType.GPT_IMAGE_2, ToolProvider.WAVESPEED)
    flash    = _t2i(generate_image_with_nano_banana_t2i, ToolType.GEMINI_2_5_FLASH_IMAGE, ToolProvider.GOOGLE)
    flash2   = _t2i(generate_image_with_nano_banana_t2i, ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW, ToolProvider.GOOGLE)
    pro      = _t2i(generate_image_with_nano_banana_t2i, ToolType.GEMINI_3_PRO_IMAGE_PREVIEW, ToolProvider.GOOGLE)

    if tool == ImageGenerationTool.NANO_BANANA:
        return [flash, flash2, seedream]
    elif tool == ImageGenerationTool.NANO_BANANA_2:
        # flash 不支持 1080p，不能作 Flash2 的 fallback
        return [flash2, pro, seedream]
    elif tool == ImageGenerationTool.SEEDREAM:
        return [seedream, flash2, pro]   # flash 不支持 1080p，不能作 fallback
    elif tool == ImageGenerationTool.GPT_IMAGE_2:
        return [gpt2, flash2, pro, seedream]
    elif tool == ImageGenerationTool.NANO_BANANA_PRO:
        return [pro, flash2, seedream]   # flash 不支持 1080p，不能作 fallback
    else:
        return [flash2, pro, seedream]   # 默认 Banana 2；flash 不能作 fallback


# ==================== I2I 核心逻辑 ====================


async def _invoke_i2i_tool(
    prompt: str,
    tool_info: "ToolInfo",
    urls: List[str],
    runtime: ToolRuntime[ImageGenerationContext],
) -> ImageGenerationResult:
    """调底层 I2I tool，纯执行不含一致性校验。

    Seedream / GPT Image 2 与 Nano Banana 的参考图参数名不同：
      - SEEDREAM_V4_5, GPT_IMAGE_2: images
      - GEMINI_*: reference_image_urls
    """
    from app.orchestration.skills.constraint_contract import (
        active_constraint_contract,
        merge_and_validate_prompt,
    )

    audit = merge_and_validate_prompt(prompt, active_constraint_contract())
    if tool_info.tool_type in (ToolType.SEEDREAM_V4_5, ToolType.GPT_IMAGE_2):
        result = await tool_info.tool.ainvoke(
            {"prompt": audit.final_prompt, "images": urls, "runtime": runtime}
        )
    else:
        result = await tool_info.tool.ainvoke(
            {"prompt": audit.final_prompt, "reference_image_urls": urls, "runtime": runtime}
        )
    result.applied_skill_ids = audit.applied_skill_ids
    result.constraint_coverage = audit.constraint_coverage
    result.final_prompt = audit.final_prompt
    result.generated_prompt = audit.final_prompt
    return result


async def _run_i2i_attempt(
    prompt: str,
    tool_info: "ToolInfo",
    urls: List[str],
    runtime: ToolRuntime[ImageGenerationContext],
) -> ImageGenerationResult:
    """Run one I2I provider attempt."""
    return await _invoke_i2i_tool(prompt, tool_info, urls, runtime)


async def _run_i2i_loop(
    prompt: str,
    reference_image_urls: List[str],
    runtime: ToolRuntime[ImageGenerationContext],
    chain: List["ToolInfo"],
) -> ImageGenerationResult:
    """I2I fallback chain with provider retries.

    每个模型最多 2 次尝试（首次 + 重试），但整次调用内实际图生图 API 总次数不超过
    IMAGE_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS（与 video wrapper 总尝试上限语义一致）。
    全部失败时，返回最后一个仍包含图片的候选。
    """
    requested_model = runtime.context.model or DefaultValues.IMAGE_MODEL
    _lang = getattr(runtime.context, "language", None) if runtime and runtime.context else None
    if not runtime.context.reference_image_urls:
        runtime.context.reference_image_urls = reference_image_urls

    start_time = time.perf_counter()
    metrics = ImageToolMetrics()
    candidates: List[_AttemptRecord] = []
    accumulated_tool_cost = 0.0
    total_generation_attempts = 0

    for info in chain:
        # 准备参考图（截断 + 写回 runtime）
        base_refs = runtime.context.reference_image_urls or []
        urls = truncate_reference_urls_for_model(base_refs, info.tool_type) or []
        runtime.context.reference_image_urls = urls
        runtime.context.model = info.tool_type

        for attempt_num in (1, 2):
            if total_generation_attempts >= IMAGE_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS:
                logger.info(
                    "🛑 [I2I] 已达最大生成尝试次数 (%d)，停止",
                    IMAGE_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS,
                )
                break
            total_generation_attempts += 1
            if attempt_num == 1:
                logger.info("🔄 [I2I] 尝试模型: %s", info.tool_type.value)
            else:
                logger.info("🔄 [I2I] 同模型重试: %s", info.tool_type.value)

            result = await _run_i2i_attempt(
                prompt, info, urls, runtime
            )
            accumulated_tool_cost += float(getattr(result, "billing_cost", None) or 0.0)

            # ---- API 失败 ----
            if not result.success:
                metrics.record_attempt(
                    info.tool_type.value, False, failure_reason="api_error"
                )
                candidates.append(
                    _AttemptRecord(
                        model=info.tool_type.value,
                        attempt_num=attempt_num,
                        result=result,
                        failure_type="api_error",
                    )
                )
                if _is_retryable_error(result) and attempt_num == 1:
                    continue  # 可重试 → 再来一次
                break  # 不可重试 / 已重试 → 换模型

            metrics.record_attempt(info.tool_type.value, True)
            metrics.flush_to_langsmith()
            out = _inject_metrics(
                result, metrics, time.perf_counter() - start_time,
                accumulated_tool_cost,
            )
            return await _maybe_add_switch_info(
                out, info.tool_type, requested_model, lang=_lang,
            )

        if total_generation_attempts >= IMAGE_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS:
            break

    # ---- 所有模型均未能通过 → best-effort 选择 ----
    best = _pick_best_candidate(candidates)
    if best and best.has_image:
        metrics.best_effort_selected = True
        metrics.success = True
        metrics.final_model = best.model
        metrics.flush_to_langsmith()
        logger.warning("⚠️ [I2I] provider fallback selected candidate model=%s", best.model)
        data = best.result.model_dump()
        data.update(
            requested_model=requested_model.value,
            user_facing_message=await get_i18n_message_async(
                "image_model_switched.best_effort",
                default="The last usable provider result was selected.",
                lang=_lang,
            ),
        )
        result = ImageGenerationResult(**data)
        result = _inject_metrics(result, metrics, time.perf_counter() - start_time, accumulated_tool_cost)
        logger.info("[image_wrapper] I2I 返回(best_effort): tool_duration_sec=%s, tool_cost=%s", result.tool_duration_sec, result.tool_cost)
        try:
            actual = ToolType(best.model)
        except ValueError:
            actual = requested_model
        return await _maybe_add_switch_info(result, actual, requested_model, lang=_lang)

    # ---- 真的全部 API 失败，无可用图片 ----
    metrics.flush_to_langsmith()
    logger.error("❌ [I2I] 所有模型均失败，无可用候选")
    last_result = (
        candidates[-1].result
        if candidates
        else ImageGenerationResult(success=False, error_msg="所有模型均失败")
    )
    data = last_result.model_dump()
    _category = classify_failure(last_result.raw_error_msg or last_result.error_msg)
    if _category in (FailureCategory.SERVICE_UNAVAILABLE, FailureCategory.UNKNOWN):
        all_unavailable_msg = await get_i18n_message_async(
            "image_model_switched.all_unavailable",
            default="All image generation models are temporarily unavailable. Please try again later.",
            lang=_lang,
        )
    else:
        all_unavailable_msg = await user_facing_reason_async(category=_category, lang=_lang)
    data.update(
        requested_model=requested_model.value,
        user_facing_message=all_unavailable_msg,
        failure_category=_category.value,
    )
    result = ImageGenerationResult(**data)
    return _inject_metrics(result, metrics, time.perf_counter() - start_time, accumulated_tool_cost)


# ==================== T2I 核心逻辑 ====================


async def _run_t2i_one(
    prompt: str,
    tool_info: "ToolInfo",
    runtime: ToolRuntime[ImageGenerationContext],
) -> ImageGenerationResult:
    """单模型 T2I 执行一次。

    所有 T2I 工具共享相同的参数（prompt / runtime），直接通过 tool_info.tool 调用。
    """
    from app.orchestration.skills.constraint_contract import (
        active_constraint_contract,
        merge_and_validate_prompt,
    )

    audit = merge_and_validate_prompt(prompt, active_constraint_contract())
    runtime.context.model = tool_info.tool_type
    result = await tool_info.tool.ainvoke(
        {"prompt": audit.final_prompt, "runtime": runtime}
    )
    result.applied_skill_ids = audit.applied_skill_ids
    result.constraint_coverage = audit.constraint_coverage
    result.final_prompt = audit.final_prompt
    result.generated_prompt = audit.final_prompt
    return result


async def _run_t2i_loop(
    prompt: str,
    runtime: ToolRuntime[ImageGenerationContext],
    chain: List["ToolInfo"],
) -> ImageGenerationResult:
    """T2I fallback chain — 无一致性校验，仅 API 层面重试 + 降级；总文生图次数上限见 IMAGE_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS。"""
    requested_model = runtime.context.model or DefaultValues.IMAGE_MODEL
    _lang = getattr(runtime.context, "language", None) if runtime and runtime.context else None
    start_time = time.perf_counter()
    metrics = ImageToolMetrics()
    last_error_result: Optional[ImageGenerationResult] = None
    accumulated_tool_cost = 0.0
    total_generation_attempts = 0

    for info in chain:
        for attempt_num in (1, 2):
            if total_generation_attempts >= IMAGE_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS:
                logger.info(
                    "🛑 [T2I] 已达最大生成尝试次数 (%d)，停止",
                    IMAGE_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS,
                )
                break
            total_generation_attempts += 1
            if attempt_num == 1:
                logger.info("🔄 [T2I] 尝试模型: %s", info.tool_type.value)
            else:
                logger.info("🔄 [T2I] 重试: %s", info.tool_type.value)

            result = await _run_t2i_one(prompt, info, runtime)
            accumulated_tool_cost += float(getattr(result, "billing_cost", None) or 0.0)
            if result.success:
                metrics.record_attempt(info.tool_type.value, True)
                metrics.flush_to_langsmith()
                logger.info("✅ [T2I] 成功: %s", info.tool_type.value)
                out = _inject_metrics(result, metrics, time.perf_counter() - start_time, accumulated_tool_cost)
                logger.info("[image_wrapper] T2I 返回(成功): tool_duration_sec=%s, tool_cost=%s", out.tool_duration_sec, out.tool_cost)
                return await _maybe_add_switch_info(out, info.tool_type, requested_model, lang=_lang)

            metrics.record_attempt(
                info.tool_type.value, False, failure_reason="api_error"
            )
            last_error_result = result

            if _is_retryable_error(result) and attempt_num == 1:
                continue  # 可重试 → 再来一次
            break  # 不可重试 / 已重试 → 换模型

        if total_generation_attempts >= IMAGE_WRAPPER_MAX_TOTAL_GENERATION_ATTEMPTS:
            break

    metrics.flush_to_langsmith()
    logger.error("❌ [T2I] 所有模型均失败")
    last = last_error_result or ImageGenerationResult(success=False, error_msg="所有模型均失败")
    data = last.model_dump()
    _category = classify_failure(last.raw_error_msg or last.error_msg)
    if _category in (FailureCategory.SERVICE_UNAVAILABLE, FailureCategory.UNKNOWN):
        t2i_unavailable_msg = await get_i18n_message_async(
            "image_model_switched.all_unavailable",
            default="All image generation models are temporarily unavailable. Please try again later.",
            lang=_lang,
        )
    else:
        t2i_unavailable_msg = await user_facing_reason_async(category=_category, lang=_lang)
    data.update(
        requested_model=requested_model.value,
        user_facing_message=t2i_unavailable_msg,
        failure_category=_category.value,
    )
    result = ImageGenerationResult(**data)
    return _inject_metrics(result, metrics, time.perf_counter() - start_time, accumulated_tool_cost)


# ==================== Wrapper Tool (T2I) ====================


class ImageWrapperT2IInput(BaseModel):
    """Input schema for image generation wrapper T2I."""

    prompt: str = Field(
        description="Detailed image description prompt. Describe the image content, style, composition, character appearance, scene environment, artistic style, etc. Be specific and detailed for better results."
    )
    runtime: Annotated[Any, SkipValidation, SkipJsonSchema()] = Field(
        default=None,
        description="Provider runtime context (internal use only)",
    )


def _make_t2i_tool(chain_t2i: List["ToolInfo"]):
    """根据已选定的 chain 创建 T2I 工具"""
    assert chain_t2i, "chain_t2i must be non-empty"

    @tool(ToolName.IMAGE_WRAPPER_T2I, args_schema=ImageWrapperT2IInput, response_format="content_and_artifact")
    async def generate_image_with_fallback_t2i(
        prompt: str,
        runtime: ToolRuntime[ImageGenerationContext],
    ) -> tuple[str, ImageGenerationResult]:
        """Professional image generation tool with automatic fallback - T2I text-to-image.
        Chain (Pro/Flash/Seedream) is chosen at tool creation from user_option; retries once per model on retryable errors.
        """
        result = await _run_t2i_loop(prompt, runtime, chain_t2i)
        return (result.model_dump_json(), result)

    return generate_image_with_fallback_t2i


# ==================== Wrapper Tool (I2I) ====================


class ImageWrapperI2IInput(BaseModel):
    """Input schema for image generation wrapper I2I."""

    prompt: str = Field(
        description="Image generation prompt based on reference images.",
    )
    reference_image_urls: List[str] = Field(
        description="List of reference image URLs for image editing or style reference.",
    )
    runtime: Annotated[Any, SkipValidation, SkipJsonSchema()] = Field(
        default=None,
        description="Provider runtime context (internal use only)",
    )


def _make_i2i_tool(chain_i2i: List["ToolInfo"]):
    """根据已选定的 chain 创建 I2I 工具"""
    assert chain_i2i, "chain_i2i must be non-empty"

    @tool(ToolName.IMAGE_WRAPPER_I2I, args_schema=ImageWrapperI2IInput, response_format="content_and_artifact")
    async def generate_image_with_fallback_i2i(
        prompt: str,
        reference_image_urls: List[str],
        runtime: ToolRuntime[ImageGenerationContext],
    ) -> tuple[str, ImageGenerationResult]:
        """Professional image generation tool with automatic fallback - I2I image-to-image.
        Chain is chosen at tool creation from user_option; each step truncates references, executes the provider, and retries transient failures once.
        When all models fail, the last usable provider candidate is returned.
        """
        result = await _run_i2i_loop(prompt, reference_image_urls, runtime, chain_i2i)
        return (result.model_dump_json(), result)

    return generate_image_with_fallback_i2i


# ==================== 工具创建函数 ====================


def create_image_wrapper_tools(
    mode: Optional[ToolMode] = None,
    user_option: Optional[Any] = None,
) -> List["ToolInfo"]:
    """创建带 fallback 的图像生成工具 Wrapper。

    Chain（Pro/Flash/Seedream 尝试顺序）在此时根据 user_option 选定并绑定到工具实例，
    由 get_image_generation_tools(user_option, mode) 调用时传入 user_option。

    Chain 返回 List[ToolInfo]，provider/tool_type 等直接从 chain[0] 获取，不再硬编码。

    Args:
        mode: 工具模式，ToolMode.T2I 或 ToolMode.I2I，None 为返回 T2I + I2I 两个工具
        user_option: 用户选项，用于决定尝试链；None 时使用默认链（nano_banana_2）

    Returns:
        List[ToolInfo]: Wrapper 工具信息列表
    """
    from app.models.tool_enums import ToolCategory
    from app.services.tool_service import ToolInfo

    image_tool = user_option.image_generation_tool if user_option else None
    if image_tool == ImageGenerationTool.AUTO:
        image_tool = DEFAULT_IMAGE_TOOL
    chain_t2i = _get_t2i_chain(image_tool)
    chain_i2i = _get_i2i_chain(image_tool)

    tools: List[ToolInfo] = []

    if mode != ToolMode.I2I:
        t2i_tool = _make_t2i_tool(chain_t2i)
        primary_t2i = chain_t2i[0]
        t2i_tool_info = ToolInfo(
            tool=t2i_tool,
            tool_name=t2i_tool.name,
            tool_type=primary_t2i.tool_type,
            provider=primary_t2i.provider,
            category=ToolCategory.IMAGE_GENERATION,
            mode=ToolMode.T2I,
        )
        tools.append(t2i_tool_info)

    if mode != ToolMode.T2I:
        i2i_tool = _make_i2i_tool(chain_i2i)
        primary_i2i = chain_i2i[0]
        i2i_tool_info = ToolInfo(
            tool=i2i_tool,
            tool_name=i2i_tool.name,
            tool_type=primary_i2i.tool_type,
            provider=primary_i2i.provider,
            category=ToolCategory.IMAGE_GENERATION,
            mode=ToolMode.I2I,
        )
        tools.append(i2i_tool_info)

    # 添加 metadata 供 callback 使用
    for info in tools:
        if hasattr(info.tool, "metadata"):
            if info.tool.metadata is None:
                info.tool.metadata = {}
            info.tool.metadata.update(
                tool_type=info.tool_type.value,
                provider=info.provider.value,
                category=ToolCategory.IMAGE_GENERATION.value,
            )

    return tools


# 模块级默认工具（供直接 import 或测试用，使用默认 chain）
generate_image_with_fallback_t2i = _make_t2i_tool(_get_t2i_chain(None))
generate_image_with_fallback_i2i = _make_i2i_tool(_get_i2i_chain(None))
