"""
Workflow 调用抽象：ChatAgent 的 ``AgentRouterService`` 只负责路由；video / story / music / image
的实际执行委托给 **Cuti-VideoAgent**。

当前默认实现按照 ``docs/VIDEOCHATAGENT_API.md`` 通过 HTTP 调用
``POST /agent-router/delegate-submit``，将任务提交到 VideoAgent。
"""
import logging
from typing import Any, Dict, Optional, Protocol
from urllib.parse import urljoin

import aiohttp
from pydantic import BaseModel, Field

from ...config import get_settings
from ...exceptions import BusinessException, BusinessExceptionCode
from ...models.video_state import UserInput

logger = logging.getLogger(__name__)


class DelegateSubmitResponse(BaseModel):
    """VideoAgent delegate-submit 返回的数据结构。"""

    run_id: Optional[str] = Field(default=None, description="VideoAgent 任务 run_id")
    thread_id: Optional[str] = Field(default=None, description="线程 ID")
    conversation_id: Optional[int] = Field(default=None, description="VideoAgent 侧 conversation_id")
    status: Optional[str] = Field(default=None, description="任务状态")
    message: Optional[str] = Field(default=None, description="返回消息")


class WorkflowClient(Protocol):
    """Workflow 调用协议；由 VideoAgent 侧或网关实现。"""

    async def invoke_video(
        self,
        state: Any,
        context: Any = None,
    ) -> Optional[DelegateSubmitResponse]:
        """执行视频 workflow（远程委托到 Cuti-VideoAgent）；成功时返回 delegate-submit 包络中的 data。"""
        ...

    async def invoke_story(
        self,
        state: Any,
        send_event_func: Any,
    ) -> Dict[str, Any]:
        """执行故事 workflow，返回 ``{"messages": [...]}``。"""
        ...

    async def invoke_music(
        self,
        state: Any,
        send_event_func: Any,
    ) -> Dict[str, Any]:
        """执行音乐 workflow，返回 ``{"messages": [...]}``。"""
        ...

    async def invoke_image(
        self,
        state: Any,
        send_event_func: Any,
    ) -> Dict[str, Any]:
        """执行图像 workflow，返回 ``{"messages": [...]}``。"""
        ...

    async def invoke_video_gen(
        self,
        state: Any,
        send_event_func: Any,
    ) -> Dict[str, Any]:
        """执行视频直生（SD2 单模型）workflow，返回 ``{"messages": [...]}``。"""
        ...


class HttpWorkflowClient:
    """按统一 API 文档将 workflow 委托到 Cuti-VideoAgent。"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        service_token: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ):
        settings = get_settings()
        self.base_url = (base_url if base_url is not None else settings.VIDEOAGENT_BASE_URL).strip()
        self.service_token = (service_token if service_token is not None else settings.CUTI_SERVICE_TOKEN).strip()
        self.timeout_seconds = timeout_seconds or settings.VIDEOAGENT_REQUEST_TIMEOUT_SECONDS

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
        """Public V2 adapter over the existing delegate transport contract."""
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
        payload = self._build_delegate_payload(target_agent=target_agent, state=state)
        headers = self._build_headers(payload["user_id"])
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        if mode:
            payload["body"]["mode"] = mode
        if parameters:
            payload["body"]["parameters"] = parameters
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        url = self._build_delegate_url()

        logger.info(
            "委托 workflow 到 Cuti-VideoAgent: target_agent=%s thread_id=%s",
            target_agent,
            payload["thread_id"],
        )

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload["body"], headers=headers) as response:
                raw_text = await response.text()
                if response.status >= 400:
                    logger.error(
                        "调用 Cuti-VideoAgent delegate-submit 失败: status=%s body=%s",
                        response.status,
                        raw_text,
                    )
                    raise BusinessException(
                        BusinessExceptionCode.INTERNAL_SERVER_ERROR,
                        f"Cuti-VideoAgent delegate-submit 失败: HTTP {response.status}",
                    )
                try:
                    result = await response.json(content_type=None)
                except Exception as exc:
                    logger.error("解析 Cuti-VideoAgent delegate-submit 响应失败: %s", raw_text)
                    raise BusinessException(
                        BusinessExceptionCode.INTERNAL_SERVER_ERROR,
                        "Cuti-VideoAgent delegate-submit 返回了无法解析的 JSON",
                    ) from exc

        if result.get("code") != 0:
            raise BusinessException(
                BusinessExceptionCode.BUSINESS_ERROR,
                result.get("message") or "Cuti-VideoAgent delegate-submit 返回业务错误",
            )

        data = result.get("data") or {}
        submit_response = DelegateSubmitResponse(**data)
        logger.info(
            "委托 workflow 成功: target_agent=%s remote_run_id=%s status=%s",
            target_agent,
            submit_response.run_id,
            submit_response.status,
        )
        return submit_response

    def _build_delegate_url(self) -> str:
        if not self.base_url:
            raise BusinessException(
                BusinessExceptionCode.INTERNAL_SERVER_ERROR,
                "未配置 VIDEOAGENT_BASE_URL，无法委托调用 Cuti-VideoAgent",
            )
        base_url = self.base_url.rstrip("/") + "/"
        return urljoin(base_url, "agent-router/delegate-submit")

    def _build_headers(self, user_id: str) -> Dict[str, str]:
        if not self.service_token:
            raise BusinessException(
                BusinessExceptionCode.INTERNAL_SERVER_ERROR,
                "未配置 CUTI_SERVICE_TOKEN，无法调用 Cuti-VideoAgent",
            )
        if not user_id:
            raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "delegate-submit 缺少 user_id")
        return {
            "Authorization": f"Bearer {self.service_token}",
            "Content-Type": "application/json",
            "X-Cuti-Service-User-Id": user_id,
        }

    def _build_delegate_payload(self, target_agent: str, state: Any) -> Dict[str, Any]:
        if isinstance(state, dict):
            normalized_state = state
        elif hasattr(state, "model_dump"):
            normalized_state = state.model_dump()
        else:
            normalized_state = dict(state)
        user_input_data = normalized_state.get("user_input_data")
        if isinstance(user_input_data, dict):
            user_input_data = UserInput(**user_input_data)
        if not isinstance(user_input_data, UserInput):
            raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "delegate-submit 缺少 user_input_data")

        thread_id = normalized_state.get("thread_id")
        user_id = normalized_state.get("user_id")
        if not thread_id:
            raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "delegate-submit 缺少 thread_id")

        body = {
            "thread_id": thread_id,
            "user_input": user_input_data.user_input,
            "target_agent": target_agent,
            "user_option": user_input_data.user_option.model_dump(mode="json") if user_input_data.user_option else None,
            "language": normalized_state.get("detected_language"),
            "user_input_files": self._build_user_input_files(user_input_data),
        }
        return {
            "user_id": user_id,
            "thread_id": thread_id,
            "body": body,
        }

    @staticmethod
    def _build_user_input_files(user_input_data: UserInput) -> Optional[Dict[str, Any]]:
        if not user_input_data.images and not user_input_data.audio_files and not user_input_data.video_files:
            return None
        return {
            "images": [item.model_dump(mode="json") for item in user_input_data.images],
            "audio_files": [item.model_dump(mode="json") for item in user_input_data.audio_files],
            "video_files": [item.model_dump(mode="json") for item in user_input_data.video_files],
        }


def create_default_workflow_client() -> WorkflowClient:
    """根据 ``WORKFLOW_CLIENT_MODE`` 选择 WorkflowClient 实现。

    - ``http``（默认）：基于 HTTP 的 ``HttpWorkflowClient``，按 VIDEOCHATAGENT_API 文档对接独立 VideoAgent 进程（原行为不变）。
    - ``local``：合并单体下的 ``LocalWorkflowClient``，进程内直接调用 VideoAgent 核心（省去 HTTP 跳转）。
    """
    mode = (get_settings().WORKFLOW_CLIENT_MODE or "http").strip().lower()
    if mode == "local":
        from .local_workflow_client import LocalWorkflowClient

        logger.info("WorkflowClient 使用进程内实现 (WORKFLOW_CLIENT_MODE=local)")
        return LocalWorkflowClient()
    return HttpWorkflowClient()


class StubWorkflowClient:
    """保留给显式测试场景；默认运行路径不应再使用该桩实现。"""

    async def invoke_video(self, state: Any, context: Any = None) -> Any:
        raise NotImplementedError(
            "WorkflowClient 未注入：请在集成层提供实现（调用 Cuti-VideoAgent 视频 workflow），"
            "并传入 AgentRouterService(workflow_client=...)"
        )

    async def invoke_story(self, state: Any, send_event_func: Any) -> Dict[str, Any]:
        raise NotImplementedError(
            "WorkflowClient 未注入：请实现 story workflow 并传入 AgentRouterService(workflow_client=...)"
        )

    async def invoke_music(self, state: Any, send_event_func: Any) -> Dict[str, Any]:
        raise NotImplementedError(
            "WorkflowClient 未注入：请实现 music workflow 并传入 AgentRouterService(workflow_client=...)"
        )

    async def invoke_image(self, state: Any, send_event_func: Any) -> Dict[str, Any]:
        raise NotImplementedError(
            "WorkflowClient 未注入：请实现 image workflow 并传入 AgentRouterService(workflow_client=...)"
        )

    async def invoke_video_gen(self, state: Any, send_event_func: Any) -> Dict[str, Any]:
        raise NotImplementedError(
            "WorkflowClient 未注入：请实现 video_gen workflow 并传入 AgentRouterService(workflow_client=...)"
        )
