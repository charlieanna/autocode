# Capture helpers for specialized provider jobs (#327)

A real Luna Investigator received a configured provider's instruction to use
`capture_command` from its handoff, but that field was absent. It searched the
installation and invoked `autocode_capture_command.py` directly; that module
exited 0 without running capture or creating receipts. The Investigator still
diagnosed the bug through ordinary commands and the runner's independent probe.
The problem was an inconsistent tool contract, not a false diagnosis.

Specialized jobs assemble their own handoff packets. A shared, cycle-free
`autocode_tool_handoff` helper now supplies the public executable capture command
when missing. Both native OpenCode and registered command providers use it.
Existing commands, output modes and packet bytes are preserved. The default
inherits `AUTOCODE_OUTPUT_MODE`; provider permissions and evidence validation
remain unchanged. A malformed structured handoff fails before provider dispatch;
markerless prompts retain their existing behavior.

## Verification

The original live failure is retained under
`.scenario-runs/remaining-defect-proof/source-recovery-313/captured-current/`.
The provider and bug-job prompt code at that runtime was unchanged through base
`122f1c8e`. Before this fix, the new six-job/three-provider-path prompt matrix and
public capture-CLI regression failed in 19 subcases.

Fresh live qualification of the candidate is retained under
`.scenario-runs/remaining-defect-proof/capture-handoff-327/`:

| Actual model | Transport | Accepted Investigator | Real receipts |
| --- | --- | --- | --- |
| GPT-6 Luna | configured Codex, report file | 68.8 seconds | 2 |
| GPT-6 Sol | native OpenCode | 91.6 seconds | 3 |
| GLM 5.3 | native OpenCode | 151.2 seconds | 6 |

Each run made one actual model call, returned an accepted `reproduced` diagnosis
and stopped at the requested checkpoint before planning. These qualify the
Investigator/tool handoff, not subsequent full bug-fix completion. Each kept the
original 600-second run, 240-second stage, 120-second idle and three-iteration
limits; source and protected-test bytes/modes stayed unchanged. No model response,
run checkpoint, approval or receipt was fabricated or edited.

The audit matches every receipt to the actual completed capture command, its
exit code and exact output hash, and verifies report references. Luna's receipts
also match the current report-file attempt/source capture context. Native event
providers use their actual tool events. GLM preserved exit 1 both for a deliberately
wrong expected result and for a test-discovery invocation error; it reported the
latter as setup failure and ran a valid loader-based check. No failed tool call
was erased or counted as a passing test.

The first native fixture was intended for GLM but used Sol: `--investigator-model`
pins stuck-run diagnosis, while bug investigation inherits the Plan Reviewer
route. This setup error was caught from the saved execution record. That passing
Sol fixture was retained, and a separate fixture pinned the correct GLM route.
The audit also had to account for ordinary non-JSON transport diagnostics; its
initial parser failure is retained separately and did not change run artifacts.

Current checks: 69 focused tests, 184 affected tests across 16 modules, and 75
registered-provider CLI/report-repair tests passed. The supplementary catalog
passed 54 scenarios, with one existing NOT_EXERCISED and one live-Investigator
SKIPPED. Runtime files match the live source pins. These are the checks for this
change; the previous 3,282-test full suite ran on `122f1c8e` before this delta.

## Remaining output-store defect

Luna and Sol captured from inside the investigation copy. Their five receipts
preserved exact raw logs but fell back to unfiltered display with `ValueError`;
the inherited parent output-store path failed the helper's CWD containment check.
The public display-operation counters stayed zero. GLM captured from the main
workspace and retained output blobs. A paired real CLI control confirmed the
CWD/store mismatch. This separate storage, retrieval and accounting defect is
tracked in [#329](https://github.com/charlieanna/autocode/issues/329); this helper
fix does not resolve it or claim output savings.
