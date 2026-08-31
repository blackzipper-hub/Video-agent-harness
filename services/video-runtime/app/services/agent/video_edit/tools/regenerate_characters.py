"""RegenerateCharactersTool — 重新生成角色形象。"""
import logging
import uuid
from typing import List, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class RegenerateCharactersInput(BaseModel):
    """regenerate_characters 的输入参数。"""
    character_uuids: List[str] = Field(
        ..., description="要重新生成的角色 UUID 列表",
    )
    instruction: str = Field(
        "", description="修改指令，如 '改为更成熟的形象' '换一种发型' '穿绿色裙子'",
    )
    mode: str = Field(
        "instruction",
        description=(
            "prompt 模式。"
            "instruction: 基于当前版本的完整 prompt 叠加用户修改指令，由系统生成新的完整 prompt（默认，推荐用于自然语言修改请求）。"
            "direct_prompt: 将 instruction 直接作为完整 t2i_prompt 使用（仅当用户明确给出了完整的图像生成 prompt 时使用）。"
        ),
    )
    regenerate_strategy: Optional[str] = Field(
        default=None,
        description=(
            "省略时默认 prompt_regenerate（instruction/direct_prompt 走完整出图；参考图仅用 reference_image_urls，"
            "为空时多为纯文生图，不等价于「基于当前成图」）。"
            "用户说基于原图/在当前图上改/保留构图只改颜色材质等 → 必须显式传 instruction_edit_image（用当前版本 character_image_url 做 I2I）。"
            "显式取值：prompt_regenerate | instruction_merge_prompt（只融 t2i 并 API 返回，不写新版本行）| instruction_edit_image"
        ),
    )


class RegenerateCharactersTool(BaseTool):
    name: str = "regenerate_characters"
    description: str = (
        "重新生成指定角色（视觉参考图）。"
        "用户说「所有角色」= get_artifact_detail(artifact_type=character) 返回列表中的全部 uuid。"
        "多个角色时一次传入完整 character_uuids 列表。"
        "instruction 写修改需求；mode 默认 instruction，或 direct_prompt（整段替换）。"
        "基于当前成图修改须传 regenerate_strategy=instruction_edit_image。"
    )
    args_schema: Type[BaseModel] = RegenerateCharactersInput

    run_id: str = ""
    user_id: str = ""
    thread_id: str = ""

    async def _arun(
        self,
        character_uuids: List[str],
        instruction: str = "",
        mode: str = "instruction",
        regenerate_strategy: Optional[str] = None,
    ) -> str:
        from .....api.agent.agent_router_endpoints import (
            CharacterRequest, CharacterVersionRequest,
        )
        from .....crud.video.video_character import get_character_by_uuid
        from .....services.task_enqueue_service import execute_regenerate_characters

        _rs_raw = (regenerate_strategy or "").strip()
        from .....models.version_regenerate_strategy import CharacterRegenerateStrategy

        _rs_eff = (
            _rs_raw
            if _rs_raw
            else CharacterRegenerateStrategy.PROMPT_REGENERATE.value
        )
        character_requests = []
        for cuuid in character_uuids:
            char_db = await get_character_by_uuid(cuuid)
            if not char_db:
                return f"角色 {cuuid} 不存在。"
            selected_vid = getattr(char_db, 'selected_version_id', None) or ""
            if not selected_vid:
                return f"角色 {cuuid} 没有可用的版本记录。"

            if mode == "direct_prompt":
                ver = CharacterVersionRequest(
                    uuid=selected_vid,
                    custom_prompt=instruction or None,
                    instruction=None,
                    regenerate_strategy=_rs_eff,
                )
            else:
                ver = CharacterVersionRequest(
                    uuid=selected_vid,
                    custom_prompt=None,
                    instruction=instruction or None,
                    regenerate_strategy=_rs_eff,
                )

            character_requests.append(
                CharacterRequest(
                    uuid=cuuid,
                    versions=[ver],
                )
            )

        new_run_id = str(uuid.uuid4())
        result = await execute_regenerate_characters(
            thread_id=self.thread_id,
            run_id=new_run_id,
            user_id=self.user_id,
            characters=character_requests,
            user_option=None,
            regenerate_source="chat",
        )

        succeeded = result.get("succeeded", 0) if isinstance(result, dict) else 0
        failed = result.get("failed", 0) if isinstance(result, dict) else 0

        return (
            f"✅ 已提交 {len(character_uuids)} 个角色重新生成任务\n"
            f"  模式: {mode}\n"
            f"  指令: {instruction or '(无)'}\n"
            f"  结果: 成功 {succeeded}, 失败 {failed}"
        )

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")
