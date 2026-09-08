---
name: h3
description: >-
  Instruction helper: MiniMax H3 / Hailuo H3 generate 调用（model、images、audios）。
  触发词：H3、Hailuo H3、minimax-h3、$h3.
metadata:
  kind: instruction
  version: "1.3.0"
  short-description: MiniMax H3 generate contract
---

# H3

`api.provider.generate`，`model: minimax-h3`（也可写 `h3`）。图 + 参考音频。

## 调用

- `images`：必填。这一镜用得上的身份/场/物参考。有用户上传的源、又要锁住源里的主体，源和设定图都带上；不要只给定妆一张。上一镜的画面如果要用，当参考，不是开场锁帧。`depends_on` 只排队，不代替 `images` / `reference_from_steps`。
- `audios`：该段 2–15s 参考音频。不能只给音频
- `duration`：4–15 **整数秒**
- `resolution`：`768p` 或 `2k`（不要写 1080p）
- `aspect_ratio`：竖屏 `9:16`；不传默认 `16:9`

## Never

- 只传音频不传图
- 有上传源要锁身份时，只传生成设定图、丢掉源
