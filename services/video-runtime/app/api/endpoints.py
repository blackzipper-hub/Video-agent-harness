from fastapi import APIRouter
import logging


# 包含对话路由
from .agent.conversation_routes import router as conversation_router
# 包含Agent路由
from .agent.agent_router_endpoints import router as agent_router_api
# 包含视频分析路由
from .agent.video_analysis_endpoints import router as video_analysis_router
# 包含视频片段路由
from .agent.video_segments_endpoints import router as video_segments_router
# 上传音频 Smart Clip 推荐
from .agent.audio_smart_clip_endpoints import router as audio_smart_clip_router


logger = logging.getLogger(__name__)

router = APIRouter()
router.include_router(conversation_router)
router.include_router(agent_router_api)
router.include_router(video_analysis_router)  
router.include_router(video_segments_router)
router.include_router(audio_smart_clip_router)
