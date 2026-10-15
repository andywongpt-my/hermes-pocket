import io
import unittest
from pocket.linux_voice import DashboardTransport
class Response(io.BytesIO):status=200
class Opener:
    def open(self,request,timeout):self.request=request;return Response(b'{"ok":true}')
class DashboardTunnelTests(unittest.TestCase):
    def test_explicit_host_only_over_loopback(self):
        opener=Opener()
        transport=DashboardTransport('http://127.0.0.1:19119','fixture',opener=opener,host_header='192.0.2.10:9119')
        transport('/api/audio/speak',{'text':'fixture'})
        self.assertEqual(opener.request.full_url,'http://127.0.0.1:19119/api/audio/speak')
        self.assertEqual(opener.request.get_header('Host'),'192.0.2.10:9119')
        self.assertEqual(opener.request.get_header('Authorization'),'Bearer fixture')
    def test_host_override_rejected_for_remote_origins(self):
        with self.assertRaises(ValueError):DashboardTransport('http://example.com','fixture',host_header='192.0.2.10:9119')
    def test_invalid_host_headers_rejected(self):
        for host in ['x\r\nAuthorization: bad','https://host','user@host','host/path','host?key=1','host:99999','', 'host:abc']:
            with self.subTest(host=host),self.assertRaises(ValueError):DashboardTransport('http://127.0.0.1:19119','fixture',host_header=host)

    def test_https_routing_preserves_tls_origin(self):
        opener=Opener()
        transport=DashboardTransport('https://voice.example.test:9443','fixture',opener=opener,host_header='192.0.2.10:9119')
        transport('/api/audio/speak',{'text':'fixture'})
        self.assertEqual(opener.request.full_url,'https://voice.example.test:9443/api/audio/speak')
        self.assertEqual(opener.request.get_header('Host'),'192.0.2.10:9119')
    def test_https_routing_keeps_certificate_validation(self):
        import ssl
        import urllib.request
        from pocket.transport import NoRedirect
        transport=DashboardTransport('https://voice.example.test:9443','fixture',host_header='192.0.2.10:9119')
        handler=next(h for h in transport.opener.handlers if isinstance(h,urllib.request.HTTPSHandler))
        self.assertTrue(handler._context.check_hostname)
        self.assertEqual(handler._context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(any(isinstance(h,NoRedirect) for h in transport.opener.handlers))
