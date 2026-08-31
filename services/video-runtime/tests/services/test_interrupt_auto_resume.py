from app.services.interrupt_auto_resume import (
    INTERRUPT_AUTO_RESUME_DELAY_SECONDS,
    INTERRUPT_SMART_CLIP_AUTO_RESUME_DELAY_SECONDS,
    resolve_auto_resume_delay_seconds,
)


def test_resolve_auto_resume_delay_default():
    assert resolve_auto_resume_delay_seconds({}) == INTERRUPT_AUTO_RESUME_DELAY_SECONDS
    assert resolve_auto_resume_delay_seconds({"interrupt_data": {"step": "after_music"}}) == 15


def test_resolve_auto_resume_delay_smart_clip_ready():
    payload = {
        "interrupt_data": {
            "step": "after_music",
            "smart_clip": {"status": "ready", "recommended_start_sec": 1.0},
        }
    }
    assert resolve_auto_resume_delay_seconds(payload) == INTERRUPT_SMART_CLIP_AUTO_RESUME_DELAY_SECONDS
