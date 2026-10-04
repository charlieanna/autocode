# Grader returned before descendant termination (#304)

`judge_final_verdict` sent SIGKILL to its private process group, then waited
only for the adapter process. A successful numerical verdict could therefore
return while ordinary descendants were still exiting. The historical macOS
`?E` assertion failure was not proof of a persistent process leak.

## Reproduction

On `b854e5e9`, a correct conversion candidate launched eight children blocked
on pipes. The probe recorded PID, birth identity, group and signal timing,
then checked them immediately after grading and after a bounded observation
wait. All 12 invocations returned before at least one descendant stopped
(42 live-at-return observations). All descendants were gone after the wait;
an unrelated sentinel survived. A fresh OpenCode/GPT-6 Sol review independently
executed the probe and reproduced the gap in all 12 invocations.

## Change

The focused `autocode_grader_process` helper retains the unreaped session
leader until group ownership has been recorded, then uses the existing
birth-aware process supervisor to stop and wait for owned descendants. An
unverified cleanup raises instead of producing a grading result. Numerical
checks, protected-test identity and timeout verdicts remain enforced.

macOS can hide a zombie's process-group ID before its zombie status becomes
observable. An initial implementation failed this case; the first live review
also caught an exit between the status and group reads. The final helper
re-observes the same birth identity and direct parent within a bounded deadline.
It requires an actual zombie state before using the unreaped leader's private
group identity. An unknown status does not establish successful cleanup, and
a changed birth identity is rejected.

## Qualification

- Latest focused checks: 48 passed, including fast parent exit, unrelated
  process survival, incomplete-cleanup refusal, the exit-observation race and
  changed-birth refusal, plus the existing grading and architecture checks.
- A fresh GPT-6 Sol AutoCode review executed those 48 checks and all 12 real
  process-tree trials against the exact source-file hashes: zero owned
  descendants live at return, all sentinels preserved, approve with no findings.
- Affected-file gate: 1,338 tests across 90 modules passed. Mandatory fake
  catalogue: 54 PASS, one existing NOT_EXERCISED, one live-Investigator SKIPPED.
  Those results are separate from the live-model qualification.
- The earlier full-suite result (3,009 tests) predates this grader change;
  it is not presented as a full-suite pass for this revision.

Original failures, the first rejected live review and subsequent successful
receipts remain separate under the ignored
`.scenario-runs/remaining-defect-proof/grader-304/` evidence directory. Two
early test-control teardowns left disposable children; those exact identities
were checked and cleaned, and the negative control now retains the ownership
receipt for its teardown. No unknown process status was accepted as PASS.

This is ordinary owned-descendant cleanup, not a sandbox against deliberate
process escape. The live and local qualification above ran on macOS; Linux
validation belongs to this revision's CI result.
