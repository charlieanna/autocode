# Adaptive planning

By default every build request goes through the same AutoPlanner sequence,
whatever its size:

```
recognize → requirements → plan → review → revise → final review → your approval
```

A one-line change pays for six model calls before you see a plan. A plan the
Plan Reviewer has no objection to is still revised and reviewed again. A hard
plan gets exactly one revision, and whatever the Reviewer still objects to comes
back to you as questions.

`--adaptive-planning` (new runs only; needs joint planning and the default
planning flow) lets the evidence decide how much planning a request gets. It
does not predict the job's size up front. Each decision is made by the first
stage that has the evidence for it:

| Decision | Made by | Evidence | Effect |
| --- | --- | --- | --- |
| Does the request need requirements gathering? | Job recognizer | The request text: does it say what to build and how to tell it is done, with no product choice left open? | `clear` build requests go straight to the Planner; `vague` ones keep the Requirements stage. When unsure the recognizer says `vague`. |
| Is the plan finished? | Plan Reviewer | Its own concerns, each marked blocking or not | No blocking concern: an ordinary Planner draft goes to you, with the non-blocking concerns as notes. Otherwise the Planner revises. Progressive delegations keep revision and final independent review, which supply their approval authority. |
| How many review rounds? | The runner | The draft's declared milestones and the files they touch (`plan_size`) | A large plan (3+ milestones or 10+ files) gets 3 review calls instead of 2, so a revision is reviewed again before the final decision. |

For this to work, the Planner's draft includes its `initial_task` (the first
Builder task). Without the flag, only the final review writes one.

What does not change:

- You approve every build plan. Adaptive planning removes model calls, never
  your approval.
- A build named with `--workflow build` skips recognition, so nobody judges its
  clarity and the Requirements stage always runs.
- The flag applies only to new runs. Resuming a run that was started without
  it, with the flag added, is refused, so an existing run keeps its planning
  flow. Repeating the flag on an adaptive run is fine.
- Questions work as before. On the fast path the Planner asks them itself.
- The Validator and Completion Owner still judge the work, and bug-fix, review,
  design and discuss workflows are unaffected.
- A plan that the Reviewer does block on takes today's path: revise, then a final
  review that settles every concern.

Where it lives: `tools/autocode_adaptive_planning.py` holds the decisions as pure
functions. `units/autoplanner.py` (`schema_for`, `after_challenge`,
`after_revise`) and `autocode_workflows.apply` apply them. State:
`workflow.clarity`, and `planning.adaptive` (`size`, `signals`, `review_limit`,
`challenges`, `approved_at`, `final_stage`).

## Comparing it with today's pipeline

`scenarios/run.py plan-compare` plans each request in `scenarios/planning.toml`
twice, once with today's pipeline and once with `--adaptive-planning`. Each run
starts in a fresh copy of the request's seed and stops when AutoCode shows the
plan for approval. Nothing is built. The driver answers questions with
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
  question savings are here (7 questions → 1 on clear requests).
- **Fewer planning restarts:** 3 today, 1 adaptive. Most began when a revision
  renamed a protected contract item; the guard then asks the user, and
  planning starts over.

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
