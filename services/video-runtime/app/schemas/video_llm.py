"""
Video 相关的 Pydantic schemas
用于 LLM 结构化输出和数据验证
"""

from enum import Enum
from typing import Any, List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


# ============ 角色一致性校验（I2I） ============
# 角色 = 人、动物、主角等。一致性维度用 _LEVEL_DESC；整图画面质量用 VideoConsistencyLevel（与视频 severe 同档）。
_LEVEL_DESC = (
    "请从以下选项中选择: 'identical'(完全一致), 'very_similar'(非常相似), "
    "'somewhat_similar'(有些相似), 'not_similar'(不太像), 'very_different'(完全不同); 无该项或未要求时填 'n_a'"
)


# ============ 视频/图片共用：五档等级（good/acceptable/poor/fail/n_a） ============
class VideoConsistencyLevel(str, Enum):
    """视频一致性及 I2I 整图画面质量档共用；>= acceptable 或 n_a 视为通过"""
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    POOR = "poor"
    FAIL = "fail"
    N_A = "n_a"

    def is_pass(self) -> bool:
        """是否视为通过（good/acceptable/n_a）"""
        return self in (VideoConsistencyLevel.GOOD, VideoConsistencyLevel.ACCEPTABLE, VideoConsistencyLevel.N_A)


_VIDEO_LEVEL_DESC = (
    "请从以下选项中选择: 'good'(很好), 'acceptable'(可接受), 'poor'(较差), 'fail'(不可接受); "
    "不适用或无法判断时填 'n_a'"
)


CHARACTER_CONSISTENCY_REQUIRED_KEYS = [
    "has_character",
    "per_character",
    "severe_abnormality",
    "severe_abnormality_reason",
    "reason",
    "suggested_prompt",
]


def _coerce_video_level_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, VideoConsistencyLevel):
        return v.value
    if isinstance(v, str) and v.strip():
        return v.strip().lower()
    return None


def _worse_video_consistency_level(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """两档中取更严（更差）的一档；用于合并旧版顶层 artifact 与 severe_abnormality。"""
    rank = {"fail": 0, "poor": 1, "acceptable": 2, "good": 3, "n_a": 4}
    cands = [x for x in (a, b) if x]
    if not cands:
        return None
    return min(cands, key=lambda s: rank.get(s, 99))


class CharacterConsistencyResult(BaseModel):
    """I2I 角色一致性校验结果：per_character 表达人设维度；整图仅 severe_abnormality（含原整图 artifact 的较轻伪影 + 离谱事故）；passed 由后端聚合"""
    model_config = ConfigDict(
        json_schema_extra={"required": CHARACTER_CONSISTENCY_REQUIRED_KEYS},
    )
    has_character: bool = Field(
        description="本次是否需要做角色一致性检查：综合分析 prompt、参考图、关键帧三者。三者同时满足（prompt 非仅风格/新角色、参考图有人物/角色、关键帧有人物/角色）时为 True；任一类不满足则为 False。"
    )
    per_character: List["PerCharacterConsistencyResult"] = Field(
        default_factory=list,
        description="按角色拆分的一致性结果列表 has_character 为 True 时必填（至少一项），为 False 时可为空列表。"
    )
    severe_abnormality: VideoConsistencyLevel = Field(
        default=VideoConsistencyLevel.N_A,
        description=(
            "生成图整图画面质量：含略糊、轻截断、轻度不协调、AI 涂抹感等，"
            "以及多手、漂浮、穿模、融化脸等明显事故。"
            "**无论 has_character 是否为 true 均需评估**（无人设比对时也要看全图）。"
        )
        + _VIDEO_LEVEL_DESC,
    )
    severe_abnormality_reason: str = Field(
        default="",
        description="整图 severe_abnormality 的一句话说明：有问题写具体现象；整图无问题或仅轻微可接受瑕疵时填「未发现明显画面质量问题。」",
    )
    reason: str = Field(
        default="",
        description="简要说明判断原因（人设维度与整图的综合摘要，仍须遵守 reason_rules）",
    )
    suggested_prompt: Optional[str] = Field(
        default=None,
        description="校验未通过时（passed 为 false）可给出的修正后完整 I2I 提示词。severe_abnormality 或人设维任一不过关时均可填写。"
    )

    @model_validator(mode="before")
    @classmethod
    def _legacy_top_level_artifact_into_severe(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        legacy = _coerce_video_level_str(out.pop("artifact", None))
        current = _coerce_video_level_str(out.get("severe_abnormality"))
        if legacy is not None and current is None:
            out["severe_abnormality"] = legacy
        elif legacy is not None and current is not None:
            merged = _worse_video_consistency_level(legacy, current)
            if merged is not None:
                out["severe_abnormality"] = merged
        return out


PER_CHARACTER_CONSISTENCY_REQUIRED_KEYS = [
    "name", "consistency_level", "accessories_level", "clothing_level",
    "body_level", "hair_level", "style_level", "artifact", "reason",
]


class PerCharacterConsistencyResult(BaseModel):
    """单角色 I2I 一致性结果（脸/配饰/服装/体型/发型/风格 + artifact）；用于 per_character 列表"""
    model_config = ConfigDict(
        json_schema_extra={"required": PER_CHARACTER_CONSISTENCY_REQUIRED_KEYS},
    )
    name: Optional[str] = Field(default=None, description="角色/元素名称（如 女主角）")
    consistency_level: str = Field(default="n_a", description="脸一致性等级。" + _LEVEL_DESC)
    accessories_level: str = Field(default="n_a", description="配饰一致性等级。" + _LEVEL_DESC)
    clothing_level: str = Field(default="n_a", description="服装一致性等级。" + _LEVEL_DESC)
    body_level: str = Field(default="n_a", description="体型一致性等级。" + _LEVEL_DESC)
    hair_level: str = Field(default="n_a", description="发型一致性等级。" + _LEVEL_DESC)
    style_level: str = Field(default="n_a", description="风格一致性等级。" + _LEVEL_DESC)
    artifact: VideoConsistencyLevel = Field(
        default=VideoConsistencyLevel.N_A,
        description="变形/异常。" + _VIDEO_LEVEL_DESC
    )
    reason: str = Field(default="", description="该角色判断原因")


# Resolve forward ref on CharacterConsistencyResult.per_character
CharacterConsistencyResult.model_rebuild()


# ============ 视频一致性校验（I2V 多维度） ============
# 等级/reason 已去掉 Optional 并设默认（N_A/""），解析时缺键会用默认值；response_json_schema 仍用 required 强制 Gemini 输出全部键。
# PerCharacterFirstFrameResult 必须定义在 VideoConsistencyCheckResult 之前，避免 deep-agent tool args_schema 前向引用未 rebuild。
PER_CHARACTER_FIRST_FRAME_REQUIRED_KEYS = [
    "name",
    "face_consistency", "accessories_consistency", "clothing_consistency",
    "body_consistency", "hair_consistency", "framing_consistency", "no_new_primary_subjects",
    "face_consistency_reason", "accessories_consistency_reason", "clothing_consistency_reason",
    "body_consistency_reason", "hair_consistency_reason", "framing_consistency_reason",
    "no_new_primary_subjects_reason",
]


class PerCharacterFirstFrameResult(BaseModel):
    """单角色首帧一致性七项（用于 per_character_first_frame 列表）"""
    model_config = ConfigDict(
        json_schema_extra={"required": PER_CHARACTER_FIRST_FRAME_REQUIRED_KEYS},
    )
    name: Optional[str] = Field(default=None, description="角色名称（如 女主角）")
    face_consistency: VideoConsistencyLevel = Field(default=VideoConsistencyLevel.N_A, description="与首帧及参考图（若有）为同一角色身份；口型表演动态不单独从严。须基于全片可观察时段判断。" + _VIDEO_LEVEL_DESC)
    accessories_consistency: VideoConsistencyLevel = Field(default=VideoConsistencyLevel.N_A, description="配饰与首帧及参考图一致。" + _VIDEO_LEVEL_DESC)
    clothing_consistency: VideoConsistencyLevel = Field(default=VideoConsistencyLevel.N_A, description="服装与首帧及参考图一致。" + _VIDEO_LEVEL_DESC)
    body_consistency: VideoConsistencyLevel = Field(default=VideoConsistencyLevel.N_A, description="体型与首帧及参考图一致。" + _VIDEO_LEVEL_DESC)
    hair_consistency: VideoConsistencyLevel = Field(default=VideoConsistencyLevel.N_A, description="发型与首帧及参考图一致。" + _VIDEO_LEVEL_DESC)
    framing_consistency: VideoConsistencyLevel = Field(default=VideoConsistencyLevel.N_A, description="景别合规。" + _VIDEO_LEVEL_DESC)
    no_new_primary_subjects: VideoConsistencyLevel = Field(default=VideoConsistencyLevel.N_A, description="无新主体。" + _VIDEO_LEVEL_DESC)
    face_consistency_reason: str = Field(default="", description="脸说明")
    accessories_consistency_reason: str = Field(default="", description="配饰说明")
    clothing_consistency_reason: str = Field(default="", description="服装说明")
    body_consistency_reason: str = Field(default="", description="体型说明")
    hair_consistency_reason: str = Field(default="", description="发型说明")
    framing_consistency_reason: str = Field(default="", description="景别说明")
    no_new_primary_subjects_reason: str = Field(default="", description="新主体说明")


VIDEO_CONSISTENCY_REQUIRED_KEYS = [
    "per_character_first_frame",
    "camera_movement", "style_consistency", "severe_abnormality",
    "camera_movement_reason", "style_consistency_reason", "severe_abnormality_reason",
    "reason_overall", "suggested_prompt",
]


class VideoConsistencyCheckResult(BaseModel):
    """I2V 视频一致性校验结果：首帧一致性用 per_character_first_frame；整片维度为 camera_movement / style_consistency / severe_abnormality；passed 由后端计算"""
    model_config = ConfigDict(
        json_schema_extra={"required": VIDEO_CONSISTENCY_REQUIRED_KEYS},
    )
    per_character_first_frame: List[PerCharacterFirstFrameResult] = Field(
        default_factory=list,
        description="按角色拆分的首帧七项结果列表；无角色时可为空列表。综合首帧是否通过由后端根据本列表聚合。"
    )
    camera_movement: VideoConsistencyLevel = Field(
        default=VideoConsistencyLevel.N_A,
        description="镜头运动（推拉摇移等）是否与 prompt 描述一致；prompt 未描述镜头则填 n_a。本维仅用于记录与展示，不参与后端 passed。" + _VIDEO_LEVEL_DESC
    )
    style_consistency: VideoConsistencyLevel = Field(
        default=VideoConsistencyLevel.N_A,
        description="画面风格（光影、色调、画风）与首帧或整体一致，无突兀跳变。" + _VIDEO_LEVEL_DESC
    )
    severe_abnormality: VideoConsistencyLevel = Field(
        default=VideoConsistencyLevel.N_A,
        description="是否出现一眼可辨的离谱画面异常（多余肢体、凭空多人、严重穿模撕裂、空中飞人等）；不明显则不拦截。good/acceptable/n_a 均视为通过。" + _VIDEO_LEVEL_DESC,
        validation_alias=AliasChoices("severe_abnormality", "artifact"),
    )
    camera_movement_reason: str = Field(default="", description="镜头运动维度的简要说明")
    style_consistency_reason: str = Field(default="", description="风格一致维度的简要说明")
    severe_abnormality_reason: str = Field(
        default="",
        description="明显异常维度的简要说明",
        validation_alias=AliasChoices("severe_abnormality_reason", "artifact_reason"),
    )
    reason_overall: str = Field(default="", description="整体汇总说明，供日志与 metrics")
    suggested_prompt: Optional[str] = Field(
        default=None,
        description="修正后的完整 I2V 提示词：必须是一条完整、可直接用于重试的 I2V 文案（不能只是片段或仅镜头描述）。仅当校验未通过且需按模板 suggested_prompt_rule 给出重试文案时填写。该字段仅包含一条 I2V 文案正文，不得在文案后追加任何解释、总结或元评论（如「文案结束」「建议：…」「提示词到此结束」等）。"
    )
    first_frame_consistency: Optional[VideoConsistencyLevel] = Field(
        default=None,
        description="由后端根据 per_character_first_frame 聚合得出，LLM 不填；供 _compute_passed 使用"
    )
    passed: Optional[bool] = Field(
        default=None,
        description="由后端根据各维度按通过条件计算，LLM 不填"
    )


# ============ 风格检测 ============
class StyleDetectionResult(BaseModel):
    """风格检测结果"""
    detected_styles: List[str] = Field(description="检测到的相关风格列表，按相关性从高到低排序")
    confidence: float = Field(description="整体置信度 (0-1)", ge=0, le=1)


# ============ 视频 Prompt 生成 ============
class VideoPromptResult(BaseModel):
    """单个视频prompt生成结果（仅用于LLM生成阶段）"""
    shot_number: int = Field(description="镜头编号")
    i2v_prompt: str = Field(description="生成的视频提示词")


class BatchVideoPromptResult(BaseModel):
    """批量视频prompt生成结果包装类"""
    prompts: List[VideoPromptResult] = Field(description="批量视频prompt生成结果列表")

    def resilience_empty_reason(self) -> Optional[str]:
        # 被 llm_resilience._wrap_invoke_with_result_check 自动调用：返回非空字符串触发 EMPTY_RESPONSE 桶。
        # 输入 N 个 shot 必须每个都产出 i2v_prompt；prompts 空 = 该批 I2V 无法生成。
        # 单条空 i2v_prompt 由调用方逐镜补齐，这里只关心整体是否为空。
        if not self.prompts:
            return "prompts is empty"
        return None


class VideoGenerationPrompt(BaseModel):
    """视频生成提示词完整信息，包含prompt、首尾帧、一致性评估等"""
    shot_number: int = Field(description="镜头编号")
    i2v_prompt: str = Field(description="生成的视频提示词")
    user_regenerate_instruction: Optional[str] = Field(
        default=None,
        description="再生时用户自然语言修改说明，与 i2v_prompt 在 tool execution 模板中分开展示",
    )
    start_image_url: str = Field(description="首帧图片URL")
    end_image_url: Optional[str] = Field(default=None, description="尾帧图片URL（用于首尾帧生成）")
    needs_end_image: bool = Field(default=False, description="是否需要首尾帧生成以保持一致性")
    consistency_reason: Optional[str] = Field(default=None, description="需要首尾帧的原因（如果需要）")
    generation_mode: Optional[str] = Field(default=None, description="生成模式：normal | lipsync | empty_shot（供 eval 等下游按镜头区分口型）")


# ============ Prompt 评估与修正 ============
class PromptEvaluationResult(BaseModel):
    """单个 video i2v_prompt 的评估结果（I2V 首帧约束；needs_end_image 等由上游决定，本结构保留字段以兼容结构化输出）。"""
    shot_number: int = Field(description="镜头编号")
    original_prompt: str = Field(description="原始 i2v_prompt")
    
    # 首帧约束修正（必须）
    needs_content_fix: bool = Field(description="是否因违反 I2V 首帧约束（及口型提示词规则）而需要修正 i2v_prompt")
    content_issues_found: List[str] = Field(description="发现的问题列表（可含首帧约束类型标签）", default_factory=list)
    fixed_prompt: str = Field(description="修正后的 i2v_prompt（若无需修正则与原文相同）")
    
    # 以下字段本流程不再由本步 LLM 填写；保留默认以兼容 schema / Hub 旧模板
    needs_end_image: Optional[bool] = Field(default=False, description="保留字段；由上游 VideoGenerationPrompt 决定，eval-fix 步忽略")
    consistency_risks: List[str] = Field(description="保留字段；eval-fix 步可留空", default_factory=list)
    consistency_analysis: Optional[str] = Field(default=None, description="保留字段；eval-fix 步可留空")


class BatchPromptEvaluationResult(BaseModel):
    """批量prompt评估结果"""
    evaluations: List[PromptEvaluationResult] = Field(description="批量prompt评估结果列表")


# ============ 关键帧 Prompt 评估与修正 ============
class KeyframePromptFixResult(BaseModel):
    """单个关键帧prompt的修正结果"""
    shot_number: int = Field(description="镜头编号")
    frame_index: int = Field(default=0, description="帧索引：0=首帧, -1=尾帧, 1/2/3=中间帧")
    original_prompt: str = Field(description="原始prompt")
    needs_fix: bool = Field(description="是否需要修正")
    issues_found: List[str] = Field(description="发现的问题列表")
    fixed_prompt: str = Field(description="修正后的prompt")


class BatchKeyframePromptFixResult(BaseModel):
    """批量关键帧prompt修正结果"""
    fixes: List[KeyframePromptFixResult] = Field(description="批量关键帧prompt修正结果列表")


# ============ 用户上传视频 Gemini 分析（user_input_analysis_node）============
class GeminiVideoCharacter(BaseModel):
    """Gemini 返回的视频角色信息"""
    name: str = Field(description="角色名称或描述")
    description: str = Field(description="角色外观和特征描述")
    role: str = Field(description="角色在视频中的作用")
    appearance_time: Optional[str] = Field(default=None, description="出现时间段")


class GeminiVideoStoryboardSegment(BaseModel):
    """Gemini 返回的视频故事板片段"""
    time_start: str = Field(description="开始时间 (如: 00:00)")
    time_end: str = Field(description="结束时间 (如: 00:03)")
    action_description: str = Field(description="画面内容描述")
    production_method: str = Field(description="制作方式推测")
    visual_style: Optional[str] = Field(default=None, description="视觉风格")
    camera_angle: Optional[str] = Field(default=None, description="镜头角度")
    emotion: Optional[str] = Field(default=None, description="情感调性")


class GeminiVideoAnalysisResult(BaseModel):
    """Gemini 视频分析结果（与 PROMPTS_CONFIG[VIDEO_GEMINI_ANALYSIS] 一致）"""
    overall_style: str = Field(description="整体视觉风格")
    theme: str = Field(description="主题内容")
    mood: str = Field(description="整体情绪调性")
    color_palette: str = Field(description="色彩风格")
    characters: List[GeminiVideoCharacter] = Field(default_factory=list, description="视频中的角色")
    storyboard: List[GeminiVideoStoryboardSegment] = Field(default_factory=list, description="分镜头故事板")
    production_techniques: List[str] = Field(default_factory=list, description="使用的制作技术")
    visual_elements: List[str] = Field(default_factory=list, description="关键视觉元素")
    narrative_structure: str = Field(description="叙事结构")


# ============ 单关键帧反思 VLM 结构化输出（VIDEO_SINGLE_KEYFRAME_REFLECTION_PROMPT）============
class KeyframeReflectionVlmIssue(BaseModel):
    """单关键帧反思中 LLM 返回的单条一致性问题（不含 shot_number，由调用方注入）"""
    character_id: Optional[str] = Field(default=None, description="角色ID")
    character_name: Optional[str] = Field(default=None, description="角色名称")
    issue_type: str = Field(description="问题类型: face/hair/clothing/body/proportion/missing")
    description: str = Field(description="问题描述")
    severity: str = Field(description="严重程度: low/medium/high")
    suggestion: str = Field(default="", description="优化建议")


class KeyframeReflectionVlmStructuredOutput(BaseModel):
    """与 parse_vlm_response 期望的 JSON 字段一致；经 llm_resilience STRUCTURED_CHAT_MESSAGES 解析"""
    needs_regeneration: bool = Field(default=False, description="是否需要重新生成关键帧")
    issues: List[KeyframeReflectionVlmIssue] = Field(default_factory=list, description="发现的问题列表")
    analysis_summary: str = Field(default="", description="分析总结")
    improved_description: str = Field(default="", description="优化后的场景描述")
    improvement_points: List[str] = Field(default_factory=list, description="改进点列表")

