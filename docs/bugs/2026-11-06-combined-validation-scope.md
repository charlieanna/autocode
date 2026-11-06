# Combined validation must override serial model notes

The live LedgerLab B/C batch repeatedly stalled because a Completion Reviewer
assigned a combined validate task whose prose said to validate B first and C in
a separate following task. The runner correctly retained B/C membership and
AC2/AC3, and refused incomplete acceptance. Nevertheless, successive real Testers
followed the narrower prose and left C unverified. The latest repair admitted an
honest FAIL and queued missing-test corrections; it did not validate C.

The batch instruction now explicitly makes the runner's member/criterion
inventory authoritative for independent validation, including when the task's
objective, requirements, validation plan or an earlier report describes a serial
review. The primary milestone ID does not narrow the batch. Concrete checks,
constraints and required behavior remain intact. Missing evidence stays FAIL or
NOT_VERIFIED; no gate, permission, approval or saved task is changed.

The new actual-handoff regression supplies contradictory serial notes and checks
that all members and criteria, all original notes and the precedence rule reach
the Tester, checkpoint and Completion Reviewer without mutating saved state.
All three stage subcases fail on unchanged master ba666d462. This verifies the
handoff contract, not a guarantee that a model will obey it; the ongoing live run
is the remaining model-behavior qualification.

Supported Linux changed selection: 330 tests across 17 modules in 78 seconds.
Sixteen modules passed, including all 43 checkpoint tests and the three public
CLI graph/citation-repair tests. The only initial failure was four expected
prompt golden hash changes. Rendering comparison proved that each changed by
only this paragraph; only those four entries were refreshed. All nine prompt
rendering tests then passed on Linux (0.419 seconds). Ruff check/format and
`git diff --check` pass. The selector keeps checkpoint and public graph coverage
even when those consumers are marked slow.

Evidence remains under `.scenario-runs/orchestrator-heavy-live-20261105/`:
`scope-base-handoff`, `scope-candidate-changed`, `scope-prompt-golden`,
`scope-reviewed-rendering.diff` and `scope-build-identity.json`. The earlier
failed golden run remains recorded. The live run has not completed the remaining
milestones and has not yet qualified this new handoff.
