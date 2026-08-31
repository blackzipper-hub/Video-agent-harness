"""
Post-regenerate：在 execute_regenerate_* 成功后写入 interaction_post_regenerate 会话消息；
post-regenerate-action 编排 propagate（选用 + 下一层 regenerate）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

EVENT_TYPE_INTERACTION_POST_REGENERATE = "interaction_post_regenerate"

VALID_REGENERATE_KINDS = (
    "regenerate_characters",
    "regenerate_keyframes",
    "regenerate_videos",
)


def _normalize_result_counts(result: Dict[str, Any]) -> Tuple[int, int]:
    ok = int(result.get("successful_count", result.get("succeeded", 0)) or 0)
    tot = int(result.get("total_versions", 0) or 0)
    failed = int(result.get("failed_count", max(0, tot - ok)) or 0)
    return ok, failed


async def _shot_for_keyframe_uuid(keyframe_uuid: str) -> Optional[int]:
    from ..crud.video.video_keyframe import get_keyframe_by_uuid

    row = await get_keyframe_by_uuid(keyframe_uuid)
    if not row:
        return None
    return int(row.shot_number) if getattr(row, "shot_number", None) is not None else None


async def _shot_for_video_uuid(video_uuid: str) -> Optional[int]:
    from ..crud.video.video_generation import get_video_generation_by_uuid

    row = await get_video_generation_by_uuid(video_uuid)
    if not row:
        return None
    sn = row.get("shot_number") if isinstance(row, dict) else getattr(row, "shot_number", None)
    return int(sn) if sn is not None else None


def build_post_regenerate_payload(
    kind: str,
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """从 regenerate_*_by_request 的 result 构建 payload.items（仅锚点；展示由前端 join + i18n，见 docs/regenerate_post_action_flow_spec §2.3.3）。"""
    if kind not in VALID_REGENERATE_KINDS:
        raise ValueError(f"invalid kind: {kind}")
    ok, failed = _normalize_result_counts(result)
    items: List[Dict[str, Any]] = []
    raw_results = result.get("results") or []
    if kind == "regenerate_characters":
        for r in raw_results:
            if not isinstance(r, dict) or not r.get("success"):
                continue
            cu = (r.get("character_uuid") or "").strip()
            nv = (r.get("new_version_uuid") or "").strip()
            if cu and nv:
                items.append(
                    {
                        "character_uuid": cu,
                        "new_version_uuid": nv,
                    }
                )
    elif kind == "regenerate_keyframes":
        for r in raw_results:
            if not isinstance(r, dict) or not r.get("success"):
                continue
            ku = (r.get("keyframe_uuid") or "").strip()
            nv = (r.get("new_version_uuid") or "").strip()
            if ku and nv:
                entry: Dict[str, Any] = {
                    "keyframe_uuid": ku,
                    "new_version_uuid": nv,
                    "frame_index": r.get("frame_index", 0),
                }
                sn = r.get("shot_number")
                if sn is not None:
                    entry["shot_number"] = int(sn)
                items.append(entry)
    elif kind == "regenerate_videos":
        for r in raw_results:
            if not isinstance(r, dict) or not r.get("success"):
                continue
            vu = (r.get("video_uuid") or "").strip()
            nv = (r.get("new_version_uuid") or "").strip()
            if vu and nv:
                items.append(
                    {
                        "video_uuid": vu,
                        "new_version_uuid": nv,
                    }
                )
    out: Dict[str, Any] = {
        "kind": kind,
        "items": items,
        "successful_count": ok,
        "failed_count": failed,
    }
    prop = result.get("propagate") if isinstance(result.get("propagate"), dict) else None
    if prop and isinstance(prop.get("items"), list):
        out["propagate"] = {"items": prop["items"]}
    return out


async def enrich_keyframe_items_shot_numbers(thread_id: str, payload: Dict[str, Any]) -> None:
    """若 keyframe 项缺少 shot_number，用 DB 补齐。"""
    if payload.get("kind") != "regenerate_keyframes":
        return
    for it in payload.get("items") or []:
        if not isinstance(it, dict):
            continue
        if it.get("shot_number") is not None:
            continue
        ku = (it.get("keyframe_uuid") or "").strip()
        if not ku:
            continue
        sn = await _shot_for_keyframe_uuid(ku)
        if sn is not None:
            it["shot_number"] = sn


async def enrich_video_items_shot_numbers(payload: Dict[str, Any]) -> None:
    if payload.get("kind") != "regenerate_videos":
        return
    for it in payload.get("items") or []:
        if not isinstance(it, dict):
            continue
        if it.get("shot_number") is not None:
            continue
        vu = (it.get("video_uuid") or "").strip()
        if not vu:
            continue
        sn = await _shot_for_video_uuid(vu)
        if sn is not None:
            it["shot_number"] = sn


async def attach_propagate_item_anchors_to_result(thread_id: str, kind: str, result: Dict[str, Any]) -> None:
    """在 persist 前写入 result.propagate.items：仅锚点（uuid + version），展示由前端 join，与 confirmed_summary 同构思路。"""
    if not (thread_id or "").strip() or not isinstance(result, dict):
        result.setdefault("propagate", {})
        result["propagate"]["items"] = []
        return
    try:
        tmp = build_post_regenerate_payload(kind, result)
        if kind == "regenerate_keyframes":
            await enrich_keyframe_items_shot_numbers(thread_id, tmp)
            shots: List[int] = []
            for it in tmp.get("items") or []:
                if isinstance(it, dict) and it.get("shot_number") is not None:
                    shots.append(int(it["shot_number"]))
            items = [{"shot_number": int(s)} for s in sorted(set(shots))]
        elif kind == "regenerate_characters":
            from app.services.post_regenerate_action_service import build_character_propagate_targets_for_message

            items = await build_character_propagate_targets_for_message(thread_id, {"items": tmp.get("items") or []})
        elif kind == "regenerate_videos":
            await enrich_video_items_shot_numbers(tmp)
            from app.services.post_regenerate_action_service import build_video_propagate_targets_for_message

            items = await build_video_propagate_targets_for_message({"items": tmp.get("items") or []})
        else:
            items = []
        result.setdefault("propagate", {})
        result["propagate"]["items"] = items
    except Exception as e:
        logger.warning("attach_propagate_item_anchors_to_result failed: %s", e, exc_info=True)
        result.setdefault("propagate", {})
        result["propagate"]["items"] = []


async def persist_post_regenerate_interaction_message(
    *,
    thread_id: str,
    run_id: str,
    user_id: str,
    kind: str,
    regenerate_source: str,
    result: Dict[str, Any],
) -> Optional[int]:
    """
    在 regenerate 成功且至少有一条成功项时写入 conversation_messages。
    返回 message id；无可写会话时返回 None。
    """
    from ..crud.conversation import async_get_conversation_by_thread_id, async_add_message_to_conversation

    ok_count, _ = _normalize_result_counts(result)
    if ok_count <= 0:
        return None
    conv = await async_get_conversation_by_thread_id(thread_id)
    if not conv or getattr(conv, "user_id", None) != user_id:
        logger.info("post_regenerate: skip message, no conversation or user mismatch thread_id=%s", thread_id)
        return None
    payload = build_post_regenerate_payload(kind, result)
    await enrich_keyframe_items_shot_numbers(thread_id, payload)
    await enrich_video_items_shot_numbers(payload)
    if not payload.get("items"):
        return None
    src = (regenerate_source or "panel").strip().lower()
    if src not in ("chat", "panel"):
        src = "panel"
    event_data: Dict[str, Any] = {
        "schema_version": 1,
        "interaction": {
            "action_type": "post_regenerate_next_step",
            "source": src,
            "payload": payload,
        },
    }
    try:
        row = await async_add_message_to_conversation(
            conversation_id=int(conv.id),
            role="ai",
            content=" ",
            event_type=EVENT_TYPE_INTERACTION_POST_REGENERATE,
            event_data=event_data,
            run_id=run_id,
            conversation_uuid=str(conv.uuid) if getattr(conv, "uuid", None) else None,
        )
        mid = row.get("id") if isinstance(row, dict) else getattr(row, "id", None)
        return int(mid) if mid is not None else None
    except Exception as e:
        logger.warning("post_regenerate: persist message failed: %s", e, exc_info=True)
        return None
