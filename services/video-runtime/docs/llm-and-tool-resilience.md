# LLM 韧性与 Tool 层重试

> **合并稿**（原 `design/llm_invocation_patterns.md`、`llm_universal_retry_fallback_design.md`、`video_wrapper_consistency_check.md`）。**实现入口**：`app/services/agent/utils/llm_resilience.py`。

---

## A. LLM 调用形态与 `llm_resilience`


> 与 **B 节（通用重试与 fallback 设计）**互补：本节盘点**现有代码里**几种调用方式、数据从哪来、应从哪里抽，避免业务里重复 `create_llm` / 与 `load_prompt` 脱节。

---

## 1. `load_prompt_with_fallback` 实际做了什么（必读）

实现见 `prompts/prompt_loader.py` → `_load_prompt_from_local`：

| 步骤 | 行为 |
|------|------|
| 解析 `hub_name` | 映射到 `PROMPTS_CONFIG[PromptName]` |
| 模板 | `load_local_mustache_template(local_template_name)` |
| 基座模型 | **`create_llm_from_model_config(model_config)`** |
| 若传入 `schema` | `base.with_structured_output(schema, …)`；Gemini 3 用 `method="json_schema"` |
| 若传入 `retry_config` | 对**最终** runnable 包 `with_retry` |

**不再从 LangSmith Hub 拉 prompt 正文**（全环境本地 Mustache）。

**「load 出来的第二条 runnable」** = 基座 **或** 基座+结构化链，**再** 可选 LangChain `with_retry`。与 **`create_agent` + Provider/Tool** 是另一条结构化路径，不能混用同一条 runnable。

---

## 2. 代码里常见的几种调用形态

| 形态 | 典型位置 | 模板 / 输入 | 结构化 |
|------|----------|-------------|--------|
| **A** `prompt \| with_structured_output` → `ainvoke(dict)` | 风格检测（现用 resilience 封装）、部分 chain | `ChatPromptTemplate` + 模板变量 dict | 绑在链尾 |
| **B** 裸 LLM `ainvoke` | clarify、部分 router | messages 或 dict | 无 |
| **C** `create_agent` + Provider/Tool | keyframe/video tool、`video_analysis` | `agent_inputs["messages"]` | Agent |
| **D** 仅 `load_local_mustache_template` | 测试、工具 | 本地 mustache | 视情况 |

---

## 3. 已抽的通用能力（`app/services/agent/utils/llm_resilience.py`）

| 函数 | 替代的旧写法 | 说明 |
|------|----------------|------|
| **`create_llm_from_model_config`**（在 `prompt_loader`） | 散落的 `create_llm(model_config)` | 与 `_load_prompt_from_local` 建基座同源 |
| **`ainvoke_structured_resilient`** + **`StructuredResilienceKind`** | 重复 schema/bundle 样板 | **`CREATE_AGENT`**：`agent_inputs`。**`STRUCTURED_CHAT_MESSAGES`**：已拼好的 `BaseMessage` 列表（``(await prompt.ainvoke(dict)).messages`` 或 `invoke_prompt_with_multimodal`）；provider 档 `with_structured_output.ainvoke(messages)`。**不要**再叠 LangChain `with_retry`。 |
| **`execute_with_resilience`** | 自定义 `invoke_fn` | 最底层 |

**`log_context`（可选）**：调用 `ainvoke_structured_resilient` / `execute_with_resilience` 时可传 `dict`，建议键含 `run_id`、`thread_id`、`conversation_id`、`caller`；视频批量 prompt 可再加 `batch_index`。日志后缀由 `llm_resilience._log_ctx_suffix` 统一格式化，便于与 `video_agent_performance` 等按任务串联。

---

## 4. `with_structured_output` 旧写法如何被「替代 / 融合」

**以前（例：`detect_video_style`）**

1. `load_prompt_with_fallback_async(..., schema=StyleDetectionResult)` → 得到 `prompt | structured`（第二条已是 runnable）。
2. 再 `chain.with_retry(...)`。
3. `await chain.ainvoke({...})`。

**问题**：第二条 runnable 绑死了**单一** `model_config`；要做「换模型 / provider→tool」就要重写一串样板代码。

**现在**

1. `load_prompt_with_fallback_async(..., schema=None, include_raw=False)` → **只要模板**（与上一步模板来源一致，避免二次绑定 structured）。
2. `msgs = (await prompt.ainvoke({...})).messages`，再 `await ainvoke_structured_resilient(..., kind=STRUCTURED_CHAT_MESSAGES, structured_chat_messages=msgs, include_raw=True)`。
3. 路由、同路重试、`model_config.model` 与备用模型、provider/tool 仍由 **`prompt_entry` + 可选 `resilience`** 驱动，与 Agent 路径同一套配置模型。

**返回值**：`include_raw=True` 时与 LangChain 一致为 `{"parsed", "raw"}`；**tool** 路由无原生 raw 时 **`raw` 为 `None`**，`parsed` 仍为结构化对象。

**仍可直接用 `load_prompt(..., schema=…)` 的场景**：不需要 resilience、只想要 LangChain `with_retry` 包一层时，可继续用 loader；**不要**再叠加 `execute_with_resilience`。

---

## 5. 迁移进度（`llm_resilience`）

**已接（本轮 + 此前）**

- Router：`merge_user_option_with_input` → `CREATE_AGENT`；`process_user_request` 路由分析 → `STRUCTURED_CHAT_MESSAGES` + `structured_schema=RouterAnalysisResult`。
- 场景生成：`CREATE_AGENT`（`ScenesCollection` 已写入 `PROMPTS_CONFIG`）。
- 详细分镜：`CREATE_AGENT`（`StoryboardDetailLLMOutput` 已写入配置）。
- Visual elements 匹配：`CREATE_AGENT` + `structured_schema=VisualElementsMatchingResult`（避免 `prompt_config` 与 service 循环 import）。
- 旁白 / 音效：`CREATE_REACT_AGENT` + `agent_tools`；并修复音效节点中原先缩进错误导致主流程不可达的问题。
- 主角色生成：`CharacterProfiles` 已写入 `PROMPTS_CONFIG`（调用方若仍手写 `create_agent`，可改为 `CREATE_AGENT` 一行）。

**仍可能手写 `create_agent` 的边角**

- 仅 **Image / Music 子 Agent 流式**：`image_generation_service`、`music/music_generation_service` 内在 `execute_with_resilience` 里对**无 `response_format`** 的 `create_agent` + `astream_events`（与 `llm_resilience` 共用 `build_resilience_bundle_from_prompt_entry` 的模型链）。
- 部分测试脚本若仍手写 `create_llm` / `create_agent`，按需再收。

**扩展**：`llm_resilience` 支持 `structured_output=False`（仅 `CREATE_AGENT`）、`llm_invoke_config`（传 `RunnableConfig` 等）；无 schema 的 `PROMPTS_CONFIG` 条目现按 **`build_model_only_routes`** 换模型。

| 形态 | 说明 |
|------|------|
| 其它 `prompt \| structured` + `with_retry` | 按 `detect_video_style` 模式：`schema=None` → 模板 `ainvoke(dict).messages` → `STRUCTURED_CHAT_MESSAGES` |
| 纯 chat 流式 + resilience | `build_resilience_bundle_from_prompt_entry`（无 schema）+ **`execute_with_resilience`** 包一层 `llm.astream`（如 Story 生成、Router clarify） |
| `invoke_prompt_with_multimodal` + 结构化 | 得到 messages 后同样走 `STRUCTURED_CHAT_MESSAGES`（与纯模板渲染统一） |

### 5.1 每条 prompt 一种结构化策略；错误与重试层次

- **配置（迁移）**：在条目的 `resilience` 里设 **`structured_output_strategy`: `"provider"` / `"tool"`**（推荐）。仍支持旧的 **`structured_strategy_order`**，但**只取第一项**；若写了多项会在日志里警告（不再自动 provider 失败后换 tool）。
- **换模型**：仍用 **`model_fallback_chain`**；fallback **沿用同一策略**（与主模型一致）。
- **LangGraph 内（ToolStrategy）**：`ToolStrategy(..., handle_errors=True)` 时，工具参数解析失败会注入 **`ToolMessage("Error: … Please fix your mistakes.")`**，**同一次** `agent.ainvoke` 内多轮续聊（见 LangChain `langchain/agents/factory.py` `_handle_model_output`）。
- **ProviderStrategy**：解析/校验失败通常直接 **`StructuredOutputValidationError`**，没有上述 ToolMessage 纠错环；业务可用 **`wrap_agent_parse_fallback`** 在 **单次** `ainvoke` 返回后从异常里抠 JSON（`prompt_utils.invoke_agent_with_parse_fallback`）。
- **`execute_with_resilience`**：对 **整次** `invoke_fn`（典型为一次 `agent.ainvoke`）按异常 **分桶**（`classify_llm_exception`：`transient`、`rate_limit`、`server`、`bad_request`、`structured_parse`、`auth_config`、`unknown` 等），在同一 **`(model_id, strategy)`** 上按 **`same_route_extra_retries`** 做**完整重试**（重新调用 `invoke_fn`，不是图内追加错因的那一种续聊）。认证类 **不重试不换路由**。
- **真实 API 烟囱**：`tests/llm/test_structured_output_recovery_real.py`（provider / tool 各一条）；`test_llm_resilience_execute_real.py`（含模拟超时后换模型）。
- **错误与恢复矩阵（消息级摘录，可复查 LLM / Tool 回复）**：运行 `tests/llm/test_structured_recovery_matrix_report.py` 生成 [`structured_recovery_matrix_report.md`](../../tests/llm/reports/structured_recovery_matrix_report.md)。

---

## 6. Live LLM call sites (DeepSeek + stage agents)

LangGraph dest pipeline services (`agent_router_service`, outline / scene / storyboard /
keyframe generation, admin smart-testing) have been removed. Remaining callers:

| Module | File | Shape |
|--------|------|--------|
| Research brief | `services/agent/video/generate_research_by_request_service.py` | `create_deep_agent` + `create_llm_from_model_config` |
| Video consistency | `tools/video/video_consistency.py` | `ainvoke_structured_resilient(STRUCTURED_CHAT_MESSAGES)` |
| Image consistency | `tools/image/character_consistency.py` | multimodal → `STRUCTURED_CHAT_MESSAGES` |
| Gemini transcription | `tools/transcribe/gemini.py` | multimodal → `STRUCTURED_CHAT_MESSAGES` |
| Music smart-clip | `services/agent/video/music_smart_clip_service.py` | stage director + `PROMPTS_CONFIG` |
| Stage runtime | `services/agent/stage_runtime/generic_stage.py` | `create_deep_agent` / `FailoverChatOpenAI` |
| Atomic media | `chat/v2/atomic_executor.py` | provider tools + resilience |

`PROMPTS_CONFIG` now only has transcription, video consistency, image consistency, and music smart-clip. Mustache Hub templates are gone; stage craft text lives in `kit/skills/stages/*/SKILL.md`.

---

## 7. 小结

- **建基座模型**：统一 **`create_llm_from_model_config`**。
- **结构化业务入口（统一）**：`ainvoke_structured_resilient` + `StructuredResilienceKind`（`CREATE_AGENT` / `STRUCTURED_CHAT_MESSAGES`）。
- **配置**：`PROMPTS_CONFIG` 条目 + 可选 **`resilience`**；多模型 fallback 仅在 **`resilience.model_fallback_chain`** 中显式列出（无默认对端启发式）。

---

## B. 通用 LLM 重试与 Fallback（设计）


> **状态**：P0 **`app/services/agent/utils/llm_resilience.py`** 已落地（分桶、合并、路由展开、`execute_with_resilience` 骨架）；**业务调用点尚未接入**。单测：`tests/llm/test_llm_resilience_policy.py`。  
> 与已移除的 `llm_retry_fallback_plan.md` 的关系：旧文档偏「盘点 + LangChain 能力验证」；本文偏「**可落地的统一策略、配置模型、异常分桶、与 PROMPTS_CONFIG 集成**」。  
> **调用形态盘点与 `load_prompt` 关系**：见 **A 节**。

---

## 1. 目标与约束

| 目标 | 说明 |
|------|------|
| **通用** | 同一套语义覆盖：`create_agent`、纯 `chain`/`llm.ainvoke`、`with_structured_output`（实现时按形态选包装方式）。 |
| **清晰** | 异常 → **分桶** → **策略表**（是否同一路由重试、是否进入下一 fallback 档），避免业务里散落 `try/except`。 |
| **默认克制** | 默认 **「同一路由多试 1 次」+「最多再切 1 档 fallback」**（具体次数可配置，可向下透传）。 |
| **尊重现状** | **结构化策略**默认 **Provider 优先**（模型支持时）；**允许按 prompt 覆盖**；与现有 `PROMPTS_CONFIG` 对齐，**不强行改各节点已有优先级**。 |

---

## 2. 调用形态盘点（实现时要分别包装）

| 形态 | 典型位置 | 包装思路 |
|------|----------|----------|
| **A. `create_agent` + `ainvoke`** | keyframe/video tool execution、router、music 等 | **不能**对 `CompiledGraph` 直接 `.with_fallbacks`**；用 **`RunnableLambda(agent.ainvoke)`** 或统一 **`ainvoke_resilient(agents[], ...)`**。 |
| **B. `load_prompt` → structured `Runnable`** | batch/eval/consistency 等 | 对 **外层 Runnable** `with_retry` / `with_fallbacks`（已在 `video_generation_service` 有 chain 先例）。 |
| **C. `llm.with_structured_output` 直连** | router、部分 admin | 同 B，对绑定后的 runnable 包一层。 |
| **D. `create_react_agent`（旧）** | narration、audio_effect | 单独适配或逐步迁移，策略表复用。 |

---

## 3. 配置模型：集中在 `PROMPTS_CONFIG`，按 prompt 覆盖

在 **`prompts/prompt_config.py` 的每条 `PROMPTS_CONFIG[PromptName]`** 增加可选字段 **`resilience`**（名称可再定），与现有 `model_config` / `schema` 并列。

### 3.1 字段草案

```python
# 仅设计示意，非当前仓库已存在代码
"resilience": {
    # 推荐：单策略（与实现一致；多项时仅首项生效并打日志）
    "structured_output_strategy": "provider",
    # 兼容旧键：仅第一项有效
    "structured_strategy_order": ["provider"],

    # 模型链：第一条通常与 model_config.model 一致；后续为 fallback 模型 id（create_llm 可识别）
    "model_fallback_chain": ["gemini-2.5-flash", "gpt-4.1-mini"],

    # 同一条路由（同一 model + 同一 strategy）上，对「可恢复」错误额外重试次数（不含首次）
    "same_route_extra_retries": 1,

    # 最多执行多少步「换档」（换 strategy 或换 model 各算一步，或合并计数，实现时二选一并文档写死）
    "max_fallback_steps": 1,

    # 可选：按错误桶覆盖默认行为
    "per_bucket": {
        "bad_request": {"same_route_extra_retries": 0, "allow_fallback": True},
        "structured_parse": {"same_route_extra_retries": 1, "allow_fallback": True},
    },
}
```

### 3.2 默认推导规则（无 `resilience` 时）

1. **`structured_output_strategy` / `structured_strategy_order`**  
   - 每条 prompt **仅一种** strategy（`provider` 或 `tool`）；推荐 **`structured_output_strategy`**。  
   - 若仍写 `structured_strategy_order` 数组，**只取第一项**；不再自动「provider 失败再试 tool」。  
2. **`model_fallback_chain`**  
   - 未在 `resilience` 中配置时 **不扩展**，仅使用 `model_config.model` 单一路由；需要多模型时在条目中 **显式** 写 `model_fallback_chain`。  
3. **`same_route_extra_retries` / `max_fallback_steps`**  
   - 默认 **`1` / `1`**，与「重试一次 + fallback 一次」的产品描述对齐。

### 3.3 Prompt 覆盖优先于推导

**显式 `resilience` 字段 > 推导**；与业务里「该节点用哪个模型」仍以 **`model_config.model`** 为第一入口，**fallback 链不应默认覆盖「首选模型」**，只是失败后的顺序。

### 3.4 与 `model_config`：组合，不是「配置类继承」

- **`model_config`**：描述 **当前这一跳** 的 LLM 构造参数（`create_llm(model_config)`），与现有代码一致。  
- **`resilience`**：描述 **失败后的行为**（同路由重试次数、strategy 顺序、换模型步数）。  
- 二者在 `PROMPTS_CONFIG` 条目里 **并列**；代码上用 **`build_resilience_bundle_from_prompt_entry(entry)`** 读取 `entry["model_config"]["model"]` 作为 primary，再合并 `entry.get("resilience")`。  
- **不做**「Resilience 继承 BaseModelConfig」这类 schema 继承：避免与 `create_llm` 的 dict 形态打架；子调用若需收紧上限，用 **`ResilienceContext.narrowed(...)`**（设计稿 §6）。

---

## 4. 异常分桶（Error Buckets）

统一函数（建议未来放在 `app/services/agent/utils/llm_resilience.py` 一类模块）：

```text
classify_exception(exc: BaseException) -> ErrorBucket
```

### 4.1 桶定义与建议默认动作

| Bucket | 识别（示例） | 同路由重试 | Fallback 换档 |
|--------|----------------|------------|----------------|
| **TRANSIENT** | `TimeoutError`、`asyncio.TimeoutError`、连接重置、`APIConnectionError`、部分 `ReadTimeout` | ✅ 默认允许 `same_route_extra_retries` | ✅ 用尽后允许 1 步 |
| **RATE_LIMIT** | HTTP 429、`RateLimitError` | ✅ 少次 + 退避 | ✅ |
| **SERVER** | HTTP 5xx | ✅ 少次 | ✅ |
| **BAD_REQUEST** | HTTP 400、`BadRequestError` | ⚠️ **默认不重试同请求**（同一 body 再发常无效） | ✅ **可**换模型/换 strategy（代理或 provider 差异）**一次** |
| **STRUCTURED_PARSE** | `StructuredOutputValidationError`、`JSONDecodeError`（若由 Provider 解析抛出） | ⚠️ 可选 1 次（温度/抖动） | ✅ **优先**换 **ToolStrategy** 或换模型 |
| **AUTH_CONFIG** | 401、403、缺 key | ❌ | ❌ |
| **UNKNOWN** | 其他 | ❌ 或保守 0 次 | ❌ 或仅日志 |

**说明**：

- **400** 需区分：LangSmith 里「**我们无法解析 JSON body**」多为**网关/代理/序列化**，与 OpenAI「**模型拒绝**」不同；分桶时可看 `message`/`code` 子分类（实现阶段再细化），**默认策略仍偏：不重试同一路由，允许一次换 provider**。  
- **转录 + Gemini + Provider** 类 **大 JSON 损坏** → 归入 **STRUCTURED_PARSE**，fallback **Tool** 或 **换 GPT** 比盲目同路由重试更稳。

---

## 5. 执行计划（Execution Plan）— 保持「代码清晰」

### 5.1 概念：一条「路由」= `(model_id, structured_strategy | None, runnable_or_agent_factory)`

- **Provider / Tool** 只影响 **带 schema 的 agent**；纯 chat 无 strategy。  
- **不**在内存里同时编译 10 个 agent；用 **factory**：`lambda: create_agent(model=create_llm(mc), ..., response_format=...)`

### 5.2 算法（伪代码）

```python
async def execute_with_resilience(invoke_fn, plan: ResiliencePlan, input, config):
    """
    invoke_fn: Callable[[route], Awaitable[result]]
    plan: 由 PROMPTS_CONFIG.resilience + 默认推导生成
    """
    for step in plan.linear_steps():  # 预展开为有序列表，避免嵌套难测
        for attempt in range(1 + step.same_route_extra_retries):
            try:
                return await invoke_fn(step.route, input, config)
            except Exception as e:
                bucket = classify_exception(e)
                if not plan.should_retry(bucket, step, attempt):
                    break  # 进入下一 step（换 strategy 或 model）
                if attempt >= 1 + step.same_route_extra_retries:
                    break
    raise last_error
```

**`linear_steps()` 展开示例**（`max_fallback_steps=1`，默认 order provider→tool，双模型）：

1. `(M0, provider)`，retries=1  
2. `(M0, tool)` — 仅当 step1 失败且 STRUCTURED_PARSE 或配置强制  
3. `(M1, provider)` — 仅当仍失败且允许 model fallback  

实际是否走 2/3 由 **`max_fallback_steps`** 与 **per_bucket** 裁剪，避免组合爆炸。

### 5.3 与 Tool wrapper 对称的语义

- **图像/视频 tool**：多模型链 + 有限循环。  
- **LLM 层**：**同桶内**「重试 + 换档」次数有硬上限；**观测字段**建议：`llm_route_index`、`llm_attempt`、`llm_error_bucket`、`llm_final_model`（与旧文档 §七一致）。

---

## 6. 参数向下透传（「也可以向下」）

建议单一 **`ResilienceContext`**（或 dict）随 `config["configurable"]` / 显式参数传递：

```python
@dataclass
class ResilienceContext:
    same_route_extra_retries: int = 1
    max_fallback_steps: int = 1
    model_fallback_chain: tuple[str, ...] = ()
    structured_strategy_order: tuple[str, ...] = ("provider", "tool")
    per_bucket_overrides: Mapping = field(default_factory=dict)

    def narrowed(self, **kwargs) -> "ResilienceContext":
        """子调用降低 max_fallback_steps / 关闭 model fallback 等"""
        return replace(self, **kwargs)
```

子任务（例如某个 shot 内再调小 LLM）可 **`parent.narrowed(max_fallback_steps=0)`** 防止指数爆炸。

---

## 7. 测试策略（已实现见 `tests/llm/test_llm_resilience_policy.py`）

| 测试 | 目的 |
|------|------|
| **`classify_llm_exception`** | 超时 / 400 / 结构化解析 等归入预期桶。 |
| **`merge_resilience_config`** | 默认 + prompt 覆盖合并规则。 |
| **`linear_steps` 展开** | 默认 `provider→tool`、单步 fallback、与 `max_fallback_steps` 裁剪。 |
| **（已有）** `test_retry_and_fallback_patterns.py` | LangChain `with_retry` + `with_fallbacks` 顺序不变。 |

**不**在首阶段做全链路真实 API 集成（已有 `test_*_real` / prompt matrix）；本模块单测以 **纯函数 + 假异常** 为主。

---

## 8. 分阶段落地（实现 PR 时）

| 阶段 | 内容 |
|------|------|
| **P0** | 新增 `llm_resilience.py`：`classify_*`、`merge_*`、`build_resilience_bundle_from_prompt_entry`、`execute_with_resilience`；**单测全绿**（`test_llm_resilience_policy.py`）。 |
| **P1** | 已接 **video_analysis**：`ainvoke_structured_resilient(..., kind=CREATE_AGENT, prompt_entry=PROMPTS_CONFIG[...])`；多模型仅在条目的 ``resilience.model_fallback_chain`` 显式配置。 |
| **P2** | 推广到 `load_prompt` chain；router/music 等。 |
| **P3** | LangSmith 字段与告警（400/STRUCTURED 比率）。 |

---

## 9. 小结

- **Provider 优先**、**按模型能力**、**PROMPTS_CONFIG 按 prompt 覆盖** — 三件事通过 **`resilience` + 推导规则** 统一。  
- **重试与 fallback 分离**：**同路由重试**对付瞬时；**换档**对付 400/解析/对端差异；默认 **1+1** 防失控。  
- **实现上** agent 用 **factory + 线性计划** 包一层，避免对 `CompiledGraph` 滥用 Runnable 组合。  
- **测试**：策略与分桶 **纯函数单测** + 已有 LangChain 组合测试；真实 API 继续用现有 integration。

---

## C. Video Wrapper 一致性检查（Tool 层重试与 metrics）


## 一、现状：Image Wrapper 一致性逻辑（对照）

### 1.1 流程概览

- **I2I**：截断参考图 → 调用底层 tool 生成 → **角色一致性校验**（`check_character_consistency_llm`）→ 通过则返回；不通过则同模型重试 1 次 → 仍不通过则换下一模型；全部不通过则 **best-effort** 选一致性最高的结果返回。
- **T2I**：无一致性校验，仅 API 错误重试 + 模型 fallback。
- 一致性输入：**参考图 URL 列表** + **生成图 URL** + 可选 prompt；输出：`ConsistencyCheckResult`（脸/配饰/服装等级、是否通过）。

### 1.2 Image 一致性判定

- `character_consistency.check_character_consistency_llm`：VLM 对比参考图与生成图，输出人设六维（`ConsistencyLevel`：identical / very_similar / … / n_a）、每角 `artifact`（变形档）、以及**整图** `severe_abnormality`（**合并**了原「整图 artifact」与严重异常：轻伪影与离谱事故同一套 good〜fail 档）。
- 通过条件：`per_character` 各维均 `>= very_similar`（或 n_a）且每角 `artifact` 通过，且整图 `severe_abnormality` 通过（good / acceptable / n_a）。

### 1.3 Image Metrics 与落库

- **ImageToolMetrics**（dataclass）：`total_attempts`、`per_model_attempts`、`success`、`final_model`、`consistency_checks`、`consistency_pass`、`consistency_details`（每笔含 model、passed、face_level、accessories_level、clothing_level）、`failure_reasons`、`best_effort_selected`。
- 每次尝试：`record_attempt(model, success, consistency_details_item=..., failure_reason=...)`；通过时写 `consistency_details`，不通过时写 `failure_reasons`（如 `consistency:face=not_similar`）。
- 结果注入：`_inject_metrics(result, metrics, duration_sec)` → 在 `ImageGenerationResult` 上写入 `image_tool_metrics`、`tool_duration_sec`、`tool_cost`。
- 落库：`video_character_generation_versions` / `video_keyframe_versions` 的 `image_tool_metrics`、`tool_duration_sec`、`tool_cost` 由上层在 `create_*_version` 时从 result 传入。
- 汇总：`build_tool_consistency_summary(run_id)` 从 character / keyframe version 表读 `image_tool_metrics`，经 `_aggregate_image_metrics` 得到 `total_calls`、`pass_count`、`fail_count`、`best_effort_count`、`face_fail_count` 等，写入 task 的 `tool_consistency_summary.character` / `.keyframe`。

---

## 二、Video 与 Image 的核心差异

- **Image**：参考图固定，生成图只需与参考图在角色维度一致即可，**单一参考标准**。
- **Video**：**首帧固定**，生成的是 5s 视频；prompt 描述的是运镜/动作（如「大脸拉远」「从小脸拉近」）。问题在于：
  - **首帧没有的内容在后续帧出现**：例如首帧只有脸部，prompt 写「拉远露出全身」，后续帧可能出现与首帧不一致的全身（首帧根本没全身信息），或拉近时首帧脸很小看不清，拉近后细节崩坏。
- 因此视频一致性不仅要判「和首帧像不像」，还要判：**是否出现了首帧中不存在或不可推断的内容**（first-frame violation）。若因此不通过，需要 **原因类型 + LLM 给出修改后的 prompt**，供重试/fallback 时使用。

---

## 三、Video 一致性检查设计

### 3.1 输入 / 输出

- **输入**：
  - `start_image_url`：首帧图 URL（固定参考）
  - `video_url`：生成视频 URL（或可抽样关键帧的 URL，若 API 支持）
  - `i2v_prompt`：本次使用的 I2V 文案
- **输出**（建议新建 `VideoConsistencyCheckResult`，与 image 的 `ConsistencyCheckResult` 平行）：
  - `passed: bool`：是否通过。
  - `failure_reason_type: Optional[str]`：未通过时的分类，例如：
    - `"first_frame_violation"`：出现首帧未出现/不可推断的内容（如拉远出现全身、拉近出现细节等）。
    - `"appearance_inconsistent"`：与首帧外观/角色明显不一致（但未 necessarily 违反「首帧未出现」）。
    - `"other"`：其他一致性或质量问题。
  - `reason_detail: Optional[str]`：简短说明（供日志与 metrics）。
  - **`suggested_prompt: Optional[str]`**：由 LLM 给出的、避免当前问题的修正版 I2V prompt，供同模型或 fallback 重试时使用（仅当 `first_frame_violation` 或需要改 prompt 时填充）。

### 3.2 VLM/LLM 调用

- 新建模块（如 `tools/video/video_consistency.py`），参考 `character_consistency.py`：
  - 输入：首帧图 + 生成视频（若 VLM 支持视频；否则可考虑首帧 + 抽帧图或尾帧）。
  - Prompt 要点：
    1. 判断视频与首帧在角色/场景上是否一致。
    2. **专门判断**：视频中是否出现「首帧中不存在或无法合理推断」的内容（例如首帧只有脸，中后段却出现全身；或首帧脸很小，拉近后五官与首帧不符）。
    3. 若未通过且与「首帧未出现内容」相关，要求 LLM 输出一条 **修改后的 I2V prompt**，要求不触发此类问题（例如避免「拉远露全身」改为「保持中景」等）。
  - 输出解析为 `VideoConsistencyCheckResult`（含 `passed`、`failure_reason_type`、`reason_detail`、`suggested_prompt`）。

### 3.3 通过标准（建议）

- 与首帧**外观/角色一致**，且**未出现首帧中不存在或不可推断的内容**即通过。
- 具体阈值可在实现时用配置或 prompt 约束（例如仅当明确判定为 first_frame_violation 或 appearance_inconsistent 时为不通过）。

---

## 3.4 多维度视频一致性分类（方案）

与图片「脸/配饰/服装」多维度+等级类似，视频也做成**多维度 + 每维等级**，避免单一 pass/fail 过于绝对；最终是否 pass 由业务/配置决定。

### 3.4.1 prompt_execution 与子维：分开 vs 合并

**概念关系**：prompt_execution（符合 prompt）通常包括：
- **camera_movement**：镜头是否按 prompt 做了推拉摇移等；
- **action**：角色/物体是否按 prompt 做了动作、是否有足够动态；
- **style_consistency**：风格是否按 prompt/首帧一致（也可视为一致性维度单独存在）。

**建议：分开。**

| 做法 | 优点 | 缺点 |
|------|------|------|
| **不分开**：只设一个 prompt_execution 综合等级 | 结构简单，LLM 只打一个分 | 不知道是镜头没动、没动作还是风格跑了；metrics 不细，suggested_prompt 难针对性改 |
| **分开**：camera_movement、action、style_consistency 各为一维 | 可定位具体问题；重试/改 prompt 可针对镜头或动作；统计能看哪一块常挂 | 维度多，prompt 需写清各维判定标准 |

结论：**拆成 camera_movement、action、style_consistency 三个独立维度**。若需要「符合 prompt」总评，可保留一个 **prompt_execution** 作为**可选综合项**（由 LLM 综合打一个等级，或后端用 camera_movement + action 聚合），便于通过条件里写「prompt 相关至少 acceptable」而不逐个列三个子维。

### 3.4.2 维度与含义（定稿）

| 维度 | 含义 | 说明 |
|------|------|------|
| **first_frame_consistency** | 与首帧一致 | 含两点，**首帧说明**中应写清二者，勿写「生成视频的首帧与输入图一致」（首帧即输入图，无校验意义）：① **持续一致**：整片视频中人物/角色与首帧一致，无脸崩、无随时间变形（后面的人跟首帧里的人一样）；② **无首帧违规**：未出现「首帧中不存在或不可推断」的内容（如首帧只有脸却出现全身、拉近后五官与首帧不符等）。可选拆成两维：temporal_consistency + no_first_frame_violation，见下。 |
| **camera_movement** | 镜头运动符合 prompt | 推拉摇移等是否与 prompt 描述一致；prompt 未描述镜头则为 n_a。 |
| **action** | 动作/动态符合 prompt | 角色/物体是否按 prompt 有动作、画面是否有足够动态（避免过于静态）。 |
| **style_consistency** | 风格一致 | 画面风格（光影、色调、画风）与首帧或整体一致，无突兀跳变；也可理解为「符合 prompt 对风格的描述」。 |
| **artifact** | 无奇怪变形/崩坏 | 人脸/身体/物体无明显变形、闪烁、撕裂等。 |
| **prompt_execution**（可选） | 符合 prompt 综合 | 仅作总评时使用：由 LLM 综合镜头+动作（+风格）打一个等级，或后端从 camera_movement + action 聚合。 |

**first_frame_consistency 是否拆成两维（可选）**

- **不拆**：保持一个维度，在 prompt 中明确要求首帧说明同时写清「① 整片人物与首帧是否持续一致、无脸崩/变形」与「② 是否出现首帧未出现/不可推断的内容」，避免 LLM 只写「首帧与输入图一致」这类废话。
- **拆开**：
  - **temporal_consistency**（持续一致）：整片视频里人物/脸与首帧一致，无脸崩、无随时间变形。
  - **no_first_frame_violation**（无首帧违规）：未出现首帧中不存在或不可推断的内容。
  拆开后通过条件需相应调整（如两维均 >= acceptable 或 n_a）。实现时需改 schema + prompt + _compute_passed。

其他可选项（按需加入）：

- **temporal_stability**：时序稳定，无异常闪烁、抖动。
- **composition**：构图与 prompt 或首帧是否一致（如景别、主体位置）。

### 3.4.3 等级（每维）

与 image 的 `ConsistencyLevel` 对齐，每维输出等级而非仅 pass/fail：

- **good**：很好，符合预期。
- **acceptable**：可接受，有小瑕疵但不影响使用。
- **poor**：较差，明显不符合或有问题。
- **fail**：不可接受（如首帧违规、严重变形）。
- **n_a**：不适用或无法判断（如 prompt 未描述镜头运动则 camera_movement 可为 n_a）。

是否通过：由**通过条件**统一决定，例如「所有维度 >= acceptable（或 n_a）」「或 first_frame_consistency + camera_movement + action 必须 >= acceptable」等，由你定。

### 3.4.4 输出结构建议（多维度版）

```text
VideoConsistencyCheckResult:
  # 各维度等级（与 image 的 face_level / accessories_level 平行）
  first_frame_consistency: "good" | "acceptable" | "poor" | "fail" | "n_a"
  camera_movement:         "good" | "acceptable" | "poor" | "fail" | "n_a"
  action:                  "good" | "acceptable" | "poor" | "fail" | "n_a"
  style_consistency:       "good" | "acceptable" | "poor" | "fail" | "n_a"
  artifact:                "good" | "acceptable" | "poor" | "fail" | "n_a"
  prompt_execution:        "good" | "acceptable" | "poor" | "fail" | "n_a"   # 可选综合项

  # 每维说明 + 汇总 + 重试
  first_frame_consistency_reason / camera_movement_reason / action_reason / style_consistency_reason / artifact_reason: str?  # 各维度简要说明
  reason_overall: str?     # 整体汇总说明，供日志与 metrics
  passed: bool             # 由业务按「通过条件」从上述维度计算
  suggested_prompt: str?   # 完整可用的 I2V 提示词，可直接用于重试（不能只是片段或仅镜头描述）
```

- **passed**：由后端根据各维度等级按「通过条件」计算。
- **suggested_prompt**：必须是一条完整 I2V 文案，可直接用于重试；在 first_frame_consistency 或 style / artifact 等会挡住 passed 的维度不通过时给出（**不因仅 camera_movement 不通过而依赖 suggested 重试**）。

### 3.4.5 通过条件（已定）

- **first_frame_consistency**、**artifact**：须 >= acceptable 或 n_a。
- **camera_movement**：**不参与 `passed`**，任意档位均不挡通过；仅写入 metrics / 展示「与 prompt 运镜是否一致」。
- **其余维度**（若仍存在 **action**、**style_consistency**）：不能为 **fail**（可为 good / acceptable / poor / n_a）。

即：`passed` **不含** `camera_movement`；示例：`(first_frame 与 artifact/side 通过) and (action != "fail") and (style_consistency != "fail")`（无 action 维时可删对应项）。实现见 `app/tools/video/video_consistency.py` 中 `_compute_passed`。

---

## 四、Video Wrapper 主循环适配

### 4.1 单次尝试：执行 + 一致性

- 在现有 `_invoke_video_tool` 之后增加一步：
  - 若 `result.success` 且 `result.video_url` 存在，则调用 `check_video_consistency_llm(start_image_url, result.video_url, i2v_prompt)`，得到 `VideoConsistencyCheckResult`。
  - 若未做校验（如无 video_url），则视为通过，直接返回 result（与 image 无参考图时行为一致）。

### 4.2 不通过时的策略

- **记录**：当前 attempt 记为不通过，写入各维度等级、各维 reason、reason_overall、suggested_prompt 到 metrics 的 `consistency_details`。
- **重试**：
  - 若存在 `suggested_prompt` 且非空，**同模型用 `suggested_prompt` 再试一次**（替换原有 prompt）。
  - 若没有 `suggested_prompt` 或已重试过一次，则 **换下一模型**（沿用原 prompt 或最后一次使用的 prompt）。
- **全部不通过**：可选与 image 类似，做 **best-effort**：从「有视频 URL」的候选中选一个（例如按 `failure_reason_type` 优先级或简单选最后一个），并标记 `best_effort_selected=True`。

### 4.3 与 Image 的对比

| 项目           | Image (I2I)                    | Video (I2V)                                        |
|----------------|---------------------------------|-----------------------------------------------------|
| 参考           | 多张参考图                      | 首帧一图                                            |
| 一致性维度     | 脸/配饰/服装 vs 参考图          | 与首帧一致 + 不出现「首帧未出现的内容」             |
| 不通过后       | 同模型重试 1 次 → 换模型        | 同模型用 suggested_prompt 重试 1 次 → 换模型         |
| 失败原因落库   | consistency_details + failure_reasons | 同左，且增加各维等级、*_reason、reason_overall、first_frame_violation、suggested_prompt |

---

## 五、Metrics 与落库

### 5.1 VideoToolMetrics 扩展

- 在现有 `VideoToolMetrics` 上增加（与 Image 对齐，便于汇总与排查）：
  - `consistency_checks: int`
  - `consistency_pass: int`
  - `consistency_details: List[Dict]`，每笔建议包含：
    - `model`, `passed`
    - `failure_reason_type`, `reason_detail`
    - **`first_frame_violation: bool`**：是否因「首帧未出现内容」不通过
    - **`suggested_prompt: Optional[str]`**：本笔 LLM 建议的修正 prompt（若有用此 prompt 重试，可同笔或下一笔记录）
  - `failure_reasons: List[str]`：保留现有，并可在不通过时追加 `consistency:first_frame_violation` 等。
  - `best_effort_selected: bool`（若实现 best-effort）。
- 新增 `to_dict()`，供写入 `VideoGenerationResult` 和落库。

### 5.2 VideoGenerationResult 扩展

- 在 `VideoGenerationResult` 上增加（与 ImageGenerationResult 对齐）：
  - `video_tool_metrics: Optional[Dict]`（由 `VideoToolMetrics.to_dict()` 填入）
  - `tool_duration_sec: Optional[float]`
  - `tool_cost: Optional[float]`
- Wrapper 在返回前做一次「注入」：根据本次调用的 metrics 与耗时/成本，写入 result，便于上游统一落库。

### 5.3 落库（video_generation_versions）

- `create_video_generation_version` 已支持 `video_tool_metrics`、`tool_duration_sec`、`tool_cost`。
- 需要保证：**所有写入 video_generation_versions 的调用路径**（如 `save_video_generation_to_db`、video_agent_service 内创建 version 的代码）都从 **VideoGenerationResult / VideoGenerationVersion** 上读取并传入这三项（目前部分路径未传，需补齐）。

### 5.4 汇总（task 的 tool_consistency_summary）

- 在 `build_tool_consistency_summary(run_id)` 中，**video 部分**不再返回空 `{}`，改为：
  - 从 `video_generation_versions` 表按 `run_id` 查出 `video_tool_metrics` 列表。
  - 新增 `_aggregate_video_metrics(rows)`，产出与 image 类似的统计，例如：
    - `total_calls`, `pass_count`, `fail_count`
    - `best_effort_count`
    - **`first_frame_violation_count`**：`consistency_details` 中 `first_frame_violation=True` 的条数
  - 将结果写入 `tool_consistency_summary["video"]`，便于列表/详情页展示「视频一致性」与「首帧违规」次数。

---

## 六、其他需要适配的点

1. **视频抽帧与 VLM 能力**  
   若当前 VLM 只支持图像，需约定：用首帧 + 生成视频的若干抽帧（如 1s、3s、5s）或尾帧作为「生成侧」输入，与首帧对比，判断一致性与 first_frame_violation。若 VLM 支持视频输入，可直接首帧 + 整段视频。

2. **Lipsync 路径**  
   lipsync 复用 `_run_video_loop`，一致性检查与 metrics 会一并生效；若 lipsync 有特殊约束（例如必须用某模型），可在一致性不通过时的 fallback 顺序上单独考虑，但 metrics 结构建议与普通 I2V 统一。

3. **Prompt 与多语言**  
   一致性检查的 prompt（含「首帧未出现内容」「给出 suggested_prompt」）需要与现有多语言/文案规范统一；若 suggested_prompt 需与用户语言一致，可在调用 LLM 时传入语言参数。

4. **成本与延迟**  
   每次视频生成成功都会多一次 VLM 调用，成本和延迟会增加；可在配置中增加开关（如按环境或用户选项关闭视频一致性检查），便于灰度或降级。

5. **Admin 与错误追踪**  
   - 列表/详情已展示 `tool_consistency_summary`；补充 video 后，前端可展示「视频 X/Y 通过」「首帧违规 N 次」等。
   - 若 `consistency_details` 或 version 的 `video_tool_metrics` 中存了 `suggested_prompt`，可在详情页展示，便于排查「为何重试、用了什么修正 prompt」。

---

## 七、实现顺序建议

1. **video_consistency 模块**：实现 `check_video_consistency_llm` 与 `VideoConsistencyCheckResult`，并写好 prompt（首帧 + 视频，输出五维等级、各维 reason、reason_overall、suggested_prompt；passed 由后端计算）。
2. **VideoToolMetrics**：扩展字段与 `record_attempt`、`to_dict()`，并在 wrapper 主循环中接入一致性结果与 suggested_prompt 重试逻辑。
3. **Video wrapper 主循环**：在 `_run_video_loop` 中接入「成功 → 一致性检查 → 不通过则 suggested_prompt 重试 → 换模型」及可选的 best-effort。
4. **VideoGenerationResult + 注入**：增加 `video_tool_metrics`、`tool_duration_sec`、`tool_cost`，在 wrapper 返回前注入。
5. **落库**：所有 `create_video_generation_version` 的调用处传入 `video_tool_metrics`、`tool_duration_sec`、`tool_cost`。
6. **汇总**：`_aggregate_video_metrics` + `build_tool_consistency_summary` 中填充 `tool_consistency_summary["video"]`。
7. **Admin/前端**：展示 video 一致性及首帧违规数；可选展示 suggested_prompt。

---

## 八、小结

- **与 Image 的差异**：视频是「首帧固定 + 多秒生成」，一致性要额外考虑「首帧中不存在的内容是否在后续出现」；不通过时需要 **原因类型 + LLM 给出修正 prompt**，供同模型重试或 fallback 使用。
- **Metrics 与汇总**：Video 与 Image 对齐，扩展 `VideoToolMetrics` 和 `tool_consistency_summary.video`，并保证 version 表与 task 汇总都能写入、可查。
- **落库与展示**：统一从 result 带出 `video_tool_metrics`、`tool_duration_sec`、`tool_cost` 到 `video_generation_versions`，并在 admin 中展示 video 一致性与首帧违规情况。
