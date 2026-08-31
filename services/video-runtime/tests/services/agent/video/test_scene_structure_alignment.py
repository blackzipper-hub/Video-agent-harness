"""audio-driven 场景校验：与 scene_structure 槽位 1:1；LLM 偏多截断、偏少补全；槽位内 UUID/时长修正。"""

from typing import Optional

from app.models.video_state import (
    AudioSegment,
    AudioTranscription,
    StoryChapter,
    StoryboardScene,
)
from app.services.agent.video.scene_generation_service import (
    _storyboard_scene_from_structure_slot,
    _validate_and_convert_audio_driven_scenes,
)


def _scene(
    scene_number: int,
    *,
    audio_segment_ids=None,
    duration: float = 5.0,
    description: str = "d",
    title: str = "t",
    generation_mode: Optional[str] = None,
) -> StoryboardScene:
    return StoryboardScene(
        scene_number=scene_number,
        title=title,
        description=description,
        duration=duration,
        camera_angle="wide",
        character_action="act",
        visual_style="v",
        transition_style="cut",
        character_ids=[],
        audio_segment_ids=audio_segment_ids,
        generation_mode=generation_mode,
    )


def _tx():
    segs = [
        AudioSegment(id=0, start=0.0, end=2.0, text="a", duration=2.0, uuid="u0"),
        AudioSegment(id=1, start=2.0, end=5.0, text="", duration=3.0, uuid="u1"),
    ]
    return AudioTranscription(
        task="transcribe",
        language="zh",
        duration=5.0,
        text="ab",
        segments=segs,
        audio_url="https://x",
    )


def _ch():
    return StoryChapter(
        id="ch1",
        title="第一章",
        description="d",
        duration=5.0,
        order=0,
        audio_segment_ids=["u0", "u1"],
    )


def test_validate_pads_when_llm_fewer_scenes():
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 2.0,
            "scene_number": 1,
        },
        {
            "audio_segment_index": 1,
            "audio_segment_uuid": "u1",
            "duration": 3.0,
            "scene_number": 2,
        },
    ]
    llm_scenes = [
        StoryboardScene(
            scene_number=99,
            title="only",
            description="x",
            duration=1.0,
            camera_angle="wide",
            character_action="act",
            visual_style="v",
            transition_style="cut",
            character_ids=[],
            audio_segment_ids=["0"],
        ),
    ]
    out = _validate_and_convert_audio_driven_scenes(llm_scenes, chapter, tx, structure)
    assert len(out) == 2
    assert out[0].scene_number == 1
    assert out[0].audio_segment_ids == ["u0"]
    assert out[0].duration == 2.0
    assert out[1].scene_number == 2
    assert out[1].audio_segment_ids == ["u1"]
    assert out[1].duration == 3.0
    assert "结构补全" in out[1].title


def test_storyboard_from_structure_uses_segment_text():
    chapter = _ch()
    tx = _tx()
    exp = {
        "audio_segment_uuid": "u0",
        "duration": 2.0,
        "scene_number": 1,
    }
    s = _storyboard_scene_from_structure_slot(exp, chapter, tx)
    assert s.audio_segment_ids == ["u0"]
    assert s.description == "a"
    assert s.scene_number == 1
    assert s.generation_mode == "normal"


def test_validate_truncates_when_llm_more_scenes_than_structure():
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 2.0,
            "scene_number": 1,
        },
    ]
    llm_scenes = [
        StoryboardScene(
            scene_number=1,
            title="first",
            description="d",
            duration=2.0,
            camera_angle="wide",
            character_action="act",
            visual_style="v",
            transition_style="cut",
            character_ids=[],
            audio_segment_ids=["0"],
        ),
        StoryboardScene(
            scene_number=2,
            title="extra",
            description="junk",
            duration=9.0,
            camera_angle="wide",
            character_action="act",
            visual_style="v",
            transition_style="cut",
            character_ids=[],
            audio_segment_ids=["1"],
        ),
    ]
    out = _validate_and_convert_audio_driven_scenes(llm_scenes, chapter, tx, structure)
    assert len(out) == 1
    assert out[0].audio_segment_ids == ["u0"]
    assert out[0].scene_number == 1


def test_validate_overwrites_scene_number_to_structure():
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 2.0,
            "scene_number": 1,
        },
    ]
    llm_scenes = [
        StoryboardScene(
            scene_number=5,
            title="t",
            description="d",
            duration=2.0,
            camera_angle="wide",
            character_action="act",
            visual_style="v",
            transition_style="cut",
            character_ids=[],
            audio_segment_ids=["0"],
        ),
    ]
    out = _validate_and_convert_audio_driven_scenes(llm_scenes, chapter, tx, structure)
    assert len(out) == 1
    assert out[0].scene_number == 1


def test_validate_empty_llm_scenes_all_padded_from_structure():
    """Gemini/LLM 返回空列表：全部槽位由程序按结构补全；无 LLM 时 generation_mode 为 normal。"""
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 2.0,
            "scene_number": 1,
        },
        {
            "audio_segment_index": 1,
            "audio_segment_uuid": "u1",
            "duration": 3.0,
            "scene_number": 2,
        },
    ]
    out = _validate_and_convert_audio_driven_scenes([], chapter, tx, structure)
    assert len(out) == 2
    assert out[0].audio_segment_ids == ["u0"]
    assert out[0].duration == 2.0
    assert out[0].generation_mode == "normal"
    assert out[1].audio_segment_ids == ["u1"]
    assert out[1].duration == 3.0
    assert out[1].generation_mode == "normal"


def test_validate_wrong_segment_index_for_slot_corrected_to_expected_uuid():
    """槽位 0 应绑 u0，LLM 却填片段 1 → 程序改为 expected_uuid。"""
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 2.0,
            "scene_number": 1,
        },
    ]
    out = _validate_and_convert_audio_driven_scenes(
        [_scene(1, audio_segment_ids=["1"], duration=2.0)],
        chapter,
        tx,
        structure,
    )
    assert len(out) == 1
    assert out[0].audio_segment_ids == ["u0"]


def test_validate_multiple_audio_ids_collapsed_to_single_expected():
    """LLM 同一槽位返回多个 index → 警告路径后压成 1 个 UUID（结构槽位）。"""
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 2.0,
            "scene_number": 1,
        },
    ]
    out = _validate_and_convert_audio_driven_scenes(
        [_scene(1, audio_segment_ids=["0", "1"], duration=2.0)],
        chapter,
        tx,
        structure,
    )
    assert out[0].audio_segment_ids == ["u0"]


def test_validate_missing_audio_segment_ids_uses_expected():
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 2.0,
            "scene_number": 1,
        },
    ]
    out = _validate_and_convert_audio_driven_scenes(
        [_scene(1, audio_segment_ids=None, duration=2.0)],
        chapter,
        tx,
        structure,
    )
    assert out[0].audio_segment_ids == ["u0"]


def test_validate_empty_audio_segment_ids_list_uses_expected():
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 2.0,
            "scene_number": 1,
        },
    ]
    out = _validate_and_convert_audio_driven_scenes(
        [_scene(1, audio_segment_ids=[], duration=2.0)],
        chapter,
        tx,
        structure,
    )
    assert out[0].audio_segment_ids == ["u0"]


def test_validate_duration_mismatch_resets_to_structure():
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 2.0,
            "scene_number": 1,
        },
    ]
    out = _validate_and_convert_audio_driven_scenes(
        [_scene(1, audio_segment_ids=["0"], duration=99.0)],
        chapter,
        tx,
        structure,
    )
    assert out[0].duration == 2.0


def test_validate_two_slots_same_uuid_split_segment():
    """同一 audio 片拆成两镜（结构两行同 uuid、不同时长）：按槽位各绑 u0。"""
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 1.0,
            "scene_number": 1,
        },
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 1.0,
            "scene_number": 2,
        },
    ]
    out = _validate_and_convert_audio_driven_scenes(
        [
            _scene(1, audio_segment_ids=["0"], duration=1.0),
            _scene(2, audio_segment_ids=["0"], duration=1.0),
        ],
        chapter,
        tx,
        structure,
    )
    assert len(out) == 2
    assert out[0].audio_segment_ids == ["u0"]
    assert out[1].audio_segment_ids == ["u0"]
    assert out[0].scene_number == 1
    assert out[1].scene_number == 2


def test_validate_equal_count_exact_match_no_pad():
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 2.0,
            "scene_number": 1,
        },
        {
            "audio_segment_index": 1,
            "audio_segment_uuid": "u1",
            "duration": 3.0,
            "scene_number": 2,
        },
    ]
    out = _validate_and_convert_audio_driven_scenes(
        [
            _scene(1, audio_segment_ids=["0"], duration=2.0),
            _scene(2, audio_segment_ids=["1"], duration=3.0),
        ],
        chapter,
        tx,
        structure,
    )
    assert len(out) == 2
    assert out[0].title == "t"
    assert out[1].title == "t"


def test_validate_invalid_index_only_yields_empty_conversion_then_corrects():
    """LLM 填非法 index '99' → 转换为空列表 → 走「数量不对」分支 → 使用 expected_uuid。"""
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 2.0,
            "scene_number": 1,
        },
    ]
    out = _validate_and_convert_audio_driven_scenes(
        [_scene(1, audio_segment_ids=["99"], duration=2.0)],
        chapter,
        tx,
        structure,
    )
    assert out[0].audio_segment_ids == ["u0"]


def test_validate_llm_explicit_lipsync_preserved():
    chapter = _ch()
    tx = _tx()
    structure = [
        {
            "audio_segment_index": 0,
            "audio_segment_uuid": "u0",
            "duration": 2.0,
            "scene_number": 1,
        },
    ]
    out = _validate_and_convert_audio_driven_scenes(
        [_scene(1, audio_segment_ids=["0"], duration=2.0, generation_mode="lipsync")],
        chapter,
        tx,
        structure,
    )
    assert out[0].generation_mode == "lipsync"


# =========================================================================
# Bug fix: empty chapter (audio_segment_ids 为空) 触发 scene_structure=[]
# 既要校验函数能截断 LLM 凭空多出的 scene，也要 _generate_scenes_for_chapter_with_llm
# 入口处提前 return，避免无谓 LLM 调用
# =========================================================================


def test_validate_empty_scene_structure_truncates_all_llm_scenes():
    """空 scene_structure（chapter 无对应 audio_segment）+ LLM 凭空给 N 个 scene → 全部截掉，返回空列表。"""
    chapter = _ch()
    tx = _tx()
    structure: list = []
    llm_scenes = [
        _scene(1, audio_segment_ids=["0"], duration=6.0, title="ghost1"),
        _scene(2, audio_segment_ids=["0"], duration=3.0, title="ghost2"),
    ]
    out = _validate_and_convert_audio_driven_scenes(llm_scenes, chapter, tx, structure)
    assert out == [], f"空 scene_structure 时 LLM 输出应被全部截断，但得到 {len(out)} 个 scene"


def test_validate_empty_structure_with_empty_llm_returns_empty():
    """空 scene_structure + 空 LLM scenes → 仍返回空，不要触发补全。"""
    chapter = _ch()
    tx = _tx()
    out = _validate_and_convert_audio_driven_scenes([], chapter, tx, [])
    assert out == []


import pytest  # noqa: E402

from app.models.video_state import (  # noqa: E402
    AudioSegment as _AudioSegment,
    AudioTranscription as _AudioTranscription,
    StoryChapter as _StoryChapter,
    StoryOutline as _StoryOutline,
    StoryStructure as _StoryStructure,
    ScenesCollection as _ScenesCollection,
    StoryboardScene as _StoryboardScene,
)
from app.services.agent.video.scene_generation_service import (  # noqa: E402
    _generate_scenes_for_chapter_with_llm,
    _compute_scene_structure_for_chapter,
)


def _make_tx_three_segments_20s() -> _AudioTranscription:
    """模拟 SmartClip 后 20s transcription：3 个 segment (6.2 + 7.8 + 6.0)，与 thread_admin_33e505d5 DB 完全一致。"""
    segs = [
        _AudioSegment(id=0, start=0.0, end=6.2, text="s0", duration=6.2, uuid="u0"),
        _AudioSegment(id=1, start=6.2, end=14.0, text="s1", duration=7.8, uuid="u1"),
        _AudioSegment(id=2, start=14.0, end=20.0, text="s2", duration=6.0, uuid="u2"),
    ]
    return _AudioTranscription(
        task="transcribe", language="zh", duration=20.0,
        text="s0s1s2", segments=segs, audio_url="https://x/trimfade.mp3",
    )


def _make_chapter(id_: str, title: str, duration: float, audio_seg_uuids=None, order: int = 0) -> _StoryChapter:
    return _StoryChapter(
        id=id_, title=title, description=f"desc-{id_}",
        duration=duration, order=order, audio_segment_ids=audio_seg_uuids,
    )


def test_compute_scene_structure_empty_when_chapter_has_no_audio_segments():
    """thread_admin_33e505d5 重现：chapter.audio_segment_ids 为 None/空 → 计算出的 scene_structure 为 []."""
    tx = _make_tx_three_segments_20s()
    ch_empty = _make_chapter("ch1", "宁静回味", duration=6.0, audio_seg_uuids=None)
    structure = _compute_scene_structure_for_chapter(ch_empty, tx, user_option=None)
    assert structure == [], "无 audio_segment_ids 的 chapter 应产生空 scene_structure"

    ch_empty_list = _make_chapter("ch1", "宁静回味", duration=6.0, audio_seg_uuids=[])
    structure2 = _compute_scene_structure_for_chapter(ch_empty_list, tx, user_option=None)
    assert structure2 == []


def test_compute_scene_structure_populated_when_chapter_has_audio_segments():
    """正常 chapter（引用 3 个 segment，split_threshold=5 时）应得到 6 个槽位，总时长 20s。"""
    tx = _make_tx_three_segments_20s()
    ch_full = _make_chapter("ch0", "暖阳初现", duration=20.0, audio_seg_uuids=["u0", "u1", "u2"])
    structure = _compute_scene_structure_for_chapter(ch_full, tx, user_option=None)
    assert len(structure) == 6, f"split_threshold=5 时 6.2s/7.8s/6.0s 各拆 2 个槽位，期望 6，实际 {len(structure)}"
    total = sum(s["duration"] for s in structure)
    assert abs(total - 20.0) < 0.01, f"槽位总时长应严格等于 transcription duration 20s，实际 {total}"


def _make_outline(chapters, total_duration: float) -> _StoryOutline:
    return _StoryOutline(
        title="t", theme="th", description="d", style_guide="sg",
        structure=_StoryStructure(chapters=chapters),
        key_message="km", total_duration=total_duration,
    )


@pytest.mark.asyncio
async def test_generate_scenes_for_chapter_with_empty_structure_skips_llm_and_returns_empty(monkeypatch):
    """thread_admin_33e505d5 修复核心路径：audio-driven + chapter 无 audio_segment_ids → 入口
    提前 return 空，**不调用 LLM 也不构建 prompt**（保护"提前 return"分支不会意外触达下游 IO）。"""
    tx = _make_tx_three_segments_20s()
    ch_empty = _make_chapter("ch1", "宁静回味", duration=6.0, audio_seg_uuids=None)
    outline = _make_outline([ch_empty], total_duration=20.0)

    da_hit = {"hit": False}

    async def _fail_da(*args, **kwargs):
        da_hit["hit"] = True
        raise AssertionError("audio-driven 空 chapter 不应走到 scene deep-agent")

    monkeypatch.setattr(
        "app.services.agent.video.scene_stage.generate_scenes_for_chapter_via_deep_agent",
        _fail_da,
    )

    result, msgs = await _generate_scenes_for_chapter_with_llm(
        story_outline=outline, characters_data=[], chapter=ch_empty, images=[],
        audio_transcription=tx, user_option=None, detected_language="zh",
        user_input="", content_category="Default",
        cached_prompt_template=None, cached_llm=None,
        thread_id="t_test", run_id="r_test",
    )
    assert result.success is True
    assert result.scenes == [], "空 chapter 应返回空 scene 列表"
    assert msgs == []
    assert da_hit["hit"] is False


@pytest.mark.asyncio
async def test_generate_scenes_for_chapter_audio_driven_with_audio_segments_does_not_early_return(monkeypatch):
    """正常 chapter（有 audio_segment_ids）→ 不提前 return，进入 scene deep-agent。"""
    tx = _make_tx_three_segments_20s()
    ch_full = _make_chapter("ch0", "暖阳初现", duration=20.0, audio_seg_uuids=["u0", "u1", "u2"])
    outline = _make_outline([ch_full], total_duration=20.0)

    da_hit = {"hit": False}

    async def _stop_at_da(*args, **kwargs):
        da_hit["hit"] = True
        raise RuntimeError("stop-here: 正常 chapter 必须进入 scene deep-agent")

    monkeypatch.setattr(
        "app.services.agent.video.scene_stage.generate_scenes_for_chapter_via_deep_agent",
        _stop_at_da,
    )

    with pytest.raises(RuntimeError, match="stop-here"):
        await _generate_scenes_for_chapter_with_llm(
            story_outline=outline, characters_data=[], chapter=ch_full, images=[],
            audio_transcription=tx, user_option=None, detected_language="zh",
            user_input="", content_category="Default",
            cached_prompt_template=None, cached_llm=None,
            thread_id="t_test", run_id="r_test",
        )
    assert da_hit["hit"] is True, "audio-driven 正常 chapter 必须走到 scene deep-agent"


@pytest.mark.asyncio
async def test_generate_scenes_video_driven_no_audio_transcription_does_not_early_return(monkeypatch):
    """video-driven（audio_transcription=None）不受空槽位防护影响——必须进入 scene deep-agent。"""
    ch = _make_chapter("ch0", "ch", duration=10.0, audio_seg_uuids=None)
    outline = _make_outline([ch], total_duration=10.0)

    da_hit = {"hit": False}

    async def _stop(*args, **kwargs):
        da_hit["hit"] = True
        raise RuntimeError("stop-here")

    monkeypatch.setattr(
        "app.services.agent.video.scene_stage.generate_scenes_for_chapter_via_deep_agent",
        _stop,
    )

    with pytest.raises(RuntimeError, match="stop-here"):
        await _generate_scenes_for_chapter_with_llm(
            story_outline=outline, characters_data=[], chapter=ch, images=[],
            audio_transcription=None, user_option=None, detected_language="zh",
            user_input="", content_category="Default",
            cached_prompt_template=None, cached_llm=None,
            thread_id="t_test", run_id="r_test",
        )
    assert da_hit["hit"] is True, "video-driven 路径应进入 scene deep-agent"


def test_thread_admin_33e505d5_total_duration_invariant():
    """thread_admin_33e505d5 不变量复现：
    - 故事大纲：2 chapter（ch0 全引用 3 个 segment / ch1 空）+ transcription=20s
    - 修复后：ch0 产 6 槽位（总 20s），ch1 槽位空（产 0 scene）
    - 不变量：scene_structure 总时长 ≡ transcription.duration（修复前 LLM 凭空给 6s 导致 26s）
    """
    tx = _make_tx_three_segments_20s()
    ch0 = _make_chapter("ch0", "暖阳初现", duration=14.0, audio_seg_uuids=["u0", "u1", "u2"], order=0)
    ch1 = _make_chapter("ch1", "宁静回味", duration=6.0, audio_seg_uuids=None, order=1)

    ch0_struct = _compute_scene_structure_for_chapter(ch0, tx, user_option=None)
    ch1_struct = _compute_scene_structure_for_chapter(ch1, tx, user_option=None)

    assert len(ch0_struct) == 6, f"ch0 应得 6 槽位，实际 {len(ch0_struct)}"
    assert len(ch1_struct) == 0, f"ch1 应得 0 槽位（无 audio_segment_ids），实际 {len(ch1_struct)}"

    total_planned = sum(s["duration"] for s in ch0_struct) + sum(s["duration"] for s in ch1_struct)
    assert abs(total_planned - 20.0) < 0.01, (
        f"两章总时长应严格等于 transcription 20s，实际 {total_planned}（bug 现象是 26s）"
    )

    # 验证修复后的校验逻辑：即便空 scene_structure 时 LLM 凭空给 1 个 scene，也会被 validate 截断（双保险）
    ghost_scene = _StoryboardScene(
        scene_number=1, title="ghost-extra-scene", description="LLM 凭空多出来的镜头",
        duration=6.0, camera_angle="wide", character_action="act", visual_style="v",
        transition_style="cut", character_ids=[], audio_segment_ids=["0"],
    )
    out = _validate_and_convert_audio_driven_scenes([ghost_scene], ch1, tx, ch1_struct)
    assert out == [], "空 ch1 scene_structure 下，LLM 凭空多出的 ghost scene 必须被截掉"
