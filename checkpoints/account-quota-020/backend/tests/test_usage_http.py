"""Usage routes with supplied snapshots; connections restricted to loopback."""
import ipaddress
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from pocket.server import make_server
from pocket.service import FakeHermes, Service
from pocket.store import Store
from pocket.usage import UsageCache, codex_limits, ollama_balance


class UsageHTTPTests(unittest.TestCase):
    def setUp(self):
        original=socket.socket.connect
        def loopback_connect(sock,address):
            if not ipaddress.ip_address(address[0]).is_loopback:
                raise AssertionError('test attempted a non-loopback connection')
            return original(sock,address)
        self.guard=patch.object(socket.socket,'connect',loopback_connect)
        self.guard.start();self.addCleanup(self.guard.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.services={target:Service(Store(Path(self.tmp.name)/target),FakeHermes()) for target in ('windows','linux')}
        for service in self.services.values():self.addCleanup(service.store.close)
        self.cache=UsageCache(ttl=60)
        self.now=time.time()
        self.server=make_server(self.services,{'fixture':'device'},usage_cache=self.cache)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close_server)
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def call(self,path,token='fixture',body=None):
        request=urllib.request.Request(self.url+path,headers={'Authorization':'Bearer '+token},
                                       data=None if body is None else json.dumps(body).encode())
        try:
            with self.opener.open(request,timeout=2) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:
            with error:return error.code,json.load(error)

    def test_target_scoped_report_preserves_units_nulls_and_fixed_provider_order(self):
        self.cache.put('windows','codex',codex_limits({'rateLimits':{'primary':{
            'usedPercent':0,'windowDurationMins':300,'resetsAt':self.now+600}}},self.now))
        self.cache.put('linux','ollama_cloud',ollama_balance({'included':{'balance_usd':2},'purchased':{'balance_usd':3}},self.now))
        code,windows=self.call('/targets/windows/usage')
        self.assertEqual(code,200)
        self.assertEqual([report['provider'] for report in windows],['codex','claude','ollama_cloud'])
        self.assertTrue(all(report['value'] is None for report in windows))
        self.assertEqual(windows[0]['availability'],'available')
        self.assertEqual(windows[0]['metrics'][0]['value'],0)
        self.assertEqual(windows[0]['metrics'][0]['window_minutes'],300)
        self.assertEqual(windows[0]['metrics'][0]['expires_at'],self.now+60)
        self.assertEqual(windows[2]['availability'],'not_configured')
        _,linux=self.call('/targets/linux/usage')
        self.assertEqual(linux[0]['availability'],'not_configured')
        self.assertEqual([metric['category'] for metric in linux[2]['metrics']],['included_credit','purchased_credit'])
        self.assertEqual([metric['unit'] for metric in linux[2]['metrics']],['USD_credit','USD_credit'])

    def test_expired_and_failed_observations_are_not_returned_as_fresh(self):
        self.cache.put('windows','codex',codex_limits({'rateLimits':{'primary':{
            'usedPercent':42,'resetsAt':self.now+600}}},self.now-61))
        self.assertEqual(self.call('/targets/windows/usage')[1][0]['availability'],'stale')
        self.cache.failed('windows','codex')
        report=self.call('/targets/windows/usage')[1][0]
        self.assertEqual(report['availability'],'failed')
        self.assertEqual(report['metrics'][0]['availability'],'stale')
        self.assertEqual(report['metrics'][0]['value'],42)

    def test_usage_keeps_auth_target_and_read_only_route_boundaries(self):
        self.assertEqual(self.call('/targets/windows/usage',token='wrong')[0],401)
        self.assertEqual(self.call('/usage')[0],400)
        self.assertEqual(self.call('/targets/absent/usage')[0],404)
        self.assertEqual(self.call('/targets/windows/usage',body={})[0],404)
