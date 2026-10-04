# OpenCode reviewer creates and removes a test before returning

## Observed failure

A local Luna/Haiku bug-fix run's Haiku Validator used Bash to create
`internal/utils/validate_probe_test.go`, execute it, and remove it. OpenCode's
edit permission was denied, but its Bash permission allowed the commands. The
runner's before/after source comparison found a clean final tree and missed the
transient modification. The native step snapshot changed and then returned to
its starting value. The same trial also wrote probe files outside the workspace;
those writes are outside the scope of this fix.

This is specific to the documented OpenCode permission boundary: tool denies
and final source comparisons do not provide an OS filesystem sandbox.

## Fix

Before accepting a read-only report, compare native step snapshot identities
within each OpenCode session. Any recorded transition rejects the review with
`PAUSED_STALE_VALIDATION`, retaining evidence and requiring inspection and fresh
validation. This includes saved-stage recovery, report-only Builder repairs and
the original review evidence used by a report repair. Ordinary Builder changes
remain allowed. Model text and tool output are not snapshot evidence.

Native snapshot preparation appends local Git exclusions for the same runtime
directories and Python caches that the runner already omits from its source
snapshot. It preserves other local rules, handles missing final newlines, and is
idempotent. A preparation failure stops before provider launch. This keeps
legitimate capture receipts from being mistaken for a reviewer source change.

## Test-first proof

The report-loading regressions accepted both a restored reviewer write and a
Builder report-repair write before the fix, failing their expected-pause
assertions. A separate regression exposed reuse of a changed original review by
a clean report repair before that route was guarded. The tests pass with the fix.

A CLI fixture physically creates and removes a Validator probe, emits the native
snapshot transition and asserts the task pauses with its source and logs retained
instead of completing or queuing a report-only repair. The recorded live Haiku
event stream is also rejected by the new guard, without modifying that evidence.

Qualification after rebasing onto master `25dec92`:

- The same restored-write regression reproduced acceptance on unchanged master
  in three of three attempts; the fixed runtime rejected it in three of three.
  This reproduces the guard failure deterministically, not the model's decision
  to write a file on every live run.
- All 132 focused tests passed: policy/report/recovery/exclusions (16), native
  OpenCode transport (32), output-limit recovery (15), report repair (65) and
  architecture (4), including the new CLI rejection test.
- The affected-suite gate ran 849 tests in 59 modules. Two modules failed:
  pilot preparation hit the joint-planning authentication check, and a browser
  test lacked `agent-browser` (`spawnSync agent-browser ENOENT`). Both failures
  were reproduced separately on unchanged master; the other 57 modules passed.
- The offline scenario run was blocked at the same joint-planning authentication
  check. The greeting scenario reproduced that startup failure on unchanged
  master. The prior base's offline scenarios passed; that is not evidence of a
  passing scenario run after this rebase.
- No live models, Jira writes, deployments or installed-user-tool changes were
  used for this qualification; package installs were confined to temporary venvs.

The earlier full-suite attempt preceded the final fixture/compatibility fixes;
it is not evidence of a passing full suite for the final patch.

## Limits

This detects recorded workspace drift; it does not prevent writes. It does not
cover external paths, ignored files, changes made and reverted between native
snapshots, or a transport with no snapshots. Strong prevention requires an OS
sandbox. No claim is made that the earlier live task ran with this new guard or
would complete under it; its transient-write validation would now be rejected.
