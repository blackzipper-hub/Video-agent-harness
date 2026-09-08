"""Prompt configuration remains loadable after retiring legacy Agent services."""
import os
from pathlib import Path
import subprocess
import sys


def test_prompt_configuration_does_not_import_retired_keyframe_executor():
    code = '''
import sys
import prompts.prompt_config
import app.services.agent.utils.llm_resilience
assert "app.services.agent.video.keyframe_generation_service" not in sys.modules
from app.schemas.keyframe_prompt import BatchKeyframePromptResult
result = BatchKeyframePromptResult(prompts=[{"shot_number": 1, "t2i_prompt": "cat"}])
assert result.prompts[0].all_ref_urls == []
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        env=os.environ.copy(), capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stderr


def test_standalone_defaults_keep_continuous_planning_after_restart():
    env = os.environ.copy()
    for key in ("VIDEO_STAGED_PLANNING_ENABLED", "VIDEO_CONTINUOUS_PLAN_PATCH_ENABLED"):
        env.pop(key, None)
    code = '''
import app.video_runtime.standalone
from app.video_runtime.runtime import VideoBuildRuntime
runtime = VideoBuildRuntime()
assert runtime.staged_planning_enabled
assert runtime.continuous_plan_patch_enabled
'''
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2], env=env,
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stderr
