from __future__ import annotations

import unittest
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.video_runtime.deepseek_client import DeepSeekHarnessClient, DeepSeekHarnessError


class DeepSeekHarnessClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_prompt_history_and_cancel_use_native_rpc(self):
        seen: list[dict] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            body = __import__("json").loads(request.content)
            seen.append(body)
            values = {
                "session.create": {"sessionId": "session-1"},
                "session.prompt": {"accepted": True},
                "session.history": {"events": [], "hasMore": False},
                "session.cancel": {"accepted": True},
            }
            return httpx.Response(200, json={
                "type": "server-response",
                "rpcId": body["rpcId"],
                "result": {"ok": True, "value": values[body["method"]]},
            })

        http = httpx.AsyncClient(
            base_url="http://deepseek.test",
            transport=httpx.MockTransport(handler),
        )
        client = DeepSeekHarnessClient("http://deepseek.test", client=http)
        self.assertEqual(await client.create_session(), "session-1")
        await client.prompt("session-1", "change shot three")
        self.assertEqual((await client.history("session-1"))["events"], [])
        await client.cancel("session-1")
        self.assertEqual(
            [item["method"] for item in seen],
            ["session.create", "session.prompt", "session.history", "session.cancel"],
        )
        await http.aclose()

    async def test_rpc_error_is_not_treated_as_success(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            body = __import__("json").loads(request.content)
            return httpx.Response(200, json={
                "type": "server-response",
                "rpcId": body["rpcId"],
                "result": {"ok": False, "error": {"code": "denied", "message": "no"}},
            })

        http = httpx.AsyncClient(
            base_url="http://deepseek.test",
            transport=httpx.MockTransport(handler),
        )
        client = DeepSeekHarnessClient("http://deepseek.test", client=http)
        with self.assertRaisesRegex(DeepSeekHarnessError, "denied"):
            await client.create_session()
        await http.aclose()
