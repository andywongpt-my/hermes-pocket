# Experimental deadline-aware Live network binding

Status: source/offline verification only; **not a real connection or physical
release**. `pocket_live_network_idf.h` is an internal interface for the inactive
worker. The device activation gate remains separate and must not be bypassed.
No task, key discovery, listener or automatic connection is installed by it.

## Supported-API architecture

1. One static, bounded DNS request slot is submitted through lwIP
   `tcpip_try_callback` and `dns_gethostbyname_addrtype`. Resolver callbacks never
   hold caller-stack or worker-context pointers. A timed-out/cancelled query is a
   retained tombstone: a replacement cannot reuse the slot until the actual
   queued/resolver callback settles. There is no fabricated DNS cancellation API.
2. TLS uses `esp_transport_connect_async` with the resolved numeric IPv4 address
   and a small **positive** timeout. In pinned IDF 5.5.3, zero in this path can
   select without a timeout and is not used. `esp_transport_ssl_set_common_name`
   is set to the original hostname. The pinned mbedTLS binding passes this name
   to `mbedtls_ssl_set_hostname`, preserving both original-host SNI and certificate
   name verification. Certificates remain required; no name-check bypass exists.
3. Only after TLS is verified is the bearer header attached. A public
   `esp_transport_set_func` shim wraps the connected TLS parent; its connect
   callback verifies the original hostname/port without reconnecting. The
   official SDK WebSocket transport still owns the Upgrade, masking and framing.
4. All nested read/write/poll calls share an absolute operation deadline. They
   cannot renew it by repeating short reads or writes. Startup is 10 seconds;
   ordinary send is 100 ms, receive 50 ms. These are software-observed deadlines,
   not a guarantee against CPU starvation or a blocked RTOS/TCPIP subsystem.
   Partial TLS writes continue the same in-progress bytes; an expired/interrupted
   frame poisons the session and is never logically retried or replayed.
5. The exact canonical text `{"type":"stop"}` alone enters a read-only
   finalization phase. It permits that control send and later reads after soft
   Stop, with a total three-second budget. Further sends are rejected. It cannot
   recover or replay a frame already interrupted by Stop.

A readable header followed by a payload timeout cannot be reported as harmless
idle: the shim preserves the operation until completion or terminal deadline.
Network reads require at least 16 bytes of scratch storage because the pinned SDK
can copy a masked header's four-byte mask into the caller buffer before rejecting
application input. The caller still owns bounded message/fragment assembly and
strict v2 controls. No RFC-conformance or live-interoperability claim follows from
fake-boundary checks.

## Ownership and cleanup

Exactly one task owns each TLS/WebSocket context. DNS completion uses a distinct
static slot and a lock; it never frees a network object. No task is forcibly
killed while holding SDK resources. Stop/timeout does not authorize freeing
storage referenced by an unfinished callback or owner.

As soon as the public socket exists during asynchronous connection, the binding
requires nonblocking mode and `SO_LINGER(0)`. The opt-in build must explicitly
provide `CONFIG_LWIP_SO_LINGER`; without it the binding fails closed. This avoids
waiting for peer ACK on normal teardown, not all scheduling delay. A failure
entirely inside the first SDK connection call can precede public-socket exposure
and policy setup; that cleanup path is **not proven to have a hard upper bound**.
The SDK's initial positive send timeout narrows its default close behavior but
is not a substitute for measured teardown acceptance.

Close marks `NET_CLOSING` before calling SDK destruction. If that owner stalls,
its controller must retain context and report pending cleanup, never invent
completion or free/reuse it on a timer. Pending DNS also keeps close false and
`NET_CLOSING` until its actual callback settles. Once all owned operations return,
close can be called again to establish completion. A context is never implicitly
restarted after terminal failure or close.

The pinned SDK can print an authenticated Upgrade request on an error path.
The surrounding opt-in build must compile `tcp_transport` with
`LOG_LOCAL_LEVEL=ESP_LOG_NONE`; merely avoiding application logs is insufficient.
No live logging or network configuration was changed by this work.

## Offline evidence

`tests/test_firmware_live_network.py` tests the real allocation-free generation,
tombstone and deadline core. `tests/test_firmware_live_network_idf.py` compiles
actual binding code against fake public IDF/lwIP boundaries. Harness assertions
cover late callbacks, cancellation admission, no auth before TLS-ready, original
host identity, positive connection slices, socket policy during handshake,
nonrenewable repeated short writes, interrupted-frame poisoning, graceful Stop,
finalization expiry, minimum scratch capacity, and pending-DNS cleanup reporting.
These tests perform no DNS, TLS, socket, provider or device operation.

The exact source basis is ESP-IDF v5.5.3:

- `components/esp-tls/esp_tls_mbedtls.c`: original-host SNI/name verification.
- `components/esp-tls/esp_tls.c`: async state machine, positive select timeout.
- `components/tcp_transport/{transport_ssl.c,transport_ws.c}`: supported parent
  transport APIs, certificate length convention, WS parser and auth-log hazards.
- `components/lwip/lwip/src/include/lwip/{dns.h,tcpip.h}`: callback ownership APIs.
- `components/lwip/lwip/src/api/api_msg.c`: close/linger behavior and residual
  scheduler/timer assumptions.

Actual firmware compile results are recorded by the checkpoint build owner.
Runtime jitter, heap headroom, DNS/handshake/teardown latency and physical audio
acceptance remain separate gates; no provider test is implied.
