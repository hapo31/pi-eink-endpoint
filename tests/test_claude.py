import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from pi_eink_endpoint.claude.client import AuthenticationError, ClaudeClient
from pi_eink_endpoint.claude.models import normalize_quota
from pi_eink_endpoint.claude.service import ClaudeService


class FakeClient:
    executable = "claude"

    def __init__(self, authenticated=True):
        self.is_authenticated = authenticated
        self.login_callback = None
        self.authentication_codes = []

    async def authenticated(self):
        return self.is_authenticated

    async def usage(self):
        return {
            "five_hour": {"utilization": 25, "resets_at": "2026-09-07T12:00:00Z"},
            "seven_day": {"utilization": 60, "resets_at": "2026-09-10T00:00:00Z"},
        }

    async def login(self, callback):
        callback("https://claude.ai/oauth/authorize?example=1")
        self.is_authenticated = True
        return True

    async def submit_authentication_code(self, code):
        self.authentication_codes.append(code)
        return True

    async def close(self):
        pass


class NormalizeTests(unittest.TestCase):
    def test_normalizes_usage_as_remaining_percent(self):
        quota = normalize_quota({
            "five_hour": {"utilization": 25, "resets_at": "2026-09-07T12:00:00Z"},
            "seven_day": {"utilization": 60, "resets_at": "bad"},
        }, fetched_at=datetime(2026, 9, 7, tzinfo=timezone.utc))
        self.assertEqual(quota.five_hour.remaining_percent, 75)
        self.assertEqual(quota.weekly.remaining_percent, 40)
        self.assertIsNone(quota.weekly.resets_at)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_submits_code_to_pending_cli_login(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "claude"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "print('Open https://claude.ai/oauth/authorize?test=1', flush=True)\n"
                "sys.exit(0 if sys.stdin.readline().strip() == 'code-123' else 1)\n"
            )
            executable.chmod(0o700)
            client = ClaudeClient(str(executable), Path(temp) / "state", timeout=1)
            urls = []
            login = asyncio.create_task(client.login(urls.append))
            while not urls:
                await asyncio.sleep(0)
            self.assertTrue(await client.submit_authentication_code("code-123"))
            self.assertTrue(await login)
            self.assertFalse(await client.submit_authentication_code("too-late"))


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.images = []
        self.client = FakeClient()
        self.service = ClaudeService(
            self.client, lambda image, partial=False: self.images.append((image, partial)),
            state_path=Path(self.temp.name) / "state.json", interval=3600)

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    async def test_display_fetches_usage(self):
        self.service.start_display()
        await self.service._start_task
        await self.service._refresh_task
        self.assertEqual(self.service.status, "ready")
        self.assertEqual(self.service.snapshot()["quota"]["five_hour"]["remaining_percent"], 75)
        self.assertFalse(self.images[-1][1])

    async def test_login_displays_cli_authorization_url(self):
        self.client.is_authenticated = False
        self.service.start_login()
        await self.service._login_task
        self.assertEqual(self.service.status, "idle")
        self.assertTrue(self.images)

    async def test_authentication_code_is_forwarded_to_client(self):
        self.assertTrue(await self.service.submit_authentication_code(" code-123 \n"))
        self.assertEqual(self.client.authentication_codes, ["code-123"])

    async def test_empty_authentication_code_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            await self.service.submit_authentication_code("  ")

    def test_renderer_displays_official_claude_icon(self):
        image = self.service.renderer.render_quota(None, "Asia/Tokyo")
        icon = image.crop((6, 4, 28, 26))
        self.assertEqual(icon.size, (22, 22))
        self.assertIn(0, icon.get_flattened_data())
        self.assertIn(255, icon.get_flattened_data())
