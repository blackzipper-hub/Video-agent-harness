"""
Agent相关的数据模式定义
"""
from dataclasses import dataclass


@dataclass
class VideoContextSchema:
    """Video Agent Runtime Context Schema
    
    ✅ 修复：已删除 async_db 字段，不再通过 context 传递连接
    所有节点按需创建连接，避免长时间占用连接池
    保留 VideoContextSchema 用于未来扩展
    """
    pass
