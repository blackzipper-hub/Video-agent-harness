"""
已迁移：`regenerate_*` 的 instruction 由协议层 `instruction` 字段 + LLM 模板分栏承载。

请运行（真实加载 .env、模板，不扣图生费）：
  conda run -n cuti-video-local python -u tests/test_regenerate_instruction_prompts.py

旧版依赖的 Tool._build_instruction_prompt 已删除；请勿再引用本文件中的用例。
"""
print(__doc__)
