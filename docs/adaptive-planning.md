# Adaptive planning

The fixed AutoPlanner sequence sends every build request through the same stages,
whatever its size:

```
requirements → plan → review → revise → final review → your approval
```

A one-line change pays for five model calls before you see a plan. A plan the
Plan Reviewer has no objection to is still revised and reviewed again. A hard
plan gets exactly one revision, and whatever the Reviewer still objects to comes
back to you as questions.

Adaptive planning lets the evidence decide how much planning a request gets
instead. It is **on by default** for new runs that use joint planning on the
default planning flow (since 2026-10-02, after the two live comparisons below
found fewer model calls and questions with the same plan quality).
`--no-adaptive-planning` keeps the fixed sequence for a new run, and
`--adaptive-planning` insists on adaptive planning, refusing a run that cannot
use it. A saved run keeps the planning flow it was started with. It does not
predict the job's size up front. Each decision is made by the first stage that
has the evidence for it:

| Decision | Made by | Evidence | Effect |
| --- | --- | --- | --- |
| Is the plan finished? | Plan Reviewer | Its own concerns, each marked blocking or not | No blocking concern: an ordinary Planner draft goes to you, with the non-blocking concerns as notes. Otherwise the Planner revises. Progressive delegations keep revision and final independent review, which supply their approval authority. |
| How many review rounds? | The runner | The draft's declared milestones and the files they touch (`plan_size`) | A large plan (3+ milestones or 10+ files) gets 3 review calls instead of 2, so a revision is reviewed again before the final decision. |
| Does feedback on a plan need requirements gathering? | The runner, then the Planner | The run's status: a complete plan waiting for your approval | The Planner revises the plan you were shown, then the Plan Reviewer reviews it. The Planner can send feedback that changes what is being built back to Requirements. See [below](#feedback-on-a-plan-you-were-shown). |

For this to work, the Planner's draft includes its `initial_task` (the first
Builder task). In the fixed sequence only the final review writes one.

What does not change:

- You approve every build plan. Adaptive planning removes model calls, never
  your approval.
- Requirements always runs. It is the first stage of every run and follow-up turn
  and names the job (owner decision 2026-10-22, #1846, #1860). Until then a build
  request the Job recognizer judged `clear` went straight to the Planner; that skip
  is gone. While it names the job, Requirements also judges clarity by the
  recognizer's rule. Clarity then skips nothing; it keeps a small clear brief on one
  review allowance at the reduced Plan Reviewer effort (#1564), one rung below the configured
  one and fitted to the variants the reviewer's model declares (GLM 5.3 declares `low`, `high`
  and `max`, so a step from `high` lands on `high`, not `medium`; the move is recorded in
  `effort_fits`, #2020). A build named with
  `--workflow build` still runs Requirements, and nobody judges its clarity.
- The setting applies only to new runs. A saved run keeps the planning flow it
  was started with: resuming with `--adaptive-planning` a run that lacks it, or
  with `--no-adaptive-planning` an adaptive one, is refused. Repeating the run's
  own choice, or passing no flag, is fine.
- Questions work as before. On the fast path the Planner asks them itself.
- The Tester and Completion Reviewer still judge the work, and bug-fix, review,
  design and discuss workflows are unaffected.
- A plan that the Reviewer does block on takes today's path: revise, then a final
  review that settles every concern.

Where it lives: `tools/autocode_adaptive_planning.py` holds the decisions as pure
functions. `units/autoplanner.py` (`schema_for`, `after_challenge`,
`after_revise`, `rerun_requirements`), `autocode_workflows.apply` and
`autocode_goals.feedback` apply them. State: `workflow.clarity`,
`workflow.requirements_skipped` (only on runs saved before #1860), `planning.adaptive` (`size`, `signals`,
`review_limit`, `challenges`, `approved_at`, `final_stage`), and `revises_plan`
on a feedback event. `autocode --status --json` shows the decisions under
`planning_depth` ([task-run interface](task-run.md)): the clarity, whether
an older run skipped Requirements, the plan's size, the review allowance and whether the
Plan Reviewer approved the draft early.

## Feedback on a plan you were shown

Without adaptive planning, feedback on a plan (`--feedback`, or typing a change
in chat) restarts planning from the top, however small the change:

```
your feedback → requirements → plan → review → revise → final review → your approval
```

In an adaptive run, feedback sent while a complete plan waits for your approval
goes to the Planner, which revises the plan you saw:

```
your feedback → plan (revises the plan you saw) → review → your approval
```

The review works as for any adaptive draft: with no blocking concern the plan
comes back to you; otherwise it is revised and reviewed again. Feedback at any
other point (while questions are open, or queued during a build) still restarts
from Requirements.

Requirements is skipped because its job was done for the request you already
planned; your feedback changes that plan. Three checks keep the shortcut safe:

1. **The runner checks the revision delivers your feedback.** Your feedback
   becomes a requirement the Planner must trace, like the ones the Requirements
   stage writes: covered by an acceptance criterion or required behavior of the
   new plan, or the draft is rejected. This needs no model call. It stays a
   traced requirement until a Requirements report takes it in.
2. **The Planner can send it back.** If your feedback changes what is being built
   (a different product, user or outcome, not an added or changed behavior), the
   Planner says so instead of revising (`requirements_rerun`), and the runner
   discards that draft and runs the Requirements stage. "Make it a web page
   instead of a command-line tool" is the example in the comparison corpus.
3. **The Plan Reviewer still reviews the revision**, and is told to check that
   your feedback is applied as you said it and that nothing you asked for earlier
   was lost. The runner's existing guard already refuses a revision that drops or
   rewords a protected item (a criterion, behavior or exclusion) without citing
   your feedback.

You still approve the revised plan.

## Comparing it with today's pipeline

`scenarios/run.py plan-compare` plans each request in `scenarios/planning.toml`
twice, once with today's pipeline and once with `--adaptive-planning`. Each run
starts in a fresh copy of the request's seed and stops when AutoCode shows the
plan for approval. A request with `feedback` then sends that feedback instead of
approving, and stops at the next plan shown for approval; the feedback round is
reported in its own columns. Nothing is built. The driver answers questions with
AutoCode's proposed default, the same way for both variants.

```sh
PY=.venv/bin/python
$PY scenarios/run.py plan-compare --fake                      # plumbing: every adaptive path, scripted, seconds
$PY scenarios/run.py plan-compare --profile default --jobs 3 --i-authorize-live-model-spend
```

The evidence directory holds `comparison.md` (stages, review calls and blocking
concerns, questions, tokens, model time and plan shape per request and variant),
`records.json`, each run's `state.json`, and `blind/`. `blind/` has each
request's two final plans side by side as Plan A and Plan B, with the key in
`blind/key.json`, so plan quality can be judged without knowing which variant
wrote which.

## Results: live comparison with feedback, 2026-10-02

Each of the 11 requests in `planning.toml` was planned once per pipeline on the
default routes (Requirements and Planner on GLM 5.3, Plan Reviewer on GPT-6 Sol).
After the first plan, each run got the request's `feedback` instead of an approval
and went on to the next plan shown for approval. The code under test was the
feedback path above plus the re-review context for answered questions
(`feat/rereview-after-answers`). Evidence:
`.scenario-runs/20261002T091307Z-plan-compare-default-ac6a8qs0` (not committed;
`blind/verdicts-before-key.md` holds the verdicts written before the key was opened).

| | Today | Adaptive | Change |
| --- | --- | --- | --- |
| **First plan:** plans reached | 11/11 | 11/11 | |
| model calls | 112 | 95 | −15% |
| questions to the user | 26 | 16 | −38% |
| **Feedback round:** new plans reached | 9/11 | 9/11 | |
| model calls | 85 | 50 | −41% |
| plan-review calls | 26 | 15 | −42% |
| tokens in / out | 5.45M / 1.05M | 4.50M / 0.63M | −17% / −40% |
| model time | 267 min | 157 min | −41% |

The first-plan numbers repeat the 2026-09-30 comparison (−15% calls, −37%
questions). Four adaptive feedback rounds took two calls: the Planner revised the
plan and the Plan Reviewer approved it at once.

**Plan quality after the feedback was the same.** Judged blind on whether the
feedback was applied as stated and the rest of the plan kept: adaptive better in
3 pairs (deployment-planner, tenant-http-api, transactional-outbox), today's
pipeline in 3 (small-json-flag, vague-reading-list, vague-timesheet-useful), 5
ties. Both pipelines sometimes added criteria nobody asked for.

**Where the adaptive path fell short:**

- `vague-reading-list` ("make it a web page instead of a command-line tool"):
  the Planner produced no output for 300 seconds three times and the run stopped
  for a person, so the send-back to Requirements was never exercised live. A large
  change can exceed the idle limit before the Planner writes anything.
- `vague-timesheet-useful`: the revision appended a section to the default report
  although the feedback said to keep that output exactly as it is, and the Plan
  Reviewer did not object.
- Planner slips on the feedback path, each costing a report repair: a
  `conflict_resolutions` entry for a feedback-versus-plan conflict (2 runs; the
  Planner rule now says to use `contract_changes` instead), a blank feedback
  `answer_id` on an assumption (2 runs), and contract changes named by list
  instead of by item.

Today's pipeline missed a new plan twice: `deployment-planner` (both pipelines
stopped for a person) and `transactional-outbox` (an Resolver blocker,
"Planning recovery scope or inputs changed").

Earlier attempts at this comparison, stopped and restarted, found four Planner
slips, each fixed on this branch or an accompanying one: a trace row without its
`requirement_id` (now named by the runner when only one requirement can be meant;
this branch), `example_correction: null` refused by the schema
(`fix/example-correction-null`), a declared change whose item was wrapped in its
list name (`fix/contract-change-item-refs`), and report fields written inside the
contract (`feat/rereview-after-answers`).

## Results: live comparison, 2026-09-30

Each of the 11 requests in `planning.toml` was planned once with each pipeline
on the default routes: Requirements and Planner on GLM 5.3, Plan Reviewer on
GPT-6 Sol. That is one sample per request, and the same request can swing by
several calls from run to run, so read per-request differences as noisy. The
evidence is under `.scenario-runs/20260930T180620Z-plan-compare-default-*`
(not committed).

| | Today | Adaptive | Change |
| --- | --- | --- | --- |
| Plans reached (no errors) | 11/11 | 11/11 | |
| Model calls | 92 | 78 | −15% |
| Clear requests (8) | 64 calls, 7 questions | 52 calls, 1 question | −19% calls |
| Vague requests (3) | 28 calls, 9 questions | 26 calls, 9 questions | about even |
| Plan-review calls | 27 | 27 | same |
| Questions to the user | 16 | 10 | −37% |
| Model time | 160 min | 128 min | −20% |
| Tokens in / out | 3.58M / 734k | 2.93M / 566k | −18% / −23% |

**Plan quality was about the same.** Each pair was judged blind (Plan A and
Plan B, key opened after every verdict was written) against the request and,
where the request came from the catalog, against the hidden oracle tests.
Adaptive was better in 3 pairs, today's pipeline in 2, and 6 were ties.

- *Adaptive better:*
  - `parallel-diamond`: today's plan invented "raise ValueError for an
    unknown node", which a faithful build would fail on two oracle checks.
  - `transactional-outbox`: only the adaptive plan tested concurrency across
    separate `Store` instances, which is what the hidden test does.
  - `feature-by-project`.
- *Today better:*
  - `vague-timesheet-useful`: the adaptive plan changed the default report
    output despite the answer "keep existing behavior intact".
  - `tenant-http-api`, slightly.

In each lopsided pair, the weaker plan had been through the same full
review-revise-final cycle. So these look like model variance, not an effect of
the pipeline.

**Where the savings came from:**

- **Skipping Requirements for clear requests.** Nearly all of the
  question savings are here (7 questions → 1 on clear requests). The owner
  removed this skip on 2026-10-22 (#1860): Requirements now runs first on every
  run and names the job, in place of the recognizer's call.
- **Fewer planning restarts:** 3 today, 1 adaptive. Most began when a revision
  renamed a protected contract item; the guard then asks the user, and
  planning starts over. Since 2026-10-25 a revision may apply the reviewer's
  blocking request to the Planner's own constraint or permission line in a plan
  nobody approved, and a redraft after AutoCode's own technical answer continues
  the reviewed cycle instead of starting over ([workflow](workflow.md)).

The live recognizer judged every request correctly: 8 clear, 3 vague.

**What did not pay off as designed:**

- **Early approval rarely fires.** The Reviewer blocked on the first draft in
  21 of 22 runs, usually on real test gaps. Adaptive approved early only
  twice: once at the first review (`tiny-greeting`), once at a re-review
  (`deployment-planner`).
- **The size rule over-fires.** "10+ acceptance criteria" made 6 of 11 plans
  large, 3 of them single-milestone changes, which gave them a third review
  call. Those extra rounds did find real gaps (for example in
  `extend-record-query`), but they are why review calls came out even. Fixed
  after the run: size now comes from milestones and files only, which would
  have made only `parallel-diamond` large.
- **More bad citations.** Planner revisions cited the Reviewer's own report
  under `.autocode/` as evidence, and the report was rejected and repaired.
  This happened 3 times, all on the adaptive side, and 2 of them followed a
  second review. Since fixed for both pipelines by #192's SOURCE CITATIONS
  rule (merged after this run), which every planning stage after Requirements
  receives.

**Found in both pipelines:**

- The protected-item guard turns test-name renames into user questions and
  full planning restarts. #192 (merged after this run) now tells the Planner
  to keep existing test names; its effect is not measured yet.
- Planning restarts after a deferred approval are unbounded
  (`docs/bugs/2026-09-30-unbounded-planning-restart.md`).

## Compact planning for bug fixes

Compact planning is enabled for new joint-planning runs. `--no-compact-bugfix-planning` disables it;
`--compact-bugfix-planning` explicitly enables it. A reproduced, localized bug can
skip one paid planning call. On the ordinary bug-fix path Requirements runs, the Investigator
reproduces the bug (`investigate_bug`), the Planner writes the plan draft (`astra_discovery`), the
Plan Reviewer reviews it and you approve the displayed plan. On the compact path the runner
writes that draft itself, deterministically, from two records it already holds: the
Investigator's diagnosis (its root cause, invariant, regression cases, affected and test paths)
and the Requirements record (every requirement row becomes a required behaviour, and the
requirement trace is checked as a Planner's would be). The Plan Reviewer reviews the runner's
draft exactly as it reviews a Planner's; a clean review goes to your exact-token approval, as an
adaptive run's early approval does, and a blocking concern returns the run to the full path, where
the Planner drafts with the review in its packet; the compact draft's review call is refunded, so the
full path keeps the plan-review allowance it would have had. The draft is never approved by a policy.
Everything after approval is unchanged: acceptance tests when configured, the Builder, the
regression proof (each restore case fails on the original code and passes after the fix), the
Tester and a separate Completion Reviewer by default. A new run may independently select
`--review-routing combined` to combine testing and completion on the configured Completion Reviewer
route. That explicit choice replaces separate enforced milestone-checkpoint routing; it is included
in the displayed plan and binds to its exact approval. The independent regression and changed-code
evidence gates still apply, and existing model/effort choices are retained.

The path is taken only when every trusted signal admits it; a model's word can keep the full
path but never grant the compact one:

- the run has compact planning enabled and plans adaptively, with joint planning;
- Requirements named the bug-fix job (a pinned `--workflow bugfix` has no Requirements record);
- the Investigator reproduced the bug with a probe the runner ran, sized the fix `small`, and named
  a root cause, an invariant and at least one regression case;
- the fix touches at most four files (affected and test paths together), and none of them, nor the
  diagnosis note, matches the project's declared `sensitive-paths`
  ([project settings](cli.md#project-settings)); without a declaration new runs save shipped conservative
  patterns for authentication, security, payments, billing, migrations, credentials, CI/build files and
  dependency manifests/locks. A project may replace these patterns, including an explicit empty list;
- the contract validators accept the runner's draft; a refused draft keeps the full path, with the
  refusal recorded.

The decision, its reasons and the hashes it rests on are recorded once per job and shown by the
status view as `stage_policy` ([task-run interface](task-run.md#status-view)). Existing runs keep
their saved behaviour; a saved run refuses a changed flag.
