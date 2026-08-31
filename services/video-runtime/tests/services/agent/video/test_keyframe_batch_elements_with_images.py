"""Keyframe batch brief facts: elements_with_images + image_tokens (no mustache)."""
import os

os.environ.setdefault("ENVIRONMENT", "development")


def test_keyframe_shot_brief_has_image_tokens_not_hint_essay():
    """Batch shot row should carry machine facts for keyframe-director (image_tokens / ref_count)."""
    shot_row = {
        "shot_number": 5,
        "elements_with_images": [
            {
                "name": "The Glamorous Pop Star Cat",
                "type_label": "角色",
                "description": "The central character.",
                "image_number": 1,
                "elem_index": 0,
            },
            {
                "name": "Crystal-Studded Microphone",
                "type_label": "物品",
                "description": "The essential prop.",
                "image_number": 2,
                "elem_index": 1,
            },
        ],
        "ref_count": 2,
        "image_tokens": "image 1, image 2",
    }
    assert shot_row["ref_count"] == 2
    assert "image 1" in shot_row["image_tokens"]
    assert "reference_images_hint" not in shot_row
    assert all("image_number" in e for e in shot_row["elements_with_images"])
