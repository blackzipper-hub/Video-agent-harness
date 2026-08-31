"""
Prompt 加载器 - 支持从 LangSmith Hub 拉取或从本地 Mustache 文件加载

多模态占位符：模板中可写 {{IMAGE:var}}, {{IMAGES:var}}, {{AUDIO:var}}, {{VIDEO:var}}；
Loader 会替换为锚点 __IMG_var__、__IMGLIST_var__ 等，invoke 后由 multimodal_post_process 替换为真实 content 块。
"""
import asyncio
import re
import logging
import os
from typing import List, Tuple, Optional, Any, Type, Dict
from pydantic import BaseModel
from langchain_core.prompts import ChatPromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
logger = logging.getLogger(__name__)

# 多模态占位符 -> 锚点，避免 Mustache 把占位符当变量渲染掉
_MULTIMODAL_PATTERN = re.compile(r"\{\{(IMAGE|IMAGES|AUDIO|VIDEO):(\w+)\}\}", re.IGNORECASE)


# 占位符类型 -> 锚点前缀（与 multimodal_post_process 一致）；IMAGES 为多图列表，对应 __IMGLIST_var__
_MULTIMODAL_KIND_TO_PREFIX = {"IMAGE": "IMG", "IMAGES": "IMGLIST", "AUDIO": "AUD", "VIDEO": "VID"}


def _replace_multimodal_placeholders_with_anchors(human_content: str) -> str:
    """将 {{IMAGE:var}}, {{IMAGES:var}}, {{AUDIO:var}}, {{VIDEO:var}} 替换为锚点 __IMG_var__, __IMGLIST_var__ 等。"""
    def repl(m: re.Match) -> str:
        kind, var = m.group(1).upper(), m.group(2)
        prefix = _MULTIMODAL_KIND_TO_PREFIX[kind]
        return f"__{prefix}_{var}__"
    return _MULTIMODAL_PATTERN.sub(repl, human_content)


def _apply_anchor_replacement_to_hub_prompt(prompt: ChatPromptTemplate) -> ChatPromptTemplate:
    """
    对从 Hub 拉取的 ChatPromptTemplate 的每条消息做多模态占位符 -> 锚点替换，
    使 Hub 模板与本地 Mustache 行为一致（业务侧只需传 keyframe_url 等，无需传 IMAGE:keyframe_url）。
    """
    new_messages: List[Tuple[str, str]] = []
    for msg in prompt.messages:
        if not hasattr(msg, "prompt") or not hasattr(msg.prompt, "template"):
            continue
        template_str = msg.prompt.template
        new_template = _replace_multimodal_placeholders_with_anchors(template_str)
        role = "system" if isinstance(msg, SystemMessagePromptTemplate) else "human"
        new_messages.append((role, new_template))
    if not new_messages:
        return prompt
    template_format = getattr(prompt, "template_format", "mustache")
    return ChatPromptTemplate.from_messages(new_messages, template_format=template_format)

# 导入共享配置
from .prompt_config import PROMPTS_CONFIG, create_llm, PromptName

# 供需要「异常重试」的调用方使用（如 video consistency），与 video_generation_service 的 with_retry 参数一致
DEFAULT_LLM_RETRY_CONFIG = {
    "retry_if_exception_type": (Exception,),
    "stop_after_attempt": 3,
    "wait_exponential_jitter": True,
}

def _get_hub_name_with_tag(hub_name: str) -> str:
    """
    根据环境为 hub_name 添加适当的 tag
    
    - Local: 使用 :local tag
    - Development: 使用 :dev tag
    - Production: 使用 :prod tag
    
    Args:
        hub_name: 基础 hub 名称，如 "style-detection"
        
    Returns:
        带 tag 的 hub 名称，如 "style-detection:local"、"style-detection:dev" 或 "style-detection:prod"
    """
    # 如果已经包含 tag，直接返回
    if ':' in hub_name:
        return hub_name
    
    try:
        from app.config import get_settings, EnvironmentType
        settings = get_settings()
        
        if settings.ENVIRONMENT == EnvironmentType.PRODUCTION:
            # 生产环境使用 :prod tag
            hub_name_with_tag = f"{hub_name}:prod"
            logger.info(f"🏷️  生产环境: 使用 {hub_name_with_tag}")
            return hub_name_with_tag
        elif settings.ENVIRONMENT == EnvironmentType.LOCAL:
            # 本地环境使用 :local tag
            hub_name_with_tag = f"{hub_name}:local"
            logger.info(f"🏷️  本地环境: 使用 {hub_name_with_tag}")
            return hub_name_with_tag
        else:
            # 开发环境使用 :dev tag
            hub_name_with_tag = f"{hub_name}:dev"
            logger.info(f"🏷️  开发环境: 使用 {hub_name_with_tag}")
            return hub_name_with_tag
    except Exception as e:
        logger.warning(f"无法获取环境配置，使用默认 hub_name: {e}")
        return hub_name

def load_local_mustache_template(template_name: str) -> ChatPromptTemplate:
    """
    从本地 Mustache 文件加载 Prompt 模板
    
    注意：不再解析 YAML frontmatter，所有配置（如 model_config）都在代码的 CONFIG 中定义
    
    Args:
        template_name: 模板名称，如 "video_generation/style_detection"
        
    Returns:
        ChatPromptTemplate: 加载的模板
    """
    # 获取当前文件所在目录
    base_dir = os.path.dirname(__file__)
    file_path = os.path.join(base_dir, f"{template_name}.mustache")
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"本地模板文件不存在: {file_path}")
    
    # 读取文件内容
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 跳过 YAML frontmatter（如果存在）
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            content = parts[2]
    
    # 分割 System 和 Human Message
    if "## System Message" in content and "## Human Message" in content:
        _, rest = content.split("## System Message", 1)
        system_content, human_content = rest.split("## Human Message", 1)
        human_content = _replace_multimodal_placeholders_with_anchors(human_content.strip())
        messages = [
            ("system", system_content.strip()),
            ("human", human_content)
        ]
    elif "## Human Message" in content:
        human_content = content.split("## Human Message", 1)[1].strip()
        human_content = _replace_multimodal_placeholders_with_anchors(human_content)
        messages = [("human", human_content)]
    else:
        human_content = content.strip()
        human_content = _replace_multimodal_placeholders_with_anchors(human_content)
        messages = [("human", human_content)]
    
    # 使用 ChatPromptTemplate.from_messages 创建
    return ChatPromptTemplate.from_messages(messages, template_format="mustache")


def _apply_retry_if_requested(runnable: Runnable, retry_config: Optional[Dict[str, Any]]) -> Runnable:
    """当 retry_config 非空时对 runnable 包一层 with_retry（异常重试）。"""
    if not retry_config:
        return runnable
    runnable = runnable.with_retry(**retry_config)
    logger.info(f"   🔄 已为 LLM 添加 with_retry: stop_after_attempt={retry_config.get('stop_after_attempt')}")
    return runnable


def create_llm_from_model_config(model_config: dict) -> Runnable:
    """
    与 ``load_prompt_with_fallback`` / ``_load_prompt_from_local`` 内部一致：仅用 ``model_config`` 调 ``create_llm``。

    - **不含** ``with_structured_output``（结构化由业务：``create_agent`` / ``chain`` 自行绑定）。
    - **不含** ``with_retry``（LangChain 级重试由调用方传入 ``retry_config`` 后在 ``_load_prompt_from_local`` 末尾统一包，或由 ``llm_resilience.execute_with_resilience`` 处理；避免双包）。
    """
    if not model_config:
        raise ValueError("model_config 不能为空")
    return create_llm(model_config)


def _is_gemini3_model(model_name: str) -> bool:
    """仅当模型为 Gemini 3 时返回 True，用于 structured output 使用 method=json_schema。"""
    return "gemini-3" in (model_name or "")


def _load_prompt_from_local(
    hub_name: str,
    local_template_name: str,
    schema: Optional[Type[BaseModel]],
    include_raw: bool,
    retry_config: Optional[Dict[str, Any]] = None,
) -> Tuple[ChatPromptTemplate, Runnable]:
    """从本地 Mustache + PROMPTS_CONFIG 加载 prompt 和 LLM。"""
    try:
        prompt_name = PromptName(hub_name)
    except ValueError:
        prompt_name = hub_name
    config = PROMPTS_CONFIG.get(prompt_name)
    if not config:
        raise ValueError(f"未找到 {hub_name} 的配置")
    model_config = config.get("model_config")
    if not model_config:
        raise ValueError(f"{hub_name} 缺少 model_config 配置")
    prompt_template = load_local_mustache_template(local_template_name)
    logger.info(f"✅ 成功从本地加载模板: {local_template_name}")
    from prompts.llm_model_profiles import resolve_model_config

    resolved_mc = resolve_model_config(dict(model_config))
    fallback_llm = create_llm_from_model_config(model_config)
    logger.info(f"   🤖 使用配置创建 LLM: {resolved_mc.get('model')} (config={model_config})")
    if schema:
        model_name = resolved_mc.get("model") or ""
        if _is_gemini3_model(model_name):
            structured_llm = fallback_llm.with_structured_output(
                schema, include_raw=include_raw, method="json_schema"
            )
            logger.info(f"   ✅ 已创建 with_structured_output({schema.__name__}, include_raw={include_raw}, method=json_schema) [Gemini 3]")
        else:
            structured_llm = fallback_llm.with_structured_output(schema, include_raw=include_raw)
            logger.info(f"   ✅ 已创建 with_structured_output({schema.__name__}, include_raw={include_raw})")
        return (prompt_template, _apply_retry_if_requested(structured_llm, retry_config))
    logger.info(f"   ✅ 返回原始 LLM（未绑定 structured output）")
    return (prompt_template, _apply_retry_if_requested(fallback_llm, retry_config))


def load_prompt_with_fallback(
    hub_name: str,
    local_template_name: str,
    schema: Optional[Type[BaseModel]] = None,
    include_raw: bool = True,
    retry_config: Optional[Dict[str, Any]] = None,
) -> Tuple[ChatPromptTemplate, Runnable]:
    """
    加载 Prompt：统一使用本地 Mustache 文件 + PROMPTS_CONFIG 中的 LLM 配置。

    所有环境（local / dev / prod）均直接加载本地模板，不请求 LangSmith Hub。
    这样 prompt 完全跟随代码版本，无网络依赖，且行为在所有环境中一致。

    - retry_config: 可选，传入时对返回的 model 包一层 with_retry（异常重试），如 {"retry_if_exception_type": (Exception,), "stop_after_attempt": 3, "wait_exponential_jitter": True}。
    """
    return _load_prompt_from_local(hub_name, local_template_name, schema, include_raw, retry_config)


async def load_prompt_with_fallback_async(
    hub_name: str,
    local_template_name: str,
    schema: Optional[Type[BaseModel]] = None,
    include_raw: bool = True,
    retry_config: Optional[Dict[str, Any]] = None,
) -> Tuple[ChatPromptTemplate, Runnable]:
    """
    异步版本：在线程池中执行 load_prompt_with_fallback，避免阻塞事件循环。
    在 async 上下文中应使用本函数并 await，例如：
        prompt_template, llm = await load_prompt_with_fallback_async(...)
    retry_config: 可选，传入时对返回的 model 包 with_retry（异常重试）。
    """
    return await asyncio.to_thread(
        load_prompt_with_fallback,
        hub_name,
        local_template_name,
        schema=schema,
        include_raw=include_raw,
        retry_config=retry_config,
    )


async def invoke_prompt_with_multimodal(
    prompt_template: ChatPromptTemplate,
    template_data: dict,
    model_provider: Optional[str] = None,
) -> List[BaseMessage]:
    """
    对带多模态占位符的模板：invoke 后做多模态后处理，将锚点替换为真实 content 块。
    业务侧只需组好 template_data（含图片 URL、音频 {data,mime_type}、视频 URL 等），
    即可得到最终发给 LLM 的 messages。
    """
    messages = (await prompt_template.ainvoke(template_data)).messages
    from app.services.agent.utils.multimodal_post_process import process as multimodal_process
    return await multimodal_process(messages, template_data, model_provider)

