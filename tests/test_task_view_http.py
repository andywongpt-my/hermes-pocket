"""Real loopback task previews stay within the Pocket wire/display budget."""
import http.client
import json
import threading
import unittest

from pocket.server import make_server
from pocket.service import FakeHermes, Service
from pocket.store import Store


class TaskViewHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.backend = FakeHermes()
        self.service = Service(self.store, self.backend)
        self.server = make_server({'linux': self.service}, {'fixture': 'device'})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.store.close()

    def preview(self, result, *, handoff=None, state='completed'):
        task = self.service.submit('device', {'version': 1, 'request_id': 'preview', 'text': 'synthetic'})
        self.backend.runs[task['run_id']] = {
            'state': 'awaiting_approval' if handoff is not None else state, 'result': result,
        }
        if handoff is not None:
            self.backend.handoff = lambda run_id: dict(handoff)
        self.client.request('GET', '/targets/linux/tasks/preview/view',
                            headers={'Authorization': 'Bearer fixture'})
        response = self.client.getresponse()
        wire = response.read()
        self.assertEqual(int(response.getheader('Content-Length')), len(wire))
        return response.status, json.loads(wire), wire

    def assert_metadata_preserved(self, preview, original):
        stored = self.store.get('device', 'preview')
        self.assertEqual(stored['result'], original)
        for key, value in stored.items():
            if key != 'result':
                self.assertEqual(preview[key], value, key)

    def test_control_heavy_result_is_bounded_and_display_safe(self):
        original = '\x01' * 3000
        code, preview, wire = self.preview(original)
        self.assertEqual(code, 200)
        self.assertLessEqual(len(wire), 8000)
        self.assertEqual(preview['result'], ' ' * 3000)
        self.assertTrue(preview['truncated'])
        self.assert_metadata_preserved(preview, original)

    def test_only_unsupported_c0_controls_are_replaced(self):
        original = 'before\x00\x01\x08\x0b\x1f\n\r\tafter'
        code, preview, wire = self.preview(original)
        self.assertEqual(code, 200)
        self.assertEqual(preview['result'], 'before     \n\r\tafter')
        self.assertTrue(preview['truncated'])
        self.assertLessEqual(len(wire), 8000)
        self.assert_metadata_preserved(preview, original)

    def test_wire_budget_includes_handoff_without_changing_metadata(self):
        original = ('\u4f60\\"\n' * 500)
        handoff = {'interface': 'hermes_desktop', 'stored_session_id': 'fixture-session', 'note': 'm' * 6200}
        code, preview, wire = self.preview(original, handoff=handoff)
        self.assertEqual(code, 200)
        self.assertLessEqual(len(wire), 8000)
        self.assertEqual(preview['handoff'], handoff)
        self.assertTrue(preview['truncated'])
        self.assertTrue(original.startswith(preview['result']))
        self.assertTrue(preview['result'])
        self.assertLess(len(preview['result'].encode()), 3000)
        self.assert_metadata_preserved(preview, original)

    def test_oversized_metadata_returns_bounded_error_and_keeps_run_id(self):
        original = 'result stays stored'
        code, error, wire = self.preview(original, handoff={'note': '\\' * 6000})
        self.assertEqual(code, 413)
        self.assertEqual(error, {'error': 'task preview metadata exceeds device limit'})
        self.assertLessEqual(len(wire), 8000)
        task = self.store.get('device', 'preview')
        self.assertEqual(task['result'], original)
        self.assertIn(task['run_id'], self.backend.runs)

    def test_normal_result_remains_unchanged(self):
        original = 'ordinary reply\nwith tabs\tand quotes "\\'
        code, preview, wire = self.preview(original)
        self.assertEqual(code, 200)
        self.assertEqual(preview['result'], original)
        self.assertFalse(preview['truncated'])
        self.assertLessEqual(len(wire), 8000)
        self.assert_metadata_preserved(preview, original)

    def test_running_missing_result_stays_null(self):
        code, preview, wire = self.preview(None, state='running')
        self.assertEqual(code, 200)
        self.assertEqual(preview['state'], 'running')
        self.assertIsNone(preview['result'])
        self.assertFalse(preview['truncated'])
        self.assertLessEqual(len(wire), 8000)
        self.assert_metadata_preserved(preview, None)

    def test_completed_empty_result_stays_known_empty(self):
        code, preview, wire = self.preview('')
        self.assertEqual(code, 200)
        self.assertEqual(preview['state'], 'completed')
        self.assertEqual(preview['result'], '')
        self.assertFalse(preview['truncated'])
        self.assertLessEqual(len(wire), 8000)
        self.assert_metadata_preserved(preview, '')

    def test_missing_result_still_enforces_metadata_budget(self):
        code, error, wire = self.preview(None, handoff={'note': '\\' * 6000})
        self.assertEqual(code, 413)
        self.assertEqual(error, {'error': 'task preview metadata exceeds device limit'})
        self.assertLessEqual(len(wire), 8000)
        self.assertIsNone(self.store.get('device', 'preview')['result'])

    def test_normal_utf8_prefix_remains_unchanged(self):
        original = '\u4f60' * 1001
        code, preview, wire = self.preview(original)
        self.assertEqual(code, 200)
        self.assertEqual(preview['result'], original.encode()[:3000].decode('utf-8', errors='ignore'))
        self.assertTrue(preview['truncated'])
        self.assertLessEqual(len(wire), 8000)
        self.assert_metadata_preserved(preview, original)


if __name__ == '__main__':
    unittest.main()
