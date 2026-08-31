from app.services.agent.image.image_generation_service import _stream_chunk_to_text


def test_stream_chunk_to_text_flattens_multimodal_content_blocks():
    assert _stream_chunk_to_text([
        {"type": "reasoning", "text": "hidden"},
        {"type": "text", "text": "portrait "},
        [{"type": "text", "text": "ready"}],
    ]) == "portrait ready"
