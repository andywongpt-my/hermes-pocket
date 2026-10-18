"""Length-framed, bounded resident STT protocol; no diagnostics on stdout."""
import json
import struct

MAX_REQUEST = 1400000
MAX_RESPONSE = 32768


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate field')
        result[key] = value
    return result


def encode_frame(value, limit):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')
    if not 0 < len(data) <= limit:
        raise ValueError('frame size')
    return struct.pack('!I', len(data)) + data


def _read_exact(stream, size):
    result = bytearray()
    while len(result) < size:
        part = stream.read(size - len(result))
        if not part:
            raise EOFError('short frame')
        result.extend(part)
    return bytes(result)


def read_frame(stream, limit):
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        header += _read_exact(stream, 4 - len(header))
    size = struct.unpack('!I', header)[0]
    if not 0 < size <= limit:
        raise ValueError('frame size')
    data = _read_exact(stream, size)
    return json.loads(data.decode('utf-8'), object_pairs_hook=_unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
