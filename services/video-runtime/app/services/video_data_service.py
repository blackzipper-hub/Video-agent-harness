"""Video data service - 统一的视频数据获取服务"""

# AsyncSession 已移除（已迁移到asyncpg）
from typing import Optional, List, Dict, Any
import logging
from ..crud.video.video_other import (
    get_video_analysis_by_uuid,
    get_video_assembly_by_uuid
)
from ..crud.video.video_story import (
    get_video_story_outline_by_uuid,
    get_scene_by_uuid,
    get_scenes_by_uuids,
    get_storyboard_detail_by_uuid,
    get_detailed_shots_by_storyboard_id,
    get_detailed_shots_by_uuids,
    get_chapters_by_story_outline_id
)
from ..crud.video.video_character import (
    get_character_by_uuid,
    get_characters_by_uuids
)
from ..crud.video.video_keyframe import (
    get_keyframes_by_uuids,
    get_keyframe_versions_by_keyframe_ids
)
from ..crud.video.video_generation import (
    get_video_generation_versions_by_uuids,
    get_video_generation_versions_by_video_generation_uuids,
    get_video_generations_by_uuids,
    get_video_generation_versions_by_video_generation_ids
)
from ..crud.video.video_audio import (
    get_music_generation_by_uuid,
    get_video_audio_transcription_by_uuid,
    get_video_audio_segments_by_transcription_uuid
)
from ..exceptions import BusinessException, BusinessExceptionCode

logger = logging.getLogger(__name__)


class VideoDataService:
    """统一的视频数据获取服务"""
    
    def check_permission(self, resource, user_id: str, resource_name: str = "资源"):
        """检查用户权限"""
        if not resource:
            raise BusinessException(
                BusinessExceptionCode.RESOURCE_NOT_FOUND,
                f"{resource_name}不存在"
            )
        
        # 检查权限（支持字典和对象两种格式）
        resource_user_id = resource.get('user_id') if isinstance(resource, dict) else getattr(resource, 'user_id', None)
        if resource_user_id and resource_user_id != user_id:
            raise BusinessException(
                BusinessExceptionCode.PERMISSION_DENIED,
                f"无权访问该{resource_name}"
            )
    
    async def get_video_analysis(self, uuid: str, user_id: str):
        """获取视频分析数据"""
        analysis = await get_video_analysis_by_uuid(uuid)
        self.check_permission(analysis, user_id, "视频分析")
        return analysis
    
    async def get_story_outline(self, uuid: str, user_id: str):
        """获取故事大纲数据"""
        outline = await get_video_story_outline_by_uuid(uuid)
        self.check_permission(outline, user_id, "故事大纲")
        return outline
    
    async def get_story_outline_with_structure(self, uuid: str, user_id: str):
        """获取故事大纲数据及其章节结构"""
        outline = await self.get_story_outline(uuid, user_id)
        
        from ..utils.chapter_order import sort_chapters_by_order

        # 获取章节信息（API 返回 0-based order，兼容历史 1-based 存库）
        outline_uuid = outline['uuid'] if isinstance(outline, dict) else outline.uuid
        chapters = sort_chapters_by_order(
            await get_chapters_by_story_outline_id(outline_uuid)
        )

        structure = []
        for i, chapter in enumerate(chapters):
            structure.append({
                "id": chapter.uuid,
                "title": chapter.title,
                "description": chapter.description,
                "duration": chapter.duration,
                "order": i,
                "audio_segment_ids": getattr(chapter, "audio_segment_ids", None)
            })
        
        return outline, structure
    
    async def get_characters(self, uuids: List[str], user_id: str):
        """获取角色数据"""
        if not uuids:
            return []
        
        characters = await get_characters_by_uuids(uuids)
        
        # 检查权限
        for character in characters:
            self.check_permission(character, user_id, "角色")
        
        return characters
    
    async def get_scenes(self, uuids: List[str], user_id: str):
        """获取场景数据"""
        if not uuids:
            return []
        
        scenes = await get_scenes_by_uuids(uuids)
        
        # 检查权限
        for scene in scenes:
            self.check_permission(scene, user_id, "场景")
        
        return scenes
    
    async def get_detailed_shots(self, uuids: List[str], user_id: str):
        """获取详细镜头数据"""
        if not uuids:
            return []
        
        shots = await get_detailed_shots_by_uuids(uuids)
        
        # 检查权限
        for shot in shots:
            self.check_permission(shot, user_id, "详细镜头")
        
        return shots
    
    async def get_keyframes(self, uuids: List[str], user_id: str):
        """获取关键帧数据"""
        if not uuids:
            return []
        
        keyframes = await get_keyframes_by_uuids(uuids)
        
        # 检查权限
        for keyframe in keyframes:
            self.check_permission(keyframe, user_id, "关键帧")
        
        return keyframes
    
    async def get_keyframes_with_versions(self, keyframe_uuids: List[str], user_id: str):
        """获取关键帧及其版本数据"""
        keyframes = await self.get_keyframes(keyframe_uuids, user_id)
        
        if not keyframes:
            return []
        
        # 获取版本数据
        keyframe_ids = [kf['uuid'] if isinstance(kf, dict) else kf.uuid for kf in keyframes]
        versions = await get_keyframe_versions_by_keyframe_ids(keyframe_ids)
        
        # 组织数据结构
        keyframe_versions_map = {}
        for version in versions:
            version_keyframe_id = version['keyframe_id'] if isinstance(version, dict) else version.keyframe_id
            if version_keyframe_id not in keyframe_versions_map:
                keyframe_versions_map[version_keyframe_id] = []
            keyframe_versions_map[version_keyframe_id].append(version)
        
        result = []
        for keyframe in keyframes:
            keyframe_uuid = keyframe['uuid'] if isinstance(keyframe, dict) else keyframe.uuid
            keyframe_versions = keyframe_versions_map.get(keyframe_uuid, [])
            result.append({
                "keyframe": keyframe,
                "versions": keyframe_versions
            })
        
        return result
    
    async def get_video_generations(self, uuids: List[str], user_id: str):
        """获取视频生成数据"""
        if not uuids:
            return []
        
        generations = await get_video_generations_by_uuids(uuids)
        
        # 检查权限
        for generation in generations:
            self.check_permission(generation, user_id, "视频生成")
        
        return generations
    
    async def get_video_generations_with_versions(self, generation_uuids: List[str], user_id: str):
        """获取视频生成及其版本数据"""
        generations = await self.get_video_generations(generation_uuids, user_id)
        
        if not generations:
            return []
        
        # 获取版本数据
        generation_ids = [gen['uuid'] if isinstance(gen, dict) else gen.uuid for gen in generations]
        versions = await get_video_generation_versions_by_video_generation_ids(generation_ids)
        
        # 组织数据结构
        generation_versions_map = {}
        for version in versions:
            version_gen_id = version['video_generation_id'] if isinstance(version, dict) else version.video_generation_id
            if version_gen_id not in generation_versions_map:
                generation_versions_map[version_gen_id] = []
            generation_versions_map[version_gen_id].append(version)
        
        result = []
        for generation in generations:
            generation_uuid = generation['uuid'] if isinstance(generation, dict) else generation.uuid
            generation_versions = generation_versions_map.get(generation_uuid, [])
            result.append({
                "generation": generation,
                "versions": generation_versions
            })
        
        return result
    
    async def get_music_generation(self, uuid: str, user_id: str):
        """获取音乐生成数据"""
        music = await get_music_generation_by_uuid(uuid)
        self.check_permission(music, user_id, "音乐生成")
        return music
    
    async def get_characters_info_by_uuids(self, character_uuids: List[str], user_id: str):
        """根据UUID列表获取角色基本信息（用于场景显示）"""
        if not character_uuids:
            return []
        
        characters = await get_characters_by_uuids(character_uuids)
        
        # 过滤权限
        allowed_characters = []
        for character in characters:
            try:
                self.check_permission(character, user_id, "角色")
                allowed_characters.append(character)
            except:
                # 跳过无权限的角色
                continue
        
        return allowed_characters
    
    async def get_video_assembly(self, uuid: str, user_id: str):
        """获取视频合成数据"""
        assembly = await get_video_assembly_by_uuid(uuid)
        self.check_permission(assembly, user_id, "视频合成")
        return assembly
    
    async def get_audio_transcription_with_segments(self, uuid: str, user_id: str):
        """获取音频转录及其片段数据"""
        transcription = await get_video_audio_transcription_by_uuid(uuid)
        self.check_permission(transcription, user_id, "音频转录")
        
        # 获取音频片段
        segments = await get_video_audio_segments_by_transcription_uuid(uuid)
        
        return transcription, segments


# 创建一个全局实例供使用
video_data_service = VideoDataService()


def get_video_data_service() -> VideoDataService:
    """VideoDataService 依赖注入工厂函数 - 返回服务实例（已迁移到asyncpg，不再需要db参数）"""
    return video_data_service