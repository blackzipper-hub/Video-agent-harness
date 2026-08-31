"""
Unit tests for agent_router_service - 完整视频生成流程测试
"""
import pytest
import json
import logging

from app.services.agent.agent_router_service import get_agent_router_service
from app.models.video_state import UserInput
from app.models.user_options import UserOption
from app.utils.thread_id_utils import generate_new_thread_id
from app.models.database import engine, Session, get_async_db
from app.crud.conversation import create_conversation, get_conversation_by_thread_id

logger = logging.getLogger(__name__)


class TestAgentRouterService:
    """Test agent router service - 完整视频生成流程"""
    
    @pytest.mark.asyncio
    async def test_video_generation_monkey_to_girl(self):
        """测试完整的视频生成流程 - 猴子变美少女"""
        
        # 1. 准备测试数据
        user_id = "test_user_monkey_to_girl"
        user_input_text = "做视频。一个猴子角色拿着金箍棒，然后变成了一个美少女"
        
        # 2. 创建 UserOption
        user_option = UserOption(
            aspect_ratio="16:9",
            resolution="480p",
            duration=30,
            video_generation_tool="auto",
            enable_lipsync=False,
            enable_continuity_mode=False,
            image_generation_tool="nano_banana",
            nano_banana_model="gemini-2.5-flash-image"
        )
        
        # 3. 创建 UserInput
        user_input_data = UserInput(
            user_input=user_input_text,
            user_option=user_option,
            images=[],
            audio_files=[],
            video_files=[],
            agent_type=None  # 让系统自动识别
        )
        
        # 4. 生成或获取 thread_id 和 conversation_id
        thread_id = generate_new_thread_id(user_id)
        
        # 5. 创建或获取会话
        with Session(engine) as db:
            existing_conversation = get_conversation_by_thread_id(db=db, thread_id=thread_id)
            if existing_conversation:
                conversation_id = existing_conversation.id
                logger.info(f"使用现有会话: {conversation_id}")
            else:
                new_conversation = create_conversation(
                    db=db,
                    user_id=user_id,
                    thread_id=thread_id,
                    title=user_input_text[:50] + "..." if len(user_input_text) > 50 else user_input_text
                )
                conversation_id = new_conversation.id
                logger.info(f"创建新会话: {conversation_id}")
        
        # 6. 获取 agent router service
        agent_router_service = get_agent_router_service()
        
        logger.info("=" * 80)
        logger.info("开始测试 Agent Router Service")
        logger.info(f"User ID: {user_id}")
        logger.info(f"Thread ID: {thread_id}")
        logger.info(f"Conversation ID: {conversation_id}")
        logger.info(f"User Input: {user_input_text}")
        logger.info("=" * 80)
        
        # 7. 获取异步数据库会话并调用 astream 方法
        event_count = 0
        event_types = set()
        final_run_id = None
        has_interrupt = False
        has_error = False
        
        # 获取异步数据库会话
        async_db_gen = get_async_db()
        async_db = await anext(async_db_gen)
        
        try:
            async for event_data in agent_router_service.astream(
                user_input_data=user_input_data,
                user_id=user_id,
                conversation_id=conversation_id,
                thread_id=thread_id,
                async_db=async_db
            ):
                event_count += 1
                
                # event_data 格式是 "data: {json}\n\n"
                if event_data.startswith("data: "):
                    json_str = event_data[6:].strip()
                    try:
                        event = json.loads(json_str)
                        event_type = event.get("type", "unknown")
                        event_types.add(event_type)
                        
                        # 记录 run_id
                        if "run_id" in event and not final_run_id:
                            final_run_id = event["run_id"]
                        
                        # 根据事件类型打印不同的信息
                        if event_type == "stream_end":
                            logger.info("\n" + "=" * 80)
                            logger.info("✅ 流式处理结束")
                            logger.info("=" * 80)
                        elif event_type == "error":
                            has_error = True
                            logger.error("\n" + "=" * 80)
                            logger.error(f"❌ 错误: {event.get('message')}")
                            logger.error("=" * 80)
                        elif event_type == "interrupt":
                            has_interrupt = True
                            logger.info("\n" + "=" * 80)
                            logger.info("⏸️  流程中断 - 需要用户交互")
                            logger.info(f"中断数据: {json.dumps(event.get('interrupt_data'), indent=2, ensure_ascii=False)}")
                            logger.info("=" * 80)
                        elif event_type == "cancelled":
                            logger.info("\n" + "=" * 80)
                            logger.info("🚫 任务已取消")
                            logger.info("=" * 80)
                        else:
                            # 其他自定义事件
                            logger.info(f"\n[事件 #{event_count}] {event_type}")
                            
                            # 打印关键信息
                            if "message" in event:
                                logger.info(f"  消息: {event['message']}")
                            if "node" in event:
                                logger.info(f"  节点: {event['node']}")
                            if "step" in event:
                                logger.info(f"  步骤: {event['step']}")
                            
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON解析失败: {e}")
                        logger.error(f"原始数据: {json_str}")
        finally:
            # 关闭异步数据库会话
            try:
                await async_db_gen.aclose()
            except Exception as e:
                logger.warning(f"关闭数据库会话失败: {e}")
        
        logger.info(f"\n共收到 {event_count} 个事件")
        logger.info(f"事件类型: {event_types}")
        if final_run_id:
            logger.info(f"Run ID: {final_run_id}")
        
        # 验证结果
        assert event_count > 0, "应该至少收到一个事件"
        assert "stream_end" in event_types, "应该收到流式结束事件"
        
        # 如果有错误，测试应该失败
        if has_error:
            pytest.fail("流程中出现错误")
        
        # 如果有中断，记录但不算测试失败（因为可能是正常的用户交互流程）
        if has_interrupt:
            logger.warning("⚠️  流程中有中断，可能需要用户交互")
        
        logger.info("\n✅ 测试通过 - Agent Router Service 完整流程")

