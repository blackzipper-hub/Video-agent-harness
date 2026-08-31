# Pilot：`outline_generation` — Deep Agent Skill + Artifact（修订）

> Status: **实施中（outline node 已接线）**  
> Date: 2026-07-31  
> Node: `outline_generation`（图 N05）  
> **整体目录/存储合同:** `docs/stage-artifact-layout.md`（必读；`kit/` + `app/contracts/{llm,artifacts,db}`）  
> Spike 目录已删除（结果见 §4）；生产路径见 `outline_stage/`。  
> Creative path always uses deep-agent + skill + artifact (no feature flag).

旧 mustache 对照已移除出热路径。

---

## 修订结论（先读这个）

1. **上一版 §3.3「Program 拼 skill 成 SystemMessage」太定制化，不像 OM，已否决作为目标态。**  
2. **目标态对齐 OM + 你们已有的 Companion：`create_deep_agent` + `SKILL.md` 自动加载 + 读/写 JSON artifact。** LangGraph node 变薄：准备输入 JSON → spawn stage agent → 校验 → DB 双写。  
3. **`outline.schema.json` 不是必须另维护一份手写真相**；Pydantic/artifact model 是源，JSON Schema 可导出给「落盘校验 / 非 Python 工具 / 对齐 OM」。  
4. **LLM / Artifact / DB 三套字段应拆开整理**；outline 试点顺带重建这一层，不在本 PR 扫荡整个 `video_state.py`。  
5. **OM 的 `script` ≈ 我们的 `outline`（同阶段角色：叙事骨架 + 时长/节拍）；不是逐字段同构。**

---

## 0. 为什么先挑 outline

| 理由 | 说明 |
|------|------|
| 阶段类比清晰 | OM `script-director` ↔ VA outline |
| 已有 deep agent 先例 | `video_edit/agent.py` 已 `create_deep_agent` + skills |
| Prompt/规则可迁到 SKILL.md | 去掉 mustache 组装 |
| 落库点单一 | 好做 artifact_path 双写 |
| Spike 已证明 | 无 mustache 也能产出合法 `outline.json` |

---

## 1. Schema：还要不要 `outline.schema.json`？要不要重建？

### 1.1 你们现在实际有几套「schema」

| 层 | 例子 | 用途 |
|----|------|------|
| **LLM 输出** | `StoryOutlineForLLMMode` / `StoryChapterForLLMMode`（Pydantic，在 `video_state.py`） | structured output；字段偏创作 |
| **领域/内存** | `StoryOutline` / `StoryChapter` | 程序 normalize 后的内存对象 |
| **DB 行** | `VideoStoryOutlineDB`（msgspec，在 `app/schemas/video/video_story.py`） | 表列：还有 `themes[]`、`target_audience`、`narrative_structure`、`uuid`、`run_id`… |
| **Artifact（缺）** | 尚无统一落盘合同 | OM 有 `schemas/artifacts/script.schema.json` |

**LLM ≠ DB**：例如 DB 有 `themes: List[str]`、`target_audience`；LLM outline 是 `theme: str` + `style_guide`。CRUD 里还有「旧字段兼容」注释——说明已经混过一轮。

### 1.2 `outline.schema.json` 还需要吗？

| 做法 | 建议 |
|------|------|
| **手写第二份 JSON Schema 当真相** | ❌ 易漂 |
| **Pydantic Artifact 为真相，需要时 `model_json_schema()` 导出** | ✅ |
| **落盘 / OM 对齐 / 外部校验** | 导出到 `schemas/artifacts/outline.schema.json`（生成物或 CI 生成） |
| **Deep agent 写盘工具内** | 直接 `OutlineArtifact.model_validate` 即可，**运行时不必读 json 文件** |

所以：**类已经够用；json 文件是「对外合同/对齐 OM」的导出，不是第三套手工真相。**

### 1.3 这次要不要重建？

**要，但范围限 outline 试点（模板化），不要一次重构全库。**

建议目录（逐步搬，先 outline）：

```
app/schemas/
  llm/outline.py          # OutlineLLMOutput（现 StoryOutlineForLLMMode）
  artifacts/outline.py    # OutlineArtifact（落盘；≈ LLM + schema_version/ids）
  db/video_story.py       # 保持 VideoStoryOutlineDB（或从现文件挪）
schemas/artifacts/outline.schema.json   # 由 Artifact 导出（可选提交）
```

转换链（显式）：

```
OutlineLLMOutput  --normalize-->  OutlineArtifact  --persist-->  DB row + JSON file
```

| 字段 | LLM/Artifact | DB |
|------|--------------|-----|
| title/description/key_message/style_guide | ✅ | ✅（部分旧列名兼容） |
| chapters[] | ✅ 嵌在 artifact | ✅ 子表 `video_chapter` |
| theme vs themes[] | `theme: str` | `theme` 旧 + `themes[]` 新 |
| uuid/run_id/user_id | artifact 可带，LLM 初稿可无 | ✅ 必填 |
| target_audience / narrative_structure | 通常不在 LLM outline | DB 有，可空或从 analysis 填 |

**本试点交付：** 三套边界写清 + `OutlineArtifact` + mapper；不强制改完所有调用点命名。

---

## 2. OM script vs VA outline；为什么 OM「看起来不用组装」

### 2.1 类比

| OM | VA |
|----|-----|
| `script-director.md` | `outline-director/SKILL.md` |
| 读 `proposal_packet` / research JSON | 读 `analysis_brief.json`（+ 可选 audio context JSON） |
| 写 `artifacts/script.json` + schema 校验 | 写 `artifacts/outline.json` + Pydantic 校验 |
| EP / 宿主 agent 串阶段 | LangGraph node **spawn** stage deep agent |
| pipeline YAML 换 skill 换品类 | 不同 content_category / audio|video → 不同 skill 或同一 skill 分支 |

OM script 更偏「旁白/节拍/时间轴文本」；VA outline 更偏「章节叙事 + duration + enhancement_cues」。**阶段角色同类，schema 不同。**

### 2.2 关键区别（为什么以前觉得要「拼 prompt」）

| | OpenMontage | VideoAgent 现状 | 目标态 |
|--|-------------|-----------------|--------|
| 谁执行阶段 | **Agent 循环**（读 skill、读 JSON、写 JSON） | **Python Program** 一次 structured call | **Deep agent**（与 Companion 同栈） |
| Skill 用法 | Agent **按需读** md | 几乎无；知识在 mustache | SkillsMiddleware 自动加载 |
| 输入 | 磁盘上 prior artifacts | DB + 代码拼 template_data | **先投影成 JSON**，再给 agent |
| 「组装」发生在哪 | **Agent 脑子里**按 skill Process | **代码** mustache/jinja | Agent；代码只做 I/O 与 DB |
| 多 pipeline | 换 `pipelines/*/script-director.md` | 巨型模板 + if content_category | 换/增 skill，不必改拼接器 |

所以：**不是 OM 更简单所以不用组装，而是组装责任从 Program 挪到了 Agent+Skill。**  
上一版用代码 `load_skill().join()` = 仍在 Program 里当模板引擎，**不同 pipeline 还是要改 Python**——你的批评成立。

### 2.3 多模态差在哪

- OM：参考图常是 tool 参数 / 资产 path。  
- VA：用户参考图今日靠 `attach_images_to_messages`。  
- Deep agent 试点可：tool `attach` 或 HumanMessage 带 image_url；**不要把图写进 skill md。**

---

## 3. 目标架构（像 OM）

```
LangGraph node: outline_generation   (薄 Program)
  1. 幂等：已有 outline → 复用
  2. 从 DB 导出 prior → workspace/inputs/*.json
     (analysis_brief, 可选 audio_context)
  3. create_deep_agent(
       skills=["…/skills/stages/outline"],  # outline-director 等
       tools=[write_outline_artifact, …],
       backend=FilesystemBackend(project_root),
     )
  4. invoke("Run outline stage…")   # 不再拼 mustache
  5. 读 artifacts/outline.json → normalize → DB + additional_data.artifact_path
  6. emit STORY_OUTLINE_GENERATED
```

**Program 只做：** 投影输入、起 agent、落 DB、发事件。  
**不做：** 拼接 system prompt、品类 if/else 长文、mustache。

品类/audio|video：用 **多个 skill** 或 **一个 skill 内分支**（读 brief 里的 `content_category` / `mode`）——与 OM 多 pipeline director 相同思路。

后处理（audio section 1:1 填 duration、时长纠偏）可留在 Program/tool 内：**写盘前或写盘后**纯函数，不回到大模板。

---

## 4. Spike 结果（已跑；脚本已删）

当时路径：`services/agent/spikes/outline_deep_agent/`（已移除，结论保留于此）。

| 项 | 结果 |
|----|------|
| `create_deep_agent` + `FilesystemBackend(virtual_mode=True)` | ✅ |
| skills 路径 | 须用虚拟路径 `skills=["/skills"]`（宿主机绝对路径在 virtual_mode 下会 load 失败） |
| 输入 | `/workspace/inputs/analysis_brief.json` |
| 工具 | `write_outline_artifact`（Pydantic 校验 + 时长和校验） |
| 输出 | `workspace/artifacts/outline.json` **PASS** |
| mustache / Program concat | **未使用** |

观察（对生产有意义）：

- Agent 会先写错 shape，靠 tool `VALIDATION_ERROR` **自纠**再成功——这是 OM 式「schema 当闸门」。  
- Skill 未加载严时，`order` 易变成 1-based、`enhancement_cues` 易空 → 应用 Field 约束 + Quality Gate 写进 skill，必要时 Program 二次 normalize。  
- Companion 已同模式；outline 阶段 agent 是 **同一技术栈横向扩展**，不是新框架。

---

## 5. 点 2：DB + JSON path（不变，接到 deep agent 写盘）

| 项 | 约定 |
|----|------|
| SoT 写 | DB |
| 投影 | `artifacts/outline.json` |
| path | `additional_data.artifact_path` |
| 写入口 | `write_outline_artifact` tool 内 validate→写盘；Program 再 upsert DB（或 tool 内双写） |
| 读 | 下游暂仍 DB；companion/stage agent 可读 path |

---

## 6. 实施顺序（修订后）

1. **Schema 边界** — `OutlineLLMOutput` / `OutlineArtifact` / 保留 DB；导出 json schema（可选）  
2. **Skill** — 从 mustache 迁规则到 `outline-director/SKILL.md`（可借鉴 OM script-director 的 When/Process/Gate 结构；内容用我们的章节/cues）  
3. **Stage agent 工厂** — 复用 Companion 的 HarnessProfile 裁剪（禁 execute 等）  
4. **Node 接线** — creative stages always deep-agent（无 flag）
5. **双写 path** — persist  
6. **Golden** — 时长和、章节非空、下游 scene 不改可跑  

**明确不做（本试点）：** 全图改成单一 EP agent；一次清完 `video_state.py` 所有混用。

---

## 7. 请你拍板

1. **生产默认：** flag 双跑 → 再切 deep agent？还是试点合入即深切？  
2. **写盘失败：** 不阻断主流程 + 懒导出？（建议）  
3. **Schema 重建范围：** 仅 outline 三套，还是顺带 scene 开个头？  
4. **Audio-driven：** 同一 outline-director + brief.mode，还是拆 `outline-director-audio` skill？  

确认后按修订稿改正式代码；旧 §「Program concat md」作废。
