"""
导出素材服务 - 将视频创作过程中生成的所有资源打包成 ZIP 文件
"""
import asyncio
import os
import zipfile
import tempfile
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime
from collections import defaultdict
from PIL import Image
from io import BytesIO
import aiohttp
import aiofiles

from ..utils.temp_file_utils import download_media_to_temp, batch_download_media_to_temp
from ..utils.file_utils import MediaType

logger = logging.getLogger(__name__)


class DownloadService:
    """导出素材服务"""
    
    async def create_complete_resource_package(
        self,
        video_assembly_uuid: str
    ) -> str:
        """
        创建完整资源包（所有素材）
        
        Args:
            video_assembly_uuid: 视频合成UUID
            
        Returns:
            ZIP文件路径
        """
        try:
            logger.info(f"🎬 开始创建导出包: {video_assembly_uuid}")
            
            # 1. 查询数据库获取所有资源
            logger.info("📊 步骤1: 查询数据库...")
            resources = await self._fetch_all_resources(video_assembly_uuid)
            logger.info(f"✅ 步骤1完成: 获取到资源统计")
        except Exception as e:
            logger.error(f"❌ 创建导出包失败(步骤1): {e}", exc_info=True)
            raise
        
        try:
            # 2. 创建临时目录
            logger.info("📁 步骤2: 创建临时目录...")
            temp_dir = tempfile.mkdtemp()
            logger.info(f"✅ 步骤2完成: {temp_dir}")
        except Exception as e:
            logger.error(f"❌ 创建导出包失败(步骤2): {e}", exc_info=True)
            raise
        
        try:
            # 3. 批量下载所有文件
            downloaded_files = await self._download_all_files(resources, temp_dir)
            
            # 4. 创建 input.txt
            input_txt_path = await self._create_input_txt(resources, temp_dir)
            
            # 5. 生成 Image Sheets
            storyboard_sheet_paths = []
            element_sheet_paths = []
            
            # 从 keyframe_items 提取 URL（只用最新版本的首帧做 sheet）
            keyframe_items_list = resources.get('keyframe_items', [])
            # 按 shot_number 分组，取每组的最大版本号（只用首帧）
            if keyframe_items_list:
                keyframes_by_shot = defaultdict(list)
                for shot_num, version, frame_index, url in keyframe_items_list:
                    # 只用首帧（frame_index=0）生成 sheet
                    if frame_index == 0:
                        keyframes_by_shot[shot_num].append((version, url))
                # 每个 shot 取最新版本
                keyframe_urls = []
                for shot_num in sorted(keyframes_by_shot.keys()):
                    versions = keyframes_by_shot[shot_num]
                    if versions:
                        latest = max(versions, key=lambda x: x[0])
                        keyframe_urls.append(latest[1])
                
                if keyframe_urls:
                    storyboard_sheet_paths = await self.create_image_sheets(
                        keyframe_urls,
                        os.path.join(temp_dir, "gen-storyboards-sheet"),
                        max_images_per_sheet=100
                    )
            else:
                keyframe_urls = []
            
            # 从 character_items 提取 URL（只用最新版本做 sheet）
            character_items_list = resources.get('character_items', [])
            if character_items_list:
                # 按角色分组，取每组的最大版本号
                character_by_index = defaultdict(list)
                for char_idx, version, url in character_items_list:
                    character_by_index[char_idx].append((version, url))
                # 每个角色取最新版本
                character_urls_for_sheet = []
                for char_idx in sorted(character_by_index.keys()):
                    versions = character_by_index[char_idx]
                    latest = max(versions, key=lambda x: x[0])
                    character_urls_for_sheet.append(latest[1])
                
                if character_urls_for_sheet:
                    element_sheet_paths = await self.create_image_sheets(
                        character_urls_for_sheet,
                        os.path.join(temp_dir, "gen-visual-elements-sheet"),
                        max_images_per_sheet=100
                    )
            else:
                character_urls_for_sheet = []
            
            # 6. 收集所有文件并规范命名
            files_dict = {}
            
            # 输入文件
            if input_txt_path:
                files_dict["input.txt"] = input_txt_path
            
            # 输入图片
            for i, img_url in enumerate(resources.get('input_images', []), 1):
                if img_url in downloaded_files:
                    ext = self._get_file_extension(img_url)
                    files_dict[f"input-{i:03d}{ext}"] = downloaded_files[img_url]
            
            # 输入视频
            for i, video_url in enumerate(resources.get('input_videos', []), 1):
                if video_url in downloaded_files:
                    files_dict[f"input-{i:03d}.mp4"] = downloaded_files[video_url]
            
            # 输入音频
            for i, audio_url in enumerate(resources.get('input_audios', []), 1):
                if audio_url in downloaded_files:
                    files_dict[f"input-{i:03d}.mp3"] = downloaded_files[audio_url]
            
            # 最终视频
            if resources.get('final_video_url') and resources['final_video_url'] in downloaded_files:
                files_dict["output-final-v1.mp4"] = downloaded_files[resources['final_video_url']]
            
            if resources.get('final_no_subtitle_url') and resources['final_no_subtitle_url'] in downloaded_files:
                files_dict["output-final-nosubtitle-v1.mp4"] = downloaded_files[resources['final_no_subtitle_url']]
            
            # Image Sheets
            for i, sheet_path in enumerate(storyboard_sheet_paths, 1):
                files_dict[f"gen-storyboards-sheet-{i:03d}.jpg"] = sheet_path
            for i, sheet_path in enumerate(element_sheet_paths, 1):
                files_dict[f"gen-visual-elements-sheet-{i:03d}.jpg"] = sheet_path
            
            # 生成素材 - Storyboards (包含所有版本，区分首尾帧)
            for shot_number, version, frame_index, url in resources.get('keyframe_items', []):
                if url in downloaded_files:
                    # 根据 frame_index 确定文件名
                    if frame_index == -1:
                        # 尾帧
                        filename = f"gen-storyboards-{shot_number:03d}-endframe-v{version}.jpg"
                    else:
                        # 首帧（frame_index=0）或中间帧（frame_index=1）
                        filename = f"gen-storyboards-{shot_number:03d}-v{version}.jpg"
                    files_dict[filename] = downloaded_files[url]
            
            # 生成素材 - Visual Elements (包含所有版本)
            for char_index, version, url in resources.get('character_items', []):
                if url in downloaded_files:
                    files_dict[f"gen-visual-elements-{char_index:03d}-v{version}.jpg"] = downloaded_files[url]
            
            # ⭐ 生成素材 - Visual Elements Multi-View (包含所有版本)
            for char_index, version, url in resources.get('multi_view_items', []):
                if url in downloaded_files:
                    files_dict[f"gen-visual-elements-{char_index:03d}-v{version}-multiview.jpg"] = downloaded_files[url]
            
            # 生成素材 - Shots (包含所有版本)
            for shot_number, version, url in resources.get('shot_items', []):
                if url in downloaded_files:
                    files_dict[f"gen-shots-{shot_number:03d}-v{version}.mp4"] = downloaded_files[url]
            
            # 生成素材 - Segments (包含所有版本)
            for segment_number, version, url in resources.get('segment_items', []):
                if url in downloaded_files:
                    files_dict[f"gen-segments-{segment_number:03d}-v{version}.mp4"] = downloaded_files[url]
            
            # 生成素材 - Music
            for i, url in enumerate(resources.get('music_urls', []), 1):
                if url in downloaded_files:
                    # 判断是BGM还是Song（简化处理，都用bgm命名）
                    files_dict[f"gen-bgm-{i:03d}-v1.mp3"] = downloaded_files[url]
            
            # 7. 打包成 ZIP
            zip_path = await self._create_zip(files_dict, temp_dir)
            
            logger.info(f"✅ 导出包创建完成: {zip_path}")
            return zip_path
            
        except Exception as e:
            logger.error(f"❌ 创建导出包失败: {e}")
            # 如果创建失败，清理临时目录（在线程中执行，避免阻塞事件循环）
            try:
                import shutil
                if os.path.exists(temp_dir):
                    await asyncio.to_thread(shutil.rmtree, temp_dir)
                    logger.info(f"🗑️ 已清理失败的临时目录: {temp_dir}")
            except Exception:
                pass
            raise
        # 注意：不在这里清理temp_dir，因为zip文件还在其中
        # 清理工作由API endpoint在发送文件后进行
    
    async def _fetch_all_resources(self, video_assembly_uuid: str) -> Dict[str, Any]:
        """
        从数据库获取所有资源（使用 asyncpg CRUD）
        
        Returns:
            包含所有资源URL的字典
        """
        from ..crud.video.video_other import get_video_assembly_by_uuid
        from ..crud.video.video_story import get_video_story_outline_by_uuid
        from ..crud.video.video_character import (
            get_characters_by_conversation,
            get_character_versions_batch
        )
        from ..crud.video.video_keyframe import (
            get_keyframes_by_conversation,
            get_keyframe_versions_by_keyframe_ids
        )
        from ..crud.video.video_generation import (
            get_video_generations_by_conversation,
            get_video_generation_versions_by_video_generation_uuids
        )
        from ..crud.video.video_segment import (
            get_video_segments_with_data_by_run_id,
            get_video_segment_versions_by_segment_ids
        )
        from ..crud.video.video_audio import (
            get_music_generations_by_run_id,
            get_music_generation_versions_by_music_generation_ids
        )
        from ..crud.error_tracking import get_task_record_by_run_id
        
        logger.info(f"📊 开始查询数据库资源...")
        
        # 1. 获取 VideoAssembly
        video_assembly = await get_video_assembly_by_uuid(video_assembly_uuid)
        if not video_assembly:
            raise Exception(f"找不到视频合成记录: {video_assembly_uuid}")
        
        # 2. 获取 StoryOutline
        story_outline = await get_video_story_outline_by_uuid(video_assembly.story_outline_id)
        if not story_outline:
            raise Exception(f"找不到故事大纲: {video_assembly.story_outline_id}")
        
        conversation_id = video_assembly.conversation_id
        thread_id = video_assembly.thread_id
        run_id = video_assembly.run_id
        
        # 3. 获取 Characters (visual elements) - 所有版本
        characters = await get_characters_by_conversation(conversation_id, thread_id)
        character_items = []  # [(character_index, version, url), ...]
        multi_view_items = []  # [(character_index, version, url), ...] ⭐ 新增：多视角图
        if characters:
            characters = sorted(characters, key=lambda x: x.id)
            # 批量获取所有角色的版本
            character_uuids = [char.uuid for char in characters]
            all_versions = await get_character_versions_batch(character_uuids, video_assembly.user_id)
            
            # 为每个角色创建索引
            char_uuid_to_index = {char.uuid: idx + 1 for idx, char in enumerate(characters)}
            
            # ⭐ 使用选中的版本（而不是所有版本）
            for char_uuid, versions in all_versions.items():
                char_index = char_uuid_to_index.get(char_uuid)
                if char_index:
                    # 获取角色信息，找到选中的版本
                    character = next((c for c in characters if c.uuid == char_uuid), None)
                    if character and character.selected_version_id:
                        # 找到选中的版本
                        selected_version = next((v for v in versions if v.uuid == character.selected_version_id), None)
                        if selected_version:
                            # 主图（使用选中的版本）
                            if selected_version.success and selected_version.character_image_url:
                                character_items.append((char_index, selected_version.version_number, selected_version.character_image_url))
                            
                            # ⭐ 多视角图（使用选中的多视角图版本）
                            if selected_version.selected_multi_view_version_id:
                                from ..crud.video.video_character import get_character_multi_view_image_version_by_id
                                multi_view_version = await get_character_multi_view_image_version_by_id(
                                    selected_version.selected_multi_view_version_id
                                )
                                if multi_view_version and multi_view_version.multi_view_image_url:
                                    multi_view_items.append((char_index, multi_view_version.version_number, multi_view_version.multi_view_image_url))
                            # 如果没有选中的多视角图版本，尝试使用 multi_view_image_version_id（向后兼容）
                            elif selected_version.multi_view_image_version_id:
                                from ..crud.video.video_character import get_character_multi_view_image_version_by_id
                                multi_view_version = await get_character_multi_view_image_version_by_id(
                                    selected_version.multi_view_image_version_id
                                )
                                if multi_view_version and multi_view_version.multi_view_image_url:
                                    multi_view_items.append((char_index, multi_view_version.version_number, multi_view_version.multi_view_image_url))
            
            # 按角色索引和版本排序
            character_items.sort(key=lambda x: (x[0], x[1]))
            multi_view_items.sort(key=lambda x: (x[0], x[1]))
        else:
            character_items = []
            multi_view_items = []
        
        # 4. 获取 Keyframes (storyboards) - 所有版本，包含 frame_index 信息
        keyframes = await get_keyframes_by_conversation(conversation_id, thread_id)
        keyframe_items = []  # [(shot_number, version, frame_index, url), ...]
        if keyframes:
            # 使用 uuid 而不是 id（uuid 是字符串，id 是整数）
            keyframe_uuids = [kf.uuid for kf in keyframes]
            keyframe_versions = await get_keyframe_versions_by_keyframe_ids(keyframe_uuids)
            
            # 创建 keyframe 映射以获取 frame_index
            keyframe_map = {kf.uuid: kf for kf in keyframes}
            
            # 收集所有成功的版本（不仅仅是最新版本）
            for kv in keyframe_versions:
                if kv.success and kv.keyframe_url:
                    # 获取对应的 keyframe 以读取 frame_index
                    keyframe = keyframe_map.get(kv.keyframe_id)
                    frame_index = keyframe.frame_index if keyframe else 0
                    keyframe_items.append((kv.shot_number, kv.version_number, frame_index, kv.keyframe_url))
            # 按 shot_number, frame_index, version 排序
            keyframe_items.sort(key=lambda x: (x[0], x[2], x[1]))
        
        # 5. 获取 VideoGenerations (shots) - 所有版本
        video_generations = await get_video_generations_by_conversation(conversation_id, thread_id)
        shot_items = []  # [(shot_number, version, url), ...]
        if video_generations:
            # 批量获取所有 video generation 的版本
            vg_uuids = [vg.uuid for vg in video_generations]
            vg_versions = await get_video_generation_versions_by_video_generation_uuids(vg_uuids)
            # 收集所有成功的版本（不仅仅是最新版本）
            for version in vg_versions:
                if version.success and version.video_url:
                    shot_items.append((version.shot_number, version.version_number, version.video_url))
            # 按 shot_number 和 version 排序
            shot_items.sort(key=lambda x: (x[0], x[1]))
        
        # 6. 获取 VideoSegments - 所有版本
        segment_items = []  # [(segment_number, version, url), ...]
        if run_id:
            video_segments = await get_video_segments_with_data_by_run_id(run_id)
            if video_segments:
                # VideoSegmentDB 没有 video_url，需要获取其版本
                segment_uuids = [seg.uuid for seg in video_segments]
                segment_versions_dict = await get_video_segment_versions_by_segment_ids(segment_uuids)
                # 收集所有成功的版本（不仅仅是最新版本）
                for seg_uuid, versions in segment_versions_dict.items():
                    for version in versions:
                        if version.success and version.video_url:
                            segment_items.append((version.segment_number, version.version_number, version.video_url))
                # 按 segment_number 和 version 排序
                segment_items.sort(key=lambda x: (x[0], x[1]))
        
        # 7. 获取 Music - 只导出完整的背景音乐，不导出segment级别的音乐片段
        music_urls = []
        if run_id:
            music_generations = await get_music_generations_by_run_id(run_id)
            if music_generations:
                # 🎵 只保留 is_full_story_music=True 的音乐（完整的背景音乐）
                # 不导出分段的音乐片段（segment level的音乐）
                full_story_music = [mg for mg in music_generations if mg.is_full_story_music]
                
                if full_story_music:
                    # 批量获取完整背景音乐的版本
                    mg_uuids = [mg.uuid for mg in full_story_music]
                    mg_versions = await get_music_generation_versions_by_music_generation_ids(mg_uuids)
                    # 按 music_generation_id 分组，取每组的最新版本
                    versions_by_mg = defaultdict(list)
                    for version in mg_versions:
                        versions_by_mg[version.music_generation_id].append(version)
                    # 收集所有的最新版本
                    music_items = []
                    for mg_uuid, versions in versions_by_mg.items():
                        if versions:
                            latest_version = max(versions, key=lambda x: x.version_number)
                            if latest_version.success and latest_version.music_url:
                                # 使用 id 排序（完整音乐通常只有1个）
                                music_items.append((latest_version.id, latest_version.music_url))
                    # 排序后提取 URL
                    music_items.sort(key=lambda x: x[0])
                    music_urls = [url for _, url in music_items]
                    
                    logger.info(f"🎵 导出完整背景音乐: {len(music_urls)} 首")
                else:
                    logger.info("🎵 没有完整背景音乐，跳过音乐导出（不导出segment级别的音乐片段）")
        
        # 8. 获取输入文件和用户原始输入
        # 注意：这部分需要根据实际数据结构调整
        input_images = []
        input_videos = []
        input_audios = []
        
        # 从 VideoTaskRecordDB 获取用户原始输入 prompt
        user_input_text = ""
        if run_id:
            task_record = await get_task_record_by_run_id(run_id)
            if task_record and task_record.task_input:
                user_input_text = task_record.task_input
                # 也可以从 user_input_data 中获取上传的文件
                if task_record.user_input_data:
                    user_data = task_record.user_input_data
                    if isinstance(user_data, dict):
                        # 获取上传的图片
                        if 'images' in user_data and isinstance(user_data['images'], list):
                            input_images = [img['url'] for img in user_data['images'] if isinstance(img, dict) and 'url' in img]
                        # 获取上传的视频
                        if 'video_files' in user_data and isinstance(user_data['video_files'], list):
                            input_videos = [vid['url'] for vid in user_data['video_files'] if isinstance(vid, dict) and 'url' in vid]
                        # 获取上传的音频
                        if 'audio_files' in user_data and isinstance(user_data['audio_files'], list):
                            input_audios = [aud['url'] for aud in user_data['audio_files'] if isinstance(aud, dict) and 'url' in aud]
        
        resources = {
            'final_video_url': video_assembly.final_video_url,
            'final_no_subtitle_url': video_assembly.final_video_url_no_subtitle,
            'character_items': character_items,  # [(char_index, version, url), ...] - 所有版本
            'multi_view_items': multi_view_items,  # [(char_index, version, url), ...] ⭐ 新增：多视角图
            'keyframe_items': keyframe_items,  # [(shot_number, version, frame_index, url), ...] - 所有版本
            'shot_items': shot_items,  # [(shot_number, version, url), ...] - 所有版本
            'segment_items': segment_items,  # [(segment_number, version, url), ...] - 所有版本
            'music_urls': music_urls,
            'input_images': input_images,
            'input_videos': input_videos,
            'input_audios': input_audios,
            'user_input_text': user_input_text,
            'title': story_outline.title or "video"
        }
        
        logger.info(f"✅ 资源统计: 角色版本{len(character_items)}, 多视角图{len(multi_view_items)}, 关键帧版本{len(keyframe_items)}, "
                   f"镜头版本{len(shot_items)}, 片段版本{len(segment_items)}, 音乐{len(music_urls)}")
        
        return resources
    
    async def _download_all_files(self, resources: Dict[str, Any], temp_dir: str) -> Dict[str, str]:
        """
        批量下载所有文件（并行异步下载）
        
        Returns:
            URL -> 本地路径的映射
        """
        import asyncio
        
        logger.info(f"📥 开始批量下载文件...")
        
        # 收集所有需要下载的URL
        all_urls = []
        
        if resources.get('final_video_url'):
            all_urls.append(resources['final_video_url'])
        if resources.get('final_no_subtitle_url'):
            all_urls.append(resources['final_no_subtitle_url'])
        
        # 从 items 中提取 URL（character/shot/segment是3-tuple，keyframe是4-tuple）
        all_urls.extend([url for _, _, url in resources.get('character_items', [])])
        all_urls.extend([url for _, _, url in resources.get('multi_view_items', [])])  # ⭐ 新增：多视角图
        all_urls.extend([url for _, _, _, url in resources.get('keyframe_items', [])])
        all_urls.extend([url for _, _, url in resources.get('shot_items', [])])
        all_urls.extend([url for _, _, url in resources.get('segment_items', [])])
        all_urls.extend(resources.get('music_urls', []))
        all_urls.extend(resources.get('input_images', []))
        all_urls.extend(resources.get('input_videos', []))
        all_urls.extend(resources.get('input_audios', []))
        
        # 去重
        all_urls = list(set([url for url in all_urls if url]))
        
        logger.info(f"📋 共需下载 {len(all_urls)} 个文件")
        
        # 定义单个文件下载任务
        async def download_one(url: str) -> tuple[str, Optional[str]]:
            try:
                # 判断媒体类型
                media_type = MediaType.VIDEO
                if url.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    media_type = MediaType.IMAGE
                elif url.endswith(('.mp3', '.wav', '.m4a')):
                    media_type = MediaType.AUDIO
                
                local_path = await download_media_to_temp(url, temp_dir, media_type)
                if local_path:
                    logger.debug(f"✅ 下载完成: {os.path.basename(url)}")
                    return (url, local_path)
                else:
                    logger.warning(f"⚠️ 下载失败: {url}")
                    return (url, None)
            except Exception as e:
                logger.error(f"❌ 下载文件失败 {url}: {e}")
                return (url, None)
        
        # 并行下载所有文件
        download_tasks = [download_one(url) for url in all_urls]
        results = await asyncio.gather(*download_tasks, return_exceptions=True)
        
        # 构建映射
        url_to_path = {}
        for result in results:
            if isinstance(result, tuple) and result[1]:
                url_to_path[result[0]] = result[1]
        
        logger.info(f"✅ 批量下载完成: 成功{len(url_to_path)}/{len(all_urls)}")
        return url_to_path
    
    async def _create_input_txt(self, resources: Dict[str, Any], temp_dir: str) -> Optional[str]:
        """创建 input.txt 文件"""
        try:
            user_input = resources.get('user_input_text', '')
            if not user_input:
                return None
            
            input_txt_path = os.path.join(temp_dir, "input.txt")
            async with aiofiles.open(input_txt_path, 'w', encoding='utf-8') as f:
                await f.write(user_input)
            
            logger.info(f"✅ 创建 input.txt")
            return input_txt_path
        except Exception as e:
            logger.error(f"❌ 创建 input.txt 失败: {e}")
            return None
    
    async def create_image_sheets(
        self,
        image_urls: List[str],
        output_prefix: str,
        max_images_per_sheet: int = 100,
        grid_cols: int = 10
    ) -> List[str]:
        """
        创建图片拼接 sheets（支持多个sheet）
        
        Args:
            image_urls: 图片URL列表
            output_prefix: 输出路径前缀（如 "/tmp/xxx/gen-storyboards-sheet"）
            max_images_per_sheet: 每个sheet最多图片数量
            grid_cols: 每行图片数量
            
        Returns:
            生成的sheet文件路径列表
        """
        sheet_paths = []
        
        if not image_urls:
            return sheet_paths
        
        logger.info(f"🖼️ 开始生成 Image Sheets: {len(image_urls)} 张图片")
        
        # 定义单个图片下载任务（PIL 解码放线程池，避免阻塞事件循环）
        loop = asyncio.get_event_loop()
        async def download_image(session: aiohttp.ClientSession, url: str) -> Optional[Image.Image]:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status == 200:
                        img_data = await response.read()
                        img = await loop.run_in_executor(None, lambda d=img_data: Image.open(BytesIO(d)).copy())
                        return img
            except Exception as e:
                logger.warning(f"⚠️ 下载图片失败: {url}, 错误: {e}")
            return None
        
        # 创建一个共享的 session
        async with aiohttp.ClientSession() as session:
            # 分批处理
            for batch_idx, start_idx in enumerate(range(0, len(image_urls), max_images_per_sheet)):
                batch_urls = image_urls[start_idx:start_idx + max_images_per_sheet]
                
                # 并行下载当前批次的所有图片
                download_tasks = [download_image(session, url) for url in batch_urls]
                downloaded_images = await asyncio.gather(*download_tasks, return_exceptions=True)
                
                # 过滤掉失败的下载
                images = [img for img in downloaded_images if isinstance(img, Image.Image)]
                
                if not images:
                    continue
                
                num_images = len(images)
                target_width = 300
                sheet_num = batch_idx + 1
                output_path = f"{output_prefix}-{sheet_num:03d}.jpg"
                
                # 画布拼接 + 保存为同步 CPU/IO，放入线程池避免阻塞事件循环
                def _build_one_sheet_sync():
                    resized_images = []
                    for img in images:
                        try:
                            if img.mode != 'RGB':
                                img = img.convert('RGB')
                            ratio = target_width / img.width
                            target_height = int(img.height * ratio)
                            resized = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
                            resized_images.append(resized)
                        except Exception:
                            continue
                    if not resized_images:
                        return None
                    grid_rows = (len(resized_images) + grid_cols - 1) // grid_cols
                    canvas_width = grid_cols * target_width
                    max_height = max(im.height for im in resized_images)
                    canvas_height = grid_rows * max_height
                    canvas = Image.new('RGB', (canvas_width, canvas_height), (255, 255, 255))
                    for idx, img in enumerate(resized_images):
                        row = idx // grid_cols
                        col = idx % grid_cols
                        x = col * target_width
                        y = row * max_height
                        canvas.paste(img, (x, y))
                    canvas.save(output_path, 'JPEG', quality=90)
                    return output_path
                
                path = await asyncio.to_thread(_build_one_sheet_sync)
                if path:
                    sheet_paths.append(path)
                    logger.info(f"✅ 生成 Image Sheet: {os.path.basename(path)} ({num_images} 张图片)")
        
        return sheet_paths
    
    async def _create_zip(self, files_dict: Dict[str, str], temp_dir: str) -> str:
        """
        创建 ZIP 文件（在线程池中执行，避免阻塞事件循环）
        
        Args:
            files_dict: {zip中的文件名: 本地文件路径}
            temp_dir: 临时目录
            
        Returns:
            ZIP文件路径
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        zip_filename = f"video-assets-{timestamp}.zip"
        zip_path = os.path.join(temp_dir, zip_filename)
        
        def _write_zip_sync():
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for zip_name, local_path in files_dict.items():
                    if local_path and os.path.exists(local_path):
                        zipf.write(local_path, zip_name)
                        logger.debug(f"  ✓ {zip_name}")
                    else:
                        logger.warning(f"  ✗ 文件不存在: {zip_name} -> {local_path}")
        
        logger.info(f"📦 开始打包 ZIP: {len(files_dict)} 个文件")
        await asyncio.to_thread(_write_zip_sync)
        logger.info(f"✅ ZIP 打包完成: {zip_filename}")
        return zip_path
    
    def _get_file_extension(self, url: str) -> str:
        """获取文件扩展名"""
        ext = os.path.splitext(url)[1]
        return ext if ext else '.jpg'


# 全局实例
download_service = DownloadService()
