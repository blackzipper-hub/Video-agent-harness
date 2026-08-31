"""modify_outline 字段规范化与 style 组展开单测。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.agent.video_edit.modify_outline_service import (
    apply_modify_outline,
    normalize_outline_fields,
    should_sync_chapters,
    _normalize_chapter_title,
)


class TestNormalizeOutlineFields:
    def test_valid_fields_deduped(self):
        assert normalize_outline_fields(["style", "title", "style"]) == ["style", "title"]

    def test_should_sync_chapters(self):
        assert should_sync_chapters(["style"]) is True
        assert should_sync_chapters(["description"]) is True
        assert should_sync_chapters(["theme"]) is True
        assert should_sync_chapters(["chapters"]) is True
        assert should_sync_chapters(["title"]) is False
        assert should_sync_chapters(["key_message"]) is False

    def test_normalize_chapter_title_rejects_long_output(self):
        long_title = "末日序章：坚韧与希望的青春在暗淡沉重的校园废墟中一群学生奔跑跳跃"
        assert len(long_title) > 30
        assert _normalize_chapter_title(long_title, fallback="原标题") == "原标题"

    def test_normalize_chapter_title_accepts_short_title(self):
        assert _normalize_chapter_title("末日序章：坚韧希望", fallback="原标题") == "末日序章：坚韧希望"

    def test_invalid_fields_ignored(self):
        assert normalize_outline_fields(["style_guide", "themes", "narrative_structure"]) == []

    def test_chapters_field_valid(self):
        assert normalize_outline_fields(["chapters", "style"]) == ["chapters", "style"]

    def test_empty(self):
        assert normalize_outline_fields(None) == []
        assert normalize_outline_fields([]) == []


class TestApplyModifyOutline:
    @pytest.mark.asyncio
    async def test_requires_fields(self):
        outline = SimpleNamespace(
            uuid="o1", title="t", description="d", theme="th", key_message="k",
            style_guide="guide", analysis_id=None,
        )
        result = await apply_modify_outline(outline=outline, instruction="改一下", fields=[])
        assert not result.success
        assert "fields" in result.message

    @pytest.mark.asyncio
    async def test_title_only(self):
        outline = SimpleNamespace(
            uuid="o1", title="旧标题", description="描述", theme="主题",
            key_message="信息", style_guide="真实摄影", analysis_id=None,
        )

        with patch(
            "app.services.agent.video_edit.modify_outline_service.instruction_merge_to_full_prompt",
            new_callable=AsyncMock,
            return_value="新标题",
        ):
            result = await apply_modify_outline(
                outline=outline, instruction="快乐测试", fields=["title"],
            )

        assert result.success
        assert result.fields_changed == ["title"]
        assert result.outline_updates == {"title": "新标题"}

    @pytest.mark.asyncio
    async def test_style_expands_to_guide_and_preferences(self):
        outline = SimpleNamespace(
            uuid="o1", title="t", description="青春校园", theme="th",
            key_message="k", style_guide="真实摄影，微电影", analysis_id="a1",
        )
        analysis = SimpleNamespace(uuid="a1", style_preferences='["真实摄影","微电影风格"]')

        async def fake_merge(*, base_prompt, instruction, asset_kind, detected_language=None):
            if asset_kind == "视觉风格指南":
                return "恐怖风格指南"
            if asset_kind == "风格偏好短标签":
                return "恐怖, 悬疑, 阴暗"
            return base_prompt

        with patch(
            "app.services.agent.video_edit.modify_outline_service.instruction_merge_to_full_prompt",
            new_callable=AsyncMock,
            side_effect=fake_merge,
        ):
            result = await apply_modify_outline(
                outline=outline,
                instruction="改成恐怖风格",
                fields=["style"],
                analysis=analysis,
            )

        # LLM 只传 style 时，后端忠实执行，不硬塞 description
        assert result.fields_changed == ["style"]
        assert result.outline_updates == {"style_guide": "恐怖风格指南"}
        assert result.analysis_updates == {"style_preferences": ["恐怖", "悬疑", "阴暗"]}

    @pytest.mark.asyncio
    async def test_style_and_description_both_when_llm_selects(self):
        outline = SimpleNamespace(
            uuid="o1", title="t", description="未来科技校园", theme="th",
            key_message="k", style_guide="真实摄影", analysis_id="a1",
        )
        analysis = SimpleNamespace(uuid="a1", style_preferences='["真实摄影"]')

        async def fake_merge(*, base_prompt, instruction, asset_kind, detected_language=None):
            if asset_kind == "视觉风格指南":
                return "恐怖风格"
            if asset_kind == "风格偏好短标签":
                return "恐怖, 悬疑"
            if asset_kind == "故事大纲整体描述":
                return "恐怖校园描述"
            return base_prompt

        with patch(
            "app.services.agent.video_edit.modify_outline_service.instruction_merge_to_full_prompt",
            new_callable=AsyncMock,
            side_effect=fake_merge,
        ):
            result = await apply_modify_outline(
                outline=outline,
                instruction="改成恐怖风格",
                fields=["style", "description"],
                analysis=analysis,
            )

        assert result.fields_changed == ["style", "description"]
        assert "style_guide" in result.outline_updates
        assert result.outline_updates["description"] == "恐怖校园描述"

    @pytest.mark.asyncio
    async def test_rejects_style_instruction_without_style_field(self):
        outline = SimpleNamespace(
            uuid="o1", title="t", description="d", theme="th", key_message="k",
            style_guide="真实摄影", analysis_id=None,
        )
        result = await apply_modify_outline(
            outline=outline,
            instruction="故事改成灾难风格",
            fields=["description"],
        )
        assert not result.success
        assert "style" in result.message

    @pytest.mark.asyncio
    async def test_style_only_does_not_touch_description(self):
        outline = SimpleNamespace(
            uuid="o1", title="t", description="原描述不变", theme="th",
            key_message="k", style_guide="真实摄影", analysis_id="a1",
        )
        analysis = SimpleNamespace(uuid="a1", style_preferences='["真实摄影"]')

        async def fake_merge(*, base_prompt, instruction, asset_kind, detected_language=None):
            if asset_kind == "视觉风格指南":
                return "冷色调摄影"
            if asset_kind == "风格偏好短标签":
                return "冷色调, 摄影"
            raise AssertionError(f"unexpected merge: {asset_kind}")

        with patch(
            "app.services.agent.video_edit.modify_outline_service.instruction_merge_to_full_prompt",
            new_callable=AsyncMock,
            side_effect=fake_merge,
        ):
            result = await apply_modify_outline(
                outline=outline,
                instruction="色调冷一点",
                fields=["style"],
                analysis=analysis,
            )

        assert result.fields_changed == ["style"]
        assert "description" not in result.outline_updates

    @pytest.mark.asyncio
    async def test_style_syncs_chapters(self):
        outline = SimpleNamespace(
            uuid="o1", title="t", description="青春校园", theme="奋斗",
            key_message="k", style_guide="真实摄影，微电影", analysis_id="a1",
        )
        analysis = SimpleNamespace(uuid="a1", style_preferences='["真实摄影"]')
        chapters = [
            SimpleNamespace(uuid="c1", order=0, title="序章", description="明媚校园阳光"),
            SimpleNamespace(uuid="c2", order=1, title="回响", description="夕阳草坪"),
        ]

        async def fake_merge(*, base_prompt, instruction, asset_kind, detected_language=None):
            if asset_kind == "视觉风格指南":
                return "末日风格指南"
            if asset_kind == "风格偏好短标签":
                return "末日, 废墟"
            if asset_kind == "故事章节描述":
                return f"末日版-{base_prompt[:6]}"
            if asset_kind == "故事章节标题":
                return "末日序章"
            return base_prompt

        with patch(
            "app.services.agent.video_edit.modify_outline_service.instruction_merge_to_full_prompt",
            new_callable=AsyncMock,
            side_effect=fake_merge,
        ):
            result = await apply_modify_outline(
                outline=outline,
                instruction="改成末日风格",
                fields=["style"],
                analysis=analysis,
                chapters=chapters,
            )

        assert "style" in result.fields_changed
        assert "chapters" in result.fields_changed
        assert "description" not in result.fields_changed
        assert len(result.chapter_updates) == 2
        assert result.chapter_updates[0].description.startswith("末日版-")