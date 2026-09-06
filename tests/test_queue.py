import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

# Load the endpoint without importing Raspberry Pi GPIO dependencies.
spec = importlib.util.spec_from_file_location(
    'endpoint_under_test', Path(__file__).parents[1] / 'pi_eink_endpoint/main.py'
)
endpoint = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'waveshare_epd': types.SimpleNamespace(epd2in9_V3=object())}):
    spec.loader.exec_module(endpoint)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.app = endpoint.create_app()
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.worker = self.app.state.render_worker

    def post(self, path, body):
        response = self.client.post(path, content=body)
        return response.status_code, response.content

    def test_response_before_render_finishes_and_shared_fifo(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def render_text(data):
            calls.append(('text', data))
            started.set()
            if not release.wait(5):
                raise TimeoutError('Test did not release renderer')

        def render_image(data):
            calls.append(('image', data))

        with patch.object(endpoint, 'update_eink_from_text', render_text), patch.object(
            endpoint, 'update_eink_from_image', render_image
        ):
            try:
                before = time.monotonic()
                status, body = self.post('/text', b'{"text":"first"}')
                self.assertLess(time.monotonic() - before, 2)
                self.assertEqual(status, 202)
                self.assertEqual(json.loads(body), {'message': 'E-ink update queued'})
                self.assertTrue(started.wait(2))
                self.assertEqual(self.post('/image', b'image bytes')[0], 202)
                self.assertEqual(self.post('/text', b'{"text":"last"}')[0], 202)
                self.assertEqual(calls, [('text', {'text': 'first'})])
            finally:
                release.set()
                self.worker.render_queue.join()
        self.assertEqual(calls, [
            ('text', {'text': 'first'}), ('image', b'image bytes'),
            ('text', {'text': 'last'}),
        ])

    def test_render_failure_does_not_stop_queue(self):
        rendered = threading.Event()
        with patch.object(endpoint, 'update_eink_from_text', side_effect=RuntimeError('display failed')), patch.object(
            endpoint, 'update_eink_from_image', side_effect=lambda data: rendered.set()
        ), self.assertLogs(endpoint.logger, level='ERROR'):
            self.assertEqual(self.post('/text', b'{}')[0], 202)
            self.assertEqual(self.post('/image', b'image bytes')[0], 202)
            self.assertTrue(rendered.wait(2))
            self.worker.render_queue.join()

    def test_invalid_json_and_unknown_path_are_not_queued(self):
        with patch.object(endpoint, 'update_eink_from_text') as render:
            for body in (b'{', b'', b'\xff'):
                self.assertEqual(self.post('/text', body), (400, b'Invalid JSON'))
            self.assertEqual(self.post('/unknown', b'{}')[0], 404)
            self.worker.render_queue.join()
            render.assert_not_called()

    def test_json_content_type_and_raw_image_body(self):
        with patch.object(endpoint, 'update_eink_from_text') as text, patch.object(
            endpoint, 'update_eink_from_image'
        ) as image:
            response = self.client.post('/text', json={'text': 'hello'})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.headers['content-type'], 'application/json')
            response = self.client.post(
                '/image', content=b'raw image', headers={'Content-Type': 'image/png'}
            )
            self.assertEqual(response.status_code, 202)
            self.worker.render_queue.join()
            text.assert_called_once_with({'text': 'hello'})
            image.assert_called_once_with(b'raw image')

    def test_api_documentation(self):
        self.assertEqual(self.client.get('/docs').status_code, 200)
        schema = self.client.get('/openapi.json').json()
        for path in ('/text', '/image', '/codex/display/start', '/codex/login/start',
                     '/codex/refresh'):
            self.assertIn('202', schema['paths'][path]['post']['responses'])
        self.assertIn(
            'application/octet-stream',
            schema['paths']['/image']['post']['requestBody']['content'],
        )


class LifespanTests(unittest.TestCase):
    def test_shutdown_drains_accepted_jobs_and_stops_worker(self):
        app = endpoint.create_app()
        release = threading.Event()
        queued = threading.Event()
        finished = threading.Event()
        calls = []
        errors = []

        def render_text(data):
            if not release.wait(5):
                raise TimeoutError('Test did not release renderer')
            calls.append('text')

        def run_client():
            try:
                with TestClient(app) as client:
                    client.post('/text', json={})
                    client.post('/image', content=b'image')
                    queued.set()
                finished.set()
            except BaseException as exc:
                errors.append(exc)

        with patch.object(endpoint, 'update_eink_from_text', render_text), patch.object(
            endpoint, 'update_eink_from_image', side_effect=lambda data: calls.append('image')
        ):
            thread = threading.Thread(target=run_client)
            thread.start()
            try:
                self.assertTrue(queued.wait(2))
                self.assertFalse(finished.wait(0.1))
                self.assertTrue(app.state.render_worker.render_worker.is_alive())
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(finished.is_set())
        self.assertEqual(calls, ['text', 'image'])
        self.assertFalse(app.state.render_worker.render_worker.is_alive())


if __name__ == '__main__':
    unittest.main()
