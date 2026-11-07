# Implementation issue drafts (#1896)

Drafts for the second owner checkpoint on #1867, one per pull request, in dependency
order. None is filed or authorized yet. Each says what closes it. Slice names match
[qualification](qualification.md); S0 shipped in #1906.

Every draft inherits these rules: claim the issue first (AGENTS.md "Taking an issue");
run the focused tests and `tools/run_suite.py --changed` before opening the pull request;
add fields to the status view, never rename or remove one; a new run-state key is named in
`autocode_run_state` with its writer and readers; no paid run unless the draft says so.

## Visibility, independent of the policy decisions

### S1a. Price each stage at the rate in force when it finished

- **Depends on:** nothing.
- **Problem:** `autocode_cost.view`, `exceeded` and `crossing` re-price every stage from
  the current `autocode_prices.json`, so a tariff update rewrites past runs' `spent_usd`
  and can flip whether a resumed run is over its cap ([accounting](accounting.md), E5).
- **Change:** when a stage finishes, store `price_basis: {as_of, model, rates, source}`
  on its record from the dated table; price finished stages from it; keep "at today's
  rates" only as a labelled recomputation.
- **Interface:** `cost.breakdown.by_stage[].price_basis` (added).
- **Tests:** a run priced, then the table changes: `spent_usd` unchanged and the cap
  decision unchanged; a stage saved before this change prices from the current table
  and says `price_basis: null`.
- **Rollout:** additive; revert drops the field and falls back to today's pricing.
- **Closed by:** those tests, plus `tests/test_cost*.py` green.

### S1b. Mark subscription billing on each stage

- **Depends on:** nothing.
- **Problem:** a coding-plan route's API-equivalent figure is indistinguishable from a
  metered estimate (E2). The selector knows the route's billing kind and discards it.
- **Change:** carry the selected route's billing kind onto each stage record.
- **Interface:** `cost.breakdown.by_stage[].billing`: `subscription`, `metered` or `null`.
- **Tests:** a GLM coding-plan stage with zero reported cost shows `subscription` and an
  estimated figure; an OpenAI metered stage shows `metered`; a pinned custom route `null`.
- **Rollout:** additive.
- **Closed by:** those tests.

### S1c. Cost per accepted task on estimated dollars

- **Depends on:** S1a.
- **Problem:** `efficiency.unit_metrics.reported_usd` divides provider-reported dollars
  only, so every coding-plan run shows `null` ([baseline](baseline.md)).
- **Change:** add `estimated_usd` beside it, from `cost` when `cost.complete` is true,
  else `null` with the reason.
- **Interface:** `efficiency.unit_metrics.estimated_usd` (added).
- **Tests:** complete GLM run: a value; one unpriced stage: `null` with reason; zero
  accepted deliveries: `null` (H10).
- **Closed by:** those tests and `tests/test_efficiency.py` green.

### S1d. Record time spent waiting for the person

- **Depends on:** nothing.
- **Problem:** the `waiting` category has no emitter, so human waiting is never
  measured and `interaction_timing` durations mix it with model time.
- **Change:** when a run stops for a person (`pause_category` `your_decision`) and later
  resumes on their action, record one `waiting` observation for that interval.
- **Interface:** `efficiency.time.exclusive_wall_seconds_by_category.waiting` gains data.
- **Tests:** fake clock: stop at plan approval for 600 s, approve: 600 s waiting, not
  counted as execution; a crash with no person's action records nothing.
- **Closed by:** those tests.

### S1e. Say when a resumed run launches with different prompts

- **Depends on:** the checkpoint answer to [contract](contract.md) question 3.
- **Problem:** each stage record stores its launch prompt set, but nothing compares it
  with the run's original set.
- **Change:** the status view names the stages whose prompt set differs from
  `prompts_hash`.
- **Interface:** `prompt_sets: {original, differing_stages}` (added).
- **Tests:** a run whose second stage launched under a different hash lists it.
- **Closed by:** that test.

### S1f. Protect the status view's always-present fields in the schema

- **Depends on:** nothing.
- **Problem:** #1906 added eight fields as optional properties; a field outside the
  schema's `required` list can still be removed without `tests/test_json_contract.py`
  failing.
- **Change:** move the fields `run_view.view()` always returns into `required`.
- **Tests:** the contract test fails if any of them is removed.
- **Closed by:** `tests/test_json_contract.py` green with the stricter list.

## Policy, after the first checkpoint

### S2. Record the stage decision, in shadow

- **Depends on:** the first checkpoint's answers.
- **Change:** a pure module `tools/autocode_stage_policy.py` computes the class and the
  decisions from saved state and the run's `settings.stage_policy`, and records them;
  nothing acts on them. The project-file keys from [contract](contract.md) land here.
- **Interface:** `stage_policy` in the status view; `stage-policy` in
  `autocode_project_config.FILE_ONLY`.
- **Tests:** pure classification tests for every row of the [stage policy](stage-policy.md)
  table and B1 to B8; a run with no project table shows `stage_policy: null` (G1, H9).
- **Rollout:** shadow only; turning it off loses only the record.
- **Closed by:** shadow records on the representative fake runs match the table.

### S3. Sensitivity input, in shadow

- **Depends on:** S2.
- **Change:** `sensitive-paths` project key plus a shipped default list; the runner checks
  the plan's `affected_paths` and each Builder attempt's changed files; a Requirements
  hint can only raise the class.
- **Tests:** H2 with the new catalog entry from S7; B1, B3, B7, B8.
- **Closed by:** H2 passes in shadow.

### S4. Planning reuse for follow-ups

- **Depends on:** S2, the first checkpoint's answer on follow-ups.
- **Change:** key acceptance-test coverage per criterion digest instead of the whole
  contract hash; carry settled answers whose source sentences are unchanged.
- **Interface:** `efficiency.observations` kinds `invalidated` and `unknown` with the
  binding that changed ([evidence reuse](evidence-reuse.md)).
- **Tests:** H4, H5; D4 and D6 rows; a changed criterion invalidates only its tests.
- **Closed by:** H4 and H5 pass.

### S5. The first shortcut

- **Depends on:** S2, S3, the first checkpoint's choice.
- **Recommended scope:** the combined validation-and-decision pass for a single
  non-sensitive task, reusing the existing `astra_checkpoint` form. It is already gated
  by the same runner checks, and #1913 closed its `source_verdict` gap. Still needed: a
  blocking finding raised in the combined pass is resolved only with a replayed passing
  check that cites its reproduction ([verification](verification.md)).
- **Alternative:** P1, the small-correction bug path with the person approving the
  runner-built contract ([stage policy](stage-policy.md)).
- **Tests:** H1, H3, C1 to C9; the stage list asserted on `bugfix-trivial` and
  `feature-timesheet-by-project`.
- **Rollout:** behind the project policy only; `--full-pipeline` turns it off per run.
- **Closed by:** deterministic tests pass, then the S8 comparison meets its criteria.

### S6. Low start rung for producer roles

- **Depends on:** S2, the second checkpoint's answer on start rungs.
- **Change:** Requirements, the Planner draft and report-format repair start at low
  effort and climb on the existing triggers; verifier rungs untouched
  ([routes and recovery](routes-and-recovery.md)).
- **Tests:** H8; a person's explicit effort is never lowered; a climb records its trigger.
- **Closed by:** deterministic tests, then its own S8 arm.

### S7. Catalog additions

- **Depends on:** nothing for the entries; S3 for the sensitive entry's routing assertion.
- **Change:** a one-line rounding or permission change in a module the default sensitive
  list covers, with a broken variant that passes the existing tests; a follow-up delta
  entry; a missing-price fixture; a stale policy token case.
- **Closed by:** `scenarios/run.py check` passes and no broken variant is judged PASS.

### S8. The live comparison

- **Depends on:** S5 (and S6 for its own arm), plus the authorization below.
- **Closed by:** a recorded result either way, with the evidence format in
  [qualification](qualification.md).

## Live comparison authorization (to fill in at the second checkpoint)

```text
Authorized by:            <owner>, <date>
Arms:                     today's path | S5 path   (routes unchanged; S6 is a separate comparison)
Tasks:                    bugfix-trivial, bugfix-iso-weeks, feature-timesheet-by-project,
                          review-then-fix, <S7 sensitive entry>
Profile and routes:       <profile from scenarios/harness/profiles.py, with each role's model and effort>
Repetitions per arm:      2 (minimum)
Spending limit:           $<per attempt> per attempt, $<total> for the sweep
Wall limit:               --timeout-minutes <n> per scenario
Stop early if:            any FALSE_COMPLETE, or the sweep limit is reached
Success criteria:         as in qualification.md "Success and rollback criteria"
```
