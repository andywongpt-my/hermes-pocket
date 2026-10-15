import importlib.util
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
import wave

SPEC=importlib.util.spec_from_file_location('local_stt',Path(__file__).resolve().parents[1]/'helpers'/'pocket_local_stt.py')
class LocalSTTHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.model=self.root/'model';self.model.mkdir();(self.model/'model.bin').write_bytes(b'fixture')
        self.audio=self.root/'audio.wav'
        with wave.open(str(self.audio),'wb') as out:
            out.setparams((1,2,16000,0,'NONE','not compressed'));out.writeframes(b'\x00\x00'*1600)
        self.module=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(self.module)
    def tearDown(self):self.tmp.cleanup()
    def test_offline_bounded_model_and_transcript(self):
        calls={}
        class Model:
            def __init__(self,path,**kwargs):calls.update(kwargs)
            def transcribe(self,path,**kwargs):
                self_options=kwargs
                return iter([SimpleNamespace(text=' hello ',no_speech_prob=.1,avg_logprob=-.2)]),None
        text=self.module.transcribe(self.audio,self.model,'auto',factory=Model)
        self.assertEqual(text,'hello');self.assertTrue(calls['local_files_only'])
        self.assertEqual(calls['cpu_threads'],2);self.assertEqual(calls['device'],'cpu')
    def test_rejects_nonlocal_model_before_loading(self):
        with self.assertRaises(ValueError):self.module.transcribe(self.audio,'small','auto',factory=lambda *a,**k:self.fail('loaded'))
    def test_rejects_oversized_audio(self):
        with wave.open(str(self.audio),'wb') as out:
            out.setparams((1,2,16000,0,'NONE','not compressed'));out.writeframes(b'\x00\x00'*480001)
        with self.assertRaises(ValueError):self.module.transcribe(self.audio,self.model,'auto',factory=lambda *a,**k:self.fail('loaded'))
    def test_drops_low_confidence_silence(self):
        class Model:
            def __init__(self,*a,**k):pass
            def transcribe(self,*a,**k):return iter([SimpleNamespace(text='false words',no_speech_prob=.9,avg_logprob=-2)]),None
        with self.assertRaises(ValueError):self.module.transcribe(self.audio,self.model,'auto',factory=Model)
    def test_output_does_not_overwrite_existing_file(self):
        out=self.root/'transcript.txt';out.write_text('original')
        with self.assertRaises(FileExistsError):self.module.write_transcript(out,'new')
        self.assertEqual(out.read_text(),'original')
