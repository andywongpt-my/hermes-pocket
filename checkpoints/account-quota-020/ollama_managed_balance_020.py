"""One-shot managed Ollama source metadata; not a backend percent snapshot."""
import asyncio
import json
import logging
import math
import time
from datetime import datetime

_LIMIT = 65536
_busy = False
_pending = set()
_quiet_levels = []


def _restore_logging_if_settled():
    global _quiet_levels
    if not _busy and not _pending:
        previous, _quiet_levels = _quiet_levels, []
        for logger, level in previous:
            logger.setLevel(level)


def _result(status):
    return {'version': 1, 'status': status, 'fetched_at': None,
            'included': {'balance_usd': None, 'allowance_usd': None,
                         'period': {'from': None, 'until': None}},
            'purchased': {'balance_usd': None},
            'legacy': {k: {'remaining_percent': None, 'resets_at': None}
                       for k in ('session', 'weekly')}}


def _number(v, low=None, high=1e15):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not math.isfinite(v) or abs(v) > high or (low is not None and v < low):
        return None
    return v


def _stamp(v):
    if not isinstance(v, str) or len(v) > 64:
        return None
    try:
        d = datetime.fromisoformat(v.replace('Z', '+00:00'))
        if d.tzinfo is None or d.utcoffset() is None:
            return None
        t = d.timestamp()
        return t if math.isfinite(t) and 0 <= t <= 253402300799 else None
    except (ValueError, OverflowError, OSError):
        return None


def _obj(v):
    return v if isinstance(v, dict) else {}


def _normalize(body, completed):
    r = _result('unknown')
    inc = _obj(_obj(body).get('included'))
    for k in ('balance_usd', 'allowance_usd'):
        r['included'][k] = _number(inc.get(k), 0 if k == 'allowance_usd' else None)
    period = _obj(inc.get('period'))
    for k in ('from', 'until'):
        r['included']['period'][k] = _stamp(period.get(k))
    r['purchased']['balance_usd'] = _number(_obj(_obj(body).get('purchased')).get('balance_usd'))
    for k in ('session', 'weekly'):
        w = _obj(inc.get(k))
        r['legacy'][k] = {'remaining_percent': _number(w.get('remaining_percent'), 0, 100),
                          'resets_at': _stamp(w.get('resets_at'))}
    bounds = r['included']['period']
    if bounds['from'] is not None and bounds['until'] is not None and bounds['from'] >= bounds['until']:
        bounds['from'] = bounds['until'] = None
    amounts = [r['included']['balance_usd'], r['included']['allowance_usd'],
               r['purchased']['balance_usd']]
    amounts.extend(r['legacy'][k]['remaining_percent'] for k in ('session', 'weekly'))
    if any(v is not None for v in amounts):
        r['status'] = 'available'
        r['fetched_at'] = completed
    return r


def _strict_json(data):
    if len(data) > _LIMIT:
        raise ValueError('invalid')
    def pairs(items):
        d = {}
        for k, v in items:
            if k in d: raise ValueError('invalid')
            d[k] = v
        return d
    def constant(_): raise ValueError('invalid')
    try:
        v = json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
        def depth(x, n=0):
            if n > 8: raise ValueError('invalid')
            if isinstance(x, dict):
                for y in x.values(): depth(y, n+1)
            elif isinstance(x, list):
                for y in x: depth(y, n+1)
            elif isinstance(x, float) and not math.isfinite(x): raise ValueError('invalid')
        depth(v)
        if not isinstance(v, dict): raise ValueError('invalid')
        return v
    except (UnicodeError, RecursionError, json.JSONDecodeError) as e:
        raise ValueError('invalid') from None


def _transport_type(httpx):
    class Bounded(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0)
            self.used = False
        async def handle_async_request(self, request):
            if self.used or request.method != 'GET' or str(request.url) != 'https://ollama.com/api/balance':
                raise ValueError('invalid')
            self.used = True
            response = await self.inner.handle_async_request(request)
            try:
                if response.headers.get('content-encoding', 'identity').lower().strip() != 'identity':
                    raise ValueError('invalid')
                chunks, size = [], 0
                async for chunk in response.aiter_raw():
                    size += len(chunk)
                    if size > _LIMIT: raise ValueError('invalid')
                    chunks.append(chunk)
                data = b''.join(chunks)
                if 200 <= response.status_code < 300: _strict_json(data)
                # Error bodies never reach SDK exception construction.
                if not 200 <= response.status_code < 300: raise ValueError('unavailable')
                return httpx.Response(response.status_code, headers={'content-type':'application/json'}, content=data)
            finally:
                await _bounded(response.aclose(), 0.25)
        async def aclose(self): await self.inner.aclose()
    return Bounded


async def _bounded(awaitable, seconds):
    """Stop awaiting at the budget; retain unfinished work to prevent overlap."""
    task = asyncio.ensure_future(awaitable)
    _pending.add(task)
    def finished(t):
        _pending.discard(t)
        if not t.cancelled():
            t.exception()  # Consume privately; never render exception details.
        _restore_logging_if_settled()
    task.add_done_callback(finished)
    try:
        done, _ = await asyncio.wait({task}, timeout=max(0, seconds))
        if not done:
            task.cancel()
            raise TimeoutError()
        return task.result()
    except asyncio.CancelledError:
        task.cancel()
        raise


async def read_managed_ollama_balance():
    """Read the existing binding. Sync cold imports/getter need an outer process deadline.

    Async request budget is 8 seconds plus <=1 second client cleanup. A response
    close gets <=0.25 seconds inside the request budget. Noncooperative cancelled
    work is retained in _pending and blocks subsequent reads until it finishes.
    """
    global _busy, _quiet_levels
    if _busy or _pending: return _result('busy')
    _busy = True
    roots = ('openai', 'httpx', 'httpcore')
    for name in roots: logging.getLogger(name)
    loggers = [v for n, v in list(logging.root.manager.loggerDict.items())
               if isinstance(v, logging.Logger) and any(n == r or n.startswith(r + '.') for r in roots)]
    _quiet_levels = [(l, l.level) for l in loggers]
    # Existing descendants are covered even with explicit DEBUG levels; normal
    # descendants created during imports inherit their suppressed namespace root.
    for l in loggers: l.setLevel(logging.CRITICAL + 1)
    http = client = None
    try:
        try:
            from agent.secret_scope import get_secret_str
            key = get_secret_str('OLLAMA_API_KEY')
        except Exception:
            return _result('not_configured')
        if not isinstance(key, str) or not key.strip(): return _result('not_configured')
        try:
            import httpx
            from openai import AsyncOpenAI
        except ImportError:
            return _result('unavailable')
        http = httpx.AsyncClient(transport=_transport_type(httpx)(), trust_env=False,
                                 follow_redirects=False, timeout=7.5)
        client = AsyncOpenAI(api_key=key, base_url='https://ollama.com/api/',
                             organization='', project='', webhook_secret='',
                             max_retries=0, http_client=http)
        key = None
        body = await _bounded(client.get('/balance', cast_to=dict[str, object]), 8)
        return _normalize(body, time.time())
    except Exception:
        return _result('unavailable')
    finally:
        try:
            if client is not None:
                await _bounded(client.close(), 1)
            elif http is not None:
                await _bounded(http.aclose(), 1)
        except Exception:
            pass
        finally:
            _busy = False
            _restore_logging_if_settled()


if __name__ == '__main__':
    print(json.dumps(asyncio.run(read_managed_ollama_balance()), allow_nan=False, separators=(',', ':')))

