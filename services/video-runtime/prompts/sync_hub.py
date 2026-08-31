"""
LangSmith Hub 同步脚本
将本地 Prompt 模板和结构化输出 Schema 同步到 LangSmith Hub
"""
import os
import asyncio
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv
from langsmith import Client
from langchain_core.prompts import ChatPromptTemplate

# 加载 .env.development
env_file = Path(__file__).parent.parent / ".env.development"
if env_file.exists():
    load_dotenv(env_file)
    print(f"✅ 已加载环境变量: {env_file}")
else:
    print(f"⚠️  未找到 .env.development: {env_file}")

# 初始化 LangSmith Client
client = Client()

# 导入共享配置
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from prompts.prompt_config import PROMPTS_CONFIG, create_llm, PromptName


def parse_mustache_file(file_path: Path) -> tuple[str, str]:
    """
    解析 Mustache 模板文件，提取 System 和 Human Message
    
    注意：不再解析 YAML frontmatter，所有配置（如 model_config）都在 PROMPTS_CONFIG 中定义
    
    返回：(system_message, human_message)
    """
    content = file_path.read_text(encoding='utf-8')
    
    # 跳过 YAML frontmatter（如果存在）
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            content = parts[2]  # 跳过 frontmatter，只取模板内容
    
    # 分割 System 和 Human Message
    if "## System Message" in content and "## Human Message" in content:
        _, rest = content.split("## System Message", 1)
        system_msg, human_msg = rest.split("## Human Message", 1)
        return system_msg.strip(), human_msg.strip()
    elif "## Human Message" in content:
        human_msg = content.split("## Human Message", 1)[1].strip()
        return "", human_msg
    else:
        # 没有标记，全部作为 human message
        return "", content.strip()


def create_chat_template(system_content: str, human_content: str) -> ChatPromptTemplate:
    """
    创建 ChatPromptTemplate
    
    注意：LangSmith Hub 原生支持 Mustache 模板格式
    """
    return ChatPromptTemplate.from_messages(
        [
            ("system", system_content),
            ("human", human_content)
        ],
        template_format="mustache"  # 明确指定使用 mustache 格式
    )


async def sync_prompt_to_hub(repo_name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    同步 Prompt Chain 到 LangSmith Hub
    
    Push 的是 prompt | model chain，包含完整的 model config
    
    Args:
        repo_name: Hub 仓库名（如 "batch-gen"）
        config: Prompt 配置
    
    Returns:
        {"status": "updated"|"unchanged"|"error"|"not_found", "file": str}
    """
    prompts_dir = Path(__file__).parent
    template_file = prompts_dir / config["file"]
    
    if not template_file.exists():
        print(f"❌ 文件不存在: {template_file}")
        return {"status": "not_found", "file": config["file"], "repo": repo_name}
    
    print(f"📝 读取模板: {template_file.name}")
    
    # 解析 Mustache 模板（不解析 YAML，配置都在 PROMPTS_CONFIG 中）
    system_content, human_content = parse_mustache_file(template_file)
    
    # 创建 ChatPromptTemplate（LangChain 原生支持 Mustache）
    prompt_template = create_chat_template(system_content, human_content)
    
    # 从 PROMPTS_CONFIG 读取 schema 和 model_config
    schema = config.get("schema")
    model_cfg = config.get("model_config")
    
    # 如果没有 schema 和 model_config，推送纯 prompt template
    if not schema and not model_cfg:
        print(f"  📋 Schema: None (推送纯 prompt template)")
        object_to_push = prompt_template
        print(f"  🔗 推送类型: ChatPromptTemplate")
    elif not schema:
        # 有 model_cfg 但没有 schema，推送 prompt + model（无 structured output）
        print(f"  📋 Schema: None")
        print(f"  🤖 模型配置: {model_cfg.get('model', 'N/A')}")
        llm = create_llm(model_cfg)
        object_to_push = prompt_template | llm
        print(f"  🔗 推送类型: ChatPromptTemplate | Model")
    elif not model_cfg:
        print(f"  ❌ 错误: 有 schema 但缺少 model_config，跳过")
        return {"status": "error", "file": config["file"], "repo": repo_name, "error": "Missing model_config"}
    else:
        # 有 schema 和 model_cfg，推送 prompt + model
        print(f"  📋 Schema (本地使用): {schema.__name__}")
        print(f"  🤖 模型配置: {model_cfg.get('model', 'N/A')}")
        
        # 创建普通的 ChatPromptTemplate（不使用 StructuredPrompt）
        # StructuredPrompt 是 beta 功能，支持不稳定
        # 我们只推送 prompt + model，本地代码自己 with_structured_output
        
        # 创建 LLM（支持多种 model）
        llm = create_llm(model_cfg)
        
        # 创建简单的 chain: ChatPromptTemplate | Model
        # 注意：不包含 structured output，本地代码会自己添加
        object_to_push = prompt_template | llm
        
        print(f"  🔗 推送类型: ChatPromptTemplate | Model")
        print(f"  💡 本地代码需要: chain.first | chain.last.with_structured_output(Schema)")
    
    try:
        # 推送到 LangSmith Hub
        print(f"🚀 推送到 Hub: {repo_name}")
        
        # Push chain（包含 prompt + model）或 prompt
        client.push_prompt(
            repo_name,
            object=object_to_push,  # Push chain 而不是单独的 template
            description=config["description"],
            tags=config.get("tags", []),
            is_public=False  # 🔒 设置为私有
        )
        
        print(f"✅ 成功推送到 Hub: {repo_name}")
        print(f"   可见性: 🔒 Private")
        

        
        print(f"   描述: {config['description']}")
        print(f"   标签: {', '.join(config.get('tags', []))}")
        
        return {"status": "updated", "file": config["file"], "repo": repo_name}
        
    except Exception as e:
        # 409 Conflict 表示内容没变化，属于正常情况
        if "409" in str(e) and "Nothing to commit" in str(e):
            print(f"✅ 已是最新: {repo_name} (无内容变化)")
            print(f"   💡 如需打 prod 标签，请在 Hub UI 中手动操作")
            return {"status": "unchanged", "file": config["file"], "repo": repo_name}
        else:
            print(f"❌ 推送失败: {repo_name}")
            print(f"   错误: {str(e)[:200]}")
            return {"status": "error", "file": config["file"], "repo": repo_name, "error": str(e)[:200]}


async def sync_all_prompts():
    """同步所有 Prompt 到 LangSmith Hub"""
    print("=" * 60)
    print("🚀 LangSmith Hub Prompt 同步工具")
    print("=" * 60)
    print()
    
    # 收集所有结果
    results = []
    for repo_name, config in PROMPTS_CONFIG.items():
        print(f"\n📦 处理: {repo_name}")
        print("-" * 60)
        result = await sync_prompt_to_hub(repo_name, config)
        results.append(result)
    
    # 统计结果
    updated = [r for r in results if r["status"] == "updated"]
    unchanged = [r for r in results if r["status"] == "unchanged"]
    errors = [r for r in results if r["status"] == "error"]
    not_found = [r for r in results if r["status"] == "not_found"]
    
    # 输出汇总统计
    print("\n" + "=" * 60)
    print("📊 同步结果统计")
    print("=" * 60)
    print(f"\n总计: {len(results)} 个 prompt")
    print(f"  ✅ 已更新（有改动）: {len(updated)} 个")
    print(f"  ⭕ 无变化（已是最新）: {len(unchanged)} 个")
    print(f"  ❌ 推送失败: {len(errors)} 个")
    print(f"  ⚠️  文件不存在: {len(not_found)} 个")
    
    # 详细列表
    if updated:
        print(f"\n🔄 有改动的 Prompt 文件（{len(updated)}）：")
        for r in updated:
            print(f"  ✓ {r['file']}")
            print(f"    └─ Hub: {r['repo']}")
    
    if unchanged:
        print(f"\n⚪ 无改动的 Prompt 文件（{len(unchanged)}）：")
        for r in unchanged:
            print(f"  • {r['file']}")
    
    if errors:
        print(f"\n❌ 推送失败的 Prompt 文件（{len(errors)}）：")
        for r in errors:
            print(f"  ✗ {r['file']}")
            print(f"    └─ 错误: {r.get('error', 'Unknown')}")
    
    if not_found:
        print(f"\n⚠️  文件不存在（{len(not_found)}）：")
        for r in not_found:
            print(f"  ? {r['file']}")
    
    print("\n" + "=" * 60)
    print("✅ 同步完成")
    print("=" * 60)


def main():
    """主函数"""
    asyncio.run(sync_all_prompts())


if __name__ == "__main__":
    main()

