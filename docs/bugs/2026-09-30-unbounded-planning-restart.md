# A deferred goal approval restarts planning with no bound

Found 2026-09-30 while building adaptive planning (docs/adaptive-planning.md). **Fixed**
2026-09-30: `autocode_run_records.normalize_human_boundary` now allows
`MAX_DEFERRED_APPROVAL_RESTARTS` (2) restarts per deferral reason since the user's last input, then
pauses with `PAUSED_APPROVAL_DEFERRED` (tests/test_approval_deferral.py). With the original trigger
re-created, the scripted run stopped after 3 reviews instead of looping.

When AutoResolver defers a `goal_approval` request
(`autocode_resolver_human._decision`, for example "Final planning evidence must
match the current source before approval"), `autocode_run_records` restarts
planning with `planning.start(state)`. `start` archives the planning record and
creates a new one with `astra_calls: 0`, so the plan-review allowance
(`review_call_limit`, default 2) starts over. If the next final review is
deferred for the same reason, planning restarts again. Nothing counts restarts,
so a condition that keeps deferring approval keeps spending review calls with no
limit and no pause for a person.

Reproduced with the scripted provider before the adaptive gate was fixed. The
approval gate accepted only `astra_finalize` as final evidence, so every early
approval was deferred. One run made 559 plan reviews in ten minutes (contract
revision 561, `state.json` 9 MB) before it was stopped by hand. On master the
same loop needs a deferral that repeats: the source revision moving between the
final review and approval, or a final token that never matches.

The fix counts restarts in `resolver.deferred_approval_restarts`, keyed by task, deferral reason and
the number of user-authored events (actor `user`, `user_cli`, or `user_intervention` for feedback queued
while the run worked), so new user input renews the
allowance; runner bookkeeping that also lands in `user_events` (recovery receipts, review reroutes)
does not. It pauses with a status
of its own, not `PAUSED_PLANNING_BUDGET`, because nothing about the review allowance is wrong, and it
does not leave the run at `RESOLVER_PENDING`, whose message ("AutoResolver is evaluating an internal
decision") would not be true here. The pause prepares a fresh planning cycle first, so
`--resume-paused` runs exactly one more cycle instead of heading for execution without an approved
plan.
