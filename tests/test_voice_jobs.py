import time
import unittest
from pocket.audio import AudioError
from pocket.chat import ChatService
from pocket.service import Service, FakeHermes
from pocket.store import Store, NotFound
from pocket.voice_jobs import VoiceJobs

class Speech:
    stt_enabled=True
    def __init__(self):self.transcriptions=0;self.syntheses=0
    def transcribe(self,pcm):self.transcriptions+=1;return 'hello'
    def speak(self,text):self.syntheses+=1;return b'\x00\x00'*1600

class VoiceJobsTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:');self.backend=FakeHermes()
        self.speech=Speech();self.chat=ChatService(Service(self.store,self.backend))
        self.voice=VoiceJobs(self.chat,self.speech,decode=lambda value:value)
    def tearDown(self):self.voice.close();self.store.close()
    def wait_for(self,read,expected):
        until=time.monotonic()+2
        while time.monotonic()<until:
            result=read()
            if result['state']==expected:return result
            time.sleep(.005)
        self.fail('voice job did not reach '+expected)
    def submit(self):
        self.voice.begin('dev','linux','one','chat')
        self.voice.chunk('dev','one',0,'AAA=')
        self.voice.finish('dev','one')
        return self.wait_for(lambda:self.voice.status('dev','one'),'submitted')
    def test_roundtrip_deduplicates_and_scopes_playback(self):
        job=self.submit();self.voice.finish('dev','one')
        self.assertEqual(self.speech.transcriptions,1)
        task=self.store.get('dev',job['task_id']);self.backend.complete(task['run_id'],'answer')
        self.voice.speak('dev',job['task_id'])
        self.wait_for(lambda:self.voice.speech_status('dev',job['task_id']),'ready')
        self.voice.speak('dev',job['task_id']);self.assertEqual(self.speech.syntheses,1)
        self.assertEqual(self.voice.playback_chunk('dev',job['task_id'],0)['sequence'],0)
        with self.assertRaises(NotFound):self.voice.playback_chunk('other',job['task_id'],0)
    def test_bad_chunk_aborts_without_transcription(self):
        self.voice.begin('dev','linux','one','chat')
        with self.assertRaises(AudioError):self.voice.chunk('dev','one',1,'AAA=')
        self.assertEqual(self.voice.status('dev','one')['state'],'failed')
        self.voice.finish('dev','one');self.assertEqual(self.speech.transcriptions,0)
    def test_disabled_stt_never_admits_recording(self):
        self.speech.stt_enabled=False
        with self.assertRaises(AudioError):self.voice.begin('dev','linux','one','chat')
        self.assertEqual(self.speech.transcriptions,0)
    def test_restart_marks_partial_capture_unknown(self):
        self.voice.begin('dev','linux','one','chat');self.voice.close()
        self.voice=VoiceJobs(self.chat,self.speech,decode=lambda value:value)
        self.assertEqual(self.voice.status('dev','one')['state'],'unknown')
        self.voice.finish('dev','one');self.assertEqual(self.speech.transcriptions,0)
