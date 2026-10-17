# AutoCode-owned direct connections (#1493)

Connections previously required an installed coding-agent backend. The owner
requested AutoCode setup and direct model access when Kilo/OpenCode are absent.
This change adds an always-present AutoCode card, private terminal setup, direct
Chat Completions and Bedrock Converse transports, and a bounded first-party
text/shell runtime behind the existing supervised command-provider contract.
It does not change the working Kilo routes or automatically migrate credentials.

## Deterministic evidence

The final runtime/configuration/transport/provider candidate was copied to a
read-only source snapshot. Tests ran as uid 501 in a separate disposable Linux
container, with networking disabled, no credentials, and no external agent on
PATH. Source/test manifest SHA-256:
`e076e1a8b293f01347d3547862810da71340a84d770d9d0a54ddc68895c8562f`.
The manifest and snapshot remain under ignored
`.scenario-runs/direct-1493-20261016/`; no logs or credentials are tracked.

```sh
docker run --rm --name autocode-direct-1493-release-tests \
  --network none --pid host --user 501:20 \
  --security-opt seccomp=unconfined --security-opt systempaths=unconfined \
  --env PYTHONDONTWRITEBYTECODE=1 --env HOME=/tmp/home \
  --mount type=bind,source="$SNAPSHOT",target=/src,readonly --workdir /src \
  --entrypoint python3 autocode-pr1425-qualification:20261016 \
  -m unittest tests.test_direct_connections tests.test_direct_connections_cli
```

**19 passed in 16.775 seconds**, including:

- Setup appears with no Kilo/OpenCode/Codex/Claude executable on PATH. API keys
  remain private, absent from status/argv/logs, and bound to their endpoint.
  Rotation preserves role assignments and transport identity; endpoint changes
  cannot implicitly reuse another destination's key. Cancelled setup is clean.
- Local HTTP fixtures exercise actual request/response and tool-result messages,
  HTTP 401/403/429, redirect refusal, real command exit 7, bounded output and
  usage accounting. AWS client fixtures verify Converse tools/results, safe
  error handling, SDK absence, and refusal of unsupported reasoning settings.
- Public `TaskRun.start(..., "hi", --provider autocode --joint-planning
  --spec-first)` launches the real supervised runtime through local HTTP,
  reaches only Job recognizer and Requirements, and returns the outcome
  question with no displayed plan or approval. No external coding agent runs.
- A public discussion task completes through a real sandboxed `cat README.md;
  exit 7` command, with its actual output/exit delivered back to the API fixture.
  Read-only tools cannot change source or read an outside credential file;
  Builder tools can write source but cannot write the protected run store.
- The configured model roster prevents escalation onto an unconfigured model.

Additional focused checks: **76 extension tests passed**, plus typecheck/build;
68 architecture/run-state/CLI/provider-registry/CI-selection tests passed; 38
connection/CLI-documentation/shell-completion/selection tests passed with one
shell-dependent skip. Ruff and mypy for all four direct modules passed. These
are overlapping focused groups, not a claimed full-suite run.

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

## Readiness

This is fixture evidence, not live model-quality or enterprise authentication
qualification. No real API credential or company Bedrock account was used, no
paid model requests were made, and GoCode's corporate sign-in protocol remains
unverified. The initial runtime is text/shell only; Bedrock model-specific
reasoning/signature content, native images/MCP and agent session resumption are
unsupported and fail closed. Keep the implementation PR draft until the
remaining native/live checks pass with an explicitly configured connection.
