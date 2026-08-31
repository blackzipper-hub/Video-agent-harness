#!/usr/bin/env python3
"""
删除数据库中的所有用户数据
包括：用户、角色、风格、积分等
"""

import os
import sys
import logging
from datetime import datetime

# 添加项目路径到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, delete
from app.models.database import engine
from app.models.user import User, UserCredit, CreditHistory, InviteCode
from app.models.character import CharacterDB
from app.models.storybook_style import StorybookStyleDB

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def delete_all_data():
    """删除所有业务数据"""
    try:
        # 使用上下文管理器确保连接正确关闭
        with Session(engine) as db:
            logger.info("🗑️  开始删除数据库数据...")
            
            deleted_counts = {}
            
            # 1. 删除角色数据
            result = db.exec(delete(CharacterDB))
            deleted_counts['角色'] = result.rowcount
            logger.info(f"✅ 删除角色数据: {result.rowcount} 条")
            
            # 2. 删除风格数据
            result = db.exec(delete(StorybookStyleDB))
            deleted_counts['风格'] = result.rowcount
            logger.info(f"✅ 删除风格数据: {result.rowcount} 条")
            
            # 3. 删除积分历史
            result = db.exec(delete(CreditHistory))
            deleted_counts['积分历史'] = result.rowcount
            logger.info(f"✅ 删除积分历史: {result.rowcount} 条")
            
            # 4. 删除用户积分
            result = db.exec(delete(UserCredit))
            deleted_counts['用户积分'] = result.rowcount
            logger.info(f"✅ 删除用户积分: {result.rowcount} 条")
            
            # 5. 删除邀请码
            result = db.exec(delete(InviteCode))
            deleted_counts['邀请码'] = result.rowcount
            logger.info(f"✅ 删除邀请码: {result.rowcount} 条")
            
            # 6. 删除用户数据
            result = db.exec(delete(User))
            deleted_counts['用户'] = result.rowcount
            logger.info(f"✅ 删除用户数据: {result.rowcount} 条")
            
            # 提交更改
            db.commit()
            
            # 显示统计
            logger.info("=" * 60)
            logger.info("📊 删除统计:")
            total_deleted = 0
            for data_type, count in deleted_counts.items():
                logger.info(f"  {data_type}: {count} 条")
                total_deleted += count
            
            logger.info(f"  总计删除: {total_deleted} 条记录")
            logger.info("=" * 60)
            logger.info("🎉 数据库清理完成！")
            
            return True
            
    except Exception as e:
        logger.error(f"❌ 删除数据失败: {str(e)}")
        return False

def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("🗑️  数据库数据清理工具")
    logger.info("=" * 60)
    logger.info("⚠️  警告：此操作将删除所有用户数据！")
    logger.info("包括：用户、角色、风格、积分、邀请码等")
    logger.info("=" * 60)
    
    # 确认操作
    response = input("确认要删除所有数据吗？输入 'YES' 继续: ").strip()
    if response != 'YES':
        logger.info("❌ 操作已取消")
        return
    
    # 执行删除
    success = delete_all_data()
    
    if success:
        logger.info("✅ 数据清理成功！现在可以运行 init_database.py 重新初始化数据")
    else:
        logger.error("❌ 数据清理失败！请检查错误信息")
        sys.exit(1)

if __name__ == "__main__":
    main()
