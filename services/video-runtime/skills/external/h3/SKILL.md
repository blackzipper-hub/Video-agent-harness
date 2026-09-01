---
name: h3
description: >-
  Instruction helper: MiniMax H3 / Hailuo H3 generate 调用（model、images、audios）。
  触发词：H3、Hailuo H3、minimax-h3、$h3.
metadata:
  kind: instruction
  version: "1.2.1"
  short-description: MiniMax H3 generate contract
---

# H3

`api.provider.generate`，`model: minimax-h3`（也可写 `h3`）。图 + 参考音频。

## 调用

- `images`：必填。定妆。多段继续带定妆；上一镜的画面如果要用，当参考，不是开场锁帧。
- `audios`：该段 2–15s 参考音频。不能只给音频
- `duration`：4–15 **整数秒**
- `resolution`：`768p` 或 `2k`（不要写 1080p）
- `aspect_ratio`：竖屏 `9:16`；不传默认 `16:9`

## Never

- 只传音频不传图
