"""Creative stage deep-agent rollout registry (Program gates stay thin)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class StageSpec:
    key: str
    node: str
    skill_dir: str
    artifact_name: str
    priority: int  # lower = sooner
    notes: str = ""


# Dependency spine. Thin gates / media I/O omitted. All creative LLM stages use deep agent.
CREATIVE_STAGES: List[StageSpec] = [
    StageSpec("analysis", "video_analysis", "analysis", "analysis.json", 10),
    StageSpec("music", "music_generation", "music", "music_intent.json", 15, "intent only; Suno Program"),
    StageSpec("outline", "outline_generation", "outline", "outline.json", 20),
    StageSpec("script", "script_generation", "script", "script.json", 25, "OM beat map; between outline and scene"),
    StageSpec("character", "main_character_design", "character", "characters.json", 30, "profiles; images Program"),
    StageSpec("scene", "scene_generation", "scene", "scenes.json", 40),
    StageSpec("music_bgm", "music_bgm_generation", "music_bgm", "bgm.json", 45, "prompt package; Suno Program"),
    StageSpec("visual_match", "visual_elements_matching", "visual_match", "visual_match.json", 50),
    StageSpec("character_fusion", "character_fusion", "character_fusion", "fusion.json", 55, "decide_* + images Program"),
    StageSpec("storyboard", "storyboard_detail_generation", "storyboard", "storyboard.json", 60),
    StageSpec("first_frame", "storyboard_first_frame_revision", "first_frame", "first_frame.json", 65),
    StageSpec("keyframe", "keyframe_generation", "keyframe", "keyframes.json", 70, "prompts; images Program"),
    StageSpec("keyframe_eval", "keyframe_generation", "keyframe_eval", "keyframe_eval.json", 72, "T2I eval/fix"),
    StageSpec("keyframe_reflection", "keyframe_reflection", "keyframe_reflection", "keyframe_reflection.json", 75, "VLM; regen Program"),
    StageSpec("image_consistency", "keyframe_generation", "image_consistency", "image_consistency.json", 76, "I2I VLM"),
    StageSpec("narration", "narration_generation", "narration", "narration.json", 80, "plan package; TTS Program"),
    StageSpec("video", "video_generation", "video", "video_prompts.json", 90, "prompts; video tools Program"),
    StageSpec("video_eval", "video_generation", "video_eval", "video_eval.json", 92, "I2V eval/fix"),
    StageSpec("video_consistency", "video_generation", "video_consistency", "video_consistency.json", 94, "I2V VLM"),
]


def stage_by_key(key: str) -> StageSpec:
    for s in CREATIVE_STAGES:
        if s.key == key:
            return s
    raise KeyError(key)
