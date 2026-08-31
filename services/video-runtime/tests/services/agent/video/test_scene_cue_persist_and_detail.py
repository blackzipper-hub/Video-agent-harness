"""cue / identity_lock / texture_keywords → additional_data + detail prompt."""

from types import SimpleNamespace

from app.models.user_options import UserOption, VideoGenerationTool
from app.models.video_state import EnhancementCue, StoryboardScene, StoryChapter
from app.services.agent.video.visual_field_contract import (
    IDENTITY_LOCK_KEY,
    TEXTURE_KEYWORDS_KEY,
    get_visual_fields,
    inherit_scene_visual_fields_to_shot_ad,
    texture_keywords_from_cue_description,
)
from app.services.agent.video.scene_generation_service import (
    _attach_chapter_cues_to_scenes_by_order,
    _attach_enhancement_cue_to_scene,
    _compute_scene_structure_for_chapter,
    _storyboard_scene_from_structure_slot,
    _validate_and_convert_audio_driven_scenes,
    get_scene_enhancement_cue,
)
def _tx(duration=11.0):
    seg = SimpleNamespace(
        id=0, start=0.0, end=duration, text="lyric", duration=duration, uuid="u0",
    )
    return SimpleNamespace(
        task="t", language="zh", duration=duration, text="x",
        segments=[seg], audio_url="https://x",
    )


def _cues():
    return [
        EnhancementCue(type="broll", description="izakaya doorway amber lanterns"),
        EnhancementCue(type="broll", description="ramen alley noren steam"),
        EnhancementCue(type="broll", description="konbini glass fluorescent treat"),
    ]


def test_attach_cue_writes_texture_keywords():
    scene = StoryboardScene(
        scene_number=1,
        title="t",
        description="d",
        duration=4.0,
        camera_angle="中景",
        character_action="a",
        visual_style="v",
        transition_style="切",
        character_ids=[],
    )
    _attach_enhancement_cue_to_scene(scene, {"type": "broll", "description": "ramen steam noren"})
    cue = get_scene_enhancement_cue(scene)
    assert cue is not None
    assert "ramen" in cue["description"]
    fields = get_visual_fields(scene)
    assert TEXTURE_KEYWORDS_KEY in fields
    assert "noren" in fields[TEXTURE_KEYWORDS_KEY] or "steam" in fields[TEXTURE_KEYWORDS_KEY]


def test_validate_audio_driven_persists_structure_cues():
    opt = UserOption(video_generation_tool=VideoGenerationTool.SEEDANCE_2_I2V)
    tx = _tx(11.0)
    ch = StoryChapter(
        id="s2",
        title="狗选路",
        description="d",
        duration=11.0,
        order=1,
        audio_segment_ids=["u0"],
        enhancement_cues=_cues(),
    )
    structure = _compute_scene_structure_for_chapter(ch, tx, opt)
    assert len(structure) == 3

    llm_scenes = [
        StoryboardScene(
            scene_number=i + 1,
            title=f"s{i}",
            description="llm desc",
            duration=3.0,
            camera_angle="中景",
            character_action="走",
            visual_style="写实",
            transition_style="切",
            character_ids=[],
            audio_segment_ids=["0"],
        )
        for i in range(3)
    ]
    out = _validate_and_convert_audio_driven_scenes(llm_scenes, ch, tx, structure)
    assert len(out) == 3
    descs = [get_scene_enhancement_cue(s)["description"] for s in out]
    assert "izakaya" in descs[0]
    assert "ramen" in descs[1]
    assert "konbini" in descs[2]
    assert TEXTURE_KEYWORDS_KEY in get_visual_fields(out[1])


def test_structure_slot_placeholder_binds_cue():
    opt = UserOption(video_generation_tool=VideoGenerationTool.SEEDANCE_2_I2V)
    tx = _tx(11.0)
    ch = StoryChapter(
        id="s2", title="t", description="d", duration=11.0, order=0,
        audio_segment_ids=["u0"], enhancement_cues=_cues(),
    )
    structure = _compute_scene_structure_for_chapter(ch, tx, opt)
    scene = _storyboard_scene_from_structure_slot(structure[1], ch, tx)
    cue = get_scene_enhancement_cue(scene)
    assert cue and "ramen" in cue["description"]
    assert "ramen" in scene.description.lower() or "noren" in scene.description.lower()


def test_video_driven_order_attach():
    ch = StoryChapter(
        id="c", title="t", description="d", duration=12.0, order=0,
        enhancement_cues=_cues(),
    )
    scenes = [
        StoryboardScene(
            scene_number=i + 1, title=f"s{i}", description="d", duration=4.0,
            camera_angle="中景", character_action="a", visual_style="v",
            transition_style="切", character_ids=[],
        )
        for i in range(3)
    ]
    _attach_chapter_cues_to_scenes_by_order(scenes, ch)
    assert "izakaya" in get_scene_enhancement_cue(scenes[0])["description"]
    assert "konbini" in get_scene_enhancement_cue(scenes[2])["description"]


def test_inherit_fields_to_shot_and_wrap():
    scene = StoryboardScene(
        scene_number=1, title="t", description="d", duration=5.0,
        camera_angle="中景", character_action="a", visual_style="v",
        transition_style="切", character_ids=[],
    )
    _attach_enhancement_cue_to_scene(
        scene, {"type": "broll", "description": "warehouse hatch dust"}
    )
    scene.additional_data[IDENTITY_LOCK_KEY] = "red robot with blue eyes"
    shot_ad = inherit_scene_visual_fields_to_shot_ad(scene, {"narration_gender": "m"})
    assert shot_ad[IDENTITY_LOCK_KEY] == "red robot with blue eyes"
    assert TEXTURE_KEYWORDS_KEY in shot_ad
    assert shot_ad.get("narration_gender") == "m"


def test_texture_keywords_from_cue():
    kws = texture_keywords_from_cue_description("ramen alley noren steam")
    assert "noren" in kws
    assert "steam" in kws
