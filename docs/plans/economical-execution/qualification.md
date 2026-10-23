# H. Qualification, rollout and implementation slices (#1896)

Planning input for the second owner checkpoint on #1867, together with
[the contract](contract.md). Read-only audit of master `08ddeb2a3` (2026-10-22). This record
defines experiments and slices. It authorizes no paid run and claims no improvement.

## Reusable assets

- **Scenario catalog** (`scenarios/catalog/`, 87 entries). Each entry has a brief, a
  seed, an oracle, a reference solution and at least one broken variant; no broken
  variant may be judged PASS (`scenarios/test_lying_matrix.py`). Verdicts are PASS,
  FALSE_COMPLETE, HONEST_BLOCKER, ERROR, SKIPPED and NOT_EXERCISED.
- **Comparison commands** (`scenarios/run.py`): `plan-compare` runs fixed against
  adaptive planning to plan approval with blind plan files; `build-compare` runs to
  completion judged by the oracles, reports cost per pass including failed attempts,
  and freezes `protocol.json` before running.
- **Profiles** (`scenarios/harness/profiles.py`) pin roles to provider, model and
  effort; `claude-tiers`, `glm53-openai` and `build-comparison` are the ones prior live
  rows used.
- **Fault layers:** `test-scenarios/` (crash and resume, budgets, dirty workspace,
  lying builder, vacuous tests and more against a fake Codex);
  `scenarios/harness/adversarial.py` (scripted-provider faults at the CLI boundary);
  `scenarios/harness/conformance.py` (a client that takes only named actions).
- **Ledger:** `tools/reliability_ledger.py` reports verified delivery, false
  completion, decisions and repairs, cost including failures and time, and never pools
  live with fixture rows. `arena/campaign.json` declares 13 tasks but has never run, and
  10 of its 11 pinned input hashes no longer match master.
- **Regressions to keep:** #1868 (`tests/test_acceptance_tests.py`: a plan-added case
  that already works goes back as a guard; a passing reproduction still pauses) and
  #1869 (`tests/test_acceptance_tests.py`, `tests/test_chat_view.py`,
  `tests/test_next_command.py`: the feedback action at the Tester's pause).

## Representative set

From [baseline](baseline.md).

| Class | Entry | Status |
| --- | --- | --- |
| Clear localized bug | `bugfix-trivial` | Exists |
| Unclear bug | `bugfix-iso-weeks` | Exists |
| Small feature | `feature-timesheet-by-project` | Exists |
| Sensitive small diff | **New:** a one-line rounding or permission change in a module the sensitive-path default covers, with a broken variant that passes the existing tests | To write |
| Same-chat follow-up | `review-then-fix` | Exists |

## Current-master deterministic row

Recorded 2026-10-23 on master `518254b7e` with `scenarios/run.py run ... --fake` on
macOS (portable supervision). The fake provider writes valid reports and its first plan
review raises no blocking concern, so these are best-case stage sequences: live runs
add revision rounds and report repairs (the Plan Reviewer blocked the first draft in 21
of 22 live runs, `docs/adaptive-planning.md`). The harness does not pass `--spec-first`, so no acceptance-test
stage runs; the clients' `full` mode adds one, plus the Requirements review stop.

| Entry | Verdict | Model stages | Runner stages | Sequence | Person's decisions | Bundle manifest |
| --- | --- | ---: | ---: | --- | --- | --- |
| `bugfix-trivial` | PASS, oracle 14/14 | 7 | 2 | Requirements, Investigator, draft, challenge, orchestrator, Builder, regression proof, Tester, Completion Reviewer | 1 plan approval | `6f7c3532…` |
| `bugfix-iso-weeks` | PASS, oracle 8/8 | 7 | 2 | Same as above | 1 plan approval | `44d01c78…` |
| `feature-timesheet-by-project` | PASS, oracle 9/9 | 6 | 1 | Requirements, draft, challenge, orchestrator, Builder, Tester, Completion Reviewer | 1 plan approval | `24957149…` |
| `review-then-fix` | PASS, oracle 21/21 | 8 | 1 | Requirements, Code Reviewer, Requirements, draft, challenge, orchestrator, Builder, Tester, Completion Reviewer | 1 plan approval | `78c18f50…` |

No report repair ran in any of the four. Against this row, the proposed P1 path would run
4 model stages on `bugfix-trivial` (Requirements, Investigator, Builder, one combined
verification) and the combined pass alone would take `feature-timesheet-by-project` from
6 to 5. Wall times (26 to 41 s) are harness overhead, not model latency. The archives are
under the main checkout's ignored `.scenario-runs/economical-baseline-20261023/`.

## Qualification matrix

Deterministic layers first: pure tests, then CLI-level tests with fake providers, then
catalog scenarios run `--fake`. Provider-launching fakes need the supported Linux
boundary (AGENTS.md "Testing").

| ID | Behavior to prove | Deterministic check | Negative control that must fail |
| --- | --- | --- | --- |
| H1 | An eligible small fix finishes with fewer calls and valid red-then-green proof | `bugfix-trivial --fake` under the short path: stage list asserted, regression proof complete | Its broken variant is never judged PASS; a regression that passes on the base is refused |
| H2 | A one-file sensitive change is not routed short by size | The new sensitive entry: `stage_policy.class` is the sensitive class; separate Tester runs | The same diff with the sensitive pattern removed from policy takes the short path (proves the input decides) |
| H3 | Builder's tests pass while the external oracle fails | `hybrid-lazy-validator` and the lying-builder fault through the combined pass | A combined pass that accepts it is a FALSE_COMPLETE |
| H4 | Unchanged evidence is reused; a changed binding invalidates it | Pure tests per row of [evidence reuse](evidence-reuse.md); `verification-reuse` | A lockfile, requirement or fixture change that still reuses evidence |
| H5 | Same-chat baseline tests pass and the new bug is reproduced | `review-then-fix --fake`; #1868 and #1869 tests | An earlier test silently dropped instead of kept as a guard |
| H6 | Transport or report retry keeps edits, counts cost, never completes falsely | Adversarial recovery tests; `capacity-recovery`; `provider-idle-hang` | A repaired report that cites a check the original attempt did not run |
| H7 | Unpriced, in-flight and parallel calls keep honest accounting | Pure `cost`/`usage` tests from [accounting](accounting.md) E1 to E7 | A total shown as complete with an unpriced stage |
| H8 | Escalation follows approved ladders and keeps independence | Ladder and `PAUSED_CROSS_MODEL` tests | A climb onto the Builder's family for a checker |
| H9 | Stale policy or approval tokens and old saved runs follow [the contract](contract.md) | CLI tests: old run resumes with `stage_policy: null`; stale token refused | A saved run without a policy that takes a shortcut |
| H10 | Zero accepted completions or incomplete cost blocks a cost-per-success figure | Pure ledger and `efficiency` tests | A cost per accepted task computed from a partial numerator |

## Implementation slices

Each slice below is drafted as an issue, with its tests, compatibility and closing
evidence, in [implementation issue drafts](implementation-issues.md). S1 there is split
into one pull request per field.

In dependency order. Each slice is one pull request unless noted, and each names what
closes it.

| Slice | Change | Public interface | Regressions and negative controls | Compatibility | Rollout and rollback | Closed by |
| --- | --- | --- | --- | --- | --- | --- |
| S0 Truth fixes | Add the missing view fields to the status schema; fix `docs/models.md` on the automatic switch, the `--adaptive-planning` help, `docs/cost-reporting.md` drift | Schema, docs | `tests/test_json_contract.py` | Additive | Docs and schema only | Delivered in #1906 |
| S1 Accounting gaps | Price basis and billing per stage; estimated-dollar unit metric; human-waiting emitter | `cost.breakdown`, `efficiency.unit_metrics` | H7, H10 | Old stages show `price_basis: null` | Additive; reverting drops fields only | H7 and H10 pass |
| S2 Decision record, shadow | `autocode_stage_policy` computes the class and decisions and records them; nothing acts on them | `stage_policy` | H9; pure classification tests including B1 to B8 | `null` for old runs | Shadow only: records what it would do | Shadow records on the fake catalog match expectations |
| S3 Sensitivity input | Project sensitive-path setting with a shipped default; diff check at Builder exit | Project config; `stage_policy.escalations` | H2 | No setting means the default list | Shadow first, with S2 | H2 passes in shadow |
| S4 Planning reuse for follow-ups | Per-criterion acceptance-test coverage; carry settled answers | `efficiency.observations` | H4, H5 | Old runs re-produce as today | Behind the policy | H4 and H5 pass |
| S5 First shortcut | The one the first checkpoint picks: the combined verification pass, or P1 with the person approving | `stage_policy.decisions` acting | H1, H3, C1 to C9 from [verification](verification.md) | Only runs that recorded the policy | Opt-in by `--stage-policy`, then default only after S8 | H1 and H3 pass; S8 comparison |
| S6 Route and recovery policy | Low start rung for producer roles; low-effort report repair | `routes`, `escalations` | H8; F-scenarios | Explicit efforts untouched | Opt-in per role | Comparison in S8 |
| S7 Catalog additions | The sensitive small-diff entry; a follow-up delta entry; a missing-price fixture; a stale-policy-token case | Scenarios | Their broken variants | — | Test-only | Entries pass `scenarios/run.py check` |
| S8 Live comparison | The approved matched comparison below | Evidence notes | — | — | Explicit owner authorization | Recorded result, either way |

S0 is delivered in #1906. S1 is independent of the policy decisions and needs no
checkpoint to start. S2 and S3 run in shadow, so they gather classification
evidence on real runs without changing behavior.

## Live comparison: authorization boundary and format

The fill-in authorization is at the end of [implementation issue drafts](implementation-issues.md).

A live comparison is a paid sweep, so AGENTS.md requires an explicit decision; this
record does not grant it. The decision at the second checkpoint states:

- the tasks (the representative set above), the profile and its routes per role;
- repetitions per arm (the campaign declaration uses two; at least two);
- a spending limit per attempt and for the whole sweep, and a wall limit per scenario
  (`scenarios/run.py --timeout-minutes`);
- the arms: today's path against the S5 path, with routes unchanged. A route change
  (S6) is a separate comparison.

**Evidence format.** Ledger rows through `tools/reliability_ledger.py`, one per attempt,
with failed attempts kept; the runtime revision, profile and routes; cost with its
coverage; elapsed time; interventions; the oracle result. A short note in `docs/bugs/`
cites each run by its `autocode bundle` manifest hash. No logs are committed.

## Success and rollback criteria

Proposed for the owner; the numbers need the matched baseline from [baseline](baseline.md).

- **Zero tolerance:** no negative control passes, and no FALSE_COMPLETE on the short
  path.
- **Completion:** the short path's completion rate is not lower than the full path's on
  the same tasks.
- **Cost:** cost per accepted task is lower by more than the spread between the two
  repetitions of the full path, with coverage stated. With two repetitions this is a
  direction, not a statistical claim (the campaign declares
  `statistical_reliability_claim: false`).
- **Rollback:** a short path is turned off by policy without migrating any run; runs
  that used it keep their recorded decisions and evidence.

## Company and Jira adoption criteria

1. Every run shows its stage decisions and reasons in the status view, and a worker
   renders them without reading private state.
2. Cost is complete or labelled with its gaps; the chosen enforcement level states its
   real guarantee ([accounting](accounting.md)).
3. The short path passed S8 under the criteria above.
4. Unknowns stay visible: accounting gaps, unclassified tasks and any policy the owner
   left undecided.
