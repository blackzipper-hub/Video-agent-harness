"""Unit tests for dynamic video workflow path (no LLM)."""
from app.models.tool_enums import ContentCategory
from app.models.user_options import (
    UserOption,
    VideoGenerationTool,
    apply_short_drama_default_video_tool,
    resolve_effective_video_tool,
    should_skip_keyframe_pipeline,
    should_use_reference_to_video,
)
from app.services.agent.video.music_generation_service import (
    MusicIntentType,
    SHOT_WORKFLOW_REFERENCE_T2V,
    apply_shot_workflow_to_path,
    build_gate_after_music_interrupt_payload,
    build_product_launch_workflow_path,
    build_video_workflow_path,
    resolve_shot_workflow_mode,
    should_skip_gate_after_music,
    should_skip_keyframe_pipeline_from_state,
    strip_storyboards_from_workflow_path,
)


def _ids(path):
    return [p["id"] for p in path]


def test_build_path_user_upload():
    path, mode = build_video_workflow_path(
        has_audio=True,
        music_intent=None,
        include_music=True,
    )
    assert mode == "user_upload"
    assert _ids(path)[0] == "music"
    assert _ids(path)[1] == "analysis"


def test_build_path_suno_front():
    path, mode = build_video_workflow_path(
        has_audio=False,
        music_intent=MusicIntentType.AUTO_LYRICS_SONG,
        include_music=True,
    )
    assert mode == "suno_then_transcribe"
    assert _ids(path) == [
        "music",
        "analysis",
        "story_style",
        "visual",
        "scenes",
        "storyboards",
        "shots",
        "final",
    ]


def test_build_path_bgm_parallel():
    path, mode = build_video_workflow_path(
        has_audio=False,
        music_intent=MusicIntentType.INSTRUMENTAL_BGM,
        include_music=True,
    )
    assert mode == "bgm_parallel"
    assert _ids(path).index("music") == _ids(path).index("scenes") + 1


def test_build_path_no_music_sora():
    path, mode = build_video_workflow_path(
        has_audio=False,
        music_intent=MusicIntentType.AUTO_LYRICS_SONG,
        include_music=False,
    )
    assert mode == "none_sora"
    assert "music" not in _ids(path)
    assert _ids(path)[0] == "analysis"


def test_build_path_product_launch():
    path, mode = build_product_launch_workflow_path()
    assert mode == "product_launch"
    assert "music" not in _ids(path)
    assert _ids(path)[0] == "analysis"
    assert _ids(path)[-1] == "final"
    assert "narration" in _ids(path)
    assert _ids(path).index("narration") == _ids(path).index("scenes") + 1
    assert _ids(path).index("storyboards") == _ids(path).index("narration") + 1


def test_gate_skip_product_launch():
    assert should_skip_gate_after_music({"music_workflow_mode": "product_launch"}) is True


def test_gate_skip_short_drama():
    assert should_skip_gate_after_music({"music_workflow_mode": "short_drama"}) is True


def test_gate_skip_none_sora():
    assert should_skip_gate_after_music({"music_workflow_mode": "none_sora"}) is True
    assert should_skip_gate_after_music({"music_workflow_mode": "bgm_parallel"}) is True
    assert should_skip_gate_after_music({"music_workflow_mode": "suno_then_transcribe"}) is False


def test_gate_payload_bgm_parallel():
    payload = build_gate_after_music_interrupt_payload(
        {"music_workflow_mode": "bgm_parallel", "music_intent": "instrumental_bgm"},
        credit_estimate={},
    )
    assert payload["music_mode"] == "bgm_parallel"
    assert payload["message_key"] == "video.pause.after_music_bgm_parallel"
    assert "场景" in payload["message_default"]


def test_short_drama_auto_defaults_to_seedance2_fast_turbo():
    uo = UserOption.default()
    uo.content_category = ContentCategory.SHORT_DRAMA
    uo.video_generation_tool = VideoGenerationTool.AUTO
    assert resolve_effective_video_tool(uo) == VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO
    applied = apply_short_drama_default_video_tool(uo)
    assert applied.video_generation_tool == VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO


def test_short_drama_keeps_explicit_seedance2_variant():
    uo = UserOption.default()
    uo.content_category = ContentCategory.SHORT_DRAMA
    uo.video_generation_tool = VideoGenerationTool.SEEDANCE_2_I2V
    assert resolve_effective_video_tool(uo) == VideoGenerationTool.SEEDANCE_2_I2V
    assert apply_short_drama_default_video_tool(uo).video_generation_tool == VideoGenerationTool.SEEDANCE_2_I2V


def test_short_drama_keeps_explicit_non_seedance2():
    uo = UserOption.default()
    uo.content_category = ContentCategory.SHORT_DRAMA
    uo.video_generation_tool = VideoGenerationTool.WAN_2_6
    assert resolve_effective_video_tool(uo) == VideoGenerationTool.WAN_2_6
    assert apply_short_drama_default_video_tool(uo).video_generation_tool == VideoGenerationTool.WAN_2_6


def test_seedance2_skips_keyframe_pipeline_and_uses_ref_t2v():
    uo = UserOption.default()
    uo.video_generation_tool = VideoGenerationTool.SEEDANCE_2_FAST_I2V_TURBO
    assert should_skip_keyframe_pipeline(uo) is True
    assert should_use_reference_to_video(uo) is True
    assert resolve_shot_workflow_mode(uo) == SHOT_WORKFLOW_REFERENCE_T2V


def test_pollo_seedance_keeps_keyframe_pipeline():
    uo = UserOption.default()
    uo.video_generation_tool = VideoGenerationTool.POLLO_SEEDANCE
    assert should_skip_keyframe_pipeline(uo) is False
    assert should_use_reference_to_video(uo) is False


def test_strip_storyboards_from_path():
    path, _ = build_product_launch_workflow_path()
    stripped = strip_storyboards_from_workflow_path(path)
    assert "storyboards" not in _ids(stripped)
    assert "shots" in _ids(stripped)
    applied = apply_shot_workflow_to_path(path, SHOT_WORKFLOW_REFERENCE_T2V)
    assert _ids(applied) == _ids(stripped)


def test_should_skip_keyframe_from_state_mode():
    assert should_skip_keyframe_pipeline_from_state(
        {"shot_workflow_mode": SHOT_WORKFLOW_REFERENCE_T2V}
    ) is True
    assert should_skip_keyframe_pipeline_from_state(
        {"shot_workflow_mode": "keyframe_i2v"}
    ) is False
