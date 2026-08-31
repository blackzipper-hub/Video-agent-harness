"""ContinuePipelineTool — 通过门控继续下一阶段。"""
import json
import logging
from typing import Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

VALID_GATES = (
    "after_music",
    "after_outline",
    "after_character",
    "after_storyboard_detail",
    "after_keyframe_reflection",
    "after_shots",
)


class ContinuePipelineInput(BaseModel):
    """continue_pipeline 的输入参数。"""
    gate: str = Field(
        ...,
        description=(
            "要通过的门控点: "
            "after_music | after_outline | after_character | "
            "after_storyboard_detail | after_keyframe_reflection | after_shots"
        ),
    )


class ContinuePipelineTool(BaseTool):
    name: str = "continue_pipeline"
    description: str = (
        "通过门控继续视频生成的下一阶段（主管线批量推进）。"
        "gate 须与 get_project_status 快照中的 pending_gate 对齐；"
        "若为 failed_*：failed_keyframe→after_keyframe_reflection（重跑关键帧）；"
        "failed_video→after_shots（重跑镜头视频，不是进成片）。"
        "gate: after_music (音乐确认后) | "
        "after_outline (大纲确认后) | "
        "after_character (角色确认后) | "
        "after_storyboard_detail (分镜确认后；reference_t2v 则直接生成视频) | "
        "after_keyframe_reflection (关键帧确认后，批量生成视频) | "
        "after_shots (视频就绪后进成片；若当前是 failed_video 则重跑视频)"
    )
    args_schema: Type[BaseModel] = ContinuePipelineInput

    run_id: str = ""
    user_id: str = ""
    thread_id: str = ""

    async def _arun(self, gate: str) -> str:
        logger.info(
            "🔄 continue_pipeline called: gate=%s, thread_id=%s, run_id=%s",
            gate, self.thread_id, self.run_id,
        )
        if gate not in VALID_GATES:
            return f"无效的 gate: {gate}。可选: {', '.join(VALID_GATES)}"

        try:
            interrupt_msgid, interrupt_run_id, pending_step = await _find_latest_interrupt(
                self.thread_id
            )
            logger.info(
                "🔍 _find_latest_interrupt result: msgid=%s, run_id=%s, step=%s (thread=%s)",
                interrupt_msgid, interrupt_run_id, pending_step, self.thread_id,
            )
            if not interrupt_msgid:
                return f"thread_id={self.thread_id} 下未找到待恢复的 interrupt 消息"

            from .....services.task_enqueue_service import prepare_resume_task
            from .....services.queue import create_task_queue
            from .....services.redis.connection import get_redis_stream_service

            resume_payload = {
                "run_id": interrupt_run_id,
                "interrupt_msgid": interrupt_msgid,
            }
            logger.info("📦 resume_payload: %s", resume_payload)

            task_data = await prepare_resume_task(resume_payload, self.user_id)
            logger.info(
                "📋 prepare_resume_task done: new_run_id=%s, thread_id=%s, resume_data=%s",
                task_data["run_id"], task_data["thread_id"], task_data.get("resume_data"),
            )

            sqs_service = create_task_queue()
            await sqs_service.add_task_to_queue(task_data)

            redis_service = await get_redis_stream_service()
            await redis_service.add_task_index(self.thread_id, task_data["run_id"])

            new_run_id = task_data["run_id"]
            user_message = _resume_user_message(pending_step or gate)
            logger.info("✅ continue_pipeline: SQS task submitted, new_run_id=%s", new_run_id)
            return json.dumps({
                "status": "resume_submitted",
                "new_run_id": new_run_id,
                "gate": gate,
                "pending_step": pending_step,
                "message": (
                    f"{user_message}（原 run_id={interrupt_run_id}，新 run_id={new_run_id}）"
                ),
            }, ensure_ascii=False)

        except Exception as e:
            logger.error(f"continue_pipeline error: {e}", exc_info=True)
            return f"恢复管线失败: {str(e)}"

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("Use async version")


def _interrupt_step(event_data: dict) -> str:
    interrupt_data = event_data.get("interrupt_data")
    if isinstance(interrupt_data, dict):
        return str(interrupt_data.get("step") or "")
    return ""


def _resume_user_message(pending_step: str) -> str:
    """按真实 interrupt step 生成对用户可见的说明（勿被 LLM 传入的 gate 误导）。"""
    step = (pending_step or "").strip()
    messages = {
        "failed_video": "已提交重试视频生成，正在重新生成镜头视频",
        "failed_keyframe": "已提交重试关键帧生成，正在重新生成关键帧",
        "failed_music": "已提交重试音乐生成",
        "failed_outline": "已提交重试大纲生成",
        "failed_character": "已提交重试角色设计",
        "after_music": "已从音乐确认继续",
        "after_outline": "已从大纲确认继续，开始角色设计",
        "after_character": "已从角色确认继续，开始场景与分镜",
        "after_storyboard_detail": "已从分镜确认继续，开始下一阶段生成",
        "after_keyframe_reflection": "已从关键帧确认继续，开始批量生成视频",
        "after_shots": "已从镜头视频确认继续，开始合并片段与成片",
    }
    return messages.get(step, f"已从 {step or '门控'} 恢复管线")


async def _find_latest_interrupt(thread_id: str) -> tuple:
    """按 thread_id 查找最新的未 continued 的 interrupt 消息。
    返回 (message_id, run_id, pending_step) 或 (None, None, None)。
    run_id 来源优先级：event_data.run_id > msg.run_id > conversation_runs 最新 interrupted run。
    """
    from .....crud.conversation import async_get_conversation_by_thread_id, async_get_conversation_messages

    conversation = await async_get_conversation_by_thread_id(thread_id)
    if not conversation:
        logger.warning("_find_latest_interrupt: no conversation for thread_id=%s", thread_id)
        return None, None, None

    messages = await async_get_conversation_messages(conversation.id)
    interrupt_count = 0
    last_continued_msg = None
    for msg in reversed(messages):
        if msg.get("event_type") != "interrupt":
            continue
        interrupt_count += 1
        event_data = msg.get("event_data")
        if isinstance(event_data, str):
            try:
                event_data = json.loads(event_data)
            except Exception:
                continue
        if not event_data:
            continue
        if event_data.get("continued"):
            if last_continued_msg is None:
                last_continued_msg = (msg, event_data)
            logger.debug(
                "_find_latest_interrupt: skip continued msg id=%s, step=%s",
                msg.get("id"), _interrupt_step(event_data) or "?",
            )
            continue

        msg_run_id = event_data.get("run_id", "") or msg.get("run_id", "")
        if not msg_run_id:
            msg_run_id = await _find_interrupted_run_id(thread_id)
        pending_step = _interrupt_step(event_data)
        logger.info(
            "_find_latest_interrupt: found! msg_id=%s, run_id=%s, step=%s (scanned %d interrupt msgs)",
            msg.get("id"), msg_run_id, pending_step or "?",
            interrupt_count,
        )
        return msg.get("id"), msg_run_id, pending_step

    # 安全网：所有 interrupt 都被标记为 continued，但 pipeline 仍然 interrupted
    # 说明之前的 resume 失败了，重置 continued 标志并允许重新 resume
    if last_continued_msg is not None:
        still_interrupted_run_id = await _find_interrupted_run_id(thread_id)
        if still_interrupted_run_id:
            _msg, _ed = last_continued_msg
            _msg_id = _msg.get("id")
            logger.warning(
                "_find_latest_interrupt: all interrupts continued but pipeline still interrupted! "
                "Resetting continued flag on msg_id=%s (run still interrupted: %s)",
                _msg_id, still_interrupted_run_id,
            )
            from .....crud.conversation import async_update_message_event_data_partial
            await async_update_message_event_data_partial(
                int(_msg_id), {"continued": False, "continued_at": None}
            )
            return _msg_id, still_interrupted_run_id, _interrupt_step(_ed)

    logger.warning(
        "_find_latest_interrupt: no pending interrupt found (scanned %d interrupt msgs, thread=%s)",
        interrupt_count, thread_id,
    )
    return None, None, None


async def _find_interrupted_run_id(thread_id: str) -> str:
    """从 conversation_runs 找最新的 interrupted run_id。"""
    from .....models.database import get_asyncpg_pool
    pool = get_asyncpg_pool()
    async with pool.acquire() as conn:
        rid = await conn.fetchval(
            "SELECT run_id FROM conversation_runs "
            "WHERE thread_id = $1 AND status = 'interrupted' "
            "ORDER BY created_at DESC LIMIT 1",
            thread_id,
        )
    return rid or ""
