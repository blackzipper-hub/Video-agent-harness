"""
VideoAgent 侧所有 msc.video_trim 调用点的契约测试（mock MSC，不断言远端）。

覆盖：
- media_service_client.video_trim 默认 mode=trim_only
- video_utils.trim_video_to_duration → MSC
- video_segments_service.process_and_merge_videos（阈值对齐 + freeze_or_tail_slow / speed_adjust）
- video_segments_service.merge_and_trim_lipsync_videos（容差跳过 + freeze_or_tail_slow，不调速）
- 源码契约：assembly / generation 中的 video_trim mode（轻量静态检查）
"""
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch

import pytest

from app.services.agent.video import video_segments_service as vss
from app.utils import media_service_client as msc
from app.utils.video_utils import trim_video_to_duration


@pytest.mark.asyncio
async def test_media_service_client_video_trim_default_trim_only():
    with patch.object(msc, "_post", new_callable=AsyncMock) as post:
        post.return_value = {"result_url": "https://cdn.example/trimmed.mp4", "duration": 3.0}
        result = await msc.video_trim("https://cdn.example/in.mp4", 3.5, run_id="run123")
        assert result["result_url"].endswith("trimmed.mp4")
        post.assert_called_once()
        assert post.call_args[0][0] == "video/trim"
        body = post.call_args[0][1]
        assert body["video_url"] == "https://cdn.example/in.mp4"
        assert body["target_duration"] == 3.5
        assert body["run_id"] == "run123"
        assert body["mode"] == "trim_only"
        assert "tolerance" not in body


@pytest.mark.asyncio
async def test_media_service_client_video_trim_passes_tolerance_when_set():
    with patch.object(msc, "_post", new_callable=AsyncMock) as post:
        post.return_value = {"result_url": "u", "duration": 1.0}
        await msc.video_trim("https://x/a.mp4", 10.0, run_id="r", tolerance=0.0)
        body = post.call_args[0][1]
        assert body["tolerance"] == 0.0


@pytest.mark.asyncio
async def test_media_service_client_video_trim_explicit_pad_or_trim():
    with patch.object(msc, "_post", new_callable=AsyncMock) as post:
        post.return_value = {"result_url": "u", "duration": 1.0}
        await msc.video_trim("https://x/a.mp4", 10.0, run_id="r", mode="pad_or_trim")
        body = post.call_args[0][1]
        assert body["mode"] == "pad_or_trim"


@pytest.mark.asyncio
async def test_trim_video_to_duration_delegates_to_msc():
    with patch("app.utils.video_utils.msc.video_trim", new_callable=AsyncMock) as vt:
        vt.return_value = {"result_url": "https://out/final.mp4"}
        url = await trim_video_to_duration("https://in/x.mp4", 6.25)
        assert url == "https://out/final.mp4"
        vt.assert_called_once()
        assert vt.call_args[0][0] == "https://in/x.mp4"
        assert vt.call_args[0][1] == 6.25
        assert vt.call_args.kwargs.get("run_id")
        assert vt.call_args.kwargs.get("tolerance") == 0.0


@pytest.mark.asyncio
async def test_process_and_merge_single_video_light_align_freeze():
    with patch.object(vss.msc, "video_info", new_callable=AsyncMock) as vi, patch.object(
        vss.msc, "video_trim", new_callable=AsyncMock
    ) as vt:
        vi.return_value = {"duration": 13.0}
        vt.return_value = {"result_url": "https://s/one.mp4"}
        out = await vss.process_and_merge_videos(["https://a.mp4"], 12.5, segment_number=3)
        assert out == "https://s/one.mp4"
        vt.assert_called_once_with(
            "https://a.mp4", 12.5, ANY, mode="freeze_or_tail_slow", tolerance=0.0
        )


@pytest.mark.asyncio
async def test_process_and_merge_single_video_within_tolerance_skips_trim():
    with patch.object(vss.msc, "video_info", new_callable=AsyncMock) as vi, patch.object(
        vss.msc, "video_trim", new_callable=AsyncMock
    ) as vt:
        vi.return_value = {"duration": 12.51}
        out = await vss.process_and_merge_videos(["https://a.mp4"], 12.5, segment_number=3)
        assert out == "https://a.mp4"
        vt.assert_not_called()


@pytest.mark.asyncio
async def test_process_and_merge_large_diff_uses_speed_adjust():
    with patch.object(vss.msc, "video_info", new_callable=AsyncMock) as vi, patch.object(
        vss.msc, "video_speed_adjust", new_callable=AsyncMock
    ) as vs:
        vi.return_value = {"duration": 15.0}
        vs.return_value = {"result_url": "https://s/sped.mp4"}
        out = await vss.process_and_merge_videos(["https://a.mp4"], 10.0, segment_number=1)
        assert out == "https://s/sped.mp4"
        vs.assert_called_once_with("https://a.mp4", 10.0, run_id=ANY)


@pytest.mark.asyncio
async def test_process_and_merge_multi_concat_then_light_align():
    with patch.object(vss.msc, "video_concat", new_callable=AsyncMock) as vc, patch.object(
        vss.msc, "video_info", new_callable=AsyncMock
    ) as vi, patch.object(vss.msc, "video_trim", new_callable=AsyncMock) as vt:
        vc.return_value = {"result_url": "https://c/merged.mp4"}
        vi.return_value = {"duration": 9.5}
        vt.return_value = {"result_url": "https://c/final.mp4"}
        out = await vss.process_and_merge_videos(["https://a.mp4", "https://b.mp4"], 9.0, segment_number=2)
        assert out == "https://c/final.mp4"
        vc.assert_called_once()
        vt.assert_called_once_with(
            "https://c/merged.mp4", 9.0, ANY, mode="freeze_or_tail_slow", tolerance=0.0
        )


@pytest.mark.asyncio
async def test_process_and_merge_per_shot_pipeline_then_light_align():
    with patch.object(vss.msc, "pipeline_segment_process", new_callable=AsyncMock) as pp, patch.object(
        vss.msc, "video_info", new_callable=AsyncMock
    ) as vi, patch.object(vss.msc, "video_trim", new_callable=AsyncMock) as vt:
        pp.return_value = {"result_url": "https://p/merged.mp4"}
        vi.return_value = {"duration": 20.4}
        vt.return_value = {"result_url": "https://p/out.mp4"}
        out = await vss.process_and_merge_videos(
            ["https://s1.mp4", "https://s2.mp4"],
            20.0,
            segment_number=1,
            shot_durations=[8.0, 10.0],
        )
        assert out == "https://p/out.mp4"
        pp.assert_called_once_with(
            video_urls=["https://s1.mp4", "https://s2.mp4"],
            target_durations=[8.0, 10.0],
            run_id=ANY,
            normalize=False,
        )
        vt.assert_called_once_with(
            "https://p/merged.mp4", 20.0, ANY, mode="freeze_or_tail_slow", tolerance=0.0
        )


@pytest.mark.asyncio
async def test_merge_lipsync_single_within_tolerance_skips_trim():
    with patch.object(vss.msc, "video_trim", new_callable=AsyncMock) as vt, patch.object(
        vss.msc, "video_info", new_callable=AsyncMock
    ) as vi:
        vi.return_value = {"duration": 5.0}
        out = await vss.merge_and_trim_lipsync_videos(["https://lip.mp4"], 5.0, segment_number=1)
        assert out == "https://lip.mp4"
        vt.assert_not_called()


@pytest.mark.asyncio
async def test_merge_lipsync_single_uses_freeze_when_diff_small():
    with patch.object(vss.msc, "video_trim", new_callable=AsyncMock) as vt, patch.object(
        vss.msc, "video_info", new_callable=AsyncMock
    ) as vi:
        vt.return_value = {"result_url": "https://l/trim.mp4"}
        vi.side_effect = [
            {"duration": 5.2},
            {"duration": 5.0},
        ]
        out = await vss.merge_and_trim_lipsync_videos(["https://lip.mp4"], 5.0, segment_number=1)
        assert out == "https://l/trim.mp4"
        vt.assert_called_once_with(
            "https://lip.mp4", 5.0, ANY, mode="freeze_or_tail_slow", tolerance=0.0
        )


@pytest.mark.asyncio
async def test_merge_lipsync_multi_concat_then_freeze():
    with patch.object(vss.msc, "video_concat", new_callable=AsyncMock) as vc, patch.object(
        vss.msc, "video_trim", new_callable=AsyncMock
    ) as vt, patch.object(vss.msc, "video_info", new_callable=AsyncMock) as vi:
        vc.return_value = {"result_url": "https://l/c.mp4"}
        vt.return_value = {"result_url": "https://l/f.mp4"}
        vi.side_effect = [
            {"duration": 5.0},
            {"duration": 5.0},
            {"duration": 4.3},
            {"duration": 4.3},
            {"duration": 4.0},
            {"duration": 4.0},
        ]
        out = await vss.merge_and_trim_lipsync_videos(["https://a.mp4", "https://b.mp4"], 4.0, segment_number=2)
        assert out == "https://l/f.mp4"
        vt.assert_called_once_with(
            "https://l/c.mp4", 4.0, ANY, mode="freeze_or_tail_slow", tolerance=0.0
        )


@pytest.mark.asyncio
async def test_merge_lipsync_large_diff_freeze_no_speed():
    with patch.object(vss.msc, "video_trim", new_callable=AsyncMock) as vt, patch.object(
        vss.msc, "video_speed_adjust", new_callable=AsyncMock
    ) as vs, patch.object(vss.msc, "video_info", new_callable=AsyncMock) as vi:
        vt.return_value = {"result_url": "https://l/big.mp4"}
        vi.side_effect = [{"duration": 14.0}, {"duration": 10.0}]
        out = await vss.merge_and_trim_lipsync_videos(["https://lip.mp4"], 10.0, segment_number=1)
        assert out == "https://l/big.mp4"
        vt.assert_called_once_with(
            "https://lip.mp4", 10.0, ANY, mode="freeze_or_tail_slow", tolerance=0.0
        )
        vs.assert_not_called()


def test_video_assembly_light_align_source_uses_freeze_or_tail_slow():
    root = Path(__file__).resolve().parents[1]
    text = (root / "app/services/agent/video/video_assembly_service.py").read_text(encoding="utf-8")
    assert 'msc.video_trim(' in text
    assert 'mode="freeze_or_tail_slow"' in text


def test_video_segments_service_align_uses_freeze_or_tail_slow():
    root = Path(__file__).resolve().parents[1]
    text = (root / "app/services/agent/video/video_segments_service.py").read_text(encoding="utf-8")
    assert 'mode="freeze_or_tail_slow"' in text
    assert "SEGMENT_ALIGN_SKIP_SEC" in text


def test_video_generation_post_gen_trim_source_uses_trim_only():
    root = Path(__file__).resolve().parents[1]
    text = (root / "app/services/agent/video/video_generation_service.py").read_text(encoding="utf-8")
    assert 'msc.video_trim(' in text
    assert 'mode="trim_only"' in text
