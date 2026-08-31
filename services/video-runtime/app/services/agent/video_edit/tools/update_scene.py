"""UpdateSceneTool — 通过自然语言修改某个场景的文本（场景描述）。"""
import logging
from typing import Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from ...video.regenerate.instruction_merge_prompt import instruction_merge_to_full_prompt
from .....crud.video.video_story import (
    get_scenes_by_run_id,
    get_scenes_by_thread_id,
    update_detailed_shots_description_by_scene_id,
    update_scene,
)

logger = logging.getLogger(__name__)


class UpdateSceneInput(BaseModel):
    """update_scene 的输入参数。"""
    scene_number: int = Field(
        ..., description="要修改的场景编号（第几个场景，从 1 开始）",
    )
    instruction: str = Field(
        ..., description="修改指令，如 '改成特写镜头，聚焦女孩的脸' '增加一个转身动作'",
    )
    mode: str = Field(
        "instruction",
        description=(
            "文本模式。"
            "instruction: 在当前场景文本基础上叠加修改指令，由 LLM 融合出新文本（默认，推荐用于自然语言修改请求）。"
            "direct: 将 instruction 直接作为完整新场景文本（仅当用户明确给了完整新文本时使用）。"
        ),
    )


class UpdateSceneTool(BaseTool):
    name: str = "update_scene"
    description: str = (
        "通过自然语言修改某个场景的文本描述（scene description），并同步到该场景下的镜头脚本（供关键帧生成使用）。"
        "用 scene_number 指定第几个场景；instruction 写修改需求。"
        "mode 默认 instruction（在当前场景文本上融合修改指令，替换冲突表述而非句尾打补丁），或 direct（整段替换）。"
        "注意：本工具改的是场景/镜头的文本脚本，不是大纲全局信息（那是 modify_outline），也不直接重画关键帧/视频（那是 regenerate_keyframes / regenerate_videos）。"
    )
    args_schema: Type[BaseModel] = UpdateSceneInput

    run_id: str = ""
    user_id: str = ""
    thread_id: str = ""

    async def _arun(
        self,
        scene_number: int,
        instruction: str,
        mode: str = "instruction",
    ) -> str:
        instruction = (instruction or "").strip()
        if not instruction:
            return "❌ 修改指令不能为空。"

        scenes = await get_scenes_by_thread_id(self.thread_id) if self.thread_id else await get_scenes_by_run_id(self.run_id)
        if not scenes:
            return "❌ 当前项目还没有场景。"

        target = next((s for s in scenes if s.scene_number == scene_number), None)
        if target is None:
            available = ", ".join(str(s.scene_number) for s in scenes)
            return f"❌ 未找到场景 {scene_number}（现有场景: {available}）。"

        base_value = (target.description or "").strip()
        is_direct = mode in ("direct", "direct_prompt")
        if is_direct or not base_value:
            new_text = instruction
        else:
            # 读出当前场景文本做基底，把 instruction 当"修改说明"，由 LLM 融合出新文本，
            # 而不是把用户指令原文直接写进字段（否则会整段覆盖原内容）。
            new_text = await instruction_merge_to_full_prompt(
                base_prompt=base_value,
                instruction=instruction,
                asset_kind="镜头场景文本",
            )
        new_text = (new_text or "").strip()
        if not new_text:
            return "❌ 生成的新场景文本为空，未修改。"

        success = await update_scene(target.uuid, {"description": new_text})
        if not success:
            return "❌ 场景更新失败。"

        # 同步到该场景下所有详细镜头的 scene_description，供后续关键帧生成使用
        synced = await update_detailed_shots_description_by_scene_id(target.uuid, new_text)

        return (
            f"✅ 场景已更新\n"
            f"  场景: 第 {scene_number} 个（{target.title}）\n"
            f"  模式: {mode}\n"
            f"  已同步镜头脚本: {synced} 个\n"
            f"  新文本: {new_text[:100]}{'...' if len(new_text) > 100 else ''}"
        )

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")
