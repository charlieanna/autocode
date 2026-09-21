# Activity-aware timeouts — 2026-09-20

Autocode now supervises provider inactivity, tool execution and total stage runtime
separately. Healthy observed activity can continue beyond the former five-minute
new-run hard cap. Existing saved hard caps, model selections and approved contracts
are preserved.

## Behavior

- New defaults: 300 seconds of provider inactivity, 1,800 seconds per tracked tool,
  and no total stage cap. `--max-idle-seconds`, `--max-tool-seconds` and
  `--max-stage-seconds` each accept zero to disable their own limit.
- Native Codex/OpenCode events provide liveness observations. Duplicate starts,
  completions, repeated text and arbitrary log lines cannot renew deadlines. Tool
  output cannot renew a known tool's fixed deadline.
- Quiet tracked tools get their own deadline. Completion-only transports use a
  bounded interval for observed descendants, renewed only by a new tool completion.
  Explicit start events supersede this approximation.
- Event parsing, deadline enforcement, process sampling and durable status writes
  are separated. Idle, tool and hard deadlines still stop workers when event reads
  or process sampling block. Existing descendant cleanup and identity checks remain.
- Status captures activity, elapsed time, limits and an observation timestamp.
  Timeout records and recovery context retain the specific cause.
- Recovery retains partial source and logs, requires all tracked workers to stop,
  archives the original request once and does not replay it. Ordinary recovery
  returns to Astra; accepted implementation still goes to independent Sol review.
- Repeated timeouts without any accepted stage are bounded for every role by the
  existing no-progress limit. A valid accepted stage resets that consecutive count.
  Explicit resumption acknowledges its pause without deleting recovery history.
- Large JSON string values are projected out of the liveness parser with bounded
  memory while preserving validated structural completion events. String escapes,
  UTF-8 and document syntax remain checked; embedded output cannot forge events.
  Original evidence logs are never rewritten by this parser.

## Verification

Full offline regression suite: **323 tests passed in 200.776 seconds**. See
`full-suite.log`. All Python source files stayed unchanged throughout this final
run; their fingerprints are recorded in `suite-source-manifest.json`.

Focused coverage includes 25 deterministic event/parser tests, 16 real process
supervision tests and 16 runtime/settings/recovery tests. The runtime tests include
real offline provider subprocesses demonstrating quiet-tool completion followed by
Sol, and a stalled writer whose source is retained through recovery.

The installed wheel passed three complete CLI workflows in 25.543 seconds: two
milestones with independent validation, bounded replanning after failed checks,
and safe legacy milestone activation. See `wheel-tests.log`. Its changed runtime
modules were compared byte-for-byte with the checkout. Concurrent planning and
resume-status fixes in the shared checkout were retained before this final build.

The first broad suite found one outdated unlimited-iterations fixture: it expected
an exact settings object without the newly added supervision fields. The fixture
now explicitly configures nondefault inactivity/tool limits and verifies that
unlimited iterations preserves them, as it preserves all other saved limits.
The compatibility and runtime subset then passed all 20 tests in 4.185 seconds.

All providers used for validation were local fixtures. No hosted inference or
course source changes were part of this fix or its tests.

## Deployment and limits

The user's editable `/Users/ankurkothari/.local/bin/autocode` installation loads
this checkout; its help exposes the new options. Existing supervisor processes
keep their already-loaded code until restarted. No live course run was interrupted
or relaunched to install this change. Saved runs gain missing inactivity/tool
defaults at their next configured launch; their existing total stage caps remain.

Liveness is not proof of useful work or correctness. Milestone acceptance still
requires independent evidence. With no explicit tool starts, process metadata
cannot identify individual concurrent tools or distinguish persistent wrappers;
the fallback is labeled as inferred. An explicit hard stage cap remains available.
Setting the no-progress limit to zero also disables the consecutive-timeout cap.
Other saved limits and approval gates remain controlling on explicit resume.

Source fingerprints are in `source-manifest.json`; the wheel is
`dist/autocode_supervisor-0.5.4-py3-none-any.whl`.
