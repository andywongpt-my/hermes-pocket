"""Temporary paired Pocket ingress; fixed TLS upstream, no credential files."""
import asyncio
import hashlib
import logging
from pathlib import Path
import re
import ssl

from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, TCPConnector, web

UPSTREAM = 'https://192.0.2.10:8443'
CA_FILE = '/home/example/.hermes/pocket/bridge-20261014/tls/pocket-ca.pem'
CA_SHA256 = '242b3f15fe48d9ac47b5933103b68036cda7b9d1ff13b610e2ffdb3d0380efff'
MAX_REQUEST = 8192
MAX_RESPONSE = 262144
REQUEST_TIMEOUT = 20.0
PREFIX = r'(?:/targets/linux)?'
IDENT = r'[A-Za-z0-9_-]{1,64}'
ROUTES = {
    'GET': tuple(re.compile(p) for p in (
        r'/(?:health|targets)',
        PREFIX + r'/(?:status|models|usage|tasks)',
        PREFIX + r'/tasks/' + IDENT + r'(?:/view|/speech)?',
        PREFIX + r'/tasks/' + IDENT + r'/events(?:\?after=[0-9]{1,20})?',
        PREFIX + r'/tasks/' + IDENT + r'/result/pages(?:\?cursor=[A-Za-z0-9_-]{47})?',
        PREFIX + r'/tasks/' + IDENT + r'/speech/chunks/[0-9]{1,3}',
        PREFIX + r'/tasks/' + IDENT + r'/speech/v2\?generation=[a-f0-9]{32}',
        PREFIX + r'/tasks/' + IDENT + r'/speech/v2/segments/[0-9]{1,2}/chunks/[0-9]{1,3}\?generation=[a-f0-9]{32}',
        PREFIX + r'/audio/' + IDENT,
        PREFIX + r'/chats/' + IDENT + r'/messages',
    )),
    'POST': tuple(re.compile(p) for p in (
        PREFIX + r'/tasks',
        PREFIX + r'/tasks/' + IDENT + r'/(?:stop|abandon|speech|speech/v2|speech/v2/cancel)',
        PREFIX + r'/audio/' + IDENT + r'(?:/chunk|/finish|/abort)?',
        PREFIX + r'/chats/' + IDENT + r'/messages',
    )),
}
AUTH_SHAPE = re.compile(r'Bearer [!-~]{32,128}')
for _name in ('aiohttp.access', 'aiohttp.server', 'aiohttp.client', 'aiohttp.internal'):
    logging.getLogger(_name).disabled = True


def tls_context():
    """Keep chain/IP checks with the existing CA's established certificate policy.

    PROTOCOL_TLS_CLIENT retains CERT_REQUIRED and hostname checking. It does not
    add Python 3.14 create_default_context's stricter X509 certificate profile.
    No verification flags are cleared, and no private keys are opened here.
    """
    public_ca = Path(CA_FILE).read_bytes()
    if hashlib.sha256(public_ca).hexdigest() != CA_SHA256:
        raise RuntimeError('Pocket public CA pin mismatch')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cadata=public_ca.decode('ascii'))
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise RuntimeError('Pocket TLS authentication is required')
    return context


def rejected(status):
    response = web.json_response({'error': 'request rejected'}, status=status,
                                headers={'Cache-Control': 'no-store', 'X-Pocket-Funnel': 'temporary-021'})
    response.force_close()
    return response


def make_app(upstream_url, context):
    """Build one proxy. main supplies the fixed HTTPS URL and pinned CA context."""
    if upstream_url != UPSTREAM and not re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', upstream_url):
        raise ValueError('unsupported upstream')
    if upstream_url == UPSTREAM and (not isinstance(context, ssl.SSLContext) or
            context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname):
        raise ValueError('verified TLS context required')
    session = None
    active = 0
    app = web.Application(client_max_size=MAX_REQUEST + 1,
        handler_args={'lingering_time': 0, 'keepalive_timeout': 15,
                      'max_field_size': 1024, 'max_line_size': 1024, 'max_headers': 64,
                      'auto_decompress': False})

    async def clients(_app):
        nonlocal session
        connector = TCPConnector(ssl=context, limit=16, limit_per_host=4, keepalive_timeout=15)
        async with ClientSession(connector=connector, trust_env=False,
                cookie_jar=DummyCookieJar(), auto_decompress=False,
                timeout=ClientTimeout(total=REQUEST_TIMEOUT, connect=3),
                skip_auto_headers={'User-Agent', 'Accept-Encoding'}) as client:
            session = client
            yield

    async def proxy(request):
        nonlocal active
        if not any(pattern.fullmatch(request.raw_path) for pattern in ROUTES.get(request.method, ())):
            return rejected(404)
        auth = request.headers.getall('Authorization', [])
        if len(auth) != 1 or not AUTH_SHAPE.fullmatch(auth[0]):
            return rejected(401)
        if (request.headers.get('Transfer-Encoding') or request.headers.get('Content-Encoding') or
                request.headers.get('Range') or len(request.headers.getall('Content-Length', [])) > 1):
            return rejected(400)
        if request.content_length is not None and request.content_length > MAX_REQUEST:
            return rejected(413)
        if active >= 16:
            return rejected(429)
        active += 1
        phase = 'body'
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                body = await request.read()
                if len(body) > MAX_REQUEST:
                    return rejected(413)
                phase = 'upstream'
                headers = {'Authorization': auth[0], 'Host': '192.0.2.10:8443',
                           'Accept': 'application/json', 'Content-Type': 'application/json'}
                async with session.request(request.method, upstream_url + request.raw_path,
                        data=body, headers=headers, allow_redirects=False) as result:
                    if (result.content_length is not None and result.content_length > MAX_RESPONSE or
                            result.headers.get('Content-Encoding') or
                            not result.headers.get('Content-Type', '').lower().startswith('application/json')):
                        return rejected(502)
                    data = bytearray()
                    async for part in result.content.iter_chunked(8192):
                        if len(data) + len(part) > MAX_RESPONSE:
                            return rejected(502)
                        data.extend(part)
                    return web.Response(status=result.status, body=bytes(data),
                        headers={'Content-Type': 'application/json; charset=utf-8',
                                 'Cache-Control': 'no-store', 'X-Pocket-Funnel': 'temporary-021'})
        except web.HTTPRequestEntityTooLarge:
            return rejected(413)
        except TimeoutError:
            return rejected(408 if phase == 'body' else 504)
        except asyncio.CancelledError:
            raise
        except Exception:
            return rejected(502)
        finally:
            active -= 1

    app.cleanup_ctx.append(clients)
    app.router.add_route('*', '/{path:.*}', proxy)
    return app


if __name__ == '__main__':
    web.run_app(make_app(UPSTREAM, tls_context()), host='127.0.0.1', port=18843,
                access_log=None, print=None, handler_cancellation=True, shutdown_timeout=5)
