"""
单独小测试：验证 LangChain ToolMessage 对 tool 返回值的序列化方式（repr vs JSON）。

源码依据（conda env cuti-video-local 下 site-packages）：
1. langchain_core/messages/tool.py 第 89-111 行 ToolMessage.coerce_args：
   - if not isinstance(content, (str, list)): values["content"] = str(content)  → 得到 repr
2. langchain_core/tools/base.py 第 1237-1266 行 _format_output：
   - 若 content 不是 ToolOutputMixin 且 tool_call_id 存在，再判 _is_message_content_type(content)
   - 若非 str/list of blocks，content = _stringify(content)
3. 第 1303-1315 行 _stringify：先 json.dumps(content)，失败则 return str(content)  → Pydantic 模型会走 str(content)

因此 wrapper 若直接 return VideoGenerationResult，ToolMessage.content 最终是 repr，无法 json.loads。
改为 return result.model_dump_json() 后，content 为我们控制的 JSON 字符串。

运行（需 conda env cuti-video-local）：
  cd Cuti-VideoAgent && python -m pytest tests/tools/test_tool_message_content_serialization.py -v -s
"""
import json
import pytest
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


class FakeToolResult(BaseModel):
    """模拟 VideoGenerationResult 的 metrics 相关字段"""
    success: bool = True
    video_url: Optional[str] = "https://example.com/v.mp4"
    tool_duration_sec: Optional[float] = 12.345
    tool_cost: Optional[float] = 0.05
    video_tool_metrics: Optional[Dict[str, Any]] = None


def test_tool_message_content_coercion_repr():
    """ToolMessage 对非 str/list 的 content 会做 str(content)，得到 repr，无法 json.loads"""
    from langchain_core.messages.tool import ToolMessage

    result = FakeToolResult(
        success=True,
        video_url="https://example.com/v.mp4",
        tool_duration_sec=12.345,
        tool_cost=0.05,
        video_tool_metrics={"total_attempts": 1},
    )
    # 模拟：tool 直接返回 Pydantic 对象，被塞进 ToolMessage 时发生 coercion
    msg = ToolMessage(content=result, tool_call_id="call_123")
    content = msg.content

    assert isinstance(content, str)
    # repr 形式类似 "FakeToolResult(success=True, video_url='https://...', ...)"
    assert "FakeToolResult" in content or "tool_duration_sec" in content
    # repr 不是合法 JSON，无法解析
    with pytest.raises(json.JSONDecodeError):
        json.loads(content)
    # 因此无法从 content 里可靠取出 tool_duration_sec / tool_cost
    print("  [repr] content 前 200 字符:", content[:200])
    print("  [repr] json.loads 会失败 ✓")


def test_tool_message_content_json_string():
    """若 tool 返回 model_dump_json()，content 为 JSON 字符串，可解析并取出 metrics"""
    from langchain_core.messages.tool import ToolMessage

    result = FakeToolResult(
        success=True,
        video_url="https://example.com/v.mp4",
        tool_duration_sec=12.345,
        tool_cost=0.05,
        video_tool_metrics={"total_attempts": 1},
    )
    json_str = result.model_dump_json()
    msg = ToolMessage(content=json_str, tool_call_id="call_123")
    content = msg.content

    assert isinstance(content, str)
    data = json.loads(content)
    assert data["tool_duration_sec"] == 12.345
    assert data["tool_cost"] == 0.05
    assert data["video_tool_metrics"] == {"total_attempts": 1}
    print("  [json] content 可 json.loads，tool_duration_sec=", data["tool_duration_sec"])
    print("  [json] tool_cost=", data["tool_cost"])


def test_langchain_base_stringify_pydantic():
    """复现 base._stringify：json.dumps(pydantic_model) 失败后 fallback 到 str(model)"""
    from langchain_core.tools.base import _stringify

    result = FakeToolResult(tool_duration_sec=1.0, tool_cost=0.01)
    out = _stringify(result)
    # Pydantic 模型不能直接被 json.dumps，会走 except 分支 str(content)
    assert isinstance(out, str)
    try:
        json.loads(out)
        parsed = True
    except json.JSONDecodeError:
        parsed = False
    assert not parsed, "Pydantic 经 _stringify 后应为 repr，不应是合法 JSON"
    print("  [_stringify] 输出为 repr，非 JSON ✓")


def test_langchain_base_stringify_dict():
    """dict 经 _stringify 会得到 JSON 字符串"""
    from langchain_core.tools.base import _stringify

    d = {"success": True, "tool_duration_sec": 10.5, "tool_cost": 0.02}
    out = _stringify(d)
    assert isinstance(out, str)
    data = json.loads(out)
    assert data["tool_duration_sec"] == 10.5
    print("  [_stringify] dict 会得到 JSON 字符串 ✓")
