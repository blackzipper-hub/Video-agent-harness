# MV 计划示例（capability 顺序）

## 上传 60s 歌 + 角色图

1. `media.audio_analyze` `{ audio_url, target_duration_sec: 60 }` → 4×15s segments
2. 对 segment 0..3：`media.audio_trim`
3. `api.provider.generate` 段0（图+音频0，`provider=wavespeed`，`model=doubao-seedance-2-0`，duration≈15）
4. `media.extract_frame` last → 段1 首帧
5. 重复 generate…
6. `media.concat`
7. `media.audio_trim` master 窗（若需要）→ `media.mix_audio` replace

## 生成音乐 → 45s MV

1. `atomic.music.generate`
2. `media.audio_analyze` `{ audio_url, target_duration_sec: 45 }`
3. trim 分段 → provider → concat → mix_audio replace

## 仅副歌窗

`media.audio_analyze` `{ audio_url, start_sec: 45, end_sec: 75 }`
后续相同。
