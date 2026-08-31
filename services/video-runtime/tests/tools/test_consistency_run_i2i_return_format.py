"""
单独测试：consistency run 里 I2I tool.ainvoke() 实际返回什么格式。

目的：确认 worker 应按哪种方式拆包；若后续 LangChain 行为变化，跑此测试即可看到当前返回形状。

运行（需项目依赖 + pytest）：
  pytest tests/tools/test_consistency_run_i2i_return_format.py -v -s

无 pytest 时可用脚本（需 PYTHONPATH=. 且安装依赖）：
  python tests/tools/script_check_i2i_return.py
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.models.tool_enums import ToolMode
from app.models.user_options import UserOption
from app.services.tool_service import ToolService
from app.tools.context_schemas import ImageGenerationContext
from app.models.image_result import ImageGenerationResult


@pytest.mark.asyncio
async def test_i2i_ainvoke_return_format(monkeypatch):
    """调用与 consistency worker 相同方式获取的 I2I 工具，mock 内部不调 API，只检查 ainvoke 返回类型。"""
    from app.tools.image import image_tool_wrapper

    # 固定返回，避免真实 API
    fixed_result = ImageGenerationResult(
        success=True,
        image_url="https://example.com/fake.webp",
        message="mock",
    )

    async def _mock_run_i2i_loop(*args, **kwargs):
        return fixed_result

    # 与 worker 一致：ToolService.get_image_generation_tools(mode=I2I).primary_tool
    user_option = UserOption.default()
    tools_info = ToolService.get_image_generation_tools(user_option=user_option, mode=ToolMode.I2I)
    i2i_tool = tools_info.primary_tool
    assert i2i_tool is not None

    monkeypatch.setattr(image_tool_wrapper, "_run_i2i_loop", _mock_run_i2i_loop)

    ctx = ImageGenerationContext(reference_image_urls=["https://example.com/ref.webp"])
    runtime = SimpleNamespace(context=ctx)
    out = await i2i_tool.ainvoke(
        {
            "prompt": "test",
            "reference_image_urls": ["https://example.com/ref.webp"],
            "runtime": runtime,
        },
        config={"metadata": {"consistency_run_id": "test"}},
    )

    # 打印实际返回，便于确认
    print("\n--- I2I ainvoke 返回 ---")
    print("type(out) =", type(out).__name__)
    print("isinstance(out, (list, tuple)) =", isinstance(out, (list, tuple)))
    if isinstance(out, (list, tuple)):
        print("len(out) =", len(out))
        for i, x in enumerate(out):
            print(f"  out[{i}] type =", type(x).__name__)
            if hasattr(x, "success"):
                print(f"  out[{i}].success =", x.success)
    else:
        print("hasattr(out, 'success') =", hasattr(out, "success"))
        print("hasattr(out, 'artifact') =", hasattr(out, "artifact"))
        if hasattr(out, "artifact"):
            a = out.artifact
            print("  out.artifact type =", type(a).__name__)
            print("  hasattr(out.artifact, 'success') =", hasattr(a, "success"))
        if hasattr(out, "success"):
            print("out.success =", out.success)

    # 断言：worker 需要的格式 —— 能拿到 artifact 且 artifact 有 .success
    if isinstance(out, (list, tuple)) and len(out) >= 2:
        content, artifact = out[0], out[1]
        assert hasattr(artifact, "success"), "artifact 应有 success 属性"
        assert artifact.success is True
        assert artifact.image_url == fixed_result.image_url
        print("\n结论: ainvoke 返回 (content, artifact) 元组，worker 用 content, artifact = out 即可。")
        return

    if getattr(out, "artifact", None) is not None:
        artifact = out.artifact
        assert hasattr(artifact, "success")
        print("\n结论: ainvoke 返回带 .artifact 的对象（如 ToolMessage），worker 应取 out.artifact。")
        return

    if hasattr(out, "success"):
        assert out.success is True
        assert out.image_url == fixed_result.image_url
        print("\n结论: ainvoke 直接返回 artifact（ImageGenerationResult），worker 应把 out 当 artifact。")
        return

    if isinstance(out, str) and out.strip().startswith("{"):
        import json
        parsed = json.loads(out)
        if parsed.get("success") is True and parsed.get("image_url") == fixed_result.image_url:
            print("\n结论: ainvoke 返回 content 的 JSON 字符串（tuple 的第一项），worker 需 json.loads(out) 当 artifact。")
            return
    pytest.fail(f"未识别的返回格式: type={type(out)}, out={out!r}")
