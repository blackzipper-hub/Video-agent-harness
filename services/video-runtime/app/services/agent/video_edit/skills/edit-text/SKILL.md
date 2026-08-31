---
name: edit-text
description: >-
  改文字内容与全局设定（不是重画单镜画面）：场景剧情文本、大纲全局信息(标题/描述/主题/核心信息/视觉风格style)。
  当用户说"把第N个场景改成…/加一句台词/改故事梗概/改标题"时用 update_scene 或 modify_outline。
  【重要路由·涉及就要改】"改成XX风格 / 换画风 / 整体色调 / 全片风格" → modify_outline，
  fields 必须含 style；整换风时对照快照中的标题/主题/描述——冲突的都要放进 fields
  （典型 style+description+theme+title），禁止只 style。含 style/description/theme 时章节自动联动。
  若场景已生成且氛围冲突 → 再 update_scene。不要用 regenerate_keyframes。
  涉及工具 update_scene / modify_outline。
---

# edit-text — 改文本（场景 / 大纲）

先判断层次，别混用：改文本用本 skill；改画面外观用 edit-visual skill。

## 层次（先认清，再选工具）

| 层 | 字段 / 产物 | 谁改 |
|---|---|---|
| 大纲全局 | `style` `description` `theme` `title` `key_message`；（章节在含 style/description/theme 时**自动联动**） | `modify_outline` |
| 场景脚本 | 各 scene 的剧情描述（及下属镜头脚本） | `update_scene`（**不**在 modify_outline 的 fields 里） |
| 画面/视频 | 关键帧、视频、角色参考图 | edit-visual（regenerate_*）— 文本改完再问要不要重画 |

## 某个场景的剧情文本 → update_scene
`update_scene(scene_number=N, instruction="…")`：在当前场景文本基础上**融合**修改，并自动同步到该场景下的镜头脚本。
- scene_number 从 1 开始。改前可 `get_artifact_detail(artifact_type=scene[, scene_number=N])` 看当前文本。
- **不要**用 modify_outline 改单个场景。镜头脚本不单独编辑，跟随场景同步。

## 大纲全局信息 → modify_outline
`modify_outline(instruction="…", fields=[…])`。fields 由你判断「本次意图牵动了哪些字段」：

| 用户要改 | fields | 写入 |
|---|---|---|
| 视觉风格/画风/色调/"改成XX风格" | 必须含 **`style`**；再按「涉及就要改」加 description/theme/title/key_message | style_guide + 风格标签 +（选中的）全局文案；**各章节自动联动** |
| 故事梗概/情节 | `description` | 大纲描述 + 各章节(联动) |
| 主题 | `theme` | 主题 + 各章节(联动) |
| 仅改章节文本 | `chapters` | 各章节 title/description |
| 标题/核心信息 | `title` / `key_message` | 对应字段(不改章节) |

### 原则：涉及就要改（由你判断，不要半改）

先用快照/产物详情看当前文案，再决定 fields——**牵动到的都改，没牵动的不滥加**。

**整风格切换（改成恐怖 / 赛博朋克 / 末日…）——对照清单：**

| 字段 | 何时算「涉及」 | 典型「温暖→恐怖」例子 |
|---|---|---|
| `style` | **必选** | 风格标签 / style_guide |
| `description` | 整体叙述语气/氛围被牵动时（整换风**几乎总是**） | 「治愈能量…轻松愉悦」仍温馨 → 要改 |
| `theme` | 主题词与新风冲突时（整换风**常见**） | 「发现日常中的美好与治愈」→ 要改 |
| `title` | 标题语义仍是旧风时（整换风**常见**） | 「温暖安然的日常」→ 要改 |
| `key_message` | 核心口号带着旧情绪时 | 「珍惜当下，感受温暖与平静」→ 要改 |
| 章节 | **不必**手写进 fields | 含 style/description/theme 时后端自动改各章 title/description |
| 场景 scene | **不在** fields 里；若场景**已生成**且描写仍是旧风 | 对每个冲突场景再调 `update_scene`；若尚未生成场景（常见 after_outline）→ 跳过并说明 |

- 整换风的**典型 fields**：`["style","description","theme","title"]`；若 key_message 也被牵动再加上。只传 `["style"]` / 只传 `["style","description"]` 却留下旧标题/旧主题，仍是半改。
- 「色调冷一点 / 稍微暗一点」且故事语义、标题、主题仍成立 → 可以只 `["style"]`。
- **禁止**用户说改风格/画风却只传 `["description"]`（更新不了风格标签）。
- 场景：`modify_outline` **不会**改 scene。有场景且冲突时，改完大纲后继续 `update_scene`；或先向用户说明「大纲已换风，场景脚本也需要同步吗？」再动手。

## mode
- `instruction`（默认）：读当前文本做基底，融合用户指令得连贯新文本。写**修改说明**即可，勿自己拼原文，勿末尾打补丁式追加。
- `direct`：仅当用户给了完整新全文时，整段替换。

改完文本若用户想让画面/视频跟上 → 先改文本，再按 edit-visual 的"重新生成后的引导"询问是否重画。
