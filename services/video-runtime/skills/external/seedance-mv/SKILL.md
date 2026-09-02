---
name: seedance-mv
description: >-
  Workflow: Seedance2 MV / 歌曲卡点 / 角色出演成片。音乐为时间轴脊柱：分析→裁切≤15s→多段生成→拼接→叠回原曲。
  Trigger: MV、歌曲、卡点、角色唱这首歌、beat sync、music video、$seedance-mv.
metadata:
  kind: workflow
  version: "1.0.1"
  workflow:
    title: Seedance MV
    mode: seedance_mv
    planning:
      mode: staged
      checkpoints:
        - id: music_ready
          after_phase: music_analysis
          next_phase: visual_production
          required_artifacts: [audiomap, audio_cut]
          resolves: [shots, captions, timeline]
          instruction: Plan the MV from the real music duration, beats, lyrics, and cut window.
    entrypoints: [text, image, audio, video]
    parameters:
      shot_workflow_mode: seedance2_script
      content_category: music_video
    pipeline:
      - atomic.music.generate
      - media.audio_analyze
      - media.audio_trim
      - atomic.image.generate
      - api.provider.generate
      - api.ark_protocol.generate
      - media.extract_frame
      - media.concat
      - media.mix_audio
    requires_keyframe: false
    allowed_capabilities:
      - actions.suggest
      - atomic.text.generate
      - atomic.image.generate
      - atomic.music.generate
      - atomic.video.generate
      - music.generate
      - api.provider.generate
      - api.ark_protocol.generate
      - media.audio_analyze
      - media.audio_trim
      - media.concat
      - media.extract_frame
      - media.mix_audio
---

# Seedance MV 工作流

你是 **MV 导演**。音乐是时间轴脊柱；画面按音乐分段生成，最后用**原曲**覆盖片内声。

**不要** `load_skill("seedance2")`。本 skill + `references/` 已自包含。
**禁止** `outline.generate` / `character.generate` / `scene.generate` / `shot.generate` / `keyframe.generate`。

卡点句式与 @ 合同见 [references/mv-beat-sync.md](references/mv-beat-sync.md)。
Provider / model 合同见 [references/mv-provider.md](references/mv-provider.md)。

## 对齐铁律（必须遵守）

1. **Music spine**：先确定 master 音频窗，再生成等长视频段。
2. Seedance 参考音频 **总时长 ≤15s**（可 ≤3 个文件）→ 必须先 `media.audio_analyze` 再 `media.audio_trim`。
3. 第 i 段生成时长 ≈ `segments[i].duration_sec`；concat 顺序与 segment 顺序一致。
4. 终片：`media.mix_audio` **`mode: replace`**，把 master 窗叠回画面（不要只靠片内 Seedance 声）。
5. 生成歌对用户目标时长：`media.audio_analyze` 传 `target_duration_sec`（有 transcription 时走 smart_clip）。

## 入口

### A. 用户已有音乐

1. 拿到 `audio_url`（上传 artifact）。
2. `media.audio_analyze`（可选 `target_duration_sec` / `start_sec`+`end_sec`）。
3. 对每个 segment：`media.audio_trim` → 得到 ≤15s 参考轨。
4. 进入「画面生成」。

### B. 需要生成音乐

1. `atomic.music.generate` / `music.generate`。
2. 若用户指定成片时长：`media.audio_analyze` + `target_duration_sec`。
3. 同 A：trim 分段 → 画面生成。

## 画面生成（Seedance）

红线：

1. 提示词**必须中文**
2. `@图片1`… / `@音频1`… 每个标清用途（节奏锚 / 定妆 / 首帧）
3. 禁止写实真人脸
4. 有角色图 → 锁定形象；有段音频 → `@音频1` 为节奏参考
5. **模型必须写全**：`provider: wavespeed` + `model: doubao-seedance-2-0`（禁止只写 `seedance` / `seedance-v2`）

多段：

1. 段 0：图 + 段音频 → `api.provider.generate`：
   `{ provider: "wavespeed", model: "doubao-seedance-2-0", duration: <segment.duration_sec>, ... }`
2. `media.extract_frame` `position: last` → 下一段**唯一首帧**（失败则重试抽帧，禁止复用开场定妆图顶替首帧）
3. 重复 generate…（每段同样带上表 `provider`/`model`）
4. `media.concat`（连续镜头可用 `transition_duration: 0.125`；硬切用 `0`）
5. `media.mix_audio`：`video_url`=成片，`audio_url`=master 窗（若 master 不是全曲，先 trim 出 master 再 mix）

## Master 窗音频

analyze 返回 `master.start_sec/end_sec`。终混前若 master 不是整轨：

```text
media.audio_trim:
  audio_url: <原曲>
  start: master.start_sec
  duration: master.duration_sec
```

再用该 trim 结果做 `media.mix_audio`。

## Never

- 把 3 分钟整曲直接塞进 Seedance
- 先乱生成视频再硬塞 BGM 指望对齐
- 走短剧 outline/shot/keyframe 链
- 改写或依赖 `seedance2/SKILL.md` 正文
- `model: "seedance"` / `"seedance-v2"` 等模糊名（bridge 会直接失败并停住等用户）
