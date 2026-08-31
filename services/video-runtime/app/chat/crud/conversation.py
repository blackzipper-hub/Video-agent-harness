"""
对话相关的CRUD操作 - asyncpg版本
"""

from typing import List, Optional, Dict, Any, Union, Tuple
from datetime import datetime
import json
import logging
import uuid as uuid_lib
import msgspec

from ..models.conversation import (
    ConversationDB, 
    ConversationMessageDB,
    ConversationRunDB,
    conversation_to_dict,
    message_to_dict
)
from ..models.database import get_asyncpg_pool
from ..utils.asyncpg_utils import fetch_one, fetch_all, fetch_val, execute, insert_and_return, now_utc, utc_isoformat

logger = logging.getLogger(__name__)

# ========== msgspec Struct 定义 ==========

class ConversationResult(msgspec.Struct, kw_only=True):
    """对话查询结果"""
    # === 基础字段 ===
    id: int
    uuid: str
    
    # === 元数据字段 ===
    user_id: str
    thread_id: str
    created_at: datetime
    updated_at: datetime
    last_active_at: datetime
    
    # === 对话状态 ===
    is_active: bool
    message_count: int
    
    # === 对话内容（可选）===
    title: Optional[str] = None
    user_input: Optional[str] = None
    user_input_files: Optional[str] = None
    user_option: Optional[str] = None
    agent_type: Optional[str] = None
    meta_data: Optional[str] = None
    additional_data: Optional[Dict[str, Any]] = None


class ConversationRunResult(msgspec.Struct, kw_only=True):
    """对话运行记录查询结果。读路径用 row_to_struct_safe 只传本 Struct 声明字段，防御 DB 新增列对老代码影响。"""
    # === 基础字段 ===
    id: int
    uuid: str
    conversation_id: str  # 注意：数据库中是 str 类型
    conversation_uuid: Optional[str]
    thread_id: str  # 重要：数据库中有此字段
    run_id: str
    user_id: str
    
    # === Run 信息 ===
    agent_type: Optional[str]
    user_option: Optional[str]
    user_input: Optional[str]
    user_input_files: Optional[str]
    
    # === 运行状态 ===
    status: str
    error_message: Optional[str]

    # === Run 类型与计费（cost_credits 兼容 dev + 6 字段）===
    run_type: Optional[str] = None
    cost_credits: Optional[float] = None
    billing_status: Optional[str] = None
    langsmith_status: Optional[str] = None
    langsmith_cost: Optional[float] = None
    cost: Optional[float] = None
    cost_calculated: Optional[bool] = None
    credits_deducted: Optional[bool] = None
    credits_amount: Optional[int] = None

    # === 时间戳 ===
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]
    
    # === 扩展数据 ===
    additional_data: Optional[Dict[str, Any]]


# ========== Helper Functions ==========

from .video.row_normalize import row_to_struct_safe

_CONVERSATION_DICT_FIELDS = ("additional_data",)
_CONVERSATION_RUN_DICT_FIELDS = ("user_option", "user_input_files", "additional_data")


class MessageEventDataResult(msgspec.Struct, kw_only=True):
    """仅用于按 id 查一条消息的 event_data（如 resume 重复点击校验），防御 DB 多列"""
    id: int
    event_data: Optional[Dict[str, Any]] = None


_MESSAGE_EVENT_DATA_DICT_FIELDS = ("event_data",)


def _row_to_message_event_data(row: Optional[Dict]) -> Optional[MessageEventDataResult]:
    """将 DB 行转为 MessageEventDataResult，只保留声明字段，DB 多列不崩"""
    return row_to_struct_safe(row, MessageEventDataResult, dict_fields=_MESSAGE_EVENT_DATA_DICT_FIELDS)


def _row_to_conversation(row: Optional[Dict]) -> Optional[ConversationResult]:
    """将数据库行转换为ConversationResult对象（只传 schema 字段，DB 多列不崩）"""
    return row_to_struct_safe(row, ConversationResult, dict_fields=_CONVERSATION_DICT_FIELDS)


def _row_to_conversation_run(row: Optional[Dict]) -> Optional[ConversationRunResult]:
    """将 DB 行转为 ConversationRunResult。用 row_to_struct_safe 只传 Struct 声明字段，防御 DB 新增列导致 Unexpected keyword argument（与 error_tracking 的 _row_to_task_record 一致）。"""
    return row_to_struct_safe(row, ConversationRunResult, dict_fields=_CONVERSATION_RUN_DICT_FIELDS)


# ========== 异步函数 ==========


async def async_get_conversation_by_id(conversation_id: int) -> Optional[ConversationResult]:
    """根据对话ID获取对话（异步版本）- 返回 msgspec.Struct"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        row = await fetch_one(
            conn,
            "SELECT * FROM conversations WHERE id = $1",
            conversation_id
        )
        return _row_to_conversation(row)


async def async_get_conversation_by_thread_id(thread_id: str) -> Optional[ConversationResult]:
    """根据线程ID获取对话（异步版本）- 返回 msgspec.Struct"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        row = await fetch_one(
            conn,
            "SELECT * FROM conversations WHERE thread_id = $1",
            thread_id
        )
        return _row_to_conversation(row)


async def async_create_conversation(
    user_id: str,
    thread_id: Optional[str] = None,
    title: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    user_input: Optional[str] = None,
    user_input_files: Optional[Dict[str, Any]] = None,
    user_option: Optional[Dict[str, Any]] = None,
    agent_type: Optional[str] = None,
) -> ConversationResult:
    """创建新对话（异步版本）- 返回 ConversationResult。language 由 LLM 识别后在 router 里写入。"""
    if not thread_id:
        thread_id = str(uuid_lib.uuid4())
    
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        conversation_uuid = str(uuid_lib.uuid4())
        row = await insert_and_return(
            conn,
            "conversations",
            uuid=conversation_uuid,
            user_id=user_id,
            thread_id=thread_id,
            title=title or f"对话 {datetime.now().strftime('%m-%d %H:%M')}",
            meta_data=json.dumps(metadata) if metadata else None,
            user_input=user_input,
            user_input_files=json.dumps(user_input_files) if user_input_files else None,
            user_option=json.dumps(user_option) if user_option else None,
            agent_type=agent_type,
            created_at=now_utc(),
            updated_at=now_utc(),
            last_active_at=now_utc(),
            is_active=True,
            message_count=0
        )
        if not row:
            raise RuntimeError("async_create_conversation: insert_and_return returned None")
        return _row_to_conversation(row)


async def async_update_conversation_first_request(
    conversation_id: int,
    user_input: Optional[str] = None,
    user_input_files: Optional[Dict[str, Any]] = None,
    user_option: Optional[Dict[str, Any]] = None,
    agent_type: Optional[str] = None
) -> bool:
    """更新对话的首次请求信息（异步版本）"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        await execute(
            conn,
            """
            UPDATE conversations
            SET user_input = $1,
                user_input_files = $2,
                user_option = $3,
                agent_type = $4,
                updated_at = $5
            WHERE id = $6
            """,
            user_input,
            json.dumps(user_input_files) if user_input_files else None,
            json.dumps(user_option) if user_option else None,
            agent_type,
            now_utc(),
            conversation_id
        )
        return True


async def _get_conversation_columns(conn) -> set:
    """查询 conversations 实际存在的列，用于只更新存在的列（未跑迁移的列不写，与 row_to_struct_safe 读路径一致）。"""
    row = await fetch_one(
        conn,
        """
        SELECT array_agg(column_name::text) AS cols
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'conversations'
        """
    )
    if not row or not row.get("cols"):
        return set()
    return set(row["cols"])


async def async_update_conversation_agent_type_and_language(
    conversation_id: int, agent_type: str, language: Optional[str] = None
) -> bool:
    """一次更新 conversation 的 agent_type 与 additional_data.language（路由分析后写入，减少请求次数）。仅写存在的列（与 _get_conversation_columns 防御一致）。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        allowed = await _get_conversation_columns(conn)
        if language and "additional_data" in allowed:
            result = await execute(
                conn,
                """
                UPDATE conversations
                SET agent_type = $1,
                    additional_data = jsonb_set(COALESCE(additional_data, '{}'::jsonb), '{language}', to_jsonb($2::text)),
                    updated_at = $3
                WHERE id = $4
                """,
                agent_type,
                language,
                now_utc(),
                conversation_id,
            )
        else:
            result = await execute(
                conn,
                "UPDATE conversations SET agent_type = $1, updated_at = $2 WHERE id = $3",
                agent_type,
                now_utc(),
                conversation_id,
            )
        return result == "UPDATE 1"


async def async_update_conversation_run_agent_type_and_language(
    run_id: str, agent_type: str, language: Optional[str] = None
) -> bool:
    """一次更新 conversation_run 的 agent_type 与 additional_data.language（路由分析后写入，减少请求次数）。仅写存在的列（与 _get_conversation_run_columns 防御一致）。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        allowed = await _get_conversation_run_columns(conn)
        if language and "additional_data" in allowed:
            result = await execute(
                conn,
                """
                UPDATE conversation_runs
                SET agent_type = $1,
                    additional_data = jsonb_set(COALESCE(additional_data, '{}'::jsonb), '{language}', to_jsonb($2::text)),
                    updated_at = $3
                WHERE run_id = $4
                """,
                agent_type,
                language,
                now_utc(),
                run_id,
            )
        else:
            result = await execute(
                conn,
                "UPDATE conversation_runs SET agent_type = $1, updated_at = $2 WHERE run_id = $3",
                agent_type,
                now_utc(),
                run_id,
            )
        return result == "UPDATE 1"


async def async_get_user_conversations(
    user_id: str, 
    page: int = 1, 
    size: int = 20,
    only_active: bool = True
) -> tuple[List[Dict[str, Any]], int]:
    """获取用户的对话列表（异步版本）"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        # 构建WHERE条件
        where_clause = "user_id = $1"
        params = [user_id]
        
        if only_active:
            where_clause += " AND is_active = true"
        
        # 获取总数
        total = await fetch_val(
            conn,
            f"SELECT COUNT(*) FROM conversations WHERE {where_clause}",
            *params
        )
        
        # 获取分页数据
        offset = (page - 1) * size
        conversations = await fetch_all(
            conn,
            f"""
            SELECT * FROM conversations 
            WHERE {where_clause}
            ORDER BY last_active_at DESC
            LIMIT $2 OFFSET $3
            """,
            *params, size, offset
        )
        
        return conversations, total


async def async_deactivate_conversation(conversation_id: int) -> bool:
    """停用对话（异步版本）"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        result = await execute(
            conn,
            """
            UPDATE conversations
            SET is_active = false, updated_at = $1
            WHERE id = $2
            """,
            now_utc(),
            conversation_id
        )
        return result == "UPDATE 1"


async def async_set_conversation_delegated_va_run_id(
    conversation_id: int,
    delegated_va_run_id: str,
) -> bool:
    """
    delegate 成功后写入 ``additional_data.delegated_va_run_id``，供内部编辑流程定位 VA run。
    """
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        allowed = await _get_conversation_columns(conn)
        if "additional_data" not in allowed:
            logger.warning("async_set_conversation_delegated_va_run_id: conversations.additional_data missing")
            return False
        result = await execute(
            conn,
            """
            UPDATE conversations
            SET additional_data = COALESCE(additional_data, '{}'::jsonb)
                || jsonb_build_object('delegated_va_run_id', to_jsonb($2::text)),
                updated_at = $3
            WHERE id = $1
            """,
            conversation_id,
            delegated_va_run_id,
            now_utc(),
        )
        return result == "UPDATE 1"


async def async_get_conversation_messages(
    conversation_id: Union[int, str], 
    limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """获取对话的消息历史（异步版本）"""
    # 确保 conversation_id 是 int 类型
    conversation_id_int = int(conversation_id) if isinstance(conversation_id, str) else conversation_id
    
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        query = "SELECT * FROM conversation_messages WHERE conversation_id = $1 ORDER BY created_at"
        
        if limit:
            query += f" LIMIT {limit}"
        
        return await fetch_all(conn, query, conversation_id_int)


async def async_get_messages_by_run_id(
    run_id: str,
    event_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """按 run_id 获取消息，可选按 event_type 过滤（如 story_agent_generated, music_agent_generated）。用于 admin 查看用户可见内容。"""
    if not run_id:
        return []
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        if event_types:
            placeholders = ", ".join([f"${i+2}" for i in range(len(event_types))])
            query = f"SELECT * FROM conversation_messages WHERE run_id = $1 AND event_type IN ({placeholders}) ORDER BY created_at"
            return await fetch_all(conn, query, run_id, *event_types)
        query = "SELECT * FROM conversation_messages WHERE run_id = $1 ORDER BY created_at"
        return await fetch_all(conn, query, run_id)


async def async_get_all_user_inputs(conversation_id: Union[int, str]) -> List[str]:
    """获取对话中所有的用户输入（异步版本）"""
    # 确保 conversation_id 是 int 类型
    conversation_id_int = int(conversation_id) if isinstance(conversation_id, str) else conversation_id
    
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        messages = await fetch_all(
            conn,
            """
            SELECT content FROM conversation_messages
            WHERE conversation_id = $1 AND role = 'user'
            ORDER BY created_at
            """,
            conversation_id_int
        )
        
        return [msg['content'] for msg in messages]


async def async_add_message_to_conversation(
    conversation_id: Union[int, str],
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
    event_type: Optional[str] = None,
    event_data: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    conversation_uuid: Optional[str] = None
) -> Dict[str, Any]:
    """添加消息到对话（异步版本）"""
    # 确保 conversation_id 是 int 类型
    conversation_id_int = int(conversation_id) if isinstance(conversation_id, str) else conversation_id
    
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 如果没有提供 conversation_uuid，从conversation获取
            if not conversation_uuid:
                conversation = await fetch_one(
                    conn,
                    "SELECT uuid FROM conversations WHERE id = $1",
                    conversation_id_int
                )
                if conversation:
                    conversation_uuid = conversation['uuid']
            
            # 获取当前消息数量作为序号
            count = await fetch_val(
                conn,
                "SELECT COUNT(*) FROM conversation_messages WHERE conversation_id = $1",
                conversation_id_int
            )
            sequence = count + 1
            
            # 创建消息
            message = await insert_and_return(
                conn,
                "conversation_messages",
                uuid=str(uuid_lib.uuid4()),
                conversation_id=conversation_id_int,
                conversation_uuid=conversation_uuid,
                run_id=run_id,
                role=role,
                content=content,
                sequence=sequence,
                meta_data=json.dumps(metadata) if metadata else None,
                event_type=event_type,
                event_data=json.dumps(event_data) if event_data else None,
                created_at=now_utc()
            )
            
            # 更新对话的消息数量和活跃时间
            await execute(
                conn,
                """
                UPDATE conversations
                SET message_count = $1,
                    last_active_at = $2,
                    updated_at = $2
                WHERE id = $3
                """,
                sequence,
                now_utc(),
                conversation_id_int
            )
            
            return message


async def async_get_message_by_id(message_id: int) -> Optional[MessageEventDataResult]:
    """根据 message_id 获取一条消息的 id 与 event_data（仅此两列），用于 resume 重复点击校验等。防御 DB 多列用 row_to_struct_safe。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        row = await fetch_one(
            conn,
            "SELECT id, event_data FROM conversation_messages WHERE id = $1",
            message_id
        )
        return _row_to_message_event_data(row)


async def async_update_message_event_data_partial(
    message_id: int,
    partial_data: Dict[str, Any]
) -> bool:
    """部分更新消息的 event_data（异步版本）"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        # 获取现有的 event_data
        message = await fetch_one(
            conn,
            "SELECT event_data FROM conversation_messages WHERE id = $1",
            message_id
        )
        
        if message:
            # 解析现有的 event_data
            existing_data = {}
            if message['event_data']:
                try:
                    existing_data = json.loads(message['event_data']) if isinstance(message['event_data'], str) else message['event_data']
                except:
                    existing_data = {}
            
            # 合并新数据
            merged_data = {**existing_data, **partial_data}
            
            await execute(
                conn,
                "UPDATE conversation_messages SET event_data = $1 WHERE id = $2",
                json.dumps(merged_data),
                message_id
            )
            return True
        return False


async def async_create_conversation_run(
    conversation_id: Union[int, str],
    thread_id: str,
    run_id: str,
    user_id: str,
    agent_type: Optional[str] = None,
    run_type: Optional[str] = None,
    user_option: Optional[Dict[str, Any]] = None,
    user_input: Optional[str] = None,
    user_input_files: Optional[Dict[str, Any]] = None,
    status: str = "running",
    conversation_uuid: Optional[str] = None,
    billing_status: Optional[str] = None,
    additional_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """创建对话运行记录（异步版本）。run_type 区分二级动作：main, resume, regenerate_keyframes, regenerate_videos, regenerate_characters。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        # 如果没有提供 conversation_uuid，从conversation获取
        if not conversation_uuid:
            conversation_id_int = int(conversation_id) if isinstance(conversation_id, str) else conversation_id
            conversation = await fetch_one(
                conn,
                "SELECT uuid FROM conversations WHERE id = $1",
                conversation_id_int
            )
            if conversation:
                conversation_uuid = conversation['uuid']
        
        row = {
            "uuid": str(uuid_lib.uuid4()),
            "conversation_id": str(conversation_id),
            "conversation_uuid": conversation_uuid,
            "thread_id": thread_id,
            "run_id": run_id,
            "user_id": user_id,
            "agent_type": agent_type,
            "user_option": json.dumps(user_option) if user_option else None,
            "user_input": user_input,
            "user_input_files": json.dumps(user_input_files) if user_input_files else None,
            "status": status,
            "created_at": now_utc(),
            "updated_at": now_utc(),
        }
        allowed = await _get_conversation_run_columns(conn)
        if run_type is not None and "run_type" in allowed:
            row["run_type"] = run_type
        if billing_status is not None and "billing_status" in allowed:
            row["billing_status"] = billing_status
        if additional_data is not None and "additional_data" in allowed:
            row["additional_data"] = json.dumps(additional_data)
        return await insert_and_return(conn, "conversation_runs", **row)


async def async_get_conversation_run_by_run_id(run_id: str) -> Optional[ConversationRunResult]:
    """根据run_id获取对话运行记录（异步版本）- 返回 msgspec.Struct"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        row = await fetch_one(
            conn,
            "SELECT * FROM conversation_runs WHERE run_id = $1",
            run_id
        )
        return _row_to_conversation_run(row)


async def async_get_task_status_cached(run_id: str) -> Optional[Dict[str, Any]]:
    """
    获取任务状态：先 Redis（缓存），无则查 DB（conversation_runs）。
    返回与 Redis 状态同构的 dict（run_id, status, created_at, completed_at, error_message 等），
    供所有需要「带 DB 回源」的调用方统一使用。

    Cache-aside write-back：Redis miss 时从 DB 读取并写回 Redis，
    防止 Redis 更新失败后长时间不一致。
    """
    from ..services.redis.connection import get_redis_stream_service
    redis_service = await get_redis_stream_service()
    task_status = await redis_service.get_task_status(run_id)
    if task_status:
        return task_status
    run = await async_get_conversation_run_by_run_id(run_id)
    if not run:
        return None
    db_status = {
        "run_id": run_id,
        "status": run.status,
        "created_at": utc_isoformat(run.created_at) or "",
        "completed_at": utc_isoformat(run.completed_at) or "",
        "error_message": run.error_message or "",
    }
    try:
        await redis_service.update_task_status(run_id, run.status, **{
            k: v for k, v in db_status.items() if k not in ("run_id", "status")
        })
    except Exception:
        logger.warning(f"cache write-back failed for run {run_id}, will retry on next read")
    return db_status


async def async_get_conversation_runs(conversation_id: int) -> List[ConversationRunResult]:
    """获取对话的所有运行记录（异步版本）- 返回 msgspec.Struct 列表"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        rows = await fetch_all(
            conn,
            """
            SELECT * FROM conversation_runs
            WHERE conversation_id = $1
            ORDER BY created_at DESC
            """,
            str(conversation_id)
        )
        return [_row_to_conversation_run(row) for row in rows if row]


async def async_get_runs_by_status(statuses: List[str]) -> List[Dict[str, Any]]:
    """根据状态获取运行记录（异步版本）"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        return await fetch_all(
            conn,
            f"""
            SELECT * FROM conversation_runs
            WHERE status = ANY($1::text[])
            ORDER BY created_at DESC
            """,
            statuses
        )


async def async_get_runs_by_thread_id_and_status(
    thread_id: str,
    statuses: List[str]
) -> List[Dict[str, Any]]:
    """根据thread_id和状态获取运行记录（异步版本）"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        return await fetch_all(
            conn,
            """
            SELECT * FROM conversation_runs
            WHERE thread_id = $1 AND status = ANY($2::text[])
            ORDER BY created_at DESC
            """,
            thread_id,
            statuses
        )


async def async_update_conversation_run_status(
    run_id: str,
    status: str,
    error_message: Optional[str] = None,
    completed_at: Optional[datetime] = None
) -> bool:
    """更新对话运行状态（异步版本）"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        # 构建更新字段
        update_fields = ["status = $1", "updated_at = $2"]
        params = [status, now_utc()]
        param_idx = 3
        
        if status in ["completed", "failed", "cancelled", "interrupted"]:
            update_fields.append(f"completed_at = ${param_idx}")
            params.append(completed_at or now_utc())
            param_idx += 1
        
        if error_message:
            update_fields.append(f"error_message = ${param_idx}")
            params.append(error_message)
            param_idx += 1
        
        params.append(run_id)
        
        result = await execute(
            conn,
            f"""
            UPDATE conversation_runs
            SET {', '.join(update_fields)}
            WHERE run_id = ${param_idx}
            """,
            *params
        )
        return result == "UPDATE 1"


async def _get_conversation_run_columns(conn) -> set:
    """查询 conversation_runs 实际存在的列，用于只更新存在的列（未跑迁移的列不写，与 row_to_struct_safe 读路径一致）。"""
    row = await fetch_one(
        conn,
        """
        SELECT array_agg(column_name::text) AS cols
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'conversation_runs'
        """
    )
    if not row or not row.get("cols"):
        return set()
    return set(row["cols"])


async def async_get_conversation_runs_by_billing_status(billing_status: str) -> List[Tuple[str, str]]:
    """按 billing_status 查 conversation_run 列表。SELECT * 后 Python 里用 r.get('billing_status') 过滤，列不存在时自然为 []。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        rows = await fetch_all(
            conn,
            """
            SELECT * FROM conversation_runs
            WHERE user_id IS NOT NULL AND user_id != ''
            ORDER BY updated_at ASC
            """
        )
    return [(r["run_id"], r["user_id"]) for r in (rows or []) if r.get("billing_status") == billing_status]


async def async_get_conversation_runs_by_langsmith_status(langsmith_status: str) -> List[Tuple[str, str]]:
    """按 langsmith_status 查 conversation_run 列表。列不存在时自然为 []（写路径防御一致）。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        rows = await fetch_all(
            conn,
            """
            SELECT * FROM conversation_runs
            WHERE user_id IS NOT NULL AND user_id != ''
            ORDER BY updated_at ASC
            """
        )
    return [(r["run_id"], r["user_id"]) for r in (rows or []) if r.get("langsmith_status") == langsmith_status]


async def async_create_conversation_run_for_regenerate(
    thread_id: str,
    run_id: str,
    user_id: str,
    run_type: str,
) -> bool:
    """
    Regenerate 开始时插入 conversation_run（status=running, billing_status=NULL）。
    仅写存在的列（走 async_create_conversation_run，含 _get_conversation_run_columns 防御）。
    返回是否插入成功。
    """
    if not run_id or not thread_id:
        return False
    conversation = await async_get_conversation_by_thread_id(thread_id)
    if not conversation or conversation.user_id != user_id:
        return False
    try:
        await async_create_conversation_run(
            conversation_id=conversation.id,
            thread_id=thread_id,
            run_id=run_id,
            user_id=user_id,
            agent_type="video",
            run_type=run_type,
            status="running",
            conversation_uuid=conversation.uuid,
            billing_status=None,
            additional_data=None,
        )
        return True
    except Exception:
        return False


async def async_update_conversation_run_after_regenerate(run_id: str, status: str) -> bool:
    """
    Regenerate 终态时更新 conversation_run：status + billing_status=pending。
    内部调用 async_update_conversation_run_status 与 async_set_conversation_run_billing_pending_if_not_completed，
    后者有 _get_conversation_run_columns 防御。
    """
    if not run_id:
        return False
    try:
        from datetime import datetime
        await async_update_conversation_run_status(
            run_id,
            status,
            completed_at=datetime.utcnow(),
        )
        await async_set_conversation_run_billing_pending_if_not_completed(run_id)
        return True
    except Exception:
        return False


async def async_set_conversation_run_billing_pending_if_not_completed(run_id: str) -> bool:
    """仅当该 run 的 billing_status 不是 completed 时设为 pending。表无 billing_status 列时直接返回 False。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        allowed = await _get_conversation_run_columns(conn)
        if "billing_status" not in allowed:
            return False
        result = await execute(
            conn,
            """
            UPDATE conversation_runs
            SET billing_status = $1, updated_at = $2
            WHERE run_id = $3 AND (billing_status IS NULL OR billing_status != $4)
            """,
            "pending",
            now_utc(),
            run_id,
            "completed",
        )
        return result == "UPDATE 1"


async def async_update_conversation_run_cost(run_id: str, cost: float) -> bool:
    """只更新 conversation_run.cost，不触碰 billing_status，避免在计费流程中间意外改变状态。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        allowed = await _get_conversation_run_columns(conn)
        if "cost" not in allowed:
            return False
        result = await execute(
            conn,
            "UPDATE conversation_runs SET cost = $1, updated_at = $2 WHERE run_id = $3",
            cost, now_utc(), run_id,
        )
        return result == "UPDATE 1"


async def async_update_conversation_run_billing(
    run_id: str,
    billing_status: str,
    cost_credits: Optional[float] = None,
    *,
    langsmith_status: Optional[str] = None,
    langsmith_cost: Optional[float] = None,
    cost: Optional[float] = None,
    cost_calculated: Optional[bool] = None,
    credits_deducted: Optional[bool] = None,
    credits_amount: Optional[int] = None,
) -> bool:
    """更新 conversation_run 的计费字段（cost_credits 兼容 dev + 6 字段）。
    写路径防御：仅写 _get_conversation_run_columns(conn) 存在的列，未跑迁移的列不写不崩（与读路径 row_to_struct_safe 一致）。"""
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        allowed = await _get_conversation_run_columns(conn)
        if "billing_status" not in allowed:
            return False
        update_fields = ["billing_status = $1", "updated_at = $2"]
        params: list = [billing_status, now_utc()]
        param_idx = 3
        if cost_credits is not None and "cost_credits" in allowed:
            update_fields.append(f"cost_credits = ${param_idx}")
            params.append(cost_credits)
            param_idx += 1
        if langsmith_status is not None and "langsmith_status" in allowed:
            update_fields.append(f"langsmith_status = ${param_idx}")
            params.append(langsmith_status)
            param_idx += 1
        if langsmith_cost is not None and "langsmith_cost" in allowed:
            update_fields.append(f"langsmith_cost = ${param_idx}")
            params.append(langsmith_cost)
            param_idx += 1
        if cost is not None and "cost" in allowed:
            update_fields.append(f"cost = ${param_idx}")
            params.append(cost)
            param_idx += 1
        if cost_calculated is not None and "cost_calculated" in allowed:
            update_fields.append(f"cost_calculated = ${param_idx}")
            params.append(cost_calculated)
            param_idx += 1
        if credits_deducted is not None and "credits_deducted" in allowed:
            update_fields.append(f"credits_deducted = ${param_idx}")
            params.append(credits_deducted)
            param_idx += 1
        if credits_amount is not None and "credits_amount" in allowed:
            update_fields.append(f"credits_amount = ${param_idx}")
            params.append(credits_amount)
            param_idx += 1
        params.append(run_id)
        result = await execute(
            conn,
            f"""
            UPDATE conversation_runs
            SET {', '.join(update_fields)}
            WHERE run_id = ${param_idx}
            """,
            *params
        )
        return result == "UPDATE 1"


# ========== 同步函数（已废弃，不再支持）==========
# 所有同步函数已移除，请使用async版本
