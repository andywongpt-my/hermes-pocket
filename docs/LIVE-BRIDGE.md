# Owner-configured live bridge candidate

Run `python -m pocket.run_bridge --config owner-config.json` on the host after
installing requirements. Startup checks liveness/capabilities without a model call.
This entry point has not yet been deployed to the owner's machines.

Windows Desktop needs `websockets==16.0`. Speech decoding needs FFmpeg on PATH,
installed separately from a trusted vendor/package source. No binary is downloaded
or installed by the program. The mock/core require no third-party Python runtime.

The configuration accepts one or two explicit targets. Each uses a separate SQLite
file. Devices authenticate using dedicated Pocket credentials supplied in environment
variables; real credentials must never enter source control or release archives.

Example (all values are placeholders):

```json
{
  "host": "192.168.1.10",
  "port": 8765,
  "tls": {"certificate": "bridge-cert.pem", "private_key": "bridge-key.pem"},
  "devices": {"pocket-one": "POCKET_DEVICE_TOKEN"},
  "targets": {
    "windows": {
      "kind": "desktop",
      "origin": "http://127.0.0.1:REPLACE_CURRENT_DESKTOP_PORT",
      "token_env": "POCKET_WINDOWS_SESSION_TOKEN",
      "database": "windows-pocket.sqlite",
      "loopback_http": true,
      "speech": {
        "origin": "https://your-private-linux-dashboard.example",
        "token_env": "POCKET_LINUX_DASHBOARD_TOKEN",
        "stt_enabled": false
      }
    },
    "linux": {
      "kind": "gateway",
      "origin": "https://your-private-linux-gateway.example",
      "token_env": "POCKET_LINUX_GATEWAY_TOKEN",
      "database": "linux-pocket.sqlite",
      "loopback_http": false
    }
  }
}
```

Replace addresses locally. A Desktop port is dynamic; do not permanently assume a
previously observed port. Windows Desktop uses `/api/ws`, not `/v1/runs`.
Desktop session credentials, Linux gateway keys and dashboard authorization are
different identities and must not be substituted for each other. The bridge does
not discover or generate these credentials. Dedicated pairing and existing credential
setup require owner authorization and secure owner entry.

A bridge running on Linux can use loopback gateway/dashboard URLs. Remote backend
connections require verified HTTPS. An owner-approved existing secure tunnel can
provide explicit loopback endpoints, but this program does not create tunnels,
change firewall rules, accept invalid certificates or expose public ingress.

LAN listening requires an explicit private interface and certificate/key paths.
Use a certificate valid for the exact hostname/IP in the device URL. Public trust
roots are supported; alternatively provision the owner's CA certificate through
phone setup. Never disable certificate validation. Keep private keys on the host.

The HTTP service bounds concurrent connections to eight and authenticates every
request. Successful consumed requests support keep-alive for audio chunks. It is
an experimental private-LAN service, not internet-hardened; health deliberately
reports production_ready=false. Run one process per database.

## Voice

The firmware sends at most 30 seconds of 16 kHz mono PCM through bounded chunks.
The bridge asynchronously calls the authenticated dashboard transcription route,
admits a chat turn, polls the task, synthesizes the completed reply through the
existing Hermes voice configuration, and returns PCM chunks to the speaker.
Setting Pocket's flag does not modify Hermes: enable and verify a working STT
service with the owner's authorization first, preserving the configured TTS voice. No paid live model or
speech calls were made during cloud verification.

Raw audio is held in memory, not written into the SQLite journal. Transcripts and
replies are persisted per target/device. Incomplete restarted jobs become unknown;
provider calls are not automatically replayed. Open approvals are handed to the
trusted Hermes UI, never automatically granted.

## Remaining deployment decisions

- Select a reachable, trusted TLS bridge endpoint for the handheld
- Supply each backend's correct authentication through secure local entry
- Enable/confirm an STT service, and preserve the configured Linux TTS voice
- Configure usage data sources separately; currently all remain unavailable
- Flash only the cloud-built candidate after identity/partition/hash checks
- Owner performs real device acceptance; no successful live path is claimed yet

## Verified-host routing through an owner-approved SSH tunnel

A dashboard bound to a specific interface can reject the local tunnel's default
Host header. If and only if an authorized loopback SSH forward reaches that exact
dashboard, speech configuration may include `host_header` with the dashboard's
verified bound hostname and port. The transport sends that HTTP Host identity while
connecting exclusively to the configured numeric loopback endpoint. This option is
rejected for remote origins. It does not disable dashboard authentication or Host
validation. Do not guess the accepted host or broaden server trust settings.

The optional cached-model helper is described in ../helpers/README.md. Host-only
compatibility changes do not change the previously built firmware image.
