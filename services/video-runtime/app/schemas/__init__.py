# 通用响应模型
from app.schemas.response import ResponseModel

# 用户相关模型
from app.schemas.user import (
    UserCreate, UserResponse, UserLogin, UserCredit,
    CreditHistory, InviteCodeCreate, InviteCodeResponse, Token,
    AuthType, UserStatus, CreditOperationType
)

# Video 相关模型（用于 LLM 结构化输出）
from app.schemas.video_llm import (
    StyleDetectionResult,
    VideoPromptResult,
    BatchVideoPromptResult,
    VideoGenerationPrompt,
    PromptEvaluationResult,
    BatchPromptEvaluationResult,
)
