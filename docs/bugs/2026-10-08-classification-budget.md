# Builder-failure classification spent the run's stuck-investigation budget (#686)

Since #660, a Builder failure whose cause the evidence does not show goes to the Investigator
to be classified before it is retried. Those calls were rows in `stuck_investigations`.
`autocode_builder_failure.queue` held the run once that history reached
`settings.stuck_investigation.max_calls_per_run` (3), whatever the rows were for.

A multi-turn conversation reached that count quickly. `discuss-then-design-then-build`
(profile `claude-tiers`, 2026-10-08, on #664's branch) stopped twice in the build turn at
`PAUSED_BUILDER_CLASSIFICATION`, "failure classification requires reconciled evidence or
operator action":
- `g58nxkui`: three classifications, two from the design turn and one from the build turn. All three
  were classified and acted on.
- `qq7uau0y`: two classifications and one rejected-output investigation.

Both oracles passed (22/23 and 23/23). The stop came from the count alone.

**Fix (owner's decision, 2026-10-08).** Classifications have their own budget per milestone:
- **The limit.** `autocode_builder_policy.classification_limit` is the number of failures the
  milestone's retry lane takes, the one it pauses on included: `ordinary_retries + 2`. So
  classification never stops a milestone before the retry policy would.
- **What counts.** A row counts toward the budget of the milestone it was queued for:
  `milestone_key`, the lane's own key, which includes the contract hash. So a later turn's
  new contract starts a fresh budget.
- **The stuck budget.** `autocode_stuck_job.intercept` no longer counts classification rows
  toward its three.
- **Off switch.** `max_calls_per_run = 0` still turns both off.
- **Saved rows.** Rows saved before this change name no milestone, so they count toward neither
  budget.
