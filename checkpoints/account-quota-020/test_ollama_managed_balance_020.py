import unittest
import ollama_managed_balance_020 as m

class NormalizeTests(unittest.TestCase):
    def test_new(self):
        r=m._normalize({'included':{'balance_usd':-2,'allowance_usd':10,'period':{'from':'2026-01-01T00:00:00Z','until':'2026-02-01T00:00:00Z'}},'purchased':{'balance_usd':3}},123)
        self.assertEqual(r['status'],'available'); self.assertEqual(r['included']['balance_usd'],-2); self.assertEqual(r['fetched_at'],123)
    def test_legacy(self):
        r=m._normalize({'included':{'session':{'remaining_percent':20,'resets_at':'2026-01-01T00:00:00Z'}}},12)
        self.assertEqual(r['legacy']['session']['remaining_percent'],20)
    def test_missing_invalid(self):
        for v in [{},{'included':{'balance_usd':True,'allowance_usd':-1}},{'included':{'weekly':{'remaining_percent':101}}}]:
            self.assertEqual(m._normalize(v,2)['status'],'unknown')
    def test_timestamps_do_not_make_available(self):
        for body in [{}, {'included': {'period': {'from': '2026-01-01T00:00:00Z'}}},
                     {'included': {'session': {'resets_at': '2026-01-01T00:00:00Z'}}}]:
            r=m._normalize(body,123)
            self.assertEqual(r['status'],'unknown'); self.assertIsNone(r['fetched_at'])
    def test_reversed_equal_and_partial_periods(self):
        for until in ['2025-01-01T00:00:00Z','2026-01-01T00:00:00Z']:
            r=m._normalize({'included':{'balance_usd':0,'period':{'from':'2026-01-01T00:00:00Z','until':until}}},123)
            self.assertEqual(r['status'],'available')
            self.assertEqual(r['included']['period'],{'from':None,'until':None})
        r=m._normalize({'included':{'balance_usd':0,'period':{'from':'2026-01-01T00:00:00Z'}}},123)
        self.assertIsNotNone(r['included']['period']['from']); self.assertIsNone(r['included']['period']['until'])
    def test_strict(self):
        for b in [b'{"x":1,"x":2}',b'{"x":NaN}',b'\xff',b'['*40+b']'*40,b'x'*65537]:
            with self.assertRaises(ValueError): m._strict_json(b)

import asyncio
import sys
import types
from unittest.mock import patch
import httpx

class OperationTests(unittest.IsolatedAsyncioTestCase):
    def _setupAsyncioRunner(self):
        class LocalLoop(asyncio.SelectorEventLoop):
            # Synthetic coroutines only: avoid sandbox-blocked Windows socketpair.
            def __init__(self):
                super().__init__()
                self._selector.select = lambda timeout=None: []
            def _make_self_pipe(self): self._csock = None
            def _close_self_pipe(self): pass
        self._asyncioRunner = asyncio.Runner(loop_factory=LocalLoop)
    async def invoke(self, data=b'{"included":{"balance_usd":2}}', headers=None, key='synthetic', delay=False, observe=None, stall_close=False, hang_request=False, on_close=None):
        self.calls=[]; self.closed=False; self.kwargs=None
        async def handler(request):
            self.calls.append(request)
            if observe: observe()
            if delay or hang_request: await asyncio.Event().wait()
            return httpx.Response(200, headers=headers, stream=httpx.ByteStream(data))
        outer=self
        class Inner(httpx.MockTransport):
            async def aclose(s):
                outer.closed=True
                if stall_close: await asyncio.Event().wait()
                await super().aclose()
        class SDK:
            def __init__(s, **kw): outer.kwargs=kw; s.http=kw['http_client']
            async def close(s):
                if on_close: await on_close()
                await s.http.aclose()
            async def __aenter__(s): return s
            async def __aexit__(s,*a): pass
            async def get(s,path,cast_to):
                outer.assertEqual(path,'/balance'); outer.assertEqual(cast_to,dict[str, object])
                response=await s.http.get('https://ollama.com/api/balance')
                return response.json()
        scope=types.ModuleType('agent.secret_scope')
        def getter(name):
            self.assertEqual(name,'OLLAMA_API_KEY')
            if isinstance(key, Exception): raise key
            return key
        scope.get_secret_str=getter
        sdk=types.ModuleType('openai'); sdk.AsyncOpenAI=SDK
        with patch.dict(sys.modules,{'agent':types.ModuleType('agent'),'agent.secret_scope':scope,'openai':sdk}), patch.object(httpx,'AsyncHTTPTransport',type('Transport', (Inner,), {'__init__': lambda s, **kw: Inner.__init__(s, handler)})):
            if delay:
                task=asyncio.create_task(m.read_managed_ollama_balance())
                while not self.calls and not task.done(): await asyncio.sleep(0)
                self.assertFalse(task.done(), task.result() if task.done() else None)
                self.assertEqual((await m.read_managed_ollama_balance())['status'],'busy')
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                return
            return await m.read_managed_ollama_balance()
    async def test_fixed_request_and_options(self):
        r=await self.invoke()
        self.assertEqual(r['status'],'available'); self.assertEqual(len(self.calls),1)
        self.assertEqual(str(self.calls[0].url),'https://ollama.com/api/balance')
        self.assertEqual(self.calls[0].method,'GET'); self.assertTrue(self.closed)
        self.assertEqual(self.kwargs['max_retries'],0)
        for k in ['organization','project','webhook_secret']: self.assertEqual(self.kwargs[k],'')
        self.assertGreater(r['fetched_at'], 1700000000)
    async def test_absent_credentials(self):
        for key in ['',None,RuntimeError('sensitive')]:
            r=await self.invoke(key=key)
            self.assertEqual(r['status'],'not_configured'); self.assertEqual(self.calls,[])
            self.assertIsNone(self.kwargs)
    async def test_rejected_bodies(self):
        for data,headers in [(b'{"x":1,"x":2}',None),(b'{"x":Infinity}',None),(b'\xff',None),(b'x'*65537,None),(b'{}',{'content-encoding':'gzip'}),(b'['*40+b']'*40,None)]:
            r=await self.invoke(data,headers)
            self.assertEqual(r['status'],'unavailable'); self.assertIsNone(r['fetched_at'])
            self.assertNotIn('sensitive',str(r)); self.assertTrue(self.closed)
    async def test_cancellation_concurrency_closure(self):
        await self.invoke(delay=True)
        self.assertTrue(self.closed); self.assertFalse(m._busy)





    async def test_namespace_logging_preserves_root_and_unrelated(self):
        import logging
        unrelated=logging.getLogger('quota.test.unrelated')
        existing=logging.getLogger('httpcore.existing'); existing.setLevel(logging.DEBUG)
        disable=logging.root.manager.disable
        level=unrelated.level
        def observe():
            self.assertFalse(existing.isEnabledFor(logging.DEBUG))
            self.assertFalse(logging.getLogger('openai.new.normal.descendant').isEnabledFor(logging.DEBUG))
            self.assertEqual(unrelated.level,level)
            self.assertEqual(logging.root.manager.disable,disable)
        await self.invoke(observe=observe)
        self.assertEqual(existing.level,logging.DEBUG)
        self.assertEqual(logging.root.manager.disable,disable)
    async def test_stalled_close_is_bounded(self):
        real=m._bounded
        async def short(awaitable,seconds): return await real(awaitable,(0.1 if seconds == 8 else min(seconds,0.01)))
        with patch.object(m,'_bounded',short):
            r=await self.invoke(stall_close=True)
        self.assertEqual(r['status'],'available'); self.assertFalse(m._busy)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertFalse(m._pending)
    async def test_stalled_request_timeout_releases_state(self):
        real=m._bounded
        async def short(awaitable,seconds): return await real(awaitable,(0.1 if seconds == 8 else min(seconds,0.01)))
        # Handler cancellation path is separately covered; bound a stalled request
        # directly to avoid the cancellation-specific invoke helper.
        async def stall(): await asyncio.Event().wait()
        with self.assertRaises(TimeoutError): await short(stall(),8)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertFalse(m._pending)


    async def test_operation_request_timeout(self):
        real=m._bounded
        async def short(awaitable,seconds): return await real(awaitable,min(seconds,0.01))
        with patch.object(m,'_bounded',short):
            r=await self.invoke(hang_request=True)
        self.assertEqual(r['status'],'unavailable'); self.assertIsNone(r['fetched_at'])
        self.assertFalse(m._busy); self.assertTrue(self.closed)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertFalse(m._pending)

    async def test_cancel_during_cleanup_propagates(self):
        entered=asyncio.Event()
        async def close():
            entered.set()
            await asyncio.Event().wait()
        task=asyncio.create_task(self.invoke(on_close=close))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertFalse(m._busy)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_retained_cleanup_keeps_namespace_quiet(self):
        import logging
        logger=logging.getLogger('httpcore.retained'); logger.setLevel(logging.DEBUG)
        release=asyncio.Event(); entered=asyncio.Event()
        async def close():
            entered.set()
            try: await asyncio.Event().wait()
            except asyncio.CancelledError: await release.wait()
        real=m._bounded
        async def short(awaitable,seconds):
            return await real(awaitable,0.01 if seconds == 1 else seconds)
        with patch.object(m,'_bounded',short):
            r=await self.invoke(on_close=close)
        try:
            self.assertEqual(r['status'],'available'); self.assertTrue(entered.is_set())
            self.assertTrue(m._pending); self.assertFalse(m._busy)
            self.assertFalse(logger.isEnabledFor(logging.DEBUG))
            self.assertEqual((await m.read_managed_ollama_balance())['status'],'busy')
        finally:
            release.set()
            for _ in range(5): await asyncio.sleep(0)
        self.assertFalse(m._pending)
        self.assertEqual(logger.level,logging.DEBUG)


class InstalledSDKTests(unittest.IsolatedAsyncioTestCase):
    _setupAsyncioRunner = OperationTests._setupAsyncioRunner

    async def test_native_only_installed_sdk_parses_bounded_balance(self):
        try:
            import openai
        except ModuleNotFoundError as exc:
            if exc.name != 'openai':
                raise
            self.skipTest('native-only actual-SDK regression: installed openai required; no installation permitted')

        requests, getter_names, transport_options, closed = [], [], [], []
        payload = b'{"included":{"balance_usd":72.5,"allowance_usd":100},"purchased":{"balance_usd":25}}'

        async def handler(request):
            requests.append(request)
            response = httpx.Response(200, headers={'content-type': 'application/json'},
                                      stream=httpx.ByteStream(payload))
            self.assertFalse(response.is_stream_consumed)
            return response

        class FixtureTransport(httpx.MockTransport):
            def __init__(self, **kwargs):
                transport_options.append(kwargs)
                super().__init__(handler)

            async def aclose(self):
                closed.append(True)
                await super().aclose()

        scope = types.ModuleType('agent.secret_scope')
        def synthetic_getter(name):
            getter_names.append(name)
            return 'synthetic-not-a-real-credential'
        scope.get_secret_str = synthetic_getter
        # Keep the installed AsyncOpenAI and its real response parser. Only the
        # secret getter and network boundary are replaced with inert fixtures.
        with patch.dict(sys.modules, {'agent': types.ModuleType('agent'),
                                      'agent.secret_scope': scope}), \
                patch.object(httpx, 'AsyncHTTPTransport', FixtureTransport), \
                patch.object(m.time, 'time', return_value=1792309539):
            result = await m.read_managed_ollama_balance()

        self.assertEqual(getter_names, ['OLLAMA_API_KEY'])
        self.assertEqual([(r.method, str(r.url)) for r in requests],
                         [('GET', 'https://ollama.com/api/balance')])
        self.assertEqual(transport_options, [{'verify': True, 'trust_env': False, 'retries': 0}])
        self.assertTrue(closed)
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result, m._normalize({
            'included': {'balance_usd': 72.5, 'allowance_usd': 100},
            'purchased': {'balance_usd': 25}}, 1792309539))
        self.assertFalse(m._busy)
        self.assertFalse(m._pending)
