"""
统一 API 响应模型（与 Cuti-VideoAgent 兼容）。
"""
from typing import Generic, TypeVar, Optional, List

from pydantic import BaseModel, Field

T = TypeVar("T")


class ClarifyResponse(BaseModel):
    """澄清响应结果（Agent Router）。"""

    clarify_message: str = Field(description="澄清消息内容，用于询问用户具体意图")
    available_services: List[str] = Field(
        default_factory=lambda: ["story", "music", "video", "image"],
        description="可用的服务列表",
    )
    reasoning: str = Field(description="为什么需要澄清的原因")


class ResponseModel(BaseModel, Generic[T]):
    code: int = Field(default=0, description="响应码，0表示成功")
    message: str = Field(default="success", description="响应消息")
    data: Optional[T] = Field(default=None, description="响应数据")

    @classmethod
    def success(cls, data: T = None, message: str = "success") -> "ResponseModel[T]":
        return cls(code=0, message=message, data=data)

    @classmethod
    def error(cls, code: int = -1, message: str = "error", data: T = None) -> "ResponseModel[T]":
        return cls(code=code, message=message, data=data)
