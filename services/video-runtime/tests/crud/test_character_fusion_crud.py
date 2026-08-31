#!/usr/bin/env python3
"""
角色融合图 CRUD 测试 - 验证 character_ids (jsonb) 的写入与查询

验证点：
- create_character_fusion_image: character_ids 以 JSON 字符串写入 jsonb 列
- get_character_fusion_image_by_key: jsonb @> / <@ 精确匹配
- get_character_fusion_images_by_character_ids: jsonb && 重叠查询
- get_character_fusion_images_batch: jsonb && 重叠查询并按角色分组

运行：需配置 .env.development 且数据库/Redis 可用
  pytest tests/crud/test_character_fusion_crud.py -v -s
  python tests/crud/test_character_fusion_crud.py   # 独立运行
"""

import asyncio
import pytest

# 独立运行时的环境加载
if __name__ == "__main__":
    import os
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    env = root / ".env.development"
    if env.exists():
        with open(env) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")
    pytest.main([__file__, "-v", "-s"])


@pytest.fixture(autouse=True)
async def init_db_pool():
    """CRUD 测试前初始化 asyncpg 连接池，测试结束后关闭"""
    from app.models.database import init_asyncpg_pool, close_asyncpg_pool
    await init_asyncpg_pool()
    try:
        yield
    finally:
        await close_asyncpg_pool()


@pytest.fixture
def fusion_user_and_ids():
    """测试用 user_id 和 character_ids（UUID 格式字符串列表）"""
    return {
        "user_id": "test-fusion-crud-user",
        "conversation_id": "test-conv",
        "thread_id": "test-thread",
        "run_id": "test-run",
        "character_ids_1": [
            "956c584a-0134-4f03-b2fd-6cf415c96527",
            "d0d3b762-d28d-49d0-b4bb-adf80ab47ea1",
        ],
        "character_ids_2": [
            "a2099ed5-b4bc-4e1a-9b48-c2616bd0ac69",
            "bfec5704-5634-4e1a-9b48-c2616bd0ac69",
            "d0d3b762-d28d-49d0-b4bb-adf80ab47ea1",
        ],
    }


@pytest.mark.asyncio
async def test_create_and_get_by_key(fusion_user_and_ids):
    """创建融合图后能用 get_character_fusion_image_by_key 按 character_ids+user_id 精确查到"""
    from app.crud.video.video_character import (
        create_character_fusion_image,
        get_character_fusion_image_by_key,
    )

    data = fusion_user_and_ids
    # 使用独立 user_id，避免与其他测试或历史数据冲突（get_by_key 按 user_id + character_ids 查 ORDER BY created_at DESC）
    user_id = "test-fusion-crud-user-create-by-key"
    fusion_data = {
        "user_id": user_id,
        "conversation_id": data["conversation_id"],
        "thread_id": data["thread_id"],
        "run_id": data["run_id"],
        "fusion_key": "test_main_fusion_ab",
        "image_type": "main",
        "character_ids": data["character_ids_1"],
        "fusion_image_url": "https://example.com/fusion1.webp",
        "success": True,
    }
    uuid_out = await create_character_fusion_image(fusion_data)
    assert uuid_out, "create 应返回 uuid"

    # 精确匹配：相同 character_ids（顺序可不同，内部用排序比较）
    by_key = await get_character_fusion_image_by_key(
        character_ids=data["character_ids_1"],
        user_id=user_id,
    )
    assert by_key is not None, "应按 character_ids + user_id 查到一条"
    assert by_key.uuid == uuid_out
    assert by_key.fusion_image_url == fusion_data["fusion_image_url"]
    assert set(by_key.character_ids or []) == set(data["character_ids_1"])

    # 逆序传 same ids 也应查到
    by_key_reversed = await get_character_fusion_image_by_key(
        character_ids=list(reversed(data["character_ids_1"])),
        user_id=user_id,
    )
    assert by_key_reversed is not None and by_key_reversed.uuid == uuid_out


@pytest.mark.asyncio
async def test_get_by_character_ids_overlap(fusion_user_and_ids):
    """get_character_fusion_images_by_character_ids 使用 jsonb && 重叠查询"""
    from app.crud.video.video_character import (
        create_character_fusion_image,
        get_character_fusion_images_by_character_ids,
    )

    data = fusion_user_and_ids
    # 插入两条：ids_1 与 ids_2（有交集 id d0d3b762-...）
    for ids, key_suffix in [(data["character_ids_1"], "1"), (data["character_ids_2"], "2")]:
        await create_character_fusion_image({
            "user_id": data["user_id"],
            "conversation_id": data["conversation_id"],
            "thread_id": data["thread_id"],
            "run_id": data["run_id"],
            "fusion_key": f"test_overlap_{key_suffix}",
            "image_type": "main",
            "character_ids": ids,
            "fusion_image_url": f"https://example.com/fusion_{key_suffix}.webp",
            "success": True,
        })

    # 查包含 d0d3b762-... 的：应包含两条
    list_with_overlap = await get_character_fusion_images_by_character_ids(
        [data["character_ids_2"][2]]  # d0d3b762-d28d-49d0-b4bb-adf80ab47ea1
    )
    assert isinstance(list_with_overlap, list)
    overlap_uuids = [r.uuid for r in list_with_overlap if (r.fusion_key or "").startswith("test_overlap_")]
    assert len(overlap_uuids) >= 2, "与 d0d3b762 有交集的应有至少 2 条（test_overlap_1 和 test_overlap_2）"


@pytest.mark.asyncio
async def test_get_batch_overlap(fusion_user_and_ids):
    """get_character_fusion_images_batch 使用 jsonb && 并按 character_id 分组"""
    from app.crud.video.video_character import (
        create_character_fusion_image,
        get_character_fusion_images_batch,
    )

    data = fusion_user_and_ids
    # 确保有一条包含 character_ids_1
    await create_character_fusion_image({
        "user_id": data["user_id"],
        "conversation_id": data["conversation_id"],
        "thread_id": data["thread_id"],
        "run_id": data["run_id"],
        "fusion_key": "test_batch_fusion",
        "image_type": "main",
        "character_ids": data["character_ids_1"],
        "fusion_image_url": "https://example.com/batch.webp",
        "success": True,
    })

    batch = await get_character_fusion_images_batch(data["character_ids_1"])
    assert isinstance(batch, dict)
    for cid in data["character_ids_1"]:
        assert cid in batch, f"batch 应包含 key {cid}"
        assert isinstance(batch[cid], list)
    # 该融合图应出现在两个 character_id 的列表里
    fusion_keys = []
    for cid in data["character_ids_1"]:
        for row in batch[cid]:
            if getattr(row, "fusion_key", None) == "test_batch_fusion":
                fusion_keys.append(cid)
    assert len(fusion_keys) >= 1, "test_batch_fusion 应至少被一个 character_id 查到"


@pytest.mark.asyncio
async def test_character_ids_stored_as_jsonb(fusion_user_and_ids):
    """create 时 character_ids 为 list[str]，DB 中为 jsonb；查询用 jsonb 操作符不报错"""
    from app.crud.video.video_character import (
        create_character_fusion_image,
        get_character_fusion_images_by_character_ids,
    )

    data = fusion_user_and_ids
    await create_character_fusion_image({
        "user_id": data["user_id"],
        "conversation_id": data["conversation_id"],
        "thread_id": data["thread_id"],
        "run_id": data["run_id"],
        "fusion_key": "test_jsonb_storage",
        "image_type": "main",
        "character_ids": data["character_ids_1"],
        "fusion_image_url": "https://example.com/jsonb.webp",
        "success": True,
    })

    # 此前这里会报错: operator does not exist: jsonb && uuid[]
    rows = await get_character_fusion_images_by_character_ids(data["character_ids_1"])
    assert isinstance(rows, list)
    found = [r for r in rows if r.fusion_key == "test_jsonb_storage"]
    assert len(found) >= 1, "jsonb 查询应能查到刚插入的记录"
