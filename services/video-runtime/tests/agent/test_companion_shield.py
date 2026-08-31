"""VA 侧 video_edit SSE / shield 模块 smoke tests.

覆盖：
- tool_registry.to_public_tool_call_payload / to_public_tool_result_payload / public_error_payload
- conversation_sanitize.sanitize_message_for_public: 保留未知 event_type、字段白/黑名单
"""
from __future__ import annotations

import pytest

from app.services.agent.video_edit import tool_registry as tr
from app.services.agent import conversation_sanitize as cs


# ============================================================
# tool_registry
# ============================================================

class TestToolRegistry:
    @pytest.mark.parametrize("tool,expected_key", [
        ("get_project_status",   "tool.project.inspect"),
        ("get_artifact_detail",  "tool.artifact.inspect"),
        ("regenerate_keyframes", "tool.keyframe.regenerate"),
        ("regenerate_videos",    "tool.video.regenerate"),
        ("regenerate_characters","tool.character.regenerate"),
        ("reassemble_video",     "tool.assembly.rebuild"),
        ("continue_pipeline",    "tool.pipeline.continue"),
        ("select_version",       "tool.version.select"),
        ("update_music_prompt",  "tool.music.update"),
        ("modify_outline",       "tool.outline.update"),
    ])
    def test_call_payload_all_10_tools_map_to_known_key(self, tool, expected_key):
        assert tr.to_public_tool_call_payload(tool) == {"message_key": expected_key}

    def test_call_payload_unknown_tool_falls_back(self):
        assert tr.to_public_tool_call_payload("nonexistent_tool") == {
            "message_key": "tool.running",
        }

    def test_call_payload_never_contains_raw_tool_name(self):
        for tool in tr.TOOL_PUBLIC_REGISTRY.keys():
            payload = tr.to_public_tool_call_payload(tool)
            assert tool not in str(payload), f"tool name {tool} leaked"

    def test_result_payload_with_str_output_empty_result(self):
        # 现实情况：所有 tool 都 return str
        payload = tr.to_public_tool_result_payload(
            "get_project_status",
            "阶段:scene_generation 任务状态:running 镜头数:6",
        )
        assert payload == {
            "message_key": "tool.project.inspect", "result": {},
        }

    def test_result_payload_with_dict_applies_whitelist(self):
        # 未来 tool 可能返回 dict
        payload = tr.to_public_tool_result_payload(
            "regenerate_keyframes",
            {
                "shot_numbers": [1, 2],
                "job_count": 2,
                "secret_internal_state": "leak",
                "raw_llm_prompt": "ignore this",
            },
        )
        assert payload["message_key"] == "tool.keyframe.regenerate"
        assert payload["result"] == {"shot_numbers": [1, 2], "job_count": 2}
        assert "secret_internal_state" not in payload["result"]
        assert "raw_llm_prompt" not in payload["result"]

    def test_result_payload_unknown_tool(self):
        assert tr.to_public_tool_result_payload("???", {"a": 1}) == {
            "message_key": "tool.done",
        }

    def test_error_payload_no_raw_message(self):
        p = tr.public_error_payload()
        assert p == {"message_key": "video_edit.internal_error"}

    def test_error_payload_with_log_id(self):
        p = tr.public_error_payload("log-abc")
        assert p == {
            "message_key": "video_edit.internal_error", "log_id": "log-abc",
        }


# ============================================================
# conversation_sanitize.sanitize_message_for_public
# ============================================================

class TestSanitizeMessageForPublic:
    def _msg(self, **over):
        base = {
            "id": 123,
            "uuid": "msg-uuid-1",
            "conversation_id": 99,
            "run_id": "run-xyz",
            "role": "assistant",
            "content": "一段对话内容",
            "event_type": "video_completed",
            "event_data": {},
            "meta_data": {"debug": "internal_only"},
            "additional_data": {"trace_id": "t-123"},
            "created_at": "2026-01-01T00:00:00",
            "sequence": 1,
        }
        base.update(over)
        return base

    def test_keeps_core_fields(self):
        out = cs.sanitize_message_for_public(self._msg())
        for k in ("id", "uuid", "conversation_id", "run_id", "role",
                  "content", "created_at", "sequence", "event_type"):
            assert k in out

    def test_drops_meta_and_additional(self):
        out = cs.sanitize_message_for_public(self._msg())
        assert "meta_data" not in out
        assert "additional_data" not in out

    def test_unknown_event_type_preserved_not_dropped(self):
        """回归保护：未来新增 event_type 不应导致整条消息被丢弃。"""
        out = cs.sanitize_message_for_public(self._msg(
            event_type="brand_new_event_2026",
            event_data={"run_id": "r", "some_new_field": "public"},
        ))
        assert out["event_type"] == "brand_new_event_2026"
        assert "run_id" in out["event_data"]
        # 未知字段默认丢（字段级白名单）
        assert "some_new_field" not in out["event_data"]

    def test_event_data_whitelist_keeps_public_fields(self):
        out = cs.sanitize_message_for_public(self._msg(
            event_type="video_edit_tool_result",
            event_data={
                "run_id": "r", "thread_id": "t", "message_key": "tool.video.regenerate",
                "result": {"shot_numbers": [1]},
                # 未在白名单里的字段
                "raw_prompt": "system prompt leak",
            },
        ))
        ed = out["event_data"]
        for k in ("run_id", "thread_id", "message_key", "result"):
            assert k in ed
        assert "raw_prompt" not in ed

    def test_event_data_forbidden_fields_always_dropped(self):
        """即使某字段名被误加到白名单，显式黑名单也必须拦住。"""
        out = cs.sanitize_message_for_public(self._msg(
            event_type="error",
            event_data={
                "run_id": "r",
                "error_message": "ConnectionError at /srv/app/main.py:88",
                "traceback": "Traceback...",
                "user_id": "u-1",
                "tool": "regenerate_keyframes",
                "args": {"shot_numbers": [3]},
                "llm_prompt": "<persona>...",
            },
        ))
        ed = out["event_data"]
        assert ed == {"run_id": "r"}
        for bad in ("error_message", "traceback", "user_id", "tool", "args", "llm_prompt"):
            assert bad not in ed

    def test_error_event_preserved_but_masked(self):
        """关键回归：ERROR 事件不应被整条丢弃（前端需要感知出错），但 event_data 里的敏感字段必须剥掉。"""
        out = cs.sanitize_message_for_public(self._msg(
            event_type="error",
            content="something went wrong",
            event_data={"error_message": "internal stack"},
        ))
        assert out is not None
        assert out["event_type"] == "error"
        assert out["content"] == "something went wrong"
        assert out.get("event_data") == {}

    def test_missing_event_data(self):
        out = cs.sanitize_message_for_public(self._msg(event_data=None))
        assert out is not None
        assert "event_data" not in out

    # --- 存量 content 中 tool 名被剥 ---

    def test_legacy_tool_call_content_stripped(self):
        out = cs.sanitize_message_for_public(self._msg(
            event_type="video_edit_tool_call",
            content="🔧 调用工具: regenerate_keyframes",
            event_data={"run_id": "r"},
        ))
        assert "regenerate_keyframes" not in out["content"]
        assert out["content"] == "🔧 调用工具..."

    def test_legacy_tool_call_content_with_other_tool_stripped(self):
        for tool in ("get_project_status", "reassemble_video", "modify_outline"):
            out = cs.sanitize_message_for_public(self._msg(
                event_type="video_edit_tool_call",
                content=f"🔧 调用工具: {tool}",
                event_data={"run_id": "r"},
            ))
            assert tool not in out["content"], tool

    def test_legacy_tool_result_content_stripped(self):
        out = cs.sanitize_message_for_public(self._msg(
            event_type="video_edit_tool_result",
            content=(
                "📋 regenerate_keyframes 结果:\n"
                "已创建 3 个重生成任务，job_ids=[a-1, a-2, a-3]"
            ),
            event_data={"run_id": "r"},
        ))
        assert "regenerate_keyframes" not in out["content"]
        assert "job_ids" not in out["content"]
        assert out["content"] == "📋 工具已执行"

    def test_new_shape_content_already_clean_preserved(self):
        """新数据（flag on 后写入）content 已是平淡占位符，不应被再次改写成相同值前做额外处理。"""
        out = cs.sanitize_message_for_public(self._msg(
            event_type="video_edit_tool_call",
            content="🔧 调用工具...",
            event_data={"run_id": "r", "message_key": "tool.keyframe.regenerate"},
        ))
        assert out["content"] == "🔧 调用工具..."
        assert out["event_data"] == {
            "run_id": "r", "message_key": "tool.keyframe.regenerate",
        }

    def test_non_companion_content_never_touched(self):
        """回归保护：其它事件的 content 不应被改写。"""
        original = "故事大纲已生成：regenerate_keyframes 可用来重生成关键帧。"
        out = cs.sanitize_message_for_public(self._msg(
            event_type="story_outline_generated",
            content=original,
            event_data={"run_id": "r"},
        ))
        assert out["content"] == original
