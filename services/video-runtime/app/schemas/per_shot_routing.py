"""
Per-shot 生成路由：结构化 LLM 输出（与 prompts/prompt_config 中 VIDEO_PER_SHOT_GENERATION_ROUTING 的 schema 一致）。

- recommended_generation_mode ↔ tool_enums.GenerationMode
- 三类工具字段 ↔ user_options.ImageGenerationTool / VideoGenerationTool（普通 I2V 不含 ltx_2_3；口型仅 LIPSYNC_CAPABLE 子集）
"""
from typing import Any, List, Literal, Optional, get_args

from pydantic import BaseModel, Field, model_validator

GenerationModeLiteral = Literal["normal", "lipsync", "empty_shot"]

# 与 ImageGenerationTool 枚举 value 一致（含 auto）
ImageGenerationToolLiteral = Literal[
    "auto",
    "nano_banana",
    "nano_banana_2",
    "nano_banana_pro",
    "seedream",
]

# 普通 I2V：VideoGenerationTool 中用于 normal 链的 value（不含 ltx_2_3）
NormalVideoToolLiteral = Literal[
    "auto",
    "pollo_seedance",
    "pollo_seedance_v1_5",
    "seedance_2_i2v",
    "seedance_2_i2v_turbo",
    "seedance_2_fast_i2v",
    "seedance_2_fast_i2v_turbo",
    "wan_2_5",
    "wan_2_6_flash",
    "kling_v3_std",
    "happyhorse_1_0_i2v",
    "happyhorse_1_1_i2v",
    "openai_sora",
    "openai_sora_pro",
]

# 口型 I2V：LIPSYNC_CAPABLE_VIDEO_TOOLS ∪ auto
LipsyncVideoToolLiteral = Literal["auto", "ltx_2_3", "kling_v2_ai_avatar_pro", "wan_2_2_speech_to_video", "wan_2_5", "wan_2_6_flash"]


class PerShotRoutingItem(BaseModel):
    """单镜路由：与 prompt 中「每条镜头」一一对应。"""

    shot_number: int = Field(description="镜头编号，与输入一致")
    recommended_generation_mode: Optional[GenerationModeLiteral] = Field(
        default=None,
        description="仅当画面与模型能力与输入 generation_mode 冲突需改路径时填写；否则省略以保留上游已判定的 generation_mode",
    )
    image_generation_tool: Optional[ImageGenerationToolLiteral] = Field(
        default=None,
        description="静图工具；省略或 null=沿用用户全局；全局为 auto 时宜给出具体枚举",
    )
    normal_video_tool: Optional[NormalVideoToolLiteral] = Field(
        default=None,
        description="普通 I2V 工具；省略或 null=沿用用户全局；仅本镜走 normal/empty_shot 链时生效",
    )
    lipsync_video_tool: Optional[LipsyncVideoToolLiteral] = Field(
        default=None,
        description="口型 I2V 工具；省略或 null=沿用用户全局；仅本镜走 lipsync 时生效",
    )
    rationale: Optional[str] = Field(default=None, description="简短理由，可空")

    @model_validator(mode="before")
    @classmethod
    def _legacy_disallow_lipsync(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("recommended_generation_mode") is None:
            if data.get("disallow_lipsync") is True:
                out = dict(data)
                out["recommended_generation_mode"] = "normal"
                return out
        return data


class PerShotGenerationRoutingOutput(BaseModel):
    """根对象：与结构化输出绑定；字段名 shots 固定。"""

    shots: List[PerShotRoutingItem] = Field(description="与输入镜头顺序一致的路由结果")


def parse_per_shot_generation_routing_agent_result(result: Any) -> Optional["PerShotGenerationRoutingOutput"]:
    """从 ``create_agent().ainvoke`` 返回的 dict 取出 ``PerShotGenerationRoutingOutput``。

    LangChain 将结构化结果放在 ``structured_response``；少数路径为 ``parsed``。与 keyframe / video 节点一致。
    """
    parsed: Any = None
    if isinstance(result, dict):
        parsed = result.get("parsed") or result.get("structured_response")
    if isinstance(parsed, dict):
        try:
            return PerShotGenerationRoutingOutput.model_validate(parsed)
        except Exception:
            return None
    if isinstance(parsed, PerShotGenerationRoutingOutput):
        return parsed
    return None


def per_shot_routing_enum_lines_for_prompt() -> dict[str, str]:
    """与 `ImageGenerationToolLiteral` / `NormalVideoToolLiteral` / `LipsyncVideoToolLiteral` 同源，供 Mustache 注入，避免 prompt 与 schema 双处维护。"""
    def _line(lit: Any) -> str:
        return " | ".join(f"`{v}`" for v in get_args(lit))

    return {
        "enum_image_tools_line": _line(ImageGenerationToolLiteral),
        "enum_normal_video_tools_line": _line(NormalVideoToolLiteral),
        "enum_lipsync_video_tools_line": _line(LipsyncVideoToolLiteral),
    }
