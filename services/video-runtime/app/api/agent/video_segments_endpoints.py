"""Video segments and lipsync API endpoints"""

from fastapi import APIRouter, Depends, Security, Query
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime

from ...services.auth_service import auth_service
from ...schemas import ResponseModel
from ...crud.video.video_segment import (
    get_video_segment_by_uuid,
    get_video_segment_versions_by_segment_ids,
)
from ...exceptions import BusinessException, BusinessExceptionCode
from ...utils.asyncpg_utils import utc_isoformat

router = APIRouter(prefix="/video-segments", tags=["video-segments"])


# Response Models
class VideoSegmentVersionResponse(BaseModel):
    """视频片段版本响应模型"""
    uuid: str
    version_number: int
    segment_number: int
    video_url: str
    lipsync_video_url: Optional[str] = None  # 新增：lipsync 后的视频 URL
    success: bool
    error_msg: Optional[str] = None
    duration: Optional[float] = None
    fps: Optional[int] = None
    lipsync_version_id: Optional[str] = None
    music_generation_version_id: Optional[str] = None
    created_at: str
    updated_at: str


class VideoSegmentResponse(BaseModel):
    """视频片段响应模型"""
    uuid: str
    segment_number: int
    video_generation_ids: List[str]
    narration_ids: List[str]
    keyframe_ids: List[str]
    scene_ids: List[str]
    storyboard_detail_ids: List[str]
    music_generation_id: Optional[str] = None  # 支持 video-driven 模式（可能没有音乐）
    lipsync_id: Optional[str] = None
    current_version_index: int
    versions: List[VideoSegmentVersionResponse]
    created_at: str
    updated_at: str


class LipsyncVersionResponse(BaseModel):
    """Lipsync 版本响应模型"""
    uuid: str
    version_number: int
    segment_number: int
    video_url: str
    audio_url: str
    original_video_url: str
    provider: str
    model: str
    success: bool
    error_msg: Optional[str] = None
    duration: Optional[float] = None
    created_at: str
    updated_at: str


class LipsyncGenerationResponse(BaseModel):
    """Lipsync 生成响应模型"""
    uuid: str
    video_segment_id: str
    music_generation_id: str
    segment_number: int
    current_version_index: int
    versions: List[LipsyncVersionResponse]
    created_at: str
    updated_at: str


class VideoSegmentListResponse(BaseModel):
    """视频片段列表响应模型"""
    segments: List[VideoSegmentResponse]
    total: int


@router.get("/{uuid}", response_model=ResponseModel[VideoSegmentResponse])
async def get_video_segment(
    uuid: str,
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据UUID获取视频片段数据"""
    try:
        # 获取视频片段
        video_segment = await get_video_segment_by_uuid(uuid)
        if not video_segment:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "Video segment not found"
            )
        
        # 检查权限
        if video_segment.user_id != user_id:
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                "No permission to access this video segment"
            )
        
        # 获取版本信息
        versions = await get_video_segment_versions(uuid)
        version_responses = [
            VideoSegmentVersionResponse(
                uuid=version.uuid,
                version_number=version.version_number,
                segment_number=version.segment_number,
                video_url=version.video_url,
                success=version.success,
                error_msg=version.error_msg,
                duration=version.duration,
                fps=version.fps,
                lipsync_version_id=version.lipsync_version_id,
                music_generation_version_id=version.music_generation_version_id,
                created_at=utc_isoformat(version.created_at),
                updated_at=utc_isoformat(version.updated_at)
            )
            for version in sorted(versions, key=lambda v: v.version_number)
        ]
        
        # 构建响应数据
        response_data = VideoSegmentResponse(
            uuid=video_segment.uuid,
            segment_number=video_segment.segment_number,
            video_generation_ids=video_segment.video_generation_ids,
            narration_ids=video_segment.narration_ids,
            keyframe_ids=video_segment.keyframe_ids,
            scene_ids=video_segment.scene_ids,
            storyboard_detail_ids=video_segment.storyboard_detail_ids,
            music_generation_id=video_segment.music_generation_id,
            lipsync_id=video_segment.lipsync_id,
            current_version_index=video_segment.current_version_index,
            versions=version_responses,
            created_at=utc_isoformat(video_segment.created_at),
            updated_at=utc_isoformat(video_segment.updated_at)
        )
        
        return ResponseModel.success(data=response_data)
        
    except BusinessException:
        raise
    except Exception as e:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"Failed to get video segment: {str(e)}"
        )


@router.get("/by-run-id/{run_id}", response_model=ResponseModel[VideoSegmentListResponse])
async def get_video_segments_by_run_id(
    run_id: str,
    limit: int = Query(20, ge=1, le=1000, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据 run_id 获取视频片段列表（支持分页）"""
    try:
        from ...schemas.video.video_segment import VideoSegmentDB

        # 查询视频片段（使用asyncpg CRUD）✅
        from ...crud.video.video_segment import get_video_segments_with_data_by_run_id
        segments = await get_video_segments_with_data_by_run_id(run_id)
        
        # 应用分页
        total = len(segments)
        segments = segments[offset:offset + limit]
        
        if not segments:
            return ResponseModel.success(data=VideoSegmentListResponse(segments=[], total=total))
        
        # ✅ 优化：批量获取所有版本信息，避免N+1查询（使用asyncpg CRUD）
        from ...crud.video.video_segment import get_video_segment_versions_by_segment_ids
        from ...crud.video.video_other import get_lipsync_versions_by_ids
        
        # 第1步：批量获取所有segments的versions
        segment_ids = [segment.uuid for segment in segments]
        versions_by_segment = await get_video_segment_versions_by_segment_ids(segment_ids)
        
        # 第2步：收集所有需要查询的lipsync_version_ids
        all_lipsync_version_ids = []
        for versions in versions_by_segment.values():
            for version in versions:
                if version.lipsync_version_id:
                    all_lipsync_version_ids.append(version.lipsync_version_id)
        
        # 第3步：批量获取所有lipsync versions
        lipsync_versions_dict = {}
        if all_lipsync_version_ids:
            lipsync_versions = await get_lipsync_versions_by_ids(all_lipsync_version_ids)
            lipsync_versions_dict = {lv.uuid: lv for lv in lipsync_versions}
        
        # 第4步：构建响应（纯内存操作，无数据库查询）
        segment_responses = []
        for segment in segments:
            versions = versions_by_segment.get(segment.uuid, [])
            version_responses = []
            
            for version in sorted(versions, key=lambda v: v.version_number):
                # 🎭 从字典中获取 lipsync 的视频 URL（内存查找）
                lipsync_video_url = None
                if version.lipsync_version_id:
                    lipsync_version = lipsync_versions_dict.get(version.lipsync_version_id)
                    if lipsync_version and lipsync_version.success and lipsync_version.video_url:
                        lipsync_video_url = lipsync_version.video_url
                
                version_responses.append(VideoSegmentVersionResponse(
                    uuid=version.uuid,
                    version_number=version.version_number,
                    segment_number=version.segment_number,
                    video_url=version.video_url,  # 原始视频 URL
                    lipsync_video_url=lipsync_video_url,  # lipsync 视频 URL（如果有）
                    success=version.success,
                    error_msg=version.error_msg,
                    duration=version.duration,
                    fps=version.fps,
                    lipsync_version_id=version.lipsync_version_id,
                    music_generation_version_id=version.music_generation_version_id,
                    created_at=utc_isoformat(version.created_at),
                    updated_at=utc_isoformat(version.updated_at)
                ))
            
            segment_responses.append(VideoSegmentResponse(
                uuid=segment.uuid,
                segment_number=segment.segment_number,
                video_generation_ids=segment.video_generation_ids,
                narration_ids=segment.narration_ids,
                keyframe_ids=segment.keyframe_ids,
                scene_ids=segment.scene_ids,
                storyboard_detail_ids=segment.storyboard_detail_ids,
                music_generation_id=segment.music_generation_id,
                lipsync_id=segment.lipsync_id,
                current_version_index=segment.current_version_index,
                versions=version_responses,
                created_at=utc_isoformat(segment.created_at),
                updated_at=utc_isoformat(segment.updated_at)
            ))
        
        response_data = VideoSegmentListResponse(
            segments=segment_responses,
            total=total
        )
        
        return ResponseModel.success(data=response_data)
        
    except BusinessException:
        raise
    except Exception as e:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"Failed to get video segments: {str(e)}"
        )


@router.get("/lipsync/{lipsync_uuid}", response_model=ResponseModel[LipsyncGenerationResponse])
async def get_lipsync_generation(
    lipsync_uuid: str,
    conversation_id: Optional[str] = Query(None, description="Conversation ID for additional permission check"),
    user_id: str = Security(auth_service.get_current_user_or_service_user)
):
    """根据UUID获取Lipsync生成数据"""
    try:
        from ...schemas.video.video_other import VideoLipsyncGenerationDB, VideoLipsyncGenerationVersionDB
        
        # 获取lipsync生成记录（使用asyncpg CRUD）✅
        from ...crud.video.video_other import get_lipsync_generation_by_uuid
        lipsync_generation = await get_lipsync_generation_by_uuid(lipsync_uuid, user_id)
        
        if not lipsync_generation:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                "Lipsync generation not found"
            )
        
        # 检查权限
        if lipsync_generation.user_id != user_id:
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                "No permission to access this lipsync generation"
            )
        
        # 如果提供了 conversation_id，进行额外的权限检查
        if conversation_id and lipsync_generation.conversation_id != str(conversation_id):
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                "Lipsync generation does not belong to the specified conversation"
            )
        
        # 获取版本信息（使用asyncpg CRUD）✅
        from ...crud.video.video_other import get_lipsync_versions_by_lipsync_id
        versions = await get_lipsync_versions_by_lipsync_id(lipsync_uuid, user_id)
        
        version_responses = [
            LipsyncVersionResponse(
                uuid=version.uuid,
                version_number=version.version_number,
                segment_number=version.segment_number,
                video_url=version.video_url,
                audio_url=version.audio_url,
                original_video_url=version.original_video_url,
                provider=version.provider,
                model=version.model,
                success=version.success,
                error_msg=version.error_msg,
                duration=version.duration,
                created_at=utc_isoformat(version.created_at),
                updated_at=utc_isoformat(version.updated_at)
            )
            for version in sorted(versions, key=lambda v: v.version_number)
        ]
        
        # 构建响应数据
        response_data = LipsyncGenerationResponse(
            uuid=lipsync_generation.uuid,
            video_segment_id=lipsync_generation.video_segment_id,
            music_generation_id=lipsync_generation.music_generation_id,
            segment_number=lipsync_generation.segment_number,
            current_version_index=lipsync_generation.current_version_index,
            versions=version_responses,
            created_at=utc_isoformat(lipsync_generation.created_at),
            updated_at=utc_isoformat(lipsync_generation.updated_at)
        )
        
        return ResponseModel.success(data=response_data)
        
    except BusinessException:
        raise
    except Exception as e:
        raise BusinessException(
            BusinessExceptionCode.BUSINESS_ERROR,
            f"Failed to get lipsync generation: {str(e)}"
        )
