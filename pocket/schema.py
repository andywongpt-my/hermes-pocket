"""Strict, bounded version-one text submission contract."""
import json
import re

MAX_FRAME=8192
MAX_TEXT=4096
IDENTIFIER=re.compile(r'[A-Za-z0-9_-]{1,64}\Z')

class ProtocolError(ValueError): pass

def identifier(value):
    if not isinstance(value,str) or not IDENTIFIER.fullmatch(value):
        raise ProtocolError('invalid identifier')
    return value

def _unique(pairs):
    out={}
    for k,v in pairs:
        if k in out: raise ProtocolError('duplicate field')
        out[k]=v
    return out

def decode(raw):
    if not isinstance(raw,bytes) or len(raw)>MAX_FRAME: raise ProtocolError('frame too large or not bytes')
    try:
        data=json.loads(raw.decode('utf-8'),object_pairs_hook=_unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ProtocolError('nonfinite number')))
    except (ValueError,UnicodeError,RecursionError) as exc: raise ProtocolError('invalid JSON') from exc
    if not isinstance(data,dict) or set(data)!={'version','request_id','text'}: raise ProtocolError('invalid fields')
    if type(data['version']) is not int or data['version']!=1: raise ProtocolError('unsupported version')
    identifier(data['request_id'])
    text=data['text']
    try: valid=isinstance(text,str) and bool(text.strip()) and len(text.encode('utf-8'))<=MAX_TEXT
    except UnicodeError: valid=False
    if not valid: raise ProtocolError('invalid text')
    return data
