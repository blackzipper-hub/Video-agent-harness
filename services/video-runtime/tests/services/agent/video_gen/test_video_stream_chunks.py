from app.services.agent.video_gen.video_generation_agent_service import _stream_chunk_to_text


def test_stream_chunk_to_text_flattens_multimodal_content():
    content = [
        {"type": "reasoning", "text": "hidden"},
        {"type": "text", "text": "generate "},
        ["the video", {"content": " now"}],
    ]

    assert _stream_chunk_to_text(content) == "generate the video now"


def test_stream_chunk_to_text_handles_plain_and_empty_values():
    assert _stream_chunk_to_text("ready") == "ready"
    assert _stream_chunk_to_text(None) == ""
