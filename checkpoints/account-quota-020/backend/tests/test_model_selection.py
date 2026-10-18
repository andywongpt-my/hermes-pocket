"""Offline selection admission, retry identity, history and voice integration."""
import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pocket.chat import ChatService
from pocket.hermes import HermesAdapter
from pocket.schema import decode, ProtocolError
from pocket.server import make_server
from pocket.service import Service, FakeHermes
from pocket.store import Store, Conflict
from pocket.voice_jobs import VoiceJobs


class RecordedAPI:
    def __init__(self):
        self.posts = []
        self.results = {}
        self.fail_admission = False

    def __call__(self, method, path, body, headers):
        if (method, path) == ('GET', '/v1/capabilities'):
            return {'features': {'run_submission': True, 'run_status': True, 'run_stop': True}}
        if (method, path) == ('POST', '/v1/runs'):
            self.posts.append(json.loads(json.dumps(body)))
            if self.fail_admission:
                raise ConnectionError('synthetic lost acceptance')
            rid = 'run_' + str(len(self.posts))
            self.results[rid] = {'status': 'running', 'output': None}
            return {'run_id': rid}
        if method == 'GET' and path.startswith('/v1/runs/'):
            return self.results[path.rsplit('/', 1)[-1]]
        raise AssertionError('unexpected offline adapter operation')


class FixtureSpeech:
    stt_enabled = True

    def __init__(self):
        self.transcriptions = 0

    def transcribe(self, pcm):
        if pcm != b'\0\0':
            raise AssertionError('wrong synthetic PCM')
        self.transcriptions += 1
        return 'voice fixture'

    def speak(self, text):
        raise AssertionError('no synthesis authorized in selector tests')


def turn(rid='one', choice='glm_flash', text='fixture'):
    return {'version': 2, 'request_id': rid, 'text': text, 'model_choice': choice}


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'selection.sqlite'
        self.store = Store(self.path)
        self.api = RecordedAPI()
        self.adapter = HermesAdapter(self.api)
        self.adapter.check()
        self.service = Service(self.store, self.adapter)
        self.chat = ChatService(self.service)
        self.voice = None

    def tearDown(self):
        if self.voice:
            self.voice.close()
        self.store.close()

    def test_v2_schema_preserves_explicit_choices(self):
        for choice in ['default', 'opus', 'glm_flash']:
            value = turn(choice=choice)
            self.assertEqual(decode(json.dumps(value).encode()), value)

    def test_strict_schema_rejects_missing_extra_unknown_and_nonstring_selection(self):
        invalid = [dict(turn(), provider='ollama-cloud'), dict(turn(), model='arbitrary'),
            dict(turn(), version=True), dict(turn(), version=3),
            {k: v for k, v in turn().items() if k != 'model_choice'}]
        invalid += [dict(turn(), model_choice=v) for v in ['', 'missing', 'glm-5.3-flash', None, 1, [], {}]]
        invalid.append(dict(turn(), version=1))
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ProtocolError):
                decode(json.dumps(data).encode())

    def test_default_v1_and_v2_preserve_the_original_upstream_contract(self):
        self.service.submit('device', {'version': 1, 'request_id': 'legacy', 'text': 'old'})
        self.service.submit('device', turn('new', 'default', 'new'))
        self.assertEqual(self.api.posts, [{'input': 'old'}, {'input': 'new'}])

    def test_glm_and_opus_are_server_controlled_explicit_overrides(self):
        self.service.submit('device', turn('glm', 'glm_flash'))
        self.service.submit('device', turn('opus', 'opus'))
        self.assertEqual(self.api.posts, [
            {'input': 'fixture', 'provider': 'ollama-cloud', 'model': 'glm-5.3-flash'},
            {'input': 'fixture', 'provider': 'claude-subscription-directsdk-experimental', 'model': 'claude-opus-5-5[1m]'}])

    def test_retry_is_pinned_and_changed_choice_conflicts_without_a_second_run(self):
        first = self.chat.submit('device', 'chat', turn())
        self.assertEqual(self.chat.submit('device', 'chat', turn())['run_id'], first['run_id'])
        with self.assertRaises(Conflict):
            self.chat.submit('device', 'chat', turn(choice='opus'))
        self.assertEqual(len(self.api.posts), 1)
        self.assertEqual(self.store.model_selection('device', first['request_id']),
            {'choice_id': 'glm_flash', 'provider': 'ollama-cloud', 'model': 'glm-5.3-flash'})

    def test_restart_keeps_the_admitted_selection_and_never_resubmits(self):
        first = self.chat.submit('device', 'chat', turn())
        self.store.close()
        self.store = Store(self.path)
        self.service = Service(self.store, self.adapter)
        self.chat = ChatService(self.service)
        self.assertEqual(self.chat.submit('device', 'chat', turn())['run_id'], first['run_id'])
        with self.assertRaises(Conflict):
            self.chat.submit('device', 'chat', turn(choice='default'))
        self.assertEqual(len(self.api.posts), 1)

    def test_uncertain_admission_is_not_retried_with_a_new_or_same_selection(self):
        self.api.fail_admission = True
        first = self.chat.submit('device', 'chat', turn())
        self.assertEqual(first['state'], 'unknown')
        self.assertEqual(self.chat.submit('device', 'chat', turn())['state'], 'unknown')
        with self.assertRaises(Conflict):
            self.chat.submit('device', 'chat', turn(choice='opus'))
        self.assertEqual(len(self.api.posts), 1)

    def test_new_turn_can_switch_model_while_preserving_chat_history(self):
        first = self.chat.submit('device', 'chat', turn())
        self.api.results[first['run_id']] = {'status': 'completed', 'output': 'first answer'}
        self.chat.submit('device', 'chat', turn('two', 'opus', 'continue'))
        self.assertEqual(self.api.posts[-1], {'input': 'continue',
            'conversation_history': [{'role': 'user', 'content': 'fixture'}, {'role': 'assistant', 'content': 'first answer'}],
            'provider': 'claude-subscription-directsdk-experimental', 'model': 'claude-opus-5-5[1m]'})

    def test_v2_request_cannot_be_rebound_to_another_chat(self):
        self.chat.submit('device', 'chat', turn())
        with self.assertRaises(Conflict):
            self.chat.submit('device', 'other_chat', turn())
        self.assertEqual(len(self.api.posts), 1)

    def test_crash_before_task_admission_cannot_change_selection(self):
        with patch.object(self.service, 'submit', side_effect=KeyboardInterrupt('fixture crash')):
            with self.assertRaises(KeyboardInterrupt):
                self.chat.submit('device', 'chat', turn())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM chat_turns').fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
        self.store.close()
        self.store = Store(self.path)
        self.service = Service(self.store, self.adapter)
        self.chat = ChatService(self.service)
        with self.assertRaises(Conflict):
            self.chat.submit('device', 'chat', turn(choice='opus'))
        self.assertEqual(self.api.posts, [])
        self.chat.submit('device', 'chat', turn())
        self.assertEqual(len(self.api.posts), 1)
        self.assertEqual(self.api.posts[0]['model'], 'glm-5.3-flash')

    def test_v2_identity_cannot_be_bypassed_by_a_legacy_retry(self):
        self.chat.submit('device', 'chat', turn())
        legacy = {'version': 1, 'request_id': 'one', 'text': 'fixture'}
        for chat_id in ['chat', 'other_chat']:
            with self.subTest(chat=chat_id), self.assertRaises(Conflict):
                self.chat.submit('device', chat_id, legacy)
        self.assertEqual(len(self.api.posts), 1)

    def test_v2_request_identity_remains_device_scoped(self):
        self.chat.submit('device', 'chat', turn())
        self.chat.submit('other_device', 'other_chat', turn(choice='opus'))
        self.assertEqual(len(self.api.posts), 2)
        self.assertEqual(self.api.posts[-1]['model'], 'claude-opus-5-5[1m]')

    def test_raw_provider_injection_cannot_escape_the_adapter_allowlist(self):
        with self.assertRaises(ProtocolError):
            self.adapter.create_selected('fixture', 'key', selection={
                'choice_id': 'glm_flash', 'provider': 'arbitrary', 'model': 'glm-5.3-flash'})
        self.assertEqual(self.api.posts, [])

    def test_invalid_or_unsupported_choice_creates_no_chat_turn_or_task(self):
        for value in [turn(choice='missing'), turn(choice='glm_flash')]:
            service = Service(self.store, FakeHermes())
            chat = ChatService(service)
            with self.assertRaises(ProtocolError):
                chat.submit('device', 'chat', value)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM chat_turns').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 0)
        self.assertEqual(self.api.posts, [])

    def test_voice_begin_pins_selection_and_changed_retry_conflicts(self):
        speech = FixtureSpeech()
        self.voice = VoiceJobs(self.chat, speech, decode=lambda x: x)
        self.voice.begin('device', 'linux', 'voice', 'chat', model_choice='glm_flash')
        self.voice.begin('device', 'linux', 'voice', 'chat', model_choice='glm_flash')
        with self.assertRaises(Conflict):
            self.voice.begin('device', 'linux', 'voice', 'chat', model_choice='opus')
        self.chat.submit('device', 'other_chat', turn('other', 'opus'))
        self.voice.chunk('device', 'voice', 0, 'AAA=')
        self.voice.finish('device', 'voice')
        until = time.monotonic() + 2
        while self.voice.status('device', 'voice')['state'] == 'processing' and time.monotonic() < until:
            time.sleep(.005)
        self.assertEqual(self.voice.status('device', 'voice')['state'], 'submitted')
        self.voice.finish('device', 'voice')
        self.assertEqual(speech.transcriptions, 1)
        self.assertEqual(self.api.posts[-1], {'input': 'voice fixture', 'conversation_history': [],
            'provider': 'ollama-cloud', 'model': 'glm-5.3-flash'})

    def test_restart_partial_voice_retains_selection_without_retranscribing(self):
        speech = FixtureSpeech()
        self.voice = VoiceJobs(self.chat, speech, decode=lambda x: x)
        self.voice.begin('device', 'linux', 'voice', 'chat', model_choice='glm_flash')
        self.voice.close()
        self.voice = VoiceJobs(self.chat, speech, decode=lambda x: x)
        self.assertEqual(self.voice.status('device', 'voice')['state'], 'unknown')
        with self.assertRaises(Conflict):
            self.voice.begin('device', 'linux', 'voice', 'chat', model_choice='opus')
        self.voice.finish('device', 'voice')
        self.assertEqual(speech.transcriptions, 0)
        self.assertEqual(self.api.posts, [])

    def test_version_two_default_voice_keeps_default_upstream_body(self):
        self.voice = VoiceJobs(self.chat, FixtureSpeech(), decode=lambda x: x)
        self.voice.begin('device', 'linux', 'voice', 'chat', model_choice='default')
        self.voice.chunk('device', 'voice', 0, 'AAA=')
        self.voice.finish('device', 'voice')
        until = time.monotonic() + 2
        while self.voice.status('device', 'voice')['state'] == 'processing' and time.monotonic() < until:
            time.sleep(.005)
        self.assertEqual(self.api.posts, [{'input': 'voice fixture', 'conversation_history': []}])


class SelectionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.api = RecordedAPI()
        adapter = HermesAdapter(self.api)
        adapter.check()
        self.service = Service(self.store, adapter)
        self.server = make_server({'linux': self.service}, {'fixture': 'device'},
            speech={'linux': FixtureSpeech()}, decoder=lambda x: x)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.store.close()

    def call(self, method, path, value=None, authenticated=True):
        headers = {'Content-Type': 'application/json'}
        if authenticated:
            headers['Authorization'] = 'Bearer fixture'
        self.client.request(method, '/targets/linux/' + path,
            body=json.dumps(value) if value is not None else None, headers=headers)
        response = self.client.getresponse()
        return response.status, json.loads(response.read())

    def test_catalog_is_authenticated_opaque_and_marks_glm_unverified(self):
        self.assertEqual(self.call('GET', 'models', authenticated=False)[0], 401)
        code, data = self.call('GET', 'models')
        self.assertEqual((code, data), (200, {'version': 2, 'default_id': 'default', 'models': [
            {'id': 'default', 'name': 'Follow default', 'verified': True},
            {'id': 'opus', 'name': 'Claude Opus', 'verified': True},
            {'id': 'glm_flash', 'name': 'GLM 5.3 Flash', 'verified': False}]}))
        self.assertEqual(self.api.posts, [])

    def test_http_text_retry_and_invalid_selection(self):
        self.assertEqual(self.call('POST', 'chats/chat/messages', turn())[0], 202)
        self.assertEqual(self.call('POST', 'chats/chat/messages', turn())[0], 202)
        self.assertEqual(self.call('POST', 'chats/chat/messages', turn(choice='opus'))[0], 409)
        self.assertEqual(self.call('POST', 'chats/other/messages', turn())[0], 409)
        self.assertEqual(self.call('POST', 'chats/other/messages', turn(choice='missing'))[0], 400)
        self.assertEqual(len(self.api.posts), 1)

    def test_voice_v2_begin_validation_and_selection_reaches_text_backend(self):
        body = {'version': 2, 'chat_id': 'chat', 'model_choice': 'glm_flash'}
        self.assertEqual(self.call('POST', 'audio/voice', body)[0], 201)
        self.assertEqual(self.call('POST', 'audio/voice', dict(body, model_choice='opus'))[0], 409)
        self.assertEqual(self.call('POST', 'audio/bad', dict(body, provider='ollama-cloud'))[0], 400)
        self.assertEqual(self.call('POST', 'audio/bad', dict(body, version=True))[0], 400)
        self.assertEqual(self.call('POST', 'audio/bad', dict(body, model_choice='missing'))[0], 400)
        self.assertEqual(self.call('POST', 'audio/voice/chunk', {'sequence': 0, 'data': 'AAA='})[0], 200)
        self.assertEqual(self.call('POST', 'audio/voice/finish', {})[0], 202)
        until = time.monotonic() + 2
        while time.monotonic() < until:
            if self.call('GET', 'audio/voice')[1]['state'] == 'submitted':
                break
            time.sleep(.005)
        self.assertEqual(self.api.posts[-1]['model'], 'glm-5.3-flash')

    def test_legacy_voice_begin_remains_default(self):
        self.assertEqual(self.call('POST', 'audio/legacy', {'chat_id': 'chat'})[0], 201)
        self.call('POST', 'audio/legacy/chunk', {'sequence': 0, 'data': 'AAA='})
        self.call('POST', 'audio/legacy/finish', {})
        until = time.monotonic() + 2
        while time.monotonic() < until:
            if self.call('GET', 'audio/legacy')[1]['state'] == 'submitted':
                break
            time.sleep(.005)
        self.assertEqual(self.api.posts, [{'input': 'voice fixture', 'conversation_history': []}])
