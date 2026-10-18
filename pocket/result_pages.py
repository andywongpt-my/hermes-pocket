"""Exact, bounded pages of an owned stored result; never refresh upstream."""
import base64
import binascii
import hashlib
import json
import re

PAGE_BYTES = 768
MAX_RESULT_BYTES = 65536


class ReplyError(ValueError):
    """An explicitly safe wire error, without source content or filesystem paths."""
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


def result_revision(target, device, task):
    """Bind exact bytes and sequence to their complete ownership namespace."""
    envelope = [target, device, task['request_id'], task['seq'], task['result']]
    return hashlib.sha256(json.dumps(envelope, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def completed_result(target, device, store, task_id):
    task = store.get(device, task_id)  # Ownership before all result metadata.
    if task['state'] != 'completed':
        raise ReplyError(409, 'result_not_completed')
    text = task['result'] if task['result'] is not None else ''
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_RESULT_BYTES:
        raise ReplyError(400, 'invalid_request')
    return task, text, result_revision(target, device, {**task, 'result': text})


def _cursor(page, revision):
    raw = b'\1' + page.to_bytes(2, 'big') + bytes.fromhex(revision)
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _read_cursor(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{47}', value):
        raise ReplyError(400, 'invalid_request')
    try:
        raw = base64.b64decode(value + '=', altchars=b'-_', validate=True)
        if len(raw) != 35 or raw[0] != 1:
            raise ValueError()
        page, revision = int.from_bytes(raw[1:3], 'big'), raw[3:].hex()
        if _cursor(page, revision) != value:
            raise ValueError()
        return page, revision
    except (ValueError, binascii.Error):
        raise ReplyError(400, 'invalid_request') from None


class ResultPages:
    def __init__(self, target, store):
        self.target, self.store = target, store

    def get(self, device, task_id, cursor=None):
        _, text, revision = completed_result(self.target, device, self.store, task_id)
        page = 0
        if cursor is not None:
            page, pinned = _read_cursor(cursor)
            if pinned != revision:
                raise ReplyError(409, 'result_revision_changed')
        # At most 64KiB already stored in the journal. Boundaries are UTF-8 byte
        # offsets, computed without normalizing whitespace or control characters.
        raw = text.encode('utf-8')
        boundaries = [0]
        while boundaries[-1] < len(raw):
            end = min(boundaries[-1] + PAGE_BYTES, len(raw))
            while end < len(raw) and raw[end] & 0xc0 == 0x80:
                end -= 1
            boundaries.append(end)
        count = max(1, len(boundaries) - 1)
        if page >= count:
            raise ReplyError(416, 'result_page_out_of_range')
        start = boundaries[page]
        end = boundaries[page + 1] if raw else 0
        return {'version': 1, 'task_id': task_id, 'result_revision': revision,
                'page': page, 'page_count': count, 'utf8_bytes': end - start,
                'total_utf8_bytes': len(raw), 'text': raw[start:end].decode('utf-8'),
                'prev': _cursor(page - 1, revision) if page else None,
                'next': _cursor(page + 1, revision) if page + 1 < count else None,
                'eof': page + 1 == count}
