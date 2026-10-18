"""Controlled local delays verify attribution; no provider or model is called."""
import base64
import http.client
import importlib
import importlib.util
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pocket.server import make_server
from pocket.service import FakeHermes, Service
from pocket.store import Store
from pocket.voice_jobs import VoiceJobs


# Wire schema is deliberately all integers, including capture correlation.
VERSION, SERIAL, UTC_US, CAPTURE, SEQUENCE, STATUS, REQUEST_BYTES, RESPONSE_BYTES, HEADER_US, READ_US, LOCK_US, EXEC_US, WRITE_HEADER_US, WRITE_BODY_US, TOTAL_US = range(15)


class Lines:
    def __init__(self):
        self.rows = queue.Queue()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.read_fd,self.write_fd=os.pipe()
        self.reader=threading.Thread(target=self.read_lines,daemon=True)
        self.reader.start()

    def fileno(self):
        return self.write_fd

    def read_lines(self):
        pending=b''
        try:
            while True:
                value=os.read(self.read_fd,4096)
                if not value:return
                pending+=value
                while b'\n' in pending:
                    line,pending=pending.split(b'\n',1)
                    self.write(line.decode('ascii'))
        finally:
            os.close(self.read_fd)

    def close(self):
        self.release.set()
        os.close(self.write_fd)
        self.reader.join(1)

    def write(self, value):
        self.entered.set()
        if self.block and not self.release.wait(3):
            raise TimeoutError('synthetic log stall exceeded fixture bound')
        if value.strip():
            self.rows.put(json.loads(value))
        return len(value)

    def flush(self):
        pass


class SpeechMustNotRun:
    stt_enabled = True

    def transcribe(self, pcm):
        raise AssertionError('diagnostics must not transcribe')

    def speak(self, text):
        raise AssertionError('diagnostics must not synthesize')


class ReadObserver:
    def __init__(self, source, entered, completed):
        self.source, self.entered, self.completed = source, entered, completed

    def read(self, size):
        self.entered.set()
        value = self.source.read(size)
        self.completed.set()
        return value

    def __getattr__(self, name):
        return getattr(self.source, name)


class WriteDelay:
    def __init__(self, source, fixture):
        self.source, self.fixture = source, fixture

    def write(self, value):
        if self.fixture.delay_body and value.startswith(b'{"sequence"'):
            time.sleep(.09)
        return self.source.write(value)

    def __getattr__(self, name):
        return getattr(self.source, name)


class ChunkTimingTests(unittest.TestCase):
    def setUp(self):
        self.lines = Lines()
        self.stderr_patch = patch('sys.stderr', self.lines)
        self.stderr_patch.start()
        self.store = Store(':memory:')
        self.backend = FakeHermes()
        self.voices = []
        self.read_entered, self.read_completed = threading.Event(), threading.Event()
        self.delay_headers = self.delay_body = False
        fixture = self

        class ObservedVoice(VoiceJobs):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                fixture.voices.append(self)

        with patch('pocket.server.VoiceJobs', ObservedVoice):
            self.server = make_server({'linux': Service(self.store, self.backend)},
                {'fixture-private-token': 'fixture-device'},
                speech={'linux': SpeechMustNotRun()}, decoder=lambda value: value)
        handler = self.server.RequestHandlerClass
        original_setup, original_headers = handler.setup, handler.end_headers

        def setup(instance):
            original_setup(instance)
            instance.rfile = ReadObserver(instance.rfile, fixture.read_entered, fixture.read_completed)
            instance.wfile = WriteDelay(instance.wfile, fixture)

        def end_headers(instance):
            if fixture.delay_headers and instance.path.endswith('/chunk'):
                time.sleep(.09)
            return original_headers(instance)

        handler.setup, handler.end_headers = setup, end_headers
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        self.path = '/targets/linux/audio/private-capture-name'
        self.request(self.path, {'chat_id': 'fixture-chat'}, 201)
        self.read_entered.clear()
        self.read_completed.clear()

    def tearDown(self):
        self.lines.release.set()
        self.client.close()
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()
        self.store.close()
        self.stderr_patch.stop()
        self.lines.close()

    def request(self, path, payload, expected=200):
        body = json.dumps(payload).encode()
        self.client.request('POST', path, body, {'Authorization': 'Bearer fixture-private-token', 'Content-Type': 'application/json'})
        response = self.client.getresponse()
        value = json.loads(response.read())
        self.assertEqual(response.status, expected)
        return value

    def chunk(self, sequence=0):
        return self.request(self.path + '/chunk', {'sequence': sequence, 'data': base64.b64encode(b'\x00\x00' * 2048).decode()})

    def row(self):
        try:
            row = self.lines.rows.get(timeout=1)
        except queue.Empty:
            self.fail('authenticated chunk must emit one numeric timing row')
        self.assertEqual(len(row), 15)
        self.assertTrue(all(type(value) is int for value in row), 'no payload, headers, paths or exception text may reach the log')
        self.assertEqual(row[VERSION], 1)
        self.assertEqual(row[SEQUENCE], 0)
        self.assertEqual(row[STATUS], 200)
        self.assertEqual(self.backend.submissions, 0)
        return row

    def assert_isolated(self, row, stage):
        self.assertGreaterEqual(row[stage], 65000)
        for other in (READ_US, LOCK_US, EXEC_US, WRITE_HEADER_US, WRITE_BODY_US):
            if other != stage:
                self.assertLess(row[other], 50000, (stage, other, row))

    def test_normal_chunk_keeps_wire_response_and_logs_only_numbers(self):
        self.assertEqual(self.chunk(), {'sequence': 0, 'accepted_bytes': 4096})
        row = self.row()
        self.assertGreater(row[UTC_US], 1000000000000000)
        self.assertGreater(row[CAPTURE], 0)
        self.assertGreater(row[REQUEST_BYTES], 5400)
        self.assertEqual(row[RESPONSE_BYTES], 39)

    def test_delayed_body_is_attributed_to_read(self):
        raw = json.dumps({'sequence': 0, 'data': base64.b64encode(b'\x00\x00' * 2048).decode()}).encode()
        connection = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2)
        try:
            request = ('POST ' + self.path + '/chunk HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer fixture-private-token\r\nContent-Length: ' + str(len(raw)) + '\r\n\r\n').encode()
            connection.sendall(request + raw[:20])
            self.assertTrue(self.read_entered.wait(1))
            time.sleep(.09)
            connection.sendall(raw[20:])
            response = http.client.HTTPResponse(connection)
            response.begin()
            self.assertEqual(response.status, 200)
            response.read()
            self.assert_isolated(self.row(), READ_US)
        finally:
            connection.close()

    def test_held_voice_lock_is_attributed_to_lock_wait(self):
        held, release = threading.Event(), threading.Event()

        def hold():
            with self.voices[0].lock:
                held.set()
                release.wait(2)

        holder = threading.Thread(target=hold)
        holder.start()
        self.assertTrue(held.wait(1))

        def release_after_body():
            self.read_completed.wait(1)
            time.sleep(.09)
            release.set()

        releaser = threading.Thread(target=release_after_body)
        releaser.start()
        try:
            self.chunk()
            self.assert_isolated(self.row(), LOCK_US)
        finally:
            release.set()
            holder.join(2)
            releaser.join(2)

    def test_slow_append_is_attributed_to_execution(self):
        capture = self.voices[0].captures['fixture-device', 'private-capture-name'][0]
        append = capture.append

        def delayed_append(*args):
            time.sleep(.09)
            return append(*args)

        capture.append = delayed_append
        self.chunk()
        self.assert_isolated(self.row(), EXEC_US)

    def test_slow_headers_are_attributed_to_header_write(self):
        self.delay_headers = True
        self.chunk()
        self.assert_isolated(self.row(), WRITE_HEADER_US)

    def test_slow_body_write_is_attributed_to_body_write(self):
        self.delay_body = True
        self.chunk()
        self.assert_isolated(self.row(), WRITE_BODY_US)

    def test_header_wait_is_separate_from_dispatch(self):
        connection = socket.create_connection(('127.0.0.1', self.server.server_port), timeout=2)
        raw = json.dumps({'sequence': 0, 'data': 'AAA='}).encode()
        try:
            connection.sendall(('POST ' + self.path + '/chunk HTTP/1.1\r\nHost: localhost\r\n').encode())
            time.sleep(.09)
            connection.sendall(('Authorization: Bearer fixture-private-token\r\nContent-Length: ' + str(len(raw)) + '\r\n\r\n').encode() + raw)
            response = http.client.HTTPResponse(connection)
            response.begin()
            self.assertEqual(response.status, 200)
            response.read()
            row = self.row()
            self.assertGreaterEqual(row[HEADER_US], 65000)
            self.assertLess(row[TOTAL_US], 50000)
        finally:
            connection.close()

    def test_blocked_log_writer_does_not_delay_next_keepalive_chunk(self):
        raw_write=os.write

        def blocked_write(fd,value):
            self.lines.entered.set()
            if not self.lines.release.wait(3):raise TimeoutError('synthetic raw output stall')
            return raw_write(fd,value)

        with patch('pocket.chunk_timing.os.write',side_effect=blocked_write):
            self.chunk()
            self.assertTrue(self.lines.entered.wait(1))
            completed = threading.Event()
            result = []

            def next_chunk():
                result.append(self.chunk(1))
                completed.set()

            worker = threading.Thread(target=next_chunk)
            worker.start()
            try:
                self.assertTrue(completed.wait(1), 'journal writer must not block subsequent requests')
                self.assertEqual(result, [{'sequence': 1, 'accepted_bytes': 8192}])
            finally:
                self.lines.release.set()
                worker.join(3)

    def test_unauthorized_chunk_never_emits_a_row(self):
        self.client.request('POST', self.path + '/chunk', b'{}', {'Authorization': 'Bearer wrong-secret'})
        response = self.client.getresponse()
        response.read()
        self.assertEqual(response.status, 401)
        with self.assertRaises(queue.Empty):
            self.lines.rows.get(timeout=.1)

    def test_bind_failure_keeps_original_socket_error(self):
        other_store = Store(':memory:')
        try:
            failure = OSError('synthetic bind failure')
            with patch('socket.socket.bind', side_effect=failure):
                with self.assertRaises(Exception) as raised:
                    make_server(Service(other_store, FakeHermes()), {'fixture': 'device'})
            self.assertIs(raised.exception, failure)
        finally:
            other_store.close()


class NumericLogTests(unittest.TestCase):
    def logger_class(self):
        self.assertIsNotNone(importlib.util.find_spec('pocket.chunk_timing'),
                             'bounded numeric timing writer is missing')
        return importlib.import_module('pocket.chunk_timing').NumericTimingLog

    def test_blocked_real_stderr_pipe_does_not_prevent_process_exit(self):
        # Fill from the parent so a Windows CRT lock in a test filler thread
        # cannot itself affect the child's interpreter shutdown.
        read_fd,write_fd=os.pipe()
        filler_fd=os.dup(write_fd)
        entered=threading.Event()

        def fill():
            try:
                entered.set()
                os.write(filler_fd,b'x'*(1024*1024))
            except OSError:
                pass
            finally:
                os.close(filler_fd)

        filler=threading.Thread(target=fill,daemon=True)
        code = '''import sys,time
from pocket.chunk_timing import NumericTimingLog
print('started',flush=True)
assert sys.stdin.buffer.read(1)==b'g'
logger=NumericTimingLog()
logger.emit(tuple(range(13)))
time.sleep(.1)
logger.close()
print('ready',flush=True)
'''
        child = subprocess.Popen([sys.executable, '-B', '-c', code],
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=write_fd)
        handshake=queue.Queue()
        reader=threading.Thread(target=lambda:handshake.put(child.stdout.readline()),daemon=True)
        reader.start()
        try:
            self.assertEqual(handshake.get(timeout=2).strip(),b'started')
            filler.start()
            self.assertTrue(entered.wait(1))
            time.sleep(.1)
            child.stdin.write(b'g');child.stdin.flush()
            try:
                exit_code = child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                exit_code = None
            self.assertEqual(exit_code, 0, 'full stderr pipe must not stall or abort interpreter shutdown')
            self.assertEqual(child.stdout.read(), b'ready\n' if sys.platform != 'win32' else b'ready\r\n')
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
            child.stdout.close()
            child.stdin.close()
            os.close(read_fd)
            os.close(write_fd)
            if filler.ident is not None:filler.join(1)
            reader.join(1)

    def test_saturated_logger_is_bounded_and_never_blocks_emitter(self):
        logger_class = self.logger_class()
        lines = Lines()
        raw_write=os.write

        def blocked_write(fd,value):
            lines.entered.set()
            if not lines.release.wait(3):raise TimeoutError('synthetic raw output stall')
            return raw_write(fd,value)

        with patch('sys.stderr', lines),patch('pocket.chunk_timing.os.write',side_effect=blocked_write):
            logger = logger_class()
            try:
                logger.emit(tuple(range(13)))
                self.assertTrue(lines.entered.wait(1))
                start = time.perf_counter()
                for _ in range(1000):
                    logger.emit(tuple(range(13)))
                self.assertLess(time.perf_counter() - start, .2)
                lines.release.set()
                rows = []
                while True:
                    try:
                        rows.append(lines.rows.get(timeout=.1))
                    except queue.Empty:
                        break
                self.assertGreater(len(rows), 1)
                self.assertLessEqual(len(rows), 65, 'at most one active row plus 64 queued rows')
                logger.emit(tuple(range(13)))
                self.assertEqual(lines.rows.get(timeout=1)[SERIAL], 1002,
                                 'serial gaps must reveal dropped timing rows')
            finally:
                lines.release.set()
                logger.close()
                lines.close()

    def test_non_numeric_payload_is_discarded_without_logging(self):
        logger_class = self.logger_class()
        lines = Lines()
        with patch('sys.stderr', lines):
            logger = logger_class()
            try:
                logger.emit((0,) * 12 + ('never-log-this-text',))
                with self.assertRaises(queue.Empty):
                    lines.rows.get(timeout=.1)
                logger.emit(tuple(range(13)))
                self.assertTrue(all(type(value) is int for value in lines.rows.get(timeout=1)))
            finally:
                logger.close()
                lines.close()


if __name__ == '__main__':
    unittest.main()
