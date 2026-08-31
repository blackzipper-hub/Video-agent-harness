---
name: media-analysis
description: >-
  看实际图片/视频内容再作答。当用户描述的画面与你的文字记录不一致（如"图片里明明有三个女人"）、
  或问"这张图/这段视频里到底有什么/有几个人/是什么颜色"时使用。涉及工具 analyze_image / analyze_video。
---

# media-analysis — 图片 / 视频理解

两个 vision 工具：
- `analyze_image(image_url, question)`：分析一张图片，回答关于图片内容的问题。
- `analyze_video(video_url, question)`：分析一段视频，回答关于视频内容的问题。

## 什么时候必须用
当用户描述的画面内容与你的文字记录**不一致**时（如「图片里有三个女人」而记录只写了一个），
**优先用 analyze_image 去看实际图片**，不要仅凭文字描述回复。

## 使用流程
1. 先 `get_artifact_detail` 获取产物信息（含图片/视频 URL）。
2. 用 `analyze_image` / `analyze_video` 传入 URL 确认实际画面内容。
3. 根据**实际画面**告知用户情况并决定后续操作（要改画面转 edit-visual）。
