# Exact output from investigation copies (#329)

Five real Luna/Sol captures from prepared investigation copies preserved their
raw logs but reported `ValueError` fallback, produced no exact-output blobs and
left completed-stage operation counts at zero. The runner supplied the main
workspace's store; the helper checked that store against the nested current
working directory. A paired real CLI control isolated that mismatch. Clearing
the store override restored scratch-local retention but still missed run-level
accounting, so it was not a complete fix.

The runner now also supplies an absolute `AUTOCODE_OUTPUT_WORKSPACE` binding.
Capture and output helpers validate their resolved current directory against
that workspace and retain the existing store containment check. The same parent
store serves capture, exact retrieval and attempt accounting. Standalone commands
retain their current-directory boundary. Source reads remain limited to the
current directory, and existing symlink, hash and immutable-receipt checks remain
in force. This environment binding is routing context, not a filesystem access
grant; providers must preserve it. No state field or model-specific branch was
added. Old receipts and saved measurements are not rewritten.

## Fresh live verification

On base `814abb6a` plus this change, three new Investigators ran against disposable
bug fixtures. Actual execution records identify the models and transports:

| Model / transport | Duration | Captures | Successful retrievals | Public operations |
| --- | ---: | ---: | ---: | ---: |
| GPT-6 Luna / configured Codex report file | 83.3 s | 3 | 3 | 6 |
| GPT-6 Sol / native OpenCode | 119.1 s | 6 | 2 | 8 |
| GLM 5.3 / native OpenCode | 167.6 s | 5 | 5 | 10 |

All 14 captures ran inside investigation copies, matched actual executed command
output and retained blob bytes identical to their raw logs. None used the old
fallback. Completed-stage public counters exactly match the 24 saved capture and
retrieval operations and their byte totals; attempt identities match the actual
stage event files. Report-file receipts also match their source/attempt/nonce.
The measurements show more displayed bytes than raw bytes for these tiny outputs;
no token or cost reduction is claimed.

All three reports were accepted in one model call each. They stopped at the
requested checkpoint before planning: this qualifies the output path, not a full
bug-fix build. Original source/protected-test bytes and modes stayed unchanged.
Limits stayed at 600 seconds per run, 240 per stage, 120 idle and three iterations.
No checkpoint, model response, receipt or approval was edited.

GLM first used an invalid unittest discovery command (exit 1), then successfully
ran the existing test module. Its first shell retrieval loop passed five empty
hashes because of shell word-splitting assumptions; retrieval and comparison each
returned 2. It corrected this with five explicit retrievals and successful byte
comparisons in the same call. Those failures remain in the evidence. The audit
initially rejected the failed capture because its outer `echo` wrapper exited 0;
it now explicitly checks the printed inner exit 1 and retained failure log,
and checks each corrected retrieval's exit code and bytes. An outer shell success
is not counted as success for an inner command.

## Regression checks and evidence

Three of four new real CLI tests failed before the implementation change. After
it, 24 focused output/policy/filter/architecture tests passed, and the affected
suite passed 11 tests across three modules. These include binary bytes and a
nonzero command exit, cross-directory retrieval, cross-attempt accounting,
immutable receipts, corrupt blobs, external paths and symlinks, unchanged source
read boundaries, and standalone behavior. These local checks supplement the live
qualification above. The supplementary catalog passed 54 scenarios, with one
existing NOT_EXERCISED case and one live-Investigator SKIPPED case.

Before evidence is retained under
`.scenario-runs/remaining-defect-proof/capture-handoff-327/`; fresh qualification,
original failures, source/runtime pins and the independent audit are under
`.scenario-runs/remaining-defect-proof/output-workspace-329/`. No run artifacts
are committed.
