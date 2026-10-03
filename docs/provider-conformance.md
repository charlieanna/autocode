# Check a tool/model combination

[Back to provider setup](providers.md)

AutoCode needs the same observable contract from each coding tool: the intended
workspace, structured reports, actual command evidence, terminal status, usage and
the correct session. The developer probe below checks these rules through native
Codex, the OpenCode adapter and the registered KiloCode adapter. It does not change
AutoCode's model-selection or escalation policy.

Run from this checkout with the venv interpreter:

```sh
# No network or model requests. Exercises all three CLI transports.
.venv/bin/python tools/provider_conformance.py --fake

# An intentionally broken transport must exit 1, with command_evidence failures.
.venv/bin/python tools/provider_conformance.py --fake --fake-fault forged_evidence

# Uses existing provider logins; at most three turns per route, no model retries.
.venv/bin/python tools/provider_conformance.py \
  --route codex=gpt-6-luna \
  --route opencode=openai/gpt-6-luna \
  --route kilocode=openai/gpt-6-luna \
  --timeout 180 --i-authorize-live-model-spend
```

Choose models available in your accounts. Repeat `--route` to compare models,
including `opencode=zai-coding-plan/glm-5.3` when connected. Native Codex probes
require ChatGPT login and refuse API-key/endpoint overrides. OpenCode and KiloCode
use their normal route preflight; OpenAI routes require OAuth. Other routes use
their configured billing. No fallback model or provider is selected.

Each route gets a fresh Git fixture under a long, randomized path containing spaces.
The Builder reads an unknown nonce from a file, copies it into the permitted output,
and runs a successful check plus an intentional exit-7 check. A fresh Validator
rechecks the result without changing source, then resumes its own session for a new
probe ID. Sessions are never shared across models or tools.

| Checked property | Independent evidence |
| --- | --- |
| Correct workspace and current task | Exact workspace, nonce and new probe ID in the schema-validated report |
| Permitted changes and final output | Before/after source hashes, Git HEAD and exact output bytes |
| Successful and failing commands | Unique completed command events, actual exit codes and expected output |
| Completion | One completed turn, no provider errors, successful process exit and a valid report |
| Session handling | Builder/Validator separation; resumed Validator retains its session ID |
| Usage | Nonnegative input, cached-input and output counts; missing counts fail instead of becoming zero |

Reasoning tokens are already included in normalized output and are not added again.
Usage is provider-reported; the probe does not reconcile an invoice. Reports bind
their two commands to events using the runtime's command comparison. This small
schema does not exercise every production stage schema or capture-receipt variant.

Results, original transport logs, normalized events, prompts and reports remain in
`.scenario-runs/provider-conformance/`. The printed `summary.json` includes tool
identity, requested model/effort, source revision and relevant source-file hashes.
Each route stops on its first failed phase. `UNAVAILABLE` is never counted as a pass.
Exit status is 0 only when every requested route passes, 1 on failure/unavailability,
2 on invalid CLI arguments, and 130 when a running probe is interrupted. The runtime's
process supervisor handles deadlines and interruption; this probe does not resume
an interrupted campaign or retry timed-out calls.

`--fake-fault` also accepts `malformed_report`, `wrong_exit`, `unexpected_write`,
`incomplete_turn`, `wrong_session`, `missing_usage`, `wrong_nonce`, `stale_report`,
and `process_failure`. These variants test that the oracle rejects each failure.
Routine unit tests keep CLI coverage small and test the remaining rejection rules
as pure logic. Fake success proves adapter/runner behavior, not model compliance.

A live pass is evidence for this small fixture on the recorded configuration. It
does not prove equal reasoning ability, performance on larger engineering tasks,
cross-model workflow quality, or resistance to a hostile agent. Read-only checks
observe source preservation; OpenCode/KiloCode permissions are not an OS sandbox.
Continue using the [scenario harness](../scenarios/README.md) and independent task
oracles for end-to-end engineering quality and the existing process tests for
detached-worker cleanup and timeout behavior.
