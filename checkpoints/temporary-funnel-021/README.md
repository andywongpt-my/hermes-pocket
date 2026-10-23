# Temporary ingress source checkpoint

`pocket_funnel_021.py` preserves the bounded, authenticated route allowlist and
verified TLS upstream proxy. `nginx.conf.example` is an alternate configuration
example. All upstream addresses and owner paths are inert examples; no credentials,
private CA material or deployment receipts are supplied. The CA pin and guarded
configuration require owner review before any separate deployment.

Python checks require `aiohttp` and use synthetic loopback fixtures:

```sh
python -m unittest test_pocket_funnel_021 -v
```

`test_funnel_proxy.py` is a separate nginx executable integration harness. It is
not a unit-test deployment instruction; run only against explicit local fixtures.
Current publication test results and limitations are in the root checkpoint checks.
The latest separately reported live check (23 October, 15:59 UTC) found the temporary
unit absent and public HTTP 502, while general Hermes remained running. Nothing in
this checkpoint starts a service or restores public access.
