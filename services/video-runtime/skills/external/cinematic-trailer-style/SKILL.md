---
name: cinematic-trailer-style
metadata:
  version: "1.0.0"
  roles: [guidance]
  scope:
    type: stage
  selectors:
    capabilities:
      - atomic.text.generate
      - atomic.image.generate
      - atomic.video.generate
      - story.generate
      - outline.generate
      - character.generate
      - scene.generate
      - shot.generate
      - keyframe.generate
      - image.generate
      - shot.video.generate
      - video.generate
      - video_gen.generate
  hooks: [before_stage]
description: >-
  复刻电影预告片的高级叙事视觉:较长镜头(平均 8–11 秒 / 每分钟仅 4–7 切)、
  低饱和克制的暖调/teal-orange 电影色、缓慢推进的氛围与留白。当用户想要"电影感 / 预告片风 /
  高级感 / 叙事氛围片 / 沉稳大气"时使用。
  【不适用】要炸、要快、要高饱和舞蹈 MV → 用 kpop-mv-style。
  本 skill 提供可直接落到生成 brief 的数值目标(镜头时长/色调/运动)。
---

# cinematic-trailer-style — 复刻电影预告的高级叙事感

> 数据来源:2 支电影预告片量化统计。数值为中位数 + 区间,是复刻的目标锚点。

## 1. 剪辑节奏(慢而有呼吸)
- **平均镜头时长(ASL)≈ 10 秒**(区间 8–11s),是 K-pop MV 的 8–10 倍。
- **每分钟切点仅 5–6**。镜头少而每个都"站得住"。
- 结构上前松后紧:开场长镜头铺气氛,临近结尾切点略密、制造张力,但整体远比 MV 克制。

## 2. 镜头设计
- **单镜信息量大**:缓慢推轨 / 摇镜 / 升降,让画面自己讲述,而非靠切换。
- 大量**留白与负空间**,人物常偏置、远景;强调环境与氛围。
- 关键镜给足停留时间(3–8s),让观众"读"画面。

## 3. 色彩(低饱和 · 电影调)
- **饱和度目标 0.23**(约为 MV 的一半),**对比度 0.15**、**colorfulness 14–23**(明显更收敛)。
- **色板方向**:暖调 teal-orange / 琥珀 / 大量近黑。代表色板:
  `#020202 纯黑` · `#25211b 暗棕` · `#564332 深卡其` · `#8f7251 暖褐` · `#d6c09e 琥珀米`。
- 调色指令:压低饱和、加暖调、保留大面积暗部与阴影;避免鲜艳撞色。

## 4. 运动与能量
- **画面动态 dynamism ≈ 0.11**(低):运动缓慢、克制;推拉/摇移为主,不甩不抖。
- 靠**镜头内缓慢运动 + 长停留**营造高级感,而非快切。

## 5. 结构
- 三段式:氛围铺陈(长镜、暗、静)→ 冲突/信息展开(镜头渐密)→ 收束高潮(短促切点 + 留黑)。

## 6. 画幅 / 时长
- **16:9(或 2.35:1 宽银幕黑边更电影感)**、1080p+;预告时长通常 60–120s。

## 复刻检查清单(生成后自检)
- [ ] 平均镜头 ≥ 6s(镜头少、停留久)
- [ ] 低饱和暖调,画面不鲜艳
- [ ] 大面积暗部 + 留白负空间
- [ ] 运动缓慢克制,无快切
- [ ] 结尾切点渐密制造张力
