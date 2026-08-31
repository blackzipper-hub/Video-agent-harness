# 方案：Seedance MV（音乐脊柱 + 独立 skill）

> 日期：2026-08-12（落地版）  
> **不改**原 `seedance2/`。新建 `seedance-mv` + 三个 media capability。

---

## 架构

```text
[音乐] 上传 or music.generate
    → media.audio_analyze（master 窗 + ≤15s segments）
    → media.audio_trim（每段参考轨 + 可选 master 窗）
         ↓
[视频] seedance-mv：api.provider / ark + extract_frame + media.concat
         ↓
[终混] media.mix_audio mode=replace（原曲覆盖片内声）
```

对齐铁律：**音乐定轴 → 等长分段生成 → 原曲回贴**。

---

## 已落地

| 项 | 路径 |
|---|---|
| Skill | `skills/external/seedance-mv/` |
| 卡点 reference | `references/mv-beat-sync.md`, `mv-examples.md` |
| 分段规划 | `app/chat/v2/mv_audio.py` |
| Capabilities | `media.audio_analyze`, `media.audio_trim`, `media.mix_audio` |
| Host / executor | `host_gateway.py`, `executors.py` |
| 系统提示 | `deep_agent_runtime.py`（MV → seedance-mv） |

### Capability 要点

- **`media.audio_analyze`**：`audio_info` → master 窗（全曲 / 中心裁 / 显式起止 / smart_clip+transcription）→ 均分 ≤15s segments。
- **`media.audio_trim`**：调用 media-service `audio/trim`（可选 fade）。
- **`media.mix_audio`**：`replace` → `video_add_audio`；`overlay` → `video_mix_audio`。

### 生成音乐对齐用户时长

有 `target_duration_sec` + `transcription` 时复用 `music_smart_clip_service`；否则中心裁窗。

---

## 不改动

- `skills/external/seedance2/**`（短剧/即梦入口保持干净）
- 社区卡点材料只进 `seedance-mv/references/`

---

## 测试

- `tests/test_mv_audio.py` — 分段/开窗纯逻辑  
- `tests/test_host_gateway_and_bridge.py` — trim/analyze/mix + registry  
- `tests/test_v2_workflows.py` — seedance-mv 注册与 workflow-free caps  

---

## 后续（可选）

- [ ] librosa beatgrid（HyperFrames 级）替换均分 segments  
- [ ] 前端「做 MV」默认 `$seedance-mv`  
- [ ] e2e：角色图 + mp3 → analyze → trim → provider → concat → mix  
- [ ] mix 支持 `audio_start_offset`（当前用先 trim master 代替）
