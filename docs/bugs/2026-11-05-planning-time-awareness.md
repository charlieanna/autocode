# Planning did not see the run's remaining execution time

The terminal discovery experiment `20261105-004649-chat-16c5f0` stopped before
implementation. Its saved public usage reports show 1,196.1 seconds in an
interrupted Analyst attempt, 360.5 seconds in Requirements attempts invalidated
by source changes, and 1,424.7 seconds in the Planner and Plan Reviewer. These
are distinct causes; elapsed time alone does not establish unnecessary work.

The captured planning prompts included the planning-review call allowance but
neither the saved execution limits nor the run's active time already used.
`units.autoplanner.context` built its own packet instead of using the execution
packet that carried those limits. A model therefore could not see how much of
the overall budget its planning left for implementation and verification.

Planning now receives a dispatch-time snapshot: active seconds used, remaining
active seconds (clamped to zero), stage time limit, and the run's spending cap.
Missing or disabled limits remain unspecified. This is guidance only: the
existing controller still enforces limits, and the packet grants no approval,
budget extension, relaxed verification, or model change. The instructions ask
for proportionate inspection, batched independent reads, and a concise report
once the evidence supports the stage's decision.

The regression checks all five default planning stages, exact remaining time,
and unchanged state. It fails before the change. Unit cases cover absent,
disabled and exhausted limits. Six in-memory mutations of remaining-time
arithmetic, clamping, disabled-limit handling, and usage/limit fields were all
detected. Architecture, affected planning, and prompt/resource/identity checks
pass locally. Fifty golden prompt variants were inspected: only the new field
and instruction were added; other bytes were preserved. No Linux provider
qualification is claimed from these local checks.

A separate isolated `bugfix-iso-weeks` fixture trial uses the original selected
model routes with a $2 cap, 1,200 active seconds, and a 480-second stage limit.
Its brief names the wrong New Year week behavior without giving the solution.
The original run remains stopped. Faster live completion remains unverified
until the new run reaches completion and its independent oracle passes.
