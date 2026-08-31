"""
任务Worker - 从SQS队列消费任务并执行

架构说明：
- 任务队列：使用AWS SQS（替代Redis Stream）
- 状态管理：使用Redis（任务状态、消息流）
- 并发控制：使用Redis信号量（RateLimiter）
- 取消机制：使用Redis Pub/Sub（跨机器/跨进程协调）
- 计费扫描：由 main 启动时拉起的 billing_worker 负责（同进程）；cancel/extend 依赖当前进程的 running_tasks 与 receipt_handle，与 task worker 捆绑。
"""
import logging
import asyncio
import os
import json
from typing import Optional, Dict, Any
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from ...services.redis.connection import get_redis_client, get_redis_stream_service
from ...services.aws.sqs_service import SQSTaskService
from ...services.queue import create_task_queue
from ...services.agent.agent_router_service import get_agent_router_service, AgentType
from ...services.agent.base_agent import MessageType
from ...crud.conversation import (
    async_update_conversation_run_status,
    async_get_conversation_run_by_run_id,
    async_update_conversation_run_billing,
    async_set_conversation_run_billing_pending_if_not_completed,
    async_get_task_status_cached,
)
from ...crud.error_tracking import update_task_record
from ...config import get_settings
from ...models.task_status import TaskStatus, BillingStatus, LangsmithStatus
from ...services.task_enqueue_service import prepare_resume_task
from ...utils.credit_deduction_utils import check_credits_before_task
from ...services.interrupt_auto_resume import schedule_interrupt_auto_resume
from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# 管线单飞锁参数：同一 thread 同一时刻只允许一个 run 执行视频管线
PIPELINE_LOCK_TTL = 180            # 锁 TTL（秒）；执行期间定期续期，崩溃后到期自动释放
PIPELINE_LOCK_REFRESH_INTERVAL = 60  # 续期间隔（秒），需 < TTL
PIPELINE_LOCK_RETRY_DELAY = 30     # 抢锁失败时重新入队的延迟（秒）
PIPELINE_LOCK_MAX_RETRIES = 80     # 抢锁重试上限（约 40 分钟），超过则放弃，避免无限堆积


class TaskWorker:
    """
    任务Worker - 从SQS队列消费任务并执行
    
    工作流程：
    1. 初始化：连接Redis（状态管理）、SQS（任务队列）、初始化限流器
    2. 启动：开始主循环，从SQS消费任务
    3. 处理任务：执行Agent，更新状态，删除SQS消息
    4. 停止：优雅关闭，等待任务完成
    
    多Worker支持：
    - SQS确保每个消息只会被一个worker接收（visibility timeout期间）
    - RateLimiter使用Redis信号量控制全局并发数（所有worker共享）
    - Redis Pub/Sub用于跨worker取消任务
    """
    
    def __init__(self, max_concurrent: int = 1000):
        """
        初始化TaskWorker
        
        Args:
            max_concurrent: 最大并发任务数（所有worker共享，通过RateLimiter控制）
        """
        self.redis_service = None  # Redis服务：用于状态管理、消息流、Pub/Sub
        self.sqs_service = None  # SQS服务：用于任务队列（消费和删除消息）
        self.agent_router_service = get_agent_router_service()  # Agent路由服务
        self.consumer_name = f"worker-{os.getpid()}-{os.getppid()}"  # 用于日志标识
        self.max_concurrent = max_concurrent  # 最大并发数
        self.rate_limiter = None  # 限流器：控制全局并发数
        self.running = False  # Worker运行状态标志
        # 跟踪正在运行的任务：run_id -> asyncio.Task（用于快速取消）
        self.running_tasks: Dict[str, asyncio.Task] = {}
        # Pub/Sub 订阅任务和对象（用于接收跨机器cancel消息）
        self.pubsub_task: Optional[asyncio.Task] = None
        self.pubsub: Optional[Any] = None  # Redis PubSub 对象，用于主动关闭
    
    async def initialize(self):
        """
        初始化Worker
        
        功能：
        - 连接Redis（用于状态管理、消息流、Pub/Sub）
        - 初始化SQS服务（用于任务队列）
        - 初始化RateLimiter（用于并发控制）
        
        注意：
        - Redis和SQS都是按环境隔离的（通过环境变量配置）
        - 多个worker共享同一个SQS队列和Redis信号量
        """
        # 获取Redis服务（使用str模式，避免bytes转换）
        redis_client = await get_redis_client(decode_responses=True)
        self.redis_service = await get_redis_stream_service()
        
        # 初始化任务队列（QUEUE_BACKEND: sqs 默认 / redis 自托管）
        self.sqs_service = create_task_queue()
        
        # 从配置获取环境，传递给RateLimiter
        from ...config import get_settings
        settings = get_settings()
        environment = settings.ENVIRONMENT.value
        
        # 初始化限流器（使用Redis信号量，所有worker共享，str模式）
        self.rate_limiter = RateLimiter(redis_client, self.max_concurrent, environment=environment)
        
        logger.info(f"✅ Worker初始化成功: {self.consumer_name} (使用SQS任务队列)")
    
    async def start(self):
        """
        启动Worker主循环
        
        工作流程：
        1. 初始化（如果未初始化）
        2. 启动Pub/Sub监听（用于接收取消消息）
        3. 主循环：
           a. 尝试获取执行权限（RateLimiter.acquire）
           b. 如果获取成功，从SQS消费任务（长轮询20秒）
           c. 如果有任务，异步处理（不阻塞主循环）
           d. 如果没有任务，释放权限并短暂休眠
           e. 如果无法获取权限（达到并发限制），等待1秒后重试
        
        多Worker行为：
        - 多个worker同时运行，共享同一个SQS队列
        - SQS确保每个消息只会被一个worker接收
        - RateLimiter确保全局并发数不超过max_concurrent
        - 每个worker独立处理任务，互不干扰
        """
        if not self.redis_service:
            await self.initialize()
        
        self.running = True
        logger.info(f"🚀 Worker启动: {self.consumer_name}, 最大并发: {self.max_concurrent}")
        
        # 启动Pub/Sub订阅任务（用于接收跨机器cancel消息）
        redis_client = await get_redis_client(decode_responses=True)
        self.pubsub_task = asyncio.create_task(self._listen_cancel_messages(redis_client))
        
        import time
        loop_iteration = 0
        while self.running:
            # 本轮是否在本地持有锁（acquire 了且未交给 process_task）
            acquired_and_not_passed = False
            try:
                loop_iteration += 1
                # ✅ 先检查是否可以获取锁（限流检查）
                # 如果能获取锁，再去消费任务；如果不能，等待一段时间再检查
                if await self.rate_limiter.acquire():
                    acquired_and_not_passed = True
                    # 获取到锁，从SQS消费任务
                    # 长轮询等待20秒，减少API调用
                    # 注意：消息到达后会立即返回，不需要等20秒
                    sqs_start_time = time.time()
                    logger.debug(f"🔄 主循环迭代 {loop_iteration}: 开始调用 SQS consume_task (wait_time_seconds=20)...")
                    try:
                        task = await self.sqs_service.consume_task(max_messages=1, wait_time_seconds=20)
                        sqs_elapsed = time.time() - sqs_start_time
                        logger.debug(f"✅ SQS consume_task 返回 (耗时: {sqs_elapsed:.2f}秒, self.running={self.running})")
                    except asyncio.CancelledError:
                        sqs_elapsed = time.time() - sqs_start_time
                        logger.info(f"⏹️ SQS consume_task 被取消 (耗时: {sqs_elapsed:.2f}秒)")
                        raise
                    
                    # 再次检查 self.running（可能在等待SQS时被设置为False）
                    if not self.running:
                        logger.info(f"⏹️ 检测到 self.running=False，退出主循环")
                        break
                    
                    if task:
                        # 异步处理任务（不阻塞）；锁已交给 process_task，由其在完成时 release
                        acquired_and_not_passed = False
                        logger.debug(f"📦 收到任务，创建异步任务处理...")
                        asyncio.create_task(self.process_task(task))
                    else:
                        # 没有任务，短暂休眠（release 在 finally 中统一做）
                        await asyncio.sleep(0.1)
                else:
                    # 无法获取锁（达到并发限制），等待一段时间再检查
                    # 不消费任务，让任务留在队列中等待
                    logger.debug(f"⏸️ 达到并发限制，等待中: {self.consumer_name} (迭代 {loop_iteration})")
                    await asyncio.sleep(1)  # 等待1秒后再检查
                    
            except asyncio.CancelledError:
                logger.info(f"⏹️ Worker被取消: {self.consumer_name} (迭代 {loop_iteration})")
                break
            except Exception as e:
                logger.error(f"❌ Worker处理任务失败: {e} (迭代 {loop_iteration})", exc_info=True)
                await asyncio.sleep(1)
            finally:
                if acquired_and_not_passed:
                    await self.rate_limiter.release()
        
        logger.info(f"✅ 主循环已退出: {self.consumer_name} (总迭代次数: {loop_iteration})")
    
    async def _listen_cancel_messages(self, redis_client):
        """
        监听Redis Pub/Sub取消消息（跨机器/跨进程协调）
        
        功能：
        - 订阅取消消息频道
        - 收到取消消息时，立即取消对应的运行中任务
        
        工作流程：
        1. 订阅取消消息频道（pattern: cancel_channel_prefix*）
        2. 循环监听消息（使用get_message，设置超时避免阻塞）
        3. 收到消息后，解析run_id
        4. 如果任务正在运行，调用cancel_running_task取消
        
        多Worker行为：
        - 每个worker都订阅相同的频道
        - 收到取消消息的worker会取消自己正在运行的任务
        - 如果任务在其他worker运行，该worker也会收到消息并取消
        """
        self.pubsub = redis_client.pubsub()
        pattern = f"{self.redis_service.cancel_channel_prefix}*"
        
        try:
            await self.pubsub.psubscribe(pattern)
            logger.info(f"📡 开始监听取消消息: pattern={pattern}")
            
            # ✅ 使用 get_message(timeout=1.0) 而不是 listen()，这样可以定期检查 self.running
            # 避免 listen() 无限阻塞导致无法及时响应 stop() 调用
            while self.running:
                try:
                    # 使用 get_message 并设置超时，避免无限阻塞
                    # timeout=1.0 表示最多等待1秒，如果没有消息则返回 None
                    message = await self.pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0
                    )
                    
                    if message and message.get('type') == 'pmessage':
                        try:
                            # 解析消息
                            data = json.loads(message['data'])
                            run_id = data.get('run_id')
                            
                            if run_id:
                                logger.info(f"📢 收到取消消息: run_id={run_id}")
                                # 如果任务正在运行，立即取消（task_obj.cancel() 会立即抛出 CancelledError）
                                if run_id in self.running_tasks:
                                    await self.cancel_running_task(run_id)
                        except (json.JSONDecodeError, KeyError) as e:
                            logger.warning(f"⚠️ 解析取消消息失败: {e}, message={message}")
                    # 如果 message 为 None（超时），继续循环检查 self.running
                except asyncio.CancelledError:
                    logger.info(f"⏹️ 取消消息监听被取消: {self.consumer_name}")
                    break
        except asyncio.CancelledError:
            logger.info(f"⏹️ 取消消息监听已停止: {self.consumer_name}")
        except Exception as e:
            logger.error(f"❌ 监听取消消息失败: {e}", exc_info=True)
        finally:
            if self.pubsub:
                try:
                    # 先取消订阅，再关闭
                    await self.pubsub.punsubscribe(pattern)
                    await self.pubsub.close()
                    logger.info(f"✅ Pub/Sub 连接已关闭: {self.consumer_name}")
                except Exception as e:
                    logger.error(f"❌ 关闭 Pub/Sub 连接失败: {e}", exc_info=True)
                finally:
                    self.pubsub = None
    
    async def stop(self):
        """
        停止Worker（优雅关闭）
        
        关闭流程：
        1. 设置running=False，停止主循环
        2. 关闭Pub/Sub连接
        3. 取消Pub/Sub监听任务
        4. 等待正在运行的任务完成（最多5分钟）
        5. 如果超时，强制取消剩余任务（最多等待10秒）
        
        注意：
        - 优雅关闭确保任务不会突然中断
        - 超时机制防止无限等待
        """
        import time
        stop_start_time = time.time()
        self.running = False
        logger.info(f"⏹️ 正在停止Worker: {self.consumer_name}")
        
        # 1. 先关闭 Pub/Sub（避免阻塞）
        logger.info(f"📝 stop() 步骤1: 关闭 Pub/Sub 连接...")
        pubsub_start_time = time.time()
        if self.pubsub:
            try:
                await self.pubsub.close()
                logger.info(f"✅ Pub/Sub 连接已关闭 (耗时: {time.time() - pubsub_start_time:.2f}秒)")
            except Exception as e:
                logger.error(f"❌ 关闭 Pub/Sub 连接失败: {e} (耗时: {time.time() - pubsub_start_time:.2f}秒)", exc_info=True)
            finally:
                self.pubsub = None
        else:
            logger.info(f"✅ Pub/Sub 连接不存在，跳过 (耗时: {time.time() - pubsub_start_time:.2f}秒)")
        
        # 2. 取消 Pub/Sub 任务
        logger.info(f"📝 stop() 步骤2: 取消 Pub/Sub 任务...")
        pubsub_task_start_time = time.time()
        if self.pubsub_task:
            self.pubsub_task.cancel()
            try:
                await asyncio.wait_for(self.pubsub_task, timeout=2.0)
                logger.info(f"✅ Pub/Sub 任务已取消 (耗时: {time.time() - pubsub_task_start_time:.2f}秒)")
            except (asyncio.CancelledError, asyncio.TimeoutError):
                logger.warning(f"⚠️ Pub/Sub 任务取消超时或已取消 (耗时: {time.time() - pubsub_task_start_time:.2f}秒)")
        else:
            logger.info(f"✅ Pub/Sub 任务不存在，跳过 (耗时: {time.time() - pubsub_task_start_time:.2f}秒)")
        
        # 3. 优雅关闭：等待正在运行的任务完成（最多5分钟）
        logger.info(f"📝 stop() 步骤3: 检查并等待正在运行的任务...")
        tasks_wait_start_time = time.time()
        
        # 详细记录 running_tasks 的状态
        total_tasks = len(self.running_tasks)
        if total_tasks > 0:
            done_tasks = [t for t in self.running_tasks.values() if t.done()]
            running_tasks = [t for t in self.running_tasks.values() if not t.done()]
            running_count = len(running_tasks)
            done_count = len(done_tasks)
            
            logger.info(f"📊 running_tasks 状态: 总数={total_tasks}, 已完成={done_count}, 运行中={running_count}")
            
            if running_count > 0:
                # 记录运行中任务的 run_id
                running_run_ids = [run_id for run_id, task_obj in self.running_tasks.items() if not task_obj.done()]
                logger.info(f"⏹️ 优雅关闭：等待 {running_count} 个运行中的任务完成（最多5分钟）...")
                logger.info(f"📋 运行中的任务 run_ids: {running_run_ids[:10]}{'...' if len(running_run_ids) > 10 else ''}")
                
                # 等待所有任务完成（最多等待2小时55分钟，配合 K8s terminationGracePeriodSeconds: 10800）
                gather_start_time = time.time()
                try:
                    logger.info(f"⏳ 开始等待任务完成 (asyncio.gather 包含 {total_tasks} 个任务)...")
                    await asyncio.wait_for(
                        asyncio.gather(*self.running_tasks.values(), return_exceptions=True),
                        timeout=10500.0  # 2小时55分钟，留5分钟余量给K8s清理
                    )
                    gather_elapsed = time.time() - gather_start_time
                    logger.info(f"✅ 所有任务已完成 (gather 耗时: {gather_elapsed:.2f}秒)")
                except asyncio.TimeoutError:
                    gather_elapsed = time.time() - gather_start_time
                    logger.warning(f"⏸️ 优雅关闭超时（2小时55分钟），gather 已耗时: {gather_elapsed:.2f}秒")
                    
                    # 超时后，强制取消剩余任务
                    remaining_tasks = [run_id for run_id, task_obj in self.running_tasks.items() if not task_obj.done()]
                    if remaining_tasks:
                        logger.warning(f"⏸️ 强制取消 {len(remaining_tasks)} 个剩余任务: {remaining_tasks[:10]}{'...' if len(remaining_tasks) > 10 else ''}")
                        cancel_tasks_start_time = time.time()
                        for run_id in remaining_tasks:
                            task_obj = self.running_tasks[run_id]
                            if not task_obj.done():
                                task_obj.cancel()
                        
                        # 等待取消完成（最多等待10秒）
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(*[self.running_tasks[rid] for rid in remaining_tasks], return_exceptions=True),
                                timeout=10.0
                            )
                            logger.info(f"✅ 剩余任务已取消 (耗时: {time.time() - cancel_tasks_start_time:.2f}秒)")
                        except asyncio.TimeoutError:
                            logger.warning(f"⏸️ 部分任务取消超时 (已耗时: {time.time() - cancel_tasks_start_time:.2f}秒)，强制停止")
            else:
                logger.info(f"✅ 没有正在运行的任务（{done_count} 个已完成的任务在 running_tasks 中，但不会等待）")
        else:
            logger.info(f"✅ running_tasks 为空，没有任务需要等待")
        
        tasks_wait_elapsed = time.time() - tasks_wait_start_time
        total_stop_elapsed = time.time() - stop_start_time
        logger.info(f"✅ Worker已停止: {self.consumer_name} (步骤3耗时: {tasks_wait_elapsed:.2f}秒, 总耗时: {total_stop_elapsed:.2f}秒)")
    
    async def _update_task_status(
        self, 
        run_id: str, 
        status: TaskStatus, 
        **kwargs
    ):
        """
        统一更新任务状态（先DB后Redis）
        
        功能：
        - 更新数据库中的任务状态（主数据源，确保持久化）
        - 更新Redis中的任务状态（缓存，用于快速查询）
        
        更新顺序：
        1. 先更新DB（主数据源）
        2. 再更新Redis（缓存）
        
        参数：
        - run_id: 任务ID
        - status: 任务状态（TaskStatus枚举）
        - **kwargs: 其他字段（completed_at, error_message, conversation_uuid等）
        
        注意：
        - completed_at如果是datetime对象，会自动转换为ISO格式字符串存储到Redis
        - 遵循最佳实践：DB是主数据源，Redis是缓存
        """
        # 1. 先更新DB（主数据源，确保持久化）- asyncpg 无需传入 db
        await async_update_conversation_run_status(
            run_id=run_id,
            status=status.value,
            **{k: v for k, v in kwargs.items() if k in ['completed_at', 'error_message']}
        )
        
        # 2. 再更新Redis（缓存，用于快速查询）
        # 将completed_at转换为ISO格式字符串（如果存在）
        redis_kwargs = {}
        for k, v in kwargs.items():
            if k == 'completed_at' and isinstance(v, datetime):
                redis_kwargs[k] = v.isoformat()
            elif k in ['cancelled_at', 'created_at']:
                redis_kwargs[k] = v
            else:
                redis_kwargs[k] = v
        
        await self.redis_service.update_task_status(
            run_id, 
            status.value, 
            **redis_kwargs
        )

    def _rebuild_user_input(self, task_data: dict):
        """
        从任务数据重建UserInput对象
        
        功能：
        - 从序列化的task_data中重建UserInput对象
        - 包括images、audio_files、video_files等文件对象
        
        参数：
        - task_data: 任务数据字典（从SQS消息中解析）
        
        返回：
        - UserInput对象，用于传递给Agent执行
        """
        from ...models.video_state import UserInput, ImageUserInput, AudioFileUserInput, VideoFileUserInput
        
        user_input_files = task_data.get("user_input_files", {})
        images = []
        audio_files = []
        video_files = []
        
        # 重建文件对象
        if user_input_files:
            for img_data in user_input_files.get("images", []):
                images.append(ImageUserInput(**img_data))
            for audio_data in user_input_files.get("audio_files", []):
                audio_files.append(AudioFileUserInput(**audio_data))
            for video_data in user_input_files.get("video_files", []):
                video_files.append(VideoFileUserInput(**video_data))
        
        # content_category 在 user_option 里，由前端在 user_option JSON 中传入
        return UserInput(
            user_input=task_data.get("user_input", ""),
            images=images,
            audio_files=audio_files,
            video_files=video_files,
            user_option=task_data.get("user_option"),
            agent_type=task_data.get("agent_type"),
        )
    
    async def _execute_agent(self, user_input_data, task_data, run_id, credit_callback=None):
        """
        执行Agent（Worker模式）- 返回 (终态, interrupt_message_id 或 None, interrupt_data 或 None)
        interrupt_message_id 用于 full_auto 时 15s 后自动 continue。
        interrupt_data 为中断 payload，含 disable_auto_resume 时表示「失败暂停」，不应自动继续。
        """
        import json

        conversation_id = int(task_data.get("conversation_id"))
        thread_id = task_data.get("thread_id")
        conversation_uuid = task_data.get("conversation_uuid")
        resume_data = task_data.get("resume_data")

        last_event_type = None
        last_interrupt_message_id = None
        last_interrupt_data = None

        skip_ar = bool(task_data.get("skip_agent_router"))
        async for event_str in self.agent_router_service.astream(
            user_input_data=user_input_data,
            user_id=task_data.get("user_id"),
            conversation_id=conversation_id,
            thread_id=thread_id,
            async_db=None,
            resume_data=resume_data,
            run_id=run_id,
            conversation_uuid=conversation_uuid,
            credit_callback=credit_callback,
            full_auto=bool(task_data.get("full_auto", False)),
            language=task_data.get("language"),
            skip_router_analysis=skip_ar,
            delegated_agent_type=task_data.get("agent_type") if skip_ar else None,
            delegated_detected_language=task_data.get("language") if skip_ar else None,
        ):
            if event_str.startswith("data: "):
                try:
                    event_data = json.loads(event_str[6:])
                    event_type = event_data.get("type", "")
                    if event_type in [MessageType.STREAM_END.value, MessageType.INTERRUPT.value,
                                      MessageType.ERROR.value, MessageType.CANCELLED.value]:
                        last_event_type = event_type
                        if event_type == MessageType.INTERRUPT.value:
                            last_interrupt_message_id = event_data.get("message_id")
                            last_interrupt_data = event_data.get("interrupt_data")
                except json.JSONDecodeError:
                    pass

        if last_event_type == MessageType.INTERRUPT.value:
            return (TaskStatus.INTERRUPTED, last_interrupt_message_id, last_interrupt_data)
        if last_event_type == MessageType.ERROR.value:
            return (TaskStatus.FAILED, None, None)
        if last_event_type == MessageType.CANCELLED.value:
            return (TaskStatus.CANCELLED, None, None)
        if last_event_type == MessageType.STREAM_END.value:
            return (TaskStatus.COMPLETED, None, None)
        return (TaskStatus.COMPLETED, None, None)
    
    async def process_task(self, task: dict):
        """
        处理任务（入口方法）
        
        功能：
        - 检查任务状态（是否已取消）
        - 创建异步任务并跟踪
        - 等待任务完成
        
        工作流程：
        1. 从task中提取message_id、receipt_handle、task_data、run_id
        2. 检查Redis中的任务状态：
           - 如果已取消，直接删除SQS消息并释放锁
           - 如果状态不是QUEUED，删除消息并释放锁（防御性编程）
        3. 创建异步任务（_process_task_internal）
        4. 跟踪任务（self.running_tasks）
        5. 等待任务完成
        
        参数：
        - task: 任务字典，包含：
          - message_id: SQS消息ID
          - receipt_handle: SQS收据句柄（用于删除消息）
          - data: 任务数据字典
        
        注意：
        - 锁已经在start()中通过rate_limiter.acquire()获取
        - 锁会在_process_task_internal的finally中释放
        - 如果任务已取消，需要在这里释放锁
        """
        message_id = task["message_id"]
        receipt_handle = task["receipt_handle"]
        task_data = task["data"]
        run_id = task_data["run_id"]

        # 15s 自动 continue：type=auto_resume 为约定字段，仅用于本延迟消息；正常任务无 type 或为其他含义
        if task_data.get("type") == "auto_resume":
            try:
                redis_chk = await get_redis_stream_service()
                _ir = str(task_data.get("run_id") or "")
                _im = str(task_data.get("interrupt_msgid") or "")
                if _ir and _im and await redis_chk.is_auto_resume_dismissed(_ir, _im):
                    logger.info(
                        "⏭️ auto_resume 已跳过（用户已关闭本次自动继续）run_id=%s interrupt_msgid=%s",
                        _ir,
                        _im,
                    )
                else:
                    user_id = task_data.get("user_id")
                    thread_id = task_data.get("thread_id")
                    check_ok, _, _ = await check_credits_before_task(user_id)
                    if not check_ok:
                        # 积分不足：跳过自动继续，且不调用 prepare_resume_task（不把 interrupt 消息标记为 continued），
                        # 老 run 保持 INTERRUPTED，用户充值后仍可手动点击继续
                        logger.warning(
                            "⏸️ auto_resume 积分不足，跳过自动继续（保留 INTERRUPTED，充值后可手动继续）run_id=%s interrupt_msgid=%s",
                            _ir,
                            _im,
                        )
                    else:
                        resume_payload = {"run_id": task_data.get("run_id"), "interrupt_msgid": task_data.get("interrupt_msgid")}
                        resume_task_data = await prepare_resume_task(resume_payload, user_id)
                        await self.sqs_service.add_task_to_queue(resume_task_data)
                        redis_svc = await get_redis_stream_service()
                        await redis_svc.add_task_index(thread_id, resume_task_data["run_id"])
                        logger.info(f"✅ 15s 自动 continue 已入队: run_id={resume_task_data['run_id']}")
            except Exception as e:
                logger.warning(f"⚠️ auto_resume 准备失败（可能用户已手动继续）: {e}")
            finally:
                await self.sqs_service.delete_task(receipt_handle)
                await self.rate_limiter.release()
            return

        # 检查任务状态（先 Redis 再 DB，避免 Redis 过期后误执行已取消/终态任务）
        task_status = await async_get_task_status_cached(run_id)
        if task_status:
            status = task_status.get('status')
            
            # 如果任务已被取消，直接删除SQS消息并释放锁
            if status == TaskStatus.CANCELLED.value:
                logger.info(f"⏹️ 任务已被取消，跳过执行: run_id={run_id}")
                await self.sqs_service.delete_task(receipt_handle)
                await self.rate_limiter.release()
                return
            
            # 只接受 QUEUED（新任务）或 RESUME_QUEUED（resume：API 入队前置为 resume_queued 防重复，RUNNING 仅由本 worker 在 _process_task_internal 内设置）
            if status not in (TaskStatus.QUEUED.value, TaskStatus.RESUME_QUEUED.value):
                logger.error(
                    f"❌ 任务已处于终态或非入队态，跳过执行: run_id={run_id}, status={status} "
                    f"(允许 QUEUED/RESUME_QUEUED；RUNNING/COMPLETED/FAILED/INTERRUPTED 视为重复或异常，删除消息)"
                )
                await self.sqs_service.delete_task(receipt_handle)
                await self.rate_limiter.release()
                return
        
        # 创建任务并跟踪
        task_coro = self._process_task_internal(task, message_id, receipt_handle, task_data, run_id)
        task_obj = asyncio.create_task(task_coro)
        self.running_tasks[run_id] = task_obj
        
        try:
            await task_obj
        except asyncio.CancelledError:
            logger.info(f"⏹️ 任务被取消: run_id={run_id}")
        finally:
            self.running_tasks.pop(run_id, None)
    
    async def _extend_visibility_timeout_loop(self, receipt_handle: str, run_id: str):
        """
        定期延长消息的visibility timeout（后台任务）
        
        功能：
        - 每27.5分钟延长一次visibility timeout（每次延长30分钟）
        - 确保长时间任务不会导致消息重新变为可见
        - 任务完成后自动停止
        
        工作方式：
        - 完全并行运行，不影响主任务逻辑
        - 如果任务在30分钟内完成，不需要延长
        - 如果任务超过30分钟，会持续延长
        
        参数：
        - receipt_handle: SQS消息的receipt_handle
        - run_id: 任务ID（用于日志）
        
        注意：
        - 如果延长失败（消息已被删除），会记录日志但不影响主任务
        - 任务完成后会自动停止（通过CancelledError）
        - 使用asyncio.create_task创建，完全并行，不阻塞主任务
        """
        try:
            # 每27.5分钟延长一次，每次延长30分钟（确保有足够的时间）
            # 如果任务在30分钟内完成，不需要延长
            # 如果任务超过30分钟，会持续延长
            extend_interval = 1650  # 27.5分钟（在30分钟前延长）
            visibility_timeout = 1800  # 30分钟
            
            while True:
                await asyncio.sleep(extend_interval)
                
                try:
                    await self.sqs_service.extend_visibility_timeout(
                        receipt_handle,
                        visibility_timeout
                    )
                    logger.debug(f"✅ 已延长消息visibility timeout: run_id={run_id}, timeout={visibility_timeout}秒")
                except Exception as e:
                    # 延长失败（可能是消息已被删除），停止循环
                    logger.debug(f"⚠️ 延长visibility timeout失败，停止延长: run_id={run_id}, error={e}")
                    break
        except asyncio.CancelledError:
            # 任务完成或被取消，停止延长
            logger.debug(f"⏹️ 停止延长visibility timeout: run_id={run_id}")
            raise
    
    async def _refresh_pipeline_lock_loop(self, thread_id: str, run_id: str):
        """持锁期间定期续期，防止长节点（如批量视频生成）执行中锁因 TTL 到期被他人抢占。"""
        try:
            while True:
                await asyncio.sleep(PIPELINE_LOCK_REFRESH_INTERVAL)
                try:
                    ok = await self.redis_service.refresh_pipeline_lock(
                        thread_id, run_id, ttl_seconds=PIPELINE_LOCK_TTL
                    )
                    if not ok:
                        logger.warning(
                            "⚠️ 管线锁续期失败（可能已被释放/被他人持有）: thread=%s run_id=%s",
                            thread_id, run_id,
                        )
                except Exception as e:
                    logger.debug("管线锁续期异常（忽略）: thread=%s run_id=%s err=%s", thread_id, run_id, e)
        except asyncio.CancelledError:
            raise

    async def _process_task_internal(self, task: dict, message_id: str, receipt_handle: str, task_data: dict, run_id: str):
        """
        内部任务处理逻辑（核心方法）
        
        功能：
        - 执行完整的任务处理流程
        - 更新任务状态
        - 删除SQS消息
        - 清理资源
        
        工作流程：
        1. 更新任务状态为RUNNING
        2. 启动后台任务延长visibility timeout（防止长时间任务导致消息重新可见）
        3. 重建UserInput对象
        4. 执行Agent，获取终态
        5. 更新任务状态（根据终态）
        6. 删除SQS消息（确认完成）
        7. 清理任务的消息流（释放内存）
        
        异常处理：
        - CancelledError: 任务被取消，更新状态为CANCELLED，删除SQS消息
        - Exception: 任务执行失败，更新状态为FAILED，删除SQS消息
        - finally: 释放执行权限（rate_limiter.release）
        
        参数：
        - task: 任务字典
        - message_id: SQS消息ID
        - receipt_handle: SQS收据句柄（用于删除消息）
        - task_data: 任务数据字典
        - run_id: 任务ID
        
        注意：
        - 锁在start()中获取，在这里的finally中释放
        - 无论成功还是失败，都要删除SQS消息，避免重复处理
        - 长时间任务会通过后台任务定期延长visibility timeout
        """
        extend_task = None
        final_status = None
        error_message = None
        completed_time = None
        credit_callback = None
        pending_auto_resume_payload = None  # INTERRUPTED + full_auto 时在 finally 入队 15s 延迟消息，避免先入队后更新状态导致 15s 后校验失败
        thread_id = task_data.get("thread_id")
        lock_acquired = False  # 是否持有本 thread 管线单飞锁
        lock_refresh_task = None
        try:
            logger.info(f"🚀 开始处理任务: run_id={run_id}")
            # 单飞锁：同一 thread 同一时刻只允许一个 run 执行视频管线。抢锁失败说明已有 run 在跑，
            # 延迟重新入队本任务（不置 RUNNING、不删消息逻辑由下方处理），待持锁者到达下一 gate/完成后再续跑。
            if thread_id:
                lock_acquired = await self.redis_service.acquire_pipeline_lock(
                    thread_id, run_id, ttl_seconds=PIPELINE_LOCK_TTL
                )
                if not lock_acquired:
                    owner = await self.redis_service.get_pipeline_lock_owner(thread_id)
                    retries = int(task_data.get("pipeline_lock_retries", 0)) + 1
                    if retries <= PIPELINE_LOCK_MAX_RETRIES:
                        logger.info(
                            "⏳ thread 已有运行中的管线(owner=%s)，延迟 %ss 重试本任务(第 %s 次): thread=%s run_id=%s",
                            owner, PIPELINE_LOCK_RETRY_DELAY, retries, thread_id, run_id,
                        )
                        requeued = dict(task_data)
                        requeued["pipeline_lock_retries"] = retries
                        try:
                            await self.sqs_service.add_task_to_queue(
                                requeued, delay_seconds=PIPELINE_LOCK_RETRY_DELAY
                            )
                        except Exception as e:
                            logger.warning("⚠️ 延迟重试入队失败（消息将按可见性自然重投）: run_id=%s err=%s", run_id, e)
                    else:
                        logger.error(
                            "❌ 抢锁重试超过上限(%s)，放弃本任务: thread=%s run_id=%s owner=%s",
                            PIPELINE_LOCK_MAX_RETRIES, thread_id, run_id, owner,
                        )
                    await self.sqs_service.delete_task(receipt_handle)
                    return
                lock_refresh_task = asyncio.create_task(
                    self._refresh_pipeline_lock_loop(thread_id, run_id)
                )
            extend_task = asyncio.create_task(
                self._extend_visibility_timeout_loop(receipt_handle, run_id)
            )
            await self._update_task_status(
                run_id,
                TaskStatus.RUNNING,
                conversation_uuid=task_data.get("conversation_uuid"),
                created_at=task_data.get("created_at")
            )
            # 在 task_worker 中创建 callback，传入 astream，stream 结束后在 finally 统一读 total_cost 写 DB
            try:
                from ...callbacks.credit_check_callback import create_credit_check_callback
                credit_callback = create_credit_check_callback(
                    user_id=task_data.get("user_id"),
                    action="video_generation",
                    db=None,
                )
            except Exception as e:
                logger.warning("创建 credit_callback 失败（不影响任务执行）: run_id=%s, error=%s", run_id, e)
            user_input_data = self._rebuild_user_input(task_data)
            logger.info(f"📝 开始执行任务: run_id={run_id}, user_input={task_data.get('user_input', '')[:50]}")
            final_status, interrupt_message_id, interrupt_data = await self._execute_agent(
                user_input_data=user_input_data,
                task_data=task_data,
                run_id=run_id,
                credit_callback=credit_callback,
            )
            completed_time = datetime.utcnow()
            logger.info(f"✅ Agent执行完成: run_id={run_id}, final_status={final_status.value}")

            # 完全托管：仅记录 payload，在 finally 状态更新后再入队 15s 延迟消息，避免 15s 后 prepare_resume_task 校验时 run 尚未 INTERRUPTED
            if final_status == TaskStatus.INTERRUPTED and interrupt_message_id is not None:
                full_auto = bool((task_data.get("user_option") or {}).get("full_auto", False))
                # 失败暂停（generation 阶段阻断失败）时不排 15s 自动继续，避免「带病继续」
                disable_auto_resume = bool(
                    (interrupt_data or {}).get("disable_auto_resume")
                ) if isinstance(interrupt_data, dict) else False
                if disable_auto_resume:
                    logger.info(
                        "⛔ 失败暂停（stage=%s），跳过 15s 自动继续: run_id=%s",
                        (interrupt_data or {}).get("stage"),
                        run_id,
                    )
                if full_auto and not disable_auto_resume:
                    pending_auto_resume_payload = {
                        "type": "auto_resume",
                        "run_id": run_id,
                        "thread_id": task_data.get("thread_id"),
                        "interrupt_msgid": interrupt_message_id,
                        "user_id": task_data.get("user_id"),
                    }
        except asyncio.CancelledError:
            logger.info(f"⏹️ 任务执行中被取消: run_id={run_id}")
            final_status = TaskStatus.CANCELLED
            raise
        except Exception as e:
            logger.error(f"❌ 任务执行失败: run_id={run_id}, error={e}", exc_info=True)
            final_status = TaskStatus.FAILED
            error_message = str(e)
        finally:
            if final_status is not None:
                task_updates = {}
                # 1. 更新 task_record（video_task_records）终态，统一放此处
                try:
                    task_updates = {
                        "task_status": final_status.value,
                        "task_finish_time": completed_time or datetime.utcnow(),
                    }
                    await update_task_record(run_id, task_updates)
                except Exception as e:
                    logger.warning("更新 task_record 失败（可未跑迁移）: run_id=%s, error=%s", run_id, e)
                # 2. 把 callback 累计的统计成本写入 conversation_run.cost（统计字段）
                if credit_callback is not None:
                    try:
                        callback_cost = credit_callback.total_cost
                        if callback_cost > 0:
                            # 只写 cost 字段，不改 billing_status
                            # billing_status 由下方 _handle_image_agent_billing 或
                            # async_set_conversation_run_billing_pending_if_not_completed 统一设置
                            # 避免此处临时写成 PENDING 导致 billing_worker 在时序窗口内误触发
                            from ...crud.conversation import async_update_conversation_run_cost
                            await async_update_conversation_run_cost(run_id, callback_cost)
                            logger.info(
                                "💰 callback 成本已写入 conversation_run.cost: run_id=%s, cost=$%.6f (llm=$%.6f, tool=$%.6f)",
                                run_id, callback_cost, credit_callback.llm_cost, credit_callback.tool_cost,
                            )
                    except Exception as e:
                        logger.warning("写入 callback 成本失败（不影响主流程）: run_id=%s, error=%s", run_id, e)
                    # 将 llm/tool 成本分项写入 LangSmith metadata，供与 LangSmith 自身统计对比
                    credit_callback.write_metadata_to_langsmith(run_id)
                # 3. 更新 conversation_run + Redis（billing 只记在 conversation_run）
                kwargs = {"conversation_uuid": task_data.get("conversation_uuid")}
                if final_status in (TaskStatus.COMPLETED, TaskStatus.INTERRUPTED):
                    kwargs["completed_at"] = completed_time or datetime.utcnow()
                if final_status == TaskStatus.FAILED:
                    kwargs["error_message"] = error_message or ""
                # 计费处理：读 DB 的 agent_type（路由分析写入，比前端 task_data 可靠）
                try:
                    run = await async_get_conversation_run_by_run_id(run_id)
                    db_agent_type = run.agent_type if run else None
                except Exception:
                    db_agent_type = None
                # image agent：按固定 10 积分/张直接扣款，不走 billing_worker
                if db_agent_type == AgentType.IMAGE.value and credit_callback is not None:
                    await self._handle_image_agent_billing(
                        run_id=run_id,
                        user_id=task_data.get("user_id") or "",
                        credit_callback=credit_callback,
                        kwargs=kwargs,
                    )
                else:
                    if await async_set_conversation_run_billing_pending_if_not_completed(run_id):
                        kwargs["billing_status"] = BillingStatus.PENDING.value
                        kwargs["user_id"] = task_data.get("user_id") or ""
                await self._update_task_status(run_id, final_status, **kwargs)
                await self.sqs_service.delete_task(receipt_handle)
                await self._cleanup_task_messages(run_id)
                # prepare_resume_task 会先写 continued=true；resume 失败必须回滚，否则 pending_gate 消失。
                if final_status == TaskStatus.FAILED and task_data.get("interrupt_msgid"):
                    try:
                        from ...crud.conversation import async_update_message_event_data_partial
                        await async_update_message_event_data_partial(
                            int(task_data["interrupt_msgid"]),
                            {"continued": False, "continued_at": None},
                        )
                    except Exception as e:
                        logger.warning(
                            "resume 失败后回滚 interrupt.continued 失败: run_id=%s err=%s",
                            run_id, e,
                        )
                # 在状态已更新为 INTERRUPTED 后再入队延迟 auto_resume，并写 auto_resume_at 供前端倒计时对齐
                if pending_auto_resume_payload:
                    try:
                        await schedule_interrupt_auto_resume(
                            run_id=pending_auto_resume_payload["run_id"],
                            thread_id=pending_auto_resume_payload["thread_id"],
                            interrupt_msgid=int(pending_auto_resume_payload["interrupt_msgid"]),
                            sqs_service=self.sqs_service,
                            auto_resume_payload=pending_auto_resume_payload,
                        )
                    except Exception as e:
                        logger.warning("⚠️ 入队延迟 auto_resume 失败: %s", e)
            if extend_task and not extend_task.done():
                extend_task.cancel()
                try:
                    await asyncio.wait_for(extend_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            if lock_refresh_task and not lock_refresh_task.done():
                lock_refresh_task.cancel()
                try:
                    await asyncio.wait_for(lock_refresh_task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            # 释放本 thread 管线单飞锁（仅当确为本 run 持有时才删，避免误删他人锁）
            if lock_acquired and thread_id:
                try:
                    await self.redis_service.release_pipeline_lock(thread_id, run_id)
                except Exception as e:
                    logger.debug("释放管线锁异常（忽略，TTL 兜底）: thread=%s run_id=%s err=%s", thread_id, run_id, e)
            await self.rate_limiter.release()
    
    async def _handle_image_agent_billing(
        self,
        run_id: str,
        user_id: str,
        credit_callback,
        kwargs: dict,
    ) -> None:
        """image agent 固定积分扣款：每成功生成一张图扣 10 积分，直接结算，不走 billing_worker。"""
        CREDITS_PER_IMAGE = 10
        image_count = credit_callback.image_success_count
        credits_to_deduct = image_count * CREDITS_PER_IMAGE

        if image_count == 0:
            # 没有成功图片，不扣款，但仍需补写 langsmith_cost（LLM 调用成本依然存在）
            kwargs["billing_status"] = BillingStatus.COMPLETED.value
            kwargs["user_id"] = user_id
            await async_update_conversation_run_billing(
                run_id,
                BillingStatus.COMPLETED.value,
                langsmith_status=LangsmithStatus.PENDING.value,
            )
            logger.info("🖼️ image agent 无成功图片，billing_status=COMPLETED, langsmith_status=PENDING: run_id=%s", run_id)
            return

        try:
            from ...utils.credit_deduction_utils import deduct_credits_with_cost, already_deducted_for_run
            from ...schemas.user import CreditOperationType

            if await already_deducted_for_run(user_id, run_id):
                logger.info("🖼️ image agent 积分已扣（幂等跳过）: run_id=%s", run_id)
            else:
                success, _, _ = await deduct_credits_with_cost(
                    run_id=run_id,
                    user_id=user_id,
                    action=CreditOperationType.IMAGE_GENERATION.value,
                    cost_float=0.0,          # image agent 不按成本算，credits_int 直接指定
                    credits_int=credits_to_deduct,
                )
                if success:
                    logger.info(
                        "🖼️ image agent 扣款完成: run_id=%s, image_count=%d, credits=%d",
                        run_id, image_count, credits_to_deduct,
                    )
                else:
                    logger.warning("🖼️ image agent 扣款失败: run_id=%s", run_id)

            await async_update_conversation_run_billing(
                run_id,
                BillingStatus.COMPLETED.value,
                langsmith_status=LangsmithStatus.PENDING.value,
                credits_deducted=True,
                credits_amount=credits_to_deduct,
                cost_calculated=True,
            )
            kwargs["billing_status"] = BillingStatus.COMPLETED.value
            kwargs["user_id"] = user_id
            logger.info("🖼️ image agent 扣款完成，等待 billing_worker 补写 langsmith_cost: run_id=%s", run_id)
        except Exception as e:
            logger.error("🖼️ image agent 扣款异常: run_id=%s, error=%s", run_id, e, exc_info=True)
            # 扣款失败时仍标记 PENDING，让 billing_worker 兜底
            if await async_set_conversation_run_billing_pending_if_not_completed(run_id):
                kwargs["billing_status"] = BillingStatus.PENDING.value
                kwargs["user_id"] = user_id

    async def cancel_running_task(self, run_id: str) -> bool:
        """
        取消正在运行的任务
        
        功能：
        - 通过task_obj.cancel()立即取消任务
        - cancel()会立即抛出CancelledError，任务会进入取消流程
        
        参数：
        - run_id: 任务ID
        
        返回：
        - bool: 是否成功取消（True=已取消，False=任务不存在或已完成）
        
        注意：
        - 如果任务已完成，从跟踪列表中移除
        - cancel()是异步安全的，不会影响其他任务
        """
        if run_id in self.running_tasks:
            task_obj = self.running_tasks[run_id]
            if not task_obj.done():
                task_obj.cancel()
                logger.info(f"⏹️ 已取消正在运行的任务: run_id={run_id}")
                return True
            else:
                # 任务已完成，从跟踪列表中移除
                self.running_tasks.pop(run_id, None)
        return False
    
    async def _cleanup_task_messages(self, run_id: str):
        """
        清理任务的消息流（任务完成后清理，释放内存）
        
        功能：
        - 删除Redis中的任务消息流
        - 释放内存，避免Redis内存泄漏
        
        参数：
        - run_id: 任务ID
        
        注意：
        - 清理失败不影响任务完成
        - 消息流用于实时推送任务进度，任务完成后不再需要
        """
        try:
            # 使用redis_service的message_prefix构建stream key
            stream_key = f"{self.redis_service.message_prefix}{run_id}"
            # 通过redis_service的redis客户端删除
            # 使用str模式的Redis客户端
            redis_client = await get_redis_client(decode_responses=True)
            await redis_client.delete(stream_key)
            logger.debug(f"✅ 已清理任务消息流: run_id={run_id}")
        except Exception as e:
            logger.warning(f"⚠️ 清理任务消息流失败: run_id={run_id}, error={e}")
