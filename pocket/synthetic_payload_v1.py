"""Frozen synthetic payload contract v1. No I/O, payload logs or providers."""
SIZES = (512, 1024, 1360, 1440, 2048, 2756)
REQUESTS = 18
TOTAL_BYTES = 27420
PATH = '/diagnostics/transport'

def body_for(sequence):
    if type(sequence) is not int or not 0 <= sequence < REQUESTS:
        raise ValueError('invalid synthetic sequence')
    prefix = ('{"sequence":%d,"padding":"' % sequence).encode('ascii')
    size = SIZES[sequence % len(SIZES)]
    return prefix + b'P' * (size - len(prefix) - 2) + b'"}'
