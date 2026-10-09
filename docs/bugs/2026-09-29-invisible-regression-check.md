# A resumed run looked paused while testing its candidate

The Planner backend was resumed to validation. Before launching its Validator,
the controller ran the required baseline and candidate regression suites. The
resume existed only in memory until the next model launch, so `--status` and
the dashboard kept showing `PAUSED_INTERVENTION` with no active worker for the
entire test run. The dependent dashboard task correctly waited for accepted
delivery, but the status made that wait look like another abandoned run.

Regression checks now persist their activity before starting a suite, including
the controller's process identity, a plain-language phase and the output path.
Status can distinguish a live local check from a dead controller and from a
provider attempt. Completion and exceptions clear the activity, preserving the
proof and the queued Validator. The change does not alter contracts, model
routes, test selection or acceptance requirements.

Coverage calls the real status CLI during stubbed baseline and candidate checks,
checks cleanup on errors and PID reuse, and exercises the dashboard projection.
