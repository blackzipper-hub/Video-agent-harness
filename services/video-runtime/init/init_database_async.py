"""
初始化数据库 - 异步版本
创建管理员用户和初始数据
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

# 根据命令行参数加载对应的环境配置
environment = sys.argv[1] if len(sys.argv) > 1 else "development"
if environment == "local":
    load_dotenv(".env.local")
elif environment == "development":
    load_dotenv(".env.development")
elif environment == "production":
    load_dotenv(".env.production")
else:
    load_dotenv(".env.development")

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from app.models.database import async_engine, AsyncSessionLocal
from app.models.user import User, UserCredit, InviteCode, UserStatus, AuthType
from app.models.subscription import SubscriptionTier, SubscriptionPackage, CreditAllocation
from datetime import datetime, timedelta
import uuid

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def create_tables():
    """创建所有数据库表"""
    from sqlmodel import SQLModel
    from app.models.conversation import ConversationDB, ConversationMessageDB
    from app.models.user import User
    
    async with async_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info("✅ 数据库表创建完成")


async def create_admin_user(db: AsyncSession) -> User:
    """创建管理员用户"""
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    
    # 检查管理员是否已存在
    result = await db.execute(select(User).where(User.username == admin_username))
    existing_admin = result.scalar_one_or_none()
    
    if existing_admin:
        logger.info(f"✅ 管理员用户已存在: {admin_username}")
        return existing_admin
    
    # 创建管理员用户
    from app.services.user_service import pwd_context
    
    admin_user = User(
        user_id=str(uuid.uuid4()),
        username=admin_username,
        email=admin_email,
        password_hash=pwd_context.hash(admin_password),
        is_admin=True,
        status=UserStatus.ACTIVE,
        auth_type=AuthType.EMAIL,
        created_at=datetime.utcnow()
    )
    
    db.add(admin_user)
    await db.commit()
    await db.refresh(admin_user)
    
    logger.info(f"✅ 管理员用户创建成功: {admin_username}")
    return admin_user


async def init_credit_config(db: AsyncSession):
    """初始化积分配置"""
    # 检查是否已有配置
    result = await db.execute(select(CreditAllocation))
    existing_config = result.scalars().first()
    
    if existing_config:
        logger.info("✅ 积分配置已存在")
        return
    
    # 创建默认积分配置
    default_allocations = [
        CreditAllocation(
            tier=SubscriptionTier.FREE,
            monthly_credits=100,
            extra_credits=0,
            credit_reset_day=1,
            created_at=datetime.utcnow()
        ),
        CreditAllocation(
            tier=SubscriptionTier.BASIC,
            monthly_credits=500,
            extra_credits=0,
            credit_reset_day=1,
            created_at=datetime.utcnow()
        ),
        CreditAllocation(
            tier=SubscriptionTier.PRO,
            monthly_credits=2000,
            extra_credits=0,
            credit_reset_day=1,
            created_at=datetime.utcnow()
        )
    ]
    
    for allocation in default_allocations:
        db.add(allocation)
    
    await db.commit()
    logger.info("✅ 积分配置初始化完成")


async def create_default_invite_code(db: AsyncSession, admin_user: User):
    """创建默认邀请码"""
    default_code = "WELCOME2024"
    
    # 检查邀请码是否已存在
    result = await db.execute(select(InviteCode).where(InviteCode.code == default_code))
    existing_code = result.scalar_one_or_none()
    
    if existing_code:
        logger.info(f"✅ 默认邀请码已存在: {default_code}")
        return existing_code
    
    # 创建邀请码
    invite_code = InviteCode(
        code=default_code,
        created_by=admin_user.user_id,
        max_uses=1000,
        used_count=0,
        expires_at=datetime.utcnow() + timedelta(days=365),
        created_at=datetime.utcnow()
    )
    
    db.add(invite_code)
    await db.commit()
    await db.refresh(invite_code)
    
    logger.info(f"✅ 默认邀请码创建成功: {default_code}")
    return invite_code


async def main():
    """主函数"""
    logger.info(f"🚀 开始初始化数据库 (环境: {environment})")
    
    # 设置环境变量
    os.environ["ENVIRONMENT"] = environment
    
    try:
        # 1. 创建数据库表
        logger.info("1️⃣ 创建数据库表...")
        await create_tables()
        
        # 2. 使用异步会话
        async with AsyncSessionLocal() as db:
            try:
                # 3. 创建管理员用户
                logger.info("2️⃣ 创建管理员用户...")
                admin_user = await create_admin_user(db)
                
                # 4. 初始化积分配置
                logger.info("3️⃣ 初始化积分配置...")
                await init_credit_config(db)
                
                # 5. 创建默认邀请码
                logger.info("4️⃣ 创建默认邀请码...")
                await create_default_invite_code(db, admin_user)
                
                logger.info("✨ 数据库初始化完成！")
                logger.info(f"管理员用户名: {admin_user.username}")
                logger.info(f"管理员邮箱: {admin_user.email}")
                
            except Exception as e:
                await db.rollback()
                logger.error(f"❌ 初始化过程中出错: {str(e)}", exc_info=True)
                raise
                
    except Exception as e:
        logger.error(f"❌ 数据库初始化失败: {str(e)}", exc_info=True)
        sys.exit(1)
    finally:
        # 关闭引擎
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
