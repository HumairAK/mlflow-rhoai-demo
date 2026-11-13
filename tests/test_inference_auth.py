"""Verify token rotation and fail-closed behavior without live credentials."""

import tempfile
import unittest
from pathlib import Path

import httpx

from mlflow_notes_agent.inference_auth import TokenFileAuth


class TokenFileAuthTests(unittest.TestCase):
    def test_sync_client_uses_rotated_token(self):
        seen = []

        def handler(request):
            seen.append(request.headers["Authorization"])
            return httpx.Response(200)

        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "token"
            token_file.write_text("first-token\n")
            with httpx.Client(
                auth=TokenFileAuth(str(token_file)), transport=httpx.MockTransport(handler)
            ) as client:
                client.get("https://inference.example/v1")
                token_file.write_text("rotated-token\n")
                client.get("https://inference.example/v1")
        self.assertEqual(seen, ["Bearer first-token", "Bearer rotated-token"])

    def test_empty_or_missing_file_never_sends_static_credentials(self):
        def handler(request):
            self.fail("A request with invalid token-file configuration must not be sent")

        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "token"
            token_file.write_text(" \n")
            with httpx.Client(
                auth=TokenFileAuth(str(token_file)), transport=httpx.MockTransport(handler),
                headers={"Authorization": "Bearer static-key"},
            ) as client:
                with self.assertRaises(ValueError):
                    client.get("https://inference.example/v1")
                token_file.unlink()
                with self.assertRaises(FileNotFoundError):
                    client.get("https://inference.example/v1")


class AsyncTokenFileAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_client_uses_rotated_token(self):
        seen = []

        def handler(request):
            seen.append(request.headers["Authorization"])
            return httpx.Response(200)

        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "token"
            token_file.write_text("first-token\n")
            async with httpx.AsyncClient(
                auth=TokenFileAuth(str(token_file)), transport=httpx.MockTransport(handler)
            ) as client:
                await client.get("https://inference.example/v1")
                token_file.write_text("rotated-token\n")
                await client.get("https://inference.example/v1")
        self.assertEqual(seen, ["Bearer first-token", "Bearer rotated-token"])
