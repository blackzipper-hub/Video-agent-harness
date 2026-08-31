# 保留文档索引

本目录为 Cuti-VideoAgent **长期保留**的说明文档，仅下列 **6 篇**（主题互不重复；`resolution` 与 `tool-output-specs` 互补，见下）。

| 文件 | 主题 | 说明 |
|------|------|------|
| **llm-and-tool-resilience.md** | LLM 韧性 + Tool 层重试 | `load_prompt`、`llm_resilience.py`、`PROMPTS_CONFIG.resilience`、Provider/Tool 策略、**Video Wrapper 一致性检查与重试**；合并自原 `design/` 下三篇。 |
| **tool-output-specs.md** | 工具实测输出规格 | 视频/图片 API **原始输出**（编码、FPS、分辨率×宽高比、`ensure_on_s3`）；与 `scripts/probe_raw_tool_specs.py`、`scripts/test_gpt_image_2_output_specs.py`、各 `*_specs_results.json` 同源。 |
| **resolution_aspect_ratio_support_and_rules.md** | 分辨率与能力规则 | **产品/选项视角**：各图/视频模型对 480/720/1080 与比例的支持、`get_tool_capabilities` 思路；**建议保留**：`user_options.py` 与 `test_capabilities_cases.py` 仍引用；与上一篇是「**能选什么**」vs「**API 吐出什么**」。 |
| **media-service-and-cpu.md** | Media Service + CPU | 原 Media Service 方案（FFmpeg、存储、API 映射、K8s）；**附录**为 VideoAgent **Regenerate** 与本地 FFmpeg CPU 摘要。 |
| **billing.md** | 计费 | 入口、LangGraph callback、regenerate、LangSmith、`billing_worker`。 |
| **aws-and-kubernetes.md** | AWS / EKS | 部署、VPC、ECR、节点、Ingress、与 EC2 DB/Redis 连线；**第 16 步** EBS CSI；**第 17 步** Loki/Promtail/Grafana 分工与 Explore 查日志。 |

**测试生成物（非人工维护主文档）**：`tests/llm/reports/structured_recovery_matrix_report.md`（由 `test_structured_recovery_matrix_report.py` 刷新）。

---

## 阅读顺序建议

1. 改 Agent / LLM：**llm-and-tool-resilience.md**
2. 改工具输出 / normalize：**tool-output-specs.md**；改前端选项与交集：**resolution_aspect_ratio_support_and_rules.md**
3. 媒体与 CPU：**media-service-and-cpu.md**
4. 计费：**billing.md**
5. 部署：**aws-and-kubernetes.md**

---

## Deep Agent V2（补充）

| 文件 | 说明 |
|------|------|
| **deep-agent-v2-skills.md** | 调用结构、Skill 加载、热加载、沙箱、热加载 vs 改代码 |
| **deep-agent-v2-skills.html** | 同上，含 Mermaid 流程图（浏览器打开） |
| **cuti-architecture.html** | 平台总架构可视化 |