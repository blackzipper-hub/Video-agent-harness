#!/usr/bin/env python3
"""
数据库初始化脚本
包含：
1. 创建数据库表
2. 创建admin用户
3. 初始化积分配置
4. 初始化预设角色数据
5. 初始化预设风格数据
"""

import os
import sys
import logging
from typing import Optional
import getpass

# 添加项目路径到sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.models.database import create_tables, get_db
from app.models.user import User, UserCredit, CreditHistory, CreditOperationType
from app.services.credit_service import CreditService
from app.crud.character import create_character, get_characters_list
from app.models.storybook_style import StorybookStyleDB
from app.services.user_service import pwd_context
from sqlmodel import Session, select
from datetime import datetime

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def create_admin_user(db: Session, username: str = "admin", password: str = None, email: str = "admin@example.com") -> Optional[User]:
    """创建管理员用户"""
    try:
        # 检查是否已存在管理员用户
        existing_admin = db.exec(select(User).where(User.username == username)).first()
        if existing_admin:
            logger.info(f"管理员用户 {username} 已存在，跳过创建")
            return existing_admin
        
        # 如果没有提供密码，提示输入
        if not password:
            password = getpass.getpass("请输入管理员密码: ")
        
        # 使用bcrypt哈希密码（与应用程序一致）
        password_hash = pwd_context.hash(password)
        
        # 创建管理员用户
        admin_user = User(
            user_id="admin",  # 必需字段
            username=username,
            email=email,
            password_hash=password_hash,
            auth_type="invite_code",
            is_admin=True,  # 修复：设置为True
            status="active",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)
        
        logger.info(f"✅ 成功创建管理员用户: {username} (ID: {admin_user.id})")
        
        # 创建用户积分账户
        create_user_credit_account(db, admin_user.user_id, initial_credits=1000)
        
        return admin_user
        
    except Exception as e:
        logger.error(f"❌ 创建管理员用户失败: {str(e)}")
        db.rollback()
        return None

def create_user_credit_account(db: Session, user_id: str, initial_credits: int = 0) -> bool:
    """创建用户积分账户"""
    try:
        # 检查是否已存在积分账户
        existing_credit = db.exec(select(UserCredit).where(UserCredit.user_id == user_id)).first()
        if existing_credit:
            logger.info(f"ℹ️  用户 {user_id} 的积分账户已存在")
            return True
        
        # 创建积分账户
        user_credit = UserCredit(
            user_id=user_id,
            balance=initial_credits,
            subscription_balance=0,
            purchased_balance=initial_credits,
            total_earned=initial_credits,
            total_used=0,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(user_credit)
        
        # 如果有初始积分，创建历史记录
        if initial_credits > 0:
            credit_history = CreditHistory(
                user_id=user_id,
                amount=initial_credits,
                balance=initial_credits,
                operation_type=CreditOperationType.INIT,
                description=f"初始化账户，赠送 {initial_credits} 积分",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(credit_history)
        
        db.commit()
        logger.info(f"✅ 成功创建用户 {user_id} 的积分账户，初始积分: {initial_credits}")
        return True
        
    except Exception as e:
        logger.error(f"❌ 创建用户 {user_id} 积分账户失败: {str(e)}")
        db.rollback()
        return False

def init_preset_characters(db: Session) -> bool:
    """初始化预设角色数据"""
    try:
        # 预设角色列表
        PRESET_CHARACTERS = [
            {
                "name": "小熊布朗",
                "description": "一只友好可爱的棕色小熊，总是面带笑容，喜欢帮助朋友们解决问题。他有着软绵绵的毛发和温暖的拥抱。",
                "photo_url": "api/photos/f12afd83-60ad-405c-91be-bbb6a79cb3d8.webp",
                "i18n_data": {
                    "en": {
                        "name": "Brown Bear",
                        "description": "A friendly and adorable brown bear who always wears a smile and loves helping friends solve problems. He has soft fur and gives warm hugs."
                    }
                }
            },
            {
                "name": "兔子露西",
                "description": "一只聪明活泼的白色小兔子，有着长长的耳朵和蓬松的尾巴，喜欢蹦蹦跳跳，充满好奇心。",
                "photo_url": "api/photos/cbcf9025-fc13-41cc-b161-5746c8e9cd5a.webp",
                "i18n_data": {
                    "en": {
                        "name": "Lucy Rabbit",
                        "description": "A smart and lively white bunny with long ears and a fluffy tail. She loves hopping around and is full of curiosity."
                    }
                }
            },
            {
                "name": "小狐狸瑞克",
                "description": "一只机智勇敢的橙色小狐狸，有着尖尖的耳朵和蓬松的大尾巴，眼睛很明亮，总是想出好主意。",
                "photo_url": "api/photos/b3ad9b83-df11-4c75-b6bf-fae44cd7a0fe.webp",
                "i18n_data": {
                    "en": {
                        "name": "Ricky Fox",
                        "description": "A clever and brave orange fox with pointy ears and a fluffy tail. His bright eyes always come up with good ideas."
                    }
                }
            },
            {
                "name": "小猫咪莉莉",
                "description": "一只优雅好奇的灰色小猫，有着绿色的眼睛和粉色的鼻子，总是用爪子探索世界，喜欢晒太阳。",
                "photo_url": "api/photos/668bb814-9058-445c-b575-a443a4e7e40e.webp",
                "i18n_data": {
                    "en": {
                        "name": "Lily Cat",
                        "description": "An elegant and curious gray kitten with green eyes and a pink nose. She always explores the world with her paws and loves basking in the sun."
                    }
                }
            },
            {
                "name": "小狗波比",
                "description": "一只忠诚活泼的金黄色小狗，有着摆动的尾巴和湿润的鼻子，总是精力充沛，喜欢和朋友们玩耍。",
                "photo_url": "api/photos/22b8a4f0-c1df-495e-bede-0ad33c606501.webp",
                "i18n_data": {
                    "en": {
                        "name": "Poppy Dog",
                        "description": "A loyal and energetic golden puppy with a wagging tail and a wet nose. He's always full of energy and loves playing with friends."
                    }
                }
            },
            {
                "name": "小鸟青青",
                "description": "一只自由快乐的蓝色小鸟，有着美丽的羽毛和清脆的歌声，喜欢在天空中自由飞翔，传递快乐。",
                "photo_url": "api/photos/761c632c-31f8-4a0b-a697-5f6adb27ad89.webp",
                "i18n_data": {
                    "en": {
                        "name": "Blue Bird",
                        "description": "A free and happy blue bird with beautiful feathers and a clear singing voice. She loves flying freely in the sky and spreading joy."
                    }
                }
            },
            {
                "name": "小象艾米",
                "description": "一只温柔智慧的粉色小象，有着长长的鼻子和大大的耳朵，记忆力很好，总是记得朋友们的生日。",
                "photo_url": "api/photos/88b13e19-fa26-4073-ac56-52edfc20d7fe.webp",
                "i18n_data": {
                    "en": {
                        "name": "Amy Elephant",
                        "description": "A gentle and wise pink elephant with a long trunk and big ears. She has a great memory and always remembers her friends' birthdays."
                    }
                }
            },
            {
                "name": "小猴子皮皮",
                "description": "一只调皮聪明的棕色小猴子，有着灵活的尾巴和顽皮的眼神，喜欢爬树和玩游戏，总是充满活力。",
                "photo_url": "api/photos/3b798b19-b9d4-4a75-93df-b9aa66908e36.webp",
                "i18n_data": {
                    "en": {
                        "name": "Peppy Monkey",
                        "description": "A mischievous and clever brown monkey with a flexible tail and playful eyes. He loves climbing trees and playing games, always full of energy."
                    }
                }
            },
            {
                "name": "小松鼠坚果",
                "description": "一只勤劳可爱的红棕色小松鼠，有着蓬松的大尾巴和圆圆的眼睛，喜欢收集坚果，总是很忙碌。",
                "photo_url": "api/photos/e7c1b5d6-da23-4069-b498-b496fa1ee4d8.webp",
                "i18n_data": {
                    "en": {
                        "name": "Nutty Squirrel",
                        "description": "A hardworking and cute reddish-brown squirrel with a fluffy tail and round eyes. She loves collecting nuts and is always busy."
                    }
                }
            },
            {
                "name": "小企鹅波波",
                "description": "一只憨厚可爱的黑白色小企鹅，走路摇摇摆摆，有着圆滚滚的肚子，虽然不会飞但很会游泳。",
                "photo_url": "api/photos/bf49d515-5e7a-4fb4-bcee-37d51490c02e.webp",
                "i18n_data": {
                    "en": {
                        "name": "Bobo Penguin",
                        "description": "A cute black and white penguin who waddles when walking, with a round belly. Although he can't fly, he's a great swimmer."
                    }
                }
            }
        ]
        
        # 检查是否已经有预设角色
        existing_characters, total = get_characters_list(db, user_id="admin", char_type="preset")
        
        if existing_characters:
            logger.info(f"发现 {total} 个已存在的预设角色，跳过初始化")
            return True
        
        logger.info(f"开始初始化 {len(PRESET_CHARACTERS)} 个预设角色...")
        success_count = 0
        
        for i, char_data in enumerate(PRESET_CHARACTERS, 1):
            try:
                character = create_character(
                    db=db,
                    user_id="system",
                    name=char_data['name'],
                    photo_url=char_data['photo_url'],
                    description=char_data['description'],
                    response_id="",
                    generation_id="",
                    char_type="preset",
                    i18n_data=char_data.get('i18n_data', {})
                )
                logger.info(f"[{i}/{len(PRESET_CHARACTERS)}] ✅ 创建角色: {character.name}")
                success_count += 1
                
            except Exception as e:
                logger.error(f"[{i}/{len(PRESET_CHARACTERS)}] ❌ 创建角色失败: {char_data['name']} - {str(e)}")
        
        logger.info(f"✅ 预设角色初始化完成: {success_count}/{len(PRESET_CHARACTERS)}")
        return success_count > 0
        
    except Exception as e:
        logger.error(f"❌ 预设角色初始化失败: {str(e)}")
        return False

def init_preset_styles(db: Session) -> bool:
    """初始化预设风格数据"""
    try:
        # 预设风格数据
        preset_styles = [
            {
                "user_id": "system",
                "title": "童话风格",
                "description": "充满魔法和想象力的童话世界，色彩鲜艳，充满梦幻感。适合3-8岁儿童阅读。",
                "image_url": "api/photos/599a5e05-6fb3-49e5-b74a-29d74e27a35d.webp",
                "type": "preset",
                "i18n_data": {
                    "en": {
                        "title": "Fairy Tale Style",
                        "description": "A magical and imaginative fairy tale world with vibrant colors and dreamy atmosphere. Suitable for children aged 3-8."
                    }
                }
            },
            {
                "user_id": "system",
                "title": "水彩画风格",
                "description": "柔和的水彩画效果，色彩自然流动，给人温馨舒适的感觉。",
                "image_url": "api/photos/fa0d286d-be23-4f45-8966-99a3e69b2231.webp",
                "type": "preset",
                "i18n_data": {
                    "en": {
                        "title": "Watercolor Style",
                        "description": "Soft watercolor effects with naturally flowing colors, creating a warm and comfortable feeling."
                    }
                }
            },
            {
                "user_id": "system",
                "title": "卡通风格",
                "description": "简洁有趣的卡通形象，线条清晰，色彩明快，充满活力。",
                "image_url": "api/photos/a52d365b-03df-41cf-8ace-c79bedea54aa.webp",
                "type": "preset",
                "i18n_data": {
                    "en": {
                        "title": "Cartoon Style",
                        "description": "Simple and fun cartoon characters with clear lines, bright colors, and full of vitality."
                    }
                }
            },
            {
                "user_id": "system",
                "title": "剪纸风格",
                "description": "模拟剪纸艺术效果，层次分明，具有民间艺术特色。",
                "image_url": "api/photos/6b94c2a1-12be-4197-ba80-51d6f2352507.webp",
                "type": "preset",
                "i18n_data": {
                    "en": {
                        "title": "Paper-cut Style",
                        "description": "Simulating paper-cutting art effects with distinct layers and folk art characteristics."
                    }
                }
            },
            {
                "user_id": "system",
                "title": "写实风格",
                "description": "接近真实世界的写实绘画，细节丰富，比例准确。",
                "image_url": "api/photos/814e5d36-cc57-4503-b13c-6d9de8e5c5b8.webp",
                "type": "preset",
                "i18n_data": {
                    "en": {
                        "title": "Realistic Style",
                        "description": "Realistic paintings close to the real world, rich in details and accurate proportions."
                    }
                }
            },
            {
                "user_id": "system",
                "title": "粘土风格",
                "description": "如同用彩色粘土捏制的效果，立体感强，色彩丰富。",
                "image_url": "api/photos/c16ced34-11a2-4c89-ae15-7fe43a94c667.webp",
                "type": "preset",
                "i18n_data": {
                    "en": {
                        "title": "Clay Style",
                        "description": "Effects like sculpted with colored clay, strong three-dimensional sense and rich colors."
                    }
                }
            },
            {
                "user_id": "system",
                "title": "油画风格",
                "description": "经典油画质感，色彩浓郁，具有艺术感和历史感。",
                "image_url": "api/photos/76d41677-d26c-4bc9-a8eb-a49db62376ba.webp",
                "type": "preset",
                "i18n_data": {
                    "en": {
                        "title": "Oil Painting Style",
                        "description": "Classic oil painting texture with rich colors, artistic and historical feel."
                    }
                }
            },
            {
                "user_id": "system",
                "title": "素描风格",
                "description": "简洁的黑白素描效果，线条清晰，适合表现情感。",
                "image_url": "api/photos/ce38563f-cdf4-48af-90a9-c91986ea34e7.webp",
                "type": "preset",
                "i18n_data": {
                    "en": {
                        "title": "Sketch Style",
                        "description": "Simple black and white sketch effects with clear lines, suitable for expressing emotions."
                    }
                }
            },
            {
                "user_id": "system",
                "title": "动漫风格",
                "description": "日式动漫风格，大眼睛角色，色彩鲜明，充满青春活力。",
                "image_url": "api/photos/ffded2d0-7e26-4042-953b-ca3741a786bb.webp",
                "type": "preset",
                "i18n_data": {
                    "en": {
                        "title": "Anime Style",
                        "description": "Japanese anime style with big-eyed characters, bright colors, and full of youthful energy."
                    }
                }
            },
            {
                "user_id": "system",
                "title": "复古风格",
                "description": "怀旧复古的插画风格，温暖色调，具有历史韵味。",
                "image_url": "api/photos/93aead14-430c-45d1-843e-e27677f45a99.webp",
                "type": "preset",
                "i18n_data": {
                    "en": {
                        "title": "Vintage Style",
                        "description": "Nostalgic retro illustration style with warm tones and historical charm."
                    }
                }
            }
        ]
        
        # 检查是否已有预设数据
        existing_count = len(db.exec(select(StorybookStyleDB).where(StorybookStyleDB.type == "preset")).all())
        if existing_count > 0:
            logger.info(f"数据库中已有 {existing_count} 条预设风格数据，跳过初始化")
            return True
        
        # 插入预设风格数据
        logger.info(f"开始初始化 {len(preset_styles)} 条预设风格数据...")
        
        for i, style_data in enumerate(preset_styles, 1):
            # 处理i18n_data字段 - 需要转换为JSON字符串
            style_data_copy = style_data.copy()
            if 'i18n_data' in style_data_copy:
                import json
                style_data_copy['i18n_data'] = json.dumps(style_data_copy['i18n_data'])
            
            style = StorybookStyleDB(**style_data_copy)
            db.add(style)
            logger.info(f"[{i}/{len(preset_styles)}] ✅ 添加风格: {style_data['title']}")
        
        db.commit()
        logger.info(f"✅ 预设风格初始化完成: {len(preset_styles)} 条")
        return True
        
    except Exception as e:
        logger.error(f"❌ 预设风格初始化失败: {str(e)}")
        db.rollback()
        return False

def main(environment: str = "production"):
    """主函数"""
    print("=" * 60)
    print("🚀 CartoonBook 数据库初始化工具")
    print(f"📝 环境: {environment}")
    print("=" * 60)
    
    # 设置环境变量
    os.environ["ENVIRONMENT"] = environment
    
    try:
        # 1. 创建数据库表
        logger.info("1️⃣ 创建数据库表...")
        create_tables()
        logger.info("✅ 数据库表创建完成")
        
        # 获取数据库会话
        db_gen = get_db()
        db = next(db_gen)
        
        try:
            # 2. 创建管理员用户
            logger.info("2️⃣ 创建管理员用户...")
            admin_user = create_admin_user(db)
            
            # 3. 初始化积分配置
            logger.info("3️⃣ 初始化积分配置...")
            try:
                CreditService.initialize_default_config()
                logger.info("✅ 积分配置初始化完成")
            except Exception as e:
                logger.error(f"❌ 积分配置初始化失败: {str(e)}")
            
            # 4. 初始化预设角色
            logger.info("4️⃣ 初始化预设角色...")
            init_preset_characters(db)
            
            # 5. 初始化预设风格
            logger.info("5️⃣ 初始化预设风格...")
            init_preset_styles(db)
            
            print("\n" + "=" * 60)
            print("🎉 数据库初始化完成！")
            print("=" * 60)
            
            if admin_user:
                print(f"管理员账号: {admin_user.username}")
                print("请妥善保管管理员密码")
            
            print("\n可以开始启动应用了！")
            
        except Exception as e:
            logger.error(f"初始化过程中出错: {str(e)}")
            return False
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"数据库连接失败: {str(e)}")
        print("\n请确保:")
        print("1. PostgreSQL 服务已启动")
        print("2. 数据库连接配置正确")
        print("3. 环境变量 DATABASE_URL 已设置")
        return False
    
    return True

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="CartoonBook 数据库初始化工具")
    parser.add_argument(
        "--environment", 
        choices=["development", "production"], 
        default="production",
        help="环境类型 (development: 使用dev数据库, production: 使用生产数据库)"
    )
    
    args = parser.parse_args()
    success = main(environment=args.environment)
    sys.exit(0 if success else 1)
