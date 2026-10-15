"""Bounded HTTPS transport. No retries, redirects or automatic credential discovery."""
import json
import re
import ssl
import urllib.request
import urllib.error
from urllib.parse import urlsplit

class TransportError(RuntimeError):pass
class UpstreamRejected(TransportError):
    """The upstream answered, but with an HTTP error or an unusable response."""
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise TransportError('upstream redirect refused')

def unique(pairs):
    obj={}
    for key,value in pairs:
        if key in obj:raise ValueError('duplicate key')
        obj[key]=value
    return obj

class JSONTransport:
    def __init__(self,base_url,key,*,opener=None,allow_loopback_http=False):
        u=urlsplit(base_url)
        local_http=allow_loopback_http is True and u.scheme=='http' and u.hostname in ('127.0.0.1','::1')
        if (u.scheme!='https' and not local_http) or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
            raise ValueError('HTTPS origin required; no embedded credentials or path')
        if not isinstance(key,str) or not key or any(ord(c)<33 or ord(c)>126 for c in key):raise ValueError('invalid credential format')
        self.base=base_url.rstrip('/');self._key=key
        self.opener=opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    def __call__(self,method,path,body,headers):
        valid=(method=='GET' and path=='/v1/capabilities') or (method=='POST' and path=='/v1/runs') or bool(re.fullmatch(r'/v1/runs/[A-Za-z0-9_-]{1,64}',path) and method=='GET') or bool(re.fullmatch(r'/v1/runs/[A-Za-z0-9_-]{1,64}/stop',path) and method=='POST')
        if not valid:raise ValueError('unsupported upstream operation')
        if set(headers)-{'Idempotency-Key'}:raise ValueError('unsupported header')
        for value in headers.values():
            if not isinstance(value,str) or not 1<=len(value)<=255 or any(ord(c)<33 or ord(c)>126 for c in value):raise ValueError('invalid header')
        data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode() if body is not None else None
        if data is not None and len(data)>32768:raise ValueError('request too large')
        req=urllib.request.Request(self.base+path,data=data,method=method,headers={**headers,'Authorization':'Bearer '+self._key,'Content-Type':'application/json','Accept':'application/json'})
        try:
            with self.opener.open(req,timeout=10) as response:
                if response.status not in (200,202):raise UpstreamRejected('unexpected upstream status')
                raw=response.read(262145)
        except urllib.error.HTTPError as exc:
            status=exc.code;exc.close();raise UpstreamRejected('upstream HTTP '+str(status)) from None
        except (OSError,ValueError,UnicodeError,RecursionError):raise TransportError('upstream request failed') from None
        if len(raw)>262144:raise UpstreamRejected('upstream response too large')
        try:
            obj=json.loads(raw.decode('utf-8'),object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        except (ValueError,UnicodeError,RecursionError):raise UpstreamRejected('invalid upstream response') from None
        if not isinstance(obj,dict):raise UpstreamRejected('invalid upstream response')
        return obj
