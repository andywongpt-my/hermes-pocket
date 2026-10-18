"""Frozen contract v2: 9 canonical 2756-byte bodies, no I/O or providers.

Contract SHA256: 7c5b42855818fe920f6f0d1daac8ee0fa3cb134962f3f86198bf2bb87e1900eb.
"""
SIZES = (2756,)
REQUESTS = 9
TOTAL_BYTES = 24804
PATH = '/diagnostics/transport'


def body_for(sequence):
    if type(sequence) is not int or not 0 <= sequence < REQUESTS:
        raise ValueError('invalid synthetic sequence')
    prefix = ('{"sequence":%d,"padding":"' % sequence).encode('ascii')
    return prefix + b'P' * (2756 - len(prefix) - 2) + b'"}'
