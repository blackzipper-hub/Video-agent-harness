"""ModifyOutlineTool — 通过自然语言修改故事大纲（LLM 选逻辑字段，style 组后端展开）。"""
import logging
from typing import List, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from ..modify_outline_service import (
    VALID_OUTLINE_FIELDS,
    apply_modify_outline,
    format_modify_outline_success,
    should_sync_chapters,
)

logger = logging.getLogger(__name__)


class ModifyOutlineInput(BaseModel):
    """modify_outline 的输入参数。"""
    instruction: str = Field(
        ...,
        description=(
            "修改说明（不是完整新全文）。会在各 fields 对应字段的**当前文本上融合**你的说明："
            "保留仍成立的内容，替换/删除与修改意图冲突的旧表述；禁止只在句尾追加几个词。"
        ),
    )
    fields: List[str] = Field(
        ...,
        description=(
            "要修改的逻辑字段（必填，可多选）：title | description | theme | key_message | style | chapters。"
            "【原则·涉及就要改】由你判断本轮牵动了哪些层再填 fields，禁止半改："
            "整风格切换（改成XX风格/换画风）几乎总会牵动 description，且常牵动 theme/title/key_message"
            "→ 典型 [\"style\",\"description\",\"theme\",\"title\"]（key_message 被牵动则加上）；"
            "仅视觉微调（色调冷一点）且标题/主题/描述仍成立时可只 [\"style\"]。"
            "禁止「改风格」却只传 [\"description\"]。"
            "含 style/description/theme 时后端自动同步各章节；场景冲突用 update_scene，不要写进本 fields。"
            "示例：改成末日风格 → [\"style\",\"description\",\"theme\",\"title\"]；色调冷一点 → [\"style\"]；只改标题 → [\"title\"]。"
        ),
    )


class ModifyOutlineTool(BaseTool):
    name: str = "modify_outline"
    description: str = (
        "修改故事大纲的全局文本：title/description/theme/key_message/style，以及章节(chapters)。"
        "instruction 是在原文上融合修改说明，不是整段替换。"
        "fields 按「涉及就要改」由你选择：整风格切换通常含 style+description+theme+title"
        "（key_message 被牵动则加上）；微调视觉且语义不变时可只 style。"
        "含 style/description/theme 时后端自动同步各章节。"
        "不自动改场景/镜头脚本/角色/关键帧/视频（场景冲突用 update_scene）。"
        f"可选 fields: {', '.join(sorted(VALID_OUTLINE_FIELDS))}。"
    )
    args_schema: Type[BaseModel] = ModifyOutlineInput

    run_id: str = ""
    user_id: str = ""
    thread_id: str = ""

    async def _arun(
        self,
        instruction: str,
        fields: List[str],
    ) -> str:
        from .....crud.video.video_other import (
            get_video_analysis_by_thread_id,
            get_video_analysis_by_uuid,
            update_video_analysis,
        )
        from .....crud.video.video_story import (
            get_chapters_by_story_outline_id,
            get_video_story_outline_by_run_id,
            get_video_story_outline_by_thread_id,
            update_chapter,
            update_video_story_outline,
        )

        outline = (
            await get_video_story_outline_by_thread_id(self.thread_id)
            if self.thread_id
            else await get_video_story_outline_by_run_id(self.run_id)
        )
        if not outline:
            return "❌ 大纲尚未生成。"

        analysis = None
        analysis_id = getattr(outline, "analysis_id", None)
        if analysis_id:
            analysis = await get_video_analysis_by_uuid(analysis_id)
        elif self.thread_id:
            analysis = await get_video_analysis_by_thread_id(self.thread_id)

        chapters = None
        if should_sync_chapters(fields):
            chapters = await get_chapters_by_story_outline_id(outline.uuid)

        result = await apply_modify_outline(
            outline=outline,
            instruction=instruction,
            fields=fields,
            analysis=analysis,
            chapters=chapters,
        )
        logger.info(
            "modify_outline: thread_id=%s fields=%s changed=%s chapters=%s success=%s",
            self.thread_id,
            fields,
            result.fields_changed if result.success else None,
            len(result.chapter_updates) if result.success else 0,
            result.success,
        )
        if not result.success:
            return result.message or "❌ 大纲更新失败"

        if not result.outline_updates and not result.analysis_updates and not result.chapter_updates:
            return "❌ 没有可写入的修改内容。"

        if result.outline_updates:
            ok = await update_video_story_outline(outline.uuid, result.outline_updates)
            if not ok:
                return "❌ 大纲更新失败"

        if result.analysis_updates and analysis is not None:
            ok = await update_video_analysis(analysis.uuid, result.analysis_updates)
            if not ok:
                return "❌ 风格标签更新失败"

        for ch in result.chapter_updates:
            payload = {}
            if ch.description:
                payload["description"] = ch.description
            if ch.title:
                payload["title"] = ch.title
            if payload:
                ok = await update_chapter(ch.uuid, payload)
                if not ok:
                    return f"❌ 章节 {ch.order + 1} 更新失败"

        return format_modify_outline_success(result, outline.uuid)

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")
