from app.models.user_options import UserOption, VideoGenerationTool
from app.services.agent.video.music_generation_service import (
    SHOT_WORKFLOW_REFERENCE_T2V,
    resolve_shot_workflow_mode,
)


def test_seedance_stage_resolves_reference_t2v_without_keyframes():
    option = UserOption(video_generation_tool=VideoGenerationTool.SEEDANCE_2_I2V)
    assert resolve_shot_workflow_mode(option) == SHOT_WORKFLOW_REFERENCE_T2V
