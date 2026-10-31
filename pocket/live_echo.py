"""Opt-in SpeexDSP 1.2.1 AEC through its documented public C ABI.

This is real acoustic echo-cancellation code, not a microphone mute gate. It
requires a separately supplied capture-aligned render reference, including idle
silence and underruns. The v1 Pocket rendered-count field does NOT supply that
reference. PairedSpeexEcho uses explicit wire-v2 co-captured mic/DAC pairs. No
guessed v1-to-reference adapter or default service setup exists.

The caller explicitly supplies a trusted, locally installed library path. No
library search/download, provider access or credential discovery occurs here.
Native calls operate on audited 240- or 480-sample frames at 24 kHz; the pinned library's
bounded frame routine is synchronous. It is not a sandbox for untrusted native
libraries. The optional reference callbacks must cooperate with cancellation.
"""
import ctypes
import os
import struct
import threading
import weakref

from .gpt_live import LiveError

_PCM = struct.Struct('<480h')
_MAX_COUNTER = (1 << 32) - 1


def _frame(pcm, samples=480):
    if not isinstance(pcm, bytes) or len(pcm) != samples * 2:
        raise LiveError('invalid_echo_audio')


class SpeexEchoCanceller:
    """One owned mono 24-kHz state, 10/20-ms frames and 200-ms echo tail.

    ``process(capture, reference)`` uses speex_echo_cancellation, not Speex's
    soundcard FIFO which assumes an arbitrary two-frame delay. The reference
    must be aligned to the capture clock by the audio owner; all digital render
    latency must be accounted for outside this class. Physical quality remains
    a device/room acceptance test even when synthetic algorithm tests pass.
    """
    sample_rate = 24000
    frame_samples = 480

    def __init__(self, library_path, *, frame_samples=480):
        if type(frame_samples) is not int or frame_samples not in (240, 480):
            raise LiveError('echo_format_unsupported')
        self.frame_samples = frame_samples
        self._pcm = struct.Struct('<%dh' % frame_samples)
        self._samples = ctypes.c_int16 * frame_samples
        if (not isinstance(library_path, (str, os.PathLike)) or
                not os.path.isabs(library_path)):
            raise LiveError('echo_library_required')
        self._lock = threading.Lock()
        self._state = None
        try:
            lib = ctypes.CDLL(os.fspath(library_path))
            lib.speex_echo_state_init.argtypes = [ctypes.c_int, ctypes.c_int]
            lib.speex_echo_state_init.restype = ctypes.c_void_p
            lib.speex_echo_state_destroy.argtypes = [ctypes.c_void_p]
            lib.speex_echo_state_destroy.restype = None
            lib.speex_echo_state_reset.argtypes = [ctypes.c_void_p]
            lib.speex_echo_state_reset.restype = None
            lib.speex_echo_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
            lib.speex_echo_ctl.restype = ctypes.c_int
            lib.speex_echo_cancellation.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_int16)] * 3
            lib.speex_echo_cancellation.restype = None
        except (OSError, AttributeError, TypeError, ValueError):
            raise LiveError('echo_library_unavailable') from None
        self._lib = lib
        state = lib.speex_echo_state_init(frame_samples, 4800)
        if not state:
            raise LiveError('echo_allocation_failed')
        self._state = state
        self._finalizer = weakref.finalize(self, lib.speex_echo_state_destroy, state)
        rate, actual_rate, frame = ctypes.c_int(24000), ctypes.c_int(), ctypes.c_int()
        # Constants from the pinned public speex_echo.h, never private layout.
        if (lib.speex_echo_ctl(state, 24, ctypes.byref(rate)) != 0 or
                lib.speex_echo_ctl(state, 25, ctypes.byref(actual_rate)) != 0 or
                lib.speex_echo_ctl(state, 3, ctypes.byref(frame)) != 0 or
                actual_rate.value != 24000 or frame.value != frame_samples):
            self.close()
            raise LiveError('echo_format_unsupported')

    def process(self, capture, reference):
        _frame(capture, self.frame_samples)
        _frame(reference, self.frame_samples)
        with self._lock:
            if self._state is None:
                raise LiveError('echo_closed')
            # Explicit LE conversion is correct even on a big-endian host.
            near = self._samples(*self._pcm.unpack(capture))
            far, output = self._samples(*self._pcm.unpack(reference)), self._samples()
            self._lib.speex_echo_cancellation(self._state, near, far, output)
            return self._pcm.pack(*output)

    def reset(self):
        with self._lock:
            if self._state is None:
                raise LiveError('echo_closed')
            self._lib.speex_echo_state_reset(self._state)

    def close(self):
        with self._lock:
            if self._state is not None:
                self._state = None
                self._finalizer()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class SynchronizedSpeexEcho:
    """DeviceAudioBridge adapter with an explicitly supplied reference owner.

    ``reference.playback(pcm, source_offset)`` records exact outgoing PCM.
    ``reference.capture_reference(capture_offset, played_samples)`` must await
    the corresponding 480-sample render-clock frame. It must include actual
    silence and fail if timing/epoch is unknown. It must retain bounded data
    only (the bridge caps sent-but-unplayed audio at 7,680 samples). It must NOT
    infer that frame from v1 cumulative acknowledgments alone. ``aclose`` is
    required and releases the reference owner. Each session needs a fresh
    adapter/reference; missing data is never replaced with pass-through audio.
    """
    def __init__(self, library_path, *, reference):
        if not all(callable(getattr(reference, name, None))
                   for name in ('playback', 'capture_reference', 'aclose')):
            raise LiveError('echo_reference_required')
        self._echo = SpeexEchoCanceller(library_path)
        self._reference = reference
        self._capture = self._playback = self._played = 0
        self._closed = self._capturing = self._playing = False

    def _open(self):
        if self._closed:
            raise LiveError('echo_closed')

    async def playback(self, pcm, sample_offset):
        self._open()
        if (not isinstance(pcm, bytes) or not 2 <= len(pcm) <= 960 or len(pcm) % 2 or
                type(sample_offset) is not int or sample_offset != self._playback or
                sample_offset > _MAX_COUNTER - len(pcm) // 2 or self._playing):
            raise LiveError('echo_discontinuity')
        self._playing = True
        try:
            await self._reference.playback(pcm, sample_offset)
            self._open()
            self._playback += len(pcm) // 2
        finally:
            self._playing = False

    async def capture(self, pcm, capture_sample, played_samples):
        self._open()
        _frame(pcm)
        if (type(capture_sample) is not int or capture_sample != self._capture or
                capture_sample > _MAX_COUNTER - 480 or self._capturing or
                type(played_samples) is not int or not self._played <= played_samples <= self._playback):
            raise LiveError('echo_discontinuity')
        self._capturing = True
        try:
            reference = await self._reference.capture_reference(capture_sample, played_samples)
            self._open()
            result = self._echo.process(pcm, reference)
            self._capture += 480
            self._played = played_samples
            return result
        finally:
            self._capturing = False

    async def aclose(self):
        if self._closed:
            return
        self._closed = True
        # Release native state before a potentially slow reference callback.
        self._echo.close()
        try:
            await self._reference.aclose()
        except Exception:
            raise LiveError('echo_reference_cleanup_failed') from None


class PairedSpeexEcho:
    """Wire-v2 processor for 240 co-captured micLEFT/DACrefRIGHT sample pairs.

    Reference is the hardware's digital DAC stream, not network output history
    or a DMA-count-derived guess. Room/codec echo latency is modeled by the
    adaptive filter; physical lane/clock/acoustic acceptance is still mandatory.
    Only cleaned mono microphone bytes leave this processor. It emits no audio
    itself and does not use digital consumption as an audible-playout position.
    """
    def __init__(self, library_path):
        self._echo = SpeexEchoCanceller(library_path, frame_samples=240)
        self._capture = self._consumed = 0
        self._closed = False
        self._mono = struct.Struct('<240h')

    async def capture_paired(self, pcm, capture_sample, dma_consumed):
        if self._closed:
            raise LiveError('echo_closed')
        _frame(pcm)  # 240 stereo pairs = 480 interleaved int16 values.
        if (type(capture_sample) is not int or capture_sample != self._capture or
                capture_sample > _MAX_COUNTER - 240 or type(dma_consumed) is not int or
                not self._consumed <= dma_consumed <= _MAX_COUNTER):
            raise LiveError('echo_discontinuity')
        pairs = _PCM.unpack(pcm)
        cleaned = self._echo.process(self._mono.pack(*pairs[0::2]),
                                     self._mono.pack(*pairs[1::2]))
        self._capture += 240
        self._consumed = dma_consumed
        return cleaned

    async def aclose(self):
        if not self._closed:
            self._closed = True
            self._echo.close()
