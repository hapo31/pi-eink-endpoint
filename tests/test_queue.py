import http.client
import importlib.util
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

# Load the endpoint without importing Raspberry Pi GPIO dependencies.
spec = importlib.util.spec_from_file_location(
    'endpoint_under_test', Path(__file__).parents[1] / 'pi_eink_endpoint/main.py'
)
endpoint = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'waveshare_epd': types.SimpleNamespace(epd2in9_V3=object())}):
    spec.loader.exec_module(endpoint)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.server = endpoint.EinkServer(('127.0.0.1', 0))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def post(self, path, body):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request('POST', path, body)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

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
                status, body = self.post('/text', b'{"text":"first"}')
                self.assertEqual(status, 202)
                self.assertEqual(json.loads(body), {'message': 'E-ink update queued'})
                self.assertTrue(started.wait(2))
                self.assertEqual(self.post('/image', b'image bytes')[0], 202)
                self.assertEqual(self.post('/text', b'{"text":"last"}')[0], 202)
                self.assertEqual(calls, [('text', {'text': 'first'})])
            finally:
                release.set()
                self.server.render_queue.join()
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
            self.server.render_queue.join()

    def test_invalid_json_and_unknown_path_are_not_queued(self):
        with patch.object(endpoint, 'update_eink_from_text') as render:
            self.assertEqual(self.post('/text', b'{')[0], 400)
            self.assertEqual(self.post('/unknown', b'{}')[0], 404)
            self.server.render_queue.join()
            render.assert_not_called()


if __name__ == '__main__':
    unittest.main()
