import io
import json
import unittest
from pocket.transport import JSONTransport, TransportError

class Response(io.BytesIO):
    status=200
class Opener:
    def __init__(self,body=b'{}'):self.body=body;self.calls=[]
    def open(self,request,timeout):
        self.calls.append((request,timeout));return Response(self.body)
class TransportTests(unittest.TestCase):
    def test_https_required(self):
        for url in ('http://example.com','https://user:pass@example.com','https://example.com/path','https://example.com?key=x','file:///tmp/a'):
            with self.subTest(url=url),self.assertRaises(ValueError):JSONTransport(url,'fixture-secret')
    def test_request_shape(self):
        opener=Opener(b'{"run_id":"run_a"}');t=JSONTransport('https://example.com','fixture-secret',opener=opener)
        self.assertEqual(t('POST','/v1/runs',{'input':'hi'},{'Idempotency-Key':'a:b'})['run_id'],'run_a')
        request,timeout=opener.calls[0]
        self.assertEqual(request.full_url,'https://example.com/v1/runs')
        self.assertEqual(json.loads(request.data),{'input':'hi'})
        self.assertEqual(request.get_header('Authorization'),'Bearer fixture-secret')
        self.assertEqual(timeout,10)
    def test_paths_headers_and_methods_allowlisted(self):
        t=JSONTransport('https://example.com','fixture-secret',opener=Opener())
        for method,path,headers in [('GET','https://evil.test',{}),('GET','/../secret',{}),('DELETE','/v1/runs',{}),('GET','/v1/models',{'Authorization':'evil'}),('GET','/v1/capabilities',{'Idempotency-Key':'x\r\ny'})]:
            with self.subTest(path=path),self.assertRaises(ValueError):t(method,path,None,headers)
    def test_bounded_and_valid_json(self):
        for body in (b'x'*262145,b'[]',b'{',b'{"a":NaN}',b'{"a":1,"a":2}'):
            t=JSONTransport('https://example.com','fixture-secret',opener=Opener(body))
            with self.subTest(body=body[:10]),self.assertRaises(TransportError):t('GET','/v1/capabilities',None,{})
    def test_errors_redacted(self):
        class Broken:
            def open(self,*args,**kwargs):raise OSError('fixture-secret https://private')
        t=JSONTransport('https://example.com','fixture-secret',opener=Broken())
        with self.assertRaises(TransportError) as error:t('GET','/v1/capabilities',None,{})
        self.assertNotIn('fixture-secret',str(error.exception))
    def test_repr_does_not_expose_key(self):
        self.assertNotIn('fixture-secret',repr(JSONTransport('https://example.com','fixture-secret')))

    def test_chat_history_budget(self):
        t=JSONTransport('https://example.com','fixture-secret',opener=Opener())
        t('POST','/v1/runs',{'input':'hi','conversation_history':[{'role':'assistant','content':'x'*16000}]},{})
        with self.assertRaises(ValueError):t('POST','/v1/runs',{'input':'x'*40000},{})
