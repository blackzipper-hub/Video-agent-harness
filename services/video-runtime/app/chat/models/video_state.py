"""
VideoAgent 状态定义
"""
from typing import Dict, Any, List, Optional, Annotated, TYPE_CHECKING, Union, Literal
from typing_extensions import TypedDict
from dataclasses import dataclass
from enum import Enum
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage, AIMessage
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
import uuid

from .user_options import UserOption
from .tool_enums import ContentCategory


class VideoSegmentStatus(str, Enum):
    """视频片段合成状态"""
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL_SUCCESS = "partial_success"


# ==================== 视频理解结果 ====================
class VideoUnderstandingResult(BaseModel):
    """视频理解分析结果"""
    is_suitable_for_lipsync: bool = Field(
        description="视频是否适合配音：只有一个主要角色，且所有其他人物（若有）都只是可忽略的背景"
    )
    main_character_count: int = Field(
        description="主要角色数量（清晰可见、占重要位置的角色）"
    )
    background_character_count: int = Field(
        default=0,
        description="背景角色数量（模糊、远距离、不重要的人物）"
    )
    analysis_reason: str = Field(
        description="分析原因和详细说明，解释为什么适合或不适合配音"
    )
    confidence_score: float = Field(
        ge=0.0, le=1.0,
        description="分析结果的置信度（0-1之间）"
    )

# 导入数据库模型类型（用于类型注解）
if TYPE_CHECKING:
    from .video.video_generation import VideoMusicGenerationDB, VideoMusicGenerationVersionDB
    from .video.video_narration import VideoNarrationDB, VideoNarrationVersionDB
    from .video.video_audio_effect import VideoAudioEffectDB, VideoAudioEffectVersionDB

# ==================== 枚举定义 ====================
class CharacterAnalysisAction(str, Enum):
    """角色分析动作枚举"""
    GENERATE_INITIAL = "generate_initial"  # 初始生成
    EXTEND_CHARACTERS = "extend_characters"  # 扩展角色
    NO_ACTION = "no_action"  # 无需操作


# ==================== 用户输入 ====================
class ImageUserInput(BaseModel):
    """图片用户输入"""
    url: str = Field(description="图片URL")
    filename: Optional[str] = Field(default=None, description="原始文件名")
    is_new: bool = Field(default=True, description="是否为本轮新上传的资源")


class AudioFileUserInput(BaseModel):
    """音频文件用户输入"""
    url: str = Field(description="音频文件URL")
    filename: Optional[str] = Field(default=None, description="原始文件名")
    is_new: bool = Field(default=True, description="是否为本轮新上传的资源")
    generated_lyrics: Optional[str] = Field(default=None, description="生成音乐时的歌词（如果是Suno生成的歌曲）")
    duration: Optional[float] = Field(default=None, description="音频时长（秒）")


class VideoFileUserInput(BaseModel):
    """视频文件用户输入"""
    url: str = Field(description="视频文件URL")
    filename: Optional[str] = Field(default=None, description="原始文件名")
    is_new: bool = Field(default=True, description="是否为本轮新上传的资源")


class UserInput(BaseModel):
    """用户输入数据"""
    user_input: str = Field(description="用户输入的文本内容")
    images: List[ImageUserInput] = Field(default_factory=list, description="角色图片或参考图片URL列表")
    audio_files: List[AudioFileUserInput] = Field(default_factory=list, description="音频文件URL列表（MP3等）")
    video_files: List[VideoFileUserInput] = Field(default_factory=list, description="视频文件URL列表（MP4等）")
    user_option: Optional[UserOption] = Field(
        default=None,
        description="用户选择的工具和配置（含 image_generation_tool、video_generation_tool、lipsync_video_tool、aspect_ratio、resolution、duration 等）。口型镜头使用 lipsync_video_tool（LTX 2.3 / Wan 2.5 / Wan 2.6 Flash）。aspect_ratio/resolution 的实际支持范围依赖所选视频/图像工具；详见 GET /agent-router/options/capabilities。"
    )
    agent_type: Optional[str] = Field(default=None, description="前端指定或前一轮确认得到的代理类型：image/video/music/story")
    has_confirmed: bool = Field(default=False, description="用户是否已经确认开始生成；为 True 时会跳过确认对话并直接进入对应生成代理")


# ==================== 视频分析结果 ====================
class VideoStoryboardSegment(BaseModel):
    """视频故事板片段"""
    time_start: str = Field(description="开始时间 (如: 00:00)")
    time_end: str = Field(description="结束时间 (如: 00:03)")
    action_description: str = Field(description="画面内容描述")
    production_method: str = Field(description="制作方式推测")
    visual_style: Optional[str] = Field(default=None, description="视觉风格")
    camera_angle: Optional[str] = Field(default=None, description="镜头角度")
    emotion: Optional[str] = Field(default=None, description="情感调性")


class VideoCharacter(BaseModel):
    """视频中的角色"""
    name: str = Field(description="角色名称或描述")
    description: str = Field(description="角色外观和特征描述")
    role: str = Field(description="角色在视频中的作用")
    appearance_time: Optional[str] = Field(default=None, description="出现时间段")


class VideoAnalysisResult(BaseModel):
    """视频分析结果"""
    overall_style: str = Field(description="整体视觉风格 (如: 动漫风格, 实拍, 混合)")
    theme: str = Field(description="主题内容")
    mood: str = Field(description="整体情绪调性")
    color_palette: str = Field(description="色彩风格")
    characters: List[VideoCharacter] = Field(default_factory=list, description="视频中的角色")
    storyboard: List[VideoStoryboardSegment] = Field(default_factory=list, description="分镜头故事板")
    production_techniques: List[str] = Field(default_factory=list, description="使用的制作技术")
    visual_elements: List[str] = Field(default_factory=list, description="关键视觉元素")
    narrative_structure: str = Field(description="叙事结构")


# ==================== 生成配置 ====================
class GenerationConfig(BaseModel):
    """内容生成配置 - 控制哪些内容需要生成"""
    generate_narration: bool = Field(default=True, description="是否生成旁白")
    generate_music: bool = Field(default=True, description="是否生成BGM")
    generate_audio_effect: bool = Field(default=True, description="是否生成音效")
    
    # 说明原因（用于日志和调试）
    reason: str = Field(default="默认配置", description="配置原因说明")
    
    @classmethod
    def for_audio_driven(cls) -> "GenerationConfig":
        """Audio-driven模式：用户上传了音频，不需要生成音效和旁白"""
        return cls(
            generate_narration=False,
            generate_music=False, # 不生成音乐，因为音频中已经包含音乐，但是需要把transcription中的音乐生成出来
            generate_audio_effect=False,
            reason="Audio-driven模式：用户已上传音频，不需要生成音效和旁白"
        )
    
    @classmethod
    def for_video_with_sound(cls) -> "GenerationConfig":
        """视频自带声音模式：Sora等视频工具已生成声音，不需要生成BGM、音效和旁白"""
        return cls(
            generate_narration=False,
            generate_music=False,
            generate_audio_effect=False,
            reason="视频自带声音模式：视频生成工具（如Sora）已包含声音，不需要额外生成音频内容"
        )
    
    @classmethod
    def default(cls) -> "GenerationConfig":
        """默认模式：生成所有内容"""
        return cls(
            generate_narration=False,
            generate_music=True,
            generate_audio_effect=False,
            reason="默认模式：生成完整的视频、旁白、音效和BGM"
        )

    @classmethod
    def for_product_launch(cls) -> "GenerationConfig":
        """Product Launch：TTS 旁白驱动，不生成 BGM/音效"""
        return cls(
            generate_narration=True,
            generate_music=False,
            generate_audio_effect=False,
            reason="Product Launch：TTS 旁白驱动，不生成 BGM",
        )

    @classmethod
    def for_short_drama(cls) -> "GenerationConfig":
        """Short Drama：片内对白 + 稀疏 TTS 旁白；默认不生成 BGM（避免盖过对白）"""
        return cls(
            generate_narration=True,
            generate_music=False,
            generate_audio_effect=False,
            reason="Short Drama：稀疏 TTS 旁白，默认无 BGM",
        )

# ==================== 角色相关模型 ====================
class VisualElementType(str, Enum):
    """视觉元素类型枚举"""
    CHARACTER = "character"  # 人物角色（主角、配角、旁白者等）
    OBJECT = "object"        # 重要物品（道具、产品、象征物等）
    LOCATION = "location"    # 核心场所（场景、环境、空间等）

class CharacterProfile(BaseModel):
    """视觉元素档案（包含人物角色、重要物品、核心场所）"""
    id: str = Field(description="元素唯一ID")
    type: VisualElementType = Field(description="元素类型：character（人物角色）、object（重要物品）、location（核心场所）")
    name: str = Field(description="元素名称")
    description: str = Field(description="元素描述")
    personality: str = Field(description="性格特点（仅人物角色必需，物品和场所可选）", default="")
    appearance: str = Field(description="外观特征")
    role: Optional[str] = Field(description="在故事中的作用", default="")
    style: Optional[str] = Field(
        description="视觉风格。根据故事主题、风格指南和内容类型推断适合的视觉风格。常见选项：真实摄影风格、电影写实风格、卡通风格、手绘风格、水彩风格、油画风格、像素风格等。", 
        default=""
    )
    body_type: Optional[str] = Field(description="体型大小（人物角色）或尺寸规模（物品和场所）", default="")
    character_image_url: Optional[str] = Field(description="元素图片URL", default="")

class CharacterProfiles(BaseModel):
    """多个视觉元素档案"""
    characters: List[CharacterProfile] = Field(description="视觉元素列表（包含人物角色、重要物品、核心场所）")
    user_message: Optional[str] = Field(description="用用户语言生成的友好消息，简要介绍设计的视觉元素", default="")

# ==================== 视频状态模型 ====================
class GenerationPhase(str, Enum):
    """生成阶段枚举"""
    KEYFRAMES = "keyframes"               # 关键帧生成阶段
    VIDEOS = "videos"                     # 视频生成阶段
    COMPLETED = "completed"               # 完成阶段


class EditingRequestType(str, Enum):
    """编辑请求类型枚举"""
    REGENERATE_STORYBOARD = "regenerate_storyboard"     # 重新生成故事板
    REGENERATE_KEYFRAMES = "regenerate_keyframes"       # 重新生成关键帧
    REGENERATE_VIDEOS = "regenerate_videos"             # 重新生成视频
    KEYFRAMES_EVALUATION = "keyframes_evaluation"       # 关键帧评估
    VIDEOS_EVALUATION = "videos_evaluation"             # 视频评估
    AUTO_EDIT = "auto_edit"                             # 自动剪辑


class VideoAnalysisResult(BaseModel):
    """视频需求分析结果"""
    video_type: str = Field(description="视频类型")
    duration: float = Field(description="视频时长要求（秒）")
    main_character: str = Field(description="主要角色")
    purpose: str = Field(description="制作目的")
    key_elements: List[str] = Field(description="关键要素")
    style_preferences: List[str] = Field(description="风格偏好", default_factory=list)
    target_audience: str = Field(description="目标受众", default="")
    next_action: str = Field(description="下一步动作")
    content_category: Optional[ContentCategory] = Field(default=None, description="内容类别：Default=故事性 MV，Lip-Sync MV=对嘴模板等（与 UserOption 一致用 enum）")
    matched_style_category: Optional[str] = Field(default=None, description="与精选风格库大类的匹配结果：仅当用户风格明确对应某一大类时填写该大类 key，否则不填；后续据此随机选一条二类")
    hidden_style_description: Optional[str] = Field(default=None, description="精选风格提示词描述（匹配到大类后由程序随机取一条，供下游生成用，不展示给前端）")


class ShotLanguage(BaseModel):
    """结构化镜头语言（写入 scene/shot.additional_data.shot_language）。"""
    shot_size: Literal[
        "extreme_wide",
        "wide",
        "medium_wide",
        "medium",
        "medium_close",
        "close_up",
        "extreme_close_up",
        "over_shoulder",
        "insert",
        "establishing",
    ] = Field(description="景别枚举")
    camera_movement: Literal[
        "static",
        "pan_left",
        "pan_right",
        "tilt_up",
        "tilt_down",
        "dolly_in",
        "dolly_out",
        "tracking",
        "crane_up",
        "crane_down",
        "handheld",
        "crash_zoom",
        "whip_pan",
        "orbital",
        "zoom_in",
        "zoom_out",
    ] = Field(description="运镜枚举；static=零运动")
    lens_mm: Literal[24, 35, 50, 85] = Field(default=50, description="焦段 mm")


class StoryboardScene(BaseModel):
    """分镜场景"""
    uuid: Optional[str] = Field(default=None, description="场景UUID")
    scene_number: int = Field(description="场景编号")
    title: str = Field(description="场景标题")
    description: str = Field(description="场景描述")
    duration: float = Field(description="场景时长（秒）")
    camera_angle: str = Field(description="镜头角度")
    character_action: str = Field(description="角色动作")
    visual_style: str = Field(description="视觉风格")
    transition_style: str = Field(description="转场风格")
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    character_ids: List[str] = Field(description="该场景使用的角色ID列表，必须来自characters列表")
    audio_segment_ids: Optional[List[str]] = Field(default=None, description="对应的音频片段ID列表（音频驱动模式）")
    chapter_id: Optional[str] = Field(default=None, description="所属章节ID")
    generation_mode: Optional[str] = Field(default=None, description="生成模式：normal | lipsync | empty_shot（空镜）")
    additional_data: Optional[Dict[str, Any]] = Field(default=None, description="额外数据")


class StoryboardSceneForLLM(BaseModel):
    """场景 LLM 输出：散文字段 + 机器合约字段（程序再 fold 进 additional_data）。"""
    scene_number: int = Field(description="场景编号")
    title: str = Field(description="场景标题")
    description: str = Field(description="场景描述（五维 SUBJECT/SUBJECT MOTION/SCENE/SPATIAL/CAMERA）")
    duration: float = Field(description="场景时长（秒）")
    camera_angle: str = Field(description="镜头角度")
    character_action: str = Field(description="角色动作")
    visual_style: str = Field(description="视觉风格")
    transition_style: str = Field(description="转场风格")
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    character_ids: List[str] = Field(description="该场景使用的角色ID列表，必须来自characters列表")
    audio_segment_ids: Optional[List[str]] = Field(default=None, description="对应的音频片段ID列表（音频驱动模式）")
    generation_mode: Optional[str] = Field(default=None, description="生成模式：normal | lipsync | empty_shot（空镜）")
    shot_language: ShotLanguage = Field(description="结构化镜头语言（必填枚举）")
    action_beats: List[str] = Field(
        min_length=2,
        max_length=6,
        description="2–6 条可碰接触事件（拍/递/敲/鞠躬/推门等），禁止全是缓缓走过/站着展现气场",
    )
    hero_moment: bool = Field(default=False, description="是否本片视觉高潮镜")


class ScenesCollection(BaseModel):
    """场景集合（内存/DB 形态）"""
    scenes: List[StoryboardScene] = Field(description="场景列表")

    def resilience_empty_reason(self) -> Optional[str]:
        if not self.scenes:
            return "scenes is empty"
        return None


class ScenesCollectionForLLM(BaseModel):
    """场景集合（LLM structured output）"""
    scenes: List[StoryboardSceneForLLM] = Field(description="生成的场景列表")

    def resilience_empty_reason(self) -> Optional[str]:
        if not self.scenes:
            return "scenes is empty"
        return None


class EnhancementCue(BaseModel):
    """章节视觉增强线索：供场景层按线索拆镜 / 绑定 additional_data。"""
    type: Literal["broll", "overlay", "animation", "hard_event"] = Field(
        default="broll",
        description="broll=可拆镜画面；overlay=字幕/HUD/标题（不计入拆镜）；animation=特效字/UI动效；hard_event=接触事件线索",
    )
    description: str = Field(description="可成像的短描述：地点/材质/主体动作/机位意图，禁止只写氛围词")
    timestamp_hint: str = Field(
        default="",
        description="章内相对时间，必填，如 0-3 / 8 / mid / late",
    )

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v: Any) -> str:
        s = str(v or "broll").lower().strip()
        if s in ("title", "text", "caption", "subtitle"):
            return "overlay"
        if s not in ("broll", "overlay", "animation", "hard_event"):
            return "broll"
        return s

    @field_validator("timestamp_hint", mode="before")
    @classmethod
    def _coerce_timestamp_hint(cls, v: Any) -> str:
        return "" if v is None else str(v)


class StoryChapter(BaseModel):
    """故事章节"""
    id: str = Field(description="章节唯一ID")
    title: str = Field(description="章节标题")
    description: str = Field(description="章节描述")
    duration: float = Field(description="章节时长（秒）")
    order: int = Field(description="章节顺序（0-based，第一章 order=0）")
    audio_segment_ids: Optional[List[str]] = Field(default=None, description="音频片段UUID列表（仅用于audio driven模式）")
    audio_section_uuid: Optional[str] = Field(default=None, description="关联的曲式段落 UUID（chapter 与 section 1:1）")
    enhancement_cues: Optional[List[EnhancementCue]] = Field(
        default=None,
        description="视觉增强线索；broll/hard_event 可拆镜，overlay/animation 不计入拆镜",
    )

class StoryStructure(BaseModel):
    """故事结构"""
    chapters: List[StoryChapter] = Field(description="章节列表")


# ==================== LLM 输出用（Video / Audio 公用）：章节仅 id/title/description/order，duration 等由程序按模式填充
# Video：程序按 target_duration 均分填 chapter.duration。Audio（曲式必有）：程序按 section 1:1 填 duration、audio_section_uuid、audio_segment_ids。不拆两套 schema，prompt 与 conversion 分支区分即可。
class StoryChapterForLLMMode(BaseModel):
    """故事章节（LLM 输出）：id/title/description/order；无 sections 时可选填 audio_segment_indices（片段编号列表）"""
    id: str = Field(description="章节唯一ID")
    title: str = Field(description="章节标题")
    description: str = Field(description="章节描述")
    order: int = Field(description="章节顺序（0-based，第一章 order=0）")
    audio_segment_indices: Optional[List[int]] = Field(default=None, description="无曲式段落时可选：该章对应的音频片段编号列表（0-based），有曲式时由程序按段落填充，无需填")
    enhancement_cues: Optional[List[EnhancementCue]] = Field(
        default=None,
        description="2–4 条视觉线索；type=broll|overlay|animation|hard_event；每条须填 timestamp_hint（如 0-3）；broll/hard_event 供拆镜，overlay/animation 不计入拆镜",
    )


class StoryStructureForLLMMode(BaseModel):
    """故事结构（LLM 输出）"""
    chapters: List[StoryChapterForLLMMode] = Field(description="章节列表")


class StoryOutlineForLLMMode(BaseModel):
    """故事梗概（LLM 输出）：Video/Audio 公用，程序后续按模式填 duration、audio_section_uuid、audio_segment_ids"""
    title: str = Field(description="故事标题")
    theme: str = Field(description="故事主题")
    structure: StoryStructureForLLMMode = Field(description="章节结构")
    key_message: str = Field(description="核心信息")
    total_duration: int = Field(description="总时长（秒）")
    style_guide: str = Field(description="风格指南")
    description: str = Field(description="详细描述")
    user_message: Optional[str] = Field(default="", description="简短总结消息")


class CharacterNeedsAnalysis(BaseModel):
    """角色需求分析结果"""
    action: CharacterAnalysisAction = Field(description="操作类型")
    reason: str = Field(description="分析原因和详细说明")
    needed_characters: List[str] = Field(default_factory=list, description="需要的角色类型描述列表")
    character_count: int = Field(default=0, description="建议生成的角色数量")


class StoryOutline(BaseModel):
    """故事梗概和分镜大纲（合并）"""
    # === 关联ID ===
    uuid: Optional[str] = Field(default=None, description="故事梗概UUID")
    analysis_uuid: Optional[str] = Field(default=None, description="关联的视频分析UUID")
    conversation_id: Optional[str] = Field(default=None, description="对话ID")
    thread_id: Optional[str] = Field(default=None, description="线程ID")
    user_id: Optional[str] = Field(default=None, description="用户ID")
    run_id: Optional[str] = Field(default=None, description="执行ID")
    
    # === 故事梗概部分 ===
    title: str = Field(description="故事标题，简洁有力，体现核心主题")
    theme: str = Field(description="故事主题，概括故事要表达的核心理念")
    structure: StoryStructure = Field(description="章节结构，描述故事的整体框架和章节安排")
    key_message: str = Field(description="核心信息，故事要传达的主要观点或价值")
    total_duration: float = Field(description="故事总时长（秒），如9.9秒写作10，不要写成9900毫秒")
    style_guide: str = Field(description="风格指南，详细描述视觉风格、色彩方案、表现手法和创作方向")
    description: str = Field(description="详细描述，完整阐述故事内容、情节发展和整体呈现")
    user_message: Optional[str] = Field(default="", description="简短友好的总结消息，用于对话框显示")
    


class DetailedShotLLMOutput(BaseModel):
    """LLM输出的镜头细节（只包含LLM需要生成的字段）"""
    shot_number: int = Field(description="镜头编号（必须与对应场景的scene_number一致）")
    
    # 1️⃣ 镜头构图（Shot Composition）⭐ 新增/扩展
    shot_type: str = Field(description="景别类型 - 9种专业景别：远景/全景/中远景/中景/中近景/近景/特写/插入镜头/主观景别")
    camera_position: Optional[str] = Field(default=None, description="相机机位（Camera Position）：[机位方向] + [机位高度]，如 '斜侧高机位'、'正面低机位'")
    camera_angle: Optional[str] = Field(default=None, description="相机角度（Camera Angle）：平视/仰拍/俯拍/顶拍/低角度/高角度/无人机角度/鸟瞰视角")
    subject_angle: Optional[str] = Field(default=None, description="主体角度（Subject Angle）：正面/三分之二侧/侧面/背面/背三分之二/斜回头/低头/仰头（当景别为远景或无明显主体时可为空）")
    subject_pose: Optional[str] = Field(default=None, description="主体姿势（Subject Pose）：具体可视化的姿势描述，如 '前倾坐姿，双手撑桌'、'奔跑，身体前倾'（当景别为远景或无明显主体时可为空）")
    
    # 2️⃣ 画面内容（Scene Content）
    scene_description: str = Field(description="画面描述（详细描述画面内容、动作、背景等）")
    
    # 3️⃣ 技术执行（Technical Execution）
    camera_movement: str = Field(description="镜头运动（如：静止、推拉、摇移、升降等）")
    lighting: str = Field(description="光影（如：暖色调、冷色调、逆光、侧光等）")
    visual_effects: str = Field(description="特效（如：粒子效果、光效、滤镜等）")
    transition: str = Field(description="转场（如：淡入淡出、切换、溶解等）")
    
    # 4️⃣ 声音设计（Sound Design）
    dialogue: str = Field(description="台词")
    sound_effects: str = Field(description="音效")
    narration: Optional[str] = Field(default=None, description="旁白（可选，由LLM决定是否需要）")
    shot_language: ShotLanguage = Field(description="结构化镜头语言（必填枚举，写入 additional_data）")
    action_beats: List[str] = Field(
        min_length=2,
        max_length=6,
        description="2–6 条可碰接触事件（与 SUBJECT MOTION 对齐），禁止全是缓缓走过",
    )
    hero_moment: bool = Field(default=False, description="是否视觉高潮镜")


class DetailedShot(BaseModel):
    """详细分镜"""
    # === 关联ID ===
    uuid: Optional[str] = Field(default=None, description="详细镜头UUID")
    scene_id: Optional[str] = Field(default=None, description="关联的场景UUID")
    storyboard_detail_id: Optional[str] = Field(default=None, description="关联的详细分镜UUID")
    
    # === 镜头信息 ===
    shot_number: int = Field(description="镜头编号")
    duration: float = Field(description="时长（秒）")
    
    # 1️⃣ 镜头构图（Shot Composition）⭐ 新增/扩展
    shot_type: Optional[str] = Field(default=None, description="景别类型 - 9种专业景别：远景/全景/中远景/中景/中近景/近景/特写/插入镜头/主观景别")
    camera_position: Optional[str] = Field(default=None, description="相机机位（Camera Position）：[机位方向] + [机位高度]，如 '斜侧高机位'、'正面低机位'")
    camera_angle: Optional[str] = Field(default=None, description="相机角度（Camera Angle）：平视/仰拍/俯拍/顶拍/低角度/高角度/无人机角度/鸟瞰视角")
    subject_angle: Optional[str] = Field(default=None, description="主体角度（Subject Angle）：正面/三分之二侧/侧面/背面/背三分之二/斜回头/低头/仰头（当景别为远景或无明显主体时可为空）")
    subject_pose: Optional[str] = Field(default=None, description="主体姿势（Subject Pose）：具体可视化的姿势描述，如 '前倾坐姿，双手撑桌'、'奔跑，身体前倾'（当景别为远景或无明显主体时可为空）")
    
    # 2️⃣ 画面内容（Scene Content）（DB 可空，从 DB 读出时可能为 None）
    scene_description: Optional[str] = Field(default=None, description="画面描述（详细描述画面内容、动作、背景等）")
    
    # 3️⃣ 技术执行（Technical Execution）（DB 可空，从 DB 读出时可能为 None）
    camera_movement: Optional[str] = Field(default=None, description="镜头运动（如：静止、推拉、摇移、升降等）")
    lighting: Optional[str] = Field(default=None, description="光影（如：暖色调、冷色调、逆光、侧光等）")
    visual_effects: Optional[str] = Field(default=None, description="特效（如：粒子效果、光效、滤镜等）")
    transition: Optional[str] = Field(default=None, description="转场（如：淡入淡出、切换、溶解等）")
    
    # 4️⃣ 声音设计（Sound Design）（DB 可空，从 DB 读出时可能为 None）
    dialogue: Optional[str] = Field(default=None, description="台词")
    sound_effects: Optional[str] = Field(default=None, description="音效")
    narration: Optional[str] = Field(default=None, description="旁白（可选，由LLM决定是否需要）")
    
    # === 其他字段 ===
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    character_ids: List[str] = Field(description="该镜头使用的角色ID列表，必须来自outline的characters列表")
    audio_segment_ids: Optional[List[str]] = Field(default=None, description="对应的音频片段ID列表（音频驱动模式）")
    style_guide: Optional[str] = Field(default=None, description="该镜头特有的风格细节，只写本镜头的特殊风格要求（控制在20字以内），不要重复整体风格")
    
    # === 生成模式 ===
    generation_mode: Optional[str] = Field(default=None, description="生成模式：normal=普通视频生成, lipsync=唇形同步视频生成（使用 wan audio_url）, empty_shot=空镜（无人物）")


class StoryboardDetailLLMOutput(BaseModel):
    """LLM输出的详细分镜（只包含LLM需要生成的字段）"""
    shots: List[DetailedShotLLMOutput] = Field(description="详细镜头列表")


class BridgeShotsOutput(BaseModel):
    """衔接片段批量输出"""
    bridge_shots: List[DetailedShot] = Field(description="衔接片段列表，每个片段时长为5秒或10秒")



class Keyframe(BaseModel):
    """关键帧"""
    shot_number: int = Field(description="镜头编号")
    keyframe_url: str = Field(description="关键帧图片URL")
    t2i_prompt: str = Field(description="生成提示词")
    provider: str = Field(description="生成工具提供商（flux/gpt-image）")
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    reference_image_urls: Optional[List[str]] = Field(default=None, description="参考图片URLs列表")


class VideoSegment(BaseModel):
    """视频片段"""
    shot_number: int = Field(description="镜头编号")
    video_url: str = Field(description="视频片段URL")
    duration: int = Field(description="片段时长（秒）")
    i2v_prompt: str = Field(default="", description="图像到视频生成提示词")
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    keyframe_url: str = Field(default="", description="对应的关键帧图片URL")


# ==================== 音频转录相关模型 ====================
class AudioSegment(BaseModel):
    """音频片段（转录结果或音乐分析结果）"""
    uuid: str = Field(default="", description="片段UUID（保存到数据库后生成）")
    id: int = Field(description="片段ID（序号）")
    start: float = Field(description="开始时间（秒）")
    end: float = Field(description="结束时间（秒）")
    text: str = Field(description="转录文本或音乐段落描述")
    duration: float = Field(description="片段时长（秒）")
    emotion: Optional[str] = Field(default=None, description="情感/调性")
    tempo: Optional[str] = Field(default=None, description="速度/动态（仅纯音乐）")
    vocal_presence: Optional[bool] = Field(default=None, description="是否有人声/演唱（lipsync 用）")
    vocal_gender: Optional[str] = Field(default=None, description="人声性别：'f' 女声，'m' 男声")

class AudioWord(BaseModel):
    """音频词汇（Whisper word-level 转录结果）"""
    id: int = Field(description="词汇ID，用于LLM组装字幕")
    word: str = Field(description="词汇文本")
    start: float = Field(description="开始时间（秒）")
    end: float = Field(description="结束时间（秒）")

class AudioTranscription(BaseModel):
    """音频转录结果或音乐分析结果（与 video_audio_transcription 表 / music_mv_three_layer_schema 对齐）"""
    uuid: Optional[str] = Field(default=None, description="转录 UUID（从 DB 加载时设置，供按 transcription 查 sections 等）")
    task: str = Field(description="任务类型")
    language: str = Field(description="识别语言")
    duration: float = Field(description="总时长（秒）")
    text: str = Field(description="完整转录文本或音乐整体描述")
    segments: List[AudioSegment] = Field(description="音频片段列表（转录片段或音乐段落）")
    audio_url: str = Field(description="原始音频文件URL")
    filename: Optional[str] = Field(default=None, description="原始音频文件名")
    is_instrumental: bool = Field(default=False, description="是否为纯音乐（无歌词）")
    additional_data: Optional[dict] = Field(default=None, description="额外数据，存储原始返回的详细信息")
    # 整曲 Global（与 video_audio_transcription 表一致，从 DB 读出或由转录/上游写入 additional_data 后透传）
    song_name: Optional[str] = Field(default=None, description="歌曲名称")
    global_bpm: Optional[float] = Field(default=None, description="整曲 BPM")
    genre: Optional[str] = Field(default=None, description="音乐流派")
    global_emotion: Optional[str] = Field(default=None, description="听觉整体基调")
    suggested_global_theme: Optional[str] = Field(default=None, description="建议核心设计理念")
    suggested_color_palette: Optional[str] = Field(default=None, description="建议整体色彩倾向")

class MusicClipInfo(BaseModel):
    """音乐片段信息"""
    clip_id: str = Field(description="片段ID")
    audio_url: str = Field(description="音频文件URL")
    video_url: Optional[str] = Field(default=None, description="音乐视频URL（如果有）")
    title: Optional[str] = Field(default=None, description="音乐标题")
    tags: Optional[str] = Field(default=None, description="音乐标签")
    lyrics: Optional[str] = Field(default=None, description="歌词内容")
    duration: int = Field(description="音乐时长（秒）")
    image_url: Optional[str] = Field(default=None, description="封面图片URL")


class BackgroundMusic(BaseModel):
    """背景音乐"""
    clips: List[MusicClipInfo] = Field(description="音乐片段列表")
    total_duration: int = Field(description="总时长（秒）")
    provider: str = Field(description="生成服务提供商")
    generated_prompt: str = Field(description="生成时使用的提示词")
    task_id: Optional[str] = Field(default=None, description="任务ID")
    clips_count: int = Field(description="片段数量")
    created_at: Optional[str] = Field(default=None, description="创建时间")
    
    # 便利属性 - 获取主要音频URL（第一个片段）
    @property
    def main_audio_url(self) -> Optional[str]:
        """获取主要音频URL（第一个片段）"""
        return self.clips[0].audio_url if self.clips else None
    
    @property
    def main_title(self) -> Optional[str]:
        """获取主要标题（第一个片段）"""
        return self.clips[0].title if self.clips else None



class FinalVideo(BaseModel):
    """最终视频"""
    video_url: str = Field(description="最终视频URL")
    total_duration: int = Field(description="总时长（秒）")
    shot_count: int = Field(description="镜头数量")
    created_at: str = Field(description="创建时间")
    title: str = Field(description="视频标题")
    metadata: Dict[str, Any] = Field(description="元数据", default_factory=dict)


class VideoAssembly(BaseModel):
    """视频合成结果"""
    uuid: Optional[str] = Field(default=None, description="合成UUID")
    final_video_url: str = Field(description="最终合成的视频URL（有字幕）")
    final_video_url_no_subtitle: Optional[str] = Field(default=None, description="最终合成的视频URL（无字幕版本）")
    total_duration: float = Field(description="总时长（秒）")
    success: bool = Field(description="合成是否成功")
    error_msg: Optional[str] = Field(default=None, description="错误信息")
    
    # 关联的故事大纲
    story_outline_id: str = Field(description="故事大纲UUID")
    
    # 拼接使用的源头资源版本ID（按镜头编号映射到具体版本UUID）
    source_video_versions: Dict[int, str] = Field(default_factory=dict, description="镜头编号 -> 视频版本UUID (video_gen.version_id)")
    source_narration_versions: Dict[int, str] = Field(default_factory=dict, description="镜头编号 -> 旁白版本UUID (narration_version.uuid)")
    source_audio_effect_versions: Dict[int, str] = Field(default_factory=dict, description="镜头编号 -> 音效版本UUID (audio_effect_version.uuid)")
    source_music_versions: Dict[str, str] = Field(default_factory=dict, description="音频片段ID -> 音乐版本UUID (music_version.uuid)")
    
    # 音频资源
    uploaded_audio_files: List[str] = Field(default_factory=list, description="用户上传的音频文件URL列表")
    
    # 源头资源URL（用于下载和重现）
    source_video_urls: Dict[int, str] = Field(default_factory=dict, description="镜头编号 -> 原始视频URL (video_gen.video_url)")
    source_narration_urls: Dict[int, str] = Field(default_factory=dict, description="镜头编号 -> 旁白音频URL (narration_version.audio_url)")
    source_audio_effect_urls: Dict[int, str] = Field(default_factory=dict, description="镜头编号 -> 音效音频URL (audio_effect_version.audio_url)")
    source_music_urls: Dict[str, str] = Field(default_factory=dict, description="音频片段ID -> 音乐音频URL (music_version.music_url)")
    
    # 拼接模式记录
    assembly_mode: str = Field(description="拼接模式: narration_driven, audio_driven, video_driven")
    
    # 元数据
    title: str = Field(description="视频标题")
    created_at: str = Field(description="创建时间")
    additional_data: Optional[Dict[str, Any]] = Field(default=None, description="额外数据")


# ==================== Reducer 函数 ====================

def add_keyframes_with_versions(
    left: List["KeyframeWithVersions"], 
    right: List["KeyframeWithVersions"]
) -> List["KeyframeWithVersions"]:
    """
    合并关键帧版本列表的 reducer 函数
    参考 add_messages 的思想，支持并发更新同一个字段
    
    Args:
        left: 现有的关键帧版本列表
        right: 新的关键帧版本列表
        
    Returns:
        合并后的关键帧版本列表
    """
    if not left:
        return right.copy() if right else []
    if not right:
        return left.copy()
    
    # 创建现有数据的副本和索引
    merged = left.copy()
    merged_by_shot = {kf.shot_number: i for i, kf in enumerate(merged)}
    
    # 处理新数据
    for new_kf in right:
        shot_number = new_kf.shot_number
        
        if shot_number in merged_by_shot:
            # 镜头已存在，合并版本
            existing_idx = merged_by_shot[shot_number]
            existing_kf = merged[existing_idx]
            
            # 合并版本列表，避免重复
            existing_version_ids = {v.version_id for v in existing_kf.versions}
            new_versions = [v for v in new_kf.versions if v.version_id not in existing_version_ids]
            
            if new_versions:
                # 创建新的 KeyframeWithVersions 对象
                updated_versions = existing_kf.versions + new_versions
                merged[existing_idx] = KeyframeWithVersions(
                    shot_number=shot_number,
                    versions=updated_versions,
                    current_index=new_kf.current_index if hasattr(new_kf, 'current_index') else existing_kf.current_index,
                    is_bridge=new_kf.is_bridge,
                    reference_image_urls=new_kf.reference_image_urls or existing_kf.reference_image_urls
                )
        else:
            # 新镜头，直接添加
            merged_by_shot[shot_number] = len(merged)
            merged.append(new_kf)
    
    return merged


def add_videos_with_versions(
    left: List["VideoGenerationWithVersions"], 
    right: List["VideoGenerationWithVersions"]
) -> List["VideoGenerationWithVersions"]:
    """
    合并视频片段版本列表的 reducer 函数
    参考 add_messages 的思想，支持并发更新同一个字段
    
    Args:
        left: 现有的视频片段版本列表
        right: 新的视频片段版本列表
        
    Returns:
        合并后的视频片段版本列表
    """
    if not left:
        return right.copy() if right else []
    if not right:
        return left.copy()
    
    # 创建现有数据的副本和索引
    merged = left.copy()
    merged_by_shot = {vs.shot_number: i for i, vs in enumerate(merged)}
    
    # 处理新数据
    for new_vs in right:
        shot_number = new_vs.shot_number
        
        if shot_number in merged_by_shot:
            # 镜头已存在，合并版本
            existing_idx = merged_by_shot[shot_number]
            existing_vs = merged[existing_idx]
            
            # 合并版本列表，避免重复
            existing_version_ids = {v.version_id for v in existing_vs.versions}
            new_versions = [v for v in new_vs.versions if v.version_id not in existing_version_ids]
            
            if new_versions:
                # 创建新的 VideoSegmentWithVersions 对象
                updated_versions = existing_vs.versions + new_versions
                merged[existing_idx] = VideoGenerationWithVersions(
                    shot_number=shot_number,
                    versions=updated_versions,
                    current_index=new_vs.current_index if hasattr(new_vs, 'current_index') else existing_vs.current_index,
                    is_bridge=new_vs.is_bridge,
                    keyframe_url=new_vs.keyframe_url or existing_vs.keyframe_url
                )
        else:
            # 新镜头，直接添加
            merged_by_shot[shot_number] = len(merged)
            merged.append(new_vs)
    
    return merged


def add_narrations_with_versions(
    left: List["NarrationWithVersions"], 
    right: List["NarrationWithVersions"]
) -> List["NarrationWithVersions"]:
    """
    合并旁白版本列表的 reducer 函数
    参考 add_keyframes_with_versions 的思想，支持并发更新同一个字段
    
    Args:
        left: 现有的旁白版本列表
        right: 新的旁白版本列表
        
    Returns:
        合并后的旁白版本列表
    """
    if not left:
        return right.copy() if right else []
    if not right:
        return left.copy()
    
    # 创建现有数据的副本和索引
    merged = left.copy()
    merged_by_shot = {nr.shot_number: i for i, nr in enumerate(merged)}
    
    # 处理新数据
    for new_nr in right:
        shot_number = new_nr.shot_number
        
        if shot_number in merged_by_shot:
            # 镜头已存在，合并版本
            existing_idx = merged_by_shot[shot_number]
            existing_nr = merged[existing_idx]
            
            # 合并版本列表，避免重复
            existing_version_uuids = {v.uuid for v in existing_nr.versions if v.uuid}
            new_versions = [v for v in new_nr.versions if not v.uuid or v.uuid not in existing_version_uuids]
            
            if new_versions:
                # 创建新的 NarrationWithVersions 对象
                updated_versions = existing_nr.versions + new_versions
                merged[existing_idx] = NarrationWithVersions(
                    shot_number=shot_number,
                    versions=updated_versions,
                    current_index=new_nr.current_index if hasattr(new_nr, 'current_index') else existing_nr.current_index,
                    is_bridge=new_nr.is_bridge,
                    has_narration=new_nr.has_narration or existing_nr.has_narration
                )
        else:
            # 新镜头，直接添加
            merged_by_shot[shot_number] = len(merged)
            merged.append(new_nr)
    
    return merged


def add_audio_effects_with_versions(
    left: List["AudioEffectWithVersions"], 
    right: List["AudioEffectWithVersions"]
) -> List["AudioEffectWithVersions"]:
    """
    合并音效版本列表的reducer函数
    
    Args:
        left: 现有的音效版本列表
        right: 新的音效版本列表
    
    Returns:
        合并后的音效版本列表
    """
    if not left:
        return right if right else []
    
    if not right:
        return left
    
    # 创建一个字典来快速查找现有音效
    existing_audio_effects = {ae.shot_number: ae for ae in left}
    merged = list(left)  # 复制现有列表
    
    # 处理新的音效
    for new_ae in right:
        if new_ae.shot_number in existing_audio_effects:
            # 找到现有音效，合并版本
            existing_ae = existing_audio_effects[new_ae.shot_number]
            
            # 合并版本列表
            existing_versions = {v.version_number: v for v in existing_ae.versions}
            
            for new_version in new_ae.versions:
                existing_versions[new_version.version_number] = new_version
            
            # 更新现有音效的版本列表
            updated_ae = AudioEffectWithVersions(
                shot_number=existing_ae.shot_number,
                versions=list(existing_versions.values()),
                current_index=new_ae.current_index if hasattr(new_ae, 'current_index') else existing_ae.current_index,
                is_bridge=new_ae.is_bridge,
                has_audio_effect=new_ae.has_audio_effect,
                video_generation_id=new_ae.video_generation_id
            )
            
            # 替换列表中的现有音效
            for i, ae in enumerate(merged):
                if ae.shot_number == new_ae.shot_number:
                    merged[i] = updated_ae
                    break
        else:
            # 新音效，直接添加
            merged.append(new_ae)
    
    return merged


class VideoAgentState(TypedDict, total=False):
    """VideoAgent 状态定义"""
    
    # === 基础信息 ===
    user_id: str
    conversation_id: str
    conversation_uuid: Optional[str]  # 对话UUID
    thread_id: str
    run_id: str  # 每次执行的唯一标识
    
    detected_language: str
    
    # === 生成配置 ===
    generation_config: Optional[GenerationConfig]  # 内容生成配置（旁白、音乐、音效）
    actual_target_duration: Optional[float]  # 实际目标时长（秒），来自音乐生成或用户指定
    
    # === 输入数据 ===
    user_input_data: UserInput  # 用户输入数据
    
    # === 数据库UUID引用 ===
    analysis_uuid: Optional[str]                      # VideoAnalysisResult UUID
    audio_transcription_uuids: Optional[List[str]]    # AudioTranscription UUIDs (对应audiofiles)
    story_outline_uuid: Optional[str]                 # StoryOutline UUID
    character_uuids: Optional[List[str]]              # Character UUIDs
    scene_uuids: Optional[List[str]]                  # Scene UUIDs
    shot_uuids: Optional[List[str]]                   # DetailedShot UUIDs
    keyframe_uuids: Optional[List[str]]               # Keyframe UUIDs
    narration_uuids: Optional[List[str]]              # Narration UUIDs
    audio_effect_uuids: Optional[List[str]]           # AudioEffect UUIDs
    video_generation_uuids: Optional[List[str]]       # VideoGeneration UUIDs
    music_generation_uuids: Optional[List[str]]       # MusicGeneration UUIDs (可能有多个音乐生成)
    video_segments_uuids: Optional[List[str]]        # VideoSegments UUIDs (音频驱动的视频片段)
    video_assembly_uuid: Optional[str]               # VideoAssembly UUID

    # === Keyframe Reflection 相关 ===
    reflection_iteration: Optional[int]              # 当前反思迭代次数
    reflection_results: Optional[List[Dict[str, Any]]]  # 反思结果列表
    reflection_message: Optional[str]                 # 反思完成后的总结消息（用于前端展示，与 KEYFRAMES_REFLECTION_COMPLETED 的 message 一致）

    # === 全自动模式（仅 admin 测试用，不暴露前端）===
    full_auto: Optional[bool]  # True 时门控节点不 interrupt，一路执行到完成

    # === 消息历史 ===
    messages: Annotated[List[BaseMessage], add_messages]
        




# ==================== 版本管理模型 ====================

class KeyframeVersion(BaseModel):
    """关键帧版本"""
    version_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="版本ID")
    shot_number: int = Field(description="镜头编号")
    keyframe_url: str = Field(default="", description="关键帧图片URL")
    t2i_prompt: str = Field(description="生成提示词")
    provider: str = Field(description="生成工具提供商")
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    reference_image_urls: Optional[List[str]] = Field(default=None, description="参考图片URLs列表")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="创建时间")
    success: bool = Field(default=True, description="生成是否成功")
    error_msg: Optional[str] = Field(default=None, description="失败时的用户友好错误消息（由LLM生成，不含供应商信息和技术细节）")
    raw_error_msg: Optional[str] = Field(default=None, description="原始错误信息（仅内部调试用，不返回前端）")
    audio_segment_ids: Optional[List[str]] = Field(default=None, description="对应的音频片段ID列表")
    seed: Optional[int] = Field(default=None, description="生成种子")
    aspect_ratio: Optional[str] = Field(default=None, description="图像宽高比")
    resolution: Optional[str] = Field(default=None, description="视频分辨率")
    model: Optional[str] = Field(default=None, description="生成模型")
    image_generation_tool: Optional[str] = Field(default=None, description="图像生成工具枚举值，如 nano_banana/seedream")
    frame_index: int = Field(default=0, description="帧索引：0=首帧, -1=尾帧, 1/2/3=中间帧")
    
    # 关联信息
    keyframe_uuid: Optional[str] = Field(default=None, description="关键帧UUID")
    scene_id: Optional[str] = Field(default=None, description="关联的场景UUID")
    storyboard_detail_id: Optional[str] = Field(default=None, description="关联的详细分镜UUID")
    detailed_shot_id: Optional[str] = Field(default=None, description="关联的详细镜头ID")
    
    # 关联的 AIMessage 信息，用于编辑时获取原始参数
    ai_message: Optional[AIMessage] = Field(default=None, description="完整的 AIMessage，包含 tool_calls")
    ai_messages_json: Optional[str] = Field(default=None, description="序列化的AI消息记录（JSON格式）")

    # Tool 维度（从 ImageGenerationResult 拷贝，供 save_keyframe_to_db -> create_keyframe_version 写入 DB）
    image_tool_metrics: Optional[Dict[str, Any]] = Field(default=None, description="当次 image wrapper metrics")
    tool_duration_sec: Optional[float] = Field(default=None, description="当次调用耗时（秒）")
    tool_cost: Optional[float] = Field(default=None, description="当次调用成本（美元）")
    applied_skill_ids: List[str] = Field(default_factory=list, description="最终 Prompt 实际应用的 Skills")
    constraint_coverage: Dict[str, Any] = Field(default_factory=dict, description="Skill 硬约束覆盖审计")
    final_prompt: Optional[str] = Field(default=None, description="Harness 合并后的最终 Prompt")


class KeyframeWithVersions(BaseModel):
    """带版本管理的关键帧"""
    shot_number: int = Field(description="镜头编号")
    versions: List[KeyframeVersion] = Field(description="所有版本")
    current_index: int = Field(default=0, description="当前选中版本索引")
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    reference_image_urls: Optional[List[str]] = Field(default=None, description="参考图片URLs列表")
    
    @property
    def current_version(self) -> KeyframeVersion:
        """获取当前选中版本"""
        return self.versions[self.current_index]
    
    @property
    def current_keyframe_url(self) -> str:
        """获取当前关键帧URL"""
        return self.current_version.keyframe_url

class VideoGenerationVersion(BaseModel):
    """视频生成版本"""
    version_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="版本ID")
    video_generation_id: Optional[str] = Field(default=None, description="关联的视频生成UUID")
    shot_number: int = Field(description="镜头编号")
    video_url: str = Field(default="", description="视频片段URL")
    duration: int = Field(description="片段时长（秒）")
    i2v_prompt: str = Field(description="图像到视频生成提示词")
    provider: str = Field(default="pollo", description="生成服务提供商")
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    seed: Optional[int] = Field(default=None, description="生成种子")
    resolution: Optional[str] = Field(default=None, description="视频分辨率")
    keyframe_url: str = Field(default="", description="对应的关键帧图片URL")
    keyframe_version_id: Optional[str] = Field(default=None, description="对应的关键帧版本ID（旧架构，向后兼容）")
    keyframe_version_ids: Optional[List[str]] = Field(default=None, description="使用的关键帧版本UUID列表（新架构：首帧version + 尾帧version）")
    aspect_ratio: Optional[str] = Field(default=None, description="视频宽高比")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), description="创建时间")
    success: bool = Field(default=True, description="生成是否成功")
    error_msg: Optional[str] = Field(default=None, description="错误信息（失败时）")
    audio_segment_ids: Optional[List[str]] = Field(default=None, description="对应的音频片段ID列表")
    
    # === 生成模式与音频 ===
    generation_mode: Optional[str] = Field(default=None, description="生成模式：normal | lipsync | empty_shot（空镜）")
    audio_url: Optional[str] = Field(default=None, description="lipsync 模式下使用的音频 URL")
    model: Optional[str] = Field(default=None, description="实际使用的模型（ToolType.value，fallback 后为真实调用模型）")
    video_generation_tool: Optional[str] = Field(default=None, description="用户选择的视频生成工具（VideoGenerationTool 枚举值）")
    
    # 关联的 AIMessage 信息，用于编辑时获取原始参数
    ai_message: Optional[AIMessage] = Field(default=None, description="完整的 AIMessage，包含 tool_calls")
    ai_messages_json: Optional[str] = Field(default=None, description="序列化的AI消息记录（JSON格式）")

    # Wrapper 一致性与耗时（与 image 对齐，供 admin 展示）
    video_tool_metrics: Optional[Dict[str, Any]] = Field(default=None, description="视频 wrapper 一致性/重试等 metrics")
    tool_duration_sec: Optional[float] = Field(default=None, description="本次 tool 调用耗时（秒）")
    tool_cost: Optional[float] = Field(default=None, description="本次 tool 成本（美元）")

    # 首尾帧/一致性原因（来自 prompt 评估，与 video consistency 的 reason 类似，供展示与 fallback 时使用 generated prompt）
    consistency_reason: Optional[str] = Field(default=None, description="需要首尾帧或一致性评估原因（keyframe consistency）")

class VideoGenerationWithVersions(BaseModel):
    """带版本管理的视频生成"""
    shot_number: int = Field(description="镜头编号")
    versions: List[VideoGenerationVersion] = Field(description="所有版本")
    current_index: int = Field(default=0, description="当前选中版本索引")
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    keyframe_url: str = Field(description="对应的关键帧图片URL")
    
    @property
    def current_version(self) -> VideoGenerationVersion:
        """获取当前选中版本"""
        return self.versions[self.current_index]

class NarrationVersion(BaseModel):
    """旁白版本"""
    # === 基础信息 ===
    uuid: Optional[str] = Field(default=None, description="旁白版本UUID")
    version_number: int = Field(description="版本号")
    shot_number: int = Field(description="镜头编号")
    
    # === 旁白内容 ===
    narration_text: str = Field(description="旁白文本")
    enhanced_prompt: str = Field(description="增强的旁白提示词")
    audio_url: Optional[str] = Field(default=None, description="生成的旁白音频URL")
    
    # === 生成参数 ===
    provider: str = Field(default="wavespeed", description="语音合成服务提供商")
    duration: Optional[float] = Field(default=None, description="音频时长（秒）")
    params: Optional[Dict[str, Any]] = Field(default=None, description="生成参数（根据provider不同而不同，如voice_id, emotion等）")
    
    # === 状态信息 ===
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    success: bool = Field(default=True, description="生成是否成功")
    error_msg: Optional[str] = Field(default=None, description="错误信息")
    
    # === 元数据 ===
    created_at: Optional[str] = Field(default=None, description="创建时间")
    ai_message: Optional[AIMessage] = Field(default=None, description="完整的 AIMessage，包含 tool_calls")
    ai_messages_json: Optional[str] = Field(default=None, description="序列化的AI消息记录（JSON格式）")


class NarrationWithVersions(BaseModel):
    """带版本管理的旁白"""
    shot_number: int = Field(description="镜头编号")
    versions: List[NarrationVersion] = Field(description="所有版本")
    current_index: int = Field(default=0, description="当前选中版本索引")
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    has_narration: bool = Field(default=False, description="是否有旁白")
    
    @property
    def current_version(self) -> NarrationVersion:
        """获取当前选中版本"""
        return self.versions[self.current_index]
    
    @property
    def current_audio_url(self) -> Optional[str]:
        """获取当前旁白音频URL"""
        return self.current_version.audio_url if self.versions else None


class AudioEffectVersion(BaseModel):
    """音效版本"""
    # === 基础信息 ===
    uuid: Optional[str] = Field(default=None, description="音效版本UUID")
    version_number: int = Field(description="版本号")
    shot_number: int = Field(description="镜头编号")
    
    # === 音效内容 ===
    video_url: str = Field(description="输入视频URL")
    audio_prompt: str = Field(description="音效描述提示词")
    enhanced_prompt: str = Field(description="增强的音效提示词")
    audio_url: Optional[str] = Field(default=None, description="生成的纯音效音频URL")
    video_with_audio_url: Optional[str] = Field(default=None, description="视频+音效合成URL")
    
    # === 生成参数 ===
    provider: str = Field(default="wavespeed", description="音效生成服务提供商")
    duration: Optional[float] = Field(default=None, description="音频时长（秒）")
    params: Optional[Dict[str, Any]] = Field(default=None, description="生成参数（根据provider不同而不同，如guidance_scale, num_inference_steps等）")
    
    # === 状态信息 ===
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    success: bool = Field(default=True, description="生成是否成功")
    error_msg: Optional[str] = Field(default=None, description="错误信息")
    
    # === 元数据 ===
    created_at: Optional[str] = Field(default=None, description="创建时间")
    ai_message: Optional[AIMessage] = Field(default=None, description="完整的 AIMessage，包含 tool_calls")
    ai_messages_json: Optional[str] = Field(default=None, description="序列化的AI消息记录（JSON格式）")


class AudioEffectWithVersions(BaseModel):
    """带版本管理的音效"""
    shot_number: int = Field(description="镜头编号")
    versions: List[AudioEffectVersion] = Field(description="所有版本")
    current_index: int = Field(default=0, description="当前选中版本索引")
    is_bridge: bool = Field(default=False, description="是否是衔接片段")
    has_audio_effect: bool = Field(default=False, description="是否有音效")
    video_generation_id: str = Field(description="关联的视频生成UUID")
    
    @property
    def current_version(self) -> AudioEffectVersion:
        """获取当前选中版本"""
        return self.versions[self.current_index]
    
    @property
    def current_audio_url(self) -> Optional[str]:
        """获取当前音效音频URL"""
        return self.current_version.audio_url if self.versions else None


class MusicVersion(BaseModel):
    """音乐版本"""
    # === 基础信息 ===
    uuid: Optional[str] = Field(default=None, description="音乐版本UUID")
    version_number: int = Field(description="版本号")
    shot_number: Optional[int] = Field(default=None, description="镜头编号（分段音乐时）")
    
    # === 音乐内容 ===
    music_url: str = Field(description="生成的音乐URL")
    original_audio_url: Optional[str] = Field(default=None, description="原始完整音频URL")
    music_prompt: Optional[str] = Field(default=None, description="音乐生成提示词")
    
    # === 生成参数 ===
    provider: str = Field(default="suno", description="音乐生成服务提供商")
    duration: float = Field(description="音乐时长（秒）")
    is_instrumental: bool = Field(default=True, description="是否是纯音乐（无歌词）")
    
    # === 状态信息 ===
    success: bool = Field(default=True, description="生成是否成功")
    error_msg: Optional[str] = Field(default=None, description="错误信息")
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]] = Field(default=None, description="额外数据（如title, tags, lyrics等）")
    
    # === 元数据 ===
    created_at: Optional[str] = Field(default=None, description="创建时间")
    ai_message: Optional[AIMessage] = Field(default=None, description="完整的 AIMessage，包含 tool_calls")
    ai_messages_json: Optional[str] = Field(default=None, description="序列化的AI消息记录（JSON格式）")


# ==================== 视频合成数据封装 ====================

@dataclass
class CharacterImageInfo:
    """角色图片信息（含 profile 与 version_id，由 _build_character_images_dict 统一填充）"""
    character_id: str
    main_image_url: Optional[str] = None  # 主图URL（匹配的用户图或生成的角色图）
    multiview_url: Optional[str] = None  # 多视角图URL
    main_fusion_url: Optional[str] = None  # 主图融合图URL（如果该角色参与了主图融合）
    multiview_fusion_url: Optional[str] = None  # multiview融合图URL（如果该角色参与了multiview融合）
    profile: Optional['CharacterProfile'] = None  # 角色档案，与 character_images 配套时无需再传 characters_data
    version_id: Optional[str] = None  # 最新关键帧版本 ID，用于保存 keyframe 时关联
    
    def get_image_for_keyframe(self, prefer_multiview: bool = False) -> Optional[str]:
        """
        获取用于关键帧生成的图片
        
        Args:
            prefer_multiview: 是否优先使用multiview（根据shot的角度需求）
        
        Returns:
            图片URL，优先顺序：融合图 > multiview/main（根据prefer_multiview）
        """
        # 如果有融合图，优先使用（融合图可能包含多个角色）
        if prefer_multiview and self.multiview_fusion_url:
            return self.multiview_fusion_url
        if not prefer_multiview and self.main_fusion_url:
            return self.main_fusion_url
        
        # 根据需求选择主图或multiview
        if prefer_multiview and self.multiview_url:
            return self.multiview_url
        
        return self.main_image_url or self.multiview_url
    
    def has_main_image(self) -> bool:
        """检查是否有主图"""
        return self.main_image_url is not None and self.main_image_url != ""
    
    def has_multiview(self) -> bool:
        """检查是否有multiview"""
        return self.multiview_url is not None and self.multiview_url != ""
    
    def has_fusion(self, image_type: str = "main") -> bool:
        """
        检查是否有融合图
        
        Args:
            image_type: "main" 或 "multiview"
        """
        if image_type == "main":
            return self.main_fusion_url is not None and self.main_fusion_url != ""
        else:
            return self.multiview_fusion_url is not None and self.multiview_fusion_url != ""


@dataclass
class MusicAnalysisResult:
    """音乐分析结果"""
    has_music: bool = False
    is_full_story: bool = False  # 是否是整段音乐（Suno）
    is_instrumental: bool = True  # 是否是纯音乐（无歌词）

@dataclass
class MusicData:
    """音乐数据封装"""
    music_generation: 'VideoMusicGenerationDB'
    version: 'VideoMusicGenerationVersionDB'

@dataclass
class VideoAssemblyData:
    """视频合成所需的所有数据封装"""
    # 基础信息
    story_outline: 'StoryOutline'
    video_generations: List['VideoGenerationVersion']
    
    # 音频数据
    narrations_data: Dict[int, Dict[str, Union['VideoNarrationDB', 'VideoNarrationVersionDB']]]  # {shot_number: {'narration': VideoNarrationDB, 'version': VideoNarrationVersionDB}}
    audio_effects_data: Dict[int, Dict[str, Union['VideoAudioEffectDB', 'VideoAudioEffectVersionDB']]]  # {shot_number: {'audio_effect': VideoAudioEffectDB, 'version': VideoAudioEffectVersionDB}}
    music_data: Dict[str, Dict[str, Union['VideoMusicGenerationDB', 'VideoMusicGenerationVersionDB']]] = None  # {audio_segment_id: {'music_generation': VideoMusicGenerationDB, 'version': VideoMusicGenerationVersionDB}}
    audio_transcription: Optional['AudioTranscription'] = None
    uploaded_audio_files: List[str] = None
    
    # 视频片段数据（audio-driven模式）
    video_segments_data: Dict[int, Dict[str, Union['VideoSegmentDB', 'VideoSegmentVersionDB']]] = None  # {segment_number: {'segment': VideoSegmentDB, 'version': VideoSegmentVersionDB}}


    
    def __post_init__(self):
        if self.uploaded_audio_files is None:
            self.uploaded_audio_files = []
        if self.music_data is None:
            self.music_data = {}
        if self.video_segments_data is None:
            self.video_segments_data = {}
    
    @property
    def video_urls(self) -> List[str]:
        """提取成功的视频URL列表"""
        return [v.video_url for v in self.video_generations if v.success and v.video_url]
    
    @property
    def assembly_mode(self) -> str:
        """确定拼接模式：与 execute_assembly_strategy 一致，仅返回 audio_driven / narration_driven / video_driven"""
        # 有音频转录 = 上传音频或 Suno 整曲转录后按片段生成音乐，统一为 audio_driven
        if self.audio_transcription:
            return "audio_driven"
        if self.narrations_data or self.audio_effects_data:
            return "narration_driven"
        return "video_driven"
    


# 重新构建模型以解决前向引用问题
KeyframeVersion.model_rebuild()
# === 视频处理结果模型 ===

class LipsyncResult(BaseModel):
    """唇形同步处理结果"""
    video_segment_uuid: str = Field(description="视频片段UUID")
    success: bool = Field(description="是否成功")
    error: Optional[str] = Field(default=None, description="错误信息")
    lipsync_generation_uuid: Optional[str] = Field(default=None, description="唇形同步生成UUID")
    lipsync_version_uuid: Optional[str] = Field(default=None, description="唇形同步版本UUID")
    lipsync_video_url: Optional[str] = Field(default=None, description="唇形同步视频URL")


class VideoSegmentResult(BaseModel):
    """视频片段处理结果 - 与 VideoSegmentDB 结构保持一致"""
    
    # === 基础信息 ===
    segment_number: int = Field(description="片段编号")
    success: bool = Field(description="是否成功")
    status: VideoSegmentStatus = Field(default=VideoSegmentStatus.SUCCESS, description="合成状态")
    error: Optional[str] = Field(default=None, description="错误信息")
    failed_video_count: int = Field(default=0, description="失败的视频数量")
    total_video_count: int = Field(default=0, description="总视频数量")
    
    # === 关联的资源ID列表（与VideoSegmentDB一致）===
    video_generation_ids: List[str] = Field(description="关联的视频生成UUID列表")
    narration_ids: Optional[List[str]] = Field(default_factory=list, description="关联的旁白UUID列表")
    keyframe_ids: Optional[List[str]] = Field(default_factory=list, description="关联的关键帧UUID列表")
    scene_ids: Optional[List[str]] = Field(default_factory=list, description="关联的场景UUID列表")
    storyboard_detail_ids: Optional[List[str]] = Field(default_factory=list, description="关联的详细分镜UUID列表")
    
    # === 一对一关系的资源ID（与VideoSegmentDB一致）===
    music_generation_id: Optional[str] = Field(default=None, description="关联的音乐生成UUID")
    audio_effect_id: Optional[str] = Field(default=None, description="关联的音效生成UUID")
    lipsync_id: Optional[str] = Field(default=None, description="关联的唇形同步生成UUID")
    
    # === 处理结果 ===
    original_video_urls: Optional[List[str]] = Field(default=None, description="原始视频URL列表")
    merged_video_url: Optional[str] = Field(default=None, description="合并后的视频URL")
    audio_url: Optional[str] = Field(default=None, description="音频URL")
    duration: Optional[float] = Field(default=None, description="时长")
    shot_numbers: Optional[List[int]] = Field(default=None, description="镜头编号列表")
    
    # === 数据库关联 ===
    video_segment_uuid: Optional[str] = Field(default=None, description="视频片段UUID")
    


class AudioVideoMapping(BaseModel):
    """音频视频映射信息"""
    videos: List[str] = Field(description="视频URL列表")
    audio_url: str = Field(description="音频URL")
    duration: float = Field(description="时长")
    shot_numbers: List[int] = Field(description="镜头编号列表")


VideoGenerationVersion.model_rebuild()
KeyframeWithVersions.model_rebuild()
VideoGenerationWithVersions.model_rebuild()
NarrationVersion.model_rebuild()
NarrationWithVersions.model_rebuild()
AudioEffectVersion.model_rebuild()
AudioEffectWithVersions.model_rebuild()
