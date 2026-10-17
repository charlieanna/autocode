# AutoCode-owned direct connections (#1493)

Connections previously required an installed coding-agent backend. The owner
requested AutoCode setup and direct model access when Kilo/OpenCode are absent.
This change adds an always-present AutoCode card, private terminal setup, direct
MiMo, Z.ai and OpenAI Chat Completions plus Claude Messages transports, and a bounded first-party
text/shell runtime behind the existing supervised command-provider contract.
It does not change the working Kilo routes or automatically migrate credentials.

## Deterministic evidence

The final runtime/configuration/transport/provider candidate was copied to a
read-only source snapshot. Tests ran as uid 501 in a separate disposable Linux
container, with networking disabled, no credentials, and no external agent on
PATH. Source/test manifest SHA-256:
`fa114027fa9460fdc82664ff5f9cdfc8e21ed78f3d7507570fadc33f57422cd6`.
The manifest and snapshot remain under ignored
`.scenario-runs/direct-1493-20261016/`; no logs or credentials are tracked.

```sh
docker run --rm --name autocode-direct-1493-api-final \
  --network none --pid host --user 501:20 \
  --security-opt seccomp=unconfined --security-opt systempaths=unconfined \
  --env PYTHONDONTWRITEBYTECODE=1 --env HOME=/tmp/home \
  --mount type=bind,source="$SNAPSHOT",target=/src,readonly --workdir /src \
  --entrypoint python3 autocode-pr1425-qualification:20261016 \
  -m unittest tests.test_direct_connections tests.test_direct_connections_cli
```

**22 passed in 26.603 seconds**, including:

- Setup appears with no Kilo/OpenCode/Codex/Claude executable on PATH. API keys
  remain private, absent from status/argv/logs, and bound to their endpoint.
  Rotation preserves role assignments and transport identity; endpoint changes
  cannot implicitly reuse another destination's key. Cancelled setup is clean.
- Local HTTP fixtures exercise actual request/response and tool-result messages,
  HTTP 401/403/429, redirect refusal, real command exit 7, bounded output and
  usage accounting. Named-provider fixtures verify MiMo authentication, Z.ai
  token limits, original reasoning replay, and Claude tool blocks, signed
  thinking replay, cache usage, HTTP errors and unsupported effort/content.
- Public `TaskRun.start(..., "hi", --provider autocode --joint-planning
  --spec-first)` launches the real supervised runtime through local HTTP,
  reaches only Job recognizer and Requirements, and returns the outcome
  question with no displayed plan or approval, through both Chat Completions
  and Claude Messages fixtures. No external coding agent runs.
- A public discussion task completes through a real sandboxed `cat README.md;
  exit 7` command, with its actual output/exit delivered back to the API fixture.
  Read-only tools cannot change source or read an outside credential file;
  Builder tools can write source but cannot write the protected run store.
- The configured model roster prevents escalation onto an unconfigured model.

Additional focused checks on the corrected provider implementation: **55 tests
passed in 13.530 seconds** (`tests.test_architecture`, `tests.test_connections`,
`tests.test_cli_documentation`, `tests.test_provider_registry`,
`tests.test_ci_test_selection`). Ruff passed for the four direct modules and
both direct test modules; mypy passed for the four direct modules. The unchanged
extension had 76 passing tests plus typecheck/build in the earlier candidate;
that evidence was not rerun for this core-only correction. No full-suite claim.

## Earlier attempts and classification

The initial macOS command `venv-python -m unittest
 tests.test_direct_connections tests.test_connections` ran 20 tests in 1.189
seconds with five errors because the sandbox forbids binding local HTTP fixture
sockets. The same HTTP fixtures subsequently passed in Linux.

The first Linux command was `docker exec --env PYTHONDONTWRITEBYTECODE=1
 --workdir "$CHECKOUT" autocode-pr1425-real-requirements python3 -m unittest
 tests.test_direct_connections_cli`: two tests, 35.384 seconds, one failure and
one error. The existing container denied bubblewrap's `/proc` mount. Its greeting
also correctly refused with `PAUSED_TRANSPORT_CHANGED` because source formatting
ran during that test. No inference request was made in that attempt. The fix was
an independent test container with the necessary Docker system-path support and
an immutable source copy, preserving the actual kernel boundary and source-drift
refusal. The existing application container was not reconfigured.

The native VS Code onboarding check was attempted, but macOS was locked and the
computer-use interface could not unlock it. An isolated development workspace
was prepared with private empty configuration and no external backends on PATH;
no credentials were entered and the user's existing chat/settings were not
changed. Native visual verification remains pending unlock.

During the corrected-provider check, the first command (same container arguments
as above, named `autocode-direct-1493-api-tests`) ran 22 tests in 26.041 seconds
with one fixture error: its Claude responder mistook an initial user content
block for a tool result. The runtime correctly refused the disconnected
response. The fixture now identifies `tool_result` explicitly; the final check
above includes the correction. No paid baseline or fallback was attempted.

## Readiness

The owner's corrected scope is Kilo plus direct MiMo, Z.ai, OpenAI and Claude
APIs. Bedrock setup, transport and dependency were removed. One direct provider
is selected per connection; existing mixed-provider Kilo token-plan routes are
unchanged. No real direct API credentials were entered or migrated, and no paid
model calls were made. These fixtures do not establish live compatibility or
model quality. Native VS Code verification was retried and remains blocked by
the locked Mac. Keep the implementation PR draft until the required native/live
checks pass with an explicitly configured connection. The initial runtime is
text/shell only; native images/MCP and external-agent session resumption remain
unsupported. Claude reasoning-effort overrides are currently refused.
