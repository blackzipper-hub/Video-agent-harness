"""
积分检查Callback Handler
在LLM和Tool调用前检查积分，如果积分不足则抛出异常停止执行
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, List
from uuid import UUID
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult, ChatGeneration
from langchain_core.messages import AIMessage

from ..exceptions import BusinessException, BusinessExceptionCode
from ..services.user_service import UserService
from ..services.tool_service import CREDITS_PER_DOLLAR, ToolService
from ..models.tool_enums import ToolType, ToolCategory, LLMModel, TOOL_TYPE_TO_CATEGORY

logger = logging.getLogger(__name__)


@dataclass
class TokenUsage:
    """Token 使用信息"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    audio_input_tokens: int = 0
    cached_audio_tokens: int = 0
    
    def to_dict(self) -> Dict[str, int]:
        """转换为字典，用于传递给 ToolService.calculate_cost"""
        return {
            'prompt_tokens': self.prompt_tokens,
            'completion_tokens': self.completion_tokens,
            'cached_tokens': self.cached_tokens,
            'audio_input_tokens': self.audio_input_tokens,
            'cached_audio_tokens': self.cached_audio_tokens
        }


class CreditCheckCallbackHandler(AsyncCallbackHandler):
    """积分检查Callback Handler
    
    逻辑：
    1. 在 on_llm_end 和 on_tool_end 时计算成本并累加
    2. 在 on_llm_start 和 on_tool_start 时检查累计成本，如果余额不足则抛出异常
    """
    
    def __init__(
        self, 
        user_id: str, 
        action: str = "video_generation",
        db=None
    ):
        """
        初始化积分检查Callback
        
        Args:
            user_id: 用户ID
            action: 操作类型（用于记录，如 "video_generation", "regenerate_keyframes"）
            db: 数据库会话（已废弃，不再使用，每次检查时按需创建）
        """
        super().__init__()
        self.user_id = user_id
        self.action = action
        self._llm_cost: float = 0.0           # LLM token 成本（美元）
        self._tool_cost: float = 0.0          # Tool API 成本（美元）
        self._image_success_count: int = 0    # 成功生成的图片张数（供 image agent 固定积分扣除用）

    @property
    def total_cost(self) -> float:
        """返回当前累计总成本（美元），供 task_worker 在任务结束时读取"""
        return self._llm_cost + self._tool_cost

    @property
    def llm_cost(self) -> float:
        """LLM token 成本（美元）"""
        return self._llm_cost

    @property
    def tool_cost(self) -> float:
        """Tool API 成本（美元）"""
        return self._tool_cost

    @property
    def image_success_count(self) -> int:
        """成功生成的图片张数，供 image agent 按固定 10 积分/张扣除"""
        return self._image_success_count

    def add_tool_cost(self, cost: float, tool_name: str = "", tool_type=None) -> None:
        """由 tool 内部在 calculate_cost 之后直接调用，累加 tool 成本。
        
        同时处理图片计数，避免 on_tool_end 再次解析 ToolMessage 字符串。
        """
        if cost <= 0:
            return
        self._tool_cost += cost
        logger.debug(
            "Tool成本累加(direct): tool_name=%s, cost=$%.6f, tool_cost=$%.6f, total=$%.6f",
            tool_name, cost, self._tool_cost, self.total_cost,
        )
        if tool_type is not None and TOOL_TYPE_TO_CATEGORY.get(tool_type) == ToolCategory.IMAGE_GENERATION:
            self._image_success_count += 1
            logger.debug("图片生成成功计数(direct): tool_name=%s, count=%d", tool_name, self._image_success_count)

    def get_cost_summary(self) -> Dict[str, float]:
        """返回成本明细，供写入 LangSmith metadata"""
        return {
            "callback_total_cost_usd": round(self.total_cost, 6),
            "callback_llm_cost_usd": round(self._llm_cost, 6),
            "callback_tool_cost_usd": round(self._tool_cost, 6),
        }

    def write_metadata_to_langsmith(self, run_id: str) -> None:
        """将成本明细写入 LangSmith run metadata（同步，fire-and-forget）。
        
        在 task_worker finally 里调用，用于在 LangSmith 上对比 callback 成本 vs LangSmith 自身统计成本。
        """
        try:
            from langsmith import Client
            client = Client()
            # update_run 会强制附加 end_time=now，导致已结束的 run 409。
            # 直接走底层 _update_run_non_batch，只 PATCH extra.metadata，不带 end_time。
            client._update_run_non_batch({"id": run_id, "extra": {"metadata": self.get_cost_summary()}})
            logger.debug(
                "📊 callback 成本写入 LangSmith metadata: run_id=%s, %s",
                run_id, self.get_cost_summary(),
            )
        except Exception as e:
            logger.warning("写入 LangSmith metadata 失败（不影响主流程）: run_id=%s, error=%s", run_id, e)

    async def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """LLM调用前检查积分"""
        pass
        # await self._check_credits("llm")
    
    async def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input: str,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Tool调用前检查积分"""
        pass
        # await self._check_credits("tool")
    
    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """LLM调用结束后计算成本并累加"""
        try:
            # 1. 提取模型名称
            model_name = self._get_model_name(response, **kwargs)
            if not model_name:
                logger.warning(f"无法识别模型，跳过成本计算: run_id={run_id}")
                return

            # 2. 将模型名称映射为 LLMModel enum（支持带日期后缀的名称，如 gpt-4.1-mini-2025-04-14）
            llm_model = self._resolve_llm_model(model_name)
            if llm_model is None:
                logger.error(f"不支持的模型: {model_name}，支持的模型: {[m.value for m in LLMModel]}, run_id={run_id}")
                return

            # 3. 根据模型类型提取 token 使用量
            token_usage = self._extract_token_usage_by_model(llm_model, response)

            # 4. 计算成本（使用统一的 calculate_cost 方法）
            cost = ToolService.calculate_cost(
                cost_type=llm_model,
                **token_usage.to_dict()
            )

            # 5. 累加成本
            if cost > 0:
                self._llm_cost += cost
                logger.debug(
                    f"LLM成本累加: model={model_name}, cost=${cost:.6f}, "
                    f"llm_cost=${self._llm_cost:.6f}, total=${self.total_cost:.6f}, run_id={run_id}"
                )
        except Exception as e:
            logger.error(f"计算LLM成本失败: {e}", exc_info=True)
            # 计算失败不阻止执行，但记录日志
    
    async def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> None:
        """Tool cost 已由 tool 内部通过 add_tool_cost() 直接累加，此处无需处理。"""
        pass
    
    async def _check_credits(self, operation_type: str) -> None:
        """
        检查积分是否足够（基于累计成本）
        
        Args:
            operation_type: 操作类型（"llm" 或 "tool"），用于日志
        """
        try:
            # 1. 计算需要的积分
            required_credits = int(self.total_cost * CREDITS_PER_DOLLAR)
            
            # 2. 获取用户积分余额（使用Redis缓存减少数据库查询）
            balance = await self._get_user_balance_cached()
            
            if balance is None:
                # 缓存未命中，从数据库查询
                from ..models.database import AsyncSessionLocal
                async with AsyncSessionLocal() as async_db:
                    user_service = UserService(async_db)
                    user_credit = await user_service.get_user_credits(self.user_id)
                    
                    if not user_credit:
                        logger.warning(f"用户积分账户不存在: user_id={self.user_id}")
                        raise BusinessException(
                            BusinessExceptionCode.RESOURCE_NOT_FOUND,
                            "用户积分账户不存在"
                        )
                    
                    balance = user_credit.balance
                    # 更新缓存（缓存5秒，避免频繁查询数据库）
                    await self._cache_user_balance(balance, ttl=5)
            
            # 3. 检查积分是否足够
            if balance < required_credits:
                logger.warning(
                    f"积分不足，停止执行: user_id={self.user_id}, "
                    f"需要={required_credits}, 余额={balance}, "
                    f"总成本=${self.total_cost:.6f} (llm=${self._llm_cost:.6f}, tool=${self._tool_cost:.6f}), operation={operation_type}"
                )
                raise BusinessException(
                    BusinessExceptionCode.INSUFFICIENT_CREDITS,
                    f"积分不足，当前余额: {balance}，需要: {required_credits}（累计成本: ${self.total_cost:.6f}）"
                )
            
            logger.debug(
                f"✅ 积分检查通过: user_id={self.user_id}, "
                f"需要={required_credits}, 余额={balance}, "
                f"总成本=${self.total_cost:.6f} (llm=${self._llm_cost:.6f}, tool=${self._tool_cost:.6f}), operation={operation_type}"
            )
            
        except BusinessException:
            raise
        except Exception as e:
            logger.error(f"检查积分失败（{operation_type}）: {e}", exc_info=True)
            # 检查失败不阻止执行，但记录日志
    
    async def _get_user_balance_cached(self) -> Optional[int]:
        """从Redis缓存获取用户积分余额
        
        Returns:
            积分余额，如果缓存未命中返回None
        """
        try:
            from ..services.redis.connection import get_redis_client
            redis_client = await get_redis_client(decode_responses=True)
            
            cache_key = f"user_credit_balance:{self.user_id}"
            cached_balance = await redis_client.get(cache_key)
            
            if cached_balance is not None:
                try:
                    return int(cached_balance)
                except (ValueError, TypeError):
                    logger.warning(f"缓存中的积分余额格式无效: {cached_balance}")
                    return None
        except Exception as e:
            logger.debug(f"从Redis获取积分余额缓存失败: {e}")
            # 缓存失败不影响功能，返回None让代码从数据库查询
            return None
        
        return None
    
    async def _cache_user_balance(self, balance: int, ttl: int = 5) -> None:
        """缓存用户积分余额到Redis
        
        Args:
            balance: 积分余额
            ttl: 缓存过期时间（秒），默认5秒
        """
        try:
            from ..services.redis.connection import get_redis_client
            redis_client = await get_redis_client(decode_responses=True)
            
            cache_key = f"user_credit_balance:{self.user_id}"
            await redis_client.setex(cache_key, ttl, str(balance))
        except Exception as e:
            logger.debug(f"缓存积分余额到Redis失败: {e}")
            # 缓存失败不影响功能，忽略错误
    
    def _get_model_name(self, response: LLMResult, **kwargs) -> Optional[str]:
        """从 response 中提取模型名称（标准方式，参考 UsageMetadataCallbackHandler）"""
        try:
            generation = response.generations[0][0]
            if isinstance(generation, ChatGeneration):
                message = generation.message
                if isinstance(message, AIMessage):
                    model_name = message.response_metadata.get("model_name")
                    if model_name:
                        return model_name
        except (IndexError, AttributeError, KeyError) as e:
            logger.debug(f"无法从 AIMessage.response_metadata 提取模型名称: {e}")
        
        return None

    def _resolve_llm_model(self, model_name: str) -> Optional[LLMModel]:
        """将 response_metadata.model_name 映射到 LLMModel enum
        
        OpenAI 返回带日期后缀的完整名，如 gpt-4.1-mini-2025-04-14，需要前缀匹配。
        Gemini 返回 gemini-2.5-flash，直接精确匹配。
        """
        if not model_name:
            return None
        # 优先精确匹配
        for m in LLMModel:
            if model_name == m.value:
                return m
        # 前缀匹配（处理 gpt-4.1-mini-2025-04-14 等带日期后缀的名称）
        for m in LLMModel:
            if model_name.startswith(m.value):
                return m
        return None
    
    def _extract_token_usage_by_model(self, llm_model: LLMModel, response: LLMResult) -> TokenUsage:
        """根据模型类型提取 token 使用量
        
        Args:
            llm_model: LLMModel enum
            response: LLMResult 对象
            
        Returns:
            TokenUsage 对象
        """
        if llm_model in [LLMModel.GEMINI_2_5_FLASH, LLMModel.GEMINI_2_0_FLASH, LLMModel.GEMINI_3_PRO_PREVIEW]:
            return self._extract_gemini_token_usage(response)
        elif llm_model in [LLMModel.GPT_4_1_MINI, LLMModel.GPT_5_NANO]:
            return self._extract_openai_token_usage(response)
        else:
            logger.warning(f"未实现的模型 token 提取: {llm_model}")
            return TokenUsage()
    
    def _extract_gemini_token_usage(self, response: LLMResult) -> TokenUsage:
        """从 Gemini 模型的 LLMResult 中提取 token 使用量"""
        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        audio_input_tokens = 0
        cached_audio_tokens = 0
        
        try:
            generation = response.generations[0][0]
            if isinstance(generation, ChatGeneration):
                message = generation.message
                if isinstance(message, AIMessage) and message.usage_metadata:
                    usage_metadata = message.usage_metadata
                    # UsageMetadata 是 TypedDict（本质是 dict），必须用 .get() 而非 getattr
                    prompt_tokens = usage_metadata.get('input_tokens', 0) or 0
                    completion_tokens = usage_metadata.get('output_tokens', 0) or 0
                    
                    # input_token_details 也是 TypedDict（本质是 dict）
                    input_details = usage_metadata.get('input_token_details')
                    if input_details:
                        cached_tokens = input_details.get('cache_read', 0) or 0
                        audio_input_tokens = input_details.get('audio', 0) or 0
        except (IndexError, AttributeError, KeyError) as e:
            logger.warning(f"无法从 Gemini AIMessage.usage_metadata 提取 token 信息: {e}")
        
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            audio_input_tokens=audio_input_tokens,
            cached_audio_tokens=cached_audio_tokens
        )
    
    def _extract_openai_token_usage(self, response: LLMResult) -> TokenUsage:
        """从 OpenAI 模型的 LLMResult 中提取 token 使用量"""
        prompt_tokens = 0
        completion_tokens = 0
        cached_tokens = 0
        audio_input_tokens = 0
        cached_audio_tokens = 0
        
        try:
            generation = response.generations[0][0]
            if isinstance(generation, ChatGeneration):
                message = generation.message
                if isinstance(message, AIMessage) and message.usage_metadata:
                    usage_metadata = message.usage_metadata
                    # UsageMetadata 是 TypedDict（本质是 dict），必须用 .get() 而非 getattr
                    prompt_tokens = usage_metadata.get('input_tokens', 0) or 0
                    completion_tokens = usage_metadata.get('output_tokens', 0) or 0
                    
                    # input_token_details 也是 TypedDict（本质是 dict）
                    input_details = usage_metadata.get('input_token_details')
                    if input_details:
                        cached_tokens = input_details.get('cache_read', 0) or 0
        except (IndexError, AttributeError, KeyError) as e:
            logger.warning(f"无法从 OpenAI AIMessage.usage_metadata 提取 token 信息: {e}")
        
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            audio_input_tokens=audio_input_tokens,
            cached_audio_tokens=cached_audio_tokens
        )
    


# 便捷函数：创建积分检查callback
def create_credit_check_callback(
    user_id: str,
    action: str = "video_generation",
    db=None  # 已废弃，不再使用
) -> CreditCheckCallbackHandler:
    """
    创建积分检查callback
    
    Args:
        user_id: 用户ID
        action: 操作类型（用于记录）
        db: 数据库会话（已废弃，不再使用，每次检查时按需创建）
    
    Returns:
        CreditCheckCallbackHandler实例
    """
    return CreditCheckCallbackHandler(
        user_id=user_id,
        action=action,
        db=None  # 不再使用 db 参数
    )
