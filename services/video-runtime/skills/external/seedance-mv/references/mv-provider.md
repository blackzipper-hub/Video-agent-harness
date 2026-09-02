# MV 画面生成：Provider / Model 合同

本地 Deep Agent MV 默认走 **WaveSpeed 托管的 ByteDance Seedance 2.0**
（`api.provider.generate` → provider bridge → `/bytedance/seedance-2.0/...`）。

`doubao-…` 只是 bridge 认的 **model 标签**，不是改走豆包/Ark 控制台。

## 默认（必须）

```text
capability: api.provider.generate
provider: wavespeed
model: doubao-seedance-2-0
```

等价合法写法：`seedance-2.0`。

## 回退（仅 2.0 不可用时）

```text
model: doubao-seedance-1-5
duration: ≤12
```

等价合法写法：`seedance-1.5`。

## 禁止

- `seedance`、`seedance-v2`、`seedance2`（太糊，bridge 会直接 ValueError）
- 只写产品名不写版本
- 为了「看起来像 seedance2 skill」去 `load_skill("seedance2")` 或抄 Ark CLI 长 ID 当唯一依据

## 与 seedance2 skill 的关系

| | seedance2 | seedance-mv（本文件） |
|---|---|---|
| 通道 | 多为 Ark CLI / 脚本 | WaveSpeed `api.provider.generate` |
| 默认 ID 示例 | `doubao-seedance-2-0-260128`（Ark） | `doubao-seedance-2-0`（bridge 标签） |
| 依赖 | 可有自己的脚本 | **自包含；不要 load seedance2** |

Ark 长 ID（含 `-260128`）若 bridge 能识别家族也可，但 MV 任务参数请优先写上表短标签，避免 Agent 乱造 `seedance`。
