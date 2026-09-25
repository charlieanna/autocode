# Independent AutoReview product audit

Requested scope: the ten scenarios in the attached “After AutoCoder comes
AutoReview” document, plus its milestone-versus-final-product boundary check.
Worktree: `codex/autocode-blackbox-scenarios`. This turn adds test fixtures and
reports only; no production fix, merge, push, or safety-pause override.

## Method

Handwritten contracts and deliberately good/bad source files were installed via
the public AutoPlanner/AutoCode CLIs using a scripted setup provider. The fixture
approved only its synthetic contract. Once the candidate existed, the public
`autoreview` CLI called **real reviewers**, without their expected answers:

- Validator: `gpt-6-luna`, medium reasoning.
- Completion reviewer: `gpt-5.6-sol`, medium reasoning (the saved completion route,
  separate from the legacy `astra` role).
- Report repair used the corresponding live role route.

The reviewer received the actual source, approved contract and ordinary handoff,
not a scripted verdict. Before/after snapshots checked source and contract identity;
the test also checked that AutoReview launched no Builder. These invariants held
for every completed review invocation, including failed/paused cases.

For scenario 10, the test deliberately installed a second candidate through the
setup Builder between reviews. No reviewer made the code change. This is not a
live AutoPlanner, live Builder, or full AutoPilot quality evaluation.

## Results

**7 PASS / 3 FAIL / 1 PARTIAL**, covering all ten requested scenarios plus the
milestone boundary. This classification includes inspection of actual evidence,
not merely the original test harness's exit codes.

| Case | Result | Observed outcome |
| --- | --- | --- |
| 1. Correct notes CLI | PASS | All four criteria verified; accepted with no findings, no extra requirements, and unchanged source. |
| 2. Memory-only notes | PASS | Executed separate add/list processes; retained reproduction of missing file and empty list; blocking persistence/listing finding; rework handoff. |
| 3. Concurrent duplicates despite green tests | PASS | Actual sequential tests passed. Reviewer synchronized concurrent requests and observed 20 different ID/count pairs for the same key. Candidate rejected. |
| 4. Mobile clipped text | PARTIAL | Identified the 150px spacer inside an 80px overflow-hidden panel. Browser URL policy blocked rendered inspection. Source-only evidence is not the requested visual proof. |
| 5. Client/server integration mismatch | PASS | Actual isolated component tests passed; review reproduced HTTP 404 for the incompatible client route and requested rework. |
| 6. Go TLD parity exception | FAIL | Detected the missing 7-day `de` override in source, but Go execution was blocked; report and repair failed exact executed-event validation. No accepted review. |
| 7. Required device unavailable | FAIL | Initial response correctly said BLOCKED/NOT_VERIFIED with no defect finding. Repair changed the contract hash; runner rejected it instead of reaching the intended blocked-review handoff. |
| 8. Old passing evidence | PASS | Historical C17 report did not approve the broken current candidate; reviewer independently checked current notes behavior and rejected it. |
| 9. Two authorization defects | PASS | Separate billing/profile findings, each with its own token-input reproduction, expected/actual result and correction. They were retained separately. |
| 10. Fix one defect, introduce another | FAIL | New invalid-input data loss was found. Old persistence findings were marked resolved while persistence remained NOT_VERIFIED; completion report repair then failed finding-ID ownership validation. |
| 11. Milestone boundary | PASS | C1–C4 passed, future C5 remained NOT_VERIFIED without a blocking finding. Completion reviewer advanced the saved task to M2/orchestrator without launching a Builder or declaring TASK_COMPLETE. |

No bad candidate was observed being declared complete. Nevertheless, a safe
rejection/pause is **not** a passed review scenario when the required deliverable
was an accepted evidence-backed review or a clean verification-blocked handoff.

## Actionable issues

### Report repair does not reliably preserve machine-owned identities

In case 7, the original report carried contract hash
`7325cf210888012fdc9a0bab1b2f6e35f6ad0afab138a34e8ea407d74e83dec3`.
A repaired report changed `dc9a0` to `dc9c0`. The guard correctly paused with
“Role result belongs to another goal revision.” The approved contract itself did
not change. This is a failed report/handoff, not unauthorized approval.

Case 10's terminal error was:
“astra finding id F-215ddd5567 is not one open finding of this reviewer.”
This likewise prevented acceptance rather than silently changing history.

### Review execution and evidence formatting can prevent usable findings

Case 6 could not create Go build temporary files in the read-only environment.
Its initial report contained source-level analysis of expected `de → 7` versus
the unconditional `30`, but no successful runtime parity reproduction. Exact
command/event citations then failed validation repeatedly:
“Check is not supported by an exact executed Sol event.”

Several otherwise successful scenarios needed report repair too. A model noticing
a defect is insufficient if the unit cannot publish a valid, current assessment.

### Resolved findings can outpace verified behavior

In case 10, the second accepted validator report set C1/C2/C3 to NOT_VERIFIED,
but resolved both prior persistence/listing findings from source inspection while
explicitly saying runtime confirmation remained blocked. C4 correctly failed for
the new `p.write_text("[]")` invalid-input regression. The history therefore does
not establish the verified resolution requested by the scenario. The subsequent
completion review did not finish successfully either.

### Rendered UI coverage remains unavailable in this run

Case 4's browser tool explicitly blocked the local URL. No alternative route was
used to bypass that denial. AutoReview disclosed the limitation and did not pass
the UI. A source-level clipping finding is useful, but does not replace the
requested viewport/state screenshot or rendered geometry proof.

## Test-harness corrections and honest counting

- Cases 3 and 5 were repeated with genuine passing sequential/component test
  files, not merely a setup smoke command. Those stronger versions passed.
- Inspection found the first browser assertion too weak (a mention of “browser”
  could satisfy it) and the history assertion too weak (any disposition could
  satisfy it). They were tightened. The table above supersedes those earlier
  coarse green assertions: case 4 is partial and case 10 failed.
- Case 11 initially failed before review because the scripted setup's always-green
  smoke check selected the later milestone. Its fixture was corrected to check
  the first milestone's actual file, then rerun fresh (**passed in 155 seconds**). This is not counted as an
  AutoReview defect.
- The initial serial batch was deliberately stopped at the completed case-5
  assessment boundary; remaining cases ran in separate batches. Exit 143 for that
  batch is not a product failure or evidence that unrun cases passed.
- Existing findings/controller/validation-metadata regressions: **27 passed**.
  These deterministic checks supplement, not replace, the product trials.

## Reproduction and artifacts

Test module: `tools/test_autoreview_products.py`. It uses real reviewer calls by
default (`/opt/homebrew/bin/codex`); `REVIEW_AUDIT_LIVE_CODEX` can select another
installed binary. `BUILD_AUDIT_ARTIFACTS` preserves every candidate, CLI receipt,
model event log, report and `assessment-*.json` summary. Failed states were retained;
none was resumed past its safety pause.

Artifact roots:

- `/private/tmp/autoreview-products-20260924`: cases 1–5 initial runs.
- `/private/tmp/autoreview-products-green-tests-20260924`: stronger cases 3 and 5.
- `/private/tmp/autoreview-products-final-20260924`: cases 6–10 and initial case-11 setup failure.
- `/private/tmp/autoreview-milestone-retest-20260924`: corrected case-11 trial.

These are individual trials, not statistical estimates of model reliability.
