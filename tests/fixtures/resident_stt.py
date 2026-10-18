"""Synthetic framed STT process: never imports a model or processes audio."""
import json
import os
import struct
import sys
import time


def read():
    size = sys.stdin.buffer.read(4)
    if len(size) != 4:
        raise SystemExit(0)
    return json.loads(sys.stdin.buffer.read(struct.unpack('!I', size)[0]))


def write(value):
    raw = json.dumps(value).encode()
    sys.stdout.buffer.write(struct.pack('!I', len(raw)) + raw)
    sys.stdout.buffer.flush()


init = read()
generation = init['generation']
mode = init['settings']['language']  # Synthetic-only fixture control.
if mode == 'startup_hang':
    time.sleep(10)
write({'kind': 'ready', 'generation': generation, 'model_load_us': 7})
while True:
    job = read()
    if mode == 'hang':
        time.sleep(10)
    if mode == 'crash':
        os._exit(9)
    if mode == 'oversize':
        sys.stdout.buffer.write(struct.pack('!I', 40000))
        sys.stdout.buffer.flush()
        time.sleep(10)
    if mode == 'delay':
        time.sleep(.30)
    value = {'kind': 'result', 'generation': generation,
             'request_id': job['request_id'], 'ok': True,
             'transcript': 'synthetic-' + str(os.getpid()), 'inference_us': 11}
    if mode == 'generation':
        value['generation'] += 1
    if mode == 'id':
        value['request_id'] = 'stale'
    if mode == 'schema':
        value['private_exception'] = '/private/path synthetic-secret'
    if mode == 'no_speech':
        value.pop('transcript')
        value.update(ok=False, reason='stt_no_confident_speech')
    write(value)
