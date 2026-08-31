"""
post-regenerate-action：校验 interaction_post_regenerate 消息并编排 propagate。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from app.exceptions import BusinessException, BusinessExceptionCode
from app.services.post_regenerate_interaction_service import EVENT_TYPE_INTERACTION_POST_REGENERATE

logger = logging.getLogger(__name__)


def _char_versions_from_payload(payload: Dict[str, Any]) -> Dict[str, str]:
    char_versions: Dict[str, str] = {}
    for it in payload.get("items") or []:
        if not isinstance(it, dict):
            continue
        cu = (it.get("character_uuid") or "").strip()
        nv = (it.get("new_version_uuid") or "").strip()
        if cu and nv:
            char_versions[cu] = nv
    return char_versions


async def _scan_character_propagate_keyframes(
    thread_id: str,
    char_versions: Dict[str, str],
) -> List[Dict[str, Any]]:
    """与 sync_downstream 一致：引用本次变更角色、首帧、当前选用版含 t2i 的关键帧。每项含构建 KeyframeRequest 所需字段。"""
    if not char_versions:
        return []
    from app.crud.video.video_keyframe import (
        get_keyframe_by_uuid,
        get_keyframes_by_thread_id,
        get_keyframe_versions_by_keyframe_ids,
    )

    all_kfs = await get_keyframes_by_thread_id(thread_id)
    rows: List[Dict[str, Any]] = []
    for kf in all_kfs:
        cids = getattr(kf, "character_ids", None) or []
        if not any(cid in char_versions for cid in cids):
            continue
        if getattr(kf, "frame_index", 0) != 0:
            continue
        versions = await get_keyframe_versions_by_keyframe_ids([kf.uuid])
        if not versions:
            continue
        sorted_v = sorted(
            [v for v in versions if v.keyframe_id == kf.uuid],
            key=lambda v: getattr(v, "version_number", 0),
        )
        kfdb = await get_keyframe_by_uuid(kf.uuid)
        if isinstance(kfdb, dict):
            current_idx = int(kfdb.get("current_version_index") or 0)
        else:
            current_idx = (kfdb.current_version_index or 0) if kfdb else 0
        if current_idx >= len(sorted_v):
            current_idx = len(sorted_v) - 1
        sel = sorted_v[current_idx]
        ver_uuid = getattr(sel, "uuid", "") or ""
        if not ver_uuid:
            continue
        t2i = (getattr(sel, "t2i_prompt", None) or "").strip()
        if not t2i:
            continue
        rows.append(
            {
                "keyframe_uuid": kf.uuid,
                "shot_number": int(kf.shot_number),
                "frame_index": int(getattr(kf, "frame_index", 0) or 0),
                "version_uuid": ver_uuid,
                "t2i_prompt": t2i,
            }
        )
    return rows


async def build_character_propagate_targets_for_message(
    thread_id: str,
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """写入 interaction payload.propagate_targets（无 t2i，仅展示锚点）。"""
    char_versions = _char_versions_from_payload(payload)
    rows = await _scan_character_propagate_keyframes(thread_id, char_versions)
    return [
        {
            "keyframe_uuid": r["keyframe_uuid"],
            "shot_number": r["shot_number"],
            "frame_index": r["frame_index"],
            "keyframe_version_uuid": r["version_uuid"],
        }
        for r in rows
    ]


async def _scan_video_propagate_timeline_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """与 _sync_after_videos 同一选用/可合成判定；每项含 timeline 请求字段。"""
    from app.crud.video.video_generation import (
        get_video_generation_by_uuid,
        get_video_generation_versions_by_video_generation_ids,
    )

    video_uuids: List[str] = []
    for it in payload.get("items") or []:
        if not isinstance(it, dict):
            continue
        vu = (it.get("video_uuid") or "").strip()
        if vu and vu not in video_uuids:
            video_uuids.append(vu)
    out: List[Dict[str, Any]] = []
    for vu in video_uuids:
        vg_row = await get_video_generation_by_uuid(vu)
        if not vg_row:
            continue
        versions = await get_video_generation_versions_by_video_generation_ids([vu])
        if not versions:
            continue
        if isinstance(vg_row, dict):
            current_idx = int(vg_row.get("current_version_index") or 0)
            shot_raw = vg_row.get("shot_number")
        else:
            current_idx = int(getattr(vg_row, "current_version_index", 0) or 0)
            shot_raw = getattr(vg_row, "shot_number", None)
        sorted_v = sorted(versions, key=lambda x: getattr(x, "version_number", 0))
        if current_idx >= len(sorted_v):
            current_idx = len(sorted_v) - 1
        if current_idx < 0:
            current_idx = 0
        sel = sorted_v[current_idx]
        ver_uuid = getattr(sel, "uuid", "") or ""
        if not ver_uuid:
            continue
        if not getattr(sel, "success", True):
            continue
        if not getattr(sel, "video_url", None):
            continue
        sn = int(shot_raw) if shot_raw is not None else None
        out.append(
            {
                "video_generation_uuid": vu,
                "video_generation_version_uuid": ver_uuid,
                "shot_number": sn,
            }
        )
    return out


async def build_video_propagate_targets_for_message(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = await _scan_video_propagate_timeline_rows(payload)
    return [
        {
            "video_generation_uuid": r["video_generation_uuid"],
            "shot_number": r["shot_number"],
            "video_generation_version_uuid": r["video_generation_version_uuid"],
        }
        for r in rows
        if r.get("shot_number") is not None
    ]


async def _sync_after_keyframes(
    thread_id: str,
    user_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """新关键帧版本已落库后，对涉及镜号提交 regenerate_videos；不写 keyframe.current（由用户自行选用）。"""
    from app.api.agent.agent_router_endpoints import VideoRequest, VideoVersionRequest
    from app.crud.video.video_generation import (
        get_video_generation_by_uuid,
        get_video_generations_by_thread_id,
        get_video_generation_versions_by_video_generation_ids,
    )
    from app.models.version_regenerate_strategy import VideoRegenerateStrategy

    items = [x for x in (payload.get("items") or []) if isinstance(x, dict)]
    shot_set: List[int] = []
    shot_to_forced_kf_ver: Dict[int, str] = {}
    for it in items:
        nv = (it.get("new_version_uuid") or "").strip()
        ku = (it.get("keyframe_uuid") or "").strip()
        if not nv or not ku:
            continue
        sn = it.get("shot_number")
        if sn is None:
            from app.services.post_regenerate_interaction_service import _shot_for_keyframe_uuid

            sn = await _shot_for_keyframe_uuid(ku)
        if sn is None:
            continue
        sn_int = int(sn)
        if sn_int not in shot_set:
            shot_set.append(sn_int)
        shot_to_forced_kf_ver[sn_int] = nv
    if not shot_set:
        return {"new_run_id": None, "message": "no shots to sync"}

    all_vgs = await get_video_generations_by_thread_id(thread_id)
    video_requests: List[VideoRequest] = []
    for sn in sorted(shot_set):
        target = [v for v in all_vgs if getattr(v, "shot_number", None) == sn]
        if not target:
            continue
        vg = target[0]
        vg_row = await get_video_generation_by_uuid(vg.uuid)
        versions = await get_video_generation_versions_by_video_generation_ids([vg.uuid])
        if not versions:
            continue
        if isinstance(vg_row, dict):
            current_idx = int(vg_row.get("current_version_index") or 0)
        else:
            current_idx = (vg_row.current_version_index or 0) if vg_row else 0
        sorted_v = sorted(versions, key=lambda x: getattr(x, "version_number", 0))
        if current_idx >= len(sorted_v):
            current_idx = len(sorted_v) - 1
        sel = sorted_v[current_idx]
        ver_uuid = getattr(sel, "uuid", "") or ""
        if not ver_uuid:
            continue
        forced_kf = (shot_to_forced_kf_ver.get(sn) or "").strip() or None
        video_requests.append(
            VideoRequest(
                uuid=vg.uuid,
                shot_number=vg.shot_number,
                versions=[
                    VideoVersionRequest(
                        uuid=ver_uuid,
                        custom_prompt=None,
                        instruction=None,
                        forced_keyframe_version_uuid=forced_kf,
                        regenerate_strategy=VideoRegenerateStrategy.PROMPT_REGENERATE.value,
                    )
                ],
            )
        )
    if not video_requests:
        return {"new_run_id": None, "message": "no video rows for shots"}
    new_run_id = str(uuid.uuid4())
    from app.services.task_enqueue_service import execute_regenerate_videos

    await execute_regenerate_videos(
        thread_id=thread_id,
        run_id=new_run_id,
        user_id=user_id,
        videos=video_requests,
        user_option=None,
        regenerate_source="panel",
    )
    return {"new_run_id": new_run_id, "message": "sync_downstream_videos_submitted"}


async def _sync_after_characters(
    thread_id: str,
    user_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """新角色版本已落库后，对引用该角色的关键帧所在镜提交 regenerate_keyframes（首帧）；不写 character.selected（由用户自行选用）。"""
    from app.api.agent.agent_router_endpoints import KeyframeRequest, KeyframeVersionRequest
    from app.models.version_regenerate_strategy import KeyframeRegenerateStrategy
    from app.services.agent.video.regenerate.regenerate_context import forced_character_version_uuids_cv

    char_versions = _char_versions_from_payload(payload)
    if not char_versions:
        return {"new_run_id": None, "message": "no character items"}

    rows = await _scan_character_propagate_keyframes(thread_id, char_versions)
    if not rows:
        return {"new_run_id": None, "message": "no keyframes reference these characters"}
    keyframe_requests: List[KeyframeRequest] = []
    for r in rows:
        keyframe_requests.append(
            KeyframeRequest(
                uuid=r["keyframe_uuid"],
                shot_number=r["shot_number"],
                frame_index=r["frame_index"],
                versions=[
                    KeyframeVersionRequest(
                        uuid=r["version_uuid"],
                        custom_prompt=r["t2i_prompt"],
                        instruction=None,
                        regenerate_strategy=KeyframeRegenerateStrategy.PROMPT_REGENERATE.value,
                    )
                ],
            )
        )
    new_run_id = str(uuid.uuid4())
    from app.services.task_enqueue_service import execute_regenerate_keyframes

    _tok = forced_character_version_uuids_cv.set(char_versions)
    try:
        await execute_regenerate_keyframes(
            thread_id=thread_id,
            run_id=new_run_id,
            user_id=user_id,
            keyframes=keyframe_requests,
            user_option=None,
            regenerate_source="panel",
        )
    finally:
        forced_character_version_uuids_cv.reset(_tok)
    return {"new_run_id": new_run_id, "message": "sync_downstream_keyframes_submitted"}


async def _sync_after_videos(
    thread_id: str,
    user_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """与页面「合并视频」一致：对该 thread 下全部镜头参与 segment 同步；卡片里的 new_version 仅覆盖对应 shot，其余用 DB 当前选用版；不写 video_generation.current（由用户自行选用）。"""
    from app.api.agent.agent_router_endpoints import SyncSegmentsVideoVersionRequest
    from app.crud.video.video_generation import (
        get_video_generations_by_thread_id,
        get_video_generation_versions_by_video_generation_ids,
    )

    override_ver: Dict[str, str] = {}
    for it in payload.get("items") or []:
        if not isinstance(it, dict):
            continue
        vu = (it.get("video_uuid") or "").strip()
        nv = (it.get("new_version_uuid") or "").strip()
        if vu and nv:
            override_ver[vu] = nv

    all_vgs = await get_video_generations_by_thread_id(thread_id)
    all_vgs = sorted(all_vgs, key=lambda x: (getattr(x, "shot_number", 0) or 0))
    vg_ids = [vg.uuid for vg in all_vgs]
    if not vg_ids:
        return {"new_run_id": None, "message": "no_video_items"}

    versions_flat = await get_video_generation_versions_by_video_generation_ids(vg_ids)
    by_vg: Dict[str, List[Any]] = {}
    for ver in versions_flat:
        gid = getattr(ver, "video_generation_id", None) or ""
        if gid:
            by_vg.setdefault(gid, []).append(ver)
    for gid in by_vg:
        by_vg[gid].sort(key=lambda x: getattr(x, "version_number", 0))

    video_versions: List[SyncSegmentsVideoVersionRequest] = []
    for vg in all_vgs:
        vu = vg.uuid
        versions = by_vg.get(vu) or []
        if not versions:
            continue
        current_idx = int(getattr(vg, "current_version_index", 0) or 0)
        sorted_v = versions
        if current_idx >= len(sorted_v):
            current_idx = len(sorted_v) - 1
        if current_idx < 0:
            current_idx = 0
        ver_uuid = (override_ver.get(vu) or "").strip()
        sel = None
        if ver_uuid:
            sel = next((x for x in sorted_v if getattr(x, "uuid", "") == ver_uuid), None)
        if sel is None:
            sel = sorted_v[current_idx]
            ver_uuid = getattr(sel, "uuid", "") or ""
        if not ver_uuid:
            continue
        if not getattr(sel, "success", True):
            continue
        if not getattr(sel, "video_url", None):
            continue
        video_versions.append(
            SyncSegmentsVideoVersionRequest(
                video_generation_uuid=vu,
                video_generation_version_uuid=ver_uuid,
            )
        )

    if not video_versions:
        return {"new_run_id": None, "message": "no_resolvable_video_versions"}

    new_run_id = str(uuid.uuid4())
    from app.services.task_enqueue_service import execute_regenerate_timeline

    await execute_regenerate_timeline(
        thread_id=thread_id,
        run_id=new_run_id,
        user_id=user_id,
        video_versions=video_versions,
        regenerate_source="panel",
    )
    return {"new_run_id": new_run_id, "message": "sync_downstream_timeline_submitted"}


async def run_post_regenerate_action(
    *,
    user_id: str,
    conversation_id: int,
    message_id: int,
    action: str,
) -> Dict[str, Any]:
    from app.crud.conversation import (
        async_get_conversation_by_id,
        async_get_message_event_type_and_data_for_conversation,
    )

    conv = await async_get_conversation_by_id(conversation_id)
    if not conv or getattr(conv, "user_id", None) != user_id:
        raise BusinessException(BusinessExceptionCode.PERMISSION_DENIED, "无权访问该对话")
    thread_id = getattr(conv, "thread_id", None) or ""
    row = await async_get_message_event_type_and_data_for_conversation(message_id, conversation_id)
    if not row:
        raise BusinessException(BusinessExceptionCode.RESOURCE_NOT_FOUND, "消息不存在")
    et, ed = row
    if et != EVENT_TYPE_INTERACTION_POST_REGENERATE:
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "消息类型不支持此操作")
    interaction = (ed or {}).get("interaction") or {}
    payload = interaction.get("payload") or {}
    kind = (payload.get("kind") or "").strip()
    act = (action or "").strip().lower()
    if act == "continue_edit":
        return {"ok": True, "action": act, "new_run_id": None}
    if act != "sync_downstream":
        raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, f"未知 action: {action}")
    if kind in ("regenerate_keyframes", "regenerate_characters", "regenerate_videos"):
        if not (thread_id or "").strip():
            raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, "thread_id 无效")
        from app.api.agent.agent_router_endpoints import validate_thread_id

        await validate_thread_id(thread_id, user_id)
    if kind in ("regenerate_keyframes", "regenerate_characters", "regenerate_videos"):
        from app.utils.credit_deduction_utils import check_credits_before_task

        check_ok, _, check_err = await check_credits_before_task(user_id)
        if not check_ok:
            raise BusinessException(BusinessExceptionCode.INSUFFICIENT_CREDITS, check_err)
    if kind == "regenerate_keyframes":
        out = await _sync_after_keyframes(thread_id, user_id, payload)
        return {"ok": True, "action": act, **out}
    if kind == "regenerate_characters":
        out = await _sync_after_characters(thread_id, user_id, payload)
        return {"ok": True, "action": act, **out}
    if kind == "regenerate_videos":
        out = await _sync_after_videos(thread_id, user_id, payload)
        return {"ok": True, "action": act, **out}
    raise BusinessException(BusinessExceptionCode.INVALID_PARAMETER, f"不支持的 kind: {kind}")
