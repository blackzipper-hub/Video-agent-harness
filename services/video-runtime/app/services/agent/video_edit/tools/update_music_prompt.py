"""UpdateMusicPromptTool — 通过自然语言修改音乐生成版本的提示词。"""
import logging
from typing import Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from ...video.regenerate.instruction_merge_prompt import instruction_merge_to_full_prompt

logger = logging.getLogger(__name__)


class UpdateMusicPromptInput(BaseModel):
    """update_music_prompt 的输入参数。"""
    instruction: str = Field(
        ..., description="修改指令，如 '节奏快一点' '换成钢琴主导的纯音乐'",
    )
    mode: str = Field(
        "instruction",
        description=(
            "文本模式。"
            "instruction: 在当前音乐提示词基础上叠加修改指令，由 LLM 融合出新提示词（默认，推荐用于自然语言修改请求）。"
            "direct: 将 instruction 直接作为完整新音乐提示词（仅当用户明确给了完整新提示词时使用）。"
        ),
    )
    music_generation_uuid: Optional[str] = Field(
        None, description="音乐生成 UUID（不提供则使用第一条配乐）",
    )
    version_uuid: Optional[str] = Field(
        None, description="要更新的版本 UUID（不提供则使用当前版本）",
    )


class UpdateMusicPromptTool(BaseTool):
    name: str = "update_music_prompt"
    description: str = (
        "通过自然语言修改音乐生成版本的提示词。可以指定具体的音乐和版本 UUID，"
        "或不指定使用默认的第一条配乐当前版本。"
        "instruction 写修改需求；mode 默认 instruction（在当前提示词上融合修改指令），或 direct（整段替换）。"
    )
    args_schema: Type[BaseModel] = UpdateMusicPromptInput

    run_id: str = ""
    user_id: str = ""
    thread_id: str = ""

    async def _arun(
        self,
        instruction: str,
        mode: str = "instruction",
        music_generation_uuid: Optional[str] = None,
        version_uuid: Optional[str] = None,
    ) -> str:
        from .....crud.video.video_audio import (
            get_music_generations_by_run_id,
            get_music_generations_by_thread_id,
            get_music_generation_versions,
            update_music_generation_version_prompt,
        )

        instruction = instruction.strip()
        if not instruction:
            return "❌ 修改指令不能为空。"

        if not music_generation_uuid:
            mgs = await get_music_generations_by_thread_id(self.thread_id) if self.thread_id else await get_music_generations_by_run_id(self.run_id)
            if not mgs:
                return "❌ 当前项目没有配乐。"
            music_generation_uuid = mgs[0].uuid

        versions = await get_music_generation_versions(music_generation_uuid)
        if not versions:
            return f"❌ 配乐 {music_generation_uuid[-8:]} 没有版本。"

        # 定位目标版本（用于读出当前提示词做基底）
        if version_uuid:
            sel = next((v for v in versions if getattr(v, "uuid", "") == version_uuid), None)
            if sel is None:
                return f"❌ 未找到版本 {version_uuid[-8:]}。"
        else:
            sel = versions[-1]
            version_uuid = sel.uuid

        base_value = (getattr(sel, "music_prompt", "") or "").strip()
        is_direct = mode in ("direct", "direct_prompt")
        if is_direct or not base_value:
            # 整段替换；或当前无提示词可作基底时，直接使用 instruction
            new_prompt = instruction
        else:
            # 读出当前提示词做基底，把 instruction 当"修改说明"，由 LLM 融合出新提示词，
            # 而不是把用户指令原文直接覆盖整段提示词。
            new_prompt = await instruction_merge_to_full_prompt(
                base_prompt=base_value,
                instruction=instruction,
                asset_kind="音乐提示词",
            )
        new_prompt = (new_prompt or "").strip()
        if not new_prompt:
            return "❌ 生成的新音乐提示词为空，未修改。"

        success = await update_music_generation_version_prompt(version_uuid, new_prompt)
        if success:
            return (
                f"✅ 音乐提示词已更新\n"
                f"  配乐: {music_generation_uuid[-8:]}\n"
                f"  版本: {version_uuid[-8:]}\n"
                f"  模式: {mode}\n"
                f"  新提示词: {new_prompt[:100]}{'...' if len(new_prompt) > 100 else ''}"
            )
        return f"❌ 更新失败（版本不存在: {version_uuid}）"

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")
