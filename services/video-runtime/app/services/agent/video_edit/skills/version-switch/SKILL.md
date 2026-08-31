---
name: version-switch
description: >-
  版本切换与"选用"：一个产物有多版时切换用哪一版；重新生成会新增版本但不自动选用。
  当用户说"切换到第N镜关键帧的v1/用刚生成的那版/选用哪一版/切回旧版/全部切成最新"时使用。
  「第一版/最新版/v几」用 select_version 的 version_selector(first/latest/v<N>)，后端自动解析 UUID，无需先查。
  但按**画面内容**选版本（"更清晰/更亮/她微笑那版/衣服红色那版/背景没杂物"）时：必须先
  get_artifact_detail 取各版本图片/视频 URL，逐版 analyze_image/analyze_video 比较，基于分析结果再
  select_version——严禁不看图就猜。也在 edit-visual 级联里被引用（重生成后、继续下游前必须先对齐选用）。涉及工具 select_version。
---

# version-switch — 版本切换与选用

## 核心规则（这条也冗余写在核心 prompt，必须遵守）
各层 `regenerate_*` 都会**新增**一条版本，但**不会**自动变成「当前选用」。下游取图/取关键帧用的是
**选用中的版本**。所以：关键帧出了新版却没 `select_version`，接着 `regenerate_videos` 仍可能基于**旧选用关键帧**——必须避免。

## 选版本：先「按什么标准」，再「怎么表达」——两步分开想
切版本永远是这两步，别混：

### 第一步：按什么标准选（criterion，可任意）
- **位置/序号**：第一个、最新、第二个、倒数第二、上一版…
- **版本号**：v2、v3…
- **画面内容**（描述性，看图才知道）：她**微笑**那版 / 衣服**红色** / 画面**更亮更清晰** / 背景**没杂物**那版…
  → 这类**必须真的看图**：`get_artifact_detail(uuid)` 会给出**每个版本的图片/视频 URL**，
    对候选版本逐个 `analyze_image`/`analyze_video`，**基于分析结果**判断哪版符合。
    ⚠️ 不许不看图就猜，也不许嘴上说“更清晰”其实没分析。
- **一致性**：关键帧版本里记了它用的角色版本，需要时据此对齐。

### 第二步：把选中的那版表达给 select_version（用最稳的 handle）
- **首选 `version_selector`**：把结论写成 `v1`/`v2`/`first`/`latest`/`第二个` 等——短、稳、后端解析。
  即使你是靠“看图”决定的，最后也用 `version_selector="v2"` 表达，**不要**去手抄那一长串 uuid。
- 仅当你手里已有确切 uuid（刚从详情原样读到、能保证不抄错）才用 `version_uuid`。
  ⚠️ 长 UUID 极易抄错，能用 selector 就别用 uuid。

> 一句话：**标准可以任意（序号/画面/一致性），但表达统一用 selector（vN）最稳**；看图才知道的标准必须先 analyze 再下结论，不能瞎猜。

## 怎么切（能自助就别问用户要 UUID）
`select_version(entity_type, entity_uuid, version_selector=… 或 version_uuid=…)`
- entity_type：`character` / `keyframe` / `video`
- entity_uuid：产物主记录 UUID（从 `get_artifact_detail` 列表拿）
- 二选一指定版本：
  - **version_selector**（推荐）：把用户原话里的版本指代**直接填进去**，后端解析成真实 UUID 再写库，你**不必**先查。
    支持：`first`/最早/第一个、`latest`/最新/刚生成、`第二个`/`第三个`/`v2`/`v3`（第几个都行）、`倒数第二`、`上一版`/旧版、`下一版`。
  - version_uuid：你确切知道某个真实版本 UUID 时用（如刚从详情读到）。
- 严禁把 `latest`/`first`/`第二个`/`v2` 之类**词**塞进 version_uuid；这类语义一律走 version_selector。

### 常见 case
1. 「所有角色切回第一个版本」→ `get_artifact_detail(character)` 列出全部角色 uuid → 对**每个** uuid 调
   `select_version(character, uuid, version_selector="first")`。别停下来问用户要 ID。
2. 「全部切成最新」→ 同上，`version_selector="latest"`。
3. 「所有角色切到第二个版本」→ 同上，`version_selector="第二个"`（或 `v2`）。第几个都能传。
4. 「切到第 3 镜关键帧的 v1」→ `get_artifact_detail(keyframe, shot_number=3)` 取 keyframe uuid →
   `select_version(keyframe, uuid, version_selector="v1")`。
5. 「用刚生成的那版」→ `version_selector="latest"`；「退回上一版」→ `version_selector="上一版"`。

## 对用户说版本时
说「第几版 / v几」必须**同时标明是视觉元素/关键帧/视频哪一类**产物的版本，别只丢一个 v2 让用户猜。

## 选用与下游（视觉元素/关键帧/视频三层同一逻辑）
- 任意一层刚生成新版、还要继续往下游走（更新关键帧/重跑视频）→ **先问用户**是否选用刚生成这版（或说明还有其它版）；确认后 `select_version` 再动下一步。
- 用户已说清整条链路（如「用刚出的继续」）→ 可一句短确认或视为同意选用最新，`select_version` 到对应层最新 version 后执行下游；回复须写明**选用的是哪一层的新结果**。
- 三层都要遵守「新生成 → 选用对齐 → 再动下游」，不要只盯关键帧一层。

## 版本级关联（判断为何会不一致）
- 关键帧版本记录了它用的角色版本（character_version_ids）；视频版本记录了它用的关键帧版本（keyframe_version_ids）。
- 角色生成了新版但关键帧还用旧角色版本生成 → 画面不一致。

## 与事实一致（必须遵守）
选用尚未切到新版前，**禁止**向用户说「视频已按新画面/新衣服生成」；须说明当前选用状态，或先 `select_version` 再 `regenerate_videos`。
