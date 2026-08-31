# Stage Runtime 布局（对照业界标准修订）

> Status: **修订** 2026-07-31  
> 关联: Anthropic Agent Skills、DDD 分层、OpenMontage `skills/` + `schemas/artifacts/`

---

## 0. 你的两个问题（先答）

### 「DB 也有 schema，要不要放一起？」

**要「放一起」的是 Python 合同入口，不是全塞进 kit 给 agent 读。**

| 放一起 | 不要混 |
|--------|--------|
| `app/contracts/{llm,artifacts,db}/` 同一棵树 | DB 行模型当 agent 热路径必读 |
| 导出的 JSON 也可同树：`kit/schemas/artifacts/`（仅落盘合同） | msgspec/CRUD 实现塞进 skill 目录；为 llm/db 再维护一份 JSON |

DB **有** schema，和 LLM/Artifact **并列三层**；agent 默认读 **artifacts（+ 可选 llm）**；db 导出给人对齐/偶发查阅，不是 stage 必读。

### 「都放在 kit 好吗？」

**不完全好——上一版把 `runs/` 塞进 kit 偏方便，不是标准。**

| 标准来源 | 实际约定 |
|----------|----------|
| [Anthropic Agent Skills](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills/overview) | Skill = 目录 + `SKILL.md`（+ `references/` / `scripts/`）；**不是**业务 DB 模型仓库 |
| OpenAI skills 迁移指南 | `.agents/skills/*/SKILL.md`；应用逻辑/工具 schema 在代码里，和 skill 目录分开 |
| DDD / 持久化实践 | Domain / LLM 合同 ≠ Persistence；**DB schema 不进 domain/agent 包当真相** |
| OpenMontage | 根下 `skills/` + `schemas/artifacts/`；**没有**把 Postgres 表合同和 skill 捆死 |

**结论：**

- `kit/` = **给 agent 的知识面**：skills +（导出的）JSON schemas  
- `app/contracts/` = **给 Python 的合同面**：llm + artifacts + **db**（真相）  
- `data/run_workspaces/` = **运行时工件**（不要叫 kit 的一部分）

---

## 1. 推荐目录（修订后）

```
services/agent/
  kit/                                 # 仅 agent 知识（Filesystem 可挂这里或挂服务根）
    skills/stages/<stage>/*/SKILL.md
    schemas/                           # 仅 artifacts（由 contracts 生成）
      artifacts/outline.schema.json
      # 不导出 llm/、db/ — 那两层只用 Python contracts
    README.md

  app/contracts/                       # 三层合同「放一起」——Python 真相
    llm/outline.py                     # OutlineLLMOutput（structured output / 转换）
    artifacts/outline.py               # OutlineArtifact（落盘；可导出 JSON）
    db/outline.py                      # OutlineDB（CRUD；不导出 JSON）
    export_json_schemas.py             # → kit/schemas/artifacts only


  data/run_workspaces/{thread}/{run}/  # 运行时 inputs/artifacts（gitignore）
  app/services/agent/stage_runtime/
  app/services/agent/video/outline_stage/
```

Deep agent 虚拟路径（backend root = `services/agent`）：

- `/kit/skills/stages/outline`
- `/kit/schemas/artifacts/outline.schema.json`
- `/data/run_workspaces/{tid}/{rid}/inputs/…`

（若只挂 `kit/` 为 root，则 runs 需 CompositeBackend 或仍放服务根——实现选服务根更简单。）

---

## 2. 三层 schema 各管什么

```
llm        → 模型该吐什么（少运维字段）
artifacts  → stage 交接 / 落盘 JSON（跨 node）
db         → 表行（uuid、run_id、themes[]、时间戳…）
```

Outline 对照见下；**转换只在 Program**：`LLM/draft → Artifact → DB`。

| | LLM | Artifact | DB |
|--|-----|----------|-----|
| 创作字段 | ✅ | ✅ | 部分列 |
| chapters | nested | nested | **子表** |
| uuid/run_id | ❌ | stamp 后有 | ✅ |
| themes[] / target_audience | ❌ | 可选 | ✅ |
| 给谁看 | structured output / tool | agent + 磁盘 | CRUD / API |

---

## 3. Skill 标准长什么样（Anthropic）

```
outline-director/
  SKILL.md              # 必填；frontmatter name + description
  references/           # 可选：大段 schema 说明、品类细则
  scripts/              # 可选：校验脚本
```

大段 JSON Schema **可以**放 `references/`，也可以指到 `/kit/schemas/artifacts/….json`。  
**不要**把 DB msgspec 源码塞进 skill 目录。

---

## 4. 存储（不变）

| | |
|--|--|
| 阶段 JSON | 本地 `data/run_workspaces`（LangGraph 热路径） |
| 媒体 | S3/CDN |
| DB | Postgres SoT |
| JSON 镜像 S3 | 可选，默认关 |

---

## 5. 和「全塞 kit」的差别（诚实对照）

| | 全塞 kit（偏错） | 本修订 |
|--|------------------|--------|
| skills | ✅ | ✅ 在 kit |
| artifact/llm JSON | ✅ | ✅ 在 kit/schemas |
| db / llm JSON 导出 | 硬塞进 skill / 双份维护 | ❌ 不导出；只用 `app.contracts` Python |
| db Python / msgspec | 不该在 kit | ✅ 只在 `app/contracts/db` |
| runs 工作区 | 不宜冒充「知识」 | ✅ `data/run_workspaces` |

「放一起」= **`app/contracts` 三层 + `kit/schemas` 三层导出对齐**；不是把 Postgres 和 SKILL.md 糊成一锅。
