import pytest

from app.main import StreamReceiveFixMiddleware


@pytest.mark.asyncio
async def test_stream_receive_fix_preserves_multichunk_request_body():
    messages = iter([
        {"type": "http.request", "body": b"first", "more_body": True},
        {"type": "http.request", "body": b"second", "more_body": False},
        {"type": "http.request", "body": b"", "more_body": False},
    ])
    captured = []

    async def receive():
        return next(messages)

    async def send(_message):
        return None

    async def downstream(_scope, wrapped_receive, _send):
        captured.append(await wrapped_receive())
        captured.append(await wrapped_receive())
        captured.append(await wrapped_receive())

    middleware = StreamReceiveFixMiddleware(downstream)
    await middleware({"type": "http"}, receive, send)

    assert [item.get("body") for item in captured[:2]] == [b"first", b"second"]
    assert captured[2] == {"type": "http.disconnect"}
