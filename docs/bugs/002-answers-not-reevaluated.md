# Bug 002: Answers recorded in state but planner doesn't re-evaluate "NOT READY" assessment

**Severity:** Medium
**Status:** Fixed and verified (2026-09-28). Closed as #25 via #106; reproduced fixed on a
fake-provider run at `ffbbfa0` (master through `332c318`): after `--answer "Q1=CLI: local use"`
with the issued AutoResolver request/token, the saved state has 0 pending questions and 0
`open_blocking_questions`, status `RUNNING` with the Planner re-planning, and nothing still
claims "NOT READY".
**Regression test:** `tests/test_planning.py::JointFlow::test_bug002_regression_recorded_answers_re_evaluate_readiness`
— answers a question through the real CLI and asserts the re-evaluation: pending questions
cleared, contract re-sealed with no blocking questions, `discovery_summary` reset, and the
next resume runs planning to `AWAITING_GOAL_APPROVAL`.
**Found:** 2026-09-25 stress test on bounded-repair-tasks feature
**Run:** `20260925-021232-add-bounded-repair-task-generation-from-findings-34320863`

## Summary

After submitting `--answer` responses for the planner's open questions, the answers
are correctly persisted to `state.json` (with provenance, timestamps, and
`actor: user_cli`), but the planner's cached `discovery_summary` still says
"NOT READY to plan implementation" and lists the questions as open.

## Reproduction

1. Requirements gatherer produces a contract with 3 open questions
2. System enters `WAITING_FOR_USER` / `DISCOVERING`
3. Submit answers via `autocode --answer "qid=text" --no-chat`
4. Answers appear in `state.json` under `answers` with full provenance
5. `pending_questions` correctly shows 0 items
6. But `discovery_summary` still says "NOT READY" and lists the questions as open
7. Goal remains in `draft` approval status

## Evidence

From `state.json` after answer submission:
```
"answers": { 3 items, all actor=user_cli }
"pending_questions": [0 items]
"discovery_summary": "...leaves three decision questions open..."
```

## Expected behavior

When answers are recorded, the planner should re-evaluate readiness and either:
- Update the contract to reflect resolved questions
- Proceed to planning if all blockers are resolved
- At minimum, update the discovery summary

## Suggested fix

Trigger a contract revision or planner re-evaluation when `--answer` events
resolve pending questions. The answers are already persisted; the planner just
doesn't look at them until a new stage invocation.

## Related

- Bug 001 (tool loop) may compound this — the planner re-runs but doesn't pick up answers
