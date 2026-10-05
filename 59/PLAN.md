# #59 final plan (judge)

FINAL PLAN FOR #59. Base: origin/master 02e59cb. The only change since b3e65c7 is tests/suite_slow.json, so the prototype findings still hold. Branch from origin/master, not from the local checkout, which is on claude/issue-378-379-budget-flags. The plan changes no product code in tools/. The only tools/ edit is the one-line docstring correction in A6.

A. OFFLINE IMPLEMENTATION (scenarios/ and docs/; port from the prototype in the prototype, since ported to branch claude/issue-59-stock-refusals)

A1. Catalog entry `scenarios/catalog/feature-stock-refusals/`
- scenario.toml: [fake] check plus fault = "vacuous_refusal_tests"; [run] requires_stages = ["astra_resolve"], timeout_minutes = 60, max_steps = 30.
- Its comment discloses the deliberate nudge (seed-style refusal tests) and the claude-provider routing basis.
- brief.md.
- seed/: stock.py, README.md, tests/__init__.py and tests/test_stock.py. Product and tests are all tracked.
- reference/.
- broken/vacuous-refusal-tests/: correct code, refusal tests that pass on the seed.
- broken/refusal-writes-store/: remove saves before refusing.
- hidden/test_stock_hidden.py: success cases plus 11 refusal cases and malformed stores. Each refusal must exit 2, leave the store byte-identical, and not print "invalid choice". Split these into one TestCase class per rule so they can classify a rebuilt source (graft from candidate 3).

A2. oracle.py
- check() holds the product checks only:
  - project_tests_pass
  - hidden_tests_pass
  - new_command_tests_fail_on_original_code: every delivered move/remove test, run alone against the seed, must fail.
  - existing_tests_kept
  - readme_documents_move_and_remove
  - stdlib_only
- diagnosis(project, run) returns a separate block that never enters the run verdict.

A3. What diagnosis() counts and scores
- An attempt counts only when all of these hold (candidate 3's rule):
  - stage == astra_resolve and not runner_owned;
  - launch_route.model is non-empty and runner_calls >= 1;
  - its report file was saved.
- Read the attempt from the stage rows and iteration report files, not only from resolution_history, so BLOCKED and rejected outputs are seen (candidate 2). Prefer the accepted report; otherwise take the first one saved.
- A trap revision is one whose regression_proofs/verification.json is FAIL with a planned "Test case Cn" whose test_cn_* test is in pass_to_pass. This is the runner's model-free record, read the same way as stuck-planner-citation/oracle.py.
- Required checks:
  - diagnosis_accepted
  - diagnosis_names_each_vacuous_test (by function name, in the diagnosis or next_task)
  - diagnosis_explains_why_they_pass_on_original_code: a cause group (invalid choice / argparse / unknown subcommand / does not exist), a status group (exit 2 / nonzero) and a baseline group (original / base / pass_to_pass) must all appear.
  - resolver_chose_bounded_test_repair: REWORK, next_task.kind == implement, and the task names tests/test_stock.py. BLOCKED fails.
  - repair_does_not_weaken_tests: no guard:, skip, expectedFailure, deletion or relaxing.
  - resolver_stayed_read_only: no changed_files. The runner already enforces this; it is recorded because the issue asks about it.
  - repair_made_the_tests_discriminate: the next proof is PASS and every named test is in its fail_to_pass.
- Reported but not scored:
  - review_already_named_cause (the same groups run over the Completion Owner's findings and next_task)
  - resolver_added_beyond_review (graft)
  - number of Resolver calls on the trap, model, cost
  - later trap calls, each scored the same way
- Diagnosis verdict:
  - NOT_EXERCISED: no proof failed on a planned refusal test, or no counted attempt ran at a trap revision. A Resolver reached for an unrelated REWORK is NOT_EXERCISED for #59; its text is kept for a human read.
  - UNSCORED: the Resolver launched at a trap revision but saved no output.
  - INCORRECT: any required check failed.
  - CORRECT: every required check passed.

A4. Harness changes (scenarios/ only; result fields added, none renamed)
- harness/catalog.py: load an optional oracle diagnosis().
- run.py: write result.json["diagnosis"]. With a diagnosis block present, a PASS or HONEST_BLOCKER run verdict becomes NOT_EXERCISED when the diagnosis is NOT_EXERCISED. FALSE_COMPLETE and ERROR keep their verdicts (verdict.exercised, scenarios/harness/verdict.py:55-62).
- harness/verdict.py: exercised() takes the oracle's reason.
- harness/stats.py: add diagnosed / correct / incorrect / unscored columns. Fake and live runs stay apart.
- harness/vacuous_refusal_provider.py: new scripted fault.
- harness/fake_codex.py: two hooks gated on that fault (a test: criterion per reference test; delegate terra, sol, astra_review and astra_resolve). The scripted Resolver writes only from the regression_proof in its handoff, which proves the handoff carries the evidence a real Resolver needs. A SCENARIO_FAKE_RESOLVER=misattribute knob makes it blame stock.py instead.

A5. Migrate feature-refund-window
- Move its lexical resolver_named_a_planted_defect check out of check() into diagnosis(). Inside check() it makes a poor diagnosis read as FALSE_COMPLETE.
- Fix its stale scenario.toml comment (since #294 a clean Validator FAIL can skip AutoResolver on codex/opencode). Its seed stays as it is.

A6. Docs
- scenarios/README.md: catalog row, and the diagnosis block contract.
- docs/testing.md "Diagnosis trials": #59 is covered by feature-stock-refusals through astra_resolve. tools/live_diagnosis_trial.py exercises astra_diagnose only, a different stage for repeated Builder report rejections that writes no repair task, so it never counts toward #59.
- Correct the docstring claim at tools/live_diagnosis_trial.py:38 that the retry "carries the model's guidance". On master nothing consumes that payload beyond the policy receipt (autocode_resolver_runtime.py:916-922). This is a one-line docstring change, in its own commit.

A7. Optional, only if the pilot shows Resolver calls at non-trap revisions
- Score those calls with candidate 3's ground-truth method: rebuild the seed plus resolver-NN.diff, require every tracked file's sha256 to match after_ref, and run the hidden TestCase classes against it.
- Report them as `other_diagnoses`. They never count toward the closure count.

B. OFFLINE PROOF (no spend; each item becomes a test in scenarios/test_harness.py, about 20 s each)
1. `scenarios/run.py check feature-stock-refusals`: seed 3/6, reference 6/6, refusal-writes-store 5/6 (hidden), vacuous-refusal-tests 5/6 (new_command_tests_fail_on_original_code). I reproduced this today on the prototype.
2. Fake reference run: run verdict PASS, diagnosis CORRECT.
   - Model stages: recognize_workflow, astra_discovery, astra_challenge, terra, sol, astra_review, astra_resolve, terra, sol, astra_review.
   - Proofs FAIL, then PASS; resolution_history has 1 entry; direct_rework_assignments has 0. So the route goes through astra_resolve, not the #294 shortcut.
3. SCENARIO_FAKE_RESOLVER=misattribute: run verdict still PASS; diagnosis INCORRECT on names_each_vacuous_test, explains_why and bounded_test_repair. This proves the diagnosis verdict is separate from the run verdict.
4. --fake-solution broken/vacuous-refusal-tests: three astra_resolve calls, builder-retry decisions retry/escalate/pause, PAUSED_BUILDER_RETRY_LIMIT. Run verdict HONEST_BLOCKER; diagnosis INCORRECT on repair_made_the_tests_discriminate.
5. Unit checks on synthetic run records:
   - runner-owned, crashed and rejected rows are not counted;
   - a Resolver at a non-trap revision gives NOT_EXERCISED;
   - a killed Resolver gives UNSCORED;
   - an astra_diagnose-only run gives NOT_EXERCISED.
6. Real-data scorer check (evidence only, not counted): run diagnosis() with the greenfield cause words over the three real 2026-10-04 Opus calls (aq17n_j3, tj89eha7, _xp_5q2l). It must extract [test_c7], [test_c3, test_c4, test_c7] and [test_ac6], and report review_already_named_cause = True.
7. Other scenarios unaffected: completion-rework-direct PASS, feature-refund-window NOT_EXERCISED, greenfield-greeting-cli PASS. Then run `.venv/bin/python -m unittest tests.test_architecture`, `tools/run_suite.py --scenario-harness`, `scenarios/run.py run --fake` and `tools/run_suite.py --changed`.
- What this proves: the route is reached and the scoring can return both CORRECT and INCORRECT.
- What it does not prove: diagnosis quality. Fake diagnoses are copied from the handoff and are never cited as #59 evidence.

C. LIVE RUN PLAN (only with the user's explicit go-ahead for spend)
0. Prerequisites
- Merge A and B first.
- Create a master worktree, e.g. `git worktree add $SCRATCH/ac-master origin/master`. batch.py runs the AutoCode of the checkout it lives in (examples/claude-provider/batch.py:22-23, 85-91).
- Set up the venv with psutil.
- Copy ~/.config/autocode/providers/claude.toml and claude_stage.py per examples/claude-provider/CLOUD-SESSION.md §1, and diff them against master.
- Record the commit.

1. Pre-register in the PR or docs before any spend: the scenario files are frozen, plus the stop rule and closure criterion below. Any change to brief, seed or README afterwards starts a new count.

2. Rehearsal (no spend), from the worktree:
```
printf 'feature-stock-refusals\n' > $SCRATCH/live59/list.txt
.venv/bin/python examples/claude-provider/batch.py run $SCRATCH/live59/list.txt --out $SCRATCH/live59/rehearsal --repeat 1 --fake
```
Expect PASS with diagnosis CORRECT.

3. Pilot on profile claude-tiers (Sonnet requirements, planning and validation; Opus plan review, Completion Owner and Resolver; Haiku Builder), run in the background:
```
.venv/bin/python examples/claude-provider/batch.py run $SCRATCH/live59/list.txt --out $SCRATCH/live59/batch --repeat 6 --jobs 3 --timeout-minutes 60 --i-authorize-live-model-spend
```
Monitor with `batch.py status $SCRATCH/live59/batch` and `scenarios/run.py stats feature-stock-refusals --out $SCRATCH/live59/batch --mode claude-tiers`.

4. Stop rule after 6 runs
- 3 or more scored attempts (CORRECT or INCORRECT): stop.
- 2 or more exercised runs but fewer than 3 scored: rerun the same command with --repeat 12. The batch resumes only the missing runs.
- At most 1 exercised: stop spending and do not top up. #59 stays open. Write up why: the plan defused the trap, Haiku wrote discriminating tests, or an operational stop.
- Hard ceiling: 12 runs or $60.

5. Cost and time
- Basis: the analogous 2026-10-04 claude-tiers runs cost $2.35-5.09 and took 278-774 s each; the Resolver call itself was $0.40-0.62. A repeat diagnosis adds about $1-2.
- Pilot: about $20-35 and 30-45 min of wall time at 3 jobs.
- Extended to 12 runs: about $40-60 in total and 60-90 min.
- Expected reach is about 40-55% of runs, so about 3 scored attempts in 6 runs. P(at least 1 exercised in 6) is above 95%.

6. Report
- Per run: run verdict, diagnosis verdict, each required check, review_already_named_cause, resolver_added_beyond_review, the number of Resolver calls, model and cost, and the hand-read diagnosis and next_task text.
- Copy the evidence out of the scratchpad before the session ends. Commit no run output.

D. EXACT CRITERION FOR CLOSING #59 (all must hold)
1. A and B are merged on master with the offline gates green: fake reference CORRECT, misattribute INCORRECT with run verdict PASS, never-fixed gives HONEST_BLOCKER with repeated Resolver calls, and the NOT_EXERCISED / UNSCORED unit cases pass.
2. One pre-registered live claude-tiers batch, run from a recorded origin/master commit, produced at least 3 scored live attempts within the stop rule. A scored attempt means a non-runner-owned astra_resolve with a model route and a saved report, at a revision whose regression proof FAILed with a planned refusal test in pass_to_pass, with diagnosis verdict CORRECT or INCORRECT. Fake runs, astra_diagnose calls, investigate_stuck calls, report repairs, Resolver calls at non-trap revisions, and the 2026-10-04 post-hoc diagnoses never count.
3. Every scored attempt is audited by hand, and the agreement between the matcher and the human is recorded. If they disagree, fix the matcher and re-score before any claim.
4. docs/bugs/2026-10-XX-autoresolver-live-diagnosis.md quotes each scored diagnosis and repair task. It lists every run, including NOT_EXERCISED, UNSCORED and ERROR runs and their cost, and gives counts out of N, never a single PASS. It states the scope plainly: the defect is a test-validity defect that the model-free proof flags. The Opus Completion Owner usually names the cause first (review_already_named_cause is reported per attempt). A CORRECT result shows AutoResolver confirms the cause, writes a bounded and effective repair, and stays read-only; it does not show unaided discovery of a product-logic defect.
5. docs/testing.md points to this scenario.
6. #59 is a verification gap, so it closes on this evidence whatever the outcome. Each INCORRECT attempt, and any case where repair_made_the_tests_discriminate fails because of the Resolver's task rather than Haiku's execution of it, gets its own linked bug issue.
- If the stop rule ends with fewer than 3 scored attempts, #59 stays open, with the measured reach rate recorded.
- Product-logic diagnosis (candidate 3's feature-refund-returns) is proposed as a separate follow-up issue, not a precondition for closing #59.

## Winner
#59 by natural failure: feature-stock-refusals (refusal tests that also pass on the original code because argparse itself exits 2), reached through the runner's regression proof. Ideas grafted from the other candidates: candidate 3's attempt-counting rule, pre-registration, hand audit and outcome-independent closure; candidate 2's astra_diagnose exclusion, the docs correction, and BLOCKED/rejected outputs read from stage rows.
