# Follow-up repairs from the Codex-only scenario campaign

The campaign at `b0e8d8ea` finished all 48 scenarios, with 20 passes. The
remaining outcomes mix application defects, non-converging planning and review,
and bounded operational stops. They are not 28 interchangeable runtime bugs.
PR #191 fixed oracle execution errors and an archive reference/coverage gap;
the prose verification replay fix was already on master.

## Planning loses a report correction after clarification

The inventory run repeatedly cited `.autocode/state.json` as repository source.
The Investigator's correction reached its immediate retry, but clarification
retired the active guidance. A later planning cycle repeated the same rejected
citation, and the one-investigation-per-problem limit correctly prevented a
second investigation.

`autocode_stuck_job.with_guidance` now reads accepted planning report corrections
from the existing investigation history. They remain prompt context across
clarification, without resetting a repair allowance, an investigation limit,
approval, permissions or a spend cap. Only `stage_output` diagnoses triggered by rejected reports are reusable, including
repeated invalid output; convergence, environment and user-decision advice is excluded.
Lessons remain run-wide because investigation identities are spent run-wide. Active
guidance is appended last and wins conflicts, without duplicating its history row.
The configured investigation limit caps retained lessons. The prompt calls these
earlier corrections, not verified successful retries.

A public CLI fault-injection regression reproduces rejection, investigation,
clarification and re-planning. It fails against the previous runtime and
completes against the repaired runtime, with one investigation and one answer.
Pure tests cover scope boundaries and the absence of state mutation.

Planner instructions also distinguish source citations from runner context,
retain settled literal requirements and saved answers, and avoid promoting
report wording or a mistaken example into another product decision. These
instructions target the observed citation and repeated clarification failures;
they are not a guarantee that every model plan will converge.

## A draft verification correction is mistaken for a product decision

The timesheet and outbox live runs exposed a separate loop: the Plan Reviewer
correctly replaced a recursive "test the whole test suite" criterion with an
ordinary suite command, but the revision guard rejected that proof change before
the plan had ever been approved. Report repair restored the unusable proof and
planning restarted or asked another question.

After review, the guard permits only planner-generated draft proof corrections,
with identical ID, criterion text and human-review requirement, no current approval
receipt, and no matching approved or user-authored proof in contract history.
Approvals are matched by revision/hash token and criterion proof rather than any
approval ever recorded. A test:/guard: proof cannot be downgraded to prose or a
suite command without a saved user basis. An unapproved planner draft may add a
human-review requirement that has never been approved or user-set. Removing one,
or changing an approved or user-set requirement, needs saved user backing even
when text and method are unchanged. Protected-list and permission guards remain.

This deliberately narrows the original repair: a recursive test-to-suite-command
replacement now stops for a user-backed correction. CLI coverage checks both an
ordinary draft command repair completing without a question and a prohibited
downgrade stopping before approval/build. Verification written only in the original
free-form task has no structured provenance; that existing limitation remains.
Planner instructions now match the guard, including protected criterion text.
The pure guard remains in `autocode_contract_revision` behind its existing public API.

## Completion reports retype approved criteria and builders rename tests

The outbox repair passed all ten oracle checks, but its Completion Owner inserted
one word into an approved criterion. The existing guard correctly rejected the
report. The `astra_review` completion decision now requests IDs, status and evidence
without criterion text. Runtime validation requires the complete ordered IDs and rejects
unknown, duplicate, missing or mixed-shape rows. Legacy full-text reports must match
exactly; a copying error enters bounded report repair. Only validated ID-only rows
receive runner-owned text before the canonical report is saved, so disk readers
and the controller receive the same complete report. The existing `.reported.json`
artifact and `reported_output` record preserve the original response; no hydration
marker is added. Identity and evidence guards remain in force. The real nested
checkpoint schema retains its full-text requirement. Report-only repair preserves
the original saved schema bytes, including full-text schemas from older runs.

The archive repair passed its application checks but renamed two original tests
to match newly planned case IDs. Both the oracle and regression proof reject
removed test names. Planner and Builder instructions now retain those names and
assertions, adding separate case tests when needed.

## Repair the generated cache, outbox and extractor

Three new catalog scenarios retain the actual failing application source as
their seed, with the original tests. Each has a repaired reference, an unchanged
negative control with the new tests, and independent hidden contract checks:

- `bugfix-cache-numeric-deadline`: compute a valid deadline before mutating cache
  entries. Retain ordinary numeric rounding, using exact rational arithmetic
  only when conversion raises or finite float addition overflows. Cover a large
  integer TTL with a float clock and expiration at an overflowed float sum.
- `bugfix-outbox-query-limit`: saturate only the internal SQLite limit at its
  representable maximum, preserving the unbounded positive Python input contract,
  event order, durable identity and acknowledgement after successful delivery.
- `bugfix-archive-corruption-cleanup`: validate directory payloads, normalize
  decompression errors to the requested `ValueError`, and clean staged files
  before re-raising. Cover stored and compressed corruption with both absent
  and existing empty destinations.

The original application module SHA-256 identities are respectively
`9bea088551b0176b15080ee55c4c34ba10635fdcc4633b4f158828f5ef94c8c0`,
`268f0654b8e2fbfee8c492429cdcee667f8c540ae79e343b208996942397cd7e`,
and `c939e21f97cd4fb4484d4005e41dd4b4a17df3e7b1240aa7f371a3082607dd85`.
Original campaign evidence remains unchanged. Reference repairs are not
counted as successful live AutoCode completions.

Planner and Validator instructions use general mixed-type numeric and staged-failure
guidance, without embedding the new scenarios’ specific edge cases in every prompt.

## Review an accepted tradeoff within its stated scope

The sound-design review blocked rollback because redeploying the old consumer
restores its old double-charge bug. The design explicitly accepted that
consequence. Architect instructions now require a blocking concern to identify
the binding requirement the tradeoff violates, rather than silently extending
the new version's guarantee to rollback. Missing rollback procedures, incompatible
data and violations of explicit rollback guarantees remain blocking concerns.

## Mobile header padding breaks the dashboard accessibility gate

The full suite reproduced a 57 px mobile header where the normal layout requires
56 px. A 44 px control, two 6 px padding edges and a 1 px border exceeded that
height. Reducing vertical padding to 5 px preserves the minimum hit area and
allows the header to expand for enlarged text. The existing browser accessibility
suite checks both breakpoint geometry and text resizing.

## Operational stops remain bounded

Provider inactivity and missing reported usage are not cured by weakening the
usage guard. Unknown consumption remains unknown, and a positive reported-token
cap still stops the run. Active-time and iteration caps likewise remain intact.
Live runs and deterministic fixture results are recorded separately under the
ignored `.scenario-runs/remaining-fix-validation/` tree.

## Validation before the review revisions

The full suite passed 2,157 tests in 149 modules; the changed-file gate passed
1,102 tests in 72 modules, with the final saved-approval boundary also covered by
the focused tests and full suite. All four planning CLI regressions and 68
harness/catalog tests passed. The fake catalog recorded 49 passes, one existing
scenario not exercised and one existing scenario requiring a live Investigator
skipped. Each new seed and unfixed variant is rejected; each reference passes.

The targeted live reruns used only the Codex-only OpenCode routes, with the same
30-minute active-time, 10-minute stage, six-iteration and two-million reported-token
limits. Source hashes stayed unchanged throughout both live snapshots.

| Scenario | Live outcome | Oracle | Clarification answers |
| --- | --- | --- | --- |
| Sound design review | PASS | 7/7 | 0 |
| Cache numeric deadline repair | PASS | 10/10 | 0 |
| Outbox query-limit repair | PASS | 10/10 | 0 |
| Archive corruption/cleanup repair | PASS | 10/10 | 0 |
| Timesheet by-project feature | PASS | 6/6 | 1 |

The timesheet's one answer authorized correction of the model's incorrectly
calculated draft whitespace example. The harness supplied the proposed default;
this remains an unnecessary clarification, not evidence of flawless planning.
These targeted passes do not stand in for a fresh live run of the original full
48-scenario campaign. Original campaign files remain unchanged.

## Review revisions

Claude reviewed the three implementation concerns in PR #192 before these revisions.
The changes address the 13 original review comments: investigation continuity and
trigger classification, active guidance precedence, honest lesson labeling and
configured limits; proof provenance and human-review protection; prompt/guard
consistency and general numeric/staging guidance; ID-only review reports and strict
legacy repair; and hidden cache checks for exact overflow expiry and float rounding.
An infinite-deadline mutant now demonstrates that the strengthened cache oracle
rejects the shortcut raised in review. The prior live results above describe the
pre-review head only; no new live-model campaign has been run for these revisions.

The saved pre-review live cache deliverable was independently rechecked with the
strengthened oracle: all five deliverable checks passed, including all ten hidden
tests. This is a re-score of existing code, not a new live run of the revised runner.

Post-review offline validation passed: 2,166 full-suite tests in 149 modules;
1,132 changed-file tests in 75 modules; five planning CLI regressions; thirteen
CLI evidence regressions; and 68 harness/catalog tests. The fake catalog records
49 PASS, one existing NOT_EXERCISED and one existing live-Investigator SKIPPED.
The original human-review-removal bypass was reproduced against `c99e955` and
rejected by the revised guard. No live model calls were made for this revision.

## Follow-up review corrections

The next review raised 12 comments. Eleven have implementation changes: persist
the canonical hydrated report for deferred blockers and other disk readers; restore
exact-copy instructions for full-text reports; allow adding human review to an
unapproved planner draft; keep the actual nested checkpoint schema outside the
ID-only path; preserve pre-upgrade repair schema bytes; strengthen the negative
proof-downgrade CLI test; reuse marker parsing; remove unused imports and the unused
hydration marker; and make the ID-only fault injection safe on repeated repair.
Canonical persistence also addresses the review's simplification request.

The deferred-blocker CLI regression fails against `90210554` because the Resolver
is never reached; it passes with the canonical-output fix. Reloading the canonical
report is idempotent and keeps the original response artifact.

The remaining guidance-policy comment conflicts with Claude's earlier explicit
recommendation to retain run-wide report corrections. The
[clarification request](https://github.com/charlieanna/autocode/pull/192#discussion_r4148743932)
asks how guidance retirement should interact with spent investigation identities
and a later investigation replacing the active correction. The existing reviewed
policy remains unchanged pending that answer. These are implementation statuses,
not reviewer verification; review threads remain open.

Follow-up validation: 1,091 changed-file tests in 70 modules (against `90210554`),
19 planning/evidence CLI tests and architecture checks passed. The fake catalog
again records 49 PASS, one existing NOT_EXERCISED and one live-Investigator SKIPPED.
The full suite ran 2,169 tests in 149 modules with one failure in the existing
`test_grader_kills_descendants_on_timeout_and_success` success case: immediately
after termination, macOS `ps` returned `?E` instead of an absent or zombie process.
All 39 diagnosis-module tests passed on an isolated rerun. This suggests a
process-observation race but is not a clean full-suite pass; the assertion remains
unchanged and the failure is disclosed for review. No live model calls were made.
