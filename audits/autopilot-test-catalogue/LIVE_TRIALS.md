# T15 — Bounded live-delivery trials (LIVE-01..LIVE-06)

Executed 2026-09-24 under the user's explicit authorization: real model
**GLM 5.3** (`zai-coding-plan/glm-5.3` via the kilo provider) with reasoning
restricted to **low / high / max only** (planner max, reviewers high, builder
and requirements low). All work in disposable git repos under
`/tmp/autopilot-live/`; briefs and human reviews approved only through the
runner's explicit token/actions; every verdict below is the independent
harness oracle's, never the runner's own completion claim.

Command shape (per trial): `tools/autocode.py --provider kilocode --chat …
--*-model zai-coding-plan/glm-5.3 --*-reasoning-effort {low,max,high}`.

| ID | Verdict | Evidence |
|---|---|---|
| LIVE-01 greeting CLI | **PASS** | TASK_COMPLETE through 11 stages (incl. a planner challenge round and two report repairs). Independent FX01 oracle **12/12**: exact outputs/exit codes for no-arg, `Ada`, multiword, Unicode, two-arg usage; three deliverables present, stdlib only |
| LIVE-02 to-do persistence | **HONEST BLOCKER** | Behavior fully verified (add/list/complete; restart-stable IDs; unknown-ID nonzero leaving data intact; malformed JSON preserved; own suite 11/11). No README delivered. Run cannot close: **finding L1** below |
| LIVE-03 duplicate-request bugfix | **PASS** | TASK_COMPLETE; seeded regression extended, never weakened (git diff: additions only). Independent HTTP oracle **7/7**: one effect per operation id, saved-result replay on retry, 409 + no new effect on changed payload, distinct ids distinct attempts, concurrent identical posts single entry |
| LIVE-04 monitoring UI | **DEFERRED** | Requires the frozen FX04 design-reference fixture (absent); the 21-screen matrix is already a recorded scoped gap from T12. Not run, not counted |
| LIVE-05 C#→Go port | **HONEST BLOCKER** | Attempt budget exhausted on repeated schema-invalid astra_review/resolver reports (wrong finding citation, missing `require_id`, non-JSON finals) across inspected retries. Partial: `reference/Policy.cs` + `golden-cases.json` (all 8 vectors correct); Go implementation missing. No C# compiler exists, so parity would have been golden-vector-only regardless — labeled, not claimed |
| LIVE-06 parallel diamond | **HONEST BLOCKER** | Milestone A delivered and verified (`contract/schema.json` with the answered schema shape; `dependency_trace.json` with edges A→B, A→C, B→D, C→D). Milestone B blocked by **finding L2** below; builder's correct `server/handler.py` retained for inspection |

## Post-trial fixes (applied after user approval, same day)

- **L1 fixed:** `human_only_pending_validation` generalized to any number of
  human-review criteria (pending set == human set, technical criteria PASS
  with evidence). The LIVE-02 contract now completes: offline replay of the
  saved run accepts AC6 and AC8 and reaches completion_ready. Regression:
  `test_goals.test_multiple_human_criteria_can_each_be_reviewed_and_completed`.
- **L2 fixed:** a fresh single-milestone task's ownership now MERGES the named
  milestone's contract-declared paths into the decision's declared scope
  (widening only; unbounded declarations stay unbounded). The LIVE-06
  builder's contract-legal server work is no longer falsely out-of-scope, and
  the saved run's pathological assignment replays to the correct merged
  ownership. Regression:
  `test_goals.test_task_ownership_merges_the_named_milestone_paths`.
- L3 is model behavior, not code; no change.
- Full suite after both fixes: 868 tests, 8 failures — all eight in
  `tools.test_command_flow`, proven pre-existing (they fail identically with
  these fixes stashed; cause: schema tightening in commits b8bd10c/945ee3b
  vs the not-yet-updated `fake_command_tool.py` fixture, in files under
  active author edits).

## Live findings (as first recorded)

- **L1 — multi-human-review acceptance deadlock (LIVE-02).** A contract with
  two or more `human_review` criteria can never be accepted:
  `human_only_pending_validation` supports exactly one such criterion, and
  `approve_review` requires Sol-PASS per criterion while Sol correctly marks
  human-gated criteria NOT_VERIFIED. The runner requests a human review it
  must then refuse. Single-criterion contracts complete (offline REV-08).
  Decision needed: support N human criteria or reject such contracts at
  planning time.
- **L2 — assignment paths not cross-checked against the named milestone
  (LIVE-06).** The planner's decision-level `affected_paths` (milestone A's
  paths) were copied onto milestone B's task; the builder's correct
  server/handler.py was rejected as out-of-scope. `assign_task` validates
  acceptance_criteria against the named milestone but not affected_paths, so
  a planner slip becomes an ownership false positive with no CLI recovery.
- **L3 — model-quality observation (LIVE-05).** GLM 5.3 (high) did not
  reliably emit the strict review/resolver JSON schemas on a complex port
  task; the bounded repair queue and budget guard stopped the loop honestly.
  Not a code defect; relevant to choosing effort/roles for review stages.

## Invariants observed working live (previously verified offline)

DAG-10 (planner dropping unanswered questions → output rejected, LIVE-06),
PAR-03 (ownership rejection with edits retained, LIVE-06), one-action-per-
invocation and displayed-token approval gates (LIVE-01/02/05/06), bounded
report repair with safe exhaustion (LIVE-05), human-review artifact binding
requiring the current validated artifact (LIVE-02), and honest
agreed-limitation reporting in every Sol verdict.

Bundles: `.tmp-autopilot-testkit/artifacts/LIVE-{1..6}/01/` (states, oracle
results, findings). Products preserved under `/tmp/autopilot-live/` and
committed in their own repos.
