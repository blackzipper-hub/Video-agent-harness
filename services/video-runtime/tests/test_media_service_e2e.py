"""
端到端测试: VideoAgent (EC2) → ALB → Cuti-Media-Service (EKS) → S3

运行方式:
  conda run -n cuti-video-local python tests/test_media_service_e2e.py
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault(
    "MEDIA_SERVICE_URL",
    "http://internal-k8s-dev-cutimedi-5147bc7a9a-682046562.ap-southeast-2.elb.amazonaws.com:8080",
)

from app.utils import media_service_client as msc


TEST_IMAGE_URL = "https://cdn-dev.newai.land/images/29ee5574-5080-4613-8d7e-b27742540608.webp"
TEST_RUN_ID = "e2e-test-001"


async def test_healthz():
    """1. 基本连通性"""
    print("=" * 60)
    print("[1/3] 测试 healthz 连通性...")
    client = msc._get_client()
    t0 = time.time()
    resp = await client.get("/healthz")
    elapsed = (time.time() - t0) * 1000
    data = resp.json()
    print(f"  Status: {resp.status_code}")
    print(f"  Body:   {data}")
    print(f"  延迟:   {elapsed:.0f}ms")
    assert resp.status_code == 200
    assert data["status"] == "ok"
    print("  ✅ 通过\n")


async def test_image_info():
    """2. Image Info — 获取图片元信息"""
    print("[2/3] 测试 image/info...")
    print(f"  URL: {TEST_IMAGE_URL}")
    t0 = time.time()
    result = await msc.image_info(TEST_IMAGE_URL)
    elapsed = (time.time() - t0) * 1000
    print(f"  结果: {result}")
    print(f"  延迟: {elapsed:.0f}ms")
    assert "width" in result and "height" in result
    assert result["width"] > 0 and result["height"] > 0
    print("  ✅ 通过\n")
    return result


async def test_image_resize(orig_width: int, orig_height: int):
    """3. Image Resize — 下采样 + S3 上传"""
    target_w = orig_width // 2
    target_h = orig_height // 2
    print(f"[3/3] 测试 image/resize ({orig_width}x{orig_height} → {target_w}x{target_h})...")
    print(f"  URL:    {TEST_IMAGE_URL}")
    print(f"  run_id: {TEST_RUN_ID}")
    t0 = time.time()
    result = await msc.image_resize(
        image_url=TEST_IMAGE_URL,
        target_width=target_w,
        target_height=target_h,
        run_id=TEST_RUN_ID,
        fmt="webp",
        quality=85,
    )
    elapsed = (time.time() - t0) * 1000
    print(f"  结果: {result}")
    print(f"  延迟: {elapsed:.0f}ms")
    assert "result_url" in result, f"Missing result_url: {result}"
    assert result["result_url"].startswith("http")
    assert result["width"] == target_w
    assert result["height"] == target_h
    print(f"  S3 URL: {result['result_url']}")
    print("  ✅ 通过\n")


async def test_workspace_cleanup():
    """4. Workspace Cleanup"""
    print("[Bonus] 测试 workspace cleanup...")
    result = await msc.workspace_cleanup(TEST_RUN_ID)
    print(f"  结果: {result}")
    print("  ✅ 通过\n")


async def main():
    print()
    print("🚀 Cuti-Media-Service 端到端测试")
    from app.config import settings
    print(f"   MEDIA_SERVICE_URL = {settings.MEDIA_SERVICE_URL}")
    print()

    try:
        await test_healthz()
        info = await test_image_info()
        await test_image_resize(info["width"], info["height"])
        await test_workspace_cleanup()
    finally:
        await msc.close()

    print("=" * 60)
    print("🎉 所有测试通过！EC2 → EKS Media Service 连通正常")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
