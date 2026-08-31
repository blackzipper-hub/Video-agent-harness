"""
测试多图占位符 {{IMAGES:var}} 与 invoke_prompt_with_multimodal 的集成。

- Loader：{{IMAGES:var}} 替换为 __IMGLIST_var__
- multimodal_post_process：__IMGLIST_var__ 从 template_data[var] 取列表，按顺序插入多张图
- 调用方只需传 list 变量，无需手拼 __IMG_BLOCK_* 锚点

运行（conda env cuti-video-local）：
  cd Cuti-VideoAgent && python -m pytest tests/test_multimodal_images_invoke.py -v -s
"""
import pytest
from langchain_core.messages import HumanMessage


def test_loader_replaces_images_placeholder():
    """Loader 将 {{IMAGES:var}} 替换为 __IMGLIST_var__"""
    from prompts.prompt_loader import _replace_multimodal_placeholders_with_anchors

    s = "参考图：{{IMAGES:refs}} 生成图：{{IMAGE:result}}"
    out = _replace_multimodal_placeholders_with_anchors(s)
    assert "__IMGLIST_refs__" in out
    assert "__IMG_result__" in out
    assert "{{IMAGES:" not in out
    assert "{{IMAGE:" not in out


@pytest.mark.asyncio
async def test_multimodal_post_process_imglist_expands_list():
    """multimodal_post_process 将 __IMGLIST_var__ 展开为多块 image content"""
    from app.services.agent.utils.multimodal_post_process import process

    content = "参考图：__IMGLIST_refs__ 生成图：__IMG_result__"
    messages = [HumanMessage(content=content)]
    template_data = {
        "refs": [
            "https://example.com/ref1.png",
            "https://example.com/ref2.png",
        ],
        "result": "https://example.com/result.png",
    }
    out = await process(messages, template_data)
    assert len(out) == 1
    msg = out[0]
    assert hasattr(msg, "content")
    assert isinstance(msg.content, list)
    # 顺序：text "参考图：" -> 2 images (refs) -> text " 生成图：" -> 1 image (result)
    parts = msg.content
    text_parts = [p for p in parts if p.get("type") == "text"]
    image_parts = [p for p in parts if p.get("type") == "image_url"]
    assert len(image_parts) == 3
    assert image_parts[0]["image_url"]["url"] == "https://example.com/ref1.png"
    assert image_parts[1]["image_url"]["url"] == "https://example.com/ref2.png"
    assert image_parts[2]["image_url"]["url"] == "https://example.com/result.png"
    assert len(text_parts) >= 1


@pytest.mark.asyncio
async def test_multimodal_post_process_imglist_supports_dict_with_url():
    """template_data[var] 每项可为 {url: ...} 或直接 URL 字符串"""
    from app.services.agent.utils.multimodal_post_process import process

    content = "前 __IMGLIST_refs__ 后"
    messages = [HumanMessage(content=content)]
    template_data = {
        "refs": [
            {"url": "https://example.com/a.png"},
            "https://example.com/b.png",
        ],
    }
    out = await process(messages, template_data)
    assert len(out) == 1
    image_parts = [p for p in out[0].content if p.get("type") == "image_url"]
    assert len(image_parts) == 2
    assert image_parts[0]["image_url"]["url"] == "https://example.com/a.png"
    assert image_parts[1]["image_url"]["url"] == "https://example.com/b.png"


@pytest.mark.asyncio
async def test_invoke_prompt_with_multimodal_images_list_e2e():
    """端到端：加载含 {{IMAGES:refs}} {{IMAGE:result}} 的模板，invoke 后得到正确 content 列表"""
    from prompts.prompt_loader import load_local_mustache_template, invoke_prompt_with_multimodal

    template = load_local_mustache_template("test_multimodal_images")
    template_data = {
        "refs": [
            "https://example.com/r1.png",
            "https://example.com/r2.png",
        ],
        "result": "https://example.com/out.png",
    }
    messages = await invoke_prompt_with_multimodal(template, template_data)
    assert len(messages) == 1
    msg = messages[0]
    assert isinstance(msg.content, list)
    image_parts = [p for p in msg.content if p.get("type") == "image_url"]
    assert len(image_parts) == 3
    assert image_parts[0]["image_url"]["url"] == "https://example.com/r1.png"
    assert image_parts[1]["image_url"]["url"] == "https://example.com/r2.png"
    assert image_parts[2]["image_url"]["url"] == "https://example.com/out.png"
