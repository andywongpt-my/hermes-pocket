"""GPT-Live client delegation through the existing durable Pocket chat service."""
import asyncio
import hashlib
import json
import math
from .schema import identifier


class HermesLiveBackend:
    """Keep Hermes model, memory, tools, task IDs and approval semantics intact.

    Closing speech stops local polling only. A submitted task remains in the
    existing journal and may continue upstream; neither stop nor approve is called.
    """
    def __init__(self, chat, device, chat_id, *, poll_interval=0.1, timeout=30):
        self.chat = chat
        self.device = identifier(device)
        self.chat_id = identifier(chat_id)
        for value, maximum in ((poll_interval, 1), (timeout, 60)):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError('invalid backend limit')
        self.poll_interval, self.timeout = poll_interval, timeout

    async def __call__(self, session_id, delegation_id, context):
        if (not isinstance(session_id, str) or not 1 <= len(session_id) <= 128 or
                not isinstance(delegation_id, str) or not 1 <= len(delegation_id) <= 128):
            raise ValueError('invalid delegation identity')
        # ChatService validates the bounded text and rejects a reused ID with
        # changed content. Service journals an uncertain dispatch, never replays it.
        identity = 'l_' + hashlib.sha256(json.dumps([session_id, delegation_id],
                                                   separators=(',', ':')).encode()).hexdigest()[:60]
        data = {'version': 1, 'request_id': identity, 'text': context}
        task = None
        try:
            # Bound local waiting including admission and each refresh. A timed
            # out thread may still admit/finish work; it must never be replayed.
            async with asyncio.timeout(self.timeout):
                task = await asyncio.to_thread(self.chat.submit, self.device, self.chat_id, data)
                while task['state'] not in ('completed', 'failed', 'cancelled', 'unknown', 'awaiting_approval'):
                    task = await asyncio.to_thread(self.chat.service.refresh, self.device, task['request_id'])
                    if task['state'] in ('queued_upstream', 'running', 'stopping'):
                        await asyncio.sleep(self.poll_interval)
        except TimeoutError:
            return {'state': 'unknown', 'task_id': task['request_id'] if task else None, 'result': None}
        return {'state': task['state'], 'task_id': task['request_id'],
                'result': task.get('result') if task['state'] == 'completed' else None}
