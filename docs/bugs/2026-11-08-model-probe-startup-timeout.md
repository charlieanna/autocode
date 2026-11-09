# Record model-probe startup timeouts before provider launch

The live overhead qualification reached one successful Requirements stage, then
stopped during Kilo startup. Its bounded resume timed out in `kilo debug config`
after 30 seconds. The access-probe transport only classified a Kilo effort
refusal; `subprocess.TimeoutExpired` escaped through the controller instead of
becoming a recorded setup pause. The run was left at `PAUSED_CONTROLLER_EXIT`.

The retained run is
`20261109-025213-pager-page-count-11-5-returns-2-but-eleven-items-4e3c33a5`;
the public bundle manifest is
`a585343de77741174431b922b78f787334bf660bd50cf22470b77681bd083ff0`.
See [the original qualification](2026-11-08-small-task-model-overhead.md).

Catch this timeout only around the access transport's command preparation and
return the existing `PAUSED_TRANSPORT_UNVERIFIED` failure. The message identifies
setup failure before any model request, and preserves the exception cause. The
existing attempt recorder retains the elapsed time and zero model cost for the
unlaunched attempt. It does not publish an access success receipt or retry.
Timeouts after command preparation keep their existing handling. Kilo policy
discovery, its timeouts, permissions, stage routes and verification gates are
unchanged.

Verification:

- The in-process public CLI regression fails on master `54930f9a0` with the
  retained `TimeoutExpired` shape (1.757 seconds), then passes with the fix
  (1.779 seconds). It checks the public pause, one retained 30-second attempt,
  zero model cost, no model child, no ordinary stage, no success receipt and no
  automatic retry. Time is simulated; the test never waits 30 seconds.
- All five transport preparation tests pass, including unchanged effort
  refusal and a timeout after preparation that cannot be labeled unlaunched.
- The CI mapping test preserves this indirect public consumer even when it is
  marked slow. A stdlib trace observed all five added executable timeout-handler
  lines; the mapping was exercised by selection. Six targeted mutations were
  detected, covering the timeout class, pause status, message, exception cause,
  raised failure and consumer mapping.
- Ruff lint and format checks pass over `tools/`, `tests/` and `scenarios/`.

This corrects the escaped failure, not the cause of slow Kilo startup. Startup
qualification is separate from this fix; no additional live model calls or paid
baseline were run. End-to-end delivery and comparative live cost remain
unverified. The independent master architecture failure is fixed in PR #2979.
