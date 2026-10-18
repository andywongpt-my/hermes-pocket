"""Synthetic disk-backed reply audio and bounded generation lifecycle."""
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pocket.linux_voice import EncodedSpeech, LinuxSpeechAdapter
from pocket.audio import AudioError
from pocket.hermes_speech import HermesSpeechTransport
from pocket.store import Store, NotFound
from test_result_pages import completed

try:
    from pocket.reply_speech import ReplySpeech, ReplySpools
    from pocket.result_pages import ReplyError
    from pocket.linux_voice import decode_speech_to_file
except ImportError:
    ReplySpeech = ReplySpools = ReplyError = decode_speech_to_file = None


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now


class SyntheticSpeech:
    stt_enabled = True
    def __init__(self):
        self.calls = []; self.started = threading.Event(); self.release = None
    def speak_job(self, text, *, cancel, timeout):
        self.calls.append(text); self.started.set()
        if self.release is not None: self.release.wait(3)
        return EncodedSpeech(b'fixture', 'audio/wav', 'fixture')


def synthetic_decoder(length, *, started=None, release=None):
    def decode(speech, output, *, cancel, timeout):
        if started is not None: started.set()
        if release is not None: release.wait(3)
        # Incrementing frames expose gaps, repeats and padding across segments.
        block = b''.join(i.to_bytes(2, 'little') for i in range(32768))
        remaining = length
        while remaining:
            chunk = block[:min(remaining, len(block))]
            output.write(chunk); remaining -= len(chunk)
        return length
    return decode


class SegmentedSpeechTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ReplySpeech, 'segmented speech lifecycle is missing')
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = Store(self.root / 'tasks.sqlite'); self.addCleanup(self.store.close)
        self.clock = Clock(); self.speech = SyntheticSpeech()
        self.spools = ReplySpools({'linux': self.store}, root=self.root / 'spools', clock=self.clock)
        self.addCleanup(self.spools.close)
        self.jobs = ReplySpeech('linux', self.store, self.speech, self.spools,
                                decode=synthetic_decoder(31 * 32000))

    def create(self, task='one', text='full accepted reply', request='speak-one', revision=None):
        completed(self.store, task=task, text=text)
        return self.jobs.create('device', task, request, revision)

    def terminal(self, job, task=None):
        task = job['task_id'] if task is None else task
        until = time.monotonic() + 4
        while time.monotonic() < until:
            value = self.jobs.status('device', task, job['generation'])
            if value['state'] not in ('processing', 'cancelling'): return value
            time.sleep(.005)
        self.fail('synthetic job did not settle')

    def test_31_and_65_second_streams_have_exact_continuity_and_retry_indexes(self):
        for seconds in (31, 65):
            self.jobs.decode = synthetic_decoder(seconds * 32000)
            task = 'seconds' + str(seconds)
            job = self.terminal(self.create(task), task)
            self.assertEqual(job['state'], 'ready', job)
            self.assertEqual(job['bytes'], seconds * 32000)
            self.assertEqual(job['segment_count'], (seconds + 19) // 20)
            offset = 0; total = 0
            for segment in job['segments']:
                self.assertEqual(segment['first_frame'], offset // 2)
                self.assertEqual(segment['frames'] * 2, segment['bytes'])
                data = bytearray()
                for sequence in range(segment['chunks']):
                    chunk = self.jobs.chunk('device', task, job['generation'], segment['index'], sequence)
                    self.assertEqual(chunk, self.jobs.chunk('device', task, job['generation'], segment['index'], sequence))
                    pcm = base64.b64decode(chunk['data']); self.assertEqual(chunk['bytes'], len(pcm))
                    self.assertLessEqual(len(pcm), 4096); self.assertEqual(len(pcm) % 2, 0)
                    self.assertEqual(chunk['segment_eof'], sequence == segment['chunks'] - 1)
                    self.assertEqual(chunk['reply_eof'], segment['index'] == job['segment_count'] - 1 and chunk['segment_eof'])
                    data.extend(pcm)
                for pos in range(0, len(data), 2):
                    self.assertEqual(int.from_bytes(data[pos:pos+2], 'little'), ((offset + pos) // 2) % 32768)
                offset += len(data); total += segment['chunks']
            self.assertEqual(total, job['chunks'])
            self.assertNotIn('path', str(job))
        self.assertEqual(self.speech.calls, ['full accepted reply'] * 2)

    def test_exact_240_seconds_is_accepted_and_any_overflow_is_unavailable(self):
        for size, state in ((7680000, 'ready'), (7680002, 'unavailable')):
            self.jobs.decode = synthetic_decoder(size)
            task = 'limit' + str(size)
            job = self.terminal(self.create(task), task)
            self.assertEqual(job['state'], state, job)
            if state == 'ready': self.assertEqual((job['bytes'], job['segment_count']), (7680000, 12))
            else:
                self.assertEqual(job['reason'], 'tts_playback_limit')
                self.assertEqual((job['bytes'], job['chunks'], job['segments']), (0, 0, []))

    def test_idempotency_revision_conflicts_and_owner_isolation(self):
        job = self.terminal(self.create())
        self.assertEqual(self.jobs.create('device', 'one', 'speak-one'), job)
        self.assertEqual(self.speech.calls, ['full accepted reply'])
        for call, code in ((lambda: self.jobs.create('device','one','different'), 'speech_request_conflict'),
                           (lambda: self.jobs.create('device','one','speak-one','0'*64), 'result_revision_changed'),
                           (lambda: self.jobs.status('device','one','0'*32), 'speech_generation_changed')):
            with self.assertRaises(ReplyError) as raised: call()
            self.assertEqual((raised.exception.status, raised.exception.code), (409, code))
        with self.assertRaises(NotFound): self.jobs.status('other', 'one', job['generation'])
        with self.assertRaises(NotFound): self.jobs.chunk('other', 'one', job['generation'], 0, 0)
        with self.assertRaises(NotFound): self.jobs.cancel('other', 'one', job['generation'])

    def test_cancel_ready_removes_spool_and_never_synthesizes_again(self):
        job = self.terminal(self.create())
        code, cancelled = self.jobs.cancel('device', 'one', job['generation'])
        self.assertEqual((code, cancelled['state']), (200, 'cancelled'))
        self.assertEqual(list((self.root / 'spools').rglob('*.pcm')), [])
        with self.assertRaises(ReplyError) as raised: self.jobs.chunk('device', 'one', job['generation'], 0, 0)
        self.assertEqual((raised.exception.status, raised.exception.code), (410, 'speech_unavailable'))
        self.assertEqual(self.jobs.create('device','one','speak-one')['state'], 'cancelled')
        self.assertEqual(len(self.speech.calls), 1)

    def test_cancel_during_provider_and_decode_invalidates_before_late_result(self):
        for phase in ('provider', 'decode'):
            release = threading.Event(); started = threading.Event()
            if phase == 'provider': self.speech.release = release; started = self.speech.started
            else: self.jobs.decode = synthetic_decoder(32000, started=started, release=release)
            task = phase; job = self.create(task); self.assertTrue(started.wait(1))
            code, value = self.jobs.cancel('device', task, job['generation'])
            self.assertEqual((code, value['state']), (202, 'cancelling'))
            with self.assertRaises(ReplyError) as raised: self.jobs.chunk('device', task, job['generation'], 0, 0)
            self.assertEqual(raised.exception.status, 410)
            release.set(); value = self.terminal(job, task)
            self.assertEqual(value['state'], 'cancelled')
            self.speech.release = None; self.speech.started.clear()
        self.assertEqual(list((self.root / 'spools').rglob('*.pcm')), [])

    def test_caps_on_text_encoded_output_disk_full_and_timeouts_are_explicit(self):
        calls = len(self.speech.calls)
        job = self.terminal(self.create('long', '中' * 1366))
        self.assertEqual((job['state'], job['reason']), ('unavailable', 'tts_text_too_long'))
        self.assertEqual(len(self.speech.calls), calls)
        original = self.speech.speak_job
        self.speech.speak_job = lambda *a, **kw: EncodedSpeech(b'x' * 3145729, 'audio/wav', None)
        job = self.terminal(self.create('encoded'))
        self.assertEqual((job['state'], job['reason']), ('unavailable', 'tts_encoded_limit'))
        self.speech.speak_job = lambda *a, **kw: (_ for _ in ()).throw(TimeoutError())
        job = self.terminal(self.create('provider-timeout'))
        self.assertEqual((job['state'], job['reason']), ('unavailable', 'tts_generation_timeout'))
        self.speech.speak_job = original
        def timeout(*a, **kw): raise TimeoutError()
        self.jobs.decode = timeout
        job = self.terminal(self.create('decode-timeout'))
        self.assertEqual((job['state'], job['reason']), ('unavailable', 'tts_decode_timeout'))
        def disk_full(*a, **kw): raise OSError(28, 'private content must not escape')
        self.jobs.decode = disk_full
        job = self.terminal(self.create('disk-full'))
        self.assertEqual((job['state'], job['reason']), ('unavailable', 'tts_disk_full'))
        self.assertNotIn('private content', str(job))

    def test_global_active_ready_and_reserved_disk_admission(self):
        self.speech.release = threading.Event()
        jobs = [self.create('active' + str(i)) for i in range(3)]
        third = self.terminal(jobs[2], 'active2')
        self.assertEqual((third['state'], third['reason']), ('unavailable', 'tts_active_capacity'))
        self.speech.release.set()
        for i in range(2): self.assertEqual(self.terminal(jobs[i], 'active'+str(i))['state'], 'ready')
        self.speech.release = None
        for i in range(2): self.assertEqual(self.terminal(self.create('ready'+str(i)), 'ready'+str(i))['state'], 'ready')
        job = self.terminal(self.create('overflow'))
        self.assertEqual((job['state'], job['reason']), ('unavailable', 'tts_ready_capacity'))

    def test_expiry_valid_reads_extend_idle_but_never_absolute_and_restart_is_terminal(self):
        job = self.terminal(self.create())
        self.clock.now += 179
        self.jobs.chunk('device','one',job['generation'],0,0)
        self.clock.now += 179
        self.assertEqual(self.jobs.status('device','one',job['generation'])['state'], 'ready')
        self.clock.now += 2; self.spools.expire()
        self.assertEqual(self.jobs.status('device','one',job['generation'])['state'], 'unavailable')
        self.assertEqual(list((self.root/'spools').rglob('*.pcm')), [])
        self.assertEqual(len(self.speech.calls), 1)
        self.assertEqual(self.jobs.create('device','one','speak-one')['state'], 'unavailable')
        second = self.terminal(self.create('absolute'), 'absolute')
        for _ in range(5):
            self.clock.now += 179; self.jobs.chunk('device','absolute',second['generation'],0,0)
        self.clock.now += 6; self.spools.expire()
        self.assertEqual(self.jobs.status('device','absolute',second['generation'])['state'], 'unavailable')
        restart = self.terminal(self.create('restart'), 'restart')
        self.spools.close()
        # Model the durable active journal left by an interrupted process; an
        # orderly close has already correctly recorded tts_shutdown instead.
        with self.store.lock,self.store.db:
            self.store.db.execute("UPDATE reply_speech_v2 SET state='ready',reason=NULL WHERE task='restart'")
            self.store.db.execute("UPDATE speech_jobs SET state='ready' WHERE task='restart'")
        self.spools = ReplySpools({'linux': self.store}, root=self.root/'spools', clock=self.clock)
        self.addCleanup(self.spools.close)
        self.jobs = ReplySpeech('linux',self.store,self.speech,self.spools,decode=synthetic_decoder(32000))
        value = self.jobs.create('device','restart','speak-one')
        self.assertEqual((value['state'], value['generation']), ('unavailable', restart['generation']))
        self.assertEqual(value['reason'], 'tts_restart_loss')
        self.assertEqual(list((self.root/'spools').rglob('*.pcm')), [])

    def test_exclusive_lease_prevents_second_instance_cleanup_and_legacy_columns_unchanged(self):
        job = self.terminal(self.create())
        with self.assertRaises((OSError, RuntimeError)):
            ReplySpools({'linux': self.store}, root=self.root/'spools')
        self.assertEqual(self.jobs.status('device','one',job['generation'])['state'], 'ready')
        self.assertEqual(len(self.store.db.execute('PRAGMA table_info(speech_jobs)').fetchall()), 3)

    def test_result_changed_during_synthesis_cannot_publish_old_audio_or_resynthesize(self):
        self.speech.release=threading.Event()
        job=self.create();self.assertTrue(self.speech.started.wait(1))
        committed=threading.Event()
        def update():
            with self.store.lock,self.store.db:
                self.store.db.execute("UPDATE tasks SET result='new revision',seq=seq+1 WHERE request_id='one'")
            committed.set()
        updater=threading.Thread(target=update);updater.start();self.assertTrue(committed.wait(1));updater.join()
        self.speech.release.set();value=self.terminal(job)
        self.assertEqual((value['state'],value['reason']),('unavailable','tts_result_revision_changed'))
        with self.assertRaises(ReplyError) as raised:self.jobs.create('device','one','speak-one')
        self.assertEqual(raised.exception.code,'result_revision_changed')
        self.assertEqual(len(self.speech.calls),1)
        self.assertEqual(list((self.root/'spools').rglob('*.pcm')),[])

    def test_ready_revision_check_and_commit_exclude_an_independent_updater(self):
        checkpoint=threading.Event();attempting=threading.Event();committed=threading.Event();observed={}
        original=self.spools._persist
        def update():
            if not checkpoint.wait(2):return
            attempting.set()
            with self.store.lock,self.store.db:
                self.store.db.execute("UPDATE tasks SET result='concurrent new revision',seq=seq+1 WHERE request_id='one'")
            committed.set()
        def persist(job):
            if job.state=='ready':
                checkpoint.set()
                observed['attempted']=attempting.wait(1)
                observed['changed_before_commit']=committed.wait(.15)
            original(job)
        updater=threading.Thread(target=update);updater.start()
        with patch.object(self.spools,'_persist',side_effect=persist):
            job=self.terminal(self.create())
        updater.join(timeout=2)
        self.assertTrue(checkpoint.is_set());self.assertTrue(observed.get('attempted'))
        self.assertFalse(observed.get('changed_before_commit'),
            'independent stored-result update slipped between the final revision check and ready commit')
        self.assertTrue(committed.is_set());self.assertFalse(updater.is_alive())
        self.assertEqual(job['state'],'ready')

    def test_disk_budget_rejection_is_terminal_and_active_marker_covers_cancellation(self):
        class Usage:free=0
        with patch('pocket.reply_speech.shutil.disk_usage',return_value=Usage()):
            value=self.create('disk-budget')
        self.assertEqual((value['state'],value['reason']),('unavailable','tts_disk_budget'))
        self.assertEqual(self.speech.calls,[])
        self.speech.release=threading.Event()
        job=self.create('blocked');self.assertTrue(self.speech.started.wait(1))
        self.jobs.cancel('device','blocked',job['generation'])
        marker=self.store.db.execute("SELECT state FROM speech_jobs WHERE task='blocked'").fetchone()[0]
        self.assertEqual(marker,'processing')
        self.speech.release.set();self.terminal(job)
        marker=self.store.db.execute("SELECT state FROM speech_jobs WHERE task='blocked'").fetchone()[0]
        self.assertEqual(marker,'unavailable')

    def test_spool_ancestor_is_checked_before_directory_creation(self):
        import stat
        from types import SimpleNamespace
        ancestor=self.root/'ancestor';ancestor.mkdir()
        unsafe_root=ancestor/'must-not-create'
        original=Path.lstat
        def lstat(path,*args,**kwargs):
            if path==ancestor:return SimpleNamespace(st_mode=stat.S_IFLNK,st_file_attributes=0)
            return original(path,*args,**kwargs)
        with patch.object(Path,'lstat',lstat):
            with self.assertRaises(OSError):ReplySpools({'linux':self.store},root=unsafe_root)
        self.assertFalse(unsafe_root.exists(),'unsafe ancestors must be rejected before chmod/mkdir')

    def test_failed_restart_cleanup_keeps_legacy_active_marker(self):
        from pocket.reply_speech import _PrivateDirectory
        job=self.terminal(self.create())
        self.spools.close()
        with self.store.lock,self.store.db:
            self.store.db.execute("UPDATE reply_speech_v2 SET state='processing',reason=NULL WHERE task='one'")
            self.store.db.execute("UPDATE speech_jobs SET state='processing' WHERE task='one'")
        with patch.object(_PrivateDirectory,'recover',side_effect=OSError('synthetic unlink failure')):
            with self.assertRaises(OSError):ReplySpools({'linux':self.store},root=self.root/'spools')
        self.assertEqual(self.store.db.execute("SELECT state FROM speech_jobs WHERE task='one'").fetchone()[0],'processing')
        self.assertEqual(self.store.db.execute("SELECT state FROM reply_speech_v2 WHERE task='one'").fetchone()[0],'processing')

    def test_second_target_shares_global_active_admission(self):
        self.spools.close()
        second=Store(self.root/'second.sqlite');self.addCleanup(second.close)
        self.spools=ReplySpools({'linux':self.store,'second':second},root=self.root/'two-spools',clock=self.clock)
        self.addCleanup(self.spools.close)
        self.jobs=ReplySpeech('linux',self.store,self.speech,self.spools,decode=synthetic_decoder(32000))
        other=ReplySpeech('second',second,self.speech,self.spools,decode=synthetic_decoder(32000))
        self.speech.release=threading.Event()
        first=self.create('first');completed(second,task='second')
        middle=other.create('device','second','speak-second')
        third=self.create('third')
        self.assertEqual((third['state'],third['reason']),('unavailable','tts_active_capacity'))
        self.speech.release.set()
        self.assertEqual(self.terminal(first)['state'],'ready')
        until=time.monotonic()+2
        while time.monotonic()<until:
            value=other.status('device','second',middle['generation'])
            if value['state']!='processing':break
            time.sleep(.01)
        self.assertEqual(value['state'],'ready')
        self.assertEqual(len(self.speech.calls),2)

    def test_process_crash_recovery_cleans_old_pcm_and_preserves_idempotency(self):
        import json
        db=self.root/'crash.sqlite';root=self.root/'crash-spools'
        source='''import json,os,sys,time
sys.path.insert(0,'tests')
from pathlib import Path
from pocket.store import Store
from pocket.reply_speech import ReplySpools,ReplySpeech
from test_result_pages import completed
from test_segmented_speech import SyntheticSpeech,synthetic_decoder
store=Store(sys.argv[1]);completed(store)
spools=ReplySpools({'linux':store},root=Path(sys.argv[2]))
jobs=ReplySpeech('linux',store,SyntheticSpeech(),spools,decode=synthetic_decoder(32000))
value=jobs.create('device','one','once')
deadline=time.monotonic()+4
while value['state']=='processing' and time.monotonic()<deadline:
 time.sleep(.005);value=jobs.status('device','one',value['generation'])
print(json.dumps(value),flush=True)
os._exit(0)
'''
        run=subprocess.run([sys.executable,'-B','-c',source,str(db),str(root)],
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8,check=True)
        old=json.loads(run.stdout);self.assertEqual(old['state'],'ready')
        self.assertEqual(len(list(root.rglob('*.pcm'))),1)
        store=Store(db)
        try:
            spools=ReplySpools({'linux':store},root=root)
            try:
                speech=SyntheticSpeech();jobs=ReplySpeech('linux',store,speech,spools,decode=synthetic_decoder(32000))
                value=jobs.create('device','one','once')
                self.assertEqual((value['state'],value['reason'],value['generation']),
                    ('unavailable','tts_restart_loss',old['generation']))
                self.assertEqual(list(root.rglob('*.pcm')),[]);self.assertEqual(speech.calls,[])
            finally:spools.close()
        finally:store.close()

    @unittest.skipUnless(os.name=='posix','FIFO semantics on Linux')
    def test_fifo_at_pcm_name_is_rejected_without_opening_or_following_it(self):
        job=self.terminal(self.create())
        path=next((self.root/'spools').rglob('*.pcm'));path.unlink();os.mkfifo(path,0o600)
        try:
            with self.assertRaises(ReplyError) as raised:self.jobs.chunk('device','one',job['generation'],0,0)
            self.assertEqual(raised.exception.status,410)
            self.assertEqual(self.jobs.status('device','one',job['generation'])['state'],'cancelling')
        finally:path.unlink();self.spools.expire()

    @unittest.skipUnless(os.name == 'posix', 'private mode/symlink assertions on Linux')
    def test_private_spools_refuse_symlink_roots_and_do_not_touch_unrelated_files(self):
        job = self.terminal(self.create())
        files = list((self.root/'spools').rglob('*.pcm')); self.assertEqual(len(files), 1)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(files[0].parent.stat().st_mode & 0o777, 0o700)
        elsewhere = self.root/'elsewhere'; elsewhere.mkdir(); target = elsewhere/'keep'; target.write_bytes(b'keep')
        link = self.root/'link'; link.symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises((OSError, RuntimeError)): ReplySpools({'linux':self.store}, root=link)
        self.spools.close(); self.assertEqual(target.read_bytes(), b'keep')


class V2TransportTests(unittest.TestCase):
    def test_exact_encoded_cap_accepts_base64_prefix_for_v2_preserving_legacy_limit(self):
        data=b'\0'*3145728
        result={'ok':True,'mime_type':'audio/wav','data_url':
                'data:audio/wav;base64,'+base64.b64encode(data).decode('ascii')}
        class Transport:
            def __call__(self,*args):return result
            def speak_job(self,*args,**kwargs):return result
        adapter=LinuxSpeechAdapter(Transport())
        try:value=adapter.speak_job('synthetic exact cap',cancel=threading.Event(),timeout=120)
        except AudioError:self.fail('V2 must accept the exact 3145728-byte encoded cap including its data URL prefix')
        self.assertEqual(value.data,data)
        with self.assertRaises(AudioError):adapter.speak('synthetic legacy cap')

    def test_cancelled_tts_worker_is_reaped_without_closing_resident_stt(self):
        self.assertTrue(hasattr(HermesSpeechTransport, 'speak_job'), 'per-job provider cancellation is missing')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); worker = root/'worker.py'; pins = root/'pins.json'; pins.write_text('{}')
            ready = root/'ready'
            worker.write_text('import json,pathlib,sys,time\njson.load(sys.stdin)\n'
                + 'pathlib.Path(' + repr(str(ready)) + ').write_text("ready")\ntime.sleep(30)\n')
            transport = HermesSpeechTransport(sys.executable,str(root),str(root),str(pins),worker_path=str(worker))
            class Resident:
                closed = False
                def close(self): self.closed = True
            resident = Resident(); transport._stt_pool = resident
            cancel = threading.Event(); errors = []
            def run():
                try: transport.speak_job({'text':'synthetic'},cancel=cancel,timeout=5)
                except Exception as error: errors.append(error)
            thread = threading.Thread(target=run); thread.start()
            until = time.monotonic()+3
            while not ready.exists() and time.monotonic()<until: time.sleep(.01)
            self.assertTrue(ready.exists()); cancel.set(); thread.join(timeout=3)
            self.assertFalse(thread.is_alive(), 'cancel must wait for the owned provider subtree to reap')
            self.assertTrue(errors); self.assertFalse(resident.closed)
            transport.close()


@unittest.skipUnless(shutil.which('ffmpeg'), 'existing ffmpeg required; Linux release gate runs these offline')
class RealSegmentedDecoderTests(unittest.TestCase):
    def encoded(self, seconds):
        command = [shutil.which('ffmpeg'),'-nostdin','-hide_banner','-loglevel','error',
            '-f','lavfi','-i','anullsrc=r=16000:cl=mono','-t',str(seconds),'-c:a','flac','-f','flac','pipe:1']
        data = subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,check=True,timeout=20).stdout
        self.assertLess(len(data),3145728)
        return EncodedSpeech(data,'audio/flac','offline-fixture')

    def test_real_ffmpeg_31_65_240_seconds_are_complete_with_bounded_pcm_memory(self):
        import tracemalloc
        self.assertIsNotNone(decode_speech_to_file, 'bounded file decoder is missing')
        for seconds in (31,65,240):
            encoded = self.encoded(seconds)
            with tempfile.TemporaryFile() as output:
                tracemalloc.start()
                try:
                    size=decode_speech_to_file(encoded,output,cancel=threading.Event(),timeout=20)
                    _,peak=tracemalloc.get_traced_memory()
                finally: tracemalloc.stop()
                self.assertEqual((size,output.tell()),(seconds*32000,seconds*32000))
                self.assertLess(peak,2*1024*1024,'decoder must not buffer complete PCM')

    def test_real_ffmpeg_overcap_is_explicit_and_output_never_exceeds_overshoot(self):
        self.assertIsNotNone(decode_speech_to_file, 'bounded file decoder is missing')
        with tempfile.TemporaryFile() as output:
            with self.assertRaises(Exception) as raised:
                decode_speech_to_file(self.encoded(241),output,cancel=threading.Event(),timeout=20)
            self.assertEqual(getattr(raised.exception,'reason',None),'tts_playback_limit')
            self.assertLessEqual(output.tell(),7680000+65536)

    def test_real_decoder_cancel_and_timeout_reap_the_owned_process(self):
        encoded=self.encoded(31)
        original=subprocess.Popen
        for timeout,cancelling in ((.3,False),(5,True)):
            processes=[];cancel=threading.Event();errors=[]
            def paced(command,**kwargs):
                position=command.index('-i')
                command=command[:position]+['-re']+command[position:]
                proc=original(command,**kwargs);processes.append(proc);return proc
            with tempfile.TemporaryFile() as output:
                def run():
                    try:decode_speech_to_file(encoded,output,cancel=cancel,timeout=timeout)
                    except Exception as error:errors.append(error)
                with patch('subprocess.Popen',side_effect=paced):
                    thread=threading.Thread(target=run);thread.start()
                    if cancelling:
                        until=time.monotonic()+2
                        while not processes and time.monotonic()<until:time.sleep(.005)
                        cancel.set()
                    thread.join(timeout=3)
                self.assertFalse(thread.is_alive());self.assertTrue(errors)
                self.assertTrue(all(proc.poll() is not None for proc in processes))
                if not cancelling:self.assertEqual(getattr(errors[0],'reason',None),'tts_decode_timeout')
