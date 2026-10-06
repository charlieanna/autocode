# AutoResolver's live diagnosis (#59)

Issue #59 said AutoResolver's judgment had never been tested on a real mistake.
Only its plumbing had been tested. This note records every live run of the
measuring scenario on 2026-10-05 that is on record, either in #59's comments or
in pull-request bodies.

**Result:**

- **Closing evidence: batch 2's six hybrid runs.** Batch 1, the five later
  hybrid runs used as PR evidence, and the natural runs are reported only.
- **Natural runs never reached the trap:** 0 of 7 `claude-tiers` runs and 0 of
  1 OpenCode run.
- **Hybrid runs:** a script planted the plan and the vacuous refusal tests, and
  everything after that ran live. In all 12 hybrid runs of #59's two batches:
  - Opus as AutoResolver named the vacuous tests and the argparse cause (hand
    read).
  - It asked for no test weakening. This is the scorer's check in both
    batches, and a hand read in batch 2.
  - On the fixed product (batch 2), it asked for a test-only repair in 6 of 6
    runs.
  - On the scenario's first, defective product (batch 1), it also put
    `stock.py` in scope. That was right: the seed and every solution shared two
    real defects, fixed in #467.
- **The Completion Owner had usually named the cause first:** 11 of 12, by the
  scorer's word match (`review_already_named_cause`), not checked by hand. So
  AutoResolver mostly confirmed a cause that was already known.
- **Scorer results:**
  - Batch 2 scored 2 of 6 CORRECT at run time, and 6 of 6 after the scorer was
    revised on those same answers.
  - Of the five later PR runs, four have run verdict PASS on record; #499
    reports the fifth only as "also passed: 6/6". Three have a diagnosis score
    on record, and all three are CORRECT. None was hand-read.
- **#59 closes on the owner's decision.** The plan's pre-registered criterion
  was not met (see [Differences from the plan](#differences-from-the-plan)).

## What was measured

The scenario is `feature-stock-refusals` (#440). The task adds `move` and
`remove` commands to `stock.py`. On the original code these commands do not
exist, so argparse exits 2 and never touches `stock.json`. A refusal test that
checks only exit 2, some stderr and an unchanged store therefore passes on the
original code too. The runner's model-free regression proof rejects such tests,
and AutoResolver must explain why. The oracle's `diagnosis()` scores the
Resolver call apart from the run verdict
([checks](../../scenarios/README.md#diagnosis)).

The scope is narrow:

- **A test-validity defect.** The model-free proof already flags it and lists
  each vacuous test under `pass_to_pass`. In hybrid runs the scripted plan also
  names each case's test, so naming the vacuous tests takes only a lookup. The
  scripted Resolver reads nothing but the proof, and it still scores CORRECT.
- **The cause was usually named first.** The Completion Owner named the cause
  before AutoResolver ran in 6 of 6 batch-2 runs and in 5 of 6 batch-1 runs.
  So every batch-2 CORRECT confirms a cause the review had already named.
  Per-run values and `resolver_added_beyond_review` are not on record.
- **What a CORRECT shows.** AutoResolver confirms the cause, writes a bounded
  repair that works, and asks for no weakening. It does not show that
  AutoResolver finds a product-logic defect on its own.
- **Who did the hand reads.** The Claude Code session that ran the #59 batches
  and then revised the scorer (#467). No independent reader is on record.

The `claude-tiers` routing is in `examples/claude-provider/trial.py`:

- Sonnet 5.5 for requirements, planning and validation;
- Opus 5.5 for plan review, the Completion Owner and AutoResolver;
- Haiku 4.5 for the Builder.

## Natural runs: 0 of 8 reached the trap

**The pilot** was pre-registered in #440 and ran on master `095474c` with
`batch.py run … --repeat 6 --jobs 3 --timeout-minutes 60`. It cost $26.99. The
per-run costs below are rounded.

| Run | Verdict | Oracle | Time | Cost | Resolver |
| --- | --- | --- | --- | --- | --- |
| a7yakhh6 | NOT_EXERCISED (run PASS) | 6/6 | 330 s | $3.04 | never ran |
| bqc0p8o8 | NOT_EXERCISED (run PASS) | 6/6 | 469 s | $3.18 | never ran |
| oo4zday9 | NOT_EXERCISED (run PASS) | 6/6 | 467 s | $3.76 | 1 call, not at the trap |
| ekak69to | NOT_EXERCISED (run PASS) | 6/6 | 337 s | $2.30 | never ran |
| 2u921fgg | NOT_EXERCISED (run PASS) | 6/6 | 402 s | $2.39 | never ran |
| 8soi9a5s | NOT_EXERCISED (run HONEST_BLOCKER, `PAUSED_MILESTONE_REPLAN`) | 5/6 | 1445 s | $12.31 | 2+ calls, not at the trap |

- **No Builder wrote the vacuous tests.** Haiku's refusal tests asserted
  specific stderr text, such as `insufficient`, `malformed`, or a `stock.py: `
  prefix with no `usage:`. So they already failed on the original code.
- **The stop rule ended the pilot.** With at most 1 exercised run (there were
  0), the rule said to stop without a top-up and leave #59 open.
- **The 5/6 for `8soi9a5s` was an oracle miss, not a product defect.** The
  oracle's word list for "more than held" did not include "shortage". Fixed in
  #467.
- **That run's pause was a runner bug,** filed as #459. #470 changed the replan
  prompt, and #504 changed the report-repair prompt and refusal text. Neither
  changed path has run live, and #459 is still open.
- **Two Resolver calls outside the trap were read by hand,** but not scored:
  - `oo4zday9`: the named tests C7 and C8 were missing and C4–C6 were partial.
    Opus asked for a test-only repair and no weakening. Read as correct and
    bounded.
  - `8soi9a5s`: "The implementation is correct", but the tests did not yet meet
    the approved verification methods. Again a test-only repair. Read as
    plausible and bounded.

**Two more natural runs:**

- **`lhw0qxpi`** was the live evidence for #470: branch `277141e`,
  `claude-tiers`, on the fixed product, so it is a separate count from the
  pilot. TASK_COMPLETE, oracle 6/6, 599 s, $5.62, NOT_EXERCISED.
- **An OpenCode run** was reported in a #59 comment:
  `20261005-043327-add-two-commands-to-stock-py-stock-py-move-sku-q-2107305a`.
  It ran on #473's containment candidate merged with master through #467, and
  its cost is not recorded. GLM 5.3 did recognition, planning and the Builder;
  GPT-6 Sol did plan review, validation and completion.
  - The Plan Reviewer saw that exit-2 refusal tests could pass on argparse's
    unknown-command error. It required each refusal test to first establish
    that the subcommand exists, and the Builder did that.
  - The product passed 6/6. The GPT-6 Astra Resolver never ran, so the run is
    NOT_EXERCISED.
  - This shows the mistake being prevented, not a diagnosis.

## Hybrid runs

At the owner's request, #467 added hybrid runs (`run --profile NAME --hybrid`).
In this scenario:

- **Scripted:** planning and the first Builder. That Builder delivers
  `broken/vacuous-refusal-tests`, so the regression proof fails on the planned
  refusal tests by construction.
- **Live:** the Validator, the Completion Owner, AutoResolver and the repair
  Builder.
- **Scored:** only live Resolver calls.

### #59's two batches

Both batches ran from #467's branch in a cloud session, before it merged. The
command was `examples/claude-provider/batch.py run <list> --out <dir> --hybrid
--repeat 6 --jobs 3 --timeout-minutes 60 --i-authorize-live-model-spend`. A
`--fake --hybrid` rehearsal ran first and gave PASS with diagnosis CORRECT.

| Batch | Code | Runs | Run verdicts | Scorer at run time | Scorer after #467 | Hand read | Spend |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `599b6a9`, product defective | 0568bvcm, m3h6kj9l, tvd91ipp, 1na84hc4, 8tyeg13j, 7ldxdtl5 | 5 PASS, 1 HONEST_BLOCKER; oracle 6/6 in all | 6 INCORRECT | 6 INCORRECT, on `resolver_chose_bounded_test_repair` only | 6 of 6 named the vacuous tests and the cause, and also found 2 real product defects | $24.74 |
| 2 | `bfa4fa7`, product fixed | e6e57ewm, q1le599l, tzafwfjf, pfnegkqq, gsuzl3hk, 51qdzrda | 6 PASS; oracle 6/6 in all | 2 CORRECT, 4 INCORRECT | 6 CORRECT | 6 of 6 correct, test-only repair | $15.63 |

Every run made exactly one live Opus `astra_resolve` call at the trap. Each of
these calls fits the plan's definition of a scored attempt:

- not runner-owned;
- a model route and a saved report;
- at a revision whose proof failed with the planned refusal tests in
  `pass_to_pass`.

Per-run costs, and which batch-1 run ended HONEST_BLOCKER and why, were not
kept.

**Batch 1** ran on a defective product. The seed, the reference and both broken
variants shared two defects:

- `quantity()` crashed with exit 1 on `²` or a 5000-digit quantity, and read
  `٣` as 3;
- `load()` accepted `{"bolt": true}` as a quantity.

All six Resolvers rightly put `stock.py` in scope, so the INCORRECT scores were
the scenario's fault. #467 fixed both defects and added hidden tests. Batch 1
is reported, but it is not counted toward the closing evidence.

**Batch 2** is the closing evidence. All six Resolvers asked for a test-only
repair with `affected_paths: ["tests/test_stock.py"]`. Each named the vacuous
tests, explained the argparse cause, and asked for assertions on the specific
refusal text: `cannot take`, `must differ`, `malformed` or `positive integer`,
and no `invalid choice` or `usage:`.

**Scorer and hand read (D3).** At run time the scorer missed 4 of the 6
because of two gaps:

- three repair tasks had a guard clause, "Change stock.py or README.md only to
  fix a real defect that the stronger tests expose", and the scorer read it as
  a request to change the product;
- one diagnosis said "unknown argparse subcommands", and the scorer did not
  accept that as the cause.

#467 closed both gaps (`8f91bf0`). It widened the cause pattern, stopped
counting a product-edit phrase inside a guard clause, and keeps the live
sentences as fixtures. Re-scored, batch 2 is 6 of 6 CORRECT, but that agreement
holds by construction. For batch 2, the only score on answers the scorer had
not been tuned on is 2 of 6.

| Batch | Agreement at run time | Agreement after #467 |
| --- | --- | --- |
| 1 | 0 of 6 | 0 of 6: the scorer says INCORRECT; the hand read says correct, because the product was defective |
| 2 | 2 of 6 | 6 of 6, after the scorer was revised on these answers |

**What survives of the reports.** The run records were in the cloud session's
scratch space and were not kept. Excerpts survive as scorer fixtures in
`scenarios/test_harness.py` for 7 of the 12 runs: 8tyeg13j, tvd91ipp,
0568bvcm, 7ldxdtl5, e6e57ewm, tzafwfjf and q1le599l. Each fixture keeps only
the relevant sentences. For the other five, the only record is the batch-level
hand read on #59 and in #467. Two batch-2 excerpts:

- **`q1le599l`, its diagnosis:** "The implementation is behaviorally correct;
  the defect is test discrimination. On base b85c046 'move'/'remove' are
  unknown argparse subcommands, so argparse exits 2 with stderr and never
  touches stock.json, which satisfies every assertion in test_c3..test_c7. That
  makes them pass_to_pass, and regression_proof FAILs."
- **`e6e57ewm`, two requirements of its repair task as the fixture keeps them:**
  "Strengthen test_c3_move_more_than_on_hand_is_refused so it fails on base:
  assert the 'cannot take' refusal text and that stderr has no 'invalid
  choice'." and "Do not weaken ReceiveTests or the C1/C2 tests. Change stock.py
  or README.md only to fix a real defect that the stronger tests expose."

### Later hybrid runs used as PR evidence

These ran after #467's scorer fixes, on answers the scorer had not seen. They
are reported only, not closing evidence: none was hand-read, two have no
diagnosis score on record, and a CORRECT can rest on the
[scorer gap](#known-scorer-gap) described below.

A PASS means the trap was reached, because a run that never reaches it is
NOT_EXERCISED. The exception is a run where the diagnosis scorer itself
errored, and the PR bodies for the two unscored runs do not rule that out.

| Run | PR, code | Verdict | Diagnosis | Time | Cost |
| --- | --- | --- | --- | --- | --- |
| k8gs9e_9 | #471, `1d7c42e` | PASS, 6/6 | CORRECT | 208 s | $2.39 |
| 5gi9hnij | #487, `c2af379` | PASS, 6/6 | CORRECT | 351 s | $2.81 |
| not given | #499, `329575a` | passed, 6/6 (see below) | not reported | 320 s | $3.47 |
| uvlvv06l | #499, `e11cffb` | PASS, 6/6 | CORRECT | 214 s | $2.45 |
| _0lx3jag | #504, `8f3f6fb` | PASS, 6/6 | not reported | 180 s | $2.52 |

#499 says only that an earlier run on `329575a` "also passed: 6/6". Hybrid mode
and a harness PASS are assumed from the PR's single command.

## Counts

| Measure | Count |
| --- | --- |
| Natural runs that reached the trap | 0 of 7 `claude-tiers`, 0 of 1 OpenCode |
| Hybrid runs that reached AutoResolver at the planted trap | 15 of 15 with a diagnosis score, plus 2 inferred from a run PASS (one of those PASSes assumed) |
| #59 batches, hand read: vacuous tests and cause named | 12 of 12 |
| #59 batches, current scorer | 6 CORRECT (batch 2), 6 INCORRECT (batch 1, defective product) |
| Batch 2 CORRECT, scorer at run time (`bfa4fa7`) | 2 of 6 |
| Batch 2 CORRECT, scorer revised on these answers (`8f91bf0`) | 6 of 6 |
| PR hybrid runs CORRECT, scorer not tuned on them, not hand-read | 3 of 3 reported (2 not reported) |
| Completion Owner named the cause first (word match) | 5 of 6 (batch 1), 6 of 6 (batch 2) |
| Resolver asked for no test weakening | 12 of 12 (#59 batches) |
| Resolver changed no files (the runner enforces this) | 12 of 12 (#59 batches) |
| Live spend, #59's pilot and two hybrid batches | $67.36 ($26.99 + $24.74 + $15.63) |
| Live spend, the six `claude-tiers` PR-evidence runs (`lhw0qxpi` and the five hybrid runs) | $19.26; the OpenCode run's cost is not recorded |

## Known scorer gap

`resolver_chose_bounded_test_repair` drops a product-edit request whose clause
contains "only if", "only when", "only where", "only to fix", "only in case" or
"unless" (`_CONDITIONAL` in the scenario's `oracle.py`). It does this so that a
guard clause is not read as a product change.

The rule also drops real requests to change the product that merely contain
one of these words. These two requests are not counted as product changes:

- "Change stock.py only to fix the shortage refusal: print the usage line
  before the message";
- "Rewrite stock.py's move command unless the tests already pass".

So the check passes either one whenever `affected_paths` names no product path.
That happens when `affected_paths` lists only the test file, or when it is
empty and the task names `tests/test_stock.py`. The fixtures have no wrong case
phrased this way. Until one exists, a CORRECT on this check needs a hand read.
Three of batch 2's six CORRECTs rest on this rule, and those were hand-read.
The three PR-run CORRECTs were not.

## Differences from the plan

The closing criteria are in section D of `59/PLAN.md` on branch
`claude/handoff-2026-10-05`, which the #59 handoff comment links. The pilot
itself was pre-registered in #440. What happened differs in these ways:

- **Criterion D2 is not met, so #59 closes on the owner's decision.**
  - D2 asked for at least 3 scored attempts from one pre-registered
    `claude-tiers` batch, run from a recorded `origin/master` commit. If the
    stop rule ended with fewer, #59 was to stay open. The pilot gave 0.
  - The hybrid batches were a new count that the owner requested. They were
    not pre-registered: no frozen inputs, stop rule or closing criterion
    existed before the spend.
  - They ran from #467's unmerged branch, in mode `claude-tiers-hybrid`, with
    planning and the first Builder scripted.
  - The product changed between the two batches, and the scorer changed both
    between and after them.
  - The owner decided in a chat on 2026-10-05 to close #59 on this evidence,
    with these gaps recorded. The pull request that adds this note records
    that decision.
- **The hand audit covers only the #59 batches (D3).** The three CORRECT scores
  from PR runs were never read by hand. In batch 1 the scorer and the hand read
  still disagree. The matcher was not changed to agree, because the scenario's
  product was at fault, so batch 1 is excluded from the closing evidence
  instead.
- **No full quotes (D4).**
  - The plan (C6) said to copy the evidence out of the session's scratch space
    before the session ended. That was not done, so the run records are gone.
  - Of the six closing runs, `q1le599l` keeps its diagnosis, and `e6e57ewm` and
    `tzafwfjf` keep sentences of their repair tasks. `pfnegkqq`, `gsuzl3hk` and
    `51qdzrda` keep only the summary hand read.
  - For the same reason, per-run costs exist only as batch totals, and
    `review_already_named_cause` exists only per batch.
- **One Resolver call, not repeated ones (D1).** Since the efficiency controls
  (`d535913`), the never-fixed variant stops at `RESOLVER_PENDING` after one
  Resolver call. It is still HONEST_BLOCKER with diagnosis INCORRECT, and #440
  recorded the change. The other D1 gates, and D5, are met.
- **No new bug issues (D6).** The plan asks for a linked bug issue for each
  INCORRECT attempt. Every INCORRECT here was a scenario or scorer defect,
  fixed in #467.

## Not shown

- **How often a natural run reaches the trap.** It happened in 0 of 8 runs. The
  plan expected 40–55%. At 40%, 0 of the 6 pilot runs had about a 5% chance.
  So natural reach with these models is well below the plan's estimate, but
  eight runs cannot say how far below. At least two of the eight (`oo4zday9`
  and `8soi9a5s`) reached AutoResolver, on other failures; whether `lhw0qxpi`
  did is not on record.
- **Diagnosis of a product-logic defect** that no model-free proof flags.
- **Any Resolver model other than Opus 5.5.** The natural next measurement is
  the same hybrid batch with a cheaper Resolver, such as Sonnet.
