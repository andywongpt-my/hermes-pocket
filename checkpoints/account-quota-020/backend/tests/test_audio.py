import unittest
from pocket.audio import Capture, AudioError, AudioPipeline

class AudioTests(unittest.TestCase):
    def test_bounded_capture(self):
        c=Capture('windows','r1');c.append(0,b'\0\0'*160)
        self.assertEqual(c.finish(),b'\0\0'*160)
        with self.assertRaises(AudioError):c.append(1,b'\0\0')
    def test_invalid_sequence_discards(self):
        c=Capture('linux','r1')
        with self.assertRaises(AudioError):c.append(1,b'ab')
        with self.assertRaises(AudioError):c.finish()
        self.assertEqual(c.size,0)
    def test_limits(self):
        for value in (b'',b'x',b'x'*4098):
            with self.assertRaises(AudioError):Capture('linux','x').append(0,value)
        c=Capture('windows','r1',max_seconds=1)
        for i in range(8):c.append(i,b'\0'*4000)
        with self.assertRaises(AudioError):c.append(8,b'\0\0')
        self.assertEqual(c.size,0)
    def test_disconnect_discards(self):
        c=Capture('windows','r1');c.append(0,b'\0\0');c.abort()
        with self.assertRaises(AudioError):c.finish()
    def test_empty_audio(self):
        with self.assertRaises(AudioError):Capture('windows','r1').finish()
    def test_transcription_and_speech(self):
        calls=[]
        p=AudioPipeline(lambda pcm:'hello',lambda target,rid,text:calls.append((target,rid,text)),lambda text:b'\0\0')
        c=Capture('windows','r1');c.append(0,b'\0\0');p.submit(c)
        self.assertEqual(calls,[('windows','r1','hello')]);self.assertEqual(p.speak('done'),b'\0\0')
    def test_silence_no_task(self):
        p=AudioPipeline(lambda pcm:' ',lambda *args:self.fail('must not submit'),lambda text:b'')
        c=Capture('linux','r1');c.append(0,b'\0\0')
        with self.assertRaises(AudioError):p.submit(c)
    def test_provider_errors_redacted(self):
        def failure(*args):raise RuntimeError('SECRET')
        p=AudioPipeline(failure,lambda *args:None,failure)
        c=Capture('linux','r1');c.append(0,b'\0\0')
        with self.assertRaisesRegex(AudioError,'transcription unavailable'):p.submit(c)
        with self.assertRaisesRegex(AudioError,'speech unavailable'):p.speak('hello')
