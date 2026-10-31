"""Real SpeexDSP tests are opt-in and synthetic, never acoustic/provider claims."""
import asyncio
import math
import os
import random
import struct
import unittest

from pocket.gpt_live import LiveError
try:
    from pocket.live_echo import SpeexEchoCanceller, SynchronizedSpeexEcho
except ImportError:
    SpeexEchoCanceller = SynchronizedSpeexEcho = None

SILENCE = b'\0' * 960
LIBRARY = os.environ.get('POCKET_TEST_SPEEXDSP_LIBRARY')


class EchoContractTests(unittest.TestCase):
    def test_implementation_and_explicit_dependency_are_required(self):
        self.assertIsNotNone(SpeexEchoCanceller, 'supported native AEC wrapper missing')
        for path in (None, '', 'libspeexdsp.so', True):
            with self.subTest(path=path):
                with self.assertRaisesRegex(LiveError, 'echo_library_required'):
                    SpeexEchoCanceller(path)

    def test_missing_reference_fails_before_native_load(self):
        self.assertIsNotNone(SynchronizedSpeexEcho, 'synchronized AEC adapter missing')
        with self.assertRaisesRegex(LiveError, 'echo_reference_required'):
            SynchronizedSpeexEcho('/nonexistent/library.so', reference=None)

    def test_invalid_absolute_library_paths_have_fixed_errors(self):
        self.assertIsNotNone(SpeexEchoCanceller)
        for path in ('/nonexistent/pocket-echo-library.so', '/invalid\0library.so'):
            with self.subTest(path=repr(path)):
                with self.assertRaisesRegex(LiveError, 'echo_library_unavailable'):
                    SpeexEchoCanceller(path)


@unittest.skipUnless(LIBRARY, 'set POCKET_TEST_SPEEXDSP_LIBRARY to a locally built SpeexDSP 1.2.1 library')
class NativeEchoTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SpeexEchoCanceller)
        self.echo = SpeexEchoCanceller(LIBRARY)
        self.addCleanup(self.echo.close)

    def test_native24k_exact_frames_close_and_reset(self):
        self.assertEqual((self.echo.sample_rate, self.echo.frame_samples), (24000, 480))
        self.assertEqual(self.echo.process(SILENCE, SILENCE), SILENCE)
        self.echo.reset()
        self.assertEqual(self.echo.process(SILENCE, SILENCE), SILENCE)
        self.echo.close()
        self.echo.close()
        for operation in (lambda: self.echo.process(SILENCE, SILENCE), self.echo.reset):
            with self.assertRaisesRegex(LiveError, 'echo_closed'):
                operation()

    def test_invalid_pcm_is_rejected_before_native_access(self):
        for bad in (b'', b'\0' * 959, b'\0' * 962, 'x' * 960, bytearray(960), None):
            with self.subTest(kind=type(bad).__name__):
                with self.assertRaisesRegex(LiveError, 'invalid_echo_audio'):
                    self.echo.process(bad, SILENCE)
                with self.assertRaisesRegex(LiveError, 'invalid_echo_audio'):
                    self.echo.process(SILENCE, bad)
        self.assertEqual(self.echo.process(SILENCE, SILENCE), SILENCE)

    def test_real_algorithm_reduces_synthetic_delayed_echo_without_muting_near_end(self):
        # Deterministic broadband far-end, FIR echo, then independent near-end.
        # This is algorithm coverage only, not Pocket/room acoustic acceptance.
        rng = random.Random(407)
        far = [rng.randint(-9000, 9000) for _ in range(480 * 650)]
        dry_energy = wet_energy = near_energy = near_correlation = 0
        for frame in range(650):
            begin = frame * 480
            near = [int(1500 * math.sin(2 * math.pi * 719 * (begin+n) / 24000))
                    if frame >= 550 else 0 for n in range(480)]
            echo = [int(.55 * far[begin+n-120] + .18 * far[begin+n-260])
                    if begin+n >= 260 else 0 for n in range(480)]
            capture = [a+b for a,b in zip(echo,near)]
            output = struct.unpack('<480h', self.echo.process(
                struct.pack('<480h', *capture), struct.pack('<480h', *far[begin:begin+480])))
            if 450 <= frame < 550:
                dry_energy += sum(n*n for n in capture)
                wet_energy += sum(n*n for n in output)
            if frame >= 600:
                near_energy += sum(n*n for n in near)
                near_correlation += sum(a*b for a,b in zip(output, near))
        self.assertGreater(10 * math.log10(dry_energy / max(wet_energy, 1)), 15)
        self.assertGreater(near_correlation / near_energy, .5, 'must not mute near-end double-talk')


class Reference:
    def __init__(self): self.played = []; self.captured = []; self.closed = False
    async def playback(self, pcm, offset): self.played.append((pcm, offset))
    async def capture_reference(self, sample_offset, played_samples):
        self.captured.append((sample_offset, played_samples))
        return SILENCE
    async def aclose(self): self.closed = True


@unittest.skipUnless(LIBRARY, 'set POCKET_TEST_SPEEXDSP_LIBRARY for synchronized adapter tests')
class SynchronizedEchoTests(unittest.IsolatedAsyncioTestCase):
    async def test_reference_wait_cancels_without_advancing_or_leaking_native_state(self):
        entered = asyncio.Event()
        class Waiting(Reference):
            async def capture_reference(self, *args):
                entered.set()
                await asyncio.Event().wait()
        echo = SynchronizedSpeexEcho(LIBRARY, reference=Waiting())
        capture = asyncio.create_task(echo.capture(SILENCE, 0, 0))
        await asyncio.wait_for(entered.wait(), 1)
        capture.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await capture
        await echo.aclose()
        self.assertTrue(echo._reference.closed)
        with self.assertRaisesRegex(LiveError, 'echo_closed'):
            echo._echo.process(SILENCE, SILENCE)

    async def test_reference_must_be_explicit_and_is_not_guessed_from_ack(self):
        reference = Reference()
        echo = SynchronizedSpeexEcho(LIBRARY, reference=reference)
        try:
            await echo.playback(SILENCE, 0)
            self.assertEqual(await echo.capture(SILENCE, 0, 480), SILENCE)
            self.assertEqual(reference.played, [(SILENCE, 0)])
            self.assertEqual(reference.captured, [(0, 480)])
            with self.assertRaisesRegex(LiveError, 'echo_discontinuity'):
                await echo.capture(SILENCE, 0, 480)
            with self.assertRaisesRegex(LiveError, 'echo_discontinuity'):
                await echo.capture(SILENCE, 480, 481)
        finally:
            await echo.aclose()
        self.assertTrue(reference.closed)
        with self.assertRaisesRegex(LiveError, 'echo_closed'):
            await echo.capture(SILENCE, 480, 480)

    async def test_missing_or_bad_reference_never_passes_unprocessed_capture(self):
        class Missing(Reference):
            async def capture_reference(self, *args):
                raise LiveError('echo_reference_unavailable')
        class Malformed(Reference):
            async def capture_reference(self, *args): return b''
        for reference in (Missing(), Malformed()):
            echo = SynchronizedSpeexEcho(LIBRARY, reference=reference)
            try:
                with self.assertRaises(LiveError):
                    await echo.capture(SILENCE, 0, 0)
            finally:
                await echo.aclose()

    async def test_close_always_releases_native_state_when_reference_cleanup_fails(self):
        class BadClose(Reference):
            async def aclose(self): raise RuntimeError('synthetic cleanup error')
        echo = SynchronizedSpeexEcho(LIBRARY, reference=BadClose())
        with self.assertRaisesRegex(LiveError, 'echo_reference_cleanup_failed'):
            await echo.aclose()
        with self.assertRaisesRegex(LiveError, 'echo_closed'):
            await echo.capture(SILENCE, 0, 0)
