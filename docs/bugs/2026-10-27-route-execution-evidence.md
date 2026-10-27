# Actual launch identity in qualification evidence (#2059)

Qualification previously retained role/model measurements without a structured
record of the backend that executed them. Reading current settings later cannot
recover that fact: providers, efforts and selected billing routes may have moved.

New provider launches freeze schema-1 `execution_identity`: the actual route
slot, backend, engine, model, effective effort and runner commit when available.
Public accounting carries validated facts and the existing qualifier groups them
with explicit known/unknown attempt counts. Historical attempts stay unknown.
Billing classification is copied only from an exactly matching saved selected
model and backend, with that basis recorded; a candidate account pool is not
proof of the billed account. This is not an invoice or observed charge identity.

Direct connections additionally record the configured origin, endpoint SHA-256,
protocol and provider slot. Tenant paths and credentials are excluded from public
facts. Native/opaque backends remain unknown for endpoint identity. An endpoint
hash does not attest redirects, compatibility or an external provider version.
Blank/null effort preserves a provider default; configured route effort must not
overwrite the effective effort after existing launch policies. A modified/unknown
runner commit is not a full code-byte fingerprint.

## Verification

The unchanged baseline failed three focused controls: stage-record retention,
qualifier execution context and direct endpoint metadata. The corrected code
passed 198 focused local tests in 19.070 seconds, including architecture,
run-state, public model catalogue and CI-consumer selection. Ruff and diff
whitespace checks passed.

Supported-Linux checks used the existing isolated test container, with no model
calls or credential reads:

```sh
/tmp/home/sweep-venv/bin/python -m unittest \
  tests.test_direct_connections_cli tests.test_provider_launch \
  tests.test_provider_launch_containment tests.test_direct_connections \
  tests.test_autocode_request_usage tests.test_route_execution_evidence
```

All 111 tests passed in 46.411 seconds. The compatible and Anthropic localhost
HTTP regressions traverse the supervised CLI and assert actual public accounting
facts; credential-bearing URLs and stale role/model/engine/effort bindings are
negative controls. Earlier failed attempts exposed a legitimate blank provider
effort, which is now retained exactly. Their logs remain outside version control.

This measurement slice does not enforce comparative admission or change
provisional eligibility. It does not provide the missing 50-issue benchmark
inputs, verified-task denominators, external benchmark results or whole-task
latency distributions. It does not prove a delivery-speed improvement. The
canonical evidence-document export does not yet include these new fields;
they are available in public accounting and qualifier records. #2059 remains
open for those remaining requirements and the shared policy decision with #2061.
