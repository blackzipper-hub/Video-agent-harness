"""
性能分析工具 - 使用 cProfile 分析慢接口
"""
import cProfile
import pstats
import io
import logging
import functools
from typing import Callable, Any
import os

logger = logging.getLogger(__name__)

# 全局开关：是否启用 profiling
# ❌ 禁用 cProfile（对异步代码不work，有性能开销）
PROFILING_ENABLED = False  # os.environ.get("ENABLE_PROFILING", "false").lower() == "true"

# Profiling 结果保存目录
PROFILE_DIR = "/tmp/videoagent_profiles"
os.makedirs(PROFILE_DIR, exist_ok=True)


def profile_endpoint(endpoint_name: str):
    """
    装饰器：对 endpoint 函数进行性能分析
    
    使用方法：
    @profile_endpoint("get_characters")
    async def get_characters_data(...):
        ...
    
    启用方法：
    export ENABLE_PROFILING=true
    
    结果：
    - 打印到日志：最慢的 20 个函数
    - 保存到文件：/tmp/videoagent_profiles/{endpoint_name}.prof
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            if not PROFILING_ENABLED:
                # Profiling 未启用，直接执行
                return await func(*args, **kwargs)
            
            # 启用 profiling
            profiler = cProfile.Profile()
            profiler.enable()
            
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                profiler.disable()
                
                # 生成统计报告
                s = io.StringIO()
                ps = pstats.Stats(profiler, stream=s)
                ps.sort_stats('cumulative')  # 按累计时间排序
                
                # 打印最慢的 20 个函数到日志
                logger.info(f"\n{'='*70}\nProfiling: {endpoint_name}\n{'='*70}")
                ps.print_stats(20)
                logger.info(s.getvalue())
                
                # 保存到文件（可用于后续详细分析）
                profile_file = os.path.join(PROFILE_DIR, f"{endpoint_name}.prof")
                profiler.dump_stats(profile_file)
                logger.info(f"📊 Profile saved to: {profile_file}")
                logger.info(f"   Analyze with: python -m pstats {profile_file}")
        
        return wrapper
    return decorator


def analyze_profile_file(profile_file: str, top_n: int = 30):
    """
    分析保存的 profile 文件
    
    使用方法：
    python -c "from app.utils.profiling import analyze_profile_file; analyze_profile_file('/tmp/videoagent_profiles/get_characters.prof')"
    """
    if not os.path.exists(profile_file):
        print(f"❌ File not found: {profile_file}")
        return
    
    print(f"\n{'='*70}")
    print(f"Analyzing: {profile_file}")
    print(f"{'='*70}\n")
    
    stats = pstats.Stats(profile_file)
    
    print("\n📊 Top functions by cumulative time:")
    print("-" * 70)
    stats.sort_stats('cumulative')
    stats.print_stats(top_n)
    
    print("\n⏱️ Top functions by total time (self):")
    print("-" * 70)
    stats.sort_stats('tottime')
    stats.print_stats(top_n)
    
    print("\n🔢 Top functions by call count:")
    print("-" * 70)
    stats.sort_stats('ncalls')
    stats.print_stats(top_n)
