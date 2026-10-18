"""Optional evidence must neither commit nor roll back critical job mutations."""
import unittest

from pocket.chat import ChatService
from pocket.service import Service, FakeHermes
from pocket.store import Store
from pocket.voice_jobs import VoiceJobs


class NoSpeech:
    stt_enabled = True
    def __init__(self):
        self.syntheses = 0
        self.transcriptions = 0
    def transcribe(self, pcm):
        self.transcriptions += 1
        raise AssertionError('no transcription in transaction fixtures')
    def speak(self, text):
        self.syntheses += 1
        raise AssertionError('no synthesis in transaction fixtures')


class ObservationTransactionTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.backend = FakeHermes()
        self.chat = ChatService(Service(self.store, self.backend))
        self.speech = NoSpeech()
        self.voice = VoiceJobs(self.chat, self.speech, decode=lambda value: value)
        with self.store.lock, self.store.db:
            self.store.db.execute('CREATE TABLE transaction_probe (id INTEGER PRIMARY KEY, label TEXT)')

    def tearDown(self):
        self.voice.close()
        self.store.close()

    def reject_observations(self, action='ABORT', table='voice_details'):
        # The SQL names/actions are fixed synthetic test parameters.
        self.assertIn(action, ('ABORT', 'ROLLBACK'))
        self.assertIn(table, ('voice_details', 'voice_timings'))
        with self.store.lock, self.store.db:
            self.store.db.executescript('DROP TRIGGER IF EXISTS synthetic_observation_failure; '
                'CREATE TRIGGER synthetic_observation_failure BEFORE INSERT ON ' + table
                + " BEGIN SELECT RAISE(" + action + ", 'synthetic observation failure'); END;")

    def test_recovery_commits_orphan_capture_stt_and_speech_despite_failed_observations(self):
        for action in ('ABORT', 'ROLLBACK'):
            with self.subTest(action=action):
                self.voice.close()
                with self.store.lock, self.store.db:
                    self.store.db.execute("INSERT OR REPLACE INTO audio_jobs VALUES('device','capture','chat','capturing',NULL,NULL)")
                    self.store.db.execute("INSERT OR REPLACE INTO audio_jobs VALUES('device','stt','chat','processing',NULL,NULL)")
                    self.store.db.execute("INSERT OR REPLACE INTO speech_jobs VALUES('device','synthesizing','processing')")
                    self.store.db.execute("INSERT OR REPLACE INTO speech_jobs VALUES('device','playback','ready')")
                self.reject_observations(action)
                self.voice = VoiceJobs(self.chat, self.speech, decode=lambda value: value)
                audio = {row[0]: row[1] for row in self.store.db.execute('SELECT request,state FROM audio_jobs')}
                speech = {row[0]: row[1] for row in self.store.db.execute('SELECT task,state FROM speech_jobs')}
                self.assertEqual(audio, {'capture': 'unknown', 'stt': 'unknown'})
                self.assertEqual(speech, {'synthesizing': 'unavailable', 'playback': 'unavailable'})
                self.assertFalse(self.store.db.in_transaction)
                self.assertEqual((self.speech.transcriptions, self.speech.syntheses), (0, 0))
                self.voice.finish('device', 'stt')
                self.assertEqual(self.speech.transcriptions, 0)

    def test_overlength_rejection_survives_failed_observations_and_remains_explicit(self):
        text = 'x' * 4097
        for action in ('ABORT', 'ROLLBACK'):
            with self.subTest(action=action):
                rid = action.lower()
                task = self.chat.submit('device', rid, {'version': 1, 'request_id': rid, 'text': 'synthetic'})
                self.backend.complete(task['run_id'], text)
                self.reject_observations(action)
                result = self.voice.speak('device', task['request_id'])
                self.assertEqual(result['state'], 'unavailable')
                self.assertEqual(result['reason'], 'tts_text_too_long')
                self.assertEqual(result['stage'], 'synthesis')
                persisted = self.voice.speech_status('device', task['request_id'])
                self.assertEqual(persisted['state'], 'unavailable')
                self.assertEqual(persisted['reason'], 'unknown')
                self.assertEqual(self.voice.speak('device', task['request_id'])['state'], 'unavailable')
                self.assertEqual(self.store.get('device', task['request_id'])['result'], text)
                self.assertFalse(self.store.db.in_transaction)
                self.assertEqual(self.speech.syntheses, 0)

    def test_nested_observation_failure_cannot_rollback_callers_work(self):
        for index, action in enumerate(('ABORT', 'ROLLBACK')):
            with self.subTest(action=action):
                self.reject_observations(action)
                with self.store.lock, self.store.db:
                    self.store.db.execute('INSERT INTO transaction_probe VALUES(?,?)', (index, 'critical'))
                    self.voice.observations.record('device', 'audio', 'probe', 'ok', 'capture', {'capture': 1})
                    self.assertTrue(self.store.db.in_transaction)
                    self.assertEqual(self.store.db.execute('SELECT label FROM transaction_probe WHERE id=?', (index,)).fetchone()[0], 'critical')
                self.assertFalse(self.store.db.in_transaction)

    def test_nested_successful_observation_cannot_commit_callers_work(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic outer rollback'):
            with self.store.lock, self.store.db:
                self.store.db.execute("INSERT INTO transaction_probe VALUES(1,'critical')")
                self.voice.observations.record('device', 'audio', 'probe', 'ok', 'capture', {'capture': 1})
                raise RuntimeError('synthetic outer rollback')
        self.assertIsNone(self.store.db.execute('SELECT label FROM transaction_probe WHERE id=1').fetchone())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM voice_details').fetchone()[0], 0)

    def test_standalone_failed_timing_write_only_rolls_back_its_own_evidence(self):
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT INTO transaction_probe VALUES(1,'committed critical')")
        self.reject_observations('ROLLBACK', 'voice_timings')
        self.voice.observations.record('device', 'audio', 'probe', 'ok', 'capture', {'capture': 1})
        self.assertEqual(self.store.db.execute('SELECT label FROM transaction_probe WHERE id=1').fetchone()[0], 'committed critical')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM voice_details').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM voice_timings').fetchone()[0], 0)
        self.assertFalse(self.store.db.in_transaction)


if __name__ == '__main__':
    unittest.main()
