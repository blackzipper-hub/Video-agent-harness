"""合并单体：复用 VideoAgent 的 ``subscription`` 模型（单一 SQLModel 表定义，避免重复注册）。

原 VideoChatAgent 侧定义了与 VideoAgent 列结构一致的同名表；在同一进程内两次在共享的
SQLModel metadata 上定义同名表会触发 "Table already defined"。合并后统一以 VideoAgent 为准，
通过模块别名让 ``app.chat.models.subscription`` 指向 ``app.models.subscription``。
"""
import sys as _sys

import app.models.subscription as _va_mod

_sys.modules[__name__] = _va_mod
