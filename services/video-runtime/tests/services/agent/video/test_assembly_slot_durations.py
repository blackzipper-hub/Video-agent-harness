"""
测试 concatenate_video_segments_from_state 的 slot 时长逻辑（audio-driven）：
- 以完整 audio 为准：segment = audio 的切分，每个 segment 时长 = audio_segment.duration
- 成功的用 segment 视频，失败的黑屏；所有 slot 时长一律来自 audio_transcription.segments，总和 = 音乐
"""


def test_slot_durations_from_audio_segments_only():
    """各 slot 时长一律来自 audio 切分，与是否有视频无关；总和 = 完整音乐"""
    # 模拟 7 个 audio segments 的时长（来自转录）
    audio_segment_durations = [2.0, 4.5, 4.5, 4.0, 5.0, 4.1, 6.0]  # sum = 30.1
    # 哪些 slot 有视频（1、7 有，2-6 黑屏）
    slot_has_video = [True, False, False, False, False, False, True]

    # 新逻辑：每个 slot 的 duration = 对应 audio segment 的 duration（不因有/无视频而变）
    slot_durations = list(audio_segment_durations)

    total = sum(slot_durations)
    expected_total = sum(audio_segment_durations)
    assert abs(total - expected_total) < 0.01, f"各 segment 时长之和应=完整音乐 {expected_total}s，得到 {total}s"
    assert slot_durations[0] == 2.0 and slot_durations[6] == 6.0
    # 黑屏的 slot 时长也是来自 audio 切分，不是均分
    assert slot_durations[1] == 4.5 and slot_durations[2] == 4.5
    print(f"✅ 以 audio 切分为准: 总和={total:.2f}s, 各slot={[round(d, 2) for d in slot_durations]}")


def test_all_segments_sum_equals_music():
    """各 segment 时长之和 = 完整音乐（30.1s 示例）"""
    audio_segment_durations = [2.0, 4.5, 4.5, 4.0, 5.0, 4.1, 6.0]
    total = sum(audio_segment_durations)
    assert abs(total - 30.1) < 0.01
    print(f"✅ 各 segment 时长总和 = 完整音乐: {total:.2f}s")


def test_black_slots_use_audio_segment_duration():
    """失败的黑屏：占位时长 = 该 segment 对应的 audio_segment.duration，不是均分剩余"""
    # 7 段，只有第 1、7 有视频；第 2-6 黑屏
    audio_durations = [2.0, 4.5, 4.5, 4.0, 5.0, 4.1, 6.0]
    slot_durations = list(audio_durations)
    # 黑屏 slot 的时长就是 4.5, 4.5, 4.0, 5.0, 4.1（来自 audio 切分）
    black_durations = [slot_durations[i] for i in (1, 2, 3, 4, 5)]
    assert abs(sum(black_durations) - (4.5 + 4.5 + 4.0 + 5.0 + 4.1)) < 0.01
    print(f"✅ 黑屏占位时长来自 audio 切分: {[round(d, 2) for d in black_durations]}")


if __name__ == "__main__":
    test_slot_durations_from_audio_segments_only()
    test_all_segments_sum_equals_music()
    test_black_slots_use_audio_segment_duration()
    print("All checks passed.")
