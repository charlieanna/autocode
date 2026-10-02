# Recovery grant invalidated by resume bookkeeping

The private TEST Jira pilot accepted an exact `grant recovery 1` reply, but no provider launched. A subsequent resume republished the exhausted-recovery question.

`run_actions.handle` reset the resolver evaluation epoch before validating the grant. When prior resolver evaluations existed, that reset appended a user event. The event changed the binding used by `resolver_human.current`, so the previously displayed request could no longer establish the timeout-exhaustion boundary.

Validate and record the explicit grant before resetting the resume epoch. Preserve both the original request receipt and the lifetime evaluation audit; do not grant an allowance on ordinary resume. A focused test with a published timeout-exhaustion request and recorded resolver evaluations failed before the reorder.

The TaskRun client also mistook CLI rejection for an accepted pause when progress text preceded `Input rejected:`. Check each output line so the driver cannot report the rejection as an accepted action.
