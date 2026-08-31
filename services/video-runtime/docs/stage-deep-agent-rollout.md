# Stage deep-agent rollout

Creative LLM stages always use `create_deep_agent` + skills + local JSON artifacts.
There are **no** `*_VIA_DEEP_AGENT` flags.

See also: `docs/stage-artifact-layout.md`, `app/services/agent/stage_runtime/registry.py`.

| Stage | Skill dir | Artifact | Production wired | Notes |
|-------|-----------|----------|------------------|-------|
| analysis | analysis | analysis.json | yes | `video_analysis_service.analyze_user_input` |
| music intent | music | music_intent.json | yes | `music_generation_service.analyze_music_intent` |
| outline | outline | outline.json | yes | |
| character | character | characters.json | yes | profiles; images Program |
| scene | scene | scenes_*.json | yes | video- + audio-driven deep-agent |
| music_bgm | music_bgm | bgm.json | **yes** | `generate_single_suno_music(needs_lyrics=False)` → DA prompt + Suno Program |
| visual_match | visual_match | visual_match.json | yes | |
| character_fusion | character_fusion | fusion.json | **yes (plan)** | `decide_fusion_combinations_via_deep_agent`; image gen still mustache Program |
| storyboard | storyboard | storyboard_*.json | yes | per batch |
| first_frame | first_frame | first_frame.json | yes | |
| keyframe | keyframe | keyframes_*.json | yes | prompts; images Program |
| keyframe_eval | keyframe_eval | keyframe_eval_*.json | yes | T2I eval/fix |
| keyframe_reflection | keyframe_reflection | keyframe_reflection_*.json | yes | VLM; regen Program |
| image_consistency | image_consistency | image_consistency.json | yes | I2I VLM |
| narration | narration | narration.json | **yes (plan)** | `_run_narration_plan_deep_agent`; TTS via `narration-tool-director` skill + react agent |
| video | video | video_prompts_*.json | yes | prompts; video tools Program |
| video_eval | video_eval | video_eval_*.json | yes | I2V eval/fix |
| video_consistency | video_consistency | video_consistency.json | yes | I2V VLM |

## Invoke paths (production)

| Stage | Entry | Deep-agent fn | Program follow-up |
|-------|-------|---------------|-------------------|
| music_bgm | LangGraph `music_bgm_generation` → `generate_single_suno_music(..., needs_lyrics=False)` | `music_stage.generate_music_bgm_prompt_via_deep_agent` | `suno._generate_music_with_suno_impl` |
| narration plan | LangGraph `narration_generation` → `_run_narration_plan_deep_agent` | `narration_stage.generate_narration_plan_via_deep_agent` | per-shot TTS react agent + `narration-tool-director` skill |
| character_fusion plan | `character_fusion_node` → `generate_fusion_images_for_scenes` | `character_fusion_stage.generate_fusion_plan_via_deep_agent` | `generate_character_fusion_image` (mustache I2I agent, unchanged) |

Mustache files **kept on disk** for rollback; BGM craft no longer loads `video_music_bgm_generation.mustache`. Narration plan no longer loads `video_narration_generation.mustache` (TTS uses skill body instead).

## Parity notes (mustache → deep brief)

Critical fields restored in briefs / multimodal attach:

- **keyframe / video**: full shot fields + reference/keyframe image URLs
- **keyframe_reflection**: character ref images + keyframe
- **character**: `user_input`, `hidden_style` / `style_guidance_for_visual`, `audio_info`
- **storyboard**: moderation, lip-sync/product flags, richer character/scene fields, ref images
- **scene**: prior/following chapters + reference images
- **analysis / music**: `history_summary` from recent messages

Media generation (T2I/I2V/TTS/Suno) stays Program-side (tool craft in `*-tool-director` SKILL.md via `skill_text`).

**Regenerate (workflow):** `instruction-merge-director`, `suggest-presets-{character,keyframe,video}-director` — no mustache.

**Chat / agent_router mustaches:** deferred (not migrated in this pass).

**Deleted:** workflow `prompts/video/**/*.mustache` craft templates (except `utils/generate_completion_message.mustache` still used by chat).

## Runtime tips

- DeepAgents `read_file` defaults to **100 lines** — stage system prompts require `limit=2000` so JSON briefs are not truncated.
- Skill frontmatter `name` must be hyphen-only (`first-frame-director`, not `first_frame-director`).
