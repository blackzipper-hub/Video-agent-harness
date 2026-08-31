# 计费系统设计方案

## 一、现状分析

### 1.1 入口梳理

| 入口 | 触发方式 | 执行方式 | LLM 调用 | Tool 调用 | billing 处理 |
|------|----------|----------|----------|-----------|-------------|
| **新任务** (`_submit_new_task`) | POST /agent/stream | 异步 SQS → task_worker → `router_agent.astream()` | 多次 ainvoke / astream（router分析、clarify、video_agent 内部） | 图像/视频/音频生成 | task_worker 完成后 → billing_status=pending → billing_worker 扫描 |
| **Resume** (`_submit_resume`) | POST /agent/stream (带 resume_data) | 异步 SQS → task_worker → `router_agent.astream(Command(resume=...))` | 同上（继续执行被中断的流程） | 同上 | **复用同一个 run_id**，⚠️ **LangSmith 只记录首次 run 的 cost，resume 成本丢失** |
| **Regenerate Keyframes** | POST /video-editing/regenerate-keyframes | **同步** endpoint 直接 await | agent.ainvoke (批量 prompt 生成 + 图像生成) | 图像生成工具 | finally → 写 conversation_run → billing_status=pending |
| **Regenerate Videos** | POST /video-editing/regenerate-videos | **同步** endpoint 直接 await | agent.ainvoke (prompt 生成 + 视频生成) | 视频生成工具 | 同上 |
| **Regenerate Characters** | POST /video-editing/regenerate-characters | **同步** endpoint 直接 await | agent.ainvoke (角色图像生成) | 图像生成工具 | 同上 |

### 1.2 LLM 调用方式

代码中有 **三种** LLM 调用模式：

| 模式 | 代码示例 | 场景 | callback 是否生效 | stream_usage |
|------|----------|------|-------------------|--------------|
| **LangGraph astream** | `router_agent.astream(state, config=RunnableConfig(callbacks=...))` | 主流程 / resume | ✅ 是（config 传了 callbacks, **且自动传播到所有子节点**） | 取决于底层 LLM |
| **agent.ainvoke** (graph 内) | `agent.ainvoke(inputs)` / `agent.ainvoke(inputs, context=...)` | keyframe/video/character 生成（graph 节点内） | ✅ **是（测试证实 graph callback 自动传播）** | 取决于底层 LLM |
| **agent.ainvoke** (regenerate) | `service.regenerate_keyframes_by_request(...)` | regenerate 端点（不走 graph） | ❌ **否**（不走 graph，需要手动传） | 取决于底层 LLM |
| **llm.astream** (graph 内) | `clarify_llm.astream(messages)` / `llm.astream(messages)` | clarify、story 生成（graph 节点内） | ✅ **是（graph callback 自动传播）** | 取决于 LLM 实例化 |

**关键发现（已测试验证）：**
- `CreditCheckCallbackHandler` 已经写好（`credit_check_callback.py`），但 **所有方法都是 `pass`（全部禁用）**
- ✅ **LangGraph callback 自动传播**：graph 级别传的 callback 会自动传播到所有子节点的 LLM 调用
- ❌ **Regenerate 不走 graph**：需要手动传 callbacks
- `ModelService` 创建 LLM 时 **没有设置 `stream_usage=True`**，只有 `prompt_config.py` 里部分配置有
- `agent_router_service.py:242` 创建的 `ChatOpenAI` 有 `stream_usage=True`

### 1.3 成本计算能力

**已有基础设施：**
- `ToolService.calculate_cost()` — 统一成本计算器，支持所有 LLM 模型 + 所有 Tool 类型
- `ToolService._PRICING_CONFIG` — 完整的模型定价表（GPT-4.1-mini / GPT-5-nano / Gemini 2.5 Flash / Gemini 2.0 Flash / Gemini 3 Pro）
- `CreditCheckCallbackHandler` — 已实现 token 提取逻辑（Gemini / OpenAI），但全部被 `pass` 禁用
- `LangSmithCostService` — 从 LangSmith API 获取 run 总成本

**当前唯一生效的成本数据来源：LangSmith API（`read_run(run_id).total_cost`）**

### 1.4 当前 billing_worker 流程

```
conversation_runs (billing_status=pending)
  ↓ 每 45s 扫描
billing_worker.process_one_billing_verify()
  ↓
get_cost_and_credits_for_run(run_id) → LangSmith 取成本并换算积分
  ↓ 成功
deduct_credits_with_cost() → 扣积分
  ↓ 或失败
保持 pending → 下次扫描再来 → 可能无限循环 ← 核心问题
```

---

## 二、问题清单

### P0（必须修复）

| # | 问题 | 影响 |
|---|------|------|
| 1 | **积分不足无限重试** | billing_status=pending 永远不变，45 秒一次无限循环 |
| 2 | **LangSmith 404 无限重试** | run_id 在 LangSmith 查不到时同样无限循环 |
| 3 | **任务前不检查余额** | 0 积分用户也能执行任务，跑完扣不了 |
| 4 | **完全依赖 LangSmith 获取成本** | LangSmith 宕机/延迟/404 → 计费全挂 |

### P0.5（新发现 — 已验证）

| # | 问题 | 影响 | 测试验证 |
|---|------|------|----------|
| 4.1 | **Resume 复用 run_id → `read_run` API 成本不更新** | `read_run(run_id)` 只返回 Phase 1 成本，即使等 30s 也不更新。但 `list_runs(trace_id)` 能拿到完整成本。前台能看到是因为前台用 trace 视图。 | `test_langsmith_timing` ✅ |
| 4.2 | **Regenerate conversation_run 创建时机太晚** | 在 finally 中创建，如服务器崩溃，预扣积分成为孤儿记录无法退款 | 代码分析 ✅ |

#### 4.2 详细分析：Regenerate conversation_run 时机问题

**当前代码（有风险）：**
```
预扣积分 → try { service.regenerate() } finally { create_conversation_run }
```
- 如果服务器在 `regenerate()` 执行中崩溃（OOM、重启），`finally` 不执行
- 预扣的积分已经扣了，但没有 conversation_run → billing_worker 找不到 → 无法退款

**对比 `_submit_new_task`（正确的）：**
```
预扣积分 → create_conversation_run → enqueue SQS
```
- conversation_run 在预扣后立即创建，即使后续失败也有记录

**修复方案：regenerate 也应在预扣后、try 前创建 conversation_run**
```
预扣积分 → create_conversation_run(status=running) → try { service.regenerate() } finally { update status }
```

### P1（应该修复）

| # | 问题 | 影响 |
|---|------|------|
| 5 | **callback 全部禁用** | 已有成本计算能力但没用上，无法自主算 cost |
| 6 | **regenerate 场景不传 callbacks** | regenerate 不走 LangGraph，需要手动传 |
| 7 | **stream_usage 不一致** | 部分 LLM 有 `stream_usage=True`，部分没有 |
| 8 | **`_deduct_credits_with_session` 余额不足没 return** | 冗余代码但不致命（已修） |

> **注意：** P1.6 原来写的是"regenerate / 裸 LLM 调用不传 callbacks"。测试证明 LangGraph graph 的 callback 会自动传播到节点内 LLM，所以主流程不需要改。只有 regenerate（不走 graph）需要手动传。

---

## 三、callback 成本追踪 — 可行性分析（已验证）

> **以下结论均已通过 `tests/billing/test_callback_and_cost_tracking.py` 9 项真实 API 测试验证。**

### 3.1 `on_llm_end` 在 streaming 下的行为 ✅ 已验证

| 调用方式 | `on_llm_end` 触发 | token usage 可用 | 条件 | 测试 |
|----------|-------------------|------------------|------|------|
| `llm.ainvoke()` (OpenAI) | ✅ | ✅ in=15, out=1 | — | `test_openai_ainvoke_on_llm_end` |
| `llm.astream()` (OpenAI, `stream_usage=True`) | ✅ | ✅ in=19, out=9 | **必须 `stream_usage=True`** | `test_openai_astream_on_llm_end` |
| `llm.astream()` (OpenAI, 无 `stream_usage`) | ✅ | ❌ **in=0, out=0** | — | `test_openai_astream_on_llm_end` |
| `llm.astream()` (Gemini) | ✅ | ✅ in=10, out=128 | 默认返回，不需要额外参数 | `test_gemini_astream_on_llm_end` |
| `llm.ainvoke()` (Gemini) | ✅ | ✅ in=13, out=33 | — | `test_gemini_ainvoke_cost` |
| `llm.with_structured_output().ainvoke()` | ✅ | ✅ in=81, out=12 | — | `test_openai_structured_output_callback` |

**关键结论：**
- OpenAI streaming **必须 `stream_usage=True`**，否则 usage 全为 0
- Gemini **默认就有 usage**，不需要额外参数
- `with_structured_output()` 也能正常触发 callback

### 3.2 LangGraph callback 传播 ✅ 已验证

| 测试场景 | callback 传播 | 说明 | 测试 |
|----------|-------------|------|------|
| `graph.ainvoke({}, config={"callbacks": [cb]})` → 节点内 `llm.ainvoke(msgs)` | ✅ **自动传播** | 2 个节点都触发 on_llm_end | `test_langgraph_astream_callback_propagation` |
| `graph.astream({}, config={"callbacks": [cb]})` → 节点内 `llm.astream(msgs)` | ✅ **自动传播** | 2 个节点都触发 on_llm_end | 同上 |
| LangGraph interrupt → resume (同 run_id) | ✅ callback 正常工作 | Phase 1 + Phase 2 各自有独立 callback 数据 | `test_langgraph_interrupt_resume_run_id` |

**重要发现：LangGraph 的 callback 会自动传播到节点内的 LLM 调用！**
- 节点内 `llm.ainvoke(msgs)` **不需要手动传 config/callbacks**
- graph 级别传了 callback，所有子节点的 LLM 调用都会自动触发
- **这意味着只要在 `agent_router_service.astream()` 的 RunnableConfig 中加入 cost callback，所有 LLM 调用都能被追踪**

但是：**regenerate 场景中 `agent.ainvoke(inputs)` 不走 graph，需要单独传 callbacks。**

### 3.3 需要做的统一化（简化版）

基于以上测试结论，统一化工作大幅简化：

1. **`ModelService` / `prompt_config.create_llm()` 所有 OpenAI 加 `stream_usage=True`** — 必须
2. ~~所有 `agent.ainvoke()` / `llm.astream()` 传入 callbacks~~ → **不需要！graph 自动传播**
3. **只需在 regenerate 入口手动传 callbacks**（因为 regenerate 不走 LangGraph）
4. **启用 `CreditCheckCallbackHandler.on_llm_end` 和 `on_tool_end`**

### 3.4 callback 成本追踪的局限

| 项目 | callback 能追踪 | 说明 |
|------|----------------|------|
| LLM token 成本 | ✅ | `on_llm_end` → `usage_metadata` → `ToolService.calculate_cost()` |
| Tool 调用成本（图像/视频/音频） | ⚠️ 部分 | `on_tool_end` 能拿到 tool name 和部分参数，但不一定有 duration/resolution 等精确信息 |
| 外部 API 调用成本 | ❌ | WaveSpeed/Suno 等外部 API 成本需要从 tool 返回值里提取 |

---

## 四、自主成本计算 vs LangSmith（已验证）

### 4.1 成本一致性 ✅ 已验证

> 测试 `test_callback_cost_vs_langsmith`：callback 自算 vs LangSmith，**偏差 0.0%，完全一致。**

```
Callback:  $0.00006040 (GPT-4.1-mini, in=19, out=33)
LangSmith: $0.00006040
差额: $0.00000000 (0.0%)
```

**结论：callback + ToolService.calculate_cost() 定价表完全准确，可以替代 LangSmith。**

### 4.2 LangSmith resume run_id — read_run vs trace 查询差异

> 测试 `test_langsmith_resume_run_id_deep` + `test_read_run_aggregation_timing`

**LangSmith 确实记录了 resume 的全部数据（前台可见）**，但 API 查询方式不同结果不同：

| 查询方式 | total_tokens | total_cost | 包含 resume? | 等 30s 后更新? |
|----------|-------------|------------|-------------|---------------|
| `read_run(run_id)` | 21 | $0.0000168 | ❌ 只有 Phase 1 | ❌ **不会更新** |
| `list_runs(trace_id=run_id)` → ROOT | **59** | **$0.0000452** | ✅ 全部 | ✅ |
| `list_runs(parent_run_id=run_id)` → sum children | **59** | — | ✅ 全部 | ✅ |

**trace 下的完整数据（7 个 runs）：**
```
ROOT: LangGraph, tokens=59, cost=$0.0000452   ← 包含全部
  ├─ s1 (Phase 1): tokens=21
  │  └─ ChatOpenAI: tokens=21
  ├─ s1 (resume): tokens=22
  │  └─ ChatOpenAI: tokens=22
  └─ s2 (resume): tokens=16
     └─ ChatOpenAI: tokens=16
```

**影响：**
- 当前 `LangSmithCostService.get_total_cost(run_id)` 如果用 `read_run` → ⚠️ **漏算 resume 成本**
- 改用 `list_runs(trace_id=run_id)` 取 ROOT → ✅ 可以拿到正确成本
- **但 `read_run` 即使等 30 秒也不更新，是 API 行为差异而非延迟**

**解决方案（三选一）：**
1. `LangSmithCostService` 改用 `list_runs(trace_id=run_id)` 查 ROOT → 能拿到完整成本
2. **✅ 推荐：用 callback 自算成本** — 实时、无 API 依赖、已验证偏差 0.0%
3. LangSmith 作为审计对账（用 trace 查询），callback 自算作为主扣款依据

### 4.3 对比总结

| 维度 | 自主计算（callback） | LangSmith `read_run` | LangSmith `list_runs(trace_id)` |
|------|---------------------|---------------------|-------------------------------|
| 可靠性 | ✅ 高（本地计算） | ⚠️ 低（API 依赖） | ⚠️ 低（API 依赖） |
| 准确度 | ✅ 偏差 0.0% | ❌ **resume 成本永久丢失** | ✅ 包含全部 |
| 延迟 | ✅ 实时 | ❌ 需等待 | ❌ 需等待 |
| 复杂度 | ⚠️ 需统一 callback | ✅ 已实现 | ⚠️ 需改查询方式 |

### 4.4 推荐方案：callback 为主，LangSmith 审计

```
主算：callback 自主计算（实时累计 LLM + Tool 成本）
校验：LangSmith 成本（仅对首次 run 做对比，发现偏差时告警）
```

**好处：**
- 不再依赖 LangSmith 做扣款（LangSmith 404 不影响计费）
- callback 实时累计，任务结束时成本已知，billing_worker 不需要等待
- **resume 成本不会丢失**
- LangSmith 作为审计校验（仅首次 run 有效）

### 4.5 model_name 映射问题（已验证）

> 测试 `test_model_identification_in_on_llm_end`

`on_llm_end` 中 `response_metadata.model_name` 返回的是**带日期后缀的完整名**：

| response_metadata.model_name | 我们的 LLMModel enum | model_provider |
|------------------------------|---------------------|----------------|
| `gpt-4.1-mini-2025-04-14` | `GPT_4_1_MINI = "gpt-4.1-mini"` | `openai` |
| `gemini-2.5-flash` | `GEMINI_2_5_FLASH = "gemini-2.5-flash"` | `google_genai` |

**映射方案：**
```python
def resolve_llm_model(model_name: str, model_provider: str) -> Optional[LLMModel]:
    """从 on_llm_end response_metadata 映射到 LLMModel enum"""
    if not model_name:
        return None
    # 优先精确匹配
    for m in LLMModel:
        if model_name == m.value:
            return m
    # 前缀匹配（处理 gpt-4.1-mini-2025-04-14 等带日期后缀的名称）
    for m in LLMModel:
        if model_name.startswith(m.value):
            return m
    return None
```

### 4.6 定价核对（已验证，2026-02 最新）

| 模型 | 我们的定价 | 官方最新定价 | 状态 |
|------|-----------|-------------|------|
| GPT-4.1-mini input | $0.40/1M | $0.40/1M | ✅ 正确 |
| GPT-4.1-mini cached | $0.10/1M | — (未单列) | ✅ |
| GPT-4.1-mini output | $1.60/1M | $1.60/1M | ✅ 正确 |
| GPT-5-nano input | $0.05/1M | $0.05/1M | ✅ 正确 |
| GPT-5-nano cached | $0.005/1M | $0.005/1M | ✅ 正确 |
| GPT-5-nano output | $0.40/1M | $0.40/1M | ✅ 正确 |
| Gemini 2.5 Flash input | $0.30/1M | $0.30/1M | ✅ 正确 |
| Gemini 2.5 Flash cached | $0.03/1M | **$0.05/1M** (explicit) | ⚠️ 偏低 |
| Gemini 2.5 Flash output | $2.50/1M | $2.50/1M (含 thinking) | ✅ 正确 |

> **注意事项：**
> - ⚠️ OpenAI 官网 $0.80/$3.20 是 **fine-tuning** 价格，不是 standard API 价格
> - Gemini **thinking tokens 包含在 output 价格中**（$2.50/1M），无需单独计费
> - `usage_metadata.output_tokens` 已经包含 reasoning tokens，直接用即可
> - Gemini cached 我们的 $0.03 略低于实际 $0.05，但目前影响极小（我们很少用 caching）

### 4.7 ainvoke vs astream 字段统一（已验证）

> 测试 `test_stream_usage_field_details`

| 字段 | ainvoke | astream(True) | astream(False) |
|------|--------|---------------|----------------|
| `usage_metadata` | ✅ 有 | ✅ 有 | ❌ null |
| `response_metadata.token_usage` | ✅ 有 (OpenAI 原生) | ❌ null | ❌ null |

**结论：统一用 `usage_metadata`。** 它的 keys 在 ainvoke 和 astream(True) 下完全一致：
`input_tokens`, `output_tokens`, `total_tokens`, `input_token_details`, `output_token_details`

`response_metadata.token_usage` 只有 ainvoke 才有，streaming 没有 → 不用它。

### 4.8 interrupt/resume 分段对账 — billing_worker 如何知道 run 结束？（已验证 + 深入分析）

> 测试 `test_langsmith_resume_run_id_deep`

#### 4.8.1 billing_worker 已经知道 run 结束了

**核心机制：`billing_status=pending` 只在终态时设置。**

```python
# task_worker._process_task_internal() finally 块：
if final_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
    if await async_set_conversation_run_billing_pending_if_not_completed(run_id):
        kwargs["billing_status"] = BillingStatus.PENDING.value
```

| Phase 状态 | billing_status | billing_worker 处理 |
|---|---|---|
| Phase 1 → **INTERRUPTED** | ❌ 不设 pending | ❌ 不处理 |
| Phase 2 → **COMPLETED** | ✅ 设为 pending | ✅ 扫到后处理 |
| Phase N → **FAILED/CANCELLED** | ✅ 设为 pending | ✅ 扫到后处理 |

**结论：billing_worker 不需要额外机制来判断 run 是否结束。它只在终态时被激活。**

#### 4.8.2 callback 成本如何跨 phase 累加？

每个 phase 是一次独立的 `_execute_agent()` 调用，有**独立的 callback 实例**。问题是如何把多个 phase 的成本加起来。

**方案：`additional_data` 按 message_id 存储每个 phase 的成本（幂等）**

```python
# conversation_runs.additional_data (jsonb):
{
    "callback_cost_by_phase": {
        "sqs_msg_abc123": 0.05,   # Phase 1 (new task)
        "sqs_msg_def456": 0.03    # Phase 2 (resume)
    },
    "callback_cost_total": 0.08   # sum
}
```

**为什么用 SQS message_id 做 key？**
- 每个 SQS 消息对应一次 `_execute_agent()` 调用
- SQS 重试（worker 崩溃后消息重新可见）= 同一个 message_id → **覆盖同一个 key（幂等）**
- 新的 phase（resume）= 新的 SQS 消息 = 新的 message_id → **新增 key**

**流程：**
```
Phase 1 (new task, message_id=abc):
  1. task_worker 创建 CostCallback
  2. agent 执行 → callback 累计 $0.05
  3. agent interrupt → final_status = INTERRUPTED
  4. finally: 写 additional_data.callback_cost_by_phase.abc = 0.05
  5. billing_status 不变 → billing_worker 不处理

Phase 2 (resume, message_id=def):
  1. task_worker 创建新 CostCallback
  2. agent 继续执行 → callback 累计 $0.03
  3. agent 完成 → final_status = COMPLETED
  4. finally: 读 additional_data → 追加 def=0.03 → 写回
  5. callback_cost_total = 0.05 + 0.03 = 0.08
  6. billing_status = pending → billing_worker 扫到

billing_worker:
  读 callback_cost_total = 0.08 → 扣 8 积分 → completed
```

#### 4.8.3 幂等 & 防重复

| 场景 | 处理 |
|---|---|
| Phase 1 正常 + Phase 2 正常 | 两个不同 message_id，各存一次 |
| Phase 1 崩溃重试（同 message_id） | 重试覆盖同一个 key（幂等） |
| Phase 2 崩溃重试（同 message_id） | 同上 |
| 同一个 callback 重复触发？ | 不可能：每次 `_execute_agent` 创建新 callback 实例 |

**不用 atomic ADD！** 用 `key=message_id` 覆盖写（幂等），然后 sum 所有 values。避免崩溃重试导致重复累加。

#### 4.8.4 ⚠️ 关键问题：INTERRUPTED 不扣费（用户永远不点继续怎么办？）

**当前代码（有 bug）：**
```python
# task_worker._process_task_internal() finally 块 L740-742:
if final_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
    # ← INTERRUPTED 不在这里！billing_status 永远不设 pending
    kwargs["billing_status"] = BillingStatus.PENDING.value
```

**问题场景：**
```
Phase 1: 用户发起任务 → Agent 执行 → LLM+Tool 消耗 $0.30 → INTERRUPTED
         billing_status 不变 → billing_worker 不处理
         
Phase 2: 用户永远不点"继续"
         
结果: $0.30 的成本永远不扣费！用户白嫖。
```

#### 4.8.5 ✅ 解决方案：每个 Phase 结束立即扣款（不等 COMPLETED）

**核心改变：不再等整个 run 结束才扣费，每个 phase 结束就扣当前 phase 的费用。**

```python
# task_worker._process_task_internal() finally 块改造：
finally:
    if final_status is not None:
        # ===== 新增：Phase 级扣款（不管什么终态都扣当前 phase 的费用）=====
        phase_cost = cost_callback.total_cost if cost_callback else 0.0
        phase_credits = int(phase_cost * CREDITS_PER_DOLLAR)
        
        if phase_credits > 0:
            try:
                # 幂等: reference_id = "phase:{message_id}"
                # SQS 重试时 message_id 相同 → 不会重复扣
                await use_credits(
                    user_id=task_data.get("user_id"),
                    credits=phase_credits,
                    reference_id=f"phase:{message_id}"
                )
            except Exception as e:
                logger.error(f"Phase billing failed: run_id={run_id}, error={e}")
        
        # 存储 phase 成本到 additional_data（审计用）
        await _store_phase_cost(run_id, message_id, phase_cost)
        
        # ===== 原有逻辑：状态更新 =====
        if final_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            # 终态：标记 billing_status = completed
            billing_status = BillingStatus.COMPLETED
        elif final_status == TaskStatus.INTERRUPTED:
            # 非终态：当前 phase 已扣费，run 可能继续
            # billing_status 不设 pending（因为已经扣了）
            pass
```

**完整时间线：**
```
=== 场景 A: 用户完成全流程 ===
Phase 1: Agent 运行 → callback $0.30 → INTERRUPTED → 立即扣 $0.30 ✅
Phase 2: 用户点"继续" → callback $0.20 → COMPLETED → 立即扣 $0.20 ✅
  billing_status = completed
  总扣费: $0.50 ✅

=== 场景 B: 用户永远不点继续 ===
Phase 1: Agent 运行 → callback $0.30 → INTERRUPTED → 立即扣 $0.30 ✅
Phase 2: 不存在
  已扣 $0.30 → 正确！用户为已消耗的资源付费 ✅

=== 场景 C: Phase 中途崩溃 ===
Phase 1: Agent 运行 → callback $0.15 → 崩溃（finally 仍执行）→ 立即扣 $0.15 ✅
Phase 1 重试: 新 SQS 消息 → callback $0.30 → INTERRUPTED → reference_id 不同 → 扣 $0.30
  总扣: $0.15 + $0.30 = $0.45 → 略多（可通过审计发现）
  
=== 场景 D: finally 也崩了（极端）===
Phase 1: Agent 运行 → callback $0.30 → OOM 杀进程 → finally 没执行
SQS 消息重新可见 → Phase 1 重试 → callback $0.30 → 正常扣 $0.30 ✅
```

**场景 C 的偏差问题：** 崩溃重试导致同一 phase 扣了两次。解决：
- 使用稳定的 `phase_id`（如 `run_id + phase_sequence`），不用 message_id
- 或：接受小额偏差（$0.15），审计对账时发现

#### 4.8.6 billing_worker 的新角色

**billing_worker 不再是主计费机制，变成纯审计 + 异常恢复：**

| 角色 | 触发条件 | 处理 |
|------|----------|------|
| **审计对账** | COMPLETED 的 run | 对比 `sum(phase_costs)` vs `LangSmith list_runs(trace_id)` |
| **crash recovery** | run 有 callback_cost 但扣款失败 | 补扣 |
| **孤儿清理** | INTERRUPTED > 24h 且无后续 | 标记为 abandoned（可选） |

**扫描逻辑变化：**
```python
# 旧：扫描 billing_status=pending → 查 LangSmith → 扣款
# 新：扫描 billing_status=completed → 查 LangSmith → 对比 → 偏差告警
#     扫描 billing_status=phase_billing_failed → 补扣
```

#### 4.8.7 LangSmith 对账（总额对比，不需分段）

```
callback 总扣费:     sum(additional_data.callback_cost_by_phase.values()) = $0.50
LangSmith 总成本:    list_runs(trace_id=run_id) → ROOT.total_cost = $0.50
差额:               $0.00 → ✅ 一致
```

- 不需要精确到每个 interrupt/resume segment
- 只需 **总额对比** — callback 侧自然累加，LangSmith 侧用 `list_runs(trace_id)` 查 ROOT
- 如果偏差 > 20% 告警

### 4.9 余额检查时机 — Node 级别方案（已验证 + 重新设计）

> 测试 `test_langgraph_node_boundary_detection` + **`test_langgraph_node_metadata_in_callbacks`**（新）

#### 4.9.1 关键发现：metadata 只在 `*_start` callback 中可用！（新验证）

| callback 类型 | `metadata` 可用？ | `langgraph_node` 可用？ | 说明 |
|---|---|---|---|
| `on_chain_start` | ✅ | ✅ `= "analyze"` | 有完整 metadata |
| `on_chat_model_start` | ✅ | ✅ `= "analyze"` | 有完整 metadata + ls_provider 等 |
| `on_tool_start` | ✅ | ✅ `= "tools"` | 有完整 metadata |
| **`on_chain_end`** | **❌ 空** | **❌** | **metadata 不传到 end callback** |
| **`on_llm_end`** | **❌ 空** | **❌** | **metadata 不传到 end callback** |
| **`on_tool_end`** | **❌ 空** | **❌** | **metadata 不传到 end callback** |

这意味着：**不能直接在 `on_chain_end` 中读 `metadata["langgraph_node"]`**。

LangGraph 自动填充的 metadata keys（在 start callbacks 中可用）：
```python
['langgraph_step', 'langgraph_node', 'langgraph_triggers', 
 'langgraph_path', 'langgraph_checkpoint_ns', 'checkpoint_ns',
 'ls_provider', 'ls_model_name', 'ls_model_type', 'ls_temperature']
```

#### 4.9.2 Node 边界检测方案对比（已验证 + 生产 graph 分析）

**测试结论（简单 graph 中都能用）：**

| 方案 | 原理 | 简单 graph | 推荐？ |
|------|------|------------|--------|
| A: `parent_run_id == root_run_id` | on_chain_end 中 parent 是 graph root | ✅ 4 个 | ⚠️ 见下 |
| B: `metadata["langgraph_node"]` in on_chain_end | 直接读 metadata | ❌ 0 个 | ❌ |
| C: name tracking | on_chain_start 记录 `{run_id: name}` | ✅ 4 个 | ⚠️ 见下 |
| D: `tags["graph:step:N"]` | LangGraph 内部 tag | ✅ 能用 | ❌ 内部实现 |

#### 4.9.3 ⚠️ 生产 graph 的问题：2 层嵌套 + 并行

**实际 graph 结构：**
```
Router Graph (Level 1, 7 nodes)
  process_user_request → conditional → route_to_video → END
                                     → route_to_story → END
                                     → ...

route_to_video 内部调用:
  video_agent.ainvoke(execution_state) → Video Agent Graph (Level 2, 21 nodes)
    user_input_analysis → music_generation → video_analysis → outline_generation
    → [main_character_design, music_bgm_generation]  ← 并行
    → gate_after_character → scene_generation → visual_elements_matching
    → [storyboard_detail_generation, character_fusion]  ← 并行
    → keyframe_generation → keyframe_reflection → gate_after_keyframe_reflection
    → video_generation → gate_after_shots → video_segments → video_lipsync
    → video_assembly → END
```

**callback 的 parent_run_id 链：**
```
Router root (run_id=R, parent=None)
  ├─ process_user_request (parent=R) → chain_end(parent=R) ← 能检测
  └─ route_to_video (parent=R)
      └─ Video Agent root (parent=route_to_video_run_id, 不是 R!)
          ├─ user_input_analysis (parent=video_root)  ← parent ≠ R, 检测不到!
          ├─ music_generation (parent=video_root)     ← 检测不到!
          ├─ ... 21 个 node (parent=video_root)       ← 全部检测不到!
          └─ video_assembly (parent=video_root)        ← 检测不到!
      chain_end(parent=R) ← route_to_video 结束  ← 唯一能检测到的
```

**`parent_run_id == R` 在生产 graph 中只能检测到 2 个事件：**
1. `process_user_request` 结束（~1 秒）
2. `route_to_video` 结束（= 整个 video pipeline 结束，~5-10 分钟后）

**Video Agent 的 21 个 node 全部检测不到！** 而这正是成本最高、时间最长的部分。

> 要检测 Level 2 的 node，需要知道 `video_root` 的 run_id，这要求多层 root 追踪，
> 代码复杂度指数级上升，且对每层嵌套都要特殊处理。**不推荐。**

#### 4.9.4 ✅ 推荐方案：成本阈值触发（不需要 node 检测）

**既然 node 检测在嵌套 graph 中不可靠，换一种思路：按累计成本增量触发余额检查。**

```python
class CostTrackingCallbackHandler(AsyncCallbackHandler):
    """实时成本追踪 + 阈值余额检查"""
    
    COST_CHECK_THRESHOLD = 0.05  # 每累计 $0.05 检查一次余额（≈5 积分）
    
    def __init__(self, user_id, run_id):
        self.user_id = user_id
        self.run_id = run_id
        self.total_cost = 0.0
        self._cost_at_last_check = 0.0
    
    async def on_llm_end(self, response, *, run_id, **kwargs):
        cost = self._calculate_llm_cost(response)
        self.total_cost += cost
        
        # 累计成本增量超过阈值 → 检查余额
        if self.total_cost - self._cost_at_last_check >= self.COST_CHECK_THRESHOLD:
            await self._check_balance()
            self._cost_at_last_check = self.total_cost
    
    async def on_tool_end(self, output, *, run_id, **kwargs):
        cost = self._calculate_tool_cost(output, kwargs)
        self.total_cost += cost
        # Tool 最贵（视频 $0.06-$0.90），每次都检查
        await self._check_balance()
        self._cost_at_last_check = self.total_cost
```

**为什么成本阈值比 node 检测更好？**

| 维度 | Node 检测 (parent_run_id) | 成本阈值 |
|------|--------------------------|----------|
| 嵌套 graph | ❌ 只检测 Level 1 | ✅ 任何深度 |
| 并行 node | ⚠️ 可能遗漏 | ✅ 不受影响 |
| 代码复杂度 | 高（多层 root 追踪） | **低（一个 if 判断）** |
| 检查频率 | 取决于 graph 结构 | **取决于成本（越贵查越频）** |
| 适应性 | graph 改了要改检测逻辑 | **graph 随便改都不影响** |

**阈值建议：**
- `$0.05`（≈5 积分）— 适合大多数场景
- 视频生成 tool 单次 $0.06-$0.90 → 每次 tool_end 都查
- LLM 单次 ~$0.001-$0.01 → 约 5-50 次 LLM 调用后查一次

**`metadata["langgraph_node"]` 仍然有用 — 但只用于日志/调试，不用于余额检查：**
```python
async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
    # 用于日志：知道这次 LLM 调用属于哪个 node
    node = kwargs.get("metadata", {}).get("langgraph_node", "unknown")
    logger.debug(f"LLM 调用开始: node={node}")
```

#### 4.9.5 延迟分析

| 操作 | 延迟 | 说明 |
|------|------|------|
| callback 本身 | ~0ms | 进程内 Python 调用 |
| 成本阈值判断 | ~0ms | 简单浮点比较 |
| 余额检查（Redis 缓存） | ~1-5ms | Redis TTL=5s |
| 余额检查（DB 回查） | ~10-50ms | 仅缓存 miss 时 |

**结论：无延迟问题。** callback 是同步 Python 调用，阈值判断是内存操作，余额用 Redis 缓存。

---

## 五、计费方案设计

### 5.1 核心思路

不做「预扣估算」—— 因为 LLM 成本确实无法准确预估。
改为 **「实时累计 + 余额检查 + 事后扣款」** 三层：

```
第一层：入口余额检查（拒绝 0 积分用户）
第二层：运行时 callback 实时累计 + node/tool 级余额检查
第三层：任务结束后，用 callback 累计的成本扣款（billing_worker 或直接扣）
校验层：LangSmith 成本对比（用 list_runs(trace_id) 审计）
```

### 5.2 详细流程

#### 阶段 1：入口检查

```python
# 任务开始前 —— 所有入口统一
estimated = get_estimated_credits(agent_type, run_type)
user_credit = await get_user_credits(user_id)
if user_credit.balance < estimated:
    raise BusinessException(INSUFFICIENT_CREDITS, "积分不足")
# 注意：这里只检查不扣款
```

为什么不预扣？
- LLM 成本无法准确预估（可能 $0.01 也可能 $0.50）
- Tool 成本更难预估（视频生成 = $0.90/5s，不知道会生成多少条）
- 预扣后的结算逻辑复杂（退款、补扣、幂等）
- **用入口检查 + 运行时检查替代预扣，更简单可靠**

#### 阶段 2：运行时 callback 成本累计

```python
class CostTrackingCallbackHandler(AsyncCallbackHandler):
    """实时成本追踪 callback"""
    
    def __init__(self, user_id, run_id):
        self.user_id = user_id
        self.run_id = run_id
        self.total_cost = 0.0       # 累计成本（美元）
        self.llm_costs = []         # 每次 LLM 调用的成本明细
        self.tool_costs = []        # 每次 Tool 调用的成本明细
        self._check_counter = 0     # 余额检查计数器
    
    async def on_llm_end(self, response, **kwargs):
        # 1. 提取 model_name + token usage
        # 2. ToolService.calculate_cost() 算成本
        # 3. 累加到 self.total_cost
        # 4. 每 N 次检查余额，不足时抛异常中断
        
    async def on_tool_end(self, output, **kwargs):
        # 1. 识别 tool type
        # 2. 提取参数（duration, resolution 等）
        # 3. ToolService.calculate_cost() 算成本
        # 4. 累加到 self.total_cost
```

**余额检查策略（成本阈值触发，不依赖 node 检测）：**
- **每次 `on_tool_end`** 检查余额（Tool 最贵：视频 $0.06-$0.90）
- **累计成本增量 ≥ $0.05 时** 检查余额（LLM 成本约 5-50 次调用后触发一次）
- 用 Redis 缓存余额（TTL 5s），减少 DB 压力
- 如果 `累计成本 × CREDITS_PER_DOLLAR > 用户余额`，抛 `INSUFFICIENT_CREDITS` 中断任务
- **不用 node 检测** — 在嵌套 graph（Router→VideoAgent 21 nodes）中 `parent_run_id` 不可靠（详见 4.9.3）

#### 阶段 3：每个 Phase 结束 → 立即扣款

**核心改变：不等整个 run 结束，每个 phase（每次 `_execute_agent()`）结束就扣当前 phase 的费用。**

```
Phase 结束（INTERRUPTED / COMPLETED / FAILED / CANCELLED，都算）
  ↓
task_worker finally: 从 callback 获取 phase_cost
  ↓
直接调 use_credits(user_id, credits, reference_id=f"phase:{message_id}")
  ↓
存储 phase_cost 到 additional_data.callback_cost_by_phase
  ↓
如果是终态(COMPLETED/FAILED/CANCELLED): billing_status = completed
如果是 INTERRUPTED: 不设 pending（已经扣了这个 phase 的费用）
```

- ✅ INTERRUPTED 不继续也能扣费
- ✅ 不需要 billing_worker 做主计费
- ✅ 不需要等 LangSmith
- ✅ 每个 phase 成本已知，立即扣

**billing_worker 降为审计 + 异常恢复（详见 4.8.6）**

### 5.3 Regenerate 特殊处理

Regenerate 是同步执行（endpoint 直接 await），callback 更好用：

```python
@router.post("/video-editing/regenerate-keyframes")
async def regenerate_keyframes(request, user_id):
    # 1. 入口余额检查
    check_credits(user_id, estimated=20)
    
    # 2. 创建 callback
    cost_callback = CostTrackingCallbackHandler(user_id, run_id)
    
    # 3. 执行（传 callback）
    result = await service.regenerate_keyframes_by_request(
        ..., callbacks=[cost_callback]
    )
    
    # 4. 直接扣款（不需要 billing_worker）
    credits = int(cost_callback.total_cost * CREDITS_PER_DOLLAR)
    if credits > 0:
        await use_credits(user_id, credits, reference_id=run_id)
    
    # 5. 写 conversation_run（billing_status=completed）
    await create_conversation_run(..., billing_status="completed", credits_amount=credits)
```

**优势：不走 billing_worker，任务结束立即扣，简单可靠。**

### 5.4 主流程 / Resume 处理

主流程是异步的（SQS → task_worker），callback 需要在 task_worker 里传递：

```python
# task_worker._process_task_internal() 中
cost_callback = CostTrackingCallbackHandler(user_id, run_id)

# 传入 RunnableConfig（在 _execute_agent 中）
config = RunnableConfig(
    callbacks=[cost_callback],  # 成本追踪 + 余额检查（合并到一个 callback）
    ...
)

# finally 块中（不管终态是什么）
phase_cost = cost_callback.total_cost
phase_credits = int(phase_cost * CREDITS_PER_DOLLAR)
if phase_credits > 0:
    await use_credits(user_id, phase_credits, reference_id=f"phase:{message_id}")
```

**每个 Phase 独立计费，包括 INTERRUPTED：**
```
Phase 1 (INTERRUPTED): callback $0.30 → 扣 $0.30 → 用户不继续也没关系
Phase 2 (resume, COMPLETED): callback $0.20 → 扣 $0.20 → billing_status=completed
```

---

## 六、实现计划

### Phase 1：修复 billing_worker 无限循环（紧急）

**不依赖 callback，只修 bug：**

1. `_deduct_credits_with_session` 余额不足加 return ✅ （已修）
2. billing_worker 加重试上限：LangSmith 失败 10 次 → failed
3. billing_worker 加扣款重试上限：失败 3 次 → failed
4. BillingStatus.FAILED 实际使用
5. 入口余额检查（最低积分门槛）

### Phase 2：启用 callback 自主成本追踪

1. **统一 `stream_usage=True`**
   - `ModelService` 所有 OpenAI LLM
   - `prompt_config.create_llm()` 所有 OpenAI 配置

2. **启用 `CreditCheckCallbackHandler`**
   - 取消 `on_llm_end` 和 `on_tool_end` 的 `pass`，恢复注释代码
   - 加成本累计逻辑（用 `ToolService.calculate_cost()`）

3. **callback 传递（已简化）**
   - ~~`agent.ainvoke()` 传 callbacks~~ → **不需要，graph 自动传播**
   - ~~`llm.astream()` 传 callbacks~~ → **不需要，graph 自动传播**
   - **只需在 regenerate 入口传 callbacks**（不走 graph 的场景）

4. **验证 test** ✅ 已完成
   - `tests/billing/test_callback_and_cost_tracking.py` — 9 项全部通过

5. **修复 regenerate conversation_run 创建时机** — P0.5
   - 预扣后立即创建 conversation_run，不放在 finally 里

### Phase 3：callback 成本直接扣款

1. task_worker 中：任务结束时从 callback 获取 total_cost → 直接扣款
2. regenerate 端点：同步扣款（不走 billing_worker）
3. billing_worker 降级为兜底（callback 数据丢失时才用 LangSmith）

### Phase 4：LangSmith 审计对账

1. billing_worker 改为对账模式：对比 callback_cost 和 langsmith_cost
2. 偏差超过 20% 时告警
3. 记录对账日志供审计

---

## 七、幂等 & 防御设计

### 7.1 幂等

| 操作 | 幂等键 | 机制 |
|------|--------|------|
| 扣款 | `credit_history.reference_id = run_id` | 扣款前查是否已有相同 reference_id |
| 结算退回 | `credit_history.reference_id = settle:{run_id}` | 同上 |
| 失败退回 | `credit_history.reference_id = fail_refund:{run_id}` | 同上 |
| billing 状态写入 | `conversation_runs.billing_status` | 只处理 pending，completed/failed 不重入 |

### 7.2 防御

| 场景 | 防御 |
|------|------|
| credit_history.reference_id 列不存在 | `use_credits` fallback：用 raw insert 不含 reference_id |
| `already_deducted_for_run` 查询失败 | catch 异常 → 返回 False（视为未扣） |
| callback 内异常 | catch → log → 不中断任务（成本追踪失败不应阻止用户） |
| LangSmith 不可用 | callback 自主成本作为主算，LangSmith 仅审计 |
| 用户积分不足（运行时） | callback 余额检查 → 抛 INSUFFICIENT_CREDITS → 中断任务 |
| DB 不可用（扣款时） | 重试 3 次 → 失败 → billing_status=failed → 人工介入 |

### 7.3 状态机

```
NULL → pending（task_worker 完成 / regenerate finally）
pending → completed（扣款成功）
pending → failed（重试超限 / 无法扣款）
failed → pending（人工重置，可选）
```

---

## 八、数据模型

### conversation_runs 相关字段

| 字段 | 类型 | 说明 |
|------|------|------|
| billing_status | varchar(32) | pending / completed / failed |
| langsmith_cost | float | LangSmith 返回的成本（美元），审计用 |
| cost | float | 我方计算的成本（美元），扣款依据 |
| cost_calculated | bool | 成本是否已计算 |
| credits_deducted | bool | 积分是否已扣除 |
| credits_amount | int | 实际扣除的积分数 |
| additional_data | jsonb | 可存 callback_cost、credits_pre_deducted 等 |

### credit_history 关键字段

| 字段 | 类型 | 说明 |
|------|------|------|
| reference_id | varchar(64) | 幂等键：run_id / pre:{run_id} / settle:{run_id} / fail_refund:{run_id} |
| operation_type | varchar(50) | use / pre_deduct / settle_refund / settle_additional / video_generation 等 |

---

## 九、验证结果汇总

> 所有测试位于 `tests/billing/test_callback_and_cost_tracking.py`
> 运行：`conda run -n cuti-video-local pytest tests/billing/test_callback_and_cost_tracking.py -v -s`

### 9.1 测试清单 — 全部通过 ✅

**第一轮：`tests/billing/test_callback_and_cost_tracking.py`**

| # | 测试 | 结果 | 关键数据 |
|---|------|------|----------|
| 1 | `test_openai_ainvoke_on_llm_end` | ✅ | on_llm_end 触发, in=15 out=1 |
| 2 | `test_openai_astream_on_llm_end` | ✅ | stream_usage=True → in=19 out=9; False → in=0 out=0 |
| 3 | `test_gemini_astream_on_llm_end` | ✅ | 默认有 usage, in=10 out=128 |
| 4 | `test_gemini_ainvoke_cost` | ✅ | cost=$0.00008640, 含 reasoning tokens |
| 5 | `test_openai_structured_output_callback` | ✅ | in=81 out=12, 解析正常 |
| 6 | `test_langgraph_astream_callback_propagation` | ✅ | **2 个节点都触发**, callback 自动传播 |
| 7 | `test_langsmith_resume_same_run_id` | ✅ | read_run 只记录第一次 |
| 8 | `test_callback_cost_vs_langsmith` | ✅ | **偏差 0.0%**, 完全一致 |
| 9 | `test_langgraph_interrupt_resume_run_id` | ✅ | callback 捕获全部 |

**第二轮：`tests/billing/test_callback_details.py`**

| # | 测试 | 结果 | 关键数据 |
|---|------|------|----------|
| 10 | `test_model_identification_in_on_llm_end` | ✅ | `response_metadata.model_name`: OpenAI→`gpt-4.1-mini-2025-04-14`, Gemini→`gemini-2.5-flash`; `model_provider`: `openai` / `google_genai` |
| 11 | `test_stream_usage_field_details` | ✅ | ainvoke 有 `token_usage`(OpenAI原生) + `usage_metadata`(langchain统一); astream(True) 只有 `usage_metadata` 没有 `token_usage`; astream(False) 两者都没 |
| 12 | `test_gemini_usage_field_details` | ✅ | Gemini ainvoke/astream 都有 `usage_metadata`，含 `reasoning` tokens; 没有 `token_usage`(OpenAI 专有) |
| 13 | `test_on_tool_end_details` | ✅ | `kwargs["name"]` = tool name; `output.name` = tool name; `output.content` = tool 输出; `serialized["name"]` in tool_start |
| 14 | `test_langsmith_resume_run_id_deep` | ✅ | **LangSmith trace 有全部数据（7 runs），但 `read_run` 只返回 Phase 1** |

**第三轮：`tests/billing/test_langsmith_timing.py`**

| # | 测试 | 结果 | 关键数据 |
|---|------|------|----------|
| 15 | `test_read_run_aggregation_timing` | ✅ | `read_run` 等 3s/5s/10s/15s/20s/**30s** 都只返回 Phase 1 (tokens=21); `list_runs(trace_id)` ROOT 返回 tokens=59 (全部) |

**第四轮：`tests/billing/test_node_detection.py`**

| # | 测试 | 结果 | 关键数据 |
|---|------|------|----------|
| 16 | `test_langgraph_node_boundary_detection` | ✅ | `on_chain_end` + `tags=['graph:step:N']` 可精确检测 node 结束; `parent_run_id == root_run_id` 也可以 |

**第五轮：`tests/billing/test_node_metadata.py`**（新增）

| # | 测试 | 结果 | 关键数据 |
|---|------|------|----------|
| 17 | `test_langgraph_node_metadata_in_callbacks` | ✅ | **metadata（含 `langgraph_node`）只在 `*_start` callback 中有，`*_end` 中 metadata 为空！** 方案 A（parent_run_id==root）= 4 ✅；方案 B（metadata in end）= 0 ❌；方案 C（name tracking）= 4 ✅ |

### 9.2 核心结论

1. **callback + `stream_usage=True` 方案可行**：能实时、准确追踪所有 LLM 成本
2. **LangGraph callback 自动传播**：graph 级别传 callback 后，节点内所有 LLM 调用自动触发
3. **LangSmith `read_run` 不包含 resume 成本**：`read_run(run_id)` 只返回 Phase 1，即使等 30s 也不更新。但 `list_runs(trace_id=run_id)` 的 ROOT 有完整成本
4. **callback 自算成本与 LangSmith 一致**：偏差 0.0%，ToolService 定价表准确
5. **Gemini 不需要额外配置**：默认返回 usage_metadata（含 reasoning tokens）
6. **OpenAI 必须 `stream_usage=True`**：否则 streaming 时 usage 全为 0
7. **模型区分**：`response_metadata.model_name` + `response_metadata.model_provider` 可以准确区分
8. **ainvoke vs astream 字段差异**：ainvoke 有 `response_metadata.token_usage` (OpenAI 原生) + `usage_metadata`；astream(True) 只有 `usage_metadata`。统一用 `usage_metadata` 即可
9. **Tool 信息获取**：`on_tool_end` 的 `kwargs["name"]` 和 `output.name` 都有 tool name；`on_tool_start` 的 `serialized["name"]` 也有
10. **⚠️ metadata 只在 `*_start` callback 有**：`on_llm_end`、`on_chain_end`、`on_tool_end` 的 metadata 为空！必须用 `on_chain_start` 建映射 + `parent_run_id` 在 end 中查找
11. **Node 检测不应用 `graph:step:N` tag**：用 `parent_run_id == root_run_id` + name tracking 替代，不依赖 LangGraph 内部实现

---

## 十、预估积分表（用于入口检查）

仅用于入口的最低余额检查，不用于扣款：

| 任务类型 | 预估积分 | 说明 |
|----------|----------|------|
| video (main) | 50 | ~$0.50, 完整视频生成流水线 |
| video (resume) | 30 | ~$0.30, 继续执行 |
| story | 20 | ~$0.20, 纯文本生成 |
| image | 10 | ~$0.10, 图像生成 |
| music | 10 | ~$0.10, 音乐生成 |
| regenerate_keyframes | 20 | ~$0.20 |
| regenerate_videos | 30 | ~$0.30 |
| regenerate_characters | 15 | ~$0.15 |

---

## 十一、总结

| 维度 | 之前 | 之后 |
|------|------|------|
| 成本来源 | 只靠 LangSmith（resume 成本丢失！） | callback 自主算（主）+ LangSmith（审计） |
| 扣款时机 | billing_worker 异步扫描（只在 COMPLETED 时） | **每个 Phase 结束立即扣**（含 INTERRUPTED） |
| INTERRUPTED 扣费 | ❌ **不扣**（用户不继续 = 白嫖） | ✅ Phase 1 立即扣费 |
| 无限循环 | 有（积分不足 / LangSmith 404） | 无（重试上限 + failed 状态） |
| 入口检查 | 无 | 最低余额检查 |
| 运行时检查 | 无 | callback 成本阈值触发（≥$0.05 查余额 + tool 每次查） |
| 余额检查方式 | — | 成本阈值（不依赖 node 检测，嵌套 graph 通用） |
| billing_worker | 主计费 | **纯审计** + 异常恢复 |
| 幂等 | reference_id | `phase:{message_id}` 幂等键 |
| LangSmith 依赖 | 强依赖（`read_run` 漏 resume） | 弱依赖（审计用 `list_runs(trace_id)` 可拿全量） |
| Regenerate 记录 | finally 中创建（有崩溃风险） | 预扣后立即创建 |

### 关键技术验证结论

1. **callback 自动传播** ✅ — LangGraph graph 级 callback 传到所有子节点 LLM
2. **stream_usage=True 必须** ✅ — OpenAI streaming 无此参数则 usage 全 0
3. **Gemini 默认有 usage** ✅ — 不需要额外配置
4. **callback 成本 = LangSmith 成本** ✅ — 偏差 0.0%
5. **LangSmith `read_run` 不含 resume 成本** ⚠️ — 需改用 `list_runs(trace_id)` 或 callback 自算
6. **regenerate conversation_run 时机有风险** ⚠️ — 需提前创建
7. **metadata 只在 start callbacks 中** ⚠️ — `*_end` callback 的 metadata 为空
8. **Node 检测在嵌套 graph 中不可靠** ⚠️ — `parent_run_id` 只检测 Level 1，Video Agent 21 nodes 全部漏掉
9. **✅ 成本阈值触发替代 node 检测** — 简单、通用、不依赖 graph 结构
10. **⚠️ INTERRUPTED 不扣费是 bug** — 用户不点继续 = 白嫖。解决：每个 Phase 立即扣费
11. **billing_worker 降为审计** — 主计费在 task_worker finally 块中完成
