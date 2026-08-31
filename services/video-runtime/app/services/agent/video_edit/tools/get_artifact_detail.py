"""GetArtifactDetailTool — 获取单个产物的详细信息。"""
import logging
from typing import Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

VALID_ARTIFACT_TYPES = (
    "outline", "character", "scene", "shot", "keyframe",
    "video", "narration", "music", "segment", "assembly",
)

MAX_RESULT_CHARS = 4000


class GetArtifactDetailInput(BaseModel):
    """get_artifact_detail 的输入参数。"""
    artifact_type: str = Field(
        ...,
        description=(
            "产物类型: outline | character | scene | shot | keyframe | "
            "video | narration | music | segment | assembly"
        ),
    )
    shot_number: Optional[int] = Field(
        None, description="按镜头编号查询（优先于 uuid）",
    )
    scene_number: Optional[int] = Field(
        None, description="按场景编号查询（仅 artifact_type=scene 时使用，第几个场景就传几）",
    )
    uuid: Optional[str] = Field(
        None, description="按 UUID 查询（shot_number/scene_number 和 uuid 至少提供一个）",
    )


class GetArtifactDetailTool(BaseTool):
    name: str = "get_artifact_detail"
    description: str = (
        "获取产物的详细信息（版本列表、URL、prompt 等）及其关联产物。"
        "character 详情会显示关联的关键帧和场景；keyframe 详情会显示关联的视频；"
        "scene 详情会显示该场景的剧情文本（修改前可先用它查看当前内容）；"
        "不传 shot_number/scene_number/uuid 可获取列表。"
        "artifact_type: outline | character | scene | shot | keyframe | video | narration | music | segment | assembly"
    )
    args_schema: Type[BaseModel] = GetArtifactDetailInput

    run_id: str = ""
    user_id: str = ""
    thread_id: str = ""

    async def _arun(
        self,
        artifact_type: str,
        shot_number: Optional[int] = None,
        scene_number: Optional[int] = None,
        uuid: Optional[str] = None,
    ) -> str:
        if artifact_type not in VALID_ARTIFACT_TYPES:
            return f"无效的 artifact_type: {artifact_type}。可选: {', '.join(VALID_ARTIFACT_TYPES)}"

        if shot_number is None and scene_number is None and uuid is None and artifact_type not in (
            "outline", "assembly", "music", "character", "narration", "video", "keyframe", "scene",
        ):
            return "请提供 shot_number 或 uuid。"

        return await _fetch_detail(
            artifact_type, self.run_id, self.user_id, self.thread_id, shot_number, scene_number, uuid,
        )

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")


async def _fetch_detail(
    artifact_type: str,
    run_id: str,
    user_id: str,
    thread_id: str,
    shot_number: Optional[int],
    scene_number: Optional[int],
    uuid: Optional[str],
) -> str:
    """按类型查 DB 并格式化。优先用 thread_id 查询。返回 ≤4000 字符文本。"""
    if artifact_type == "outline":
        return await _detail_outline(run_id, thread_id)
    elif artifact_type == "character":
        return await _detail_character(run_id, user_id, thread_id, shot_number, uuid)
    elif artifact_type == "scene":
        return await _detail_scene(run_id, thread_id, scene_number, uuid)
    elif artifact_type == "keyframe":
        return await _detail_keyframe(run_id, thread_id, shot_number, uuid)
    elif artifact_type == "video":
        return await _detail_video(run_id, thread_id, shot_number, uuid)
    elif artifact_type == "narration":
        return await _detail_narration(run_id, thread_id, shot_number, uuid)
    elif artifact_type == "music":
        return await _detail_music(run_id, thread_id, uuid)
    elif artifact_type == "shot":
        return await _detail_shot(run_id, thread_id, shot_number, uuid)
    else:
        return f"{artifact_type} 详情查询尚未实现。"


def _truncate(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS - 20] + "\n...(已截断)"


async def _detail_outline(run_id: str, thread_id: str) -> str:
    from .....crud.video.video_story import get_video_story_outline_by_run_id, get_video_story_outline_by_thread_id
    from .....crud.video.video_other import get_video_analysis_by_thread_id, get_video_analysis_by_uuid
    outline = await get_video_story_outline_by_thread_id(thread_id) if thread_id else await get_video_story_outline_by_run_id(run_id)
    if not outline:
        return "大纲尚未生成。"
    style_guide = (getattr(outline, "style_guide", None) or getattr(outline, "narrative_structure", None) or "").strip()
    style_tags = ""
    analysis_id = getattr(outline, "analysis_id", None)
    analysis = None
    if analysis_id:
        analysis = await get_video_analysis_by_uuid(analysis_id)
    elif thread_id:
        analysis = await get_video_analysis_by_thread_id(thread_id)
    if analysis is not None:
        raw_prefs = getattr(analysis, "style_preferences", None)
        if isinstance(raw_prefs, list):
            style_tags = ", ".join(str(x) for x in raw_prefs if str(x).strip())
        elif raw_prefs:
            style_tags = str(raw_prefs)
    return _truncate(
        f"大纲 (uuid: {outline.uuid})\n"
        f"  标题: {outline.title}\n"
        f"  描述(description): {outline.description}\n"
        f"  主题: {', '.join(outline.themes) if outline.themes else 'N/A'}\n"
        f"  视觉风格(style → style_guide): {style_guide or 'N/A'}\n"
        f"  风格标签(style → style_preferences，前端「风格」区块): {style_tags or 'N/A'}\n"
        f"  改视觉风格：modify_outline 至少含 style；整换风时再按涉及情况加 description/theme/title/key_message；"
        f"场景冲突另用 update_scene，不要只传 description\n"
        f"  总时长: {outline.total_duration}s\n"
        f"  当前版本: v{outline.current_version_index or 0}"
    )


async def _detail_character(run_id: str, user_id: str, thread_id: str, shot_number: Optional[int], uuid: Optional[str]) -> str:
    from .....crud.video.video_character import get_character_by_uuid, get_characters_by_run_id, get_characters_by_thread_id
    from .....crud.video.video_character import get_character_versions_by_character_id
    from .....crud.video.video_keyframe import get_keyframes_by_run_id, get_keyframes_by_thread_id
    from .....crud.video.video_story import get_scenes_by_run_id, get_scenes_by_thread_id

    if uuid:
        char = await get_character_by_uuid(uuid)
        if not char:
            return f"角色 {uuid} 不存在。"
        img_url = getattr(char, 'image_url', '') or ''
        selected_vid = getattr(char, 'selected_version_id', None) or ''

        lines = [
            f"角色 (uuid: {char.uuid})",
            f"  名称: {char.name}",
            f"  描述: {char.description}",
            f"  外观: {char.appearance}",
            f"  图片URL: {img_url}",
        ]

        if user_id:
            versions = await get_character_versions_by_character_id(char.uuid, user_id)
            if versions:
                lines.append("  版本（每版都带图片URL，可 analyze_image 后按画面内容挑，再 select_version(version_uuid=…)）:")
                for v in sorted(versions, key=lambda x: x.version_number):
                    marker = " ← 当前" if str(getattr(v, 'uuid', '')) == selected_vid else ""
                    success = "✅" if getattr(v, "success", True) else "❌"
                    v_url = getattr(v, 'character_image_url', '') or ''
                    url_part = f" 图片: {v_url}" if v_url else ""
                    lines.append(f"    v{v.version_number}{success} (version_uuid: {v.uuid}){url_part}{marker}")
            else:
                lines.append("  版本: N/A")
        else:
            lines.append(f"  selected_version_id: {selected_vid}")

        all_kfs = await get_keyframes_by_thread_id(thread_id) if thread_id else await get_keyframes_by_run_id(run_id)
        related_kfs = [kf for kf in all_kfs if uuid in (getattr(kf, 'character_ids', None) or [])]
        if related_kfs:
            lines.append("── 关联的关键帧 ──")
            for kf in sorted(related_kfs, key=lambda k: (k.shot_number, k.frame_index)):
                frame_label = "首帧" if kf.frame_index == 0 else ("尾帧" if kf.frame_index == -1 else f"帧{kf.frame_index}")
                lines.append(f"  shot_{kf.shot_number} {frame_label} (uuid: {kf.uuid})")

        all_scenes = await get_scenes_by_thread_id(thread_id) if thread_id else await get_scenes_by_run_id(run_id)
        related_scenes = [s for s in all_scenes if uuid in (getattr(s, 'character_ids', None) or [])]
        if related_scenes:
            lines.append("── 关联的场景 ──")
            for s in related_scenes:
                s_name = getattr(s, 'title', '') or getattr(s, 'name', '') or ''
                lines.append(f"  scene (uuid: {s.uuid}) {s_name}")

        return _truncate("\n".join(lines))

    if user_id:
        chars = await get_characters_by_thread_id(thread_id, user_id) if thread_id else await get_characters_by_run_id(run_id, user_id)
    else:
        chars = []
    if not chars:
        return "尚无角色数据。"
    from .....crud.video.video_character import get_character_versions_batch
    ver_map = {}
    if user_id:
        ver_map = await get_character_versions_batch([str(c.uuid) for c in chars], user_id)
    lines = [f"角色列表（共 {len(chars)} 个）:"]
    for c in chars:
        img_url = getattr(c, 'image_url', '') or ''
        img_hint = f", 图片: {img_url}" if img_url else ""
        selected_vid = getattr(c, 'selected_version_id', None)
        sel_hint = f", 当前版本: {selected_vid[:8]}..." if selected_vid else ""
        n_ver = len(ver_map.get(str(c.uuid), []))
        ver_hint = f", 共{n_ver}版" if n_ver else ""
        lines.append(f"  - {c.name} (uuid: {c.uuid}{ver_hint}{sel_hint}{img_hint})")
    lines.append(
        "提示：切版本无需逐个查 version_uuid——对每个角色 uuid 调 "
        "select_version(character, uuid, version_selector='first'|'latest'|'v<N>')。"
    )
    return _truncate("\n".join(lines))


async def _detail_scene(run_id: str, thread_id: str, scene_number: Optional[int], uuid: Optional[str]) -> str:
    from .....crud.video.video_story import get_scenes_by_run_id, get_scenes_by_thread_id

    scenes = await get_scenes_by_thread_id(thread_id) if thread_id else await get_scenes_by_run_id(run_id)
    if not scenes:
        return "尚无场景数据。"

    if scene_number is not None or uuid is not None:
        if uuid is not None:
            target = next((s for s in scenes if s.uuid == uuid), None)
        else:
            target = next((s for s in scenes if s.scene_number == scene_number), None)
        if target is None:
            available = ", ".join(str(s.scene_number) for s in scenes)
            ident = uuid if uuid is not None else f"第 {scene_number} 个"
            return f"未找到场景 {ident}（现有场景: {available}）。"
        char_ids = getattr(target, "character_ids", None) or []
        return _truncate(
            f"场景 第 {target.scene_number} 个 (uuid: {target.uuid})\n"
            f"  标题: {target.title}\n"
            f"  描述: {target.description}\n"
            f"  镜头角度: {getattr(target, 'camera_angle', '') or 'N/A'}\n"
            f"  角色动作: {getattr(target, 'character_action', '') or 'N/A'}\n"
            f"  视觉风格: {getattr(target, 'visual_style', '') or 'N/A'}\n"
            f"  时长: {getattr(target, 'duration', 0) or 0}s\n"
            f"  关联视觉元素: [{', '.join(char_ids) if char_ids else '无'}]"
        )

    lines = ["场景列表:"]
    for s in sorted(scenes, key=lambda x: x.scene_number):
        desc = (s.description or "")[:60]
        lines.append(f"  第 {s.scene_number} 个《{s.title}》(uuid: {s.uuid}): {desc}...")
    return _truncate("\n".join(lines))


async def _detail_keyframe(run_id: str, thread_id: str, shot_number: Optional[int], uuid: Optional[str]) -> str:
    from .....crud.video.video_keyframe import (
        get_keyframe_by_uuid,
        get_keyframes_by_run_id,
        get_keyframes_by_thread_id,
        get_keyframe_versions_by_keyframe_ids,
    )
    from .....crud.video.video_generation import get_video_generation_by_keyframe_id

    if uuid:
        kf = await get_keyframe_by_uuid(uuid)
        if not kf:
            return f"关键帧 {uuid} 不存在。"
        kfs = [kf]
    elif shot_number is not None:
        all_kfs = await get_keyframes_by_thread_id(thread_id) if thread_id else await get_keyframes_by_run_id(run_id)
        kfs = [k for k in all_kfs if k.shot_number == shot_number]
        if not kfs:
            return f"shot_{shot_number} 没有关键帧。"
    else:
        all_kfs = await get_keyframes_by_thread_id(thread_id) if thread_id else await get_keyframes_by_run_id(run_id)
        if not all_kfs:
            return "尚无关键帧数据。"
        lines = ["关键帧-视觉元素映射:"]
        for kf in sorted(all_kfs, key=lambda k: (k.shot_number, k.frame_index)):
            char_ids = getattr(kf, 'character_ids', None) or []
            char_hint = ", ".join(char_ids) if char_ids else "无"
            frame_label = "首帧" if kf.frame_index == 0 else ("尾帧" if kf.frame_index == -1 else f"帧{kf.frame_index}")
            lines.append(f"  shot_{kf.shot_number} {frame_label} (uuid: {kf.uuid}) 视觉元素: [{char_hint}]")
        return _truncate("\n".join(lines))

    kf_ids = [k.uuid for k in kfs]
    versions = await get_keyframe_versions_by_keyframe_ids(kf_ids)

    lines = []
    for kf in kfs:
        frame_label = "首帧" if kf.frame_index == 0 else ("尾帧" if kf.frame_index == -1 else f"帧{kf.frame_index}")
        char_ids = getattr(kf, 'character_ids', None) or []
        char_hint = f" 视觉元素: [{', '.join(char_ids)}]" if char_ids else ""
        lines.append(f"关键帧 shot_{kf.shot_number} {frame_label} (uuid: {kf.uuid}){char_hint}")
        kf_versions = sorted(
            [v for v in versions if v.keyframe_id == kf.uuid],
            key=lambda v: v.version_number,
        )
        for v in kf_versions:
            current = " ← 当前" if v.version_number == (kf.current_version_index or 0) else ""
            success = "✅" if getattr(v, "success", True) else "❌"
            kf_url = getattr(v, 'keyframe_url', '') or ''
            url_part = f" url: {kf_url}" if kf_url else ""
            char_ver_ids = getattr(v, 'character_version_ids', None) or []
            char_ver_hint = f" 使用角色版本: [{', '.join(char_ver_ids)}]" if char_ver_ids else ""
            lines.append(
                f"  v{v.version_number} {success} (version_uuid: {v.uuid}) ({v.provider}) "
                f"prompt: {(v.t2i_prompt or '')[:80]}...{url_part}{char_ver_hint}{current}"
            )

        vg = await get_video_generation_by_keyframe_id(kf.uuid)
        if vg:
            lines.append(f"── 关联的视频 ──")
            lines.append(f"  shot_{vg.shot_number} 视频 (uuid: {vg.uuid}) 当前版本: v{vg.current_version_index or 0}")

    return _truncate("\n".join(lines))


async def _detail_video(run_id: str, thread_id: str, shot_number: Optional[int], uuid: Optional[str]) -> str:
    from .....crud.video.video_generation import (
        get_video_generation_by_uuid,
        get_video_generations_by_run_id,
        get_video_generations_by_thread_id,
        get_video_generation_versions_by_video_generation_ids,
    )
    if uuid:
        vg = await get_video_generation_by_uuid(uuid)
        if not vg:
            return f"视频 {uuid} 不存在。"
        vgs = [vg]
    elif shot_number is not None:
        all_vgs = await get_video_generations_by_thread_id(thread_id) if thread_id else await get_video_generations_by_run_id(run_id)
        vgs = [v for v in all_vgs if v.shot_number == shot_number]
        if not vgs:
            return f"shot_{shot_number} 没有视频。"
    else:
        all_vgs = await get_video_generations_by_thread_id(thread_id) if thread_id else await get_video_generations_by_run_id(run_id)
        if not all_vgs:
            return "尚无视频数据。"
        lines = ["视频列表:"]
        for vg in sorted(all_vgs, key=lambda v: v.shot_number):
            kf_id = getattr(vg, 'keyframe_id', '') or ''
            kf_hint = f", 基于关键帧: {kf_id[:8]}..." if kf_id else ""
            lines.append(f"  shot_{vg.shot_number} (uuid: {vg.uuid}, 当前版本: v{vg.current_version_index or 0}{kf_hint})")
        return _truncate("\n".join(lines))

    vg_ids = [v.uuid for v in vgs]
    versions = await get_video_generation_versions_by_video_generation_ids(vg_ids)

    lines = []
    for vg in vgs:
        kf_id = getattr(vg, 'keyframe_id', '') or ''
        kf_hint = f" 基于关键帧: {kf_id}" if kf_id else ""
        lines.append(f"视频 shot_{vg.shot_number} (uuid: {vg.uuid}){kf_hint}")
        vg_versions = sorted(
            [v for v in versions if v.video_generation_id == vg.uuid],
            key=lambda v: v.version_number,
        )
        for v in vg_versions:
            current = " ← 当前" if v.version_number == vg.current_version_index else ""
            success = "✅" if getattr(v, "success", True) else "❌"
            dur = f"{v.duration:.1f}s" if v.duration else "N/A"
            vid_url = getattr(v, 'video_url', '') or ''
            url_part = f" url: {vid_url}" if vid_url else ""
            kf_ver_ids = getattr(v, 'keyframe_version_ids', None) or []
            kf_ver_hint = f" 使用关键帧版本: [{', '.join(kf_ver_ids)}]" if kf_ver_ids else ""
            lines.append(
                f"  v{v.version_number} {success} (version_uuid: {v.uuid}) ({v.provider}) {dur} "
                f"prompt: {(v.motion_prompt or '')[:60]}...{url_part}{kf_ver_hint}{current}"
            )
    return _truncate("\n".join(lines))


async def _detail_narration(run_id: str, thread_id: str, shot_number: Optional[int], uuid: Optional[str]) -> str:
    from .....crud.video.video_audio import get_narration_by_uuid, get_narrations_by_run_id, get_narrations_by_thread_id
    if uuid:
        nr = await get_narration_by_uuid(uuid)
        if not nr:
            return f"旁白 {uuid} 不存在。"
        return _truncate(
            f"旁白 shot_{nr.shot_number} (uuid: {nr.uuid})\n"
            f"  有旁白: {nr.has_narration}\n"
            f"  当前版本: v{nr.current_version_index}"
        )
    nrs = await get_narrations_by_thread_id(thread_id) if thread_id else await get_narrations_by_run_id(run_id)
    if shot_number is not None:
        nrs = [n for n in nrs if n.shot_number == shot_number]
    if not nrs:
        return "无旁白数据。"
    lines = ["旁白列表:"]
    for n in nrs:
        lines.append(f"  shot_{n.shot_number}: {'有' if n.has_narration else '无'}旁白 (uuid: {n.uuid})")
    return _truncate("\n".join(lines))


async def _detail_music(run_id: str, thread_id: str, uuid: Optional[str]) -> str:
    from .....crud.video.video_audio import get_music_generation_by_uuid, get_music_generations_by_run_id, get_music_generations_by_thread_id
    if uuid:
        mg = await get_music_generation_by_uuid(uuid)
        if not mg:
            return f"配乐 {uuid} 不存在。"
        return _truncate(
            f"配乐 (uuid: {mg.uuid})\n"
            f"  全曲BGM: {mg.is_full_story_music}\n"
            f"  纯音乐: {mg.is_instrumental}\n"
            f"  当前版本: v{mg.current_version_index}"
        )
    mgs = await get_music_generations_by_thread_id(thread_id) if thread_id else await get_music_generations_by_run_id(run_id)
    if not mgs:
        return "无配乐数据。"
    lines = ["配乐列表:"]
    for m in mgs:
        label = "全曲BGM" if m.is_full_story_music else f"shot_{m.shot_number or '?'}"
        lines.append(f"  {label} (uuid: {m.uuid})")
    return _truncate("\n".join(lines))


async def _detail_shot(run_id: str, thread_id: str, shot_number: Optional[int], uuid: Optional[str]) -> str:
    """综合展示单个 shot 的所有关联产物。"""
    from .....crud.video.video_keyframe import get_keyframes_by_run_id, get_keyframes_by_thread_id
    from .....crud.video.video_generation import get_video_generations_by_run_id, get_video_generations_by_thread_id
    from .....crud.video.video_audio import get_narrations_by_run_id, get_narrations_by_thread_id

    if shot_number is None and uuid is None:
        return "请提供 shot_number 或 uuid。"

    lines = [f"Shot {shot_number or uuid} 综合信息:"]

    all_kfs = await get_keyframes_by_thread_id(thread_id) if thread_id else await get_keyframes_by_run_id(run_id)
    shot_kfs = [k for k in all_kfs if (shot_number is not None and k.shot_number == shot_number)]
    lines.append(f"  关键帧: {len(shot_kfs)} 个")
    for kf in shot_kfs:
        frame_label = "首帧" if kf.frame_index == 0 else ("尾帧" if kf.frame_index == -1 else f"帧{kf.frame_index}")
        lines.append(f"    {frame_label} (uuid: {kf.uuid})")

    all_vgs = await get_video_generations_by_thread_id(thread_id) if thread_id else await get_video_generations_by_run_id(run_id)
    shot_vgs = [v for v in all_vgs if (shot_number is not None and v.shot_number == shot_number)]
    lines.append(f"  视频: {len(shot_vgs)} 个")

    all_nrs = await get_narrations_by_thread_id(thread_id) if thread_id else await get_narrations_by_run_id(run_id)
    shot_nrs = [n for n in all_nrs if (shot_number is not None and n.shot_number == shot_number)]
    lines.append(f"  旁白: {len(shot_nrs)} 个")

    return _truncate("\n".join(lines))
