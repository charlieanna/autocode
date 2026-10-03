# Task prerequisite qualification (#252)

PR #275 established runner-environment readiness. Version 2 adds worker
execution, portable design context, deterministic browser setup and named
proof eligibility. Use [the manifest guide](../task-preflight-v2.md) to declare
the task's actual inputs and approved probes.

## Model-free tool qualification, 2026-10-03

The following checks used installed tools in an isolated checkout on macOS:

| Tool/check | Observed result |
| --- | --- |
| Codex CLI 0.160.0, native read-only sandbox, attempted write | Write denied, as required |
| Codex workspace-write sandbox, real Chromium launch | Blocked by macOS Mach-port permission; preflight refused readiness |
| OpenCode 1.18.33, actual bash tool | Command executed and its tool exit status was retained |
| OpenCode planning agent, actual read tool | Complete context text and PNG attachment matched the approved bytes |
| OpenCode, Playwright 1.62.1, actual Chromium capture | Ready: 652×188, DPR 1, declared white canvas, Inter font bytes loaded, completed nonempty catalogue, screenshot captured and teardown finished |
| Same browser probe, missing font file | Blocked |
| Same browser probe, incomplete catalogue | Blocked |

These probes invoked `codex sandbox` or `opencode debug agent`, never a paid
model. OpenCode used isolated configuration/data/cache directories. The
browser fixture and image were synthetic; this was not a fresh Figma export,
live-model campaign or visual acceptance of the original composer run.

## Offline regression coverage

The public TaskRun tests exercise the actual CLI launch boundary with a
standalone fake provider: a worker denial or an incorrect browser-state
receipt produces zero model dispatches; an explicit correction permits one;
a pause arriving at the end of the probe still prevents dispatch. The
existing v1 tests retain named/discovery isolation, nested scratch execution,
input-copy checks, receipt invalidation and recovery coverage.

Structured-contract tests reject truncated design derivatives, omitted
references, mismatched viewport/canvas/font readiness, aggregate-only proof,
incompatible protected-test policy, missing selectors, setup/teardown errors
and broken controls that pass. A later subtest assertion cannot conceal an
earlier setup error. Readiness receipts remain separate from candidate proof.

## Scope of the evidence

The local full-suite attempt was not green: six modules failed, including
provider-flow and dashboard-browser timeouts. The OpenCode chat timeout was
also reproduced on the untouched PR #275 merge; its four flow tests passed
with CI's Python 3.11. The GoCode 30-second timeout reproduced on that same
untouched baseline with Python 3.11. These observations do not establish the
cause of every full-suite failure. Consult the PR's exact-head CI results
and validation report before merging.

On the rebased branch, 58 focused tests passed with Python 3.11, including
preflight, architecture, output-transport integration and the report-repair
cases that previously timed out. The separate general subprocess rerun
passed all 15 tests. The full fake catalog produced 53 PASS, one existing
NOT_EXERCISED and one live-Investigator SKIPPED; all 118 harness tests passed.
Those catalog/harness runs preceded the rebase. The original full-suite
failure remains recorded rather than being relabelled as a full pass.

Required worker probes fail closed on GoCode and custom command transports
until those transports provide a supported model-free adapter. OpenCode
tool permissions are not an OS sandbox; its receipt does not claim native
sandbox attestation. Unresolved approval-bearing policies block diagnostic
execution instead of being silently approved.
