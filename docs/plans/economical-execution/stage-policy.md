# B. When each stage runs, is omitted or escalates (#1890)

Planning input for the first owner checkpoint on #1867, read with
[verification](verification.md). Read-only audit of master `08ddeb2a3` (2026-10-22). This
record proposes policy; it changes no runtime behavior, route or approval rule.
[Qualification](qualification.md) owns the implementation slices.

## Stages today

Requirements runs first in every run (#1860) and names the job. The build pipeline then
runs the stages below. Screen names come from `autocode_roles`; where the screen name and
the planning docs disagree, both are given.

| Stage | Job on screen | Responsibility | Enters with | Leaves with | Runner work around it, no model |
| --- | --- | --- | --- | --- | --- |
| `requirements_gather` | Requirements | Understand the request, ask what only a person can answer, name the job and its clarity | The brief and the repository | Requirements report with `job` and `clarity`; settled questions | Code references checked; repeats while questions remain; spec-first runs stop for "answer `plan`" |
| `investigate_bug` | Investigator | Reproduce the bug and find its cause (bug-fix job only) | Requirements report | `bug_diagnosis` with outcome, reproduction, root cause, affected paths, test cases and `fix_size`; bound to the source revision | A reproduced bug goes straight to planning; not reproduced ends the job |
| `astra_discovery` | Requirements (docs call it the Planner's draft) | Draft the contract and trace every requirement | Requirements handoff, diagnosis | Draft contract and `requirement_trace` | Citation and trace checks when the draft arrives |
| `astra_challenge` | Plan Reviewer | Find blocking concerns in the draft | Draft | Concerns, brief and risk observations | Under adaptive planning a challenge with no blocking concern can approve the draft |
| `glm_revise` | Requirements | Answer the concerns | Draft and concerns | Revised contract | Skipped under adaptive planning when the first challenge passes |
| `astra_finalize` | Plan Reviewer | Seal the contract with an initial task | Revised contract | Sealed contract with `initial_task` | Skipped with `glm_revise`; then the person approves the displayed token |
| `write_acceptance_tests` | Tester (route `acceptance_tester`) | Write `test:` and `guard:` cases before the build (spec-first only) | Approved contract | Test files | `test:` must fail and `guard:` pass on the current source |
| `orchestrator` | Orchestrator | Schedule parallel milestones | Approved milestones | Worker batch | Entirely deterministic |
| `terra` | Builder | Implement the assigned task | Task with `affected_paths` | Changed files, commands run, results | Out-of-assignment edits refused (`PAUSED_ASSIGNMENT_SCOPE`); low-effort first attempt on small tasks |
| `sol` | Tester | Verify each criterion with executed checks | Builder's change | Per-criterion results with command evidence | Check replay in a clean copy, regression proof, protected tests, receipt binding |
| `astra_review` | Completion Reviewer | Decide complete, rework or continue | Validation and the change | Decision with every criterion verified | The deterministic completion gate |
| `astra_resolve` | Resolver | Diagnose a blocker after rework | Findings and history | Recovery decision | Bounded recovery counters |

Review, design and discuss jobs run their own single-stage workflows (`autocode_jobs`)
and never reach the build pipeline.

### What already decides depth

- **Adaptive planning is the default** for new runs that use joint planning on the
  default flow. After a first challenge with no blocking concern and an approvable
  draft, it skips `glm_revise` and `astra_finalize`. A clear brief that is small at
  recognition, or whose draft is small (at most one milestone and four affected paths),
  counts one review allowance across planning cycles, and its Plan Reviewer launches at
  a reduced effort (#1564, #1815). Since #1860, clarity no longer skips Requirements.
- **The low-effort first Builder attempt** (`autocode_light_builder`) applies to an
  approved one-milestone task with one to four affected paths, unless the person chose
  the effort.
- **The small-correction bug path is off.** `autocode_bug_job.SMALL_CORRECTION_ENABLED`
  is false by the user's decision of 2026-09-29. When on, a reproduced bug with
  `fix_size` "small" would get a runner-built one-task contract
  (`correction_contract`), skip planning and plan review, and record a
  `goal_approval` whose actor is `workflow_policy`: the run would approve itself.
- **A combined review path exists but cannot be turned on.** Under reviewer routing
  (`autocode_workflow`, mode `glm_first_v1`) one read-only `astra_checkpoint` call does
  validation and the completion decision together. It needs its own user approval
  bound to the contract hash. No command-line action calls
  `autocode_workflow.activate` on master; only tests do. See [verification](verification.md).

### What does not exist

- **No task-risk classifier.** Size (milestones and paths) is the only signal any
  stage decision reads. The Resolver's `RISK_FLAGS` and `HUMAN_ONLY_KINDS` classify a
  blocker for autonomous resolution, and `Blocker.risk_flags` is never set at runtime,
  so that gate is dormant. `autocode_risk_evidence` proves two named lifecycle
  protocols at completion; it does not classify tasks.
- **No plan-amendment path.** Scope growth during the Builder always pauses for a
  person (`--feedback` narrows or re-approves). The Resolver's `replan` action is
  excluded from every runtime boundary by design: the Resolver never changes the
  approved contract (1254fe712, written down in #1915).

## Principles for any shortcut

1. **Trusted inputs decide; model output can only raise depth.** Repository policy,
   the observed diff and runner checks may admit a shorter path. A model's
   classification or confidence may move a run to a deeper path but never to a shorter
   one. Task text never controls admission (B6).
2. **Every omitted stage names what still covers its responsibility**, using
   [verification](verification.md)'s obligations. An omission without named coverage
   is not allowed.
3. **Unknown means full.** A missing or conflicting signal selects the full path. A
   policy conflict takes the stricter rule.
4. **Omission is not approval.** Skipping a paid review never approves a plan. The
   person approves the displayed contract token on every path, unless an explicit
   company preauthorization applies ([contract](contract.md)).
5. **Escalation is one-way within a turn** and visible, with its trigger and
   evidence. Successful work is never restarted from the top.

## Trusted sensitivity input (proposed)

No input exists today, so every shortcut below depends on adding one.

- A project setting listing sensitive path patterns, with a default list AutoCode
  ships: authentication, permissions, payment and money arithmetic, migrations and
  schema, cryptography, concurrency primitives, destructive file operations, CI and
  build configuration, dependency manifests and lockfiles.
- The runner checks the plan's `affected_paths` against it before choosing a path,
  and the Builder's actual changed files against it at the end of each attempt.
- A Requirements report may add a hint (for example "changes how refunds are
  rounded"). The hint can only raise the class.

Path patterns miss behavior that lives in ordinary files (B1, B8). That is why the
sensitive class also triggers on a hint, and why the comparison in
[qualification](qualification.md) needs a deliberately small sensitive task.

## Decision table

"Proposed" rows need the first owner checkpoint. The existing rows describe current
behavior for completeness.

| Trigger | Stages | Required evidence | Omitted responsibility and what covers it | Escalates when |
| --- | --- | --- | --- | --- |
| Default, or any unknown signal | Full pipeline as today, adaptive planning included | Today's gates | Nothing omitted beyond adaptive planning | — |
| **Proposed P1:** reproduced bug, cause named, no sensitive path, one task of at most four paths, no public interface in `affected_paths` | Requirements, Investigator, runner-built contract, **person approves**, Builder, one combined independent verification | Reproduction fails on the base; diagnosis bound to the current source; contract derived from the diagnosis's test cases | Planner draft and Plan Reviewer: the contract is mechanical from the diagnosis, and the person reviews it. Separate Tester: the combined pass ([verification](verification.md)) | Diagnosis not reproduced or cause uncertain; any changed file outside the task or on a sensitive path; regression passes on base; a second Builder attempt; any criterion NOT_VERIFIED |
| Bug with unclear cause | Requirements, Investigator, adaptive planning, Builder, separate Tester and Completion Reviewer | Today's gates | Nothing new | Diagnosis widens scope: milestone plan |
| Small feature, not sensitive | Today's adaptive path with reduced review depth | Today's gates | **Proposed:** separate Tester, replaced by the combined pass when qualified | Changed files outside the plan; interface change; sensitive hint |
| Change across interfaces or components | Full planning with milestones and the orchestrator; separate verification per milestone | Today's gates | Nothing | — |
| Sensitive path or hint | Full planning at full review depth; no low-effort Builder attempt; separate Tester and Completion Reviewer | Today's gates plus the trigger recorded | Nothing. Size never shortens it | — |
| Review, design, discuss | Their own single stage | Today's | Not applicable | Job change by the person |
| Same-chat follow-up | Today: Requirements, then a new contract revision through planning, approval and full verification. **Proposed:** planning reuses unchanged criteria and settled answers under [evidence reuse](evidence-reuse.md) | Bindings in [evidence reuse](evidence-reuse.md) | Repeat planning of unchanged criteria: retained under their hash. Verification is never reused across a source change | Any binding fails |

## Worked paths for the representative classes

The classes and catalog entries are from [baseline](baseline.md). All savings are
hypotheses until the matched comparison runs.

- **Clear localized bug (`bugfix-trivial`).** Today, at best: eight model stages
  (Requirements, Investigator, draft, one challenge that approves early, acceptance
  tests under spec-first, Builder, Tester, Completion Reviewer) plus the person's
  approval. Under P1: Requirements, Investigator, the person approves a runner-built
  contract, acceptance tests, Builder, one combined verification. It omits the draft,
  the challenge and one verification call. Unmeasured.
- **Unclear bug (`bugfix-iso-weeks`).** The obvious place to fix is wrong; the cause
  lies in another module. If the Investigator names that cause and the fix fits one
  task, P1 applies; if not, the full bug path runs. The entry tests exactly what P1
  relies on: a fix in the wrong module must fail the oracle.
- **Small feature (`feature-timesheet-by-project`).** Today's adaptive path. The only
  candidate omission is the separate Tester, if the combined pass qualifies.
- **Sensitive small diff (new entry needed).** The sensitive path or hint selects the
  full path even though the diff is one line.
- **Same-chat follow-up (`review-then-fix`).** The review turn's findings carry
  forward today. The fix turn plans and verifies in full. Proposed reuse is limited
  to unchanged criteria and settled answers.

## Adversarial examples

| Case | Rule that handles it |
| --- | --- |
| B1 one-line authorization or rounding change | Sensitive path or hint; size is not read for this class |
| B2 failing regression, unproven cause | P1 needs the cause named and bound to the current source; otherwise the Investigator and full planning run |
| B3 "local" fix changes a shared API or schema | Public interface or schema files in `affected_paths` or in the changed files disqualify P1 and escalate |
| B4 scope expands during the Builder | `PAUSED_ASSIGNMENT_SCOPE` already refuses out-of-assignment edits. On a short path the run also moves to the full path for the rest of the turn |
| B5 follow-up changes one behavior; earlier requirements still pass | Earlier passing checks become `guard:` cases; unchanged criteria are retained; the changed behavior needs new red-then-green proof |
| B6 task text asks to skip checks | Only policy and runner checks admit a path. Text is at most a hint, and hints only raise depth |
| B7 documentation change that touches executable configuration | The class reads the actual changed files; CI, build and manifest files are sensitive by default |
| B8 no Resolver blocker flag, but sensitive behavior | Blocker flags are not a task classifier and are dormant at runtime; the sensitivity input decides |

## Unknowns and conflicts

- A run with no recorded policy keeps the full path ([contract](contract.md)).
- Missing size, diagnosis or diff information selects the full path.
- If repository policy and the model's hint disagree, the deeper class wins.
- If two rules select different paths, the one with more independent checks wins.

## Questions for the first owner checkpoint

1. **P1.** Revive the small-correction path with the person approving the runner-built
   contract, replacing its self-approval? Or keep it off until the full path is
   dependable, as decided on 2026-09-29?
2. **Sensitivity input.** Adopt a project-configured sensitive-path list with a
   shipped default, plus a Requirements hint that can only raise depth?
3. **Combined verification.** Allow the existing one-call validation-and-decision form
   for non-sensitive single-task work, subject to [verification](verification.md)?
4. **Follow-ups.** Allow planning reuse of unchanged criteria and answers under
   [evidence reuse](evidence-reuse.md)'s bindings?
5. **Scope growth on a short path.** Move to the full path automatically, or pause for
   the person as today?
