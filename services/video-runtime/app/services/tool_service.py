"""
工具服务 - 提供可用工具的信息和配置
统一管理所有工具和成本配置
"""
from typing import List, Dict, Any, Optional, Union, FrozenSet, TYPE_CHECKING
from dataclasses import dataclass
import logging
from langsmith import get_current_run_tree

from langchain_core.tools import BaseTool

from ..models.user_options import (
    ImageGenerationTool,
    UserOption,
    VideoGenerationTool,
    DEFAULT_VIDEO_TOOL,
    DEFAULT_IMAGE_TOOL,
)
from ..models.tool_enums import (
    ToolMode, ToolType, ToolProvider, ToolCategory, Resolution, AspectRatio, LLMModel, DefaultValues
)

if TYPE_CHECKING:
    from google import genai

logger = logging.getLogger(__name__)

# ==================== 积分兑换比例配置 ====================
# 钱和积分的兑换比例：1美元 = 100积分
CREDITS_PER_DOLLAR = 100

# 基于 Gemini API 图像生成指南的 Prompt 指导系统
# 参考: https://ai.google.dev/gemini-api/docs/image-generation#prompt-guide
# 按 mode 组织，每个 mode 对应支持的工具和指南内容
IMAGE_PROMPT_GUIDES = {
    ToolMode.T2I.value: {
        "supported_tools": [ImageGenerationTool.NANO_BANANA, ImageGenerationTool.NANO_BANANA_2, ImageGenerationTool.NANO_BANANA_PRO, ImageGenerationTool.SEEDREAM, ImageGenerationTool.GPT_IMAGE_2],
        "guide": """
## 🎨 **Prompts for Generating Images**

### **Core Principle**
**Describe the scene, don't just list keywords.** The model's core strength is its deep language understanding. A narrative, descriptive paragraph will almost always produce a better, more coherent image than a list of disconnected words.

### **1. Photorealistic Scenes**
For realistic images, use photography terms. Mention camera angles, lens types, lighting, and fine details to guide the model toward a photorealistic result.

**Template:**
```
A photorealistic [shot type] of [subject], [action or expression], set in [environment]. 
The scene is illuminated by [lighting description], creating a [mood] atmosphere. 
Captured with a [camera/lens details], emphasizing [key textures and details]. 
```

### **2. Stylized Illustrations & Stickers**
To create stickers, icons, or assets, be explicit about the style and request a transparent background.

**Template:**
```
A [style] sticker of a [subject], featuring [key characteristics] and a [color palette]. 
The design should have [line style] and [shading style]. 
The background must be transparent.
```

### **3. Accurate Text in Images**
Gemini excels at rendering text. Be clear about the text, the font style (descriptively), and the overall design. Use Gemini 3 Pro Image Preview for professional asset production.

**Template:**
```
Create a [image type] for [brand/concept] with the text "[text to render]" in a [font style]. 
The design should be [style description], with a [color scheme].
```

### **4. Product Mockups & Commercial Photography**
Perfect for creating clean, professional product shots for e-commerce, advertising, or branding.

**Template:**
```
A high-resolution, studio-lit product photograph of a [product description] on a [background surface/description]. 
The lighting is a [lighting setup, e.g., three-point softbox setup] to [lighting purpose]. 
The camera angle is a [angle type] to showcase [specific feature]. 
Ultra-realistic, with sharp focus on [key detail].
```

### **5. Minimalist & Negative Space Design**
Excellent for creating backgrounds for websites, presentations, or marketing materials where text will be overlaid.

**Template:**
```
A minimalist composition featuring a single [subject] positioned in the [bottom-right/top-left/etc.] of the frame. 
The background is a vast, empty [color] canvas, creating significant negative space. 
Soft, subtle lighting.
```

### **6. Sequential Art (Comic Panel / Storyboard)**
Builds on character consistency and scene description to create panels for visual storytelling. For accuracy with text and storytelling ability, these prompts work best with Gemini 3 Pro Image Preview.

**Template:**
```
Make a 3 panel comic in a [style]. Put the character in a [type of scene].
```
"""
    },
    
    ToolMode.I2I.value: {
        "supported_tools": [ImageGenerationTool.NANO_BANANA, ImageGenerationTool.NANO_BANANA_2, ImageGenerationTool.NANO_BANANA_PRO, ImageGenerationTool.SEEDREAM, ImageGenerationTool.GPT_IMAGE_2],
        "guide": """
## 🖼️ **Prompts for Editing Images**

These examples show how to provide images alongside your text prompts for editing, composition, and style transfer.

### **1. Adding and Removing Elements**
Provide an image and describe your change. The model will match the original image's style, lighting, and perspective.

**Template:**
```
Using the provided image of [subject], please [add/remove/modify] [element] to/from the scene. 
Ensure the change is [description of how the change should integrate].
```

### **2. Inpainting (Semantic Masking)**
Conversationally define a "mask" to edit a specific part of an image while leaving the rest untouched.

**Template:**
```
Using the provided image, change only the [specific element] to [new element/description]. 
Keep everything else in the image exactly the same, preserving the original style, lighting, and composition.
```

### **3. Style Transfer**
Provide an image and ask the model to recreate its content in a different artistic style.

**Template:**
```
Transform the provided photograph of [subject] into the artistic style of [artist/art style]. 
Preserve the original composition but render it with [description of stylistic elements].
```

### **4. Advanced Composition: Combining Multiple Images**
Provide multiple images as context to create a new, composite scene. This is perfect for product mockups or creative collages.

**Template:**
```
Create a new image by combining the elements from the provided images. 
Take the [element from image 1] and place it with/on the [element from image 2]. 
The final image should be a [description of the final scene].
```
**When keeping a character/animal from image 1:** Add "Ensure that the facial features, facial details, accessories, and clothing of [element from image 1] remain completely unchanged" so face identity, accessories, and clothing are preserved.

**🎯 IMPORTANT: Specify Image Sources**
- Use format: '[specific feature] from image [number]'
- Example: 'long curly brown hair from image 1', 'blue jacket with gold buttons from image 2'
- Avoid vague expressions like "same as reference"

### **5. High-Fidelity Detail Preservation**
To ensure critical details (like a face or logo) are preserved during an edit, describe them in great detail along with your edit request.

**Template:**
```
Using the provided images, place [element from image 2] onto [element from image 1]. 
Ensure that the features of [element from image 1] remain completely unchanged. 
The added element should [description of how the element should integrate].
```
**When [element from image 1] is a character or animal:** Prefer "Ensure that the facial features, facial details, accessories, and clothing of [element from image 1] remain completely unchanged" so the model keeps face identity, accessories, and clothing (五官特征、面部细节、配饰、服装).

### **6. Bring Something to Life**
Upload a rough sketch or drawing and ask the model to refine it into a finished image.

**Template:**
```
Turn this rough [medium] sketch of a [subject] into a [style description] photo. 
Keep the [specific features] from the sketch but add [new details/materials].
```

### **7. Character Consistency: 360 View**
You can generate 360-degree views of a character by iteratively prompting for different angles. For best results, include previously generated images in subsequent prompts to maintain consistency. For complex poses, include a reference image of the desired pose.

**Template:**
```
A studio portrait of [person] against [background], [looking forward/in profile looking right/etc.]
```
"""
    },
    
    ToolMode.ALL.value: {
        "supported_tools": [ImageGenerationTool.NANO_BANANA, ImageGenerationTool.NANO_BANANA_2, ImageGenerationTool.NANO_BANANA_PRO, ImageGenerationTool.SEEDREAM, ImageGenerationTool.GPT_IMAGE_2],
        "guide": """
## 🌟 **Best Practices**

To elevate your results from good to great, incorporate these professional strategies into your workflow.

### **Professional Strategies**

#### **1. Be Hyper-Specific**
The more detail you provide, the more control you have. Instead of "fantasy armor," describe it: "ornate elven plate armor, etched with silver leaf patterns, with a high collar and pauldrons shaped like falcon wings."

#### **2. Provide Context and Intent**
Explain the purpose of the image. The model's understanding of context will influence the final output. For example, "Create a logo for a high-end, minimalist skincare brand" will yield better results than just "Create a logo."

#### **3. Iterate and Refine**
Don't expect a perfect image on the first try. Use the conversational nature of the model to make small changes. Follow up with prompts like, "That's great, but can you make the lighting a bit warmer?" or "Keep everything the same, but change the character's expression to be more serious."

#### **4. Use Step-by-Step Instructions**
For complex scenes with many elements, break your prompt into steps. "First, create a background of a serene, misty forest at dawn. Then, in the foreground, add a moss-covered ancient stone altar. Finally, place a single, glowing sword on top of the altar."

#### **5. Use "Semantic Negative Prompts"**
Instead of saying "no cars," describe the desired scene positively: "an empty, deserted street with no signs of traffic."

#### **6. Control the Camera**
Use photographic and cinematic language to control the composition. Terms like wide-angle shot, macro shot, low-angle perspective.

### **Quality Enhancement Elements**
- **Professional terminology**: Use vocabulary from photography, art, and design fields
- **Technical parameters**: Include resolution, depth of field, aperture and other technical descriptions
- **Emotional expression**: Describe the emotions and atmosphere you want to convey
- **Detail hierarchy**: Layered descriptions from overall to local details

### **Nano Banana Specific Advantages**
- **High-fidelity text rendering**: Accurately generate clear and readable text content
- **Conversational editing**: Support multi-turn conversational image editing and adjustments
- **Multi-image processing**: Excel at fusing different elements from multiple reference images
- **Consistency maintenance**: Maintain visual consistency in multi-image combinations

### **Limitations**
- For best performance, use the following languages: EN, es-MX, ja-JP, zh-CN, hi-IN
- Image generation does not support audio or video inputs
- The model won't always follow the exact number of image outputs that the user explicitly asks for
- The model works best with up to 3 images as an input
- When generating text for an image, Gemini works best if you first generate the text and then ask for an image with the text
- All generated images include a SynthID watermark
"""
    }
}


# ==================== 数据类定义 ====================

@dataclass
class ToolInfo:
    """单个工具信息"""
    tool: BaseTool  # 工具对象（LangChain Tool）
    tool_name: str  # 工具名称（tool.name）
    tool_type: ToolType  # 工具类型（融合的）
    provider: ToolProvider  # 提供商
    category: ToolCategory  # 工具分类（image/video/audio等）
    mode: Optional[ToolMode] = None  # 工具模式（t2i/i2i/t2v/i2v）
    supports_lipsync: bool = False  # 是否支持 generation-time lipsync（audio_url）
    # 视频 I2V：该底层模型在代码中允许的整数秒（与各 tool 内 clamp / 离散档一致）；非视频或未填为 None
    supported_duration_seconds: Optional[FrozenSet[int]] = None


@dataclass
class ToolsInfo:
    """工具信息集合"""
    tools: List[ToolInfo]  # 工具信息列表
    category: ToolCategory  # 工具分类
    user_option_tool: Optional[str] = None  # 用户选择的工具枚举值（保留用于日志）
    fallbacks: Optional[List['ToolsInfo']] = None  # 可选，按顺序的备用工具集（主要用于日志/扩展）

    @property
    def tool_objects(self) -> List[BaseTool]:
        """获取工具对象列表"""
        return [info.tool for info in self.tools]
    
    @property
    def tool_names(self) -> List[str]:
        """获取工具名称列表"""
        return [info.tool_name for info in self.tools]
    
    @property
    def primary_tool(self) -> Optional[BaseTool]:
        """获取第一个工具对象"""
        return self.tools[0].tool if self.tools else None
    
    @property
    def primary_tool_name(self) -> Optional[str]:
        """获取第一个工具名称"""
        return self.tools[0].tool_name if self.tools else None
    
    @property
    def tool_type(self) -> Optional[ToolType]:
        """获取工具类型（第一个工具的）"""
        return self.tools[0].tool_type if self.tools else None
    
    @property
    def provider(self) -> Optional[ToolProvider]:
        """获取提供商（第一个工具的）"""
        return self.tools[0].provider if self.tools else None


# ==================== 用户选项到工具类型的映射 ====================

_USER_OPTION_TO_TOOL_TYPE: Dict[Union[ImageGenerationTool, VideoGenerationTool], ToolType] = {
    # 图像工具（细化到具体模型，3 个 nano banana 独立）
    ImageGenerationTool.NANO_BANANA: ToolType.GEMINI_2_5_FLASH_IMAGE,
    ImageGenerationTool.NANO_BANANA_2: ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW,
    ImageGenerationTool.NANO_BANANA_PRO: ToolType.GEMINI_3_PRO_IMAGE_PREVIEW,
    ImageGenerationTool.SEEDREAM: ToolType.SEEDREAM_V4_5,
    ImageGenerationTool.GPT_IMAGE_2: ToolType.GPT_IMAGE_2,
    ImageGenerationTool.AUTO: ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW,  # 与 DEFAULT_IMAGE_TOOL 链首模型一致
    
    # 视频工具（基础映射，Seedance 需要根据参数选择具体模型）
    VideoGenerationTool.POLLO_SEEDANCE: ToolType.SEEDANCE_V1_PRO_FAST,  # v1 Pro Fast，有尾帧时用 Lite
    VideoGenerationTool.SEEDANCE_V1_5: ToolType.SEEDANCE_V1_5_PRO_FAST,  # v1.5 Pro image-to-video-fast，720p/1080p
    VideoGenerationTool.SEEDANCE_2_I2V: ToolType.SEEDANCE_2_I2V,
    VideoGenerationTool.SEEDANCE_2_I2V_TURBO: ToolType.SEEDANCE_2_I2V_TURBO,
    VideoGenerationTool.SEEDANCE_2_FAST_I2V: ToolType.SEEDANCE_2_FAST_I2V,
    VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO: ToolType.SEEDANCE_2_FAST_I2V_TURBO,
    VideoGenerationTool.WAN_2_5: ToolType.WAN_2_5_I2V,  # 单模型，resolution 为参数
    VideoGenerationTool.WAN_2_6: ToolType.WAN_2_6_FLASH_I2V,  # Wan 2.6 Flash，720p/1080p，enable_audio 影响计费
    VideoGenerationTool.LTX_2_3: ToolType.LTX_2_3_LIPSYNC,  # 主流程 lipsync 默认，audio+image→video
    VideoGenerationTool.KLING_V2_AI_AVATAR_PRO: ToolType.KLING_V2_AI_AVATAR_PRO,
    VideoGenerationTool.WAN_2_2_SPEECH_TO_VIDEO: ToolType.WAN_2_2_SPEECH_TO_VIDEO,
    VideoGenerationTool.KLING_V3_STD: ToolType.KLING_V3_STD,  # Kling v3.0 Std，3-15s，$0.90/5s，sound 1.5x
    VideoGenerationTool.HAPPYHORSE_1_0_I2V: ToolType.HAPPYHORSE_1_0_I2V,  # HappyHorse 1.0，720p/1080p，3-15s
    VideoGenerationTool.HAPPYHORSE_1_1_I2V: ToolType.HAPPYHORSE_1_1_I2V,  # HappyHorse 1.1，720p/1080p，3-15s
    VideoGenerationTool.OPENAI_SORA: ToolType.SORA_2,
    VideoGenerationTool.OPENAI_SORA_PRO: ToolType.SORA_2_PRO,
}


def get_seedance_tool_type(resolution: Resolution, has_end_image: bool) -> ToolType:
    """根据参数选择 Seedance 的具体模型
    
    Args:
        resolution: 分辨率
        has_end_image: 是否有尾帧图片
    
    Returns:
        具体的 ToolType
    """
    if has_end_image:
        if resolution == Resolution.P480:
            return ToolType.SEEDANCE_V1_LITE_I2V_480P
        elif resolution == Resolution.P720:
            return ToolType.SEEDANCE_V1_LITE_I2V_720P
        else:
            return ToolType.SEEDANCE_V1_LITE_I2V_1080P
    else:
        return ToolType.SEEDANCE_V1_PRO_FAST


class ToolService:
    """工具服务类 - 统一管理所有工具和成本配置"""
    
    @classmethod
    def get_max_reference_images_for_keyframe(cls, user_option: Optional['UserOption'] = None) -> int:
        """根据图像生成工具获取 keyframe 生成时的最大参考图片数量
        
        Args:
            user_option: 用户选项配置
            
        Returns:
            int: 最大参考图片数量
        """
        if not user_option:
            from ..models.user_options import UserOption
            user_option = UserOption.default()
        
        from ..models.user_options import ImageGenerationTool
        
        img = user_option.image_generation_tool
        if img == ImageGenerationTool.AUTO:
            img = DEFAULT_IMAGE_TOOL
        if img == ImageGenerationTool.NANO_BANANA:
            return 3  # Nano Banana (2.5 Flash)
        elif img == ImageGenerationTool.NANO_BANANA_2:
            return 4  # Nano Banana 2 (3.1 Flash)，文档 4 张
        elif img == ImageGenerationTool.NANO_BANANA_PRO:
            return 5  # Nano Banana Pro
        elif img == ImageGenerationTool.SEEDREAM:
            return 6  # Seedream（待测试，先填 10）
        elif img == ImageGenerationTool.GPT_IMAGE_2:
            return 6
        else:
            return 3  # 默认值
    
    # ==================== 成本配置 ====================
    
    # 定价配置（细化到具体 ToolType）
    _PRICING_CONFIG = {
        # Nano Banana (Gemini 2.5 Flash Image)
        # 参考: https://ai.google.dev/gemini-api/docs/pricing
        # 注意：此模型专门用于图像生成，输出只有图片，没有文本输出，所以不需要区分 text/image
        # Input: $0.30 per 1M tokens (text/image 统一价格)
        # Output: $0.039 per image 或 $30 per 1M tokens (只有图片输出)
        ToolType.GEMINI_2_5_FLASH_IMAGE: {
            "input_per_1m_tokens": 0.30,      # $0.30 per 1M input tokens (text/image 统一价格)
            "output_per_1m_tokens": 30.00,    # $30 per 1M output tokens (images only, 无文本输出)
            "output_per_image": 0.039,        # $0.039 per image (up to 1024x1024px = 1290 tokens)
            "tokens_per_image_1k": 1290,      # 1290 tokens per image up to 1024x1024px
        },
        # Nano Banana Pro (Gemini 3 Pro Image Preview)
        # 参考: https://ai.google.dev/gemini-api/docs/pricing
        ToolType.GEMINI_3_PRO_IMAGE_PREVIEW: {
            "input_per_1m_tokens": 2.00,      # $2.00 per 1M input tokens (text/image)
            "input_per_image": 0.0011,        # $0.0011 per input image (560 tokens)
            "output_text_per_1m_tokens": 12.00,  # $12.00 per 1M tokens (text and thinking)
            "output_image_per_1m_tokens": 120.00,  # $120 per 1M tokens (images)
            "output_per_image_1k_2k": 0.134,  # $0.134 per 1K/2K image (up to 2048x2048px = 1120 tokens)
            "output_per_image_4k": 0.24,      # $0.24 per 4K image (up to 4096x4096px = 2000 tokens)
            "tokens_per_image_1k_2k": 1120,   # 1120 tokens per 1K/2K image
            "tokens_per_image_4k": 2000,     # 2000 tokens per 4K image
            "tokens_per_input_image": 560,   # 560 tokens per input image
        },
        # Nano Banana 2 (Gemini 3.1 Flash Image Preview) — 便宜版 Pro，Paid Tier: Input $0.25/1M, Output $1.50/1M (text/thinking), $60/1M (images)
        # 1K=1120 tokens=$0.067, 2K=1680=$0.101, 4K=2520=$0.151; 512px=747=$0.045
        ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW: {
            "input_per_1m_tokens": 0.25,      # $0.25 per 1M input tokens (text/image)
            "output_text_per_1m_tokens": 1.50,   # $1.50 per 1M tokens (text and thinking)
            "output_image_per_1m_tokens": 60.00,  # $60 per 1M tokens (images)
            "output_per_image_1k_2k": 0.067,  # 1K 1120 tokens ≈ $0.067; 2K 1680 ≈ $0.101，取 1K 价作 1k_2k 代表
            "output_per_image_4k": 0.151,     # 4K 2520 tokens ≈ $0.151
            "tokens_per_image_1k_2k": 1120,
            "tokens_per_image_4k": 2520,
        },
        # Seedream 定价：$0.04 per generated image（简单，无关参数）
        ToolType.SEEDREAM_V4_5: {
            "per_image": 0.04,
        },
        # OpenAI GPT Image 2（WaveSpeed）：按 quality × resolution（1k/2k/4k），见官方定价表
        ToolType.GPT_IMAGE_2: {
            "matrix": {
                ("low", "1k"): 0.030,
                ("low", "2k"): 0.060,
                ("low", "4k"): 0.090,
                ("medium", "1k"): 0.060,
                ("medium", "2k"): 0.120,
                ("medium", "4k"): 0.180,
                ("high", "1k"): 0.220,
                ("high", "2k"): 0.440,
                ("high", "4k"): 0.660,
            },
        },
        # Seedance 定价（Pro Fast 模型，支持 2-12 秒，按秒计费）
        ToolType.SEEDANCE_V1_PRO_FAST: {
            "per_second_480p": 0.012,  # $0.012 per second (5s = $0.06, 10s = $0.12)
            "per_second_720p": 0.024,  # $0.024 per second (5s = $0.12, 10s = $0.24)
            "per_second_1080p": 0.048,  # $0.048 per second (5s = $0.24, 10s = $0.48)
        },
        # Seedance v1.5 Pro Fast (image-to-video-fast)，720p/1080p，默认不生成语音
        ToolType.SEEDANCE_V1_5_PRO_FAST: {
            "per_second_720p": 0.02,   # $0.10/5s
            "per_second_1080p": 0.03,  # $0.15/5s
        },
        # Seedance 2.0 Fast Image-to-Video（WaveSpeed）：480p $0.50/5s，720p 2×，1080p 5×，4–15s 连续
        ToolType.SEEDANCE_2_I2V: {
            "base_per_5s_480p": 0.60,
            "mult_720p": 2.0,
            "mult_1080p": 5.0,
        },
        ToolType.SEEDANCE_2_FAST_I2V: {
            "base_per_5s_480p": 0.50,
            "mult_720p": 2.0,
            "mult_1080p": 5.0,
        },
        # Seedance 2.0 Fast Image-to-Video Turbo（WaveSpeed）：$0.60/5s（720p）、$0.65/5s（1080p），4–15s连续；480p 走 720p API 按 720p 计
        ToolType.SEEDANCE_2_I2V_TURBO: {
            "base_per_5s_720p": 0.60,
            "base_per_5s_1080p": 0.65,
        },
        ToolType.SEEDANCE_2_FAST_I2V_TURBO: {
            "base_per_5s_720p": 0.60,
            "base_per_5s_1080p": 0.65,
        },
        # Seedance 2.0 Text-to-Video（WaveSpeed）
        # 无 reference_videos：480p $0.60/5s，720p 2×，1080p 5×，4–15s 连续
        # 有 reference_videos：按 Video-Edit 每秒计费（input+output），input=参考视频总时长 clamp 2–15s
        ToolType.SEEDANCE_2_T2V: {
            "base_per_5s_480p": 0.60,
            "mult_720p": 2.0,
            "mult_1080p": 5.0,
            "ref_video_per_second_480p": 0.075,
            "ref_video_per_second_720p": 0.15,
            "ref_video_per_second_1080p": 0.375,
        },
        # Seedance 2.0 Fast Text-to-Video（WaveSpeed）速度优化版，比标准 T2V 便宜
        # 无 reference_videos：480p $0.50/5s，720p 2×，1080p 5×，4–15s 连续
        # 有 reference_videos：按 Fast Video-Edit 每秒计费（input+output），input=参考视频总时长 clamp 2–15s
        ToolType.SEEDANCE_2_FAST_T2V: {
            "base_per_5s_480p": 0.50,
            "mult_720p": 2.0,
            "mult_1080p": 5.0,
            "ref_video_per_second_480p": 0.065,
            "ref_video_per_second_720p": 0.13,
            "ref_video_per_second_1080p": 0.325,
        },
        # Seedance 2.0 Text-to-Video Turbo（WaveSpeed）HD turbo，仅 720p/1080p（480p 按 720p 计）
        # 无 reference_videos：720p $0.70/5s，1080p $0.75/5s
        # 有 reference_videos：720p $1.30/5s，1080p $1.35/5s（按输出时长线性，与参考视频长度无关）
        ToolType.SEEDANCE_2_T2V_TURBO: {
            "base_per_5s_720p": 0.70,
            "base_per_5s_1080p": 0.75,
            "ref_video_per_5s_720p": 1.30,
            "ref_video_per_5s_1080p": 1.35,
        },
        # Seedance 2.0 Fast Text-to-Video Turbo（WaveSpeed）最快最便宜 HD turbo，仅 720p/1080p（480p 按 720p 计）
        # 无 reference_videos：720p $0.60/5s，1080p $0.65/5s
        # 有 reference_videos：720p $1.10/5s，1080p $1.15/5s（按输出时长线性，与参考视频长度无关）
        ToolType.SEEDANCE_2_FAST_T2V_TURBO: {
            "base_per_5s_720p": 0.60,
            "base_per_5s_1080p": 0.65,
            "ref_video_per_5s_720p": 1.10,
            "ref_video_per_5s_1080p": 1.15,
        },
        # Seedance Lite 模型定价（按秒计费）
        ToolType.SEEDANCE_V1_LITE_I2V_480P: {
            "per_second": 0.016,  # $0.016 per second
        },
        ToolType.SEEDANCE_V1_LITE_I2V_720P: {
            "per_second": 0.032,  # $0.032 per second
        },
        ToolType.SEEDANCE_V1_LITE_I2V_1080P: {
            "per_second": 0.09,   # $0.09 per second
        },
        # Wan 2.5 (Alibaba, via WaveSpeed) - 单模型，resolution 为参数，按分辨率按秒计费
        ToolType.WAN_2_5_I2V: {
            "per_second_480p": 0.05,   # $0.05 per second
            "per_second_720p": 0.10,  # $0.10 per second
            "per_second_1080p": 0.15, # $0.15 per second
        },
        # Wan 2.6 (Alibaba, via WaveSpeed) - base $0.125/5s (720p no audio), 1080p 1.5x, audio 2x
        ToolType.WAN_2_6_FLASH_I2V: {
            "base_per_5s": 0.125,       # $0.125 per 5 seconds (720p, no audio)
            "resolution_1080p_mult": 1.5,
            "audio_mult": 2.0,
        },
        # Kling v3.0 Std (WaveSpeed) - base $0.90 per 5s, sound 1.5x
        ToolType.KLING_V3_STD: {
            "base_per_5s": 0.90,
            "sound_mult": 1.5,
        },
        # HappyHorse 1.0 (Alibaba, WaveSpeed) - 720p $0.14/s ($0.70/5s), 1080p $0.28/s ($1.40/5s)
        ToolType.HAPPYHORSE_1_0_I2V: {
            "base_per_5s_720p": 0.70,
            "base_per_5s_1080p": 1.40,
        },
        # HappyHorse 1.1 (Alibaba, WaveSpeed) - 720p $0.70/5s, 1080p $0.945/5s
        ToolType.HAPPYHORSE_1_1_I2V: {
            "base_per_5s_720p": 0.70,
            "base_per_5s_1080p": 0.945,
        },
        # OpenAI Sora 定价（按秒计费，根据 size 区分）
        # 参考: https://openai.com/api/pricing/
        ToolType.SORA_2: {
            # sora-2: Portrait: 720x1280 Landscape: 1280x720 - $0.10 per second
            "720x1280": 0.10,  # per second
            "1280x720": 0.10,  # per second
        },
        ToolType.SORA_2_PRO: {
            # sora-2-pro: Portrait: 720x1280 Landscape: 1280x720 - $0.30 per second
            "720x1280": 0.30,  # per second
            "1280x720": 0.30,  # per second
            # sora-2-pro: Portrait: 1024x1792 Landscape: 1792x1024 - $0.50 per second
            "1024x1792": 0.50,  # per second
            "1792x1024": 0.50,  # per second
        },
        # Suno 定价
        ToolType.CHIRP_V4_5: {
            "per_song": 0.08,
        },
        # MMAudio 定价（TODO: 确认实际价格）
        ToolType.MMAUDIO_V2: {
            "per_second": 0.01,
        },
        # Minimax Speech 2.5 Turbo (WaveSpeed): $0.04 / 1000 chars
        ToolType.MINIMAX_SPEECH_2_5: {
            "per_character": 0.00004,
        },
        # Lipsync 2 Pro 唇形同步 (WaveSpeed) - Base rate: $0.08 per second of audio
        ToolType.LIPSYNC_2_PRO: {
            "per_second": 0.08,
        },
        # LTX 2.3 Lipsync (WaveSpeed) - billable_sec = max(duration_sec, 5), rate per resolution
        ToolType.LTX_2_3_LIPSYNC: {
            "per_second_480p": 0.02,
            "per_second_720p": 0.03,
            "per_second_1080p": 0.04,
            "min_billable_seconds": 5,
        },
        # Kling V2 AI Avatar Pro (WaveSpeed)：按音频计费，min 5s；单价 $0.56/5s ≈ $0.112/s（与 WaveSpeed 标价表一致）
        ToolType.KLING_V2_AI_AVATAR_PRO: {
            "per_second": 0.112,
            "min_billable_seconds": 5,
        },
        # WAN 2.2 Speech-to-Video：标价 $0.15/5s（480p）、$0.30/5s（720p）→ 折合 $0.03/s、$0.06/s；实测账单：不足 5s 按 5s，≥5s 按实际时长线性
        ToolType.WAN_2_2_SPEECH_TO_VIDEO: {
            "per_second_480p": 0.03,
            "per_second_720p": 0.06,
            "min_billable_seconds": 5,
        },
        
        # ==================== LLM 定价配置 ====================
        # OpenAI 模型
        # 参考: https://openai.com/api/pricing/
        LLMModel.GPT_4_1_MINI: {
            "input_per_1m_tokens": 0.40,   # $0.40 per 1M input tokens
            "cached_input_per_1m_tokens": 0.10,  # $0.10 per 1M cached input tokens
            "output_per_1m_tokens": 1.60,  # $1.60 per 1M output tokens
        },
        LLMModel.GPT_5_NANO: {
            "input_per_1m_tokens": 0.05,   # $0.05 per 1M input tokens
            "cached_input_per_1m_tokens": 0.005,  # $0.005 per 1M cached input tokens
            "output_per_1m_tokens": 0.40,  # $0.40 per 1M output tokens
        },
        # https://developers.openai.com/api/docs/pricing （短上下文标准价）
        LLMModel.GPT_5_MINI: {
            "input_per_1m_tokens": 0.25,
            "cached_input_per_1m_tokens": 0.025,
            "output_per_1m_tokens": 2.00,
        },
        LLMModel.GPT_5_4_MINI: {
            "input_per_1m_tokens": 0.75,
            "cached_input_per_1m_tokens": 0.075,
            "output_per_1m_tokens": 4.50,
        },
        LLMModel.GPT_5_4_NANO: {
            "input_per_1m_tokens": 0.20,
            "cached_input_per_1m_tokens": 0.02,
            "output_per_1m_tokens": 1.25,
        },
        LLMModel.GPT_5_6_SOL: {
            "input_per_1m_tokens": 5.00,
            "cached_input_per_1m_tokens": 0.50,
            "output_per_1m_tokens": 30.00,
        },
        LLMModel.GPT_5_6_TERRA: {
            "input_per_1m_tokens": 2.50,
            "cached_input_per_1m_tokens": 0.25,
            "output_per_1m_tokens": 15.00,
        },
        LLMModel.GPT_5_6_LUNA: {
            "input_per_1m_tokens": 1.00,
            "cached_input_per_1m_tokens": 0.10,
            "output_per_1m_tokens": 6.00,
        },
        # Google Gemini 模型
        # 参考: https://ai.google.dev/gemini-api/docs/pricing
        LLMModel.GEMINI_2_5_FLASH: {
            # Input price (text/image/video)
            "input_per_1m_tokens": 0.30,  # $0.30 per 1M tokens (text/image/video)
            "input_audio_per_1m_tokens": 1.00,  # $1.00 per 1M tokens (audio)
            # Output price (including thinking tokens)
            "output_per_1m_tokens": 2.50,  # $2.50 per 1M tokens
            # Context caching price
            "cached_input_per_1m_tokens": 0.03,  # $0.03 per 1M tokens (text/image/video)
            "cached_input_audio_per_1m_tokens": 0.10,  # $0.10 per 1M tokens (audio)
            "cached_storage_per_1m_tokens_per_hour": 1.00,  # $1.00 per 1M tokens per hour (storage)
        },
        LLMModel.GEMINI_3_PRO_PREVIEW: {
            # 分层定价：根据 prompt token 数量
            # Input price
            "input_per_1m_tokens_le_200k": 2.00,  # $2.00 per 1M tokens (prompts ≤ 200k tokens)
            "input_per_1m_tokens_gt_200k": 4.00,  # $4.00 per 1M tokens (prompts > 200k tokens)
            "threshold_tokens": 200_000,  # 200k tokens 阈值
            # Output price (including thinking tokens)
            "output_per_1m_tokens_le_200k": 12.00,  # $12.00 per 1M tokens (prompts ≤ 200k tokens)
            "output_per_1m_tokens_gt_200k": 18.00,  # $18.00 per 1M tokens (prompts > 200k tokens)
            # Context caching price
            "cached_input_per_1m_tokens_le_200k": 0.20,  # $0.20 per 1M tokens (prompts ≤ 200k tokens)
            "cached_input_per_1m_tokens_gt_200k": 0.40,  # $0.40 per 1M tokens (prompts > 200k tokens)
            "cached_storage_per_1m_tokens_per_hour": 4.50,  # $4.50 per 1M tokens per hour (storage)
        },
        LLMModel.GEMINI_3_1_PRO_PREVIEW: {
            # 分层定价：根据 prompt token 数量（与 gemini-3-pro-preview 定价结构相同）
            # Input price
            "input_per_1m_tokens_le_200k": 2.00,  # $2.00 per 1M tokens (prompts ≤ 200k tokens)
            "input_per_1m_tokens_gt_200k": 4.00,  # $4.00 per 1M tokens (prompts > 200k tokens)
            "threshold_tokens": 200_000,  # 200k tokens 阈值
            # Output price (including thinking tokens)
            "output_per_1m_tokens_le_200k": 12.00,  # $12.00 per 1M tokens (prompts ≤ 200k tokens)
            "output_per_1m_tokens_gt_200k": 18.00,  # $18.00 per 1M tokens (prompts > 200k tokens)
            # Context caching price
            "cached_input_per_1m_tokens_le_200k": 0.20,  # $0.20 per 1M tokens (prompts ≤ 200k tokens)
            "cached_input_per_1m_tokens_gt_200k": 0.40,  # $0.40 per 1M tokens (prompts > 200k tokens)
            "cached_storage_per_1m_tokens_per_hour": 4.50,  # $4.50 per 1M tokens per hour (storage)
        },
        LLMModel.GEMINI_2_0_FLASH: {
            # Input price (text/image/video)
            "input_per_1m_tokens": 0.10,  # $0.10 per 1M tokens (text/image/video)
            "input_audio_per_1m_tokens": 0.70,  # $0.70 per 1M tokens (audio)
            # Output price
            "output_per_1m_tokens": 0.40,  # $0.40 per 1M tokens
            # Context caching price
            "cached_input_per_1m_tokens": 0.025,  # $0.025 per 1M tokens (text/image/video)
            "cached_input_audio_per_1m_tokens": 0.175,  # $0.175 per 1M tokens (audio)
            "cached_storage_per_1m_tokens_per_hour": 1.00,  # $1.00 per 1M tokens per hour (storage)
        },
        # Gemini 3 Flash Preview
        # 输入: $0.50/1M (text/image/video), $1.00/1M (audio); 输出: $3.00/1M (含 thinking tokens)
        # 上下文缓存: $0.05/1M (text/image/video), $0.10/1M (audio); 存储 $1.00/1M tokens/小时
        LLMModel.GEMINI_3_FLASH_PREVIEW: {
            "input_per_1m_tokens": 0.50,  # $0.50 per 1M tokens (text/image/video)
            "input_audio_per_1m_tokens": 1.00,  # $1.00 per 1M tokens (audio)
            "output_per_1m_tokens": 3.00,  # $3.00 per 1M tokens (including thinking tokens)
            "cached_input_per_1m_tokens": 0.05,  # $0.05 per 1M tokens (text/image/video)
            "cached_input_audio_per_1m_tokens": 0.10,  # $0.10 per 1M tokens (audio)
            "cached_storage_per_1m_tokens_per_hour": 1.00,  # $1.00 per 1M tokens per hour (storage)
        },
    }
    
    @classmethod
    def calculate_llm_cost(
        cls,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        **kwargs
    ) -> float:
        """计算 LLM 调用成本（兼容方法，内部调用统一的 calculate_cost）
        
        Args:
            model: 模型名称（如 "gpt-4.1-mini", "gemini-2.5-flash"）
            prompt_tokens: 输入 token 数量（总输入 tokens，包括 cached）
            completion_tokens: 输出 token 数量
            **kwargs: 其他参数
                - cached_tokens: 缓存的 token 数量（可选）
                - audio_input_tokens: Audio 输入 token 数量（可选，用于 Gemini）
                - cached_audio_tokens: 缓存的 audio token 数量（可选，用于 Gemini）
        
        Returns:
            成本（美元）
        """
        # 将模型名称转换为 LLMModel enum
        try:
            llm_model = LLMModel(model)
        except ValueError:
            logger.warning(f"Unknown LLM model: {model}, cannot convert to LLMModel enum")
            return 0.0
        
        # 调用统一的方法
        return cls.calculate_cost(
            cost_type=llm_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            **kwargs
        )
    
    @classmethod
    def calculate_cost(
        cls,
        cost_type: Union[ToolType, LLMModel],
        usage_metadata: Optional["genai.types.GenerateContentResponseUsageMetadata"] = None,
        **kwargs
    ) -> float:
        """统一成本计算方法
        
        Args:
            cost_type: 成本类型（ToolType 或 LLMModel）
            usage_metadata: 使用元数据（用于精确计算，可选，仅用于 ToolType）
            **kwargs: 成本计算参数
                - 对于 ToolType: duration, resolution, size, text 等
                - 对于 LLMModel: prompt_tokens, completion_tokens, cached_tokens, audio_input_tokens 等
        
        Returns:
            成本（美元）
        """
        # 根据类型分发
        if isinstance(cost_type, LLMModel):
            return cls._calculate_llm_cost_internal(cost_type, **kwargs)
        elif isinstance(cost_type, ToolType):
            return cls._calculate_tool_cost_internal(cost_type, usage_metadata=usage_metadata, **kwargs)
        else:
            logger.warning(f"Unknown cost type: {cost_type} (type: {type(cost_type)})")
            return 0.0
    
    @classmethod
    def _calculate_tool_cost_internal(
        cls,
        tool_type: ToolType,
        usage_metadata: Optional["genai.types.GenerateContentResponseUsageMetadata"] = None,
        **kwargs
    ) -> float:
        """工具成本计算（内部方法）"""
        # 工具成本计算映射表
        if tool_type in [ToolType.GEMINI_2_5_FLASH_IMAGE, ToolType.GEMINI_3_PRO_IMAGE_PREVIEW, ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW]:
            return cls._calculate_nano_banana_cost(tool_type, usage_metadata=usage_metadata, **kwargs)
        elif tool_type == ToolType.SEEDREAM_V4_5:
            return cls._calculate_seedream_cost(**kwargs)
        elif tool_type == ToolType.GPT_IMAGE_2:
            return cls._calculate_gpt_image_2_cost(**kwargs)
        elif tool_type in [
            ToolType.SEEDANCE_V1_PRO_FAST,
            ToolType.SEEDANCE_V1_5_PRO_FAST,
            ToolType.SEEDANCE_V1_LITE_I2V_480P,
            ToolType.SEEDANCE_V1_LITE_I2V_720P,
            ToolType.SEEDANCE_V1_LITE_I2V_1080P
        ]:
            return cls._calculate_seedance_cost(tool_type, **kwargs)
        elif tool_type == ToolType.WAN_2_5_I2V:
            return cls._calculate_wan25_cost(tool_type, **kwargs)
        elif tool_type == ToolType.WAN_2_6_FLASH_I2V:
            return cls._calculate_wan26_cost(tool_type, **kwargs)
        elif tool_type == ToolType.KLING_V3_STD:
            return cls._calculate_kling_v3_cost(tool_type, **kwargs)
        elif tool_type == ToolType.HAPPYHORSE_1_0_I2V:
            return cls._calculate_happyhorse_1_0_i2v_cost(tool_type, **kwargs)
        elif tool_type == ToolType.HAPPYHORSE_1_1_I2V:
            return cls._calculate_happyhorse_1_1_i2v_cost(tool_type, **kwargs)
        elif tool_type == ToolType.SEEDANCE_2_I2V:
            return cls._calculate_seedance_2_i2v_cost(tool_type, **kwargs)
        elif tool_type == ToolType.SEEDANCE_2_FAST_I2V:
            return cls._calculate_seedance_2_fast_i2v_cost(tool_type, **kwargs)
        elif tool_type == ToolType.SEEDANCE_2_I2V_TURBO:
            return cls._calculate_seedance_2_i2v_turbo_cost(tool_type, **kwargs)
        elif tool_type == ToolType.SEEDANCE_2_FAST_I2V_TURBO:
            return cls._calculate_seedance_2_fast_i2v_turbo_cost(tool_type, **kwargs)
        elif tool_type in (ToolType.SEEDANCE_2_T2V, ToolType.SEEDANCE_2_FAST_T2V):
            return cls._calculate_seedance_2_t2v_cost(tool_type, **kwargs)
        elif tool_type in (ToolType.SEEDANCE_2_T2V_TURBO, ToolType.SEEDANCE_2_FAST_T2V_TURBO):
            return cls._calculate_seedance_2_t2v_turbo_cost(tool_type, **kwargs)
        elif tool_type in [ToolType.SORA_2, ToolType.SORA_2_PRO]:
            return cls._calculate_sora_cost(tool_type, **kwargs)
        elif tool_type == ToolType.CHIRP_V4_5:
            return cls._calculate_suno_cost()
        elif tool_type == ToolType.MMAUDIO_V2:
            return cls._calculate_mmaudio_cost(**kwargs)
        elif tool_type == ToolType.MINIMAX_SPEECH_2_5:
            return cls._calculate_minimax_cost(**kwargs)
        elif tool_type == ToolType.LIPSYNC_2_PRO:
            return cls._calculate_lipsync_cost(**kwargs)
        elif tool_type == ToolType.LTX_2_3_LIPSYNC:
            return cls._calculate_ltx23_lipsync_cost(**kwargs)
        elif tool_type == ToolType.KLING_V2_AI_AVATAR_PRO:
            return cls._calculate_kling_v2_ai_avatar_pro_cost(**kwargs)
        elif tool_type == ToolType.WAN_2_2_SPEECH_TO_VIDEO:
            return cls._calculate_wan22_speech_to_video_cost(**kwargs)
        else:
            logger.warning(f"Unknown tool type for cost calculation: {tool_type}")
            return 0.0
    
    @classmethod
    def _calculate_llm_cost_internal(
        cls,
        llm_model: LLMModel,
        **kwargs
    ) -> float:
        """LLM 成本计算（内部方法）"""
        # LLM 成本计算映射表
        if llm_model == LLMModel.GPT_4_1_MINI:
            return cls._calculate_gpt_4_1_mini_cost(**kwargs)
        elif llm_model == LLMModel.GPT_5_NANO:
            return cls._calculate_gpt_5_nano_cost(**kwargs)
        elif llm_model in (
            LLMModel.GPT_5_MINI,
            LLMModel.GPT_5_4_MINI,
            LLMModel.GPT_5_4_NANO,
            LLMModel.GPT_5_6_SOL,
            LLMModel.GPT_5_6_TERRA,
            LLMModel.GPT_5_6_LUNA,
        ):
            return cls._calculate_openai_token_cost(llm_model, **kwargs)
        elif llm_model == LLMModel.GEMINI_2_5_FLASH:
            return cls._calculate_gemini_2_5_flash_cost(**kwargs)
        elif llm_model == LLMModel.GEMINI_2_0_FLASH:
            return cls._calculate_gemini_2_0_flash_cost(**kwargs)
        elif llm_model == LLMModel.GEMINI_3_FLASH_PREVIEW:
            return cls._calculate_gemini_3_flash_preview_cost(**kwargs)
        elif llm_model == LLMModel.GEMINI_3_PRO_PREVIEW:
            return cls._calculate_gemini_3_pro_preview_cost(**kwargs)
        elif llm_model == LLMModel.GEMINI_3_1_PRO_PREVIEW:
            return cls._calculate_gemini_3_1_pro_preview_cost(**kwargs)
        else:
            logger.warning(f"Unknown LLM model for cost calculation: {llm_model}")
            return 0.0
    
    @classmethod
    def _calculate_gpt_4_1_mini_cost(
        cls,
        **kwargs
    ) -> float:
        """计算 GPT-4.1 Mini 成本"""
        try:
            pricing = cls._PRICING_CONFIG.get(LLMModel.GPT_4_1_MINI)
            if not pricing:
                logger.warning(f"No pricing config for GPT_4_1_MINI")
                return 0.0
            
            prompt_tokens = kwargs.get('prompt_tokens', 0) or 0
            completion_tokens = kwargs.get('completion_tokens', 0) or 0
            cached_tokens = kwargs.get('cached_tokens', 0) or 0
            
            regular_input_tokens = prompt_tokens - cached_tokens
            
            input_cost = 0.0
            if regular_input_tokens > 0:
                input_cost += (regular_input_tokens / 1_000_000) * pricing.get("input_per_1m_tokens", 0.40)
            if cached_tokens > 0:
                input_cost += (cached_tokens / 1_000_000) * pricing.get("cached_input_per_1m_tokens", 0.10)
            
            output_cost = (completion_tokens / 1_000_000) * pricing.get("output_per_1m_tokens", 1.60)
            
            total_cost = input_cost + output_cost
            
            logger.debug(
                f"gpt-4.1-mini cost calculation: "
                f"input_tokens={prompt_tokens} (regular={regular_input_tokens}, cached={cached_tokens}) "
                f"(${input_cost:.6f}) + "
                f"output_tokens={completion_tokens} (${output_cost:.6f}) = ${total_cost:.6f}"
            )
            
            return total_cost
        except Exception as e:
            logger.error(f"Error calculating GPT-4.1 Mini cost: {e}", exc_info=True)
            return 0.0
    
    @classmethod
    def _calculate_gpt_5_nano_cost(
        cls,
        **kwargs
    ) -> float:
        """计算 GPT-5 Nano 成本"""
        return cls._calculate_openai_token_cost(LLMModel.GPT_5_NANO, **kwargs)

    @classmethod
    def _calculate_openai_token_cost(
        cls,
        llm_model: LLMModel,
        **kwargs
    ) -> float:
        """通用 OpenAI token 计价（input / cached / output per 1M）。"""
        try:
            pricing = cls._PRICING_CONFIG.get(llm_model)
            if not pricing:
                logger.warning(f"No pricing config for {llm_model}")
                return 0.0

            prompt_tokens = kwargs.get('prompt_tokens', 0) or 0
            completion_tokens = kwargs.get('completion_tokens', 0) or 0
            cached_tokens = kwargs.get('cached_tokens', 0) or 0
            regular_input_tokens = prompt_tokens - cached_tokens

            input_cost = 0.0
            if regular_input_tokens > 0:
                input_cost += (regular_input_tokens / 1_000_000) * pricing.get("input_per_1m_tokens", 0.0)
            if cached_tokens > 0:
                input_cost += (cached_tokens / 1_000_000) * pricing.get("cached_input_per_1m_tokens", 0.0)

            output_cost = (completion_tokens / 1_000_000) * pricing.get("output_per_1m_tokens", 0.0)
            total_cost = input_cost + output_cost
            logger.debug(
                f"{llm_model.value} cost: input={prompt_tokens} "
                f"(regular={regular_input_tokens}, cached={cached_tokens}) "
                f"${input_cost:.6f} + output={completion_tokens} ${output_cost:.6f} = ${total_cost:.6f}"
            )
            return total_cost
        except Exception as e:
            logger.error(f"Error calculating {llm_model} cost: {e}", exc_info=True)
            return 0.0
    
    @classmethod
    def _calculate_gemini_2_5_flash_cost(
        cls,
        **kwargs
    ) -> float:
        """计算 Gemini 2.5 Flash 成本（支持 audio tokens）"""
        try:
            pricing = cls._PRICING_CONFIG.get(LLMModel.GEMINI_2_5_FLASH)
            if not pricing:
                logger.warning(f"No pricing config for GEMINI_2_5_FLASH")
                return 0.0
            
            prompt_tokens = kwargs.get('prompt_tokens', 0) or 0
            completion_tokens = kwargs.get('completion_tokens', 0) or 0
            cached_tokens = kwargs.get('cached_tokens', 0) or 0
            audio_input_tokens = kwargs.get('audio_input_tokens', 0) or 0
            cached_audio_tokens = kwargs.get('cached_audio_tokens', 0) or 0
            
            regular_input_tokens = prompt_tokens - cached_tokens
            regular_non_audio_tokens = regular_input_tokens - audio_input_tokens
            cached_non_audio_tokens = cached_tokens - cached_audio_tokens
            
            input_cost = 0.0
            # 非音频输入 tokens
            if regular_non_audio_tokens > 0:
                input_cost += (regular_non_audio_tokens / 1_000_000) * pricing.get("input_per_1m_tokens", 0.30)
            if cached_non_audio_tokens > 0:
                input_cost += (cached_non_audio_tokens / 1_000_000) * pricing.get("cached_input_per_1m_tokens", 0.03)
            
            # 音频输入 tokens
            if audio_input_tokens > 0:
                input_cost += (audio_input_tokens / 1_000_000) * pricing.get("input_audio_per_1m_tokens", 1.00)
            if cached_audio_tokens > 0:
                input_cost += (cached_audio_tokens / 1_000_000) * pricing.get("cached_input_audio_per_1m_tokens", 0.10)
            
            output_cost = (completion_tokens / 1_000_000) * pricing.get("output_per_1m_tokens", 2.50)
            
            total_cost = input_cost + output_cost
            
            logger.debug(
                f"gemini-2.5-flash cost calculation: "
                f"input_tokens={prompt_tokens} (regular={regular_input_tokens}, cached={cached_tokens}, "
                f"audio_input={audio_input_tokens}, cached_audio={cached_audio_tokens}) "
                f"(${input_cost:.6f}) + "
                f"output_tokens={completion_tokens} (${output_cost:.6f}) = ${total_cost:.6f}"
            )
            
            return total_cost
        except Exception as e:
            logger.error(f"Error calculating Gemini 2.5 Flash cost: {e}", exc_info=True)
            return 0.0
    
    @classmethod
    def _calculate_gemini_2_0_flash_cost(
        cls,
        **kwargs
    ) -> float:
        """计算 Gemini 2.0 Flash 成本（支持 audio tokens）"""
        try:
            pricing = cls._PRICING_CONFIG.get(LLMModel.GEMINI_2_0_FLASH)
            if not pricing:
                logger.warning(f"No pricing config for GEMINI_2_0_FLASH")
                return 0.0
            
            prompt_tokens = kwargs.get('prompt_tokens', 0) or 0
            completion_tokens = kwargs.get('completion_tokens', 0) or 0
            cached_tokens = kwargs.get('cached_tokens', 0) or 0
            audio_input_tokens = kwargs.get('audio_input_tokens', 0) or 0
            cached_audio_tokens = kwargs.get('cached_audio_tokens', 0) or 0
            
            regular_input_tokens = prompt_tokens - cached_tokens
            regular_non_audio_tokens = regular_input_tokens - audio_input_tokens
            cached_non_audio_tokens = cached_tokens - cached_audio_tokens
            
            input_cost = 0.0
            # 非音频输入 tokens
            if regular_non_audio_tokens > 0:
                input_cost += (regular_non_audio_tokens / 1_000_000) * pricing.get("input_per_1m_tokens", 0.10)
            if cached_non_audio_tokens > 0:
                input_cost += (cached_non_audio_tokens / 1_000_000) * pricing.get("cached_input_per_1m_tokens", 0.025)
            
            # 音频输入 tokens
            if audio_input_tokens > 0:
                input_cost += (audio_input_tokens / 1_000_000) * pricing.get("input_audio_per_1m_tokens", 0.70)
            if cached_audio_tokens > 0:
                input_cost += (cached_audio_tokens / 1_000_000) * pricing.get("cached_input_audio_per_1m_tokens", 0.175)
            
            output_cost = (completion_tokens / 1_000_000) * pricing.get("output_per_1m_tokens", 0.40)
            
            total_cost = input_cost + output_cost
            
            logger.debug(
                f"gemini-2.0-flash cost calculation: "
                f"input_tokens={prompt_tokens} (regular={regular_input_tokens}, cached={cached_tokens}, "
                f"audio_input={audio_input_tokens}, cached_audio={cached_audio_tokens}) "
                f"(${input_cost:.6f}) + "
                f"output_tokens={completion_tokens} (${output_cost:.6f}) = ${total_cost:.6f}"
            )
            
            return total_cost
        except Exception as e:
            logger.error(f"Error calculating Gemini 2.0 Flash cost: {e}", exc_info=True)
            return 0.0
    
    @classmethod
    def _calculate_gemini_3_flash_preview_cost(
        cls,
        **kwargs
    ) -> float:
        """计算 Gemini 3 Flash Preview 成本（支持 audio tokens，与 2.5 Flash 结构一致）"""
        try:
            pricing = cls._PRICING_CONFIG.get(LLMModel.GEMINI_3_FLASH_PREVIEW)
            if not pricing:
                logger.warning(f"No pricing config for GEMINI_3_FLASH_PREVIEW")
                return 0.0
            
            prompt_tokens = kwargs.get('prompt_tokens', 0) or 0
            completion_tokens = kwargs.get('completion_tokens', 0) or 0
            cached_tokens = kwargs.get('cached_tokens', 0) or 0
            audio_input_tokens = kwargs.get('audio_input_tokens', 0) or 0
            cached_audio_tokens = kwargs.get('cached_audio_tokens', 0) or 0
            
            regular_input_tokens = prompt_tokens - cached_tokens
            regular_non_audio_tokens = regular_input_tokens - audio_input_tokens
            cached_non_audio_tokens = cached_tokens - cached_audio_tokens
            
            input_cost = 0.0
            if regular_non_audio_tokens > 0:
                input_cost += (regular_non_audio_tokens / 1_000_000) * pricing.get("input_per_1m_tokens", 0.50)
            if cached_non_audio_tokens > 0:
                input_cost += (cached_non_audio_tokens / 1_000_000) * pricing.get("cached_input_per_1m_tokens", 0.05)
            if audio_input_tokens > 0:
                input_cost += (audio_input_tokens / 1_000_000) * pricing.get("input_audio_per_1m_tokens", 1.00)
            if cached_audio_tokens > 0:
                input_cost += (cached_audio_tokens / 1_000_000) * pricing.get("cached_input_audio_per_1m_tokens", 0.10)
            
            output_cost = (completion_tokens / 1_000_000) * pricing.get("output_per_1m_tokens", 3.00)
            
            total_cost = input_cost + output_cost
            
            logger.debug(
                f"gemini-3-flash-preview cost calculation: "
                f"input_tokens={prompt_tokens} (regular={regular_input_tokens}, cached={cached_tokens}, "
                f"audio_input={audio_input_tokens}, cached_audio={cached_audio_tokens}) "
                f"(${input_cost:.6f}) + "
                f"output_tokens={completion_tokens} (${output_cost:.6f}) = ${total_cost:.6f}"
            )
            
            return total_cost
        except Exception as e:
            logger.error(f"Error calculating Gemini 3 Flash Preview cost: {e}", exc_info=True)
            return 0.0
    
    @classmethod
    def _calculate_gemini_3_pro_preview_cost(
        cls,
        **kwargs
    ) -> float:
        """计算 Gemini 3 Pro Preview 成本（分层定价）"""
        try:
            pricing = cls._PRICING_CONFIG.get(LLMModel.GEMINI_3_PRO_PREVIEW)
            if not pricing:
                logger.warning(f"No pricing config for GEMINI_3_PRO_PREVIEW")
                return 0.0
            
            prompt_tokens = kwargs.get('prompt_tokens', 0) or 0
            completion_tokens = kwargs.get('completion_tokens', 0) or 0
            cached_tokens = kwargs.get('cached_tokens', 0) or 0
            
            regular_input_tokens = prompt_tokens - cached_tokens
            
            # 根据 prompt token 数量选择定价层
            threshold = pricing.get("threshold_tokens", 200_000)
            if prompt_tokens <= threshold:
                input_price = pricing.get("input_per_1m_tokens_le_200k", 2.00)
                cached_input_price = pricing.get("cached_input_per_1m_tokens_le_200k", 0.20)
                output_price = pricing.get("output_per_1m_tokens_le_200k", 12.00)
                tier = "≤200k"
            else:
                input_price = pricing.get("input_per_1m_tokens_gt_200k", 4.00)
                cached_input_price = pricing.get("cached_input_per_1m_tokens_gt_200k", 0.40)
                output_price = pricing.get("output_per_1m_tokens_gt_200k", 18.00)
                tier = ">200k"
            
            input_cost = 0.0
            if regular_input_tokens > 0:
                input_cost += (regular_input_tokens / 1_000_000) * input_price
            if cached_tokens > 0:
                input_cost += (cached_tokens / 1_000_000) * cached_input_price
            
            output_cost = (completion_tokens / 1_000_000) * output_price
            
            total_cost = input_cost + output_cost
            
            logger.debug(
                f"gemini-3-pro-preview cost calculation (tier={tier}): "
                f"input_tokens={prompt_tokens} (regular={regular_input_tokens}, cached={cached_tokens}) "
                f"(${input_cost:.6f}) + "
                f"output_tokens={completion_tokens} (${output_cost:.6f}) = ${total_cost:.6f}"
            )
            
            return total_cost
        except Exception as e:
            logger.error(f"Error calculating Gemini 3 Pro Preview cost: {e}", exc_info=True)
            return 0.0
    
    @classmethod
    def _calculate_gemini_3_1_pro_preview_cost(
        cls,
        **kwargs
    ) -> float:
        """计算 Gemini 3.1 Pro Preview 成本（分层定价，与 3 Pro 相同结构）"""
        try:
            pricing = cls._PRICING_CONFIG.get(LLMModel.GEMINI_3_1_PRO_PREVIEW)
            if not pricing:
                logger.warning(f"No pricing config for GEMINI_3_1_PRO_PREVIEW")
                return 0.0
            
            prompt_tokens = kwargs.get('prompt_tokens', 0) or 0
            completion_tokens = kwargs.get('completion_tokens', 0) or 0
            cached_tokens = kwargs.get('cached_tokens', 0) or 0
            
            regular_input_tokens = prompt_tokens - cached_tokens
            
            threshold = pricing.get("threshold_tokens", 200_000)
            if prompt_tokens <= threshold:
                input_price = pricing.get("input_per_1m_tokens_le_200k", 2.00)
                cached_input_price = pricing.get("cached_input_per_1m_tokens_le_200k", 0.20)
                output_price = pricing.get("output_per_1m_tokens_le_200k", 12.00)
                tier = "≤200k"
            else:
                input_price = pricing.get("input_per_1m_tokens_gt_200k", 4.00)
                cached_input_price = pricing.get("cached_input_per_1m_tokens_gt_200k", 0.40)
                output_price = pricing.get("output_per_1m_tokens_gt_200k", 18.00)
                tier = ">200k"
            
            input_cost = 0.0
            if regular_input_tokens > 0:
                input_cost += (regular_input_tokens / 1_000_000) * input_price
            if cached_tokens > 0:
                input_cost += (cached_tokens / 1_000_000) * cached_input_price
            
            output_cost = (completion_tokens / 1_000_000) * output_price
            
            total_cost = input_cost + output_cost
            
            logger.debug(
                f"gemini-3.1-pro-preview cost calculation (tier={tier}): "
                f"input_tokens={prompt_tokens} (regular={regular_input_tokens}, cached={cached_tokens}) "
                f"(${input_cost:.6f}) + "
                f"output_tokens={completion_tokens} (${output_cost:.6f}) = ${total_cost:.6f}"
            )
            
            return total_cost
        except Exception as e:
            logger.error(f"Error calculating Gemini 3.1 Pro Preview cost: {e}", exc_info=True)
            return 0.0
    
    @classmethod
    def calculate_llm_cost(
        cls,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        **kwargs
    ) -> float:
        """计算 LLM 调用成本（兼容方法，内部调用统一的 calculate_cost）
        
        Args:
            model: 模型名称（如 "gpt-4.1-mini", "gemini-2.5-flash"）
            prompt_tokens: 输入 token 数量（总输入 tokens，包括 cached）
            completion_tokens: 输出 token 数量
            **kwargs: 其他参数
        
        Returns:
            成本（美元）
        """
        # 将模型名称转换为 LLMModel enum
        try:
            llm_model = LLMModel(model)
        except ValueError:
            logger.warning(f"Unknown LLM model: {model}, cannot convert to LLMModel enum")
            return 0.0
        
        # 调用统一的方法
        return cls.calculate_cost(
            cost_type=llm_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            **kwargs
        )
    
    @classmethod
    def _calculate_nano_banana_cost(
        cls,
        tool_type: ToolType,
        usage_metadata: Optional["genai.types.GenerateContentResponseUsageMetadata"] = None,
        **kwargs
    ) -> float:
        """计算 Nano Banana 成本
        
        Args:
            tool_type: 工具类型（GEMINI_2_5_FLASH_IMAGE 或 GEMINI_3_PRO_IMAGE_PREVIEW）
            usage_metadata: GenerateContentResponseUsageMetadata 对象（可选，如果没有则使用默认估算）
            **kwargs: 其他参数（暂未使用）
        
        Returns:
            总成本（美元）
        """
        try:
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                logger.warning(f"No pricing config for {tool_type}")
                return 0.0
            
            # 如果没有 usage_metadata，使用默认估算值
            if usage_metadata is None:
                logger.debug(f"No usage_metadata for {tool_type}, using default estimate")
                if tool_type == ToolType.GEMINI_2_5_FLASH_IMAGE:
                    # 使用固定估算值：$0.039 per image
                    return pricing.get("output_per_image", 0.039)
                elif tool_type in (ToolType.GEMINI_3_PRO_IMAGE_PREVIEW, ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW):
                    # Pro / 3.1 Flash Image：使用 1K/2K 价格作为保守估算
                    return pricing.get("output_per_image_1k_2k", 0.134 if tool_type == ToolType.GEMINI_3_PRO_IMAGE_PREVIEW else 0.067)
                else:
                    return 0.0
            
            # 从 usage_metadata 提取 token 信息
            prompt_token_count = usage_metadata.prompt_token_count or 0
            candidates_token_count = usage_metadata.candidates_token_count or 0
            # 提取详细的 token 信息
            prompt_text_tokens = 0
            prompt_image_tokens = 0
            if usage_metadata.prompt_tokens_details:
                for detail in usage_metadata.prompt_tokens_details:
                    from google.genai import types
                    if detail.modality == types.MediaModality.TEXT:
                        prompt_text_tokens = detail.token_count
                    elif detail.modality == types.MediaModality.IMAGE:
                        prompt_image_tokens = detail.token_count
            
            # 如果没有详细分解，使用总数估算
            if prompt_text_tokens == 0 and prompt_image_tokens == 0:
                # 简单估算：假设都是文本（实际可能包含图片）
                prompt_text_tokens = prompt_token_count
            
            # 提取输出 token 信息
            candidates_image_tokens = 0
            candidates_text_tokens = 0
            if usage_metadata.candidates_tokens_details:
                for detail in usage_metadata.candidates_tokens_details:
                    from google.genai import types
                    if detail.modality == types.MediaModality.IMAGE:
                        candidates_image_tokens = detail.token_count
                    elif detail.modality == types.MediaModality.TEXT:
                        candidates_text_tokens = detail.token_count
            
            # 如果没有详细分解，使用总数（通常是图片）
            if candidates_image_tokens == 0 and candidates_text_tokens == 0:
                candidates_image_tokens = candidates_token_count
            
            # ⭐ Pro 模型：思考过程 token（thoughts_token_count）按文本输出定价
            thoughts_token_count = usage_metadata.thoughts_token_count or 0
            
            # ⭐ 计算输入成本：使用 prompt_token_count（包括所有输入：文本+图片）
            input_cost = (prompt_token_count / 1_000_000) * pricing["input_per_1m_tokens"]
            
            # ⭐ 计算输出成本
            output_cost = 0.0
            if tool_type == ToolType.GEMINI_2_5_FLASH_IMAGE:
                # Gemini 2.5 Flash Image: 只支持图片输出，没有文本输出
                # 参考: https://ai.google.dev/gemini-api/docs/pricing
                # Output price: $0.039 per image 或 $30 per 1M tokens
                if candidates_image_tokens > 0:
                    # 使用 token 计算更精确：$30 per 1M tokens
                    output_cost = (candidates_image_tokens / 1_000_000) * pricing["output_per_1m_tokens"]
                else:
                    # 如果没有 token 信息，使用固定价格（fallback）：$0.039 per image
                    output_cost = pricing["output_per_image"]
                # ⚠️ 注意：Gemini 2.5 Flash Image 不支持文本输出，所以不需要计算 candidates_text_tokens
            elif tool_type in (ToolType.GEMINI_3_PRO_IMAGE_PREVIEW, ToolType.GEMINI_3_1_FLASH_IMAGE_PREVIEW):
                # Gemini 3 Pro / 3.1 Flash Image: 根据图片尺寸计算
                if candidates_image_tokens > 0:
                    # 根据 token 数量判断图片尺寸
                    if candidates_image_tokens >= pricing.get("tokens_per_image_4k", 0):
                        # 4K 图片
                        output_cost = pricing["output_per_image_4k"]
                    else:
                        # 1K/2K 图片
                        output_cost = pricing["output_per_image_1k_2k"]
                else:
                    # 默认使用 1K/2K 价格（fallback）
                    output_cost = pricing["output_per_image_1k_2k"]

                # ⭐ Pro / 3.1 Flash：思考过程 token（thoughts_token_count）按文本输出定价
                if thoughts_token_count > 0 and "output_text_per_1m_tokens" in pricing:
                    thoughts_cost = (thoughts_token_count / 1_000_000) * pricing["output_text_per_1m_tokens"]
                    output_cost += thoughts_cost

                # ⭐ 如果有文本输出（candidates_text_tokens），也需要计算
                if candidates_text_tokens > 0 and "output_text_per_1m_tokens" in pricing:
                    text_output_cost = (candidates_text_tokens / 1_000_000) * pricing["output_text_per_1m_tokens"]
                    output_cost += text_output_cost

            total_cost = input_cost + output_cost
            
            logger.debug(f"{tool_type.value} cost calculation (from usage_metadata): "
                       f"input_tokens={prompt_token_count} (text={prompt_text_tokens}, image={prompt_image_tokens}) "
                       f"(${input_cost:.6f}) + "
                       f"output_tokens={candidates_token_count} (image={candidates_image_tokens}, text={candidates_text_tokens}, thoughts={thoughts_token_count}) "
                       f"(${output_cost:.6f}) = ${total_cost:.6f}")
            
            return total_cost
                
        except Exception as e:
            logger.error(f"Error calculating nano banana cost: {e}", exc_info=True)
            return 0.0
    
    @classmethod
    def _calculate_seedance_cost(
        cls,
        tool_type: ToolType,
        duration: int = 5,
        resolution: Union[Resolution, str] = Resolution.P480
    ) -> float:
        """计算 WaveSpeed Seedance 成本
        
        Pro Fast 模型：支持 2-12 秒（包含），按秒计费，根据 resolution 选择不同的 per_second 价格
        Lite 模型：按秒计费（per_second * duration）
        """
        try:
            if isinstance(resolution, str):
                resolution = Resolution(resolution)
            
            # 限制 duration 在有效范围内（2-12 秒）
            duration = max(2, min(12, duration))
            
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                logger.warning(f"No pricing config for {tool_type}")
                return 0.0
            
            # Lite 模型：按秒计费
            if tool_type in [
                ToolType.SEEDANCE_V1_LITE_I2V_480P,
                ToolType.SEEDANCE_V1_LITE_I2V_720P,
                ToolType.SEEDANCE_V1_LITE_I2V_1080P
            ]:
                per_second = pricing.get("per_second", 0.0)
                cost = per_second * duration
                logger.debug(f"{tool_type.value} cost: {duration}s x ${per_second}/s = ${cost:.4f}")
                return cost
            
            # Pro Fast / v1.5 Pro Fast：按秒计费，根据 resolution 选择 per_second
            else:
                if tool_type == ToolType.SEEDANCE_V1_5_PRO_FAST:
                    # v1.5 仅 720p/1080p，480p 按 720p 计费
                    per_second = pricing.get("per_second_1080p", 0.03) if resolution == Resolution.P1080 else pricing.get("per_second_720p", 0.02)
                elif resolution == Resolution.P480:
                    per_second = pricing.get("per_second_480p", 0.036)
                elif resolution == Resolution.P720:
                    per_second = pricing.get("per_second_720p", 0.072)
                else:  # P1080
                    per_second = pricing.get("per_second_1080p", 0.18)
                cost = per_second * duration
                logger.debug(f"{tool_type.value} cost: {duration}s {resolution.value} x ${per_second}/s = ${cost:.4f}")
                return cost
        except Exception as e:
            logger.error(f"Error calculating seedance cost: {e}")
            return 0.0
    
    @classmethod
    def _calculate_wan25_cost(
        cls,
        tool_type: ToolType,
        duration: int = 5,
        resolution: Union[Resolution, str] = Resolution.P720
    ) -> float:
        """计算 Wan 2.5 成本（单模型，按 resolution：480p $0.05/s, 720p $0.10/s, 1080p $0.15/s）"""
        try:
            if isinstance(resolution, str):
                resolution = Resolution(resolution)
            duration = max(3, min(10, duration))  # Wan 2.5 API: 3–10 秒
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                return 0.0
            if resolution == Resolution.P480:
                per_second = pricing.get("per_second_480p", 0.05)
            elif resolution == Resolution.P720:
                per_second = pricing.get("per_second_720p", 0.10)
            else:
                per_second = pricing.get("per_second_1080p", 0.15)
            cost = per_second * duration
            logger.debug(f"{tool_type.value} cost: {duration}s {resolution.value} x ${per_second}/s = ${cost:.4f}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating wan25 cost: {e}")
            return 0.0
    
    @classmethod
    def _calculate_wan26_cost(
        cls,
        tool_type: ToolType,
        duration: int = 5,
        resolution: Union[Resolution, str] = Resolution.P720,
        enable_audio: bool = True
    ) -> float:
        """Wan 2.6 计费：base $0.125/5s (720p 无音频)，1080p ×1.5，开启音频 ×2"""
        try:
            if isinstance(resolution, str):
                resolution = Resolution(resolution)
            duration = max(3, min(15, duration))
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                return 0.0
            base_per_5s = pricing.get("base_per_5s", 0.125)
            res_mult = pricing.get("resolution_1080p_mult", 1.5) if resolution == Resolution.P1080 else 1.0
            audio_mult = pricing.get("audio_mult", 2.0) if enable_audio else 1.0
            cost = base_per_5s * (duration / 5.0) * res_mult * audio_mult
            logger.debug(
                f"{tool_type.value} cost: {duration}s {resolution.value} audio={enable_audio} "
                f"= 0.125*({duration}/5)*{res_mult}*{audio_mult} = ${cost:.4f}"
            )
            return cost
        except Exception as e:
            logger.error(f"Error calculating wan26 cost: {e}")
            return 0.0

    @classmethod
    def _calculate_kling_v3_cost(
        cls,
        tool_type: ToolType,
        duration: int = 5,
        sound: bool = False,
        **kwargs
    ) -> float:
        """Kling v3.0 Std：$0.90/5s，sound 开启 1.5x。"""
        try:
            duration = max(3, min(15, duration))
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                return 0.0
            base_per_5s = pricing.get("base_per_5s", 0.90)
            sound_mult = pricing.get("sound_mult", 1.5) if sound else 1.0
            cost = base_per_5s * (duration / 5.0) * sound_mult
            logger.debug(f"{tool_type.value} cost: {duration}s sound={sound} = ${cost:.4f}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating kling v3 cost: {e}")
            return 0.0

    @classmethod
    def _calculate_happyhorse_1_0_i2v_cost(
        cls,
        tool_type: ToolType,
        duration: int = 5,
        resolution: Union[Resolution, str] = Resolution.P720,
        **kwargs
    ) -> float:
        """HappyHorse 1.0：720p $0.70/5s，1080p $1.40/5s；480p 按 720p 计费。"""
        try:
            if isinstance(resolution, str):
                resolution = Resolution(resolution)
            duration = max(3, min(15, duration))
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                return 0.0
            if resolution == Resolution.P1080:
                base_per_5s = pricing.get("base_per_5s_1080p", 1.40)
            else:
                base_per_5s = pricing.get("base_per_5s_720p", 0.70)
            cost = base_per_5s * (duration / 5.0)
            logger.debug(f"{tool_type.value} cost: {duration}s {resolution.value} = ${cost:.4f}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating happyhorse 1.0 cost: {e}")
            return 0.0

    @classmethod
    def _calculate_happyhorse_1_1_i2v_cost(
        cls,
        tool_type: ToolType,
        duration: int = 5,
        resolution: Union[Resolution, str] = Resolution.P720,
        **kwargs
    ) -> float:
        """HappyHorse 1.1：720p $0.70/5s，1080p $0.945/5s；480p 按 720p 计费。"""
        try:
            if isinstance(resolution, str):
                resolution = Resolution(resolution)
            duration = max(3, min(15, duration))
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                return 0.0
            if resolution == Resolution.P1080:
                base_per_5s = pricing.get("base_per_5s_1080p", 0.945)
            else:
                base_per_5s = pricing.get("base_per_5s_720p", 0.70)
            cost = base_per_5s * (duration / 5.0)
            logger.debug(f"{tool_type.value} cost: {duration}s {resolution.value} = ${cost:.4f}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating happyhorse 1.1 cost: {e}")
            return 0.0

    @classmethod
    def _calculate_seedance_2_i2v_cost(
        cls,
        tool_type: ToolType,
        duration: int = 5,
        resolution: Union[Resolution, str] = Resolution.P720,
        **kwargs
    ) -> float:
        """Seedance 2.0 I2V：480p $0.60/5s；720p 2×；1080p 5×；时长 4–15s 线性按 5s 块。"""
        try:
            if isinstance(resolution, str):
                resolution = Resolution(resolution)
            duration = max(4, min(15, duration))
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                return 0.0
            base_480 = pricing.get("base_per_5s_480p", 0.60)
            if resolution == Resolution.P1080:
                base = base_480 * pricing.get("mult_1080p", 5.0)
            elif resolution == Resolution.P720:
                base = base_480 * pricing.get("mult_720p", 2.0)
            else:
                base = base_480
            cost = base * (duration / 5.0)
            logger.debug(f"{tool_type.value} cost: {duration}s {resolution.value} = ${cost:.4f}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating seedance 2.0 i2v cost: {e}")
            return 0.0

    @classmethod
    def _calculate_seedance_2_t2v_cost(
        cls,
        tool_type: ToolType,
        duration: int = 5,
        resolution: Union[Resolution, str] = Resolution.P720,
        reference_videos_duration_sec: float = 0.0,
        **kwargs
    ) -> float:
        """Seedance 2.0 T2V 计费。

        无 reference_videos：480p $0.60/5s；720p 2×；1080p 5×；时长 4–15s 线性按 5s 块。
        有 reference_videos：按 Video-Edit 每秒计费 = per_second * (input + output)，
            其中 input = 参考视频总时长 clamp 到 2–15s，output = duration。
            per_second：480p $0.075，720p $0.15，1080p $0.375。
        """
        try:
            if isinstance(resolution, str):
                resolution = Resolution(resolution)
            duration = max(4, min(15, duration))
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                return 0.0

            if reference_videos_duration_sec and reference_videos_duration_sec > 0:
                input_sec = max(2.0, min(15.0, float(reference_videos_duration_sec)))
                if resolution == Resolution.P1080:
                    per_sec = pricing.get("ref_video_per_second_1080p", 0.375)
                elif resolution == Resolution.P720:
                    per_sec = pricing.get("ref_video_per_second_720p", 0.15)
                else:
                    per_sec = pricing.get("ref_video_per_second_480p", 0.075)
                cost = per_sec * (input_sec + duration)
                logger.debug(f"{tool_type.value} cost (ref_video): in={input_sec}s out={duration}s {resolution.value} = ${cost:.4f}")
                return cost

            base_480 = pricing.get("base_per_5s_480p", 0.60)
            if resolution == Resolution.P1080:
                base = base_480 * pricing.get("mult_1080p", 5.0)
            elif resolution == Resolution.P720:
                base = base_480 * pricing.get("mult_720p", 2.0)
            else:
                base = base_480
            cost = base * (duration / 5.0)
            logger.debug(f"{tool_type.value} cost: {duration}s {resolution.value} = ${cost:.4f}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating seedance 2.0 t2v cost: {e}")
            return 0.0

    @classmethod
    def _calculate_seedance_2_t2v_turbo_cost(
        cls,
        tool_type: ToolType,
        duration: int = 5,
        resolution: Union[Resolution, str] = Resolution.P720,
        reference_videos_duration_sec: float = 0.0,
        **kwargs
    ) -> float:
        """Seedance 2.0 T2V Turbo 计费（仅 720p/1080p，480p 按 720p 计）。

        无 reference_videos：720p $0.70/5s，1080p $0.75/5s。
        有 reference_videos：720p $1.30/5s，1080p $1.35/5s。
        两者均按输出时长线性（4–15s 连续，按 5s 块比例），与参考视频长度无关。
        """
        try:
            if isinstance(resolution, str):
                resolution = Resolution(resolution)
            duration = max(4, min(15, duration))
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                return 0.0
            has_ref = bool(reference_videos_duration_sec and reference_videos_duration_sec > 0)
            if resolution == Resolution.P1080:
                per5 = pricing.get("ref_video_per_5s_1080p", 1.35) if has_ref else pricing.get("base_per_5s_1080p", 0.75)
            else:
                per5 = pricing.get("ref_video_per_5s_720p", 1.30) if has_ref else pricing.get("base_per_5s_720p", 0.70)
            cost = per5 * (duration / 5.0)
            logger.debug(f"{tool_type.value} cost ({'ref_video' if has_ref else 'no_ref'}): {duration}s {resolution.value} = ${cost:.4f}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating seedance 2.0 t2v turbo cost: {e}")
            return 0.0

    @classmethod
    def _calculate_seedance_2_fast_i2v_cost(
        cls,
        tool_type: ToolType,
        duration: int = 5,
        resolution: Union[Resolution, str] = Resolution.P720,
        **kwargs
    ) -> float:
        """Seedance 2.0 Fast I2V：480p $0.50/5s；720p 2×；1080p 5×；时长 4–15s 线性按 5s 块。"""
        try:
            if isinstance(resolution, str):
                resolution = Resolution(resolution)
            duration = max(4, min(15, duration))
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                return 0.0
            base_480 = pricing.get("base_per_5s_480p", 0.50)
            if resolution == Resolution.P1080:
                base = base_480 * pricing.get("mult_1080p", 5.0)
            elif resolution == Resolution.P720:
                base = base_480 * pricing.get("mult_720p", 2.0)
            else:
                base = base_480
            cost = base * (duration / 5.0)
            logger.debug(f"{tool_type.value} cost: {duration}s {resolution.value} = ${cost:.4f}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating seedance 2.0 fast i2v cost: {e}")
            return 0.0

    @classmethod
    def _calculate_seedance_2_i2v_turbo_cost(
        cls,
        tool_type: ToolType,
        duration: int = 5,
        resolution: Union[Resolution, str] = Resolution.P720,
        **kwargs
    ) -> float:
        """Seedance 2.0 Turbo：720p $0.60/5s、1080p $0.65/5s；480p 按 720p 计价；时长 4–15s 线性按 5s 块。"""
        try:
            if isinstance(resolution, str):
                resolution = Resolution(resolution)
            duration = max(4, min(15, duration))
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                return 0.0
            if resolution == Resolution.P1080:
                base = pricing.get("base_per_5s_1080p", 0.65)
            else:
                base = pricing.get("base_per_5s_720p", 0.60)
            cost = base * (duration / 5.0)
            logger.debug(f"{tool_type.value} cost: {duration}s {resolution.value} = ${cost:.4f}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating seedance 2.0 turbo cost: {e}")
            return 0.0

    @classmethod
    def _calculate_seedance_2_fast_i2v_turbo_cost(
        cls,
        tool_type: ToolType,
        duration: int = 5,
        resolution: Union[Resolution, str] = Resolution.P720,
        **kwargs
    ) -> float:
        """Seedance 2.0 Fast Turbo：720p $0.60/5s、1080p $0.65/5s；480p 与 720p 同价（API 720p）；时长 4–15s 线性按 5s 块。"""
        try:
            if isinstance(resolution, str):
                resolution = Resolution(resolution)
            duration = max(4, min(15, duration))
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                return 0.0
            if resolution == Resolution.P1080:
                base = pricing.get("base_per_5s_1080p", 0.65)
            else:
                base = pricing.get("base_per_5s_720p", 0.60)
            cost = base * (duration / 5.0)
            logger.debug(f"{tool_type.value} cost: {duration}s {resolution.value} = ${cost:.4f}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating seedance 2.0 fast turbo cost: {e}")
            return 0.0

    @classmethod
    def _calculate_sora_cost(
        cls,
        tool_type: ToolType,
        duration: int = 4,
        size: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
        resolution: Optional[str] = None
    ) -> float:
        """计算 OpenAI Sora 成本（按秒计费，根据 size 区分）
        
        Args:
            tool_type: 工具类型（SORA_2 或 SORA_2_PRO）
            duration: 视频时长（秒），支持 4, 8, 12
            size: Sora size 格式，如 "1280x720", "720x1280", "1024x1792", "1792x1024"
            aspect_ratio: 宽高比，如 "16:9", "9:16"（如果 size 未提供，用于计算 size）
            resolution: 分辨率，如 "1080p", "720p"（如果 size 未提供，用于计算 size）
        
        Returns:
            成本（美元）
        """
        try:
            pricing = cls._PRICING_CONFIG.get(tool_type)
            if not pricing:
                logger.warning(f"No pricing config for {tool_type}")
                return 0.0
            
            # 限制 duration 在有效范围内
            if duration not in [4, 8, 12]:
                duration = 4
            
            # 如果没有提供 size，尝试从 aspect_ratio 和 resolution 计算（需与 sora 模型支持的 size 一致）
            if not size:
                if aspect_ratio and resolution:
                    from ..tools.video.sora import convert_aspect_ratio_and_resolution_to_sora_size
                    sora_model = "sora-2-pro" if tool_type == ToolType.SORA_2_PRO else "sora-2"
                    size = convert_aspect_ratio_and_resolution_to_sora_size(
                        aspect_ratio, resolution, model=sora_model
                    )
                    logger.debug(f"Calculated Sora size from aspect_ratio={aspect_ratio}, resolution={resolution}, model={sora_model}: {size}")
                else:
                    # 使用默认值（根据模型选择）
                    if tool_type == ToolType.SORA_2:
                        # sora-2 默认使用 1280x720
                        size = "1280x720"
                    else:
                        # sora-2-pro 默认使用 1280x720（较低价格）
                        size = "1280x720"
                    logger.debug(f"No size provided, using default: {size}")
            
            # 获取每秒钟的价格
            per_second_price = pricing.get(size)
            if per_second_price is None:
                # 如果找不到对应的 size，使用第一个可用的价格（fallback）
                per_second_price = next(iter(pricing.values()), 0.10)
                logger.warning(f"No pricing for size {size}, using fallback: ${per_second_price}/s")
            
            # 计算总成本 = 每秒钟价格 * 时长
            cost = per_second_price * duration
            
            logger.debug(
                f"{tool_type.value} cost: {duration}s x ${per_second_price}/s (size={size}) = ${cost:.2f}"
            )
            return cost
        except Exception as e:
            logger.error(f"Error calculating sora cost: {e}", exc_info=True)
            return 0.0
    
    @classmethod
    def _calculate_seedream_cost(
        cls,
        mode: Optional[ToolMode] = None,
        output_image_count: int = 1
    ) -> float:
        """计算 Seedream 成本
        
        简单计算：$0.04 per generated image，无关参数
        """
        try:
            pricing = cls._PRICING_CONFIG.get(ToolType.SEEDREAM_V4_5)
            if not pricing:
                logger.warning(f"No pricing config for Seedream")
                return 0.0
            # 使用统一的 per_image 价格（t2i 和 i2i 都是 $0.04）
            cost_per_image = pricing.get("per_image", 0.04)
            cost = cost_per_image * output_image_count
            logger.debug(f"Seedream cost: ${cost:.6f} (${cost_per_image:.2f} x {output_image_count} images)")
            return cost
        except Exception as e:
            logger.error(f"Error calculating seedream cost: {e}")
            return 0.0
    
    @classmethod
    def _calculate_gpt_image_2_cost(
        cls,
        gpt_image_resolution: str = "2k",
        gpt_image_quality: str = "medium",
    ) -> float:
        """GPT Image 2（WaveSpeed）按 quality 与 API resolution（1k/2k/4k）计费。"""
        try:
            pricing = cls._PRICING_CONFIG.get(ToolType.GPT_IMAGE_2)
            if not pricing:
                logger.warning("No pricing config for GPT_IMAGE_2")
                return 0.0
            matrix = pricing.get("matrix") or {}
            res = (gpt_image_resolution or "2k").strip().lower()
            qual = (gpt_image_quality or "medium").strip().lower()
            if res not in ("1k", "2k", "4k"):
                res = "2k"
            if qual not in ("low", "medium", "high"):
                qual = "medium"
            cost = float(matrix.get((qual, res), 0.12))
            logger.debug(f"GPT_IMAGE_2 cost: quality={qual}, resolution={res} -> ${cost:.6f}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating GPT Image 2 cost: {e}")
            return 0.0
    
    @classmethod
    def _calculate_suno_cost(cls) -> float:
        """计算 Suno 音乐生成成本（固定 $0.08 per song）"""
        try:
            pricing = cls._PRICING_CONFIG.get(ToolType.CHIRP_V4_5)
            if not pricing:
                logger.warning(f"No pricing config for Suno")
                return 0.0
            cost = pricing.get("per_song", 0.08)
            logger.debug(f"Suno cost: ${cost} per song")
            return cost
        except Exception as e:
            logger.error(f"Error calculating suno cost: {e}")
            return 0.0
    
    @classmethod
    def _calculate_mmaudio_cost(
        cls,
        duration: float = 8.0
    ) -> float:
        """计算 MMAudio 音效生成成本"""
        try:
            pricing = cls._PRICING_CONFIG.get(ToolType.MMAUDIO_V2)
            if not pricing:
                logger.warning(f"No pricing config for MMAudio")
                return 0.0
            cost = pricing.get("per_second", 0.01) * duration
            logger.debug(f"MMAudio cost: {duration}s = ${cost}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating mmaudio cost: {e}")
            return 0.0
    
    @classmethod
    def _calculate_minimax_cost(
        cls,
        text: str
    ) -> float:
        """计算 Minimax Speech 语音合成成本"""
        try:
            pricing = cls._PRICING_CONFIG.get(ToolType.MINIMAX_SPEECH_2_5)
            if not pricing:
                logger.warning(f"No pricing config for Minimax Speech")
                return 0.0
            char_count = len(text)
            cost = pricing.get("per_character", 0.0001) * char_count
            logger.debug(f"Minimax Speech cost: {char_count} chars = ${cost}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating minimax cost: {e}")
            return 0.0
    
    @classmethod
    def _calculate_lipsync_cost(
        cls,
        duration: float
    ) -> float:
        """计算 Lipsync 2 Pro 唇形同步成本（$0.08/秒音频）"""
        try:
            pricing = cls._PRICING_CONFIG.get(ToolType.LIPSYNC_2_PRO)
            if not pricing:
                logger.warning(f"No pricing config for Lipsync 2 Pro")
                return 0.0
            cost = pricing.get("per_second", 0.08) * duration
            logger.debug(f"Lipsync 2 Pro cost: {duration}s = ${cost}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating lipsync cost: {e}")
            return 0.0

    @classmethod
    def _calculate_ltx23_lipsync_cost(
        cls,
        duration: float,
        resolution=None,
        **kwargs
    ) -> float:
        """计算 LTX 2.3 Lipsync 成本：billable_sec = max(duration, 5)，cost = billable_sec × rate[resolution]。"""
        try:
            from ..models.tool_enums import Resolution
            pricing = cls._PRICING_CONFIG.get(ToolType.LTX_2_3_LIPSYNC)
            if not pricing:
                logger.warning("No pricing config for LTX 2.3 Lipsync")
                return 0.0
            min_billable = pricing.get("min_billable_seconds", 5)
            billable_sec = max(float(duration), min_billable)
            res_val = getattr(resolution, "value", None) or (resolution if isinstance(resolution, str) else "720p")
            if res_val == "480p":
                rate = pricing.get("per_second_480p", 0.02)
            elif res_val == "1080p":
                rate = pricing.get("per_second_1080p", 0.04)
            else:
                rate = pricing.get("per_second_720p", 0.03)
            cost = round(billable_sec * rate, 6)
            logger.debug(f"LTX 2.3 Lipsync cost: {duration}s -> billable={billable_sec}s, {res_val} = ${cost}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating LTX 2.3 lipsync cost: {e}")
            return 0.0

    @classmethod
    def _calculate_kling_v2_ai_avatar_pro_cost(
        cls,
        duration: float,
        **kwargs
    ) -> float:
        """Kling V2 AI Avatar Pro：billable_sec = max(duration, 5)，cost = billable_sec × per_second（与分辨率无关）。"""
        try:
            pricing = cls._PRICING_CONFIG.get(ToolType.KLING_V2_AI_AVATAR_PRO)
            if not pricing:
                logger.warning("No pricing config for Kling V2 AI Avatar Pro")
                return 0.0
            min_billable = pricing.get("min_billable_seconds", 5)
            billable_sec = max(float(duration), min_billable)
            rate = pricing.get("per_second", 0.112)
            cost = round(billable_sec * rate, 6)
            logger.debug(f"Kling V2 AI Avatar Pro cost: {duration}s -> billable={billable_sec}s = ${cost}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating Kling V2 AI Avatar Pro cost: {e}")
            return 0.0

    @classmethod
    def _calculate_wan22_speech_to_video_cost(
        cls,
        duration: float,
        resolution=None,
        **kwargs
    ) -> float:
        """WAN 2.2 S2V：billable_sec = max(duration, min 5s)；× $0.03/s(480p) 或 $0.06/s(720p)。与 WaveSpeed 实测一致：6–10s 为 0.36–0.60 线性阶梯。"""
        try:
            from ..models.tool_enums import Resolution
            pricing = cls._PRICING_CONFIG.get(ToolType.WAN_2_2_SPEECH_TO_VIDEO)
            if not pricing:
                logger.warning("No pricing config for WAN 2.2 Speech-to-Video")
                return 0.0
            min_billable = float(pricing.get("min_billable_seconds", 5) or 5)
            d = float(duration)
            if d <= 0:
                billable_sec = min_billable
            else:
                billable_sec = max(d, min_billable)
            res_val = getattr(resolution, "value", None) or (resolution if isinstance(resolution, str) else "720p")
            if res_val == "480p":
                rate = pricing.get("per_second_480p", 0.03)
            else:
                rate = pricing.get("per_second_720p", 0.06)
            cost = round(billable_sec * rate, 6)
            logger.debug(f"WAN 2.2 S2V cost: {duration}s -> billable={billable_sec}s, {res_val} = ${cost}")
            return cost
        except Exception as e:
            logger.error(f"Error calculating WAN 2.2 Speech-to-Video cost: {e}")
            return 0.0
    
    @classmethod
    def log_cost(cls, cost: float):
        """记录成本到 LangSmith"""
        try:
            run = get_current_run_tree()
            if run:
                run.set(usage_metadata={"total_cost": cost})
        except Exception as e:
            logger.warning(f"Failed to set cost metadata: {e}")

    @staticmethod
    def get_credit_callback(config=None):
        """从 RunnableConfig 中提取 CreditCheckCallbackHandler（如果存在）。
        
        供 tool 内部在 calculate_cost 之后调用 callback.add_tool_cost()。
        config 优先从参数取；若为 None，则尝试从 LangChain 当前执行上下文的
        var_child_runnable_config 中取（适用于没有 runtime 参数的 tool）。
        """
        try:
            from ..callbacks.credit_check_callback import CreditCheckCallbackHandler

            def _extract(cfg):
                if cfg is None:
                    return None
                callbacks = cfg.get('callbacks') if isinstance(cfg, dict) else getattr(cfg, 'callbacks', None)
                if callbacks is None:
                    return None
                if isinstance(callbacks, list):
                    for c in callbacks:
                        if isinstance(c, CreditCheckCallbackHandler):
                            return c
                elif hasattr(callbacks, 'handlers'):
                    for c in callbacks.handlers:
                        if isinstance(c, CreditCheckCallbackHandler):
                            return c
                return None

            result = _extract(config)
            if result is not None:
                return result

            # fallback：从 LangChain 当前执行上下文取
            from langchain_core.runnables.config import var_child_runnable_config
            ctx_config = var_child_runnable_config.get()
            return _extract(ctx_config)
        except Exception:
            pass
        return None
    
    # ==================== 工具获取方法 ====================
    
    @classmethod
    def _map_user_option_to_tool_type(
        cls,
        user_option_tool: Union[ImageGenerationTool, VideoGenerationTool]
    ) -> ToolType:
        """将用户选项映射到工具类型（基础映射）"""
        return _USER_OPTION_TO_TOOL_TYPE.get(user_option_tool, ToolType.GEMINI_2_5_FLASH_IMAGE)
    
    @classmethod
    def get_image_generation_tools(
        cls,
        user_option: Optional[UserOption] = None,
        mode: Optional[Union[ToolMode, str]] = None
    ) -> ToolsInfo:
        """获取图像生成工具
        
        Args:
            user_option: 用户选项配置
            mode: 工具模式，ToolMode enum 或字符串（"t2i"/"i2i"），None为返回所有工具
            
        Returns:
            ToolsInfo: 工具信息集合
        """
        if not user_option:
            user_option = UserOption.default()
        
        # 根据 user_option 选定尝试链并创建 Wrapper 工具（chain 在此时绑定，运行时不再依赖 context）
        from ..tools.image.image_tool_wrapper import create_image_wrapper_tools

        tool_mode = None if mode == ToolMode.ALL else mode
        tool_infos = create_image_wrapper_tools(mode=tool_mode, user_option=user_option)
        return ToolsInfo(
            tools=tool_infos,
            category=ToolCategory.IMAGE_GENERATION,
            user_option_tool=user_option.image_generation_tool.value,
        )
    
    @classmethod
    def get_video_generation_tools(
        cls,
        user_option: Optional[UserOption] = None,
        mode: Optional[Union[ToolMode, str]] = None,
        resolution: Optional[Resolution] = None,
        has_end_image: Optional[bool] = None,
        generation_mode: Optional[str] = None,
    ) -> ToolsInfo:
        """获取视频生成工具
        
        Args:
            user_option: 用户选项配置
            mode: 工具模式，ToolMode enum 或字符串（"t2v"/"i2v"），None为返回所有工具
            resolution: 分辨率（可选，用于确定 Seedance Lite 的具体版本）
            has_end_image: 是否有尾帧（可选，用于确定使用 FastPro 还是 Lite）
            generation_mode: 生成模式（GenerationMode.LIPSYNC.value 时走 lipsync 工具路径）
            
        Returns:
            ToolsInfo: 工具信息集合
        """
        from ..models.tool_enums import GenerationMode
        
        if not user_option:
            user_option = UserOption.default()
        
        if generation_mode == GenerationMode.LIPSYNC.value:
            return cls._get_lipsync_video_tools(user_option, mode, resolution)
        else:
            return cls._get_normal_video_tools(user_option, mode, resolution, has_end_image)
    
    @classmethod
    def _get_normal_video_tools(
        cls,
        user_option: UserOption,
        mode: Optional[Union[ToolMode, str]] = None,
        resolution: Optional[Resolution] = None,
        has_end_image: Optional[bool] = None,
    ) -> ToolsInfo:
        """普通模式：根据用户选择的工具获取对应 ToolsInfo

        所有视频工具统一走 video_tool_wrapper（带 fallback + metrics）。
        Chain: 主模型(用户选择) → seedance v1.0 → wan 2.6（去重后最多 3 个）。
        """
        from ..tools.video.video_tool_wrapper import create_video_wrapper_tools

        tool = user_option.video_generation_tool
        tool_mode = None if mode == ToolMode.ALL else mode

        tool_infos = create_video_wrapper_tools(
            mode=tool_mode,
            user_option=user_option,
            resolution=resolution,
            has_end_image=has_end_image,
        )
        return ToolsInfo(
            tools=tool_infos,
            category=ToolCategory.VIDEO_GENERATION,
            user_option_tool=tool.value,
        )
    
    @classmethod
    def _get_lipsync_video_tools(
        cls,
        user_option: UserOption,
        mode: Optional[Union[ToolMode, str]] = None,
        resolution: Optional[Resolution] = None,
    ) -> ToolsInfo:
        """Lipsync 模式：统一走 lipsync_tool_wrapper（链由用户 lipsync_video_tool 决定，含 LTX / Kling Avatar / WAN 2.2 S2V / Wan 2.5 / 2.6 等，fallback 见 lipsync_tool_wrapper）。
        
        用户选非 LIPSYNC_CAPABLE 工具时自动用默认口型工具。不支持尾帧（has_end_image 由调用方保证为 False）。
        """
        from ..tools.video.lipsync_tool_wrapper import create_lipsync_wrapper_tools

        tool_mode = None if mode == ToolMode.ALL else mode
        tool_infos, tool = create_lipsync_wrapper_tools(
            mode=tool_mode,
            user_option=user_option,
            resolution=resolution,
        )
        return ToolsInfo(
            tools=tool_infos,
            category=ToolCategory.VIDEO_GENERATION,
            user_option_tool=tool.value,
        )
    
    @classmethod
    def get_music_generation_tools(cls) -> ToolsInfo:
        """获取音乐生成工具
        
        Returns:
            ToolsInfo: 工具信息集合
        """
        from ..tools.music.suno import get_suno_tools
        tool_infos = get_suno_tools()
        
        return ToolsInfo(
            tools=tool_infos,
            category=ToolCategory.MUSIC_GENERATION
        )
    
    @classmethod
    def get_audio_effect_tools(cls) -> ToolsInfo:
        """获取音效生成工具
        
        Returns:
            ToolsInfo: 工具信息集合
        """
        from ..tools.audioeffect.mmaudio import get_mmaudio_tools
        tool_infos = get_mmaudio_tools()
        
        return ToolsInfo(
            tools=tool_infos,
            category=ToolCategory.AUDIO_EFFECT
        )
    
    @classmethod
    def get_speech_generation_tools(cls) -> ToolsInfo:
        """获取语音合成工具
        
        Returns:
            ToolsInfo: 工具信息集合
        """
        from ..tools.narration.speech_tool_wrapper import create_speech_wrapper_tools
        tool_infos = create_speech_wrapper_tools()
        
        return ToolsInfo(
            tools=tool_infos,
            category=ToolCategory.SPEECH_SYNTHESIS
        )
    
    @classmethod
    def get_lipsync_tools(cls) -> ToolsInfo:
        """获取唇形同步工具
        
        Returns:
            ToolsInfo: 工具信息集合
        """
        from ..tools.lipsync.latentsync import get_lipsync_tools
        tool_infos = get_lipsync_tools()
        
        return ToolsInfo(
            tools=tool_infos,
            category=ToolCategory.LIPSYNC
        )
    
    @classmethod
    def get_image_prompt_guide(
        cls,
        user_option: Optional[UserOption] = None,
        mode: Optional[Union[ToolMode, str]] = None
    ) -> str:
        """根据工具和模式获取图像生成的 prompt 指南
        
        Args:
            user_option: 用户选项配置
            mode: 模式，ToolMode enum 或字符串（"t2i"/"i2i"/"all"）
                - T2I: 返回 "generate" 指南
                - I2I: 返回 "edit" 指南
                - ALL: 返回 "best_practices" 指南
                - None/其他: 默认返回 "generate" 指南
            
        Returns:
            相应的 prompt 指南文本
        """
        if not user_option:
            user_option = UserOption.default()
        
        # 标准化 mode 参数，获取 mode 的字符串值
        if isinstance(mode, str):
            mode_key = mode.lower()
        elif mode is not None:
            mode_key = mode.value.lower() if hasattr(mode, 'value') else str(mode).lower()
        else:
            # 默认使用 T2I
            mode_key = ToolMode.T2I.value
        
        image_tool = user_option.image_generation_tool
        if image_tool == ImageGenerationTool.AUTO:
            image_tool = DEFAULT_IMAGE_TOOL
        
        # 获取当前 mode 对应的指南数据
        guide_data = IMAGE_PROMPT_GUIDES.get(mode_key)
        
        # 如果找不到对应的 mode，使用默认的 T2I
        if guide_data is None:
            guide_data = IMAGE_PROMPT_GUIDES.get(ToolMode.T2I.value, {})
        
        # 检查工具是否支持当前 mode 的指南
        supported_tools = guide_data.get("supported_tools", [])
        
        # 如果工具不支持当前 mode 的指南，返回空字符串
        if image_tool not in supported_tools:
            return ""
        
        # 获取当前 mode 的指南文本
        guide_text = guide_data.get("guide", "")
        
        # 如果 mode 不是 ALL，必须加上 ALL mode 的指南（如果工具支持）
        if mode_key != ToolMode.ALL.value:
            all_mode_data = IMAGE_PROMPT_GUIDES.get(ToolMode.ALL.value, {})
            all_mode_supported = all_mode_data.get("supported_tools", [])
            
            # 如果工具支持 ALL mode，则加上 ALL mode 的指南
            if image_tool in all_mode_supported:
                all_mode_guide = all_mode_data.get("guide", "")
                if all_mode_guide:
                    guide_text += "\n\n" + all_mode_guide
        
        return guide_text