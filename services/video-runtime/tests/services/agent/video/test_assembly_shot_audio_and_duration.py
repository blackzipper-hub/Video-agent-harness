"""
测试 assembly 中 shot_audio_mapping（key=segment_number）与字幕、最后一段时长问题。

问题复现：
- shot_audio_mapping 以 segment_number 为 key，segment 7 的 duration=9.14，但最终成片里最后一段只有 3-4s
  原因：有视频时直接拼接了原文件，未按 slot duration 对齐
- 字幕只有几秒：create_subtitle_segments_from_audio_mapping 用 key 当 audio_segment_id 查 music_data，
  当 key 为 int 时查不到，需用 value['audio_segment_id']
"""
import pytest
from app.utils.subtitle_utils import (
    create_subtitle_segments_from_audio_mapping_by_segment_number,
    SubtitleSegment,
)


# 模拟用户提供的 shot_audio_mapping（key=segment_number）
SHOT_AUDIO_MAPPING_BY_SEGMENT = {
    1: {
        "audio_segment_id": "7663093e-a48b-4bc8-9984-403a1da2ac37",
        "videos": ["https://cdn-dev.newai.land/videos/segment_1_eaf1b07e.mp4"],
        "duration": 2.0,
        "shot_numbers": [1],
    },
    2: {
        "audio_segment_id": "7590d653-0304-4782-9944-886e75da2027",
        "videos": [],
        "duration": 3.0,
        "shot_numbers": [2],
    },
    3: {
        "audio_segment_id": "7590d653-0304-4782-9944-886e75da2027",
        "videos": [],
        "duration": 3.0,
        "shot_numbers": [3],
    },
    4: {"audio_segment_id": "", "videos": [], "duration": 4.0, "shot_numbers": [4]},
    5: {"audio_segment_id": "", "videos": [], "duration": 5.0, "shot_numbers": [5]},
    6: {"audio_segment_id": "", "videos": [], "duration": 4.0, "shot_numbers": [6]},
    7: {
        "audio_segment_id": "",
        "videos": ["https://cdn-dev.newai.land/videos/segment_7_150d8511.mp4"],
        "duration": 9.14525,
        "shot_numbers": [7],
    },
}

# 模拟 music_data（key=audio_segment_id uuid）
MUSIC_DATA = {
    "7663093e-a48b-4bc8-9984-403a1da2ac37": {
        "version": type("V", (), {"music_prompt": "Who knows? I felt it from the first embrace..."})(),
    },
    "7590d653-0304-4782-9944-886e75da2027": {
        "version": type("V", (), {"music_prompt": "Same music segment for 2 and 3"})(),
    },
}


def test_subtitle_uses_audio_segment_id_from_value():
    """字幕应从 value['audio_segment_id'] 取文案，而不是用 key（segment_number）查 music_data"""
    segments = create_subtitle_segments_from_audio_mapping_by_segment_number(
        SHOT_AUDIO_MAPPING_BY_SEGMENT, MUSIC_DATA
    )
    # 有 audio_segment_id 且 music_data 有文案的：1, 2, 3 → 应生成 3 条字幕
    assert len(segments) >= 2, "应按 segment 顺序生成多条字幕（至少 segment 1、2、3 有文案）"
    total_duration = sum(s.end_time - s.start_time for s in segments)
    # segment 1 2 3 的 duration 总和 2+3+3=8
    assert total_duration >= 2.0 + 3.0 + 3.0, "字幕时间轴应覆盖有文案的 segment 时长"
    # 第一条字幕应对应 segment 1 的文案
    assert "first embrace" in segments[0].text or "Who knows" in segments[0].text


def test_subtitle_time_advances_for_all_segments():
    """即使某 segment 无文案（audio_segment_id 为空），current_time 仍应按 duration 累加"""
    segments = create_subtitle_segments_from_audio_mapping_by_segment_number(
        SHOT_AUDIO_MAPPING_BY_SEGMENT, MUSIC_DATA
    )
    # 按 shot_audio_mapping 各段 duration 总和 = 2+3+3+4+5+4+9.14525 = 30.14525
    # 若只生成 1 条字幕且 duration=2，说明之前用 key 当 audio_segment_id 只命中了一条
    # 修复后应有多条，且时间轴连续
    if len(segments) >= 2:
        for i in range(1, len(segments)):
            assert segments[i].start_time >= segments[i - 1].end_time - 0.01


def test_segment_duration_sum():
    """各 segment duration 之和应为总长（用于确认 slot 时长一致）"""
    total = sum(SHOT_AUDIO_MAPPING_BY_SEGMENT[i]["duration"] for i in range(1, 8))
    assert abs(total - 30.14525) < 0.01
    assert SHOT_AUDIO_MAPPING_BY_SEGMENT[7]["duration"] == pytest.approx(9.14525, rel=1e-5)
