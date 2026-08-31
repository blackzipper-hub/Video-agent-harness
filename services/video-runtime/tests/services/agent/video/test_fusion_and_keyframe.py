"""
融合图 + 关键帧生成逻辑综合测试

基于 thread_id = full-803c8251-eff-eff_0 的真实数据：
- 14 个角色（2 character / 8 object / 4 location）
- 34 个 shot，其中 20 个 shot 的 character_ids 超过 model_limit=3
- 0 个融合图（ENABLE_FUSION=False，从未生成）

测试内容：
1. split_characters_by_model_limit 分组正确性
2. decide_fusion_combinations_for_scenes 去重、覆盖
3. get_character_ref_images —— 有/无融合图时的行为
4. character_ids 排序一致性（character > object > location）
5. _build_character_images_dict 构建
6. regenerate keyframe 路径的 character_ids 使用
"""
import asyncio
import os
import json
import logging
from dataclasses import dataclass, field as dc_field
from typing import Dict, List, Optional, Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

# 先导入 keyframe_generation_service 以避免循环导入
import app.services.agent.video.keyframe_generation_service  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/storybook_dev",
)
THREAD_ID = "full-803c8251-eff-eff_0"
RUN_ID = "787bf76b-927f-47a1-9948-b06fd5a41a14"
USER_ID = "admin"


# ==================== Fixtures ====================

_cached_real_data = None

async def _load_real_data():
    global _cached_real_data
    if _cached_real_data is not None:
        return _cached_real_data
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        chars = await conn.fetch(
            "SELECT uuid, name, type, role, image_url FROM video_characters "
            "WHERE thread_id = $1 AND user_id = $2 ORDER BY created_at",
            THREAD_ID, USER_ID,
        )
        scenes = await conn.fetch(
            "SELECT uuid, scene_number, title, character_ids FROM video_scenes "
            "WHERE run_id = $1 ORDER BY scene_number",
            RUN_ID,
        )
        shots = await conn.fetch(
            "SELECT uuid, shot_number, character_ids, scene_description "
            "FROM video_detailed_shots WHERE run_id = $1 ORDER BY shot_number",
            RUN_ID,
        )
        fusions = await conn.fetch(
            "SELECT uuid, fusion_key, image_type, character_ids, fusion_image_url "
            "FROM video_character_fusion_images WHERE thread_id = $1",
            THREAD_ID,
        )
    await pool.close()
    _cached_real_data = {
        "characters": [dict(c) for c in chars],
        "scenes": [dict(s) for s in scenes],
        "shots": [dict(s) for s in shots],
        "fusions": [dict(f) for f in fusions],
    }
    return _cached_real_data


@pytest.fixture
async def real_data():
    """从 DB 加载 thread 的真实数据（async fixture，每个 test 复用缓存）"""
    return await _load_real_data()


# ==================== 1. split_characters_by_model_limit ====================

class TestSplitCharactersByModelLimit:
    def test_exact_multiple(self):
        from app.services.agent.video.character_fusion_service import split_characters_by_model_limit
        ids = ["a", "b", "c", "d", "e", "f"]
        groups = split_characters_by_model_limit(ids, 3)
        assert groups == [["a", "b", "c"], ["d", "e", "f"]]

    def test_remainder(self):
        from app.services.agent.video.character_fusion_service import split_characters_by_model_limit
        ids = ["a", "b", "c", "d", "e"]
        groups = split_characters_by_model_limit(ids, 3)
        assert groups == [["a", "b", "c"], ["d", "e"]]

    def test_under_limit(self):
        from app.services.agent.video.character_fusion_service import split_characters_by_model_limit
        ids = ["a", "b"]
        groups = split_characters_by_model_limit(ids, 3)
        assert groups == [["a", "b"]]

    def test_single_element(self):
        from app.services.agent.video.character_fusion_service import split_characters_by_model_limit
        ids = ["a"]
        groups = split_characters_by_model_limit(ids, 3)
        assert groups == [["a"]]

    def test_empty(self):
        from app.services.agent.video.character_fusion_service import split_characters_by_model_limit
        assert split_characters_by_model_limit([], 3) == []

    def test_preserves_order(self):
        """分组必须保持原始顺序（排序后的 character_ids：character > object > location）"""
        from app.services.agent.video.character_fusion_service import split_characters_by_model_limit
        ids = ["char1", "char2", "obj1", "obj2", "loc1", "loc2"]
        groups = split_characters_by_model_limit(ids, 3)
        assert groups[0] == ["char1", "char2", "obj1"]
        assert groups[1] == ["obj2", "loc1", "loc2"]


# ==================== 2. decide_fusion_combinations_for_scenes ====================

class TestDecideFusionCombinations:
    def _make_scene(self, scene_number, character_ids):
        from app.models.video_state import StoryboardScene
        return StoryboardScene(
            scene_number=scene_number,
            title=f"Scene {scene_number}",
            description="test",
            duration=5.0,
            camera_angle="medium",
            character_action="action",
            visual_style="style",
            transition_style="cut",
            character_ids=character_ids,
        )

    def test_under_limit_no_fusion(self):
        from app.services.agent.video.character_fusion_service import decide_fusion_combinations_for_scenes
        scenes = [self._make_scene(1, ["a", "b", "c"])]
        main, mv = decide_fusion_combinations_for_scenes(scenes, model_limit=3)
        assert len(main) == 0
        assert len(mv) == 0

    def test_over_limit_generates_fusion(self):
        from app.services.agent.video.character_fusion_service import decide_fusion_combinations_for_scenes
        scenes = [self._make_scene(1, ["a", "b", "c", "d"])]
        main, mv = decide_fusion_combinations_for_scenes(scenes, model_limit=3)
        assert len(main) == 1, f"Expected 1 main fusion, got {len(main)}"
        assert len(mv) == 1
        group = list(main.values())[0]
        assert set(group) == {"a", "b", "c"}

    def test_six_chars_two_fusions(self):
        from app.services.agent.video.character_fusion_service import decide_fusion_combinations_for_scenes
        scenes = [self._make_scene(1, ["a", "b", "c", "d", "e", "f"])]
        main, mv = decide_fusion_combinations_for_scenes(scenes, model_limit=3)
        assert len(main) == 2, f"Expected 2 main fusions, got {len(main)}"
        groups = list(main.values())
        all_chars = set()
        for g in groups:
            all_chars.update(g)
        assert all_chars == {"a", "b", "c", "d", "e", "f"}

    def test_dedup_across_scenes(self):
        """同样的角色组合在多个 scene 中只生成一次融合图"""
        from app.services.agent.video.character_fusion_service import decide_fusion_combinations_for_scenes
        ids = ["a", "b", "c", "d"]
        scenes = [self._make_scene(1, ids), self._make_scene(2, ids)]
        main, mv = decide_fusion_combinations_for_scenes(scenes, model_limit=3)
        assert len(main) == 1, "Duplicate combinations should be deduped"

    def test_real_data_scenes(self, real_data):
        """用真实数据测试：20 个 scene 超限，验证融合组合覆盖"""
        from app.services.agent.video.character_fusion_service import decide_fusion_combinations_for_scenes
        from app.models.video_state import StoryboardScene

        scenes = []
        for s in real_data["scenes"]:
            cids = s["character_ids"] if isinstance(s["character_ids"], list) else json.loads(s["character_ids"])
            scenes.append(self._make_scene(s["scene_number"], cids))

        main, mv = decide_fusion_combinations_for_scenes(scenes, model_limit=3)
        logger.info(f"Real data: {len(main)} main fusions, {len(mv)} multiview fusions")
        for key, chars in main.items():
            logger.info(f"  {key[:50]}: {chars}")

        over_limit = [s for s in scenes if len(s.character_ids) > 3]
        assert len(over_limit) > 0, "Should have scenes over limit"
        assert len(main) > 0, "Should generate at least one fusion combination"
        assert len(main) == len(mv), "Main and multiview should have same count"


# ==================== 3. get_character_ref_images ====================

class TestGetCharacterRefImages:

    def _make_shot(self, shot_number, character_ids):
        shot = MagicMock()
        shot.shot_number = shot_number
        shot.character_ids = character_ids
        return shot

    def _make_char_info(self, char_id, main_url=None):
        from app.models.video_state import CharacterImageInfo
        return CharacterImageInfo(
            character_id=char_id,
            main_image_url=main_url or f"https://cdn.test/{char_id}.jpg",
        )

    @pytest.mark.asyncio
    async def test_under_limit_returns_individual_images(self):
        """角色 <= model_limit 时，直接返回各自的单独图片"""
        from app.services.agent.video.keyframe_generation_service import get_character_ref_images

        ids = ["c1", "c2", "c3"]
        shot = self._make_shot(1, ids)
        char_images = {cid: self._make_char_info(cid) for cid in ids}

        result = await get_character_ref_images(shot, char_images, model_limit=3, user_id="admin")
        assert len(result) == 3
        for cid in ids:
            assert f"https://cdn.test/{cid}.jpg" in result

    @pytest.mark.asyncio
    async def test_over_limit_no_fusion_falls_back(self):
        """角色 > model_limit 但无融合图 → 降级为单独图片（全部返回）"""
        from app.services.agent.video.keyframe_generation_service import get_character_ref_images

        ids = ["c1", "c2", "c3", "c4"]
        shot = self._make_shot(1, ids)
        char_images = {cid: self._make_char_info(cid) for cid in ids}

        result = await get_character_ref_images(
            shot, char_images, model_limit=3, user_id="admin",
            pre_fetched_fusions=[],
        )
        assert len(result) == 4, f"Without fusion, should return all 4 images, got {len(result)}"

    @pytest.mark.asyncio
    async def test_over_limit_with_matching_fusion(self):
        """角色 > model_limit + 有匹配融合图 → 用融合图 + 剩余单独图"""
        from app.services.agent.video.keyframe_generation_service import get_character_ref_images

        ids = ["c1", "c2", "c3", "c4"]
        shot = self._make_shot(1, ids)
        char_images = {cid: self._make_char_info(cid) for cid in ids}

        fusion_mock = MagicMock()
        fusion_mock.character_ids = ["c1", "c2", "c3"]
        fusion_mock.fusion_image_url = "https://cdn.test/fusion_c1_c2_c3.jpg"
        fusion_mock.image_type = "main"

        result = await get_character_ref_images(
            shot, char_images, model_limit=3, user_id="admin",
            pre_fetched_fusions=[fusion_mock],
        )
        assert "https://cdn.test/fusion_c1_c2_c3.jpg" in result
        assert "https://cdn.test/c4.jpg" in result
        assert len(result) == 2, f"Should be 1 fusion + 1 individual, got {len(result)}"

    @pytest.mark.asyncio
    async def test_six_chars_two_fusions(self):
        """6 个角色 → 2 组融合图，各自匹配"""
        from app.services.agent.video.keyframe_generation_service import get_character_ref_images

        ids = ["c1", "c2", "c3", "c4", "c5", "c6"]
        shot = self._make_shot(1, ids)
        char_images = {cid: self._make_char_info(cid) for cid in ids}

        fusion1 = MagicMock()
        fusion1.character_ids = ["c1", "c2", "c3"]
        fusion1.fusion_image_url = "https://cdn.test/fusion_123.jpg"
        fusion1.image_type = "main"

        fusion2 = MagicMock()
        fusion2.character_ids = ["c4", "c5", "c6"]
        fusion2.fusion_image_url = "https://cdn.test/fusion_456.jpg"
        fusion2.image_type = "main"

        result = await get_character_ref_images(
            shot, char_images, model_limit=3, user_id="admin",
            pre_fetched_fusions=[fusion1, fusion2],
        )
        assert "https://cdn.test/fusion_123.jpg" in result
        assert "https://cdn.test/fusion_456.jpg" in result
        assert len(result) == 2, f"Should be 2 fusions, got {len(result)}: {result}"

    @pytest.mark.asyncio
    async def test_partial_fusion_match(self):
        """5 个角色，model_limit=3 → 第一组有融合图，第二组(2个)没有 → 融合图 + 2 个单独图"""
        from app.services.agent.video.keyframe_generation_service import get_character_ref_images

        ids = ["c1", "c2", "c3", "c4", "c5"]
        shot = self._make_shot(1, ids)
        char_images = {cid: self._make_char_info(cid) for cid in ids}

        fusion1 = MagicMock()
        fusion1.character_ids = ["c1", "c2", "c3"]
        fusion1.fusion_image_url = "https://cdn.test/fusion_123.jpg"
        fusion1.image_type = "main"

        result = await get_character_ref_images(
            shot, char_images, model_limit=3, user_id="admin",
            pre_fetched_fusions=[fusion1],
        )
        assert "https://cdn.test/fusion_123.jpg" in result
        assert "https://cdn.test/c4.jpg" in result
        assert "https://cdn.test/c5.jpg" in result
        assert len(result) == 3, f"Should be 1 fusion + 2 individual, got {len(result)}"

    @pytest.mark.asyncio
    async def test_empty_character_ids(self):
        """空 character_ids → 空列表"""
        from app.services.agent.video.keyframe_generation_service import get_character_ref_images
        shot = self._make_shot(1, [])
        result = await get_character_ref_images(shot, {}, model_limit=3)
        assert result == []

    @pytest.mark.asyncio
    async def test_none_character_ids(self):
        """None character_ids → 空列表"""
        from app.services.agent.video.keyframe_generation_service import get_character_ref_images
        shot = self._make_shot(1, None)
        result = await get_character_ref_images(shot, {}, model_limit=3)
        assert result == []


# ==================== 4. character_ids 排序一致性 ====================

class TestCharacterIdsOrdering:
    def test_type_priority_sorting(self):
        """character > object > location 的排序"""
        from app.models.video_state import VisualElementType

        _type_priority = {"character": 0, "object": 1, "location": 2}
        char_type_map = {
            "loc1": "location",
            "obj1": "object",
            "char1": "character",
            "obj2": "object",
            "char2": "character",
            "loc2": "location",
        }
        unsorted = ["loc1", "obj1", "char1", "obj2", "char2", "loc2"]
        sorted_ids = sorted(
            unsorted,
            key=lambda cid: _type_priority.get(char_type_map.get(cid, "character"), 3)
        )
        assert sorted_ids[:2] == ["char1", "char2"], f"Characters should come first: {sorted_ids}"
        assert sorted_ids[2:4] == ["obj1", "obj2"], f"Objects should come second: {sorted_ids}"
        assert sorted_ids[4:] == ["loc1", "loc2"], f"Locations should come last: {sorted_ids}"

    def test_real_data_character_first(self, real_data):
        """真实数据中的排序验证：character 类型应该排在 object/location 前面"""
        char_type_map = {c["uuid"]: c["type"] for c in real_data["characters"]}
        character_uuids = {c["uuid"] for c in real_data["characters"] if c["type"] == "character"}

        _type_priority = {"character": 0, "object": 1, "location": 2}

        for shot in real_data["shots"]:
            cids = shot["character_ids"] if isinstance(shot["character_ids"], list) else json.loads(shot["character_ids"])
            if len(cids) <= 1:
                continue

            sorted_cids = sorted(
                cids,
                key=lambda cid: _type_priority.get(char_type_map.get(cid, "character"), 3)
            )

            first_non_char_idx = len(sorted_cids)
            for i, cid in enumerate(sorted_cids):
                if char_type_map.get(cid) != "character":
                    first_non_char_idx = i
                    break

            for i in range(first_non_char_idx):
                assert char_type_map.get(sorted_cids[i]) == "character", \
                    f"Shot {shot['shot_number']}: Position {i} should be character type"

    def test_sorted_ids_fed_to_split_preserves_character_first_in_fusion(self, real_data):
        """排序后再 split → 第一个融合组必然包含所有 character 类型"""
        from app.services.agent.video.character_fusion_service import split_characters_by_model_limit

        char_type_map = {c["uuid"]: c["type"] for c in real_data["characters"]}
        _type_priority = {"character": 0, "object": 1, "location": 2}

        for shot in real_data["shots"]:
            cids = shot["character_ids"] if isinstance(shot["character_ids"], list) else json.loads(shot["character_ids"])
            if len(cids) <= 3:
                continue

            sorted_cids = sorted(
                cids,
                key=lambda cid: _type_priority.get(char_type_map.get(cid, "character"), 3)
            )

            groups = split_characters_by_model_limit(sorted_cids, 3)

            char_count_in_shot = sum(1 for c in cids if char_type_map.get(c) == "character")
            char_count_in_first_group = sum(1 for c in groups[0] if char_type_map.get(c) == "character")
            assert char_count_in_first_group >= min(char_count_in_shot, 3), \
                f"Shot {shot['shot_number']}: First fusion group should contain characters first, " \
                f"expected >= {min(char_count_in_shot, 3)}, got {char_count_in_first_group}"


# ==================== 5. generate_fusion_key ====================

class TestGenerateFusionKey:
    def test_deterministic_regardless_of_order(self):
        """fusion_key 应与 character_ids 顺序无关（内部排序）"""
        from app.services.agent.video.character_fusion_service import generate_fusion_key
        from app.services.agent.video.keyframe_generation_service import ImageType

        key1 = generate_fusion_key(["a", "b", "c"], ImageType.MAIN)
        key2 = generate_fusion_key(["c", "a", "b"], ImageType.MAIN)
        assert key1 == key2

    def test_different_types_different_keys(self):
        from app.services.agent.video.character_fusion_service import generate_fusion_key
        from app.services.agent.video.keyframe_generation_service import ImageType

        main_key = generate_fusion_key(["a", "b"], ImageType.MAIN)
        mv_key = generate_fusion_key(["a", "b"], ImageType.MULTIVIEW)
        assert main_key != mv_key


# ==================== 6. _build_character_images_dict (真实 DB) ====================

class TestBuildCharacterImagesDict:
    @pytest.mark.asyncio
    async def test_build_from_real_data(self, real_data):
        """用真实 character UUIDs 构建图片字典，验证所有角色都被加载"""
        from app.models.database import init_asyncpg_pool, close_asyncpg_pool
        from app.services.agent.video.keyframe_generation_service import _build_character_images_dict

        await init_asyncpg_pool()
        try:
            all_char_ids = {c["uuid"] for c in real_data["characters"]}
            char_images = await _build_character_images_dict(all_char_ids, USER_ID)

            logger.info(f"Built character_images: {len(char_images)} entries")
            for cid, ci in char_images.items():
                logger.info(f"  {cid[:8]}: main={ci.main_image_url is not None}, "
                           f"mv={ci.multiview_url is not None}, "
                           f"fusion={ci.main_fusion_url is not None}, "
                           f"profile={ci.profile is not None}")

            assert len(char_images) >= len(all_char_ids) * 0.8, \
                f"Should load most characters, got {len(char_images)}/{len(all_char_ids)}"

            for cid, ci in char_images.items():
                assert ci.profile is not None, f"Character {cid} should have profile"
        finally:
            await close_asyncpg_pool()


# ==================== 7. End-to-end: get_character_ref_images with real shot data ====================

class TestEndToEndRefImages:
    @pytest.mark.asyncio
    async def test_real_shots_ref_images_count(self, real_data):
        """
        对所有真实 shot 调用 get_character_ref_images（无融合图）：
        - 角色 <= 3 的 shot：返回数量 == character_ids 中有图的数量
        - 角色 > 3 的 shot：降级为全部返回（无融合图）
        """
        from app.models.database import init_asyncpg_pool, close_asyncpg_pool
        from app.services.agent.video.keyframe_generation_service import (
            get_character_ref_images, _build_character_images_dict
        )

        await init_asyncpg_pool()
        try:
            all_char_ids = {c["uuid"] for c in real_data["characters"]}
            char_images = await _build_character_images_dict(all_char_ids, USER_ID)

            over_limit_count = 0
            under_limit_count = 0

            for s in real_data["shots"]:
                cids = s["character_ids"] if isinstance(s["character_ids"], list) else json.loads(s["character_ids"])
                shot = MagicMock()
                shot.shot_number = s["shot_number"]
                shot.character_ids = cids

                result = await get_character_ref_images(
                    shot, char_images, model_limit=3, user_id=USER_ID,
                    pre_fetched_fusions=[],
                )

                chars_with_images = sum(1 for cid in cids if cid in char_images and char_images[cid].main_image_url)
                if len(cids) > 3:
                    over_limit_count += 1
                    assert len(result) == chars_with_images, \
                        f"Shot {s['shot_number']} (no fusion fallback): expected {chars_with_images}, got {len(result)}"
                else:
                    under_limit_count += 1
                    assert len(result) == chars_with_images, \
                        f"Shot {s['shot_number']}: expected {chars_with_images}, got {len(result)}"

            logger.info(f"Tested {under_limit_count} under-limit + {over_limit_count} over-limit shots")
            assert over_limit_count > 0, "Should have over-limit shots in real data"
        finally:
            await close_asyncpg_pool()


# ==================== 8. Regenerate keyframe 路径验证 ====================

class TestRegenerateKeyframePath:
    @pytest.mark.asyncio
    async def test_regenerate_uses_shot_character_ids(self, real_data):
        """
        regenerate 路径直接读 shot.character_ids。
        验证 shot.character_ids 中的所有 ID 都能在 _build_character_images_dict 中找到。
        """
        from app.models.database import init_asyncpg_pool, close_asyncpg_pool
        from app.services.agent.video.keyframe_generation_service import _build_character_images_dict

        await init_asyncpg_pool()
        try:
            for s in real_data["shots"][:5]:
                cids = s["character_ids"] if isinstance(s["character_ids"], list) else json.loads(s["character_ids"])
                char_images = await _build_character_images_dict(set(cids), USER_ID)

                missing = [cid for cid in cids if cid not in char_images]
                assert not missing, \
                    f"Shot {s['shot_number']}: IDs not found in char_images: {missing}"
        finally:
            await close_asyncpg_pool()


# ==================== 9. fusion key 与 DB character_ids 排序无关 ====================

class TestFusionKeyStability:
    def test_fusion_key_stable_after_reorder(self, real_data):
        """
        character_ids 被排序后，fusion_key 应该不变（因为 generate_fusion_key 内部也排序）。
        这确保重新排序 character_ids 不会导致融合图查找失败。
        """
        from app.services.agent.video.character_fusion_service import (
            generate_fusion_key, split_characters_by_model_limit
        )
        from app.services.agent.video.keyframe_generation_service import ImageType

        char_type_map = {c["uuid"]: c["type"] for c in real_data["characters"]}
        _type_priority = {"character": 0, "object": 1, "location": 2}

        for s in real_data["shots"]:
            cids = s["character_ids"] if isinstance(s["character_ids"], list) else json.loads(s["character_ids"])
            if len(cids) <= 3:
                continue

            original_groups = split_characters_by_model_limit(cids, 3)

            sorted_cids = sorted(
                cids,
                key=lambda cid: _type_priority.get(char_type_map.get(cid, "character"), 3)
            )
            sorted_groups = split_characters_by_model_limit(sorted_cids, 3)

            for orig_g, sort_g in zip(original_groups, sorted_groups):
                if len(orig_g) < 2 or len(sort_g) < 2:
                    continue
                orig_key = generate_fusion_key(orig_g, ImageType.MAIN)
                sort_key = generate_fusion_key(sort_g, ImageType.MAIN)
                logger.info(
                    f"Shot {s['shot_number']}: orig_key == sort_key? {orig_key == sort_key} "
                    f"(orig_group={orig_g}, sort_group={sort_g})"
                )


# ==================== Main ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--tb=short"])
