"""Exact stored UTF-8 pages: no refresh, cursor registry or content cleanup."""
import base64
import json
import unittest

from pocket.store import Store, NotFound

try:
    from pocket.result_pages import ResultPages, ReplyError, result_revision
except ImportError:
    ResultPages = ReplyError = result_revision = None


def completed(store, device='device', task='one', text='reply'):
    store.admit(device, {'version': 1, 'request_id': task, 'text': 'fixture'})
    store.transition(device, task, 'dispatching')
    store.transition(device, task, 'running', run_id='run-' + task)
    return store.transition(device, task, 'completed', result=text)


class ResultPageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ResultPages, 'bounded result pager is missing')
        self.store = Store(':memory:')
        self.addCleanup(self.store.close)
        self.pages = ResultPages('linux', self.store)

    def test_exact_utf8_reconstruction_and_reverse_cursors_at_storage_limit(self):
        text = (' \r\n\t\0\x01中文🙂é' * 4000).encode()[:65536].decode('utf8', 'ignore')
        text += 'x' * (65536 - len(text.encode()))
        completed(self.store, text=text)
        page = self.pages.get('device', 'one')
        revision = page['result_revision']
        self.assertRegex(revision, r'^[0-9a-f]{64}$')
        found = []; previous = []
        while True:
            self.assertEqual(page['version'], 1)
            self.assertEqual(page['page'], len(found))
            self.assertEqual(page['result_revision'], revision)
            self.assertEqual(page['total_utf8_bytes'], 65536)
            self.assertEqual(page['utf8_bytes'], len(page['text'].encode()))
            self.assertLessEqual(page['utf8_bytes'], 768)
            self.assertLessEqual(len(json.dumps(page, ensure_ascii=False).encode()), 8000)
            found.append(page['text']); previous.append(page)
            if page['next'] is None: break
            self.assertRegex(page['next'], r'^[A-Za-z0-9_-]{1,128}$')
            page = self.pages.get('device', 'one', page['next'])
        self.assertTrue(page['eof'])
        self.assertEqual(''.join(found), text)
        for expected in reversed(previous[:-1]):
            page = self.pages.get('device', 'one', page['prev'])
            self.assertEqual(page, expected)
        self.assertIsNone(page['prev'])

    def test_empty_and_multibyte_boundaries_are_complete(self):
        for i, text in enumerate(('', 'a' * 767 + '🙂' + '中' * 400, '\0' * 65536)):
            task = 'case' + str(i); completed(self.store, task=task, text=text)
            page = self.pages.get('device', task); parts = []
            while True:
                parts.append(page['text'])
                if page['eof']: break
                page = self.pages.get('device', task, page['next'])
            self.assertEqual(''.join(parts), text)
            if not text: self.assertEqual((page['page_count'], page['utf8_bytes']), (1, 0))

    def test_revision_pins_target_device_task_sequence_and_exact_bytes(self):
        task = completed(self.store, text='a' * 900)
        revision = result_revision('linux', 'device', task)
        variants = [result_revision('other', 'device', task),
                    result_revision('linux', 'other', task),
                    result_revision('linux', 'device', {**task, 'request_id': 'other'}),
                    result_revision('linux', 'device', {**task, 'seq': task['seq'] + 1}),
                    result_revision('linux', 'device', {**task, 'result': task['result'] + '\n'})]
        self.assertTrue(all(value != revision for value in variants))
        cursor = self.pages.get('device', 'one')['next']
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE tasks SET result=?,seq=seq+1 WHERE request_id='one'", ('new',))
        with self.assertRaises(ReplyError) as raised: self.pages.get('device', 'one', cursor)
        self.assertEqual((raised.exception.status, raised.exception.code), (409, 'result_revision_changed'))

    def test_owner_check_precedes_cursor_and_completion_metadata(self):
        completed(self.store)
        with self.assertRaises(NotFound): self.pages.get('other', 'one', 'malformed')
        self.store.admit('device', {'version': 1, 'request_id': 'pending', 'text': 'fixture'})
        with self.assertRaises(ReplyError) as raised: self.pages.get('device', 'pending')
        self.assertEqual((raised.exception.status, raised.exception.code), (409, 'result_not_completed'))

    def test_cursors_reject_noncanonical_and_out_of_range_pages(self):
        task = completed(self.store)
        for value in ('', 'x' * 129, '../x', 'x=', ' ', 'é', 'A' * 47):
            with self.subTest(value=value), self.assertRaises(ReplyError) as raised:
                self.pages.get('device', 'one', value)
            self.assertEqual(raised.exception.status, 400)
        # The permitted stateless envelope carries version/page/revision only.
        raw = b'\1' + (65535).to_bytes(2, 'big') + bytes.fromhex(result_revision('linux', 'device', task))
        cursor = base64.urlsafe_b64encode(raw).decode().rstrip('=')
        with self.assertRaises(ReplyError) as raised: self.pages.get('device', 'one', cursor)
        self.assertEqual((raised.exception.status, raised.exception.code), (416, 'result_page_out_of_range'))
