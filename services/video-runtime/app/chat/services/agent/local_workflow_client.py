"""进程内 WorkflowClient：合并单体（monorepo）下 Chat Router 直接调用 VideoAgent 核心。

与 ``HttpWorkflowClient`` 等价，但不经过 HTTP ``delegate-submit`` 与服务间 Bearer 鉴权：
复用 VideoAgent 侧 ``app`` 的会话查询/创建、扣费预检与 ``_submit_delegate_task`` 入队逻辑，
使 ChatAgent 与 VideoAgent 运行于同一进程时省去一次网络往返。

行为与 HTTP 版保持一致：入队一个 ``skip_agent_router=True`` 的任务，返回 ``DelegateSubmitResponse``。
仅当 ``WORKFLOW_CLIENT_MODE=local`` 时启用；默认 ``http`` 保持原有跨进程行为不变。
"""
import logging
from typing import Any, Dict, Optional

from ...exceptions import BusinessException, BusinessExceptionCode
from .workflow_client import DelegateSubmitResponse, HttpWorkflowClient

logger = logging.getLogger(__name__)


class LocalWorkflowClient:
    """在进程内直接委托到 VideoAgent 核心（app），不发起 HTTP 调用。

    复用 ``HttpWorkflowClient`` 的 payload 构造逻辑（``_build_delegate_payload``）以确保
    与跨进程版本字段一致，仅将“发 HTTP 请求”替换为“进程内函数调用”。
    """

    def __init__(self) -> None:
        # 仅复用其纯函数式的 payload 构造，不需要 base_url / token。
        self._payload_builder = HttpWorkflowClient(base_url="_local_", service_token="_local_")

    async def invoke_video(self, state: Any, context: Any = None) -> Optional[DelegateSubmitResponse]:
        return await self._delegate_submit(target_agent="video", state=state)

    async def invoke_story(self, state: Any, send_event_func: Any) -> Dict[str, Any]:
        await self._delegate_submit(target_agent="story", state=state)
        return {"messages": []}

    async def invoke_music(self, state: Any, send_event_func: Any) -> Dict[str, Any]:
        await self._delegate_submit(target_agent="music", state=state)
        return {"messages": []}

    async def invoke_image(self, state: Any, send_event_func: Any) -> Dict[str, Any]:
        await self._delegate_submit(target_agent="image", state=state)
        return {"messages": []}

    async def invoke_video_gen(self, state: Any, send_event_func: Any) -> Dict[str, Any]:
        await self._delegate_submit(target_agent="video_gen", state=state)
        return {"messages": []}

    async def delegate_submit(
        self,
        *,
        target_agent: str,
        state: Any,
        idempotency_key: Optional[str] = None,
        mode: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> DelegateSubmitResponse:
        """Public V2 adapter matching HttpWorkflowClient.delegate_submit."""
        return await self._delegate_submit(
            target_agent=target_agent,
            state=state,
            idempotency_key=idempotency_key,
            mode=mode,
            parameters=parameters,
        )

    async def _delegate_submit(
        self,
        target_agent: str,
        state: Any,
        idempotency_key: Optional[str] = None,
        mode: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> DelegateSubmitResponse:
        # 进程内调用 VideoAgent 核心（合并单体中 app 为同级顶层包）。
        from app.api.agent.agent_router_endpoints import _submit_delegate_task
        from app.crud.conversation import (
            async_get_conversation_by_thread_id,
            async_create_conversation,
        )
        from app.utils.credit_deduction_utils import check_credits_before_task
        from app.exceptions import (
            BusinessException as VABusinessException,
            BusinessExceptionCode as VABusinessExceptionCode,
        )

        payload = self._payload_builder._build_delegate_payload(target_agent=target_agent, state=state)
        user_id = payload["user_id"]
        thread_id = payload["thread_id"]
        body = payload["body"]
        if mode:
            body["mode"] = mode
        if parameters:
            body["parameters"] = parameters
        if not user_id:
            raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "delegate 缺少 user_id")

        logger.info(
            "进程内委托 workflow 到 VideoAgent: target_agent=%s thread_id=%s mode=%s",
            target_agent,
            thread_id,
            mode,
        )
        try:
            conversation = await async_get_conversation_by_thread_id(thread_id)
            if not conversation:
                title = body["user_input"][:50]
                if len(body["user_input"]) > 50:
                    title = f"{body['user_input'][:50]}..."
                conversation = await async_create_conversation(
                    user_id=user_id, thread_id=thread_id, title=title
                )
            elif conversation.user_id != user_id:
                raise BusinessException(BusinessExceptionCode.PERMISSION_DENIED, "无权访问此对话")

            check_ok, _, check_err = await check_credits_before_task(user_id)
            if not check_ok:
                raise BusinessException(BusinessExceptionCode.INSUFFICIENT_CREDITS, check_err)

            # user_option 需为 VideoAgent 的 UserOption 类型；_submit_delegate_task 内部会做 None 兜底。
            user_option = None
            if body.get("user_option"):
                from app.models.user_options import UserOption as VAUserOption
                user_option = VAUserOption(**body["user_option"])

            data = await _submit_delegate_task(
                conversation,
                thread_id,
                body["user_input"],
                target_agent,
                user_option,
                body.get("user_input_files"),
                body.get("language"),
                user_id,
            )
        except (VABusinessException, BusinessException):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("进程内 delegate 失败: %s", exc, exc_info=True)
            raise BusinessException(
                BusinessExceptionCode.INTERNAL_SERVER_ERROR, f"进程内委托失败: {exc}"
            ) from exc

        submit_response = DelegateSubmitResponse(**(data or {}))
        logger.info(
            "进程内委托成功: target_agent=%s remote_run_id=%s status=%s",
            target_agent,
            submit_response.run_id,
            submit_response.status,
        )
        return submit_response
