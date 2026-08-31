"""
Redis Stream服务 - 处理消息流、任务队列、任务状态
（与 Cuti-VideoAgent 对齐）
"""
import logging
import time
from typing import Optional, List, Dict, Any, Tuple
import redis.asyncio as aioredis
from datetime import datetime

from ...utils.asyncpg_utils import utc_isoformat

logger = logging.getLogger(__name__)

# 从base_agent导入事件类型（实际使用的是MessageType）
from ...services.agent.base_agent import MessageType
from ...models.task_status import TaskStatus, BillingStatus

# Generated事件（需要持久化的里程碑事件）
# 这些事件表示任务的重要阶段完成，需要保存到Redis Stream用于刷新后恢复
GENERATED_EVENTS = {
    # 视频代理事件
    MessageType.STORY_OUTLINE_GENERATED.value,      # 故事大纲生成完成
    MessageType.STORYBOARD_DETAIL_GENERATED.value,    # 详细分镜生成完成
    MessageType.KEYFRAMES_GENERATED.value,             # 关键帧生成完成
    MessageType.KEYFRAMES_REFLECTION_COMPLETED.value,  # 关键帧反思完成
    MessageType.NARRATIONS_GENERATED.value,           # 旁白生成完成
    MessageType.AUDIO_EFFECTS_GENERATED.value,       # 音效生成完成
    MessageType.VIDEO_SEGMENTS_GENERATED.value,       # 视频片段生成完成
    MessageType.VIDEO_SEGMENTS_ASSEMBLED.value,       # 视频片段合并完成
    MessageType.VIDEO_COMPLETED.value,                # 视频完成
    MessageType.CHARACTERS_DESIGNED.value,            # 角色设计完成
    MessageType.MUSIC_GENERATED.value,                # 音乐生成完成（视频代理）
    MessageType.SCENES_GENERATED.value,               # 场景生成完成
    MessageType.VIDEO_LIPSYNC_COMPLETED.value,        # 唇形同步完成
    MessageType.VIDEO_ANALYSIS.value,                 # 视频分析完成
    MessageType.WORKFLOW_STATE.value,

    # 其他代理事件
    MessageType.STORY_AGENT_GENERATED.value,          # 故事代理生成完成
    MessageType.MUSIC_AGENT_GENERATED.value,          # 音乐代理生成完成
    MessageType.IMAGE_AGENT_GENERATED.value,          # 图像代理生成完成
}

# Progress事件（中间状态，可跳过）
# 这些事件是进度更新，刷新后不需要恢复，可以跳过
PROGRESS_EVENTS = {
    MessageType.KEYFRAME_GENERATION_PROGRESS.value,   # 关键帧生成进度
    MessageType.KEYFRAME_REFLECTION_PROGRESS.value,   # 关键帧反思进度
    MessageType.VIDEO_GENERATION_PROGRESS.value,      # 视频生成进度
    MessageType.VIDEO_SEGMENTS_PROGRESS.value,        # 视频片段处理进度
    MessageType.MUSIC_GENERATION_PROGRESS.value,      # 音乐生成进度
    MessageType.VIDEO_LIPSYNC_PROGRESS.value,        # 唇形同步处理进度
}


class RedisStreamService:
    """Redis Stream服务"""
    
    def __init__(self, redis_client: aioredis.Redis, environment: Optional[str] = None):
        self.redis = redis_client
        
        # 获取环境前缀（从环境变量或参数）
        if environment is None:
            from ...config import get_settings
            settings = get_settings()
            env = settings.ENVIRONMENT.value  # "local", "development", "production"
        else:
            env = environment
        
        # 环境前缀映射（简化key）
        env_prefix_map = {
            "local": "local",
            "development": "dev",
            "production": "prod"
        }
        env_prefix = env_prefix_map.get(env, "dev")
        
        # 所有key都加上环境前缀
        self.message_prefix = f"cuti-videoagent:{env_prefix}:message:"
        self.task_queue_key = f"cuti-videoagent:{env_prefix}:task:queue"
        self.task_status_prefix = f"cuti-videoagent:{env_prefix}:task:status:"
        self.task_index_prefix = f"cuti-videoagent:{env_prefix}:task:index:"
        self.cancel_channel_prefix = f"cuti-videoagent:{env_prefix}:cancel:"
        
        logger.info(f"🔧 Redis Stream Service初始化: 环境={env} (前缀={env_prefix}), 队列key={self.task_queue_key}")
    
    @staticmethod
    def _decode_id(redis_id) -> str:
        """解码Redis ID（兼容bytes和str）"""
        return redis_id.decode() if isinstance(redis_id, bytes) else redis_id
    
    async def add_message(self, run_id: str, event_data: dict) -> str:
        """添加消息到Stream
        
        将整个event_data序列化为JSON字符串存储在一个字段中，简化处理
        """
        import json
        stream_key = f"{self.message_prefix}{run_id}"
        
        # 标记是否为generated事件
        event_type = event_data.get("type", "")
        event_data["is_generated"] = event_type in GENERATED_EVENTS
        
        # 将整个数据序列化为JSON字符串（Redis Stream要求值是字符串）
        payload = json.dumps(event_data, ensure_ascii=False, default=str)
        
        message_id = await self.redis.xadd(stream_key, {"data": payload})
        return self._decode_id(message_id)
    
    async def read_messages(
        self, 
        run_id: str, 
        from_id: str = "0",
        count: int = 100,
        block: Optional[int] = None
    ) -> List[Tuple[str, dict]]:
        """读取消息"""
        stream_key = f"{self.message_prefix}{run_id}"
        kwargs = {"count": count}
        if block is not None:
            kwargs["block"] = block
        
        messages = await self.redis.xread({stream_key: from_id}, **kwargs)
        
        if messages:
            import json
            stream, message_list = messages[0]
            result = []
            for msg_id, data in message_list:
                msg_id_str = self._decode_id(msg_id)
                
                # 从Redis读取数据（str模式，直接使用）
                payload_str = data.get("data")
                if payload_str is None:
                    continue
                
                # 反序列化JSON
                try:
                    decoded_data = json.loads(payload_str)
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning(f"解析消息JSON失败: {e}, payload: {payload_str[:100]}")
                    continue
                
                result.append((msg_id_str, decoded_data))
            return result
        
        return []
    
    async def get_all_messages(self, run_id: str) -> List[Tuple[str, dict]]:
        """获取所有消息"""
        import json
        stream_key = f"{self.message_prefix}{run_id}"
        messages = await self.redis.xrange(stream_key, "-", "+")
        
        result = []
        for msg_id, data in messages:
            msg_id_str = self._decode_id(msg_id)
            
            # 从Redis读取数据（str模式，直接使用）
            payload_str = data.get("data")
            if payload_str is None:
                continue
            
            # 反序列化JSON
            try:
                decoded_data = json.loads(payload_str)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"解析消息JSON失败: {e}, payload: {payload_str[:100]}")
                continue
            
            result.append((msg_id_str, decoded_data))
        
        return result
    
    async def check_stream_exists(self, run_id: str) -> bool:
        """检查消息流是否存在"""
        stream_key = f"{self.message_prefix}{run_id}"
        exists = await self.redis.exists(stream_key)
        return bool(exists)
    
    async def get_stream_length(self, run_id: str) -> int:
        """获取消息流长度"""
        stream_key = f"{self.message_prefix}{run_id}"
        length = await self.redis.xlen(stream_key)
        return length
    
    async def add_task_to_queue(self, task_data: dict) -> str:
        """添加任务到队列
        
        将整个task_data序列化为JSON字符串存储在一个字段中，简化处理
        """
        import json
        # 将整个数据序列化为JSON字符串（Redis Stream要求值是字符串）
        payload = json.dumps(task_data, ensure_ascii=False, default=str)
        
        message_id = await self.redis.xadd(self.task_queue_key, {"data": payload})
        return self._decode_id(message_id)
    
    async def consume_task(self, group_name: str, consumer_name: str) -> Optional[dict]:
        """消费任务"""
        messages = await self.redis.xreadgroup(
            group_name,
            consumer_name,
            {self.task_queue_key: ">"},
            count=1,
            block=1000
        )
        
        if messages:
            import json
            stream, message_list = messages[0]
            message_id, data = message_list[0]
            
            msg_id_str = self._decode_id(message_id)
            
            # 从Redis读取数据（str模式，直接使用）
            payload_str = data.get("data")
            if payload_str is None:
                return None
            
            # 反序列化JSON
            try:
                decoded_data = json.loads(payload_str)
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"解析任务JSON失败: {e}, payload: {payload_str[:100]}")
                return None
            
            return {"message_id": msg_id_str, "data": decoded_data}
        
        return None
    
    async def ack_task(self, group_name: str, message_id: str):
        """确认任务完成"""
        await self.redis.xack(self.task_queue_key, group_name, message_id)
    
    async def update_task_status(
        self,
        run_id: str,
        status: str,
        last_generated_event: Optional[str] = None,
        last_generated_id: Optional[str] = None,
        **kwargs
    ):
        """更新任务状态"""
        status_key = f"{self.task_status_prefix}{run_id}"
        data = {
            "run_id": run_id,  # 保存run_id以便查询
            "status": status,
            "updated_at": utc_isoformat(datetime.utcnow()),
            **kwargs
        }
        if last_generated_event:
            data["last_generated_event"] = last_generated_event
        if last_generated_id:
            data["last_generated_id"] = last_generated_id
        
        # Hash结构可以存储字符串值，直接格式化
        formatted_data = {}
        for k, v in data.items():
            if v is None:
                formatted_data[k] = ""
            elif isinstance(v, bool):
                formatted_data[k] = "1" if v else "0"
            else:
                formatted_data[k] = str(v)
        
        await self.redis.hset(status_key, mapping=formatted_data)
        # 设置过期时间（5分钟，cache-aside best practice：短 TTL + DB 回源写回）
        await self.redis.expire(status_key, 300)
    
    async def get_task_status(self, run_id: str) -> Optional[dict]:
        """获取任务状态（仅 Redis，无 DB 回源）。需要「先 Redis 再 DB」时请用 crud.conversation.async_get_task_status_cached。"""
        status_key = f"{self.task_status_prefix}{run_id}"
        data = await self.redis.hgetall(status_key)
        
        if data:
            # str模式，直接使用（兼容bytes模式）
            result = {}
            for k, v in data.items():
                key = k.decode() if isinstance(k, bytes) else k
                value = v.decode() if isinstance(v, bytes) else v
                result[key] = value
            return result
        
        return None
    
    async def get_user_running_tasks(self, user_id: str) -> List[dict]:
        """获取用户的所有运行中任务"""
        tasks = []
        pattern = f"{self.task_status_prefix}*"
        
        async for key in self.redis.scan_iter(match=pattern):
            key_str = self._decode_id(key)
            run_id = key_str.replace(self.task_status_prefix, "")
            
            status = await self.get_task_status(run_id)
            if status and status.get('status') in [TaskStatus.RUNNING.value, TaskStatus.QUEUED.value, TaskStatus.RESUME_QUEUED.value]:
                # 检查user_id是否匹配（从任务状态中获取）
                task_user_id = status.get('user_id')
                if task_user_id == user_id:
                    status['run_id'] = run_id
                    tasks.append(status)
        
        return tasks
    
    async def get_running_tasks_by_thread_id(self, thread_id: str) -> List[dict]:
        """获取该thread_id的所有运行中任务"""
        # 从任务索引获取所有run_id
        index_key = f"{self.task_index_prefix}{thread_id}"
        run_ids = await self.redis.zrevrange(index_key, 0, -1)
        
        tasks = []
        for run_id in run_ids:
            run_id_str = self._decode_id(run_id)
            status = await self.get_task_status(run_id_str)
            if status and status.get('status') in [TaskStatus.RUNNING.value, TaskStatus.QUEUED.value, TaskStatus.RESUME_QUEUED.value]:
                status['run_id'] = run_id_str
                status['thread_id'] = thread_id
                tasks.append(status)
        
        return tasks
    
    async def add_task_index(self, thread_id: str, run_id: str):
        """添加任务索引"""
        index_key = f"{self.task_index_prefix}{thread_id}"
        await self.redis.zadd(index_key, {run_id: time.time()})
        # 设置过期时间（6小时）
        await self.redis.expire(index_key, 21600)
    
    async def get_task_index(self, thread_id: str) -> List[str]:
        """获取任务索引（按时间倒序）"""
        index_key = f"{self.task_index_prefix}{thread_id}"
        run_ids = await self.redis.zrevrange(index_key, 0, -1)
        return [self._decode_id(run_id) for run_id in run_ids]
    
    async def publish_cancel(self, run_id: str):
        """发布取消消息到Pub/Sub channel（用于跨机器/跨进程通知）"""
        import json
        channel = f"{self.cancel_channel_prefix}{run_id}"
        message = json.dumps({"run_id": run_id, "timestamp": time.time()})
        subscribers = await self.redis.publish(channel, message)
        logger.info(f"📢 发布取消消息: run_id={run_id}, channel={channel}, 订阅者数={subscribers}")
        return subscribers
    
    def get_pubsub(self):
        """获取Pub/Sub对象（用于订阅）"""
        return self.redis.pubsub()
