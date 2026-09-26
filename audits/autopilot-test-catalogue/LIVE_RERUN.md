# T16 — Baseline diff: live re-trials vs the 2026-09-24 record

Executed 2026-09-25 under the user's explicit authorization, using the
live-trial harness built in this session (`tools/live_trial.py`,
`tools/live_profiles.py`, `tools/live_scenarios.py`). Verdicts below come from
the harness's independent oracles — never the runner's own completion claim —
except where noted as an operator-closed run.

Re-trials cover the three scenarios that produced findings L1/L2/L3.
LIVE-03 (bug fix) and LIVE-04 (deferred, missing FX04 fixture) were not re-run.

## Models

| Profile | Builder | Validator / Completion | Planner | Reviewer / Resolver | Provider |
|---|---|---|---|---|---|
| `glm53` (baseline + LIVE-01/02/05) | `zai-coding-plan/glm-5.3` | `zai-coding-plan/glm-5.3` | `zai-coding-plan/glm-5.3` | `zai-coding-plan/glm-5.3` | kilocode |
| `glm53-mimo` (LIVE-06) | `xiaomi-token-plan-sgp/mimo-v2.6-pro` | `zai-coding-plan/glm-5.3` | `zai-coding-plan/glm-5.3` | `xiaomi-token-plan-sgp/mimo-v2.6-pro` | opencode |

`glm53` reproduces the 2026-09-24 route. `glm53-mimo` was required for LIVE-06
because `dispatch.enforce_cross_model_verification` refuses to let a model grade
its own work (see Finding F9). Effort: planner high/max, reviewers high,
builder/requirements low/medium.

## Results

| ID | Task | Baseline (2026-09-24) | Re-trial (2026-09-25) | Delta |
|---|---|---|---|---|
| LIVE-01 | Greeting CLI | **PASS** · FX01 12/12 | **PASS** · FX01 12/12 · TASK_COMPLETE (16 stages) | unchanged |
| LIVE-02 | To-do persistence | **HONEST BLOCKER** · behavior verified · finding **L1** | **PASS** · FX02 6/6 · TASK_COMPLETE (22 stages) · **L1 confirmed fixed** | **resolved** |
| LIVE-03 | Duplicate-request bug fix | **PASS** · oracle 7/7 | *not re-run* | — |
| LIVE-04 | Monitoring UI | **DEFERRED** · FX04 fixture absent | *not re-run* | — |
| LIVE-05 | C#→Go policy port | **HONEST BLOCKER** · finding **L3** · Go missing | **PASS** · FX05 11/11 · TASK_COMPLETE (15 stages) · **L3 did not recur** | **resolved (single run)** |
| LIVE-06 | Parallel diamond | **HONEST BLOCKER** · finding **L2** · B blocked | **PASS** · FX06 6/6 · TASK_COMPLETE (24 stages) · **L2 confirmed fixed** | **resolved** |

**Score: 3/3 re-trials PASS. 0 honest blockers. 0 deferred.**

## Finding-by-finding

### L1 — multi-human-review acceptance deadlock → **fixed and confirmed live**

Baseline: a contract with two or more `human_review` criteria could never close.
`human_only_pending_validation` supported exactly one; `approve_review` required
a Sol-PASS that Sol correctly refused for human-gated criteria.

Re-trial surface: LIVE-02's contract was given **two** human-review criteria
(AC9 README docs, AC14 README cross-check) via the documented `--edit-goal`
operator path, both on milestone M2 so they went pending together.

```
missing_human: ['AC9', 'AC14']          ← two pending simultaneously
→ --approve-review AC9   → missing_human: ['AC14']
→ --approve-review AC14  → missing_human: []
                           completion_ready: True
→ TASK_COMPLETE
```

Offline regression: `test_goals.test_multiple_human_criteria_can_each_be_reviewed_and_completed`.
Fix commit: `28c4962`.

### L2 — assignment paths not cross-checked against the milestone → **fixed and confirmed live**

Baseline: the planner's decision-level `affected_paths` (milestone A's paths)
were copied verbatim onto milestone B's task, so the Builder's contract-legal
`server/handler.py` was rejected as out-of-scope with no CLI recovery.

Re-trial surface: LIVE-06's four-milestone diamond with disjoint ownership
(A `contract/`+`dependency_trace.json`, B `server/`, C `client/`, D `integration/`).

Decisive live observation — milestone B's task carried the fix's **widening-only
merge**:

```
current_task: B ['contract/schema.json', 'dependency_trace.json', 'server/']
```

The decision listed A's paths; the milestone's declared `server/` was merged in.
Pre-fix the task would have had only A's paths. All four milestones'
owned paths landed with **zero** `PAUSED_ORCHESTRATOR_OWNERSHIP` events across
24 stages.

Offline regression: `test_goals.test_task_ownership_merges_the_named_milestone_paths`.
Fix commit: `28c4962`.

### L3 — model-quality observation on review/resolver JSON → **did not recur (single run)**

Baseline: GLM 5.3 (high) did not reliably emit the strict review/resolver JSON
schemas (wrong finding citation, missing `require_id`, non-JSON finals). Budget
exhausted; Go implementation missing.

Re-trial: the same model, same task class, same reviewer effort. `astra_review`
and `resolver` were **both accepted first try**. The run produced a
**model-authored** `TASK_COMPLETE` (no `--accept-completion`) and delivered a
working Go port scoring **FX05 11/11** on the eight golden vectors plus build
and reference-presence checks.

The L3 *class* (sloppy JSON) appeared **once**, on `glm_revise_report_repair`
("final message is not a JSON report"), and was recovered by the bounded
report-repair path. It never exhausted the budget and never reached the review
stages where it killed the baseline run.

**Honest caveat:** L3 was always stochastic ("did not *reliably* emit"). A single
pass is not proof of reliability — it is evidence the failure is not
deterministic for this model/task pairing. A budgeted multi-run trial would be
needed to claim a rate.

## Oracle independence

Each re-trial is scored by an oracle that never imports production matchers and
never trusts the model's own tests:

| Oracle | Checks | What it enforces |
|---|---|---|
| FX01 | 12 | exact exit codes + outputs for 5 invocations, 3 deliverables, stdlib-only imports |
| FX02 | 6 | add/list/complete, restart-stable IDs, unknown-id byte-identical store, malformed store byte-identical |
| FX05 | 11 | `go build`, 8 golden vectors, reference present — **golden-vector-only, no C# execution claimed** |
| FX06 | 6 | four diamond edges, every milestone's artifact at its declared path |

Each oracle was validated offline against a reference delivery (must pass) and a
deliberately broken delivery (must fail) before any live spend.

## Product findings from the re-trials

Discovered while driving the live runs; all fixed in this session with
regressions where the code path is unit-testable.

| ID | Symptom | Class | Fix |
|---|---|---|---|
| **F4** | `opencode models` took ~57s vs a 30s timeout → run died before any agent launched | timeout too short | `providers/opencode.py:108` timeout 30 → 180 |
| **F5** | `opencode auth list` timeout 15s — same class on the billing-route check | timeout too short | `providers/opencode.py:138` timeout 15 → 180 |
| **F6** | Profile mapping omitted `--sol-model`, so `sol` silently kept `openai/gpt-5.6-sol` and tripped the OAuth guard | harness gap | `live_profiles.py` role map gained `validator` |
| **F7** | `--accept-completion` was **unreachable on every run with a task**: its probe omitted `task_id`, which `execution_guard` requires | **product bug** | `autocode.py:1669` probe now carries the current task id + `test_goals.test_accept_completion_probe_carries_the_current_task_identity` |
| **F8** | Native Codex joint planning requires bare GPT model names; the fixture profile passed none and died before any agent launched | harness gap | fixture profile pins `gpt-5.6-sol`/`gpt-5.6-terra` |
| **F9** | `PAUSED_CROSS_MODEL` refused an all-GLM profile: a model may not grade its own work (`enforce_cross_model_verification`) | product guard, working as designed | not a bug — see below |

### F7 is the significant one

The documented operator escape hatch for "gates all verify but the model cannot
produce the final report in the required echo format" simply **never worked**:
`execution_guard` rejects a result whose `task_id` is absent while a task is
assigned, and `accept_completion` built its probe without one. Found only
because a real model actually reached the completion gate.

### F9 is a guard, not a defect

`dispatch.enforce_cross_model_verification` requires producer and verifier to
sit in different model families (`zai-coding-plan/*` → `glm`,
`xiaomi-token-plan-sgp/*` → `mimo`) for three pairs: Builder/Validator,
Builder/Completion, Planner/Plan Reviewer. The 2026-09-24 trials ran everything
on GLM 5.3 — meaning that record predates this guard, or the guard was not
active on that path. The re-trials use `glm53-mimo` where independence is
required. Worth noting the baseline and the re-trial are **not** identical
routes for LIVE-06.

## Notable runner behavior across the re-trials

Every honest-pause class fired at least once and behaved as documented:

| Pause | Where | Behavior |
|---|---|---|
| `PAUSED_PLANNING_BUDGET` | LIVE-06 ×2 | 2-review-call limit held; explicit `--feedback` starts a new cycle |
| `PAUSED_PROVIDER_UNCERTAIN` | LIVE-02, LIVE-06 | interrupted stage never auto-replayed; explicit `--abandon-stage` |
| `PAUSED_STAGE_ABANDONED` | LIVE-02, LIVE-06 | partial work retained; explicit resume |
| `PAUSED_CROSS_MODEL` | LIVE-06 | refused dispatch until independence satisfied |
| `PAUSED_INTERRUPTED` | LIVE-02, LIVE-06 | work retained |
| report repair | all four | bounded recovery; 4 repairs on LIVE-05 alone, none exhausted |

## What this does and does not establish

**Established (live, oracle-scored):**
- L1's fix holds with N=2 human-review criteria on a real model run
- L2's fix holds on a real diamond with disjoint ownership
- The L3 failure mode did not occur in one full port run
- The runner's honest-pause and bounded-recovery invariants hold under real models
- Two real product defects (F4/F5 class, F7) were found only by live runs

**Not established:**
- A rate for L3. One pass ≠ reliability. Needs a budgeted multi-run trial.
- Executed-C# parity for LIVE-05. No C# compiler exists here; parity is
  **golden-vector-only**, labeled, not claimed.
- LIVE-03 and LIVE-04 under this harness.
- OpenCode-vs-kilocode route equivalence. LIVE-01/02/05 used kilocode, LIVE-06
  used opencode for the mixed profile.

## Evidence

| Trial | Bundle | Key artifact |
|---|---|---|
| LIVE-01 | `.tmp-autopilot-testkit/artifacts/LIVE-01/35/` | `live-trial.json` (FX01 12 rows) |
| LIVE-02 | `.tmp-autopilot-testkit/artifacts/LIVE-02/03/` | `live-trial.json` (FX02 6 + L1 assertions) |
| LIVE-05 | `.tmp-autopilot-testkit/artifacts/LIVE-05/02/` | `live-trial.json` (FX05 11 + L3 assertions) |
| LIVE-06 | `.tmp-autopilot-testkit/artifacts/LIVE-06/05/` | `live-trial.json` (FX06 6 + L2 assertions) |

Products preserved under `/tmp/lt-glm/`, `/tmp/lt-02/`, `/tmp/lt-05/`, `/tmp/lt-06b/`.
Harness sources: `tools/live_trial.py`, `tools/live_profiles.py`,
`tools/live_scenarios.py`, `tools/live_fixture_provider.py`,
`tools/test_live_trial.py`. Regression suite: `tools.test_live_trial`,
`tools.test_goals`, `tools.test_cross_model` — green.

Baseline record: [LIVE_TRIALS.md](LIVE_TRIALS.md) (T15, 2026-09-24).
