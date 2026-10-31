# Explicit host embedding opt-in

`pocket.run_bridge.build(config, *, live=None)` accepts an optional mapping from
an existing configured target (`linux` or `windows`) to an already constructed
`pocket.live_websocket.LiveEndpoint`. This connects trusted embedding code to the
existing authenticated `/targets/<target>/live` route. It is **off by default**:
`None` and an empty mapping leave Live unavailable. There is no new JSON config
key, CLI flag, environment toggle, credential discovery, SDK/client constructor,
or provider call in this wiring. The existing CLI still calls `build(config)`.
The legacy bridge still reads its explicitly named backend/device credentials
and probes backend capabilities as before; these are not Live credentials.

## Contract, not a deployment recipe

An embedding application that already owns an appropriately configured client
and trusted fresh-per-session echo factory may use:

```python
from pocket.live_websocket import LiveEndpoint
from pocket.run_bridge import build

# owner_client and paired_echo_factory are supplied by the trusted application.
# No SDK/key/native-library setup is performed by this snippet.
endpoint = LiveEndpoint(owner_client, paired_echo_factory)
server, resources = build(owner_config, live={"linux": endpoint})
try:
    server.serve_forever()
finally:
    server.server_close()
    for resource in reversed(resources):
        resource.close()
# The embedding application remains responsible for closing owner_client.
```

This code is an interface illustration, not authorization to start a service.
`build` constructs the existing listener; starting a configured host remains an
explicit operational action. Only targets already in `owner_config['targets']`
may be supplied. Non-mappings, unknown targets and non-`LiveEndpoint` values are
rejected before credential reads, backend probes, database creation or listener
construction. The mapping is copied; the endpoint/client objects are not copied
or replaced. An endpoint is a trusted object, not a serialized configuration or
a client-validation sandbox. Construction does not call its echo factory or
validate live provider availability.

For wire v2, the trusted factory must return a **fresh `PairedSpeexEcho`** using
the separately audited, explicitly supplied SpeexDSP library path. Host wiring
does not synthesize a capture reference or downgrade to v1. Existing device
protocol dispatch and echo compatibility checks remain responsible for rejecting
unsupported pairings. The existing bridge still requests exactly `gpt-live-1`;
no alternate model, ordinary Realtime fallback or reconnect policy is added.

## Ownership and failure cleanup

The client inside each `LiveEndpoint` is **borrowed**. Neither startup failure,
`server.server_close()`, nor the returned resource cleanup calls its `close()`
method. A shared client must remain alive while its sessions are active; the
embedding owner closes it after all its uses finish, with the API appropriate to
that client. Session-specific provider connections/SDK contexts are separately
closed by the existing session bridge. The per-session echo object is cleaned
up by the existing device adapter; a shared echo instance is not a supported
factory design.

On later startup failure, `make_server` unwinds its partial registry, voice and
spool ownership; `build` closes its accumulated stores/adapters in reverse
order. Normal shutdown closes the server/session registry before returned
resources. Do not close a store beneath an active session. Existing incomplete
session/voice cleanup exceptions remain visible; they are not converted into
success or permission to dispose of still-used resources.

## Offline verification and limits

`python -m unittest discover -s tests -p test_live_host_wiring.py -v` covers six
cases: environment-independent default-off, explicit endpoint identity/mapping
snapshot, empty opt-in, early invalid mapping/type/target rejection, JSON-key
rejection, and listener-startup failure cleanup without client ownership theft.
Tests construct real stores, services and server handlers. Only backend HTTP
transport and listener bind/activation are replaced; no provider, real
credential, running listener or physical board is involved.

This change installs no configuration or production secrets, starts no service,
changes no network/security setting and deploys/flashes nothing. It does not
open the physical acceptance gate. Wire-v2 pairing, actual acoustic quality and
physical lane/clock checks remain required; digital DMA completion remains
**not evidence of audible playout**. See [v2 protocol](GPT-LIVE-DEVICE-V2.md),
[AEC](GPT-LIVE-AEC.md) and [Live integration](GPT-LIVE.md).
