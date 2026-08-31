"""Regenerate run metadata helpers for companion dice loading."""
from __future__ import annotations

import pytest

from app.services.task_enqueue_service import (
    _collect_character_uuids_from_character_requests,
    _collect_shot_numbers_from_keyframe_requests,
    _collect_shot_numbers_from_video_requests,
)


class _FakeKF:
    def __init__(self, shot_number: int):
        self.shot_number = shot_number


class _FakeVideo:
    def __init__(self, shot_number: int):
        self.shot_number = shot_number


class _FakeChar:
    def __init__(self, uuid: str):
        self.uuid = uuid


def test_collect_shot_numbers_from_keyframe_requests():
    assert _collect_shot_numbers_from_keyframe_requests([_FakeKF(3), _FakeKF(1), {"shot_number": 3}]) == [1, 3]


def test_collect_shot_numbers_from_video_requests():
    assert _collect_shot_numbers_from_video_requests([_FakeVideo(7), {"shot_number": 2}]) == [2, 7]


def test_collect_character_uuids_from_character_requests():
    assert _collect_character_uuids_from_character_requests([
        _FakeChar("a"),
        _FakeChar("b"),
        {"uuid": "a"},
    ]) == ["a", "b"]
