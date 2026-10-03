# Workflow gaps from the complete Codex-only scenario campaign

The 51-scenario campaign ran the merged PR #192 tree (`a0312a5f`, identical
source to merge `e998a3a8`) through OpenCode. Fifty scenarios used the
`codex-only` profile; the Investigator scenario retained its intended scripted
stages and one live Codex Investigator. All 561 provider launches passed the
model allowlist. Source and oracle hashes stayed unchanged during the campaign.

Raw outcomes: 27 PASS, 21 HONEST_BLOCKER, 2 FALSE_COMPLETE, 1 NOT_EXERCISED.
These are workflow outcomes, not 24 distinct bugs. Seventeen of the 21 blocked
runs passed every deliverable oracle check. Budget exhaustion was reported
honestly; some other runs correctly caught defects or contradictory criteria.
The bounded stops remain evidence, not successful workflow completion.

## Confirmed gaps and corrections

- **Human review did not reach the user (#195).** The greeting deliverable
  passed 12/12 oracle checks. Only its declared human acceptance was pending;
  the Validator reported BLOCKED/NOT_VERIFIED, and the Completion Owner kept
  asking for validation. Artifact review used to require a COMPLETE decision
  with every criterion already marked verified. The new completion helper
  presents a bound human-review request for COMPLETE or a validation-only
  CONTINUE when all technical guards pass. The original reports remain
  unchanged, and only user receipts satisfy human acceptance. Human-only
  BLOCKED validations now have their commands replayed before review is offered.
- **Testing advice reopened an approved float design.** The locked-design run
  asked nine questions and delivered no modules. The numeric-boundary prompt
  pushed the reviewer toward `10**5000` capacities and exact arbitrary-precision
  results despite the approved float representation and `available -> float`.
  Shared planning/validation policy now derives the tested domain from the
  actual contract. An integer annotation cannot override an explicit float
  design. Unbounded integer requirements still get the large-value checks.
- **One example stood in for a broader parsing rule.** The CSV run claimed
  completion but failed a hidden test for blank lines before the header. Its
  plan and checks exercised blanks only after the header. Planning and
  validation now check the full input classes of each source requirement,
  including ignored records before, between and after meaningful records.
  Reviewers receive the original requirement quotes in the compact checklist.
  This is model guidance plus better context, not a deterministic proof that
  every possible input is covered.

## Other outcomes retained

The design-review score (#197) failed its expected ordering question even
though the report requested changes and identified the ordering violation.
Per-domain ordering was already binding. Whether the implementation choice
needs a user question is disputed; this change leaves that oracle unchanged.

The word-frequency run stopped for a contradictory model-written example;
that was an honest refusal, not a false completion. Current master already
instructs plan reviewers to recompute every worked example. The Go-port
Validator left a compiled `policy` binary in source; the source-drift guard
correctly refused its read-only result. Archive and parallel integration runs
still had failing or incomplete deliverables at their caps. No result from the
original campaign is rewritten or counted as repaired by this PR.

## Verification

A public CLI regression reproduces the former human-review loop, then checks
artifact review, explicit approval and completion with only one Builder and
one Validator launch. Gate tests reject failed, stale, absent and changed
technical evidence, blocking findings, criterion changes and implementation
requests. The fake catalog passed 49 scenarios, with one existing
NOT_EXERCISED result and the intentionally live Investigator scenario skipped.
All 71 harness tests passed. Broader suite results are recorded in the PR.

Three fresh bounded live runs used the same unchanged source and oracle files.
All 39 provider launches used allowed Codex models through OpenCode:

| Scenario | Deliverable oracle | Workflow outcome |
| --- | --- | --- |
| Greeting CLI | 12/12 | PASS / TASK_COMPLETE |
| Locked approved design | 15/15; no clarification questions | HONEST_BLOCKER: repeated mistyped workspace paths hit OpenCode external-directory permissions |
| CSV CLI | 7/7, including leading blank lines | HONEST_BLOCKER: Validator caught a very long age causing a traceback, then the active-time cap stopped repair |

These runs establish the named deliverable checks, not universal workflow
completion. In particular, the CSV Validator found a real generated-code defect
beyond the catalog oracle and correctly refused completion. The public CLI
regression exercises the human-review path deterministically; the fresh greeting
PASS is additional workflow evidence, not a guarantee that a live model will
always declare the same human-review criterion. Ignored evidence is under
`.scenario-runs/live-campaign-fix-verification-20261001T063437Z/`.
