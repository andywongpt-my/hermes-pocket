"""Opt-in Pocket Live wire adapter; no listener, credentials or device activation.

The caller must authenticate the stream and supply its verified device identity,
serialize concurrent sessions, cap transport messages at 1024 bytes, and supply a
supported echo processor. This module doesn't implement or claim acoustic AEC.
All audio is native 24 kHz mono PCM16 LE; 16 kHz bytes are never relabeled.
"""
import asyncio
from dataclasses import dataclass
import json
import struct

from .chat import ChatService
from .gpt_live import LiveError, LiveOutcome, LiveSettings, run_gpt_live
from .live_hermes import HermesLiveBackend
from .schema import identifier
from .transport import unique

_HEADER = struct.Struct('!2sBBIII')
_MAX_COUNTER = (1 << 32) - 1


@dataclass(frozen=True)
class AudioFrame:
    kind: int
    sequence: int
    sample_offset: int
    played_samples: int
    pcm: bytes


def _validate(frame):
    if (type(frame.kind) is not int or frame.kind not in (1, 2) or
            any(type(n) is not int or not 0 <= n <= _MAX_COUNTER
                for n in (frame.sequence, frame.sample_offset, frame.played_samples)) or
            not isinstance(frame.pcm, bytes) or not 2 <= len(frame.pcm) <= 960 or
            len(frame.pcm) % 2 or (frame.kind == 1 and len(frame.pcm) != 960) or
            (frame.kind == 2 and frame.played_samples != 0) or
            frame.sequence == _MAX_COUNTER or
            frame.sample_offset > _MAX_COUNTER - len(frame.pcm) // 2):
        raise LiveError('invalid_device_frame')


def encode_frame(frame):
    _validate(frame)
    return _HEADER.pack(b'PL', 1, frame.kind, frame.sequence,
                        frame.sample_offset, frame.played_samples) + frame.pcm


def decode_frame(data):
    if not isinstance(data, bytes) or not 18 <= len(data) <= 976:
        raise LiveError('invalid_device_frame')
    magic, version, kind, sequence, offset, played = _HEADER.unpack_from(data)
    if magic != b'PL' or version != 1:
        raise LiveError('invalid_device_frame')
    frame = AudioFrame(kind, sequence, offset, played, data[16:])
    _validate(frame)
    return frame


class StreamState:
    """Counters advance only when the whole packet is valid; no replay or wrap."""
    def __init__(self, kind):
        if type(kind) is not int or kind not in (1, 2):
            raise LiveError('invalid_device_frame')
        self.kind = kind
        self.sequence = self.samples = self.played_samples = 0

    def accept(self, frame, *, speaker_samples_sent=0):
        _validate(frame)
        count = len(frame.pcm) // 2
        if (frame.kind != self.kind or frame.sequence != self.sequence or
                frame.sample_offset != self.samples or self.sequence == _MAX_COUNTER or
                self.samples > _MAX_COUNTER - count or
                type(speaker_samples_sent) is not int or not 0 <= speaker_samples_sent <= _MAX_COUNTER or
                (self.kind == 1 and not self.played_samples <= frame.played_samples <= speaker_samples_sent)):
            raise LiveError('device_stream_discontinuity')
        self.sequence += 1
        self.samples += count
        self.played_samples = frame.played_samples


def _control(raw):
    if not isinstance(raw, str):
        raise LiveError('invalid_device_control')
    try:
        if len(raw.encode('utf-8')) > 1024:
            raise ValueError()
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise LiveError('invalid_device_control') from None
    if not isinstance(value, dict):
        raise LiveError('invalid_device_control')
    return value


class DeviceAudioBridge:
    """Bounded wire I/O and echo boundary, independent of SDK and socket choice.

    echo.capture(pcm, capture_sample, played_samples) returns same-size PCM.
    echo.playback(pcm, sample_offset) registers the reference before it is sent.
    References are not proof of playout: capture carries the device's actual
    rendered count. The processor must handle silence, drift and acoustic delay.
    It must be reset/new for each session and cooperate with cancellation.
    An optional echo.aclose() transfers cleanup ownership to this adapter and
    is called with the session close timeout, including handshake failures.
    """
    def __init__(self, stream, echo):
        if not all(callable(getattr(echo, name, None)) for name in ('capture', 'playback')):
            raise LiveError('echo_processor_required')
        self.stream, self.echo = stream, echo
        self.microphone_queue = asyncio.Queue(maxsize=8)
        self.input_state, self.output_state = StreamState(1), StreamState(2)
        self._wire_lock = asyncio.Lock()

    @property
    def playback_pending(self):
        return self.output_state.samples > self.input_state.played_samples

    async def receive(self, stop):
        while not stop.is_set():
            raw = await self.stream.recv()
            if raw is None:
                raise LiveError('device_connection_lost')
            if isinstance(raw, str):
                if _control(raw) != {'type': 'stop'}:
                    raise LiveError('invalid_device_control')
                stop.set()
                return
            frame = decode_frame(raw)
            async with self._wire_lock:
                self.input_state.accept(frame, speaker_samples_sent=self.output_state.samples)
            if self.microphone_queue.full():
                raise LiveError('device_capture_backpressure')
            self.microphone_queue.put_nowait(frame)
            await asyncio.sleep(0)

    async def microphone(self):
        # run_gpt_live starts this iterator only after session.started.
        await self.stream.send('{"type":"ready","version":1,"rate":24000}')
        while True:
            frame = await self.microphone_queue.get()
            pcm = await self.echo.capture(frame.pcm, frame.sample_offset, frame.played_samples)
            if not isinstance(pcm, bytes) or len(pcm) != 960:
                raise LiveError('invalid_echo_audio')
            yield pcm

    async def playback(self, pcm):
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 48000:
            raise LiveError('invalid_output_audio')
        for at in range(0, len(pcm), 960):
            chunk = pcm[at:at + 960]
            if self.output_state.samples - self.input_state.played_samples + len(chunk) // 2 > 7680:
                raise LiveError('device_playback_backpressure')
            frame = AudioFrame(2, self.output_state.sequence, self.output_state.samples, 0, chunk)
            await self.echo.playback(chunk, frame.sample_offset)
            # A receive may race a completing send. Serialize the committed sent
            # counter with acknowledgment validation, never with echo preparation.
            async with self._wire_lock:
                await self.stream.send(encode_frame(frame))
                self.output_state.accept(frame)
            await asyncio.sleep(0)


async def run_device_gpt_live(client, service, device, stream, stop, *, echo, settings=None):
    """Attach one already-authenticated stream. Calling can incur Live charges.

    Not a public authorization boundary or a server route. Callers must supply
    the authenticated identity, not copy a device ID from client-controlled JSON.
    No fallback to legacy speech or no-AEC speakerphone operation is performed.
    """
    settings = settings or LiveSettings()
    owned = set()
    microphone = None
    outcome_type = LiveOutcome
    async def finish(outcome):
        report = getattr(stream, 'report_outcome', None)
        if callable(report):
            await asyncio.wait_for(report(outcome), settings.close_timeout)
        return outcome
    try:
        try:
            device = identifier(device)
        except (ValueError, TypeError):
            raise LiveError('invalid_device_identity') from None
        if not (all(callable(getattr(echo, name, None)) for name in ('capture', 'playback')) or
                callable(getattr(echo, 'capture_paired', None))):
            raise LiveError('echo_processor_required')
        if stop.is_set():
            return await finish(outcome_type(False, 'stopped_before_connect', False, False))
        hello_task = asyncio.create_task(stream.recv())
        stop_task = asyncio.create_task(stop.wait())
        owned.update((hello_task, stop_task))
        done, _ = await asyncio.wait((hello_task, stop_task),
            timeout=settings.startup_timeout, return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done:
            return await finish(outcome_type(False, 'stopped_before_connect', False, False))
        if hello_task not in done:
            raise LiveError('device_start_timeout')
        raw = hello_task.result()
        hello = _control(raw)
        version = hello.get('version')
        fields = {'type', 'version', 'rate', 'channels', 'format', 'chat_id'}
        if version == 2: fields.add('reference')
        if (type(version) is not int or version not in (1, 2) or set(hello) != fields or
                hello['type'] != 'start' or type(hello['rate']) is not int or hello['rate'] != 24000 or
                type(hello['channels']) is not int or hello['channels'] != version or
                hello['format'] != 'pcm_s16le' or
                (version == 2 and hello['reference'] != 'es8311_dac_right')):
            raise LiveError('invalid_device_start')
        outcome_type = LiveOutcome
        if version == 2:
            from .live_device_v2 import PairedDeviceAudioBridge, PairedLiveOutcome
            bridge = PairedDeviceAudioBridge(stream, echo)
            outcome_type = PairedLiveOutcome
        else:
            bridge = DeviceAudioBridge(stream, echo)
        try:
            chat_id = identifier(hello['chat_id'])
        except (ValueError, TypeError):
            raise LiveError('invalid_device_start') from None
        if stop.is_set():
            return await finish(outcome_type(False, 'stopped_before_connect', False, False))
        backend = HermesLiveBackend(ChatService(service), device, chat_id)
        microphone = bridge.microphone()
        reader = asyncio.create_task(bridge.receive(stop))
        live = asyncio.create_task(run_gpt_live(client, backend, microphone,
            bridge.playback, stop, settings=settings))
        owned.update((reader, live))
        done, _ = await asyncio.wait((reader, live), return_when=asyncio.FIRST_COMPLETED)
        if reader in done:
            reader.result()
        outcome = await live
        return await finish(outcome_type(outcome.finalized, outcome.reason, outcome.backend_pending,
            outcome.playback_pending or bridge.playback_pending))
    except (LiveError, asyncio.CancelledError):
        raise
    except Exception:
        raise LiveError('device_stream_failed') from None
    finally:
        for task in owned:
            task.cancel()
        cleanup_error = None
        try:
            await asyncio.wait_for(stream.close(), settings.close_timeout)
        except Exception:
            cleanup_error = 'device_close_failed'
        if owned:
            done, pending = await asyncio.wait(owned, timeout=settings.close_timeout)
            await asyncio.gather(*done, return_exceptions=True)
            if pending:
                cleanup_error = cleanup_error or 'device_cleanup_timeout'
                for task in pending:
                    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        if microphone is not None:
            try:
                await asyncio.wait_for(microphone.aclose(), settings.close_timeout)
            except Exception:
                cleanup_error = cleanup_error or 'device_cleanup_timeout'
        echo_close = getattr(echo, 'aclose', None)
        if callable(echo_close):
            try:
                await asyncio.wait_for(echo_close(), settings.close_timeout)
            except Exception:
                cleanup_error = cleanup_error or 'device_echo_cleanup_failed'
        if cleanup_error:
            raise LiveError(cleanup_error) from None
