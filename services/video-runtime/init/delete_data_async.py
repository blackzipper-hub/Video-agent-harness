"""
删除数据库数据 - 异步版本
用于开发和测试环境清理数据
"""
import asyncio
import os
import sys
import logging
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(".env.development")

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete
from app.models.database import AsyncSessionLocal, async_engine

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def delete_all_data():
    """删除所有业务数据"""
    try:
        async with AsyncSessionLocal() as db:
            logger.info("🗑️  开始删除数据库数据...")
            
            deleted_counts = {}
            
            # 导入模型
            from app.models.video.video_character import CharacterDB
            from app.models.video.video_style import StyleDB
            from app.models.video.video_scene import SceneDB
            from app.models.video.video_keyframe import VideoKeyframeDB
            from app.models.video.video_generation import VideoGenerationDB
            from app.models.conversation import ConversationMessageDB, ConversationDB, ConversationRunDB
            
            # 1. 删除角色数据
            result = await db.execute(delete(CharacterDB))
            deleted_counts['角色'] = result.rowcount
            logger.info(f"✅ 删除角色数据: {result.rowcount} 条")
            
            # 2. 删除风格数据
            result = await db.execute(delete(StyleDB))
            deleted_counts['风格'] = result.rowcount
            logger.info(f"✅ 删除风格数据: {result.rowcount} 条")
            
            # 3. 删除场景数据
            result = await db.execute(delete(SceneDB))
            deleted_counts['场景'] = result.rowcount
            logger.info(f"✅ 删除场景数据: {result.rowcount} 条")
            
            # 4. 删除关键帧数据
            result = await db.execute(delete(VideoKeyframeDB))
            deleted_counts['关键帧'] = result.rowcount
            logger.info(f"✅ 删除关键帧数据: {result.rowcount} 条")
            
            # 5. 删除视频生成数据
            result = await db.execute(delete(VideoGenerationDB))
            deleted_counts['视频生成'] = result.rowcount
            logger.info(f"✅ 删除视频生成数据: {result.rowcount} 条")
            
            # 6. 删除对话消息
            result = await db.execute(delete(ConversationMessageDB))
            deleted_counts['对话消息'] = result.rowcount
            logger.info(f"✅ 删除对话消息: {result.rowcount} 条")
            
            # 7. 删除对话运行记录
            result = await db.execute(delete(ConversationRunDB))
            deleted_counts['对话运行'] = result.rowcount
            logger.info(f"✅ 删除对话运行记录: {result.rowcount} 条")
            
            # 8. 删除对话
            result = await db.execute(delete(ConversationDB))
            deleted_counts['对话'] = result.rowcount
            logger.info(f"✅ 删除对话数据: {result.rowcount} 条")
            
            # 提交所有删除操作
            await db.commit()
            
            logger.info("✅ 所有数据删除完成")
            logger.info("删除统计:")
            for name, count in deleted_counts.items():
                logger.info(f"  - {name}: {count} 条")
                
    except Exception as e:
        logger.error(f"❌ 删除数据时出错: {str(e)}", exc_info=True)
        raise
    finally:
        await async_engine.dispose()


async def main():
    """主函数"""
    # 确认操作
    print("⚠️  警告: 此操作将删除数据库中的所有业务数据!")
    print("⚠️  此操作不可逆，请确认!")
    confirm = input("输入 'YES' 继续: ")
    
    if confirm != "YES":
        print("❌ 操作已取消")
        return
    
    await delete_all_data()
    print("✨ 操作完成")


if __name__ == "__main__":
    asyncio.run(main())
