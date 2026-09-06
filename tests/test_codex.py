import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from pi_eink_endpoint.codex.client import AppServerClient, AppServerError
from pi_eink_endpoint.codex.models import normalize_quota


class QuotaTests(unittest.TestCase):
    def test_selection_duration_clamping_and_timezone(self):
        result = normalize_quota({
            'rateLimits': {'primary': {'windowDurationMins': 300, 'usedPercent': 99}},
            'rateLimitsByLimitId': {'codex': {
                'secondary': {'windowDurationMins': 300, 'usedPercent': -5, 'resetsAt': 0},
                'primary': {'windowDurationMins': 10080, 'usedPercent': 120},
            }},
            'rateLimitResetCredits': {'availableCount': 0, 'credits': [1, 2]},
        })
        self.assertEqual(result.five_hour.remaining_percent, 100)
        self.assertEqual(result.weekly.remaining_percent, 0)
        self.assertEqual(result.five_hour.resets_at.hour, 9)
        self.assertEqual(result.available_resets, 0)
        self.assertIsNone(result.weekly.resets_at)
        self.assertTrue(result.mark_stale().stale)
        self.assertFalse(result.stale)

    def test_other_buckets_do_not_fall_back(self):
        for buckets in ({}, {'other': {}}):
            quota = normalize_quota({'rateLimitsByLimitId': buckets, 'rateLimits': {
                'primary': {'windowDurationMins': 300, 'usedPercent': 10}}})
            self.assertIsNone(quota.five_hour)
        self.assertIsNone(normalize_quota({'rateLimits': {'limitId': 'other'}}).five_hour)

    def test_unknowns_and_expired_reset_are_preserved(self):
        quota = normalize_quota({'rateLimits': {
            'primary': {'windowDurationMins': 300, 'usedPercent': 25, 'resetsAt': 0},
            'secondary': {'windowDurationMins': 60, 'usedPercent': 5},
        }})
        self.assertEqual(quota.five_hour.remaining_percent, 75)
        self.assertEqual(quota.five_hour.resets_at.timestamp(), 0)
        self.assertIsNone(quota.weekly)
        self.assertIsNone(quota.available_resets)

    def test_invalid_numbers_and_naive_fetch_time(self):
        quota = normalize_quota({'rateLimits': {'primary': {
            'windowDurationMins': 300, 'usedPercent': float('nan'), 'resetsAt': 1e100}},
            'rateLimitResetCredits': {'availableCount': True}})
        self.assertIsNone(quota.five_hour.remaining_percent)
        self.assertIsNone(quota.five_hour.resets_at)
        self.assertIsNone(quota.available_resets)
        with self.assertRaises(ValueError):
            normalize_quota({}, fetched_at=datetime(2026, 1, 1))
        self.assertEqual(normalize_quota({}, fetched_at=datetime(
            2026, 1, 1, tzinfo=timezone.utc)).fetched_at.hour, 9)


FAKE_SERVER = '''#!/usr/bin/env python3
import json, sys
ready = False
held = None
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get('method')
    if method == 'initialized':
        ready = True
        continue
    if method == 'initialize':
        result = {'initialized': True}
    elif not ready:
        sys.exit(2)
    elif method == 'hang':
        continue
    elif method == 'exit':
        sys.exit(0)
    elif method == 'invalid':
        print('not json', flush=True)
        continue
    elif method == 'error':
        print(json.dumps({'id': msg['id'], 'error': {'code': -1, 'message': 'SECRET'}}), flush=True)
        continue
    elif method == 'without_params':
        result = 'present' if 'params' in msg else 'omitted'
    elif method == 'first':
        held = msg['id']
        continue
    elif method == 'second':
        print(json.dumps({'method': 'account/login/completed', 'params': {'loginId': 'fake', 'success': True}}), flush=True)
        print(json.dumps({'id': msg['id'], 'result': 2}), flush=True)
        print(json.dumps({'id': held, 'result': 1}), flush=True)
        continue
    else:
        sys.stderr.write('x' * 100000)
        sys.stderr.flush()
        result = msg.get('params')
    print(json.dumps({'id': msg['id'], 'result': result}), flush=True)
'''


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        executable = Path(self.temp.name) / 'fake-codex'
        executable.write_text(FAKE_SERVER)
        executable.chmod(0o700)
        self.client = AppServerClient(str(executable), Path(self.temp.name) / 'state', timeout=1)
        self.addAsyncCleanup(self.client.close)
        await self.client.start()

    async def test_initialization_concurrent_requests_and_notifications(self):
        await self.client.start()
        first, second = await asyncio.gather(self.client.request('first'), self.client.request('second'))
        self.assertEqual((first, second), (1, 2))
        notification = await self.client.notifications.get()
        self.assertEqual(notification['params']['loginId'], 'fake')
        self.assertEqual(await self.client.request('echo', {'ok': True}), {'ok': True})
        self.assertEqual(await self.client.request('without_params'), 'omitted')
        self.assertEqual(self.client.state_dir.stat().st_mode & 0o777, 0o700)

    async def test_timeout_cancel_and_sanitized_error(self):
        self.client.timeout = 0.05
        with self.assertRaises(TimeoutError):
            await self.client.request('hang')
        self.assertEqual(self.client._pending, {})
        self.client.timeout = 1
        with self.assertRaises(AppServerError) as error:
            await self.client.request('error')
        self.assertNotIn('SECRET', str(error.exception))
        task = asyncio.create_task(self.client.request('hang'))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.client._pending, {})
        self.assertEqual(await self.client.request('echo', 42), 42)

    async def test_exit_fails_pending_and_allows_explicit_restart(self):
        process = self.client._process
        with self.assertRaises(ConnectionError):
            await self.client.request('exit')
        await self.client.start()
        self.assertIsNot(self.client._process, process)
        self.assertEqual(await self.client.request('echo', 'restarted'), 'restarted')

    async def test_malformed_output_and_shutdown(self):
        with self.assertRaises(ConnectionError):
            await self.client.request('invalid')
        await self.client.start()
        pending = asyncio.create_task(self.client.request('hang'))
        await asyncio.sleep(0)
        process = self.client._process
        await self.client.close()
        with self.assertRaises(ConnectionError):
            await pending
        self.assertIsNotNone(process.returncode)
        self.assertEqual(self.client._tasks, [])
        await self.client.close()
