"""SelectVersionTool — 选中角色/关键帧/视频的特定版本。

支持两种指定方式：
- version_uuid：真实版本 UUID（精确）。
- version_selector：语义选择器 first | latest | v<N>，由**后端**解析成真实 UUID 再写入。
  （历史 bug：LLM 曾把字面量 "latest" 直接写进 selected_version_id，导致假切换；
   现在语义一律在后端解析，绝不把非 UUID 落库。）
"""
import logging
import re
from typing import List, Optional, Tuple, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

VALID_ENTITY_TYPES = ("character", "keyframe", "video")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_FIRST_KW = ("first", "1st", "oldest", "初版", "初", "第一", "最早", "最初", "原始", "原版")
_LATEST_KW = ("latest", "newest", "last", "最新", "最后", "刚生成", "刚出", "刚做", "新版")
_PREV_KW = ("prev", "previous", "上一", "前一", "上个", "上版", "退回", "回退", "旧版")
_NEXT_KW = ("next", "下一", "后一", "下个", "下版")

_CN_NUM = {
    "零": 0, "〇": 0, "一": 1, "壹": 1, "两": 2, "二": 2, "贰": 2, "三": 3, "叁": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _cn_to_int(token: str):
    """把 '3' / '三' / '十二' / '二十一' 之类解析成 int，失败返回 None。"""
    t = token.strip()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    if "十" in t:
        left, _, right = t.partition("十")
        tens = _CN_NUM.get(left, 1) if left else 1
        ones = _CN_NUM.get(right, 0) if right else 0
        if left and left not in _CN_NUM:
            return None
        if right and right not in _CN_NUM:
            return None
        return tens * 10 + ones
    if t in _CN_NUM:
        return _CN_NUM[t]
    return None


_ORD_RE = re.compile(r"第\s*([0-9零〇一壹两二贰三叁四五六七八九十]+)")
_REV_ORD_RE = re.compile(r"倒数第?\s*([0-9零〇一壹两二贰三叁四五六七八九十]+)")
_NUM_RE = re.compile(r"^v?\s*(\d+)$")


class SelectVersionInput(BaseModel):
    """select_version 的输入参数。"""
    entity_type: str = Field(
        ..., description="实体类型: character | keyframe | video",
    )
    entity_uuid: str = Field(
        ..., description="实体 UUID（角色/关键帧/视频的主记录 UUID）",
    )
    version_uuid: Optional[str] = Field(
        None,
        description=(
            "精确指定：版本记录的真实 UUID（如 get_artifact_detail 返回的 version_uuid）。"
            "若你不知道确切 UUID，改用 version_selector，不要在这里填 latest/first/v2 等别名。"
        ),
    )
    version_selector: Optional[str] = Field(
        None,
        description=(
            "语义选择（后端解析成真实 UUID，你**不需要**先查 UUID）。可直接用自然语言/序号，例如："
            "first/最早/第一个 | latest/最新/刚生成 | 第二个/第三个/v2/v3（第几个都行）| "
            "倒数第二 | 上一版/旧版 | 下一版。"
            "把用户原话里的版本指代直接填进来即可，不用硬套固定词。"
            "version_uuid 与 version_selector 至少提供一个；都给时以 version_uuid 为准。"
        ),
    )


class SelectVersionTool(BaseTool):
    name: str = "select_version"
    description: str = (
        "选中角色/关键帧/视频的某个版本作为当前使用版本。"
        "entity_type: character | keyframe | video。"
        "指定版本二选一：version_uuid（真实 UUID）或 version_selector（自然语言/序号，后端解析）。"
        "version_selector 支持：first/最早、latest/最新、第二个/第三个/v2/v3、倒数第二、上一版、下一版——"
        "把用户原话里的版本指代直接填进去即可，无需自己查 UUID。"
        "「所有角色」→ 先用 get_artifact_detail(character) 列出全部角色 uuid，"
        "再对每个 uuid 各调一次本工具（version_selector 相同）。"
    )
    args_schema: Type[BaseModel] = SelectVersionInput

    run_id: str = ""
    user_id: str = ""

    async def _arun(
        self,
        entity_type: str,
        entity_uuid: str,
        version_uuid: Optional[str] = None,
        version_selector: Optional[str] = None,
    ) -> str:
        if entity_type not in VALID_ENTITY_TYPES:
            return f"无效的 entity_type: {entity_type}。可选: {', '.join(VALID_ENTITY_TYPES)}"

        entity_uuid = (entity_uuid or "").strip()
        version_uuid = (version_uuid or "").strip() or None
        version_selector = (version_selector or "").strip() or None

        if not _UUID_RE.match(entity_uuid):
            return "❌ entity_uuid 必须是真实 UUID（可先用 get_artifact_detail 列表获取）。"

        # LLM 常把别名塞进 version_uuid；识别并转到 selector 逻辑，而不是落库
        if version_uuid and not _UUID_RE.match(version_uuid):
            version_selector = version_selector or version_uuid
            version_uuid = None

        if not version_uuid and not version_selector:
            return "❌ 请提供 version_uuid（真实 UUID）或 version_selector（first/latest/v<N>）。"

        # 取该实体全部版本 (version_number, uuid)，升序
        versions = await self._list_versions(entity_type, entity_uuid)
        if not versions:
            return f"❌ 未找到 {entity_type} {entity_uuid[-8:]} 的版本记录。"

        # 解析出目标真实 uuid
        if version_uuid:
            target = next((v for v in versions if v[1] == version_uuid), None)
            if target is None:
                return f"❌ 版本 {version_uuid[-8:]} 不属于该 {entity_type}。"
        else:
            current_idx = None
            if any(k in version_selector.lower() for k in _PREV_KW + _NEXT_KW):
                current_idx = await self._current_index(entity_type, entity_uuid, versions)
            target = self._resolve_selector(version_selector, versions, current_idx)
            if target is None:
                avail = ", ".join(f"v{n}" for n, _ in versions)
                return (
                    f"❌ 无法解析 version_selector={version_selector!r}。"
                    f"该 {entity_type} 现有版本：{avail}。可用 first/latest/第N个/vN/倒数第N/上一版/下一版。"
                )

        target_no, target_uuid = target
        if entity_type == "character":
            return await self._select_character_version(entity_uuid, target_uuid, target_no)
        if entity_type == "keyframe":
            return await self._select_keyframe_version(entity_uuid, target_uuid, versions)
        return await self._select_video_version(entity_uuid, target_uuid, versions)

    # ---- 版本列举 / 解析 ----
    async def _list_versions(
        self, entity_type: str, entity_uuid: str
    ) -> List[Tuple[int, str]]:
        if entity_type == "character":
            from .....crud.video.video_character import (
                get_character_versions_by_character_id,
            )
            vers = await get_character_versions_by_character_id(entity_uuid, self.user_id)
            pairs = [
                (int(getattr(v, "version_number", 0) or 0), str(getattr(v, "uuid", "")))
                for v in vers
            ]
        elif entity_type == "keyframe":
            from .....crud.video.video_keyframe import (
                get_keyframe_versions_by_keyframe_ids,
            )
            vers = await get_keyframe_versions_by_keyframe_ids([entity_uuid])
            pairs = [
                (int(getattr(v, "version_number", 0) or 0), str(getattr(v, "uuid", "")))
                for v in vers
                if getattr(v, "keyframe_id", None) == entity_uuid
            ]
        else:  # video
            from .....crud.video.video_generation import (
                get_video_generation_versions_by_video_generation_ids,
            )
            vers = await get_video_generation_versions_by_video_generation_ids([entity_uuid])
            pairs = [
                (int(getattr(v, "version_number", 0) or 0), str(getattr(v, "uuid", "")))
                for v in vers
            ]
        pairs = [p for p in pairs if _UUID_RE.match(p[1])]
        pairs.sort(key=lambda x: x[0])
        return pairs

    async def _current_index(
        self, entity_type: str, entity_uuid: str, versions: List[Tuple[int, str]]
    ) -> Optional[int]:
        """当前选用版本在 versions（升序）里的位置，供「上一版/下一版」相对定位。"""
        try:
            if entity_type == "character":
                from .....crud.video.video_character import get_character_by_uuid
                char = await get_character_by_uuid(entity_uuid)
                sel = getattr(char, "selected_version_id", None)
                if sel:
                    for i, (_, vid) in enumerate(versions):
                        if vid == sel:
                            return i
                idx = getattr(char, "current_version_index", None)
                return int(idx) if idx is not None else None
            if entity_type == "keyframe":
                from .....crud.video.video_keyframe import get_keyframe_by_uuid
                kf = await get_keyframe_by_uuid(entity_uuid)
                idx = getattr(kf, "current_version_index", None)
                return int(idx) if idx is not None else None
            from .....crud.video.video_generation import get_video_generation_by_uuid
            vg = await get_video_generation_by_uuid(entity_uuid)
            idx = getattr(vg, "current_version_index", None)
            return int(idx) if idx is not None else None
        except Exception:
            return None

    @staticmethod
    def _resolve_selector(
        selector: str,
        versions: List[Tuple[int, str]],
        current_idx: Optional[int] = None,
    ) -> Optional[Tuple[int, str]]:
        """把自然语言/序号选择器解析成 (version_number, uuid)。versions 已按版本号升序。"""
        s = selector.strip().lower()
        n_ver = len(versions)

        # 倒数第 N（先于「第 N」匹配，避免被吞）
        m = _REV_ORD_RE.search(s)
        if m:
            n = _cn_to_int(m.group(1))
            if n and 1 <= n <= n_ver:
                return versions[-n]
            return None

        # first / latest（先判，避免「最后一个」被相对词误吞）
        if any(k in s for k in _FIRST_KW):
            return versions[0]
        if any(k in s for k in _LATEST_KW):
            return versions[-1]

        # 相对当前：上一版 / 下一版
        if any(k in s for k in _PREV_KW):
            if current_idx is not None and current_idx - 1 >= 0:
                return versions[current_idx - 1]
            # 无法定位当前时，「旧版」退而取第一版
            if any(k in s for k in ("旧版", "退回", "回退")):
                return versions[0]
            return None
        if any(k in s for k in _NEXT_KW):
            if current_idx is not None and current_idx + 1 < n_ver:
                return versions[current_idx + 1]
            return None

        # 第 N 个 / 第 N 版（按列表位置）
        m = _ORD_RE.search(s)
        if m:
            n = _cn_to_int(m.group(1))
            if n and 1 <= n <= n_ver:
                return versions[n - 1]
            return None

        # vN / 纯数字：先按版本号精确匹配，退而按位置
        m = _NUM_RE.match(s)
        if m:
            n = int(m.group(1))
            exact = next((v for v in versions if v[0] == n), None)
            if exact:
                return exact
            if 1 <= n <= n_ver:
                return versions[n - 1]
        return None

    # ---- 写入（按类型）----
    async def _select_character_version(
        self, entity_uuid: str, version_uuid: str, ver_no: int
    ) -> str:
        from .....crud.video.video_character import (
            get_character_by_uuid,
            get_character_version_by_uuid,
            update_character,
            update_character_selected_version,
        )

        char = await get_character_by_uuid(entity_uuid)
        name = getattr(char, "name", None) or entity_uuid[-8:]

        success = await update_character_selected_version(entity_uuid, version_uuid)
        if not success:
            return f"❌ 切换角色版本失败（UUID 不存在或不匹配）"

        version = await get_character_version_by_uuid(version_uuid)
        img = getattr(version, "character_image_url", None) if version else None
        patch = {"current_version_index": max(int(ver_no) - 1, 0)}
        if img:
            patch["image_url"] = img
        await update_character(entity_uuid, patch)

        logger.info(
            "select_version character=%s version=%s v%s", entity_uuid, version_uuid, ver_no
        )
        return f"✅ 角色 {name} 已切换到版本 v{ver_no} ({version_uuid[-8:]})"

    async def _select_keyframe_version(
        self, entity_uuid: str, version_uuid: str, versions: List[Tuple[int, str]]
    ) -> str:
        from .....crud.video.video_keyframe import (
            update_keyframe_current_version_index,
        )
        version_index = next(
            (i for i, (_, vid) in enumerate(versions) if vid == version_uuid), None
        )
        if version_index is None:
            return f"❌ 版本 {version_uuid[-8:]} 不属于该关键帧"
        success = await update_keyframe_current_version_index(entity_uuid, version_index)
        if success:
            return f"✅ 关键帧 {entity_uuid[-8:]} 已切换到版本 v{version_index} ({version_uuid[-8:]})"
        return f"❌ 切换关键帧版本失败"

    async def _select_video_version(
        self, entity_uuid: str, version_uuid: str, versions: List[Tuple[int, str]]
    ) -> str:
        from .....crud.video.video_generation import (
            batch_update_video_generation_current_version_index,
        )
        version_index = next(
            (i for i, (_, vid) in enumerate(versions) if vid == version_uuid), None
        )
        if version_index is None:
            return f"❌ 版本 {version_uuid[-8:]} 不属于该视频"
        success = await batch_update_video_generation_current_version_index([
            {"uuid": entity_uuid, "current_version_index": version_index}
        ])
        if success:
            return f"✅ 视频 {entity_uuid[-8:]} 已切换到版本 v{version_index} ({version_uuid[-8:]})"
        return f"❌ 切换视频版本失败"

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")
