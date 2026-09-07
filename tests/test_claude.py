import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from pi_eink_endpoint.claude.client import AuthenticationError
from pi_eink_endpoint.claude.models import normalize_quota
from pi_eink_endpoint.claude.service import ClaudeService


class FakeClient:
    executable = "claude"

    def __init__(self, authenticated=True):
        self.is_authenticated = authenticated
        self.login_callback = None

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
