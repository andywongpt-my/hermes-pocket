"""Fixed, payload-free voice outcomes shared by internal adapters."""
from .audio import AudioError

REASONS = (
    'unknown', 'ok', 'capture_aborted', 'capture_invalid', 'capture_empty',
    'capture_expired', 'capture_restart_loss', 'stt_cancelled', 'stt_shutdown',
    'stt_queue_full', 'stt_queue_timeout', 'stt_timeout', 'stt_worker_crash',
    'stt_worker_failed', 'stt_protocol_error', 'stt_startup_failed',
    'stt_startup_timeout', 'stt_restart_backoff', 'stt_worker_unreaped',
    'stt_no_confident_speech', 'stt_invalid_audio', 'stt_restart_loss',
    'submission_unknown', 'submission_too_late', 'tts_synthesis_failed',
    'tts_synthesis_timeout', 'tts_decode_failed', 'tts_decode_timeout',
    'tts_playback_limit', 'tts_text_too_long', 'tts_expired', 'tts_restart_loss',
    'tts_playback_capacity', 'tts_shutdown', 'voice_capacity')
STAGES = ('unknown', 'capture', 'queue', 'startup', 'inference', 'stt_total',
          'submission', 'synthesis', 'decode', 'playback')


class VoiceError(AudioError):
    def __init__(self, reason, *, timings_us=None):
        self.reason = reason if reason in REASONS else 'unknown'
        self.timings_us = safe_timings(timings_us)
        super().__init__(self.reason)


def safe_timings(value):
    if not isinstance(value, dict):
        return {}
    return {key: count for key, count in dict(value).items()
            if key in STAGES and type(count) is int and 0 <= count < 2**63}


def failure_reason(error, fallback):
    value = getattr(error, 'reason', None)
    return value if value in REASONS and value != 'unknown' else fallback


def reason_stage(reason, fallback):
    if reason in ('stt_queue_full', 'stt_queue_timeout', 'voice_capacity'):
        return 'queue'
    if reason in ('stt_startup_failed', 'stt_startup_timeout', 'stt_restart_backoff', 'stt_worker_unreaped'):
        return 'startup'
    return fallback
