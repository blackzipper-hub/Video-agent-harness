# 测试说明

## 测试概览

本项目包含三种类型的测试：

### 1. 工具集成测试 (`tools/`)
集成测试调用真实的 AI 服务 API，用于验证完整的功能流程。

#### 可用测试

1. **Suno API 音乐生成** (`test_suno_integration.py`)
   - 测试真实的音乐生成流程
   - 需要配置 Suno API key

2. **Pollo AI 视频生成** (`test_pollo_integration.py`)
   - 测试真实的视频生成流程
   - 需要 `POLLO_API_KEY` 环境变量

3. **Nano Banana 图像生成** (`test_nano_banana_integration.py`)
   - 测试 Google Gemini I2I 图生图功能
   - 使用硬编码的 API key

### 2. 服务单元测试 (`services/`)
测试各个服务的业务逻辑和功能。

### 3. Agent 测试 (`agent/`)
测试 Agent 相关的功能和流程。

## 运行方式

### 命令行运行

```bash
# 运行所有工具集成测试
pytest tests/tools/ -v -s

# 运行特定工具测试
pytest tests/tools/test_suno_integration.py -v -s
pytest tests/tools/test_pollo_integration.py -v -s
pytest tests/tools/test_nano_banana_integration.py -v -s

# 运行服务单元测试
pytest tests/services/ -v

# 运行 Agent 测试
pytest tests/agent/ -v

# 跳过集成测试（只运行单元测试）
pytest -m "not integration" -v
```

### VSCode 调试

1. 按 `F5` 打开调试菜单
2. 选择对应的调试配置：
   - **Debug Suno Integration Test** - 调试音乐生成
   - **Debug Pollo Integration Test** - 调试视频生成
   - **Debug Nano Banana Integration Test** - 调试图像生成

### 环境要求

- **Suno API**: 需要配置 Suno API key
- **Pollo AI**: 需要设置 `POLLO_API_KEY` 环境变量
- **Nano Banana**: API key 已硬编码在代码中

## 注意事项

⚠️ **重要提醒**：
- 集成测试会调用真实 API 并消耗 credits
- 每个测试可能需要几秒到几分钟完成
- 确保网络连接稳定

💡 **测试内容**：
- **Suno**: 音乐生成流程（支持长提示词截取）
- **Pollo**: 完整视频生成流程（提交任务 → 轮询 → 下载保存）
- **Nano Banana**: I2I 图生图（基于参考图片生成新图像）

🔧 **调试技巧**：
- 使用 `-s` 参数查看详细输出
- 在代码中设置断点进行调试
- 查看生成的图片/视频/音频 URL 验证结果
