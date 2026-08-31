"""
提示词构建工具函数
"""
from __future__ import annotations

import asyncio
import json
import random
from typing import Optional, List, Dict, Any, Union, TYPE_CHECKING, Type, TypeVar

if TYPE_CHECKING:
    from ....services.agent.base_agent import MessageType
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langchain_core.runnables import Runnable
from ....models.user_options import UserOption, VideoGenerationTool, ImageGenerationTool, DEFAULT_IMAGE_TOOL
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class RuleApplicability(str, Enum):
    """规则适用性枚举"""
    VIDEO = "video"      # 仅适用于视频生成
    IMAGE = "image"      # 仅适用于图像生成  
    ALL = "all"          # 适用于视频和图像生成


# 风格化视频示例字典
STYLE_EXAMPLES_DICT = {
    "general": {
        "supported_tools": [VideoGenerationTool.POLLO_SEEDANCE, VideoGenerationTool.SEEDANCE_V1_5, VideoGenerationTool.SEEDANCE_2_I2V, VideoGenerationTool.SEEDANCE_2_I2V_TURBO, VideoGenerationTool.SEEDANCE_2_FAST_I2V, VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO, VideoGenerationTool.WAN_2_5, VideoGenerationTool.WAN_2_6, VideoGenerationTool.KLING_V3_STD, VideoGenerationTool.HAPPYHORSE_1_0_I2V, VideoGenerationTool.HAPPYHORSE_1_1_I2V, VideoGenerationTool.LTX_2_3, VideoGenerationTool.OPENAI_SORA, VideoGenerationTool.OPENAI_SORA_PRO],
        "system_role": "专业的AI视频生成专家，精通AI视频生成技术",
        "example_timeline": "",
        "style_requirements": ""
    },
    "kpop": {
        "supported_tools": [VideoGenerationTool.OPENAI_SORA, VideoGenerationTool.OPENAI_SORA_PRO],
        "system_role": "专业的K-pop MV视频生成专家，精通韩流音乐视频的视觉美学和编舞艺术，擅长AI视频生成技术",
        "example_timeline": """
**🎵 K-pop MV专业示例时间轴（强调人物动作）**：

**单人动作示例 (5s)**：
Action (5s total): 0-1s: Female character tilts head gracefully, pink hair cascades over shoulder, bright smile spreads across face, eyes sparkle with joy, white collar shifts with movement, 1-2s: raises right hand to touch gold necklace, hair continues flowing motion, head turns slightly toward camera, expression becomes more confident, 2-3s: leans forward slightly, hair shimmers intensify, both hands now gesture expressively, smile widens, eyes maintain direct contact, 3-4s: straightens posture with elegant movement, hair settles into new position, hands move to sides in graceful motion, maintains radiant expression, 4-5s: takes small step forward, hair bounces gently, final pose with hands clasped, ready for next sequence

**多人编舞示例 (6s)**：
Action (6s total): 0-1s: Five members start in tight formation, simultaneously step forward with left foot, arms swing up in unison, pastel outfits catch stage lights, synchronized head movements, 1-2s: members pivot 45 degrees right, arms cross over chests, hair flows with turning motion, facial expressions shift to intense focus, 2-3s: explosive arm extension outward, members jump slightly, legs spread to shoulder width, outfits billow with movement, confident smiles emerge, 3-4s: members crouch down in sequence (left to right), arms sweep down, hair falls forward, then spring back up with energy, 4-5s: synchronized spin turn, arms extended, outfits create flowing motion, hair whips around, members maintain formation while rotating, 5-6s: final powerful pose with arms raised, legs in wide stance, heads tilted back, outfits settle, ready for next choreography sequence

**动作重点强调**：
- **人物主导**：每秒都是人物在做动作，不是镜头在移动
- **身体部位**：头部、手臂、腿部、躯干都要有具体动作
- **服装互动**：衣服、头发随动作产生的物理效果
- **表情变化**：眼神、微笑、专注等表情的动态变化
- **空间移动**：人物在空间中的位置变化和移动
""",
        "style_requirements": """
**🎵 K-pop MV风格要求**：
- **🚨 每秒动作要求**：每1秒都要有明确的动作变化，包括表情、姿势、镜头运动
- **色彩方案**：霓虹粉色、青色、紫色、白色的高对比度搭配
- **视觉元素**：大型文字覆盖、几何图形、反光地板、剪影效果
- **镜头语言**：快速切换、特写与全景交替、动态光影
- **动作特点**：同步编舞、自信表情、时尚造型、动态姿势
- **光影效果**：霓虹灯轮廓、反射光源、高亮背景、轮廓光
- **节奏感**：动作要与音乐节拍同步，每秒都要有视觉冲击
"""
    }
}

# 内容审核规则字典 - 使用规则名称作为key，避免重复
CONTENT_MODERATION_RULES = {
    "sora_human_restrictions": {
        "supported_tools": [VideoGenerationTool.OPENAI_SORA, VideoGenerationTool.OPENAI_SORA_PRO],
        "applicability": RuleApplicability.VIDEO,
        "content": """
### Sora 人物词汇限制 (Sora Human Vocabulary Restrictions)
*   **定义 (Definition)**: 严禁在 Prompt 中使用直接指代人类身份的词汇，如 "girl", "boy", "man", "woman", "human", "child" 等。
*   **理由 (Reason)**: Sora 的安全审核机制对特定身份词汇极其敏感，使用这些词汇会显著增加触发内容拦截（Safety Filter）的概率，导致生成失败。
*   **执行协议 (Action Protocol)**:
    - **中性词替换**: 必须将身份词汇替换为中性描述词。例如：
        * "girl/woman" → "figure", "character", "silhouette"
        * "boy/man" → "figure", "character", "silhouette"
        * "human/people" → "entities", "figures"
    - **去身份化描述**: 专注于描述主体的动作、姿态、服装和环境特征，而非其生理身份。
    - **示例修正**: 
        * ❌ "A girl dancing" → ✅ "A graceful figure dancing"
        * ❌ "Two people walking" → ✅ "Two characters walking"
"""
    },
    "nano_banana_halo_warning": {
        "supported_tools": [ImageGenerationTool.NANO_BANANA, ImageGenerationTool.NANO_BANANA_2, ImageGenerationTool.NANO_BANANA_PRO],
        "applicability": RuleApplicability.IMAGE,
        "content": """
<rule name="Nano Banana Halo Physics">
  <definition>
    在描述光影效果时，应极其谨慎地使用 "halo" 或其近义词。
  </definition>
  <reason>
    模型对 "halo" 等词汇的理解倾向于具象化，容易将预期的"环境氛围光"误生成为宗教性质或实体性质的"头部光环"，造成视觉风格偏离。
  </reason>
  <action_protocol>
    - 词汇规避: 除非明确需要头部光环效果，否则严禁使用 "halo", "angelic glow", "sacred light", "celestial glow"
    - 环境光替代: 推荐使用 "Volumetric lighting" (体积光), "Cinematic soft lighting" (电影感柔光), "Ethereal ambient glow" (超凡环境光)
  </action_protocol>
</rule>
"""
    },
    "i2v_locked_angle_principle": {
        "supported_tools": "all",  # 通用规则，适用于所有视频生成工具
        "applicability": RuleApplicability.VIDEO,
        "content": """
### 角度锁定原则 (The Locked-Angle Principle)
*   **定义 (Definition)**: 主体在视频全过程必须基本保持相对于相机的原始朝向角度。严格禁止任何 Y 轴旋转（如转身、回头、侧脸转正、原地旋转）。
*   **理由 (Reason)**: I2V 模型缺乏真实的 3D 空间理解。旋转角度 >30 度会迫使 AI 幻觉出不可见的特征（如另一只耳朵、后脑勺），导致严重的身份坍缩和人脸扭曲。
*   **执行协议 (Action Protocol)**:
    - **侧重平面与线性运动**: 指导 AI 沿 X 轴（左右）或 Z 轴（前后）生成运动，但锁定 Y 轴。
    - **鼓励肢体表达**: 明确允许手臂、手部、腿部的自由摆动和身体晃动，前提是躯干和头部的朝向保持稳定。
    - **抽象指令**: 描述当前姿态内的动作（如舞蹈、行走、手势），而非改变姿态的角度。
"""
    },
    "i2v_resolution_framing_ratio": {
        "supported_tools": "all",  # 通用规则，适用于所有视频生成工具
        "applicability": RuleApplicability.VIDEO,
        "content": """
### 分辨率与构图比例 (The Resolution-Framing Ratio)
*   **定义 (Definition)**: 避免在“全景/全身镜头 (Full Body Shot)”中同时要求“高精度面部细节 (High Fidelity Facial Details)”。
*   **理由 (Reason)**: 在标准视频画布（如 1080p）上，全身镜头中的面部占比不到总像素的 5%。在如此小的像素网格中物理上无法渲染微小的面部细节或有神的眼睛。
*   **执行协议 (Action Protocol)**:
    - **构图覆盖**: 如果用户要求面部清晰度，自动将构图描述修正为“牛仔镜头 (Cowboy Shot)”（膝盖以上）或“腰部以上镜头 (Waist-up Shot)”。
    - **像素优先级**: 牺牲脚部/鞋子的可见性，以为面部争取更高的像素密度。
"""
    },
    "i2v_temporal_stability_enforcement": {
        "supported_tools": "all",  # 通用规则，适用于所有视频生成工具
        "applicability": RuleApplicability.VIDEO,
        "content": """
### 时域稳定性增强 (Temporal Stability Enforcement)
*   **定义 (Definition)**: 禁止出现会导致帧间像素剧烈变化的画面元素，特别是“频闪灯 (Strobe Lights)”、“闪烁特效 (Flashing Effects)”或“剧烈的相机抖动 (Fast Camera Shakes)”。
*   **理由 (Reason)**: 快速的高对比度变化会干扰扩散模型的时域注意力层，导致面部闪烁、画面抖动伪影以及连贯性丧失。
*   **执行协议 (Action Protocol)**:
    - **灯光稳定化**: 将“闪烁/频闪灯”修正为“体积光 (Volumetric lighting)”、“电影感柔光 (Cinematic soft lighting)”或“缓慢移动的动态光”。
    - **运动平滑化**: 始终执行“慢动作 (Slow Motion)”或“高帧率摄影 (High-Speed Camera)”关键词。这会强制模型生成更多中间帧，从而平滑任何潜在的抖动。
"""
    },
    "i2v_source_fidelity": {
        "supported_tools": "all",  # 通用规则，适用于所有视频生成工具
        "applicability": RuleApplicability.VIDEO,
        "content": """
### 源图保真原则 (Source Fidelity)
*   **定义 (Definition)**: 严禁描述参考图中完全不存在的元素**或交互行为**。
*   **理由 (Reason)**: AI 无法稳定地生成第一帧中不可见的**复杂解剖学交互**（如抚摸、握手、拥抱）。这会导致“变形伪影”，即背景纹理突然变成畸形的肢体。
*   **执行协议 (Action Protocol)**:
    - **画布边界**: 仅描述源图中实际存在的解剖结构和环境。
    - **交互禁令**: **严格禁止物理交互（抚摸、握手、拥抱），除非交互双方在源图中均清晰可见。**
    - **替代方案**: 专注于主体的“反应”而非“交互”（例如：将“手抚摸狗”改为“狗因为享受而闭上眼睛”）。
"""
    },
    "i2v_spatial_trajectory_alignment": {
        "supported_tools": "all",
        "applicability": RuleApplicability.VIDEO,
        "content": """
### 空间轨迹对齐 (Spatial Trajectory Alignment)
*   **定义 (Definition)**: Prompt 中的运动描述必须与主体在参考图（第一帧）中的实际位置严格对齐。
*   **理由 (Reason)**: 如果主体位于图像中心，却要求其“从左侧进入”，会迫使模型生成一个重复的主体（重影伪影），最终与原主体合并，造成严重的逻辑不一致。
*   **执行协议 (Action Protocol)**:
    - **位置检查**: 如果主体已清晰可见或位于中心，禁止使用“从[侧面]进入”、“走入画面”或“出现”等词汇。
    - **轨迹修正**: 替代“从左侧进入”，使用“向前移动”、“向相机走来”或“身体微微转动”。
    - **在场假设**: 假设主体已经在场景中。描述他们在原地的动作 or 前进，绝非“抵达”。
"""
    },
    "aesthetic_and_anatomical_integrity": {
        "supported_tools": "all",
        "applicability": RuleApplicability.IMAGE,
        "content": """
<rule name="Aesthetic and Anatomical Integrity">
  <definition>
    防止解剖结构不连续和过度的生物学细节描述，避免引起视觉不适或恐怖谷效应。
  </definition>
  <reason>
    AI 模型可能会生成漂浮的肢体或过于写实的生物纹理，违反审美标准并造成感官不适。
  </reason>
  <action_protocol>
    - 解剖连贯性: 即使是特写镜头也要包含连接点（脖子、肩膀），防止出现"漂浮的头"。确保主体牢牢扎根于环境中
    - 洁净美学: 避免显微镜级的生物细节（如舌头颗粒、唾液、粘性残留物）。保持干净、优雅的电影级表面质量
    - 比例平衡: 优先考虑符合审美的比例，而非原始的生物学准确性。保持柔和、吸引人的特征
  </action_protocol>
</rule>
"""
    },
    "spatial_scale_and_perspective": {
        "supported_tools": "all",
        "applicability": RuleApplicability.IMAGE,
        "content": """
<rule name="Spatial Scale and Perspective Integrity">
  <definition>
    防止尺寸失真，即主体和背景元素的比例在视觉上异常接近，违反现实世界的比例。
  </definition>
  <reason>
    AI 倾向于平衡所有提到元素的尺寸，使它们同等可见，导致空间层级崩溃和错误的比例关系。
  </reason>
  <action_protocol>
    - 主体比例: 指定主体在画面中的视觉比例，以锚定其预期的突出地位
    - 参考缩放: 引入已知尺寸的参考物体以建立比例逻辑
    - 地平线逻辑: 定义地平线高度（高、低、平视）以控制透视深度
  </action_protocol>
</rule>
"""
    }
}


def attach_images_to_messages(
    messages: List[BaseMessage],
    image_urls: List[str],
    target_message_index: Optional[int] = None,
    prepend_text: bool = True,
    add_image_index_hint: bool = True,
    image_descriptions: Optional[List[str]] = None,
) -> List[BaseMessage]:
    """通用工具函数：将图片附加到消息列表中
    
    将图片 URL 列表附加到指定的消息中，转换为 LangChain 支持的多模态消息格式。
    这个函数会修改消息列表并返回，支持链式调用。
    
    Args:
        messages: LangChain 消息列表（通常来自 prompt_template.format_messages()）
        image_urls: 要附加的图片 URL 列表
        target_message_index: 要附加到的消息索引（None 表示自动查找最后一条 HumanMessage）
        prepend_text: 是否将文本放在图片前面（True）还是后面（False），默认 True
        add_image_index_hint: 是否在消息文本中添加图片索引提示（默认 True）
        image_descriptions: 可选，与 image_urls 等长的每张图说明（如 ["参考图1", "参考图2", "生成图"]），
            会在每张图前插入「【图N: 说明】」便于 LLM 区分；长度不一致时忽略
        
    Returns:
        List[BaseMessage]: 修改后的消息列表（原地修改并返回）
        
    Example:
        >>> messages = prompt_template.format_messages(...)
        >>> image_urls = ["https://example.com/img1.jpg", "https://example.com/img2.jpg"]
        >>> messages = attach_images_to_messages(messages, image_urls)
        >>> # 现在 messages 中的最后一条 HumanMessage 包含了文本和图片
        >>> # 并且文本末尾会自动添加图片索引提示
        
    Note:
        - 如果目标消息已经是多模态格式（content 是 list），会在现有内容基础上添加图片
        - 如果图片列表为空，直接返回原消息列表
        - 支持的图片格式取决于使用的 LLM（如 Gemini、GPT-4V 等）
        - 当 add_image_index_hint=True 时，会在文本末尾添加图片编号说明，便于 LLM 引用
        - 当 image_descriptions 长度与 image_urls 一致时，每张图前会加「【图N: 说明】」文本
    """
    # 如果没有图片，直接返回
    if not image_urls:
        logger.debug("没有图片需要附加，返回原消息列表")
        return messages
    
    # 过滤掉空的 URL，并去掉首尾引号（避免从 JSON/字符串解析带出 '..."...' 导致 invalid_image_url）
    def _normalize_image_url(u: str) -> str:
        return (u or "").strip().strip("'\"")
    valid_image_urls = [_normalize_image_url(url) for url in image_urls if url and (url or "").strip()]
    valid_image_urls = [u for u in valid_image_urls if u]
    if not valid_image_urls:
        logger.debug("没有有效的图片 URL，返回原消息列表")
        return messages
    
    # 确定目标消息索引
    if target_message_index is None:
        # 自动查找最后一条 HumanMessage
        target_message_index = None
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], HumanMessage):
                target_message_index = i
                break
        
        if target_message_index is None:
            logger.warning("未找到 HumanMessage，无法附加图片")
            return messages
    
    # 验证索引有效性
    if target_message_index < 0 or target_message_index >= len(messages):
        logger.warning(f"消息索引 {target_message_index} 越界，无法附加图片")
        return messages
    
    target_message = messages[target_message_index]
    
    # 构建新的多模态内容
    new_content = []
    
    # 处理原有内容
    original_text = ""
    if isinstance(target_message.content, str):
        # 原内容是字符串
        original_text = target_message.content
    elif isinstance(target_message.content, list):
        # 原内容已经是多模态格式，提取文本部分
        for item in target_message.content:
            if isinstance(item, dict) and item.get("type") == "text":
                original_text = item.get("text", "")
                break
    
    # 如果需要添加图片索引提示，在文本末尾添加（有 image_descriptions 时带标签，与参考图总表一致）
    if add_image_index_hint and valid_image_urls:
        image_count = len(valid_image_urls)
        use_desc = image_descriptions and len(image_descriptions) == image_count
        index_hint = f"\n\n📋 参考图片编号（共 {image_count} 张）：\n"
        for i in range(image_count):
            if use_desc and image_descriptions[i]:
                index_hint += f"- 图片 {i + 1}: {image_descriptions[i]}\n"
            else:
                index_hint += f"- 图片 {i + 1}\n"
        index_hint += "\n注意：上述图片已按顺序附加在本消息中，编号从 1 开始。如需引用特定图片，请使用图片编号（整数）。"
        original_text = original_text + index_hint
    
    # 添加文本块
    if original_text:
        text_block = {"type": "text", "text": original_text}
        if prepend_text:
            new_content.append(text_block)
    
    # 是否使用每张图说明（长度一致时才使用）
    use_descriptions = (
        image_descriptions is not None
        and len(image_descriptions) == len(valid_image_urls)
    )
    # 添加图片（可选：每张图前插入说明文本）
    for i, url in enumerate(valid_image_urls):
        if use_descriptions and image_descriptions[i]:
            new_content.append({
                "type": "text",
                "text": f"【图{i + 1}: {image_descriptions[i]}】",
            })
        new_content.append({
            "type": "image_url",
            "image_url": {"url": url}
        })
    
    # 如果文本在后面，现在添加
    if not prepend_text and original_text:
        new_content.append({"type": "text", "text": original_text})
    
    # 创建新的 HumanMessage 替换原消息
    messages[target_message_index] = HumanMessage(content=new_content)
    
    logger.info(f"✅ 已将 {len(valid_image_urls)} 张图片附加到消息索引 {target_message_index}")
    
    return messages


def _get_language_instruction(detected_language: Optional[str] = None) -> str:
    """返回语言强制要求的 XML 块（用于追加到 System 或 Human 消息内容末尾）。"""
    from ....utils.i18n import get_current_language
    current_lang = (detected_language or get_current_language() or "").strip() or "en"
    return f"""
<language_requirements>
  <detected_language>{current_lang}</detected_language>
  <note>User/audio detected language is '{current_lang}' (ISO 639-1). You MUST use this language only.</note>

  <mandatory_rules>
    <rule priority="critical">Your entire response (all prompts, descriptions, text) MUST be 100% in '{current_lang}' language. No exceptions.</rule>
    <rule priority="critical">Do NOT use English unless detected_language is 'en'. If detected_language is zh, ja, ko, etc., output ONLY in that language.</rule>
    <rule priority="critical">If you produce any text in the wrong language, regenerate it in '{current_lang}' immediately.</rule>
  </mandatory_rules>

  <self_correction>If you notice you used the wrong language, stop and regenerate in '{current_lang}' only.</self_correction>
</language_requirements>"""


def add_language_suffix_to_system_message(system_content: str, detected_language: Optional[str] = None) -> str:
    """为 system message 添加语言指示后缀（XML 格式）。首先使用 detected_language，强烈要求输出仅使用该语言。
    优先使用传入的 detected_language（用户/音频检测语言），没有再用 ContextVar。
    """
    suffix = _get_language_instruction(detected_language)
    if not system_content.endswith("\n"):
        system_content += "\n"
    return system_content + suffix


def _apply_language_suffix_to_content(
    content: Union[str, List[Dict[str, Any]]],
    detected_language: Optional[str] = None,
) -> Union[str, List[Dict[str, Any]]]:
    """为 content 追加语言要求。若 content 为多模态 list（text + image_url 等），仅修改最后一个 text 块，保持 list 结构不变。"""
    suffix = _get_language_instruction(detected_language)
    if isinstance(content, str):
        return add_language_suffix_to_system_message(content, detected_language)
    if not isinstance(content, list):
        return content
    # 多模态 list：找到最后一个 type="text" 的块，在其 text 末尾追加 suffix
    new_content = []
    last_text_idx = -1
    for idx, part in enumerate(content):
        if isinstance(part, dict) and part.get("type") == "text":
            last_text_idx = len(new_content)
        new_content.append(part)
    if last_text_idx >= 0:
        block = new_content[last_text_idx]
        text = block.get("text", "")
        if text and not text.endswith("\n"):
            text += "\n"
        new_content[last_text_idx] = {**block, "text": text + suffix}
    else:
        prefix = "\n" if new_content else ""
        new_content.append({"type": "text", "text": prefix + suffix})
    return new_content


def apply_language_suffix_to_system_message_in_messages(
    messages: List[BaseMessage],
    detected_language: Optional[str] = None,
) -> None:
    """对每条 SystemMessage 和 HumanMessage 都追加语言要求（强化输出语言一致性）。原地修改 messages。
    支持多模态 content（list of text/image_url）：仅修改最后一个 text 块，不将 content 转为字符串。
    唯一使用 add_language_suffix_to_system_message 的调用方。
    """
    for i, msg in enumerate(messages):
        if isinstance(msg, SystemMessage):
            content = getattr(msg, "content", None)
            new_content = _apply_language_suffix_to_content(content, detected_language)
            messages[i] = SystemMessage(content=new_content)
        elif isinstance(msg, HumanMessage):
            content = getattr(msg, "content", None)
            new_content = _apply_language_suffix_to_content(content, detected_language)
            messages[i] = HumanMessage(content=new_content)


def get_content_from_structured_output_parse_error(exc: Exception) -> Optional[str]:
    """
    从 Agent ProviderStrategy 解析失败异常中尝试取出「最后一条 AI 回复」的文本 content。
    不同 LangChain 版本可能把 content 放在 output / response / ai_message 或 __cause__ 上。

    Returns:
        若能从 exc 或其 __cause__ 上找到 .content（str 或 list 中最后一个 type=text 的 text），返回该字符串；否则 None。
    """
    def _content_from_obj(obj: Any) -> Optional[str]:
        if obj is None:
            return None
        raw = getattr(obj, "content", None)
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            for block in reversed(raw):
                if isinstance(block, dict) and block.get("type") == "text":
                    return (block.get("text") or "") or ""
        return None

    for e in (exc, getattr(exc, "__cause__", None)):
        if e is None:
            continue
        for attr in ("output", "response", "ai_message", "message"):
            c = _content_from_obj(getattr(e, attr, None))
            if c is not None:
                return c
    return None


def extract_first_json_from_string(content: str) -> Optional[Dict[str, Any]]:
    """
    从可能含「多段 JSON」或「JSON + 尾部」的字符串中，只取第一个完整 JSON 对象并解析。
    用于 Agent ProviderStrategy 解析失败（Extra data）时的兜底：content 里多段时只取第一段。

    Returns:
        解析得到的 dict，若找不到合法 JSON 则返回 None。
    """
    if not content or not isinstance(content, str):
        return None
    content = content.strip()
    start = content.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(content[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


_T = TypeVar("_T")


def try_parse_structured_output_from_exception(exc: Exception, model_class: Type[_T]) -> Optional[_T]:
    """
    create_agent ProviderStrategy 解析失败（Extra data / Failed to parse structured output）时，
    从异常里取 content、取第一段 JSON、用 model_class 解析。通用，任意 create_agent 调用方可用。

    Returns:
        解析成功返回 model_class 实例，否则 None。
    """
    err = str(exc)
    if "Failed to parse structured output" not in err and "Extra data" not in err:
        return None
    content = get_content_from_structured_output_parse_error(exc)
    if not content:
        return None
    d = extract_first_json_from_string(content)
    if not d:
        return None
    try:
        return model_class.model_validate(d)
    except Exception:
        return None


async def invoke_agent_with_parse_fallback(
    agent: Any,
    inputs: Dict[str, Any],
    *,
    context: Optional[Dict[str, Any]] = None,
    result_model: Type[_T],
) -> Dict[str, Any]:
    """
    与 invoke_structured_llm_resilient 对应：对 create_agent 的 ainvoke 做包装。
    正常返回与 agent.ainvoke 同形：{"structured_response": ..., "messages": [...]}。
    若抛出的异常为 ProviderStrategy 解析失败（Extra data 等），则从异常中取第一段 JSON 解析为 result_model，
    返回 {"structured_response": parsed, "messages": []}，后续逻辑无需区分是正常还是兜底。
    """
    try:
        if context is not None:
            return await agent.ainvoke(inputs, context=context)
        return await agent.ainvoke(inputs)
    except Exception as e:
        fallback = try_parse_structured_output_from_exception(e, result_model)
        if fallback is not None:
            return {"structured_response": fallback, "messages": []}
        raise


async def invoke_structured_llm_resilient(
    runnable: Runnable,
    messages: List[BaseMessage],
    *,
    retry_config: Optional[Dict[str, Any]] = None,
    max_attempts: int = 2,
    retry_on_none: bool = True,
    logger_instance: Optional[logging.Logger] = None,
    run_config: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    ①② 统一：直接 LLM 的「异常重试 + 返回 None 重试」。
    - 若传 retry_config，先对 runnable 包 with_retry（异常重试），再循环 ainvoke。
    - 循环内：ainvoke 直到得到非 None 或用尽 max_attempts（None 重试）。
    - run_config：可选，传给 ainvoke(config=run_config)，用于 LangSmith 统一 trace（run_id/trace_id 等）。
    用于 video_consistency 等「直接 with_structured_output 的 LLM」调用。

    Returns:
        首次非 None 的返回值；若用尽次数仍为 None 则返回 None。
    """
    log = logger_instance or logger
    effective = runnable
    if retry_config:
        effective = runnable.with_retry(**retry_config)
    result = None
    for attempt in range(1, max_attempts + 1):
        result = await effective.ainvoke(messages, config=run_config or {})
        if result is not None:
            return result
        if not retry_on_none:
            return None
        log.warning("LLM 返回 None，第 %s 次尝试（共最多 %s 次）", attempt, max_attempts)
    return None


# ==================== 视频时长：统一范围与各工具原生支持（仅作注释参考） ====================
# 对外统一使用 3–10 秒，避免 video/lipsync wrapper fallback 时主模型与 fallback 时长不一致。
# 各工具底层 API 原生支持（工具内部 clamp 用，此处仅注释，不参与返回）：
#   POLLO_SEEDANCE:    2–12s (seedance.py max(2, min(12, duration)))
#   SEEDANCE_V1_5:     2–12s (同上)
#   WAN_2_5:           3–10s (wan25.py)
#   WAN_2_6:           3–15s (wan26_flash.py)
#   LTX_2_3_LIPSYNC:   实测 1s 起，文档 5–20s (ltx23_lipsync.py)，规划仍用 3–10
#   KLING_V3_STD:      3–15s (kling.py)
#   HAPPYHORSE_1_0_I2V: 3–15s (happyhorse_1_0_i2v.py)
#   HAPPYHORSE_1_1_I2V: 3–15s (happyhorse_1_1_i2v.py)
#   OPENAI_SORA / PRO: [4, 8, 12] 离散 (sora.py _SORA_SUPPORTED_DURATIONS)
UNIFIED_DURATION_RANGE = list(range(3, 11))  # [3, 4, ..., 10]，规划与 tool 对外统一使用


def get_video_duration_values(user_option: Optional[UserOption] = None) -> List[int]:
    """根据用户选项获取支持的视频时长值列表（统一 3–10s，与 get_tool_supported_duration_range 一致）。"""
    return UNIFIED_DURATION_RANGE


def get_tool_supported_duration_range(
    user_option: Optional[UserOption] = None,
    generation_mode: Optional[str] = None,
) -> List[int]:
    """获取工具实际支持的 duration 范围（统一 3–10s）。供规划、video_generation_service 等使用。"""
    return UNIFIED_DURATION_RANGE


def get_split_threshold_audio_driven(
    user_option: Optional[UserOption] = None,
    content_category: Optional[str] = None,
) -> float:
    """Audio-driven 场景拆分的单段时长阈值（秒），与 scene_generation 中 _compute_scene_structure_for_chapter 一致。
    Lipsync 用 tool 支持的最大时长，否则用 min(video_durations)。"""
    from ....models.tool_enums import ContentCategory as CC, GenerationMode
    if content_category in (CC.LIP_SYNC_MV.value, CC.PRODUCT_LAUNCH.value):
        duration_range = get_tool_supported_duration_range(
            user_option, generation_mode=GenerationMode.LIPSYNC.value
        )
        return float(max(duration_range)) if duration_range else 15.0
    video_durations = get_video_duration_values(user_option)
    return float(min(video_durations)) if video_durations else 5.0


def get_split_threshold_video_driven(user_option: Optional[UserOption] = None) -> float:
    """Video-driven 下用于估算场景数的单场景时长（秒），取工具支持的最小时长。"""
    video_durations = get_video_duration_values(user_option)
    return float(min(video_durations)) if video_durations else 5.0


def get_image_moderation_rules(user_option: Optional[UserOption] = None) -> str:
    """获取 IMAGE 专用的内容审核规则（不包含批量连贯性策略）
    
    Args:
        user_option: 用户选项（用于过滤工具特定规则）
    
    Returns:
        str: 所有 IMAGE 规则的 XML 内容（已拼接，用 XML 标签包裹）
    """
    rules = []
    
    # 过滤 IMAGE 规则
    for rule_name, rule_config in CONTENT_MODERATION_RULES.items():
        # 跳过废弃的规则
        if rule_config.get("deprecated"):
            continue
            
        # 只要 IMAGE 规则
        if rule_config["applicability"] != RuleApplicability.IMAGE:
            continue
        
        # 检查工具兼容性
        supported_tools = rule_config["supported_tools"]
        if supported_tools != "all" and user_option:
            img_tool = user_option.image_generation_tool
            if img_tool == ImageGenerationTool.AUTO:
                img_tool = DEFAULT_IMAGE_TOOL
            if img_tool not in supported_tools:
                continue
        
        rules.append(rule_config["content"])
    
    # 用 XML 包裹
    if not rules:
        return "<content_moderation_rules_for_image>\n</content_moderation_rules_for_image>"
    
    return f"""<content_moderation_rules_for_image>
{chr(10).join(rules)}
</content_moderation_rules_for_image>"""


def get_video_moderation_rules(user_option: Optional[UserOption] = None) -> str:
    """获取 VIDEO 专用的内容审核规则
    
    Args:
        user_option: 用户选项（用于过滤工具特定规则）
    
    Returns:
        str: 所有 VIDEO 规则的 XML 内容（已拼接，用 XML 标签包裹）
    """
    rules = []
    
    # 过滤 VIDEO 规则
    for rule_name, rule_config in CONTENT_MODERATION_RULES.items():
        # 跳过废弃的规则
        if rule_config.get("deprecated"):
            continue
            
        # 只要 VIDEO 规则
        if rule_config["applicability"] != RuleApplicability.VIDEO:
            continue
        
        # 检查工具兼容性
        supported_tools = rule_config["supported_tools"]
        if supported_tools != "all" and user_option:
            if user_option.video_generation_tool not in supported_tools:
                continue
        
        rules.append(rule_config["content"])
    
    # 用 XML 包裹
    if not rules:
        return "<content_moderation_rules_for_video>\n</content_moderation_rules_for_video>"
    
    return f"""<content_moderation_rules_for_video>
{chr(10).join(rules)}
</content_moderation_rules_for_video>"""


I2V_FIRST_FRAME_CONSTRAINT_FOR_VIDEO_PROMPT = """
- **I2V 首帧保真（硬性规则）**：i2v_prompt 描述的动作与画面必须基于**起始关键帧中已经可见**的内容。禁止描述首帧中不存在的身体部位/元素在后续出现。例如：首帧头被遮住 → 禁止写「伸出头」；首帧只有半身 → 禁止写「露出全身」；首帧无此人 → 禁止写该人入画。违反会导致生成失败或严重伪影。
"""


def get_i2v_first_frame_constraint_for_video_prompt() -> str:
    """获取视频 prompt 生成时的 I2V 首帧保真约束。"""
    return I2V_FIRST_FRAME_CONSTRAINT_FOR_VIDEO_PROMPT


def get_video_tool_name(user_option: Optional[UserOption] = None) -> str:
    """获取视频生成工具的友好名称
    
    Args:
        user_option: 用户选项配置
        
    Returns:
        工具的友好名称
    """
    if not user_option:
        user_option = UserOption.default()
    
    video_tool = user_option.video_generation_tool
    
    tool_names = {
        VideoGenerationTool.AUTO: "Auto",
        VideoGenerationTool.POLLO_SEEDANCE: "WaveSpeed Seedance v1 Pro Fast",
        VideoGenerationTool.SEEDANCE_V1_5: "WaveSpeed Seedance v1.5 Pro (image-to-video-fast)",
        VideoGenerationTool.SEEDANCE_2_I2V: "WaveSpeed Seedance 2.0",
        VideoGenerationTool.SEEDANCE_2_I2V_TURBO: "WaveSpeed Seedance 2.0 Turbo",
        VideoGenerationTool.SEEDANCE_2_FAST_I2V: "WaveSpeed Seedance 2.0 Fast",
        VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO: "WaveSpeed Seedance 2.0 Fast Turbo",
        VideoGenerationTool.WAN_2_5: "WaveSpeed Wan 2.5",  # Alibaba Wan 2.5 I2V，可选音频，无尾帧
        VideoGenerationTool.WAN_2_6: "WaveSpeed Wan 2.6 Flash",  # Alibaba Wan 2.6 Flash，720p/1080p，3-15s
        VideoGenerationTool.LTX_2_3: "WaveSpeed LTX 2.3 Lipsync",  # 主流程 lipsync 默认，audio+image→video
        VideoGenerationTool.KLING_V3_STD: "WaveSpeed Kling v3.0 Std",
        VideoGenerationTool.HAPPYHORSE_1_0_I2V: "Alibaba HappyHorse 1.0",
        VideoGenerationTool.HAPPYHORSE_1_1_I2V: "Alibaba HappyHorse 1.1",
        VideoGenerationTool.OPENAI_SORA: "OpenAI Sora",
        VideoGenerationTool.OPENAI_SORA_PRO: "OpenAI Sora Pro"
    }
    
    return tool_names.get(video_tool, "Auto")


# 并发控制配置
# 说明：semaphore控制并发批次数，batch_size控制每批数量，内层API有独立的并发控制
# 实际并发 = semaphore × batch_size（理论值，实际受内层API的semaphore限制）
CONCURRENCY_LIMITS = {
    # 视频片段处理 - FFmpeg资源密集型操作
    "video_segments_processing": 5,  # 单任务处理，无批次

    # 视频组装下载 - S3下载 + FFmpeg操作
    "video_assembly_download": 10,   # 并发下载segment视频（I/O密集型，可以高并发）

    # 场景生成 - LLM调用
    "scene_generation": 10,          # 并发数
    "scene_batch_size": 5,           # 每批数量
    
    # 关键帧生成 - 图像生成API调用（三层控制）
    "keyframe_generation": 10,                    # 第1层：内部API并发（实际控制API调用数）
    "keyframe_batch_processing_semaphore": 10,    # 第2层：批次并发（控制同时处理的批次数）
    "keyframe_batch_size": 4,                     # 第3层：每批数量（10批×4个=理论40并发，实际受第1层限制为10）
    
    # 视频生成 - 视频生成API调用（三层控制）
    "video_generation": 10,                       # 第1层：内部API并发（全局共享，跨批次限制总并发）
    "video_batch_processing_semaphore": 10,       # 第2层：批次并发（控制同时处理的批次数，设为10允许批次并行）
    "video_batch_size": 4,                        # 第3层：每批数量（LLM prompt限制，不能太大）
    
    # 分镜详细生成 - LLM调用
    "storyboard_detail_generation": 10,  # 并发数
    "storyboard_detail_batch_size": 5,   # 每批数量

    # 分镜首帧合规修订 - LLM调用
    "storyboard_first_frame_revision": 10,  # 并发批次数
    "first_frame_revision_batch_size": 5,   # 每批镜头数

    # 视频对嘴型 - 计算密集型操作
    "video_lipsync_processing": 5,   # 并发处理数
    "video_lipsync_selection": 10,   # 并发选择数
    
    # 视觉元素匹配 - LLM调用
    "visual_elements_matching": 10,  # 并发批次数
    
    # 关键帧反思 - VLM调用（视觉分析+LLM调用）
    "keyframe_reflection": 10,        # VLM评审并发数（全部关键帧并发评审）
    "keyframe_reflection_regen": 10,  # 重新生成并发数（评审完需要重生的并发重生）

    # FFmpeg 每进程线程数（多 agent/多并发时避免单进程占满 CPU；0=全部核心）
    "ffmpeg_threads": 2,
}

# 随机延迟范围配置（秒）
RANDOM_DELAY_RANGE = {
    "min_delay": 0.1,  # 最小延迟100ms
    "max_delay": 1.0,  # 最大延迟1s
}


async def add_random_delay():
    """添加随机延迟，避免并发请求过于集中"""
    delay = random.uniform(RANDOM_DELAY_RANGE["min_delay"], RANDOM_DELAY_RANGE["max_delay"])
    await asyncio.sleep(delay)


def get_concurrency_limit(service_name: str) -> int:
    """获取指定服务的并发限制
    
    Args:
        service_name: 服务名称，对应CONCURRENCY_LIMITS中的key
        
    Returns:
        并发限制数量，如果服务名不存在则返回默认值5
    """
    return CONCURRENCY_LIMITS.get(service_name, 5)


def format_history_for_summarization(messages: List[BaseMessage]) -> str:
    """将消息列表格式化为「会议纪要」式文本，便于总结模型生成完成消息。
    
    Best Practice: 不直接给模型原始消息对象，而是给一份结构化描述，减少噪音、突出关键信息。
    
    Args:
        messages: 对话消息列表（HumanMessage / AIMessage / ToolMessage）
        
    Returns:
        格式化后的多行字符串，形如 [用户输入]: ... / [助手执行]: ... / [系统动作]: ... / [动作结果]: ...
    """
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
    
    formatted_lines = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            if msg.content:
                formatted_lines.append(f"[用户输入]: {msg.content}")
        elif isinstance(msg, AIMessage):
            if msg.content:
                formatted_lines.append(f"[助手执行]: {msg.content}")
            if getattr(msg, "tool_calls", None):
                tool_names = [tc.get("name", "?") for tc in msg.tool_calls]
                formatted_lines.append(f"[系统动作]: 调用了工具 {', '.join(tool_names)}")
        elif isinstance(msg, ToolMessage):
            content_str = str(msg.content) if msg.content else ""
            summary = content_str[:100] + ("..." if len(content_str) > 100 else "")
            formatted_lines.append(f"[动作结果]: 任务已完成/报错（摘要: {summary}）")
        else:
            if hasattr(msg, "content") and msg.content:
                formatted_lines.append(f"[其他]: {str(msg.content)[:200]}")
    return "\n".join(formatted_lines) if formatted_lines else "(无对话内容)"


def llm_chunk_content_to_text(raw_chunk: Any) -> str:
    """将 LLM stream chunk.content 抽成纯文本。

    gpt-5.x / Responses API 常返回 list[dict]（含 type=reasoning / type=text）。
    若直接 ''.join 或塞给前端，会报 TypeError 或 UI 出现 [object Object]。
    """
    if raw_chunk is None:
        return ""
    if isinstance(raw_chunk, str):
        return raw_chunk
    if isinstance(raw_chunk, list):
        parts: List[str] = []
        for item in raw_chunk:
            text = llm_chunk_content_to_text(item)
            if text:
                parts.append(text)
        return "".join(parts)
    if isinstance(raw_chunk, dict):
        if raw_chunk.get("type") in ("reasoning", "thinking"):
            return ""
        text = raw_chunk.get("text") or raw_chunk.get("content")
        if isinstance(text, str):
            return text
        if isinstance(text, list):
            return llm_chunk_content_to_text(text)
        return ""
    text_attr = getattr(raw_chunk, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    if isinstance(text_attr, list):
        return llm_chunk_content_to_text(text_attr)
    return ""


async def generate_completion_message_stream(
    event_type: MessageType,
    messages: List[BaseMessage],
    send_event_func: Optional[Any] = None,
    conversation_id: Optional[int] = None,
    lang: Optional[str] = None
) -> tuple[str, Optional[BaseMessage]]:
    """生成节点完成消息（支持流式输出，内部用 VIDEO_COMPLETION_MESSAGE 取 LLM）
    
    Args:
        event_type: 事件类型（如 MessageType.STORY_OUTLINE_GENERATED）
        messages: 本次 node 的完整对话（input + output）
        send_event_func: 发送事件函数（用于 stream）
        conversation_id: 对话ID（用于 stream）
        lang: 语言代码（ISO 639-1）
        
    Returns:
        (user_message, completion_message): 用户消息和AI消息
        
    注意：
        - 使用 i18n 获取任务上下文描述
        - 使用 apply_language_suffix_to_system_message_in_messages 强制语言
        - 不要在消息中暴露供应商、代码、UUID 等内部细节
        - 支持流式输出到前端
    """
    from ....utils.i18n import get_i18n_message_async, get_current_language
    from app.chat.prompts.prompt_loader import load_prompt_with_fallback_async
    from app.chat.prompts.prompt_config import PromptName
    from langchain_core.messages import AIMessage
    from ....services.agent.base_agent import MessageType
    
    # 获取当前语言
    current_lang = lang or get_current_language()
    
    # ✅ 使用 i18n 获取任务上下文
    task_context = await get_i18n_message_async(
        f"completion.{event_type.value}",
        default=event_type.value,
        lang=current_lang
    )
    
    try:
        # 加载 prompt 模板
        prompt_template, agent_llm = await load_prompt_with_fallback_async(
            hub_name=PromptName.VIDEO_COMPLETION_MESSAGE.value,
            local_template_name="video/utils/generate_completion_message",
            schema=None,
            include_raw=False
        )
        
        # 构建对话历史：使用「会议纪要」式格式化，便于总结（Best Practice: Structured Mapping）
        formatted_history = format_history_for_summarization(messages)
        conversation_history = [formatted_history]  # 模板用 list 渲染一节
        
        # 构建 prompt
        # 使用 invoke 格式化消息（LangSmith 可追踪）
        prompt_messages = (await prompt_template.ainvoke({
            "event_type": event_type.value,
            "task_context": task_context,
            "conversation_history": conversation_history,
            "formatted_history": formatted_history,
            "language_suffix": ""  # 稍后添加
        })).messages
        
        # 强制语言（System + Human 都强化）
        apply_language_suffix_to_system_message_in_messages(prompt_messages, current_lang)
        
        # ✅ 流式输出（chunk.content 可能是 Responses list[dict]，必须先抽成 str）
        full_content: List[str] = []
        
        async for chunk in agent_llm.astream(prompt_messages):
            raw = chunk.content if hasattr(chunk, "content") else chunk
            content = llm_chunk_content_to_text(raw)
            
            if content:
                full_content.append(content)
                
                # 发送流式 chunk 事件
                if send_event_func and conversation_id:
                    try:
                        await send_event_func(
                            event_type=MessageType.STREAMING_CHUNK,
                            conversation_id=conversation_id,
                            message=content,
                            extra_data={
                                "target_event": event_type.value,
                                "content_type": "text"
                            }
                        )
                    except Exception as stream_error:
                        logger.warning(f"流式事件发送失败: {stream_error}")
        
        # 构建最终消息
        final_content = "".join(full_content).strip()
        if not final_content:
            raise ValueError("completion stream produced empty text after chunk normalization")
        ai_message = AIMessage(content=final_content)
        
        return final_content, ai_message
        
    except Exception as e:
        logger.error(f"生成完成消息失败: {e}")
        # 降级：返回简单的完成消息
        fallback_msg = await get_i18n_message_async(
            f"{event_type.value}.default",
            default=f"{task_context}完成",
            lang=current_lang
        )
        return fallback_msg, None
