# VideoAgent Node → Skill + Tool + Program 拆分方案（已填充版）

> Status: **草案已填充 / 待你逐节点拍板**  
> Date: 2026-07-30  
> Scope: `_build_graph` 全部 25 node  
> 原则: LangGraph 控流；Skill=知识；Tool=可调用能力；Program=薄编排  
> 约定: 评审结论为**建议值**，你改了再勾 `[x]`

**结论速查**

| # | Node | 建议 |
|---|------|------|
| 01 | user_input_analysis | **Split** |
| 02 | music_generation | **Split** |
| 03 | gate_after_music | **Keep-thin** |
| 04 | video_analysis | **Extract-tools-only** |
| 05 | outline_generation | **Extract-tools-only** |
| 06 | gate_after_outline | **Keep-thin** |
| 07 | main_character_design | **Split** |
| 08 | gate_after_character | **Keep-thin** |
| 09 | scene_generation | **Extract-tools-only** |
| 10 | music_bgm_generation | **Rewire** |
| 11 | visual_elements_matching | **Extract-tools-only** |
| 12 | character_fusion | **Extract-tools-only** |
| 13 | storyboard_detail_generation | **Split** |
| 14 | gate_after_storyboard_detail | **Keep-thin**（对齐 failure） |
| 15 | storyboard_first_frame_revision | **Extract-tools-only** |
| 16 | per_shot_generation_routing | **Keep-thin**（范本） |
| 17 | keyframe_generation | **Split** |
| 18 | keyframe_reflection | **Split** |
| 19 | gate_after_keyframe_reflection | **Rewire** |
| 20 | narration_generation | **Extract-tools-only** |
| 21 | video_generation | **Split** |
| 22 | gate_after_shots | **Keep-thin** |
| 23 | video_segments | **Extract-tools-only** |
| 24 | video_assembly | **Split** |
| 25 | audio_effect_generation | **Rewire**（或产品确认 Delete） |

图拓扑与总原则见文末附录；下面按节点展开。

---

## N01 — `user_input_analysis` `[ ]`

**建议: Split（P0）**

### 现状

有视频时：Gemini 分析 → 拼进 `user_input` 文本 → media-service 抽音频挂到 `audio_files`。  
无媒体：直接 `messages: []`。  
**注意：** `music_intent` / `workflow_path` 等实际在 wrapper（`resolve_video_workflow_at_user_input`）里写，不全在本 service。

### State

| In | Out（service） | Out（wrapper 额外） |
|----|----------------|---------------------|
| `user_input_data`, ids, `messages` | `user_input_data`, `messages` | `music_intent`, `music_workflow_mode`, `workflow_path`, `generation_config`, `actual_target_duration` |

### 现有可提为 Tool 的函数

- `analyze_video_with_gemini` → Tool
- `_extract_audio_from_video` → Tool
- `_convert_video_analysis_to_text` → 纯程序 helper
- `resolve_video_workflow_at_user_input` / `analyze_music_intent` / early content_category（在 music / wrapper）→ 建议并入本 stage 的编排或独立 tools

### 目标拆法

**Program（薄 node）**
1. 无 video/audio → return 空 messages  
2. 有 video → `analyze_reference_video` → 文本拼进 user_input  
3. → `extract_audio_from_video` → 追加 audio_files  
4. （建议迁入）`infer_content_category` + `analyze_music_intent` + `build_workflow_path`  
5. return 更新后的 user_input_data + 路由字段 + messages  

**Tools**
```python
async def analyze_reference_video(video_url, user_input, log_context) -> tuple[GeminiVideoAnalysisResult, list[BaseMessage]]
async def extract_audio_from_video(video_url) -> Optional[str]
async def infer_content_category(...) -> ContentCategoryEarlyOutput
async def analyze_music_intent(user_input, history) -> tuple[MusicIntentType, list[BaseMessage]]
def build_video_workflow_path(...) -> tuple[list[dict], str]
```

**Skill** `stages/user-input/SKILL.md`
- When: pipeline 入口；有参考视频时必须分析  
- Rules: 只处理第一个 video；抽轨失败不阻断；intent→mode 映射表  
- Checklist: enhanced text 有「参考视频风格」段；audio_files 已挂；workflow_path 已 emit  

### 多 pipeline

入口几乎都复用；instant 可跳过 music intent。

### 评审结论（建议）

- **Split** — Gemini / 抽轨 / intent / path 四件事挤在入口  
- 备注: wrapper 与 service 边界一并理清  

---

## N02 — `music_generation` `[ ]`

**建议: Split（P0）**

### 现状

mode 分支：`user_upload` | `suno_then_transcribe` | 延后 BGM | PL skip。含 Suno agent、转写 sections、smart_clip 落库。

### State

| In | Out |
|----|-----|
| `user_input_data`, `music_*`, `messages`, `detected_language`, ids | `audio_transcription_uuids`, `music_generation_uuids`, `actual_target_duration`, `generation_config`, `messages` |

### 现有函数 → Tool

- `generate_single_suno_music`
- `analyze_music_intent`（若入口没跑）
- `transcribe_audio_for_analysis`（smart_clip_flow）
- `run_smart_clip_analysis` / `fetch_smart_clip_peaks` / `build_smart_clip_ready_payload`
- persist: `create_music_generation*` / transcription CRUD

### Program 保留

1. 读 mode；PL / none_sora 早退  
2. upload → 只转写；suno → `generate_suno` 再转写  
3. 写 duration / generation_config  
4. 可选 smart_clip 挂 music.additional_data  
5. emit MUSIC_GENERATED；stage_failure；return UUIDs  

**Skill** `stages/music/SKILL.md`  
When/Rules: lyrics_provided vs auto vs instrumental；何时 smart_clip；何时 defer BGM。

### 评审结论

- **Split**  
- 备注: smart_clip 与 gate_after_music 强耦合，拆 tool 后 gate 只读 payload  

---

## N03 — `gate_after_music` `[ ]`

**建议: Keep-thin（P2）**

### Program

1. `full_auto` 或 `should_skip_gate_after_music` → `{}`  
2. `_blocking_failure_payload` → interrupt 失败  
3. 估 credit/time；带上 smart_clip  
4. `interrupt(after_music)`  

可选 Tool: `estimate_gate_cost_time(state, step)`（各 gate 共用）。

Skill: 仅确认文案（可选）。

### 评审结论

- **Keep-thin**

---

## N04 — `video_analysis` `[ ]`

**建议: Extract-tools-only（P1）**

### 现有 → Tool

- `build_prompt_for_video_analysis_node`
- `_analyze_video_requirements` → `analyze_video_requirements`
- persist analysis + merge content_category

### Program

1. 读 transcription / duration / user_input  
2. 调 `analyze_video_requirements`  
3. 写 `video_analyses`；更新 generation_config  
4. emit；return `analysis_uuid`  

**Skill** `stages/video-analysis/SKILL.md` — category 规则、有无音频时字段差异。  
**Schema** `VideoAnalysisResult`（Pydantic SoT）。

### 评审结论

- **Extract-tools-only**

---

## N05 — `outline_generation` `[ ]`

**建议: Extract-tools-only（P1）**

### 现有 → Tool

- `build_prompt_for_outline_generation`
- `_generate_outline_with_agent` → `generate_story_outline`
- `_convert_llm_mode_outline_to_outline` / `_process_audio_driven_outline` / duration normalize（可留 program 或并入 tool 后处理）

### Program

1. 幂等：thread 已有 outline → 复用 UUID  
2. `get_video_analysis_from_db` + transcription  
3. 调 generate tool（audio/video path）  
4. CRUD outline+chapters；emit；return `story_outline_uuid`  

**Skill** `stages/outline/SKILL.md` — 章节-音频段对齐、时长。  
**Schema** `StoryOutlineForLLMMode`。

### 评审结论

- **Extract-tools-only**

---

## N06 — `gate_after_outline` `[ ]`

**建议: Keep-thin** — 标准 interrupt。

---

## N07 — `main_character_design` `[ ]`

**建议: Split（P0）**

### 现有 → Tool

- `_generate_characters_from_story` → `design_character_profiles`
- `_match_characters_with_user_images` → `match_upload_to_characters`
- `_generate_character_image_with_llm` / `batch_generate_character_images` → `generate_character_image`
- `generate_multi_view_character_sheet` → `generate_multi_view_sheet`
- `_save_character_to_database` → persist helper

### Program

1. 载 outline / uploads；幂等按 name skip  
2. profiles → match → 分支（reuse / style-regen / isolate / T2I）  
3. 批量生图 + 可选 multiview  
4. 合并 `character_uuids`；stage_failure；emit CHARACTERS_DESIGNED  

**Skill** `stages/character/SKILL.md` — person/prop/location；匹配策略；何时 multiview。

### 评审结论

- **Split**

---

## N08 — `gate_after_character` `[ ]`

**建议: Keep-thin**

---

## N09 — `scene_generation` `[ ]`

**建议: Extract-tools-only（P1）**

### 现有 → Tool

- `_generate_scenes_for_chapter_with_llm` → `generate_scenes_for_chapter`
- `_compute_scene_structure_for_chapter` / `_validate_and_convert_audio_driven_scenes`

### Program

1. 载 outline/characters/transcription；算 allow_lipsync  
2. 按 chapter 并行调 tool  
3. persist scenes；emit；return `scene_uuids`  

**Skill** `stages/scene/SKILL.md` — 槽位对齐、时长和、lipsync 标记。

### 评审结论

- **Extract-tools-only**

---

## N10 — `music_bgm_generation` `[ ]`

**建议: Rewire（P1）**

### 问题

Launcher `create_task` 进 `_bgm_tasks[run_id]`，**不在 checkpoint**；join 藏在 N19 / assembly。

### 目标

| Node | 职责 |
|------|------|
| `music_bgm_launcher` | 检查 config → 启动任务 → `{}` |
| `await_bgm_join`（新） | await → 写 `music_generation_uuids` |
| Tool | `generate_bgm` = `generate_single_suno_music(..., needs_lyrics=False)` |

**Skill**: 挂 music skill「何时并行 BGM」。

### 评审结论

- **Rewire** — 修 resume/多 worker 隐患  

---

## N11 — `visual_elements_matching` `[ ]`

**建议: Extract-tools-only（P2）**

### 现有 → Tool

- `match_and_update_visual_elements_for_batch` → `match_visual_elements_to_scenes`

### Program

1. `SKIP_VISUAL_ELEMENTS_MATCHING` 则跳过  
2. 调 tool；DB 更新 scene.character_ids  
3. **建议** state 也回写显式字段（便于测）  

### 评审结论

- **Extract-tools-only**

---

## N12 — `character_fusion` `[ ]`

**建议: Extract-tools-only（P1）**

### 现有 → Tool

- `generate_fusion_images_for_scenes`（edit regenerate 已复用）
- `decide_fusion_combinations_for_scenes` / `generate_character_fusion_image`

### Program

1. `ENABLE_FUSION` 门控  
2. 调 fusion tool；结果在 DB  
3. return messages；图级 fan-in 进 keyframe  

**Skill** — 模型 ref 上限、MAIN vs MULTIVIEW、何时跳过。

### 评审结论

- **Extract-tools-only**

---

## N13 — `storyboard_detail_generation` `[ ]`

**建议: Split（P1）**

### 现状

batch LLM + persist 缠在 node 里，缺独立 `_generate_*` 导出。

### 目标 Tool

```python
async def generate_shot_details(outline, scenes_batch, characters, ...) -> tuple[list[DetailedShot], list[BaseMessage]]
```

### Program

幂等复用 → batch 调 tool → 继承 duration/character/audio_segment → CRUD → emit → `shot_uuids`

**Skill** `stages/storyboard/SKILL.md` + Schema `StoryboardDetailLLMOutput`

### 评审结论

- **Split**

---

## N14 — `gate_after_storyboard_detail` `[ ]`

**建议: Keep-thin**

补齐与其他 gate 一致的 `stage_failure` 检查（小修，非大拆）。

---

## N15 — `storyboard_first_frame_revision` `[ ]`

**建议: Extract-tools-only（P2）**

### 现有 → Tool

- `_revision_batch` → `revise_first_frame_prompts`  
- Schema: `FirstFrameRevisionOutput`

### Program

`SKIP_*` → 载 shots → 并发 batch tool → persist → messages

**Skill** — I2V/合规首帧清单（很适合 skill）。

### 评审结论

- **Extract-tools-only**

---

## N16 — `per_shot_generation_routing` `[ ]`

**建议: Keep-thin（范本，P2）**

### 现状

**无 LLM**。`assign_generation_mode_to_shots` → 写 DB。

### 目标

Program 保持规则引擎；可选暴露 tool 给 edit agent：

```python
def assign_shot_generation_modes(shots, transcription, ..., allow_lipsync=True) -> list
```

**Skill** — empty/normal/lipsync 策略（给人 + edit 读）。

### 评审结论

- **Keep-thin** — 其它 node 应对齐这种薄度  

---

## N17 — `keyframe_generation` `[ ]`

**建议: Split（P0）**

### 现有 → Tool

- `_build_character_images_dict` / `create_and_upload_reference_sheet` / `resolve_keyframe_prompt_reference_images`
- `generate_batch_keyframe_prompts` / `evaluate_and_fix_batch_keyframe_prompts`
- `execute_batch_keyframe_generation` / `generate_batch_keyframes`（组合 tool）

### Program

1. fan-in 后载 shots + characters + fusions  
2. 幂等 skip 已完成 keyframe  
3. 组 refs → write prompts → execute images  
4. progress / stage_failure / return `keyframe_uuids`  

**Skill** `stages/keyframe/SKILL.md` — ref 优先级（fusion>sheet>single）、连续性。

### 评审结论

- **Split**

---

## N18 — `keyframe_reflection` `[ ]`

**建议: Split（P0）**

### 现有 → Tool

- `reflect_single_keyframe` / `reflect_all_keyframes`
- `regenerate_single_keyframe`（复用 N17）
- `_save_reflection_to_db`

### Program

开关 off → skip；iteration loop（max / threshold）；progress；写 `reflection_*` + 更新 keyframe_uuids

**Skill** — 一致性 rubric、何时重生、停止条件。

### 评审结论

- **Split**

---

## N19 — `gate_after_keyframe_reflection` `[ ]`

**建议: Rewire（P1）**

### 问题

Gate 里 await BGM + interrupt + failure + estimate。

### 目标

```
... → keyframe_reflection → await_bgm_join → gate_after_keyframe_reflection → ...
```

Gate 只做 interrupt；`build_gate_after_bgm_ready_interrupt_payload` 仅组文案。

### 评审结论

- **Rewire**（与 N10 一起做）

---

## N20 — `narration_generation` `[ ]`

**建议: Extract-tools-only（P1）**

### 现有 → Tool

- `_filter_shots_for_narration_tts`
- `_generate_single_narration` → `generate_shot_tts`（speech ToolService）

### Program

config off → skip；filter；幂等；并行 TTS；persist；return `narration_uuids`

**Skill** — 空镜跳过、PL 例外、音色规则。

### 评审结论

- **Extract-tools-only**

---

## N21 — `video_generation` `[ ]`

**建议: Split（P0）**

### 现有 → Tool

- `detect_and_build_style_context`
- `generate_batch_video_prompts` / `evaluate_and_fix_batch_prompts`
- `execute_single_video` / `execute_batch_video_generation` / `generate_batch_videos`
- `_build_lipsync_audio_url_map*`

### Program

载 keyframe/shot/music/narration；组 lipsync map；幂等；batch execute；stage_failure；return `video_generation_uuids`

**Skill** `stages/video-gen/SKILL.md` — lipsync 音频源、空镜、I2V 锁首帧。

### 评审结论

- **Split**

---

## N22 — `gate_after_shots` `[ ]`

**建议: Keep-thin**

---

## N23 — `video_segments` `[ ]`

**建议: Extract-tools-only（P1）**

### 现有 → Tool（无 LLM）

- `_analyze_audio_video_mapping` / `_process_segments_parallel` / `_process_segments_video_driven`
- `process_segment_by_request` / `merge_segment_videos` / duration align helpers

### Program

组 `VideoAssemblyData` → 选 audio/video-driven → 调 tools → DB → `video_segments_uuids`

**Skill** — 时间线映射规则。

### 评审结论

- **Extract-tools-only**

---

## N24 — `video_assembly` `[ ]`

**建议: Split（P0）**

### 现有 → Tool

- `execute_assembly_strategy` / `concatenate_segments_*`
- `add_background_music_to_video` / `mix_narration_with_video`
- `_burn_subtitles_onto_video_url` / `_add_watermark_to_video`
- loaders: narrations / effects / music

### Program

（过渡期）fallback await BGM → 载全产物 → strategy → persist → VIDEO_COMPLETED → `video_assembly_uuid` + `final_video_url`

**Skill** `stages/assembly/SKILL.md` — upload vs suno vs narration 混音。

### 评审结论

- **Split**；BGM fallback 在 N10/N19 rewire 后删除  

---

## N25 — `audio_effect_generation`（orphan） `[ ]`

**建议: Rewire（P2）**

### 现状

`generate_single_audio_effect` 已实现；**无边**；assembly 仍可读 effects。

### 选项

| 选项 | 说明 |
|------|------|
| **Rewire** | 挂在 `video_generation` 后或 `assembly` 前（推荐，若产品要 SFX） |
| Tool-only | 不入主图，edit agent 按需调 |
| Delete | 产品确认永不做 SFX 再删 |

```python
async def generate_shot_audio_effect(video_gen, video_version, ...) -> tuple[AudioEffectVersion, list[BaseMessage]]
```

### 评审结论

- **Rewire**（或等产品拍板）

---

## 落地顺序（填充后建议）

1. **契约评审**：你按 N01→N25 勾结论（可改 Split/Keep…）  
2. **P0 Split 抽 Tool（不改边）**：N01/02/07/17/18/21/24  
3. **Rewire**：N10 + N19（BGM join）  
4. **Extract-tools-only 批量**：N04/05/09/11/12/15/20/23  
5. **Stage SKILL.md**：优先 outline / storyboard / keyframe / video-gen / assembly / character  
6. **pipeline YAML + NodeRegistry**（图边声明化）  
7. **N25** 产品决策  

---

## 附录 A — 三层职责 & State 穿法

见原原则：SoT=DB；state=UUID+路由；messages=LLM 痕迹非 UI；事件=旁路。

## 附录 B — 图拓扑

```
user_input_analysis → music_generation → gate_after_music → video_analysis
  → outline → gate_outline → character → gate_character
  → scene → music_bgm_launcher → visual_match
       ├→ storyboard → gate → first_frame → routing
       │     ├→ keyframe → reflection → [await_bgm] → gate_keyframe ─┐
       │     └→ narration ──────────────────────────────────────────┤→ video_gen
       └→ fusion ──────────(fan-in keyframe)────────────────────────┘
  → video_gen → gate_shots → segments → assembly
```

## 附录 C — 代码入口

- Graph: `video_agent_service.py` `_build_graph`
- Nodes: `app/services/agent/video/*_service.py`
- Schema: `video_state.py`, `prompt_config.py`
- Edit skills 对照: `video_edit/skills/*/SKILL.md`

## 附录 D — 评审记录

| 日期 | Node | 你的结论 | 备注 |
|------|------|----------|------|
| | | | |
