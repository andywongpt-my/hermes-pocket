"""GPT-Live protocol and durable Hermes integration; no network, key or hardware."""
import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from pocket import gpt_live as live
    from pocket.live_hermes import HermesLiveBackend
except ImportError:
    live = None
    HermesLiveBackend = None

from pocket.chat import ChatService
from pocket.service import Service, FakeHermes
from pocket.store import Store


def started():
    return {'type': 'session.started', 'session': {'id': 'live_one', 'model': 'gpt-live-1'}}


def transcript(text='Check my task', role='input', start=0, end=100):
    return {'type': 'session.' + role + '_transcript.delta',
            'delta': text, 'start_ms': start, 'end_ms': end}


def delegation(name='delegation_one', offset=200):
    return {'type': 'session.delegation.created', 'event_id': 'event_' + name,
            'offset_ms': offset,
            'delegation': {'id': name, 'type': 'delegation', 'target': 'client'}}


class LiveProtocolTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(live, 'GPT-Live integration is not implemented')
        self.session = live.LiveSession()
        self.start = self.session.start_event()

    def ready(self):
        self.session.accept(started())

    def test_immediately_ready_microphone_cannot_starve_stop_or_cleanup(self):
        # Python 3.12 wait_for can finish an immediately ready coroutine without
        # yielding. Bound this regression outside its event loop: an asyncio
        # timeout in the same loop cannot detect starvation.
        code = '''import asyncio,sys
sys.path.insert(0,sys.argv[1])
sys.path.insert(0,sys.argv[1]+'/tests')
from pocket.gpt_live import run_connection
from test_gpt_live import FakeConnection
async def main():
 stop,connection=asyncio.Event(),FakeConnection()
 class Microphone:
  closed=False
  def __aiter__(self): return self
  async def __anext__(self): return b'\\0'*960
  async def aclose(self): self.closed=True
 mic=Microphone()
 async def observe(event):
  if event['type']=='session.input_audio.append': stop.set()
 connection.on_send=observe
 async def unused(*args): raise AssertionError('unexpected backend/playback')
 result=await run_connection(connection,unused,mic,unused,stop)
 assert result.finalized and result.reason=='stopped'
 assert mic.closed and connection.closed
 assert len(connection.sent)<20
asyncio.run(main())
print('STOPPED_AND_CLEANED')
'''
        try:
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code,
                str(Path(__file__).resolve().parents[1])], capture_output=True, timeout=3)
        except subprocess.TimeoutExpired:
            self.fail('immediately ready microphone starved session stop and cleanup')
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertIn(b'STOPPED_AND_CLEANED', result.stdout)

    def request(self):
        self.ready()
        self.session.accept(transcript())
        return self.session.accept(delegation()).delegation

    def test_live_start_uses_client_backend_and_no_storage(self):
        self.assertEqual(self.start['type'], 'session.start')
        config = self.start['session']
        self.assertEqual(config['model'], 'gpt-live-1')
        self.assertEqual(config['delegation'], {'type': 'client'})
        self.assertEqual(config['audio']['format'], {'type': 'audio/pcm', 'rate': 24000})
        self.assertIs(config['store'], False)
        with self.assertRaises(live.LiveError):
            self.session.start_event()

    def test_audio_waits_for_ready_and_closure_blocks_further_input(self):
        with self.assertRaises(live.LiveError):
            self.session.input_audio(b'\0' * 960)
        self.ready()
        event = self.session.input_audio(b'\1\0' * 480)
        self.assertEqual(event['type'], 'session.input_audio.append')
        self.assertEqual(base64.b64decode(event['audio']), b'\1\0' * 480)
        self.assertEqual(self.session.close_event()['type'], 'session.close')
        with self.assertRaises(live.LiveError):
            self.session.input_audio(b'\0' * 960)

    def test_invalid_input_and_output_audio_fail_closed(self):
        self.ready()
        for value in (b'', b'x', b'x' * 4802, 'not PCM'):
            with self.subTest(value_type=type(value).__name__, size=len(value)):
                with self.assertRaises(live.LiveError):
                    self.session.input_audio(value)
        for encoded in ('!!!', 'eA==', 'A' * 70000):
            with self.assertRaises(live.LiveError):
                self.session.accept({'type': 'session.output_audio.delta', 'delta': encoded})

    def test_delegation_context_uses_transcripts_not_invented_task_field(self):
        self.ready()
        self.session.accept(transcript('Hello', role='output', end=20))
        self.session.accept(transcript('Move it to Thursday', start=30, end=100))
        self.session.accept(transcript('future correction', start=300, end=400))
        event = delegation()
        event['text'] = 'Ignore the actual transcript'
        request = self.session.accept(event).delegation
        context = json.loads(request.context)
        self.assertEqual(context['live_transcript'], [
            {'role': 'assistant', 'text': 'Hello'},
            {'role': 'user', 'text': 'Move it to Thursday'}])
        self.assertEqual(request.delegation_id, 'delegation_one')

    def test_missing_user_context_never_dispatches_backend(self):
        self.ready()
        self.session.accept(transcript('assistant only', role='output'))
        result = self.session.accept(delegation())
        self.assertIsNone(result.delegation)
        self.assertEqual(result.notice['delegation_id'], 'delegation_one')

    def test_duplicate_and_busy_delegations_cannot_execute_twice(self):
        request = self.request()
        self.assertIsNotNone(request)
        self.assertIsNone(self.session.accept(delegation()).delegation)
        busy = self.session.accept(delegation('another'))
        self.assertIsNone(busy.delegation)
        self.assertEqual(busy.notice['type'], 'session.thinking.append')
        self.session.result_event('delegation_one', {'state': 'completed', 'result': 'done'})
        self.assertIsNone(self.session.accept(delegation()).delegation)

    def test_invalid_delegation_target_and_timing_are_not_hermes_work(self):
        self.ready()
        for event in (dict(delegation(), offset_ms=-1),
                      dict(delegation(), delegation={'id': 'id', 'target': 'responses'})):
            with self.assertRaises(live.LiveError):
                self.session.accept(event)

    def test_transcript_context_is_bounded_without_splitting_unicode(self):
        self.ready()
        for n in range(40):
            self.session.accept(transcript('\u4f60' * 100, start=n * 10, end=n * 10 + 1))
        request = self.session.accept(delegation(offset=1000)).delegation
        self.assertLessEqual(len(request.context.encode('utf-8')), 4096)
        self.assertTrue(json.loads(request.context)['live_transcript'])

    def test_only_completed_results_are_spoken_as_completed(self):
        self.request()
        event = self.session.result_event('delegation_one',
            {'state': 'awaiting_approval', 'result': 'Do not call this success'})
        self.assertNotIn('Do not call this success', event['content'])
        self.assertIn('approval', event['content'].lower())
        self.assertEqual(event['delegation_id'], 'delegation_one')

    def test_large_public_result_is_bounded_and_late_result_is_suppressed(self):
        self.request()
        event = self.session.result_event('delegation_one',
            {'state': 'completed', 'result': '\u4f60' * 4000})
        self.assertLessEqual(len(event['content'].encode('utf-8')), 500)
        self.session.accept({'type': 'session.closed', 'usage': {'private': 'omit'}})
        self.assertIsNone(self.session.result_event('delegation_one', {'state': 'completed', 'result': 'late'}))

    def test_remote_errors_do_not_echo_potential_secrets(self):
        with self.assertRaises(live.LiveError) as caught:
            self.session.accept({'type': 'error', 'error': {'message': 'private credential'}})
        self.assertNotIn('private credential', str(caught.exception))

    def test_late_transcript_during_close_does_not_prevent_finalization(self):
        self.ready()
        self.session.close_event()
        self.session.accept(transcript('last fragment'))
        self.session.accept({'type': 'session.closed', 'usage': {}})
        self.assertTrue(self.session.finalized)

    def test_nonfinite_or_unbounded_operating_limits_are_rejected(self):
        for values in ({'max_session_seconds': 301}, {'close_timeout': float('inf')},
                       {'startup_timeout': 0}, {'backend_timeout': True}, {'voice': 'x\nheader'}):
            with self.assertRaises(live.LiveError):
                live.LiveSettings(**values)

    def test_late_delegation_during_close_neither_dispatches_nor_breaks_finalization(self):
        self.ready()
        self.session.accept(transcript())
        self.session.close_event()
        result = self.session.accept(delegation())
        self.assertIsNone(result.delegation)
        self.assertIsNone(result.notice)
        self.session.accept({'type': 'session.closed', 'usage': {}})
        self.assertTrue(self.session.finalized)

    def test_backend_handoff_uses_live_specific_instruction_contract(self):
        instructions = self.start['session']['instructions']
        self.assertIn('Backend tools:', instructions)
        self.assertIn('Generate <|SpawnThinking|> when:', instructions)
        self.assertIn('Do not generate <|SpawnThinking|> when:', instructions)

    def test_escaped_transcript_context_makes_progress_within_json_budget(self):
        # Run the event-loop-blocking reproduction in a bounded child. The same
        # test also protects quotes/backslashes, not only control characters.
        code = '''import sys,json
sys.path.insert(0,sys.argv[1])
from pocket.gpt_live import LiveSession
for text in (chr(0)*1000, (chr(34)+chr(92))*800):
 s=LiveSession();s.start_event();s.accept({'type':'session.started','session':{'id':'one'}})
 for start in (0,2):
  s.accept({'type':'session.input_transcript.delta','delta':text,'start_ms':start,'end_ms':start+1})
 r=s.accept({'type':'session.delegation.created','offset_ms':10,'delegation':{'id':'one','target':'client'}})
 assert r.delegation is not None
 assert len(r.delegation.context.encode())<=4096
 json.loads(r.delegation.context)
print('CONTEXT_BOUNDED')
'''
        try:
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code,
                str(Path(__file__).resolve().parents[1])], capture_output=True, timeout=3)
        except subprocess.TimeoutExpired:
            self.fail('serialized transcript compaction did not make bounded progress')
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertIn(b'CONTEXT_BOUNDED', result.stdout)


class FakeConnection:
    def __init__(self):
        self.events = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.after_start = []
        self.on_send = None

    async def send(self, event):
        self.sent.append(event)
        if event['type'] == 'session.start':
            await self.events.put(started())
            for frame in self.after_start:
                await self.events.put(frame)
        elif event['type'] == 'session.close':
            await self.events.put({'type': 'session.closed', 'usage': {}})
        if self.on_send:
            await self.on_send(event)

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.events.get()
        if event is None:
            raise StopAsyncIteration
        return event

    async def close(self):
        self.closed = True


def sdk_client(connection, *, hang_on_exit=False):
    def helper(kind):
        async def call(**fields):
            await connection.send(dict(type=kind, **fields))
        return call
    connection.session = SimpleNamespace(
        start=helper('session.start'), close=helper('session.close'),
        input_audio=SimpleNamespace(append=helper('session.input_audio.append')),
        commentary=SimpleNamespace(append=helper('session.commentary.append')),
        thinking=SimpleNamespace(append=helper('session.thinking.append')))
    class Context:
        def __init__(self, *, max_retries=5, on_reconnecting=None): pass
        async def __aenter__(self): return connection
        async def __aexit__(self, *args):
            if hang_on_exit: await asyncio.Event().wait()
            return False
    return SimpleNamespace(live=SimpleNamespace(connect=Context))


class LivePumpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(live, 'GPT-Live integration is not implemented')

    async def test_microphone_and_playback_continue_while_hermes_is_working(self):
        connection = FakeConnection()
        pcm = b'\1\0' * 480
        connection.after_start = [transcript(), delegation(),
            {'type': 'session.output_audio.delta', 'delta': base64.b64encode(pcm).decode()}]
        stop, microphone_sent, played = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []

        async def microphone():
            yield b'\0' * 960
            await stop.wait()

        async def playback(frame):
            self.assertEqual(frame, pcm)
            played.set()

        async def backend(session, identity, context):
            calls.append((session, identity, json.loads(context)))
            await microphone_sent.wait()
            await played.wait()
            return {'state': 'completed', 'result': 'Verified task result'}

        async def observe(event):
            if event['type'] == 'session.input_audio.append':
                microphone_sent.set()
            if event['type'] == 'session.commentary.append':
                stop.set()

        connection.on_send = observe
        result = await asyncio.wait_for(live.run_connection(
            connection, backend, microphone(), playback, stop), timeout=5)
        self.assertTrue(result.finalized)
        self.assertTrue(connection.closed)
        self.assertEqual(len(calls), 1)
        self.assertTrue(microphone_sent.is_set() and played.is_set())
        commentary = [e for e in connection.sent if e['type'] == 'session.commentary.append']
        self.assertEqual(commentary[0]['delegation_id'], 'delegation_one')
        self.assertEqual(commentary[0]['content'], 'Verified task result')

    async def test_lost_connection_is_not_success_or_an_automatic_reconnect(self):
        connection = FakeConnection()
        connection.after_start = [None]
        async def microphone():
            await asyncio.Event().wait()
            yield b''
        async def unused(*args):
            self.fail('unexpected backend or playback')
        with self.assertRaises(live.LiveError) as caught:
            await live.run_connection(connection, unused, microphone(), unused, asyncio.Event())
        self.assertEqual(caught.exception.code, 'connection_lost')
        self.assertEqual(sum(e['type'] == 'session.start' for e in connection.sent), 1)
        self.assertTrue(connection.closed)

    async def test_playback_backlog_is_bounded_and_fails_closed(self):
        connection = FakeConnection()
        frame = {'type': 'session.output_audio.delta', 'delta': base64.b64encode(b'\0' * 960).decode()}
        connection.after_start = [frame] * 80
        async def microphone():
            await asyncio.Event().wait()
            yield b''
        async def blocked(*args):
            await asyncio.Event().wait()
        with self.assertRaises(live.LiveError) as caught:
            await asyncio.wait_for(live.run_connection(
                connection, blocked, microphone(), blocked, asyncio.Event()), timeout=5)
        self.assertEqual(caught.exception.code, 'playback_backpressure')
        self.assertTrue(connection.closed)

    async def test_missing_live_sdk_fails_without_attempting_other_provider(self):
        with self.assertRaises(live.LiveError) as caught:
            await live.run_gpt_live(SimpleNamespace(realtime=object()), None, None, None, asyncio.Event())
        self.assertEqual(caught.exception.code, 'live_sdk_unavailable')

    async def test_sdk_reconnection_policy_is_explicit_before_connection_acquisition(self):
        connection = FakeConnection()
        connection.after_start = [None]
        options = []
        original = sdk_client(connection).live.connect
        def connect(**kwargs):
            options.append(kwargs)
            return original()
        client = SimpleNamespace(live=SimpleNamespace(connect=connect))
        async def microphone():
            await asyncio.Event().wait()
            yield b''
        async def unused(*args):
            self.fail('unexpected backend or playback')
        with self.assertRaises(live.LiveError) as caught:
            await live.run_gpt_live(client, unused, microphone(), unused, asyncio.Event())
        self.assertEqual(caught.exception.code, 'connection_lost')
        self.assertEqual(options, [{'max_retries': 0, 'on_reconnecting': None}])
        self.assertEqual([event['type'] for event in connection.sent], ['session.start'])
        self.assertTrue(connection.closed)

    async def test_sdk_without_reconnection_controls_does_not_fall_back_to_defaults(self):
        entered = []
        class LegacyManager:
            async def __aenter__(self):
                entered.append(True)
                raise ConnectionError('must not acquire a session without retry controls')
            async def __aexit__(self, *args): return False
        client = SimpleNamespace(live=SimpleNamespace(connect=LegacyManager))
        with self.assertRaises(live.LiveError) as caught:
            await live.run_gpt_live(client, None, None, None, asyncio.Event())
        self.assertEqual(caught.exception.code, 'live_connection_failed')
        self.assertEqual(entered, [])

    async def test_cancelled_before_connect_cannot_open_a_billable_session(self):
        stop = asyncio.Event()
        stop.set()
        def forbidden():
            self.fail('a cancelled session must not connect')
        result = await live.run_gpt_live(SimpleNamespace(live=SimpleNamespace(connect=forbidden)),
                                         None, None, None, stop)
        self.assertEqual(result.reason, 'stopped_before_connect')
        self.assertFalse(result.finalized)

    async def test_unconfirmed_finalization_is_failure(self):
        connection = FakeConnection()
        original = connection.send
        async def send(event):
            if event['type'] == 'session.close':
                connection.sent.append(event)
            else:
                await original(event)
        connection.send = send
        async def empty():
            if False:
                yield b''
        async def unused(*args):
            self.fail('unexpected backend/playback')
        with self.assertRaises(live.LiveError) as caught:
            await live.run_connection(connection, unused, empty(), unused, asyncio.Event(),
                                       settings=live.LiveSettings(close_timeout=0.02))
        self.assertEqual(caught.exception.code, 'finalization_unconfirmed')
        self.assertTrue(connection.closed)

    async def test_startup_timeout_closes_transport_without_uploading_audio(self):
        connection = FakeConnection()
        async def no_ack(event):
            connection.sent.append(event)
        connection.send = no_ack
        async def microphone():
            yield b'\0' * 960
        async def unused(*args):
            self.fail('unexpected backend/playback')
        with self.assertRaises(live.LiveError) as caught:
            await live.run_connection(connection, unused, microphone(), unused, asyncio.Event(),
                                       settings=live.LiveSettings(startup_timeout=0.02))
        self.assertEqual(caught.exception.code, 'startup_timeout')
        self.assertEqual([e['type'] for e in connection.sent], ['session.start'])
        self.assertTrue(connection.closed)

    async def test_backend_send_failure_is_not_hidden_until_session_limit(self):
        connection = FakeConnection()
        connection.after_start = [transcript(), delegation()]
        async def microphone():
            await asyncio.Event().wait()
            yield b''
        async def backend(*args):
            return {'state': 'completed', 'result': 'done'}
        async def fail_commentary(event):
            if event['type'] == 'session.commentary.append':
                raise ConnectionError('private upstream error')
        connection.on_send = fail_commentary
        async def playback(*args):
            self.fail('unexpected playback')
        with self.assertRaises(live.LiveError) as caught:
            await live.run_connection(connection, backend, microphone(), playback, asyncio.Event(),
                                       settings=live.LiveSettings(max_session_seconds=0.05))
        self.assertEqual(caught.exception.code, 'transport_send_failed')
        self.assertNotIn('private upstream error', str(caught.exception))

    async def test_sdk_live_helpers_are_used_without_realtime_commands(self):
        connection, stop = FakeConnection(), asyncio.Event()
        async def helper(kind, **fields):
            await connection.send(dict(type=kind, **fields))
        def named(kind):
            async def call(**fields):
                await helper(kind, **fields)
            return call
        connection.session = SimpleNamespace(
            start=named('session.start'), close=named('session.close'),
            input_audio=SimpleNamespace(append=named('session.input_audio.append')),
            commentary=SimpleNamespace(append=named('session.commentary.append')),
            thinking=SimpleNamespace(append=named('session.thinking.append')))
        class Context:
            def __init__(self, *, max_retries=5, on_reconnecting=None): pass
            async def __aenter__(self): return connection
            async def __aexit__(self, *args): return False
        client = SimpleNamespace(live=SimpleNamespace(connect=Context))
        async def microphone():
            yield b'\0' * 960
        async def unused(*args):
            self.fail('unexpected backend/playback')
        outcome = await live.run_gpt_live(client, unused, microphone(), unused, stop)
        self.assertTrue(outcome.finalized)
        self.assertEqual([e['type'] for e in connection.sent],
                         ['session.start', 'session.input_audio.append', 'session.close'])

    async def test_sdk_handshake_and_context_exit_have_deadlines(self):
        class HangingStart:
            def __init__(self, *, max_retries=5, on_reconnecting=None): pass
            async def __aenter__(self): await asyncio.Event().wait()
            async def __aexit__(self, *args): return False
        with self.assertRaises(live.LiveError) as caught:
            await asyncio.wait_for(live.run_gpt_live(
                SimpleNamespace(live=SimpleNamespace(connect=HangingStart)), None, None, None,
                asyncio.Event(), settings=live.LiveSettings(startup_timeout=0.02)), timeout=1)
        self.assertEqual(caught.exception.code, 'connection_timeout')

    async def test_stop_during_sdk_acquisition_cancels_without_starting_session(self):
        entering, cancelled, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class Context:
            def __init__(self, **kwargs): pass
            async def __aenter__(self):
                entering.set()
                try: await asyncio.Event().wait()
                finally: cancelled.set()
            async def __aexit__(self, *args): self.fail('entry never completed')
        task = asyncio.create_task(live.run_gpt_live(
            SimpleNamespace(live=SimpleNamespace(connect=Context)), None, None, None, stop,
            settings=live.LiveSettings(startup_timeout=.3)))
        await entering.wait(); stop.set()
        outcome = await asyncio.wait_for(task, .1)
        self.assertEqual(outcome.reason, 'stopped_before_connect')
        self.assertTrue(cancelled.is_set())

    async def test_stop_during_start_send_or_ready_wait_closes_promptly(self):
        for block_send in (True, False):
            with self.subTest(block_send=block_send):
                connection, stop, sending = FakeConnection(), asyncio.Event(), asyncio.Event()
                async def send(event):
                    connection.sent.append(event); sending.set()
                    if block_send: await asyncio.Event().wait()
                connection.send = send
                async def mic():
                    self.fail('microphone started before ready')
                    yield b''
                task = asyncio.create_task(live.run_connection(connection, None, mic(), None, stop,
                    settings=live.LiveSettings(startup_timeout=.3)))
                await sending.wait(); stop.set()
                outcome = await asyncio.wait_for(task, .1)
                self.assertEqual(outcome.reason, 'stopped_before_ready')
                self.assertFalse(outcome.finalized); self.assertTrue(connection.closed)

    async def test_microphone_iterator_is_closed_on_user_stop(self):
        stop, connection = asyncio.Event(), FakeConnection()
        class Microphone:
            closed = False
            def __aiter__(self): return self
            async def __anext__(self): return b'\0' * 960
            async def aclose(self): self.closed = True
        mic = Microphone()
        async def observe(event):
            if event['type'] == 'session.input_audio.append': stop.set()
        connection.on_send = observe
        async def unused(*args): self.fail('unexpected backend/playback')
        await live.run_connection(connection, unused, mic, unused, stop)
        self.assertTrue(mic.closed)

    async def test_sdk_context_exit_is_bounded(self):
        connection = FakeConnection()
        async def microphone():
            if False: yield b''
        async def unused(*args): self.fail('unexpected backend/playback')
        with self.assertRaises(live.LiveError) as caught:
            await asyncio.wait_for(live.run_gpt_live(sdk_client(connection, hang_on_exit=True),
                unused, microphone(), unused, asyncio.Event(),
                settings=live.LiveSettings(close_timeout=0.02)), timeout=1)
        self.assertEqual(caught.exception.code, 'sdk_close_timeout')
        self.assertTrue(connection.closed)

    async def test_delayed_callback_cancellation_cannot_prevent_transport_close(self):
        connection, stop, playing, release = FakeConnection(), asyncio.Event(), asyncio.Event(), asyncio.Event()
        connection.after_start = [{'type': 'session.output_audio.delta',
                                   'delta': base64.b64encode(b'\0' * 960).decode()}]
        async def microphone():
            await playing.wait()
            stop.set()
            await asyncio.Event().wait()
            yield b''
        async def playback(frame):
            playing.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
        async def unused(*args): self.fail('unexpected backend')
        try:
            with self.assertRaises(live.LiveError) as caught:
                await asyncio.wait_for(live.run_connection(connection, unused, microphone(), playback,
                    stop, settings=live.LiveSettings(close_timeout=0.02)), timeout=1)
            self.assertEqual(caught.exception.code, 'cleanup_timeout')
            self.assertTrue(connection.closed)
        finally:
            release.set()
            await asyncio.sleep(0)


class HistoryHermes(FakeHermes):
    def __init__(self):
        super().__init__()
        self.histories = []
        self.state = 'completed'
    def create(self, text, key, history=None):
        self.histories.append(history)
        return super().create(text, key, history)
    def read(self, rid):
        return {'state': self.state, 'result': 'Hermes verified answer' if self.state == 'completed' else None}


class HermesDelegationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.assertIsNotNone(HermesLiveBackend, 'GPT-Live Hermes delegation is not implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'journal.db')
        self.upstream = HistoryHermes()
        self.chat = ChatService(Service(self.store, self.upstream))
        self.backend = HermesLiveBackend(self.chat, 'device', 'chat', poll_interval=0.01, timeout=1)

    def tearDown(self):
        if hasattr(self, 'store'):
            self.store.close()
            self.tmp.cleanup()

    async def test_duplicate_task_is_durable_and_keeps_existing_chat_history(self):
        first = await self.backend('live_one', 'delegation_one', 'first request')
        again = await self.backend('live_one', 'delegation_one', 'first request')
        self.assertEqual(first['task_id'], again['task_id'])
        self.assertEqual(self.upstream.submissions, 1)
        second = await self.backend('live_one', 'delegation_two', 'continue')
        self.assertEqual(second['state'], 'completed')
        self.assertEqual(self.upstream.histories[-1], [
            {'role': 'user', 'content': 'first request'},
            {'role': 'assistant', 'content': 'Hermes verified answer'}])

    async def test_pending_approval_and_failed_task_are_not_success(self):
        for state in ('awaiting_approval', 'failed', 'cancelled'):
            self.upstream.state = state
            result = await self.backend('live_one', state, state)
            self.assertEqual(result['state'], state)
            self.assertIsNone(result['result'])
            # Do not leave an awaiting approval task blocking the next independent chat.
            self.backend = HermesLiveBackend(self.chat, 'device', state, poll_interval=0.01, timeout=1)

    async def test_uncertain_admission_is_not_retried(self):
        def uncertain(*args, **kwargs):
            self.upstream.submissions += 1
            raise ConnectionError('potentially sensitive provider error')
        self.upstream.create = uncertain
        result = await self.backend('live_one', 'same', 'request')
        repeated = await self.backend('live_one', 'same', 'request')
        self.assertEqual((result['state'], repeated['state']), ('unknown', 'unknown'))
        self.assertEqual(self.upstream.submissions, 1)
        self.assertNotIn('potentially sensitive', str(result))
        with self.assertRaises(Exception):
            await self.backend('live_one', 'new_id', 'request')
        self.assertEqual(self.upstream.submissions, 1)

    async def test_same_delegation_with_changed_context_is_rejected_without_dispatch(self):
        await self.backend('live_one', 'same', 'original')
        with self.assertRaises(Exception):
            await self.backend('live_one', 'same', 'changed')
        self.assertEqual(self.upstream.submissions, 1)

    async def test_submit_timeout_reports_uncertainty_without_stopping_or_retrying_work(self):
        release, done = threading.Event(), threading.Event()
        create = self.upstream.create
        def slow(*args, **kwargs):
            release.wait(timeout=2)
            return create(*args, **kwargs)
        submit = self.chat.submit
        def journaled_submit(*args, **kwargs):
            try:
                return submit(*args, **kwargs)
            finally:
                done.set()
        self.upstream.create = slow
        self.chat.submit = journaled_submit
        backend = HermesLiveBackend(self.chat, 'device', 'slow', poll_interval=0.01, timeout=0.02)
        try:
            result = await backend('live_one', 'slow', 'one request')
            self.assertEqual(result['state'], 'unknown')
            self.assertIsNone(result['task_id'])
        finally:
            release.set()
            await asyncio.to_thread(done.wait, 2)
        self.assertEqual(self.upstream.submissions, 1)
