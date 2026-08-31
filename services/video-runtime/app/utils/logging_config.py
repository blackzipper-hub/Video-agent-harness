"""
日志系统配置
提供统一的日志配置、文件轮转、日志获取功能

功能模块：
1. 日志配置: 控制台和文件日志配置
2. 文件轮转: 按日期轮转日志文件（保留7天）
3. 分类日志: 错误日志单独记录到 error.log
4. 日志获取: 统一的 logger 获取接口

使用场景：
- 应用启动时初始化日志系统
- 各模块获取统一配置的 logger
- 自动管理日志文件轮转和清理
"""

import os
import logging
import logging.handlers
import queue
import threading
from datetime import datetime
from pathlib import Path
from ..config import settings

# 全局变量，用于停止日志监听器
_log_listener = None

def setup_logging():
    """
    配置应用程序的日志系统
    - 采用异步日志架构 (QueueHandler + QueueListener)
    - 彻底解决高并发下同步写日志导致的阻塞问题
    """
    global _log_listener
    
    # 如果已经有监听器在运行，先停止它
    if _log_listener:
        _log_listener.stop()
    
    # 创建logs目录
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # 获取根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    
    # 清除根日志器的现有处理器
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 创建格式器
    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # --- 定义实际执行写入操作的处理器 (Internal Handlers) ---
    
    # 1. 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(file_formatter)
    
    # 2. 文件处理器 - 主日志
    main_file_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_dir / "app.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8"
    )
    main_file_handler.setLevel(logging.DEBUG)
    main_file_handler.setFormatter(file_formatter)
    
    # 3. 错误文件处理器
    error_handler = logging.handlers.TimedRotatingFileHandler(
        filename=log_dir / "error.log",
        when="midnight",
        interval=1,
        backupCount=90,
        encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_formatter)
    
    # 定义要启动的处理器列表
    handlers = [console_handler, main_file_handler, error_handler]
    
    # 3.5 性能测试专用日志（异步处理后不再是瓶颈）
    if os.getenv("LOG_TO_FILE") == "true":
        perf_log_path = os.getenv("LOG_FILE_PATH", "video_agent_performance.log")
        perf_handler = logging.FileHandler(
            filename=perf_log_path,
            mode='a',
            encoding="utf-8"
        )
        perf_handler.setLevel(logging.INFO)
        perf_handler.setFormatter(file_formatter)
        handlers.append(perf_handler)
        print(f"✅ 异步性能日志已启用，输出到: {perf_log_path}")

    # --- 核心：异步日志架构 ---
    
    # 创建消息队列
    log_queue = queue.Queue(-1)  # 无限大小
    
    # 创建 QueueHandler 并添加到根日志器
    # 之后所有的 logger.info/error 调用都会把消息丢进这个队列，立即返回
    queue_handler = logging.handlers.QueueHandler(log_queue)
    root_logger.addHandler(queue_handler)
    
    # 创建 QueueListener，它在后台线程中运行，从队列中取出消息并调用真正的处理器
    _log_listener = logging.handlers.QueueListener(log_queue, *handlers, respect_handler_level=True)
    _log_listener.start()
    
    # 4. 特殊 Logger 配置
    # 设置其他库的日志级别
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.INFO)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # uvicorn --reload 用 watchfiles 监控目录变化。DEBUG 级别下每次写日志都会触发它
    # 再打一行 DEBUG → 那行又写进 log → 再触发 → 自激循环（实测占总日志 39%）。
    # 调到 WARNING 切断反馈环（仍保留 reload 失败时的告警）。
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    
    root_logger.info("="*60)
    root_logger.info(f"StoryBook App 异步日志系统已启动")
    root_logger.info(f"环境: {settings.ENVIRONMENT}")
    root_logger.info("="*60)


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的logger
    
    Args:
        name: logger名称，通常使用 __name__
        
    Returns:
        配置好的logger实例
    """
    return logging.getLogger(name)


# 日志中间件已移动到main.py中作为FastAPI中间件实现