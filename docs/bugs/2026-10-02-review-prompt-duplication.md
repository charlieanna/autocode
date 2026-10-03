# Repeated definitions and commands in review prompts

The recorded todo scenario `20261001T061511Z-greenfield-todo-cli-glm53-openai-aisqo2ir`
sent all 18 acceptance criteria both at the top level and in the approved
contract. Its Completion Owner also received 11 command strings in both the
Validator's checks and the runner's independent replay results.

`autocode_handoff_dedup` now replaces only exact repeated definitions and command
text with explicit inline JSON pointers. The full approved contract remains
inline. Replay outcomes, reported outcomes, failures, evidence, human approvals,
saved answers and feedback remain intact. Planning and Builder prompts are
unchanged; packets that would grow retain their original representation.

Before implementation, the new regression test failed for all three review
stages. After implementation it verifies smaller packets, exact reconstruction,
preservation of a reported PASS followed by a runner FAIL, human review still
pending, a saved denial, unresolved findings, and unchanged source state. It also
exercises the actual stage prompt builder and its token estimate.

An offline comparison applied the same function to 370 recorded review prompts,
reconstructed every original handoff exactly, and checked that source files did
not change. Both sides used the same JSON formatting to isolate deduplication.
This covers 59 review prompts from 26 scenario runs in the main and restructure
checkouts, plus 311 prompts from the inspected large self-build runs. Archived
`/work/` paths were resolved to local copies using the scenario, run-ID suffix
and iteration path. There were no skipped registered prompts in this sample.

| Sample | Prompts | Reduction in complete prompt bytes |
| --- | ---: | ---: |
| Scenario Validator | 28 | 3.26% |
| Scenario Completion Owner | 31 | 2.73% |
| Self-build Validator | 136 | 4.48% |
| Self-build Completion Owner | 175 | 4.26% |

The 18-criterion example falls from 113,465 to 100,499 bytes (11.43%). The long
Completion Owner example falls from 268,603 to 255,077 bytes (5.04%). Per-prompt
paths, hashes, byte counts and reconstruction results are in the ignored local
`.scenario-runs/prompt-dedup-20261002/comparison.json`.

These are prompt-byte reductions, not measured billing or total-session token
savings. There was no live-model A/B run. Exact duplication is a worthwhile but
limited saving; these measurements do not support a 66–87% reduction. Moving
historical answers or feedback out of context and restricting milestone scope
need separate evaluation because they can hide still-binding requirements.

## Validation

- Final affected-test gate: 22 tests passed, including architecture checks.
- Black-box build module: all 21 tests passed after updating its scripted provider
  to read criteria from the complete contract when the duplicate list is absent.
  Three other scripted providers had the same assumption; a subprocess regression
  now verifies all 18 criteria with both original and compact prompts for each.
- Scenario harness: 72 tests passed. The fake campaign exited successfully with
  52 PASS, one `feature-refund-window` NOT_EXERCISED (its deliverable passed, but
  the Resolver was not needed), and the live-only Investigator skipped.
- Full suite: 2,533 tests in 175 modules ran. Besides the scripted-provider
  compatibility failure above, the installed-package test lacked `autocode_cli`
  in the borrowed interpreter, and one process-cleanup assertion failed under
  concurrency. The package tests passed against an isolated local installation;
  all 39 diagnosis-trial tests passed in isolation without a code change. The
  full suite was not repeated after these targeted reruns.

Sandboxed subprocess checks initially could not enumerate processes; the checks
above used the required process access. Local logs are retained alongside the
comparison JSON. No live-model requests were made.
