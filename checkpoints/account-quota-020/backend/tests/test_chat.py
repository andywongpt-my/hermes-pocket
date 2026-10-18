import tempfile
import unittest
from pathlib import Path
from pocket.store import Store, Conflict, InvalidTransition
from pocket.service import Service, FakeHermes
from pocket.chat import ChatService

class HistoryFake(FakeHermes):
    def __init__(self):super().__init__();self.histories=[]
    def create(self,text,key,history=None):self.histories.append(history);return super().create(text,key)

class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'db')
        self.backend=HistoryFake();self.chat=ChatService(Service(self.store,self.backend))
    def tearDown(self):self.store.close();self.tmp.cleanup()
    def send(self,rid='one',text='hello',chat='chat',device='device'):
        return self.chat.submit(device,chat,{'version':1,'request_id':rid,'text':text})
    def test_history_continues(self):
        task=self.send();self.backend.complete(task['run_id'],'hello back')
        self.send('two','continue')
        self.assertEqual(self.backend.histories[-1],[{'role':'user','content':'hello'},{'role':'assistant','content':'hello back'}])
    def test_duplicate_no_dispatch(self):
        first=self.send();second=self.send();self.assertEqual(first['run_id'],second['run_id']);self.assertEqual(self.backend.submissions,1)
    def test_changed_duplicate_rejected(self):
        self.send()
        with self.assertRaises(Conflict):self.send(text='different')
    def test_busy_chat_rejected(self):
        self.send()
        with self.assertRaises(Conflict):self.send('two')
    def test_abandon_unblocks_chat_without_resubmitting(self):
        def fail(*args,**kwargs):raise ConnectionError('offline')
        create=self.backend.create;self.backend.create=fail
        task=self.send();self.backend.create=create
        self.assertEqual(self.chat.service.abandon('device',task['request_id'])['state'],'failed')
        self.send('two','next')
        self.assertEqual((self.backend.submissions,self.backend.histories[-1]),(1,[]))
    def test_abandon_rejects_tracked_task(self):
        task=self.send()
        with self.assertRaises(InvalidTransition):self.chat.service.abandon('device',task['request_id'])
    def test_transcript_survives_upstream_failure(self):
        self.send()
        def offline(rid):raise ConnectionError('offline')
        self.backend.read=offline
        self.assertEqual([r['state'] for r in self.chat.transcript('device','chat')],['queued_upstream'])
    def test_other_chat_and_device_separate(self):
        self.send();self.send(chat='other');self.send(device='other')
        self.assertEqual(self.backend.histories,[[],[],[]])
    def test_transcript(self):
        task=self.send();self.backend.complete(task['run_id'],'done')
        rows=self.chat.transcript('device','chat')
        self.assertEqual(rows[0]['result'],'done')
        self.assertEqual(self.chat.transcript('other','chat'),[])

    def test_restart_preserves_history(self):
        task=self.send();self.backend.complete(task['run_id'],'saved reply')
        self.chat.transcript('device','chat')
        self.store.close();self.store=Store(Path(self.tmp.name)/'db')
        self.chat=ChatService(Service(self.store,self.backend))
        self.send('two','next')
        self.assertEqual(self.backend.histories[-1][-1]['content'],'saved reply')
    def test_unknown_blocks_new_turn(self):
        def fail(*args,**kwargs):raise ConnectionError('offline')
        self.backend.create=fail
        self.assertEqual(self.send()['state'],'unknown')
        with self.assertRaises(Conflict):self.send('two')
