"""断点续跑幂等回归测试：

- get_completed_keyframe_uuids_by_shot：按 required_frames 判定镜头是否「所需帧全部成功」
- get_completed_video_uuids_by_shot_number：判定镜头视频是否已成功
判定口径：选中版本 success=True 且 url 非空；按 story_outline 作用域。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.agent.utils.database_utils import (
    get_completed_keyframe_uuids_by_shot,
    get_completed_video_uuids_by_shot_number,
)

KF_MOD = "app.services.agent.utils.database_utils.get_keyframes_by_story_outline_id"
KFV_MOD = "app.services.agent.utils.database_utils.get_keyframe_versions_by_keyframe_ids"
VG_MOD = "app.services.agent.utils.database_utils.get_video_generations_by_thread_id"
VGV_MOD = "app.services.agent.utils.database_utils.get_video_generation_versions_by_video_generation_ids"


def _kf(uuid, shot_id, frame_index, current_version_index=0):
    return SimpleNamespace(uuid=uuid, detailed_shot_id=shot_id, frame_index=frame_index,
                           current_version_index=current_version_index)


def _kfv(keyframe_id, version_number, success, keyframe_url):
    return SimpleNamespace(keyframe_id=keyframe_id, version_number=version_number,
                           success=success, keyframe_url=keyframe_url)


@pytest.mark.asyncio
async def test_keyframe_first_frame_only_done():
    # 仅首帧模式 required_frames={0}
    kfs = [_kf("kf-a", "shot-a", 0), _kf("kf-b", "shot-b", 0)]
    vers = [
        _kfv("kf-a", 1, True, "http://a.png"),
        _kfv("kf-b", 1, False, ""),  # 失败 -> 未完成
    ]
    with patch(KF_MOD, new_callable=AsyncMock, return_value=kfs), \
         patch(KFV_MOD, new_callable=AsyncMock, return_value=vers):
        done = await get_completed_keyframe_uuids_by_shot("outline-1", {0})
    assert "shot-a" in done and done["shot-a"] == ["kf-a"]
    assert "shot-b" not in done


@pytest.mark.asyncio
async def test_keyframe_continuity_requires_both_frames():
    # 连贯模式 required_frames={0,-1}：shot-a 首尾都成功，shot-b 缺尾帧
    kfs = [
        _kf("kf-a0", "shot-a", 0), _kf("kf-a1", "shot-a", -1),
        _kf("kf-b0", "shot-b", 0),
    ]
    vers = [
        _kfv("kf-a0", 1, True, "http://a0.png"),
        _kfv("kf-a1", 1, True, "http://a1.png"),
        _kfv("kf-b0", 1, True, "http://b0.png"),
    ]
    with patch(KF_MOD, new_callable=AsyncMock, return_value=kfs), \
         patch(KFV_MOD, new_callable=AsyncMock, return_value=vers):
        done = await get_completed_keyframe_uuids_by_shot("outline-1", {0, -1})
    assert "shot-a" in done and set(done["shot-a"]) == {"kf-a0", "kf-a1"}
    assert "shot-b" not in done  # 缺尾帧不算完成


@pytest.mark.asyncio
async def test_keyframe_selects_current_version_index():
    # current_version_index=1 指向失败版本 -> 未完成
    kfs = [_kf("kf-a", "shot-a", 0, current_version_index=1)]
    vers = [
        _kfv("kf-a", 1, True, "http://v1.png"),
        _kfv("kf-a", 2, False, ""),  # 选中版本（index 1）失败
    ]
    with patch(KF_MOD, new_callable=AsyncMock, return_value=kfs), \
         patch(KFV_MOD, new_callable=AsyncMock, return_value=vers):
        done = await get_completed_keyframe_uuids_by_shot("outline-1", {0})
    assert "shot-a" not in done


@pytest.mark.asyncio
async def test_keyframe_empty_outline_returns_empty():
    done = await get_completed_keyframe_uuids_by_shot("", {0})
    assert done == {}


def _vg(uuid, shot_number, story_outline_id, current_version_index=0):
    return SimpleNamespace(uuid=uuid, shot_number=shot_number,
                           story_outline_id=story_outline_id,
                           current_version_index=current_version_index)


def _vgv(video_generation_id, version_number, success, video_url):
    return SimpleNamespace(video_generation_id=video_generation_id, version_number=version_number,
                           success=success, video_url=video_url)


@pytest.mark.asyncio
async def test_video_done_filtered_by_outline_and_success():
    vgs = [
        _vg("vg-1", 1, "outline-1"),
        _vg("vg-2", 2, "outline-1"),
        _vg("vg-3", 3, "outline-OTHER"),  # 别的视频，应被排除
    ]
    vers = [
        _vgv("vg-1", 1, True, "http://1.mp4"),
        _vgv("vg-2", 1, False, ""),  # 失败
        _vgv("vg-3", 1, True, "http://3.mp4"),
    ]
    with patch(VG_MOD, new_callable=AsyncMock, return_value=vgs), \
         patch(VGV_MOD, new_callable=AsyncMock, return_value=vers):
        done = await get_completed_video_uuids_by_shot_number("outline-1", "thread-1")
    assert done == {1: "vg-1"}  # 只有 outline-1 且成功的 shot1


@pytest.mark.asyncio
async def test_video_empty_thread_returns_empty():
    done = await get_completed_video_uuids_by_shot_number("outline-1", "")
    assert done == {}


# ---- narration ----
NARR_MOD = "app.services.agent.utils.database_utils.get_narrations_by_thread_id"
NARRV_MOD = "app.services.agent.utils.database_utils.get_narration_versions_by_narration_ids"


def _narr(uuid, shot_number, story_outline_id, current_version_index=0):
    return SimpleNamespace(uuid=uuid, shot_number=shot_number,
                           story_outline_id=story_outline_id,
                           current_version_index=current_version_index)


def _narrv(narration_id, version_number, success, audio_url):
    return SimpleNamespace(narration_id=narration_id, version_number=version_number,
                           success=success, audio_url=audio_url)


@pytest.mark.asyncio
async def test_narration_done_filtered_by_outline_and_success():
    from app.services.agent.utils.database_utils import (
        get_completed_narration_uuids_by_shot_number,
    )
    narrs = [
        _narr("n-1", 1, "outline-1"),
        _narr("n-2", 2, "outline-1"),
        _narr("n-3", 3, "outline-OTHER"),  # 别的视频，应被排除
    ]
    vers = [
        _narrv("n-1", 1, True, "http://1.mp3"),
        _narrv("n-2", 1, True, "  "),  # 空白 url -> 未完成
        _narrv("n-3", 1, True, "http://3.mp3"),
    ]
    with patch(NARR_MOD, new_callable=AsyncMock, return_value=narrs), \
         patch(NARRV_MOD, new_callable=AsyncMock, return_value=vers):
        done = await get_completed_narration_uuids_by_shot_number("outline-1", "thread-1")
    assert done == {1: "n-1"}


@pytest.mark.asyncio
async def test_narration_empty_thread_returns_empty():
    from app.services.agent.utils.database_utils import (
        get_completed_narration_uuids_by_shot_number,
    )
    done = await get_completed_narration_uuids_by_shot_number("outline-1", "")
    assert done == {}


# ---- audio effect ----
AE_MOD = "app.services.agent.utils.database_utils.get_audio_effects_by_conversation"
AEV_MOD = "app.services.agent.utils.database_utils.get_audio_effect_versions_by_audio_effect_ids"


def _ae(uuid, shot_number, story_outline_id, current_version_index=0):
    return SimpleNamespace(uuid=uuid, shot_number=shot_number,
                           story_outline_id=story_outline_id,
                           current_version_index=current_version_index)


def _aev(audio_effect_id, version_number, success, audio_url):
    return SimpleNamespace(audio_effect_id=audio_effect_id, version_number=version_number,
                           success=success, audio_url=audio_url)


@pytest.mark.asyncio
async def test_audio_effect_done_filtered_by_outline_and_success():
    from app.services.agent.utils.database_utils import (
        get_completed_audio_effect_uuids_by_shot_number,
    )
    aes = [
        _ae("ae-1", 1, "outline-1"),
        _ae("ae-2", 2, "outline-1"),
        _ae("ae-3", 3, "outline-OTHER"),  # 别的视频，应被排除
    ]
    vers = [
        _aev("ae-1", 1, True, "http://1.mp3"),
        _aev("ae-2", 1, False, ""),  # 失败
        _aev("ae-3", 1, True, "http://3.mp3"),
    ]
    with patch(AE_MOD, new_callable=AsyncMock, return_value=aes), \
         patch(AEV_MOD, new_callable=AsyncMock, return_value=vers):
        done = await get_completed_audio_effect_uuids_by_shot_number("outline-1", "conv-1", "thread-1")
    assert done == {1: "ae-1"}


@pytest.mark.asyncio
async def test_audio_effect_empty_ids_return_empty():
    from app.services.agent.utils.database_utils import (
        get_completed_audio_effect_uuids_by_shot_number,
    )
    assert await get_completed_audio_effect_uuids_by_shot_number("outline-1", "", "thread-1") == {}
    assert await get_completed_audio_effect_uuids_by_shot_number("outline-1", "conv-1", "") == {}


# ---- character (thread 作用域；一个 thread 永远只一个视频) ----
CHAR_MOD = "app.services.agent.utils.database_utils.get_characters_by_thread_id"
CHARV_MOD = "app.services.agent.utils.database_utils.get_character_versions_batch"


def _char(uuid, name, current_version_index=0, selected_version_id=None):
    return SimpleNamespace(uuid=uuid, name=name,
                           current_version_index=current_version_index,
                           selected_version_id=selected_version_id)


def _charv(uuid, version_number, success, character_image_url):
    return SimpleNamespace(uuid=uuid, version_number=version_number,
                           success=success, character_image_url=character_image_url)


@pytest.mark.asyncio
async def test_character_done_by_name_success_and_url():
    from app.services.agent.utils.database_utils import (
        get_completed_character_uuids_by_name,
    )
    chars = [
        _char("c-1", "小明"),
        _char("c-2", "小红"),
        _char("c-3", "路人"),
    ]
    vers = {
        "c-1": [_charv("v-1", 1, True, "http://1.png")],
        "c-2": [_charv("v-2", 1, True, "  ")],   # 空白 url -> 未完成
        "c-3": [_charv("v-3", 1, False, "http://3.png")],  # 失败 -> 未完成
    }
    with patch(CHAR_MOD, new_callable=AsyncMock, return_value=chars), \
         patch(CHARV_MOD, new_callable=AsyncMock, return_value=vers):
        done = await get_completed_character_uuids_by_name("thread-1", "user-1")
    assert done == {"小明": "c-1"}


@pytest.mark.asyncio
async def test_character_empty_thread_or_user_returns_empty():
    from app.services.agent.utils.database_utils import (
        get_completed_character_uuids_by_name,
    )
    assert await get_completed_character_uuids_by_name("", "user-1") == {}
    assert await get_completed_character_uuids_by_name("thread-1", "") == {}
