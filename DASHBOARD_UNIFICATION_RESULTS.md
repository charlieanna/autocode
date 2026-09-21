# Local dashboard unification — 2026-09-21

## Delivered and activated

One packaged Autocode dashboard serves `http://127.0.0.1:5191/`.
It replaces the separate read-only monitor on 5191 and the idle integrated
dashboard instance on 8767. Neither the dashboard nor the task was deployed
remotely. No provider/model, authentication, reasoning, runner limit, or goal
configuration changed.

The existing task remains
`20260918-113408-complete-the-adaptive-dsa-mapping-and-validation`.
At activation it remained RUNNING, iteration 28, Terra-role implementation by
`zai-coding-plan/glm-5.3` with max reasoning. The same worker PID 94855 and
09:19:35 process start identity survived the dashboard replacement. Astra is
`gpt-6-astra` high; Sol is `gpt-5.6-sol` high. These are observations at activation,
not a promise that the task will never subsequently pause.

The actual coverage artifact reads 440 / 12,302, modified
2026-09-19 00:03:25 UTC (September 18 local time). The dashboard deliberately shows
this as an artifact snapshot, not live completion, teaching approval, or mastery.

## Changes

- `tools/dashboard/dashboard_chat.py`: package-safe task-detail imports and an
  optional project-metric projection. The former script-only import caused the
  installed CLI's `/api/run` request to terminate with an empty reply.
- `tools/dashboard/agent_console.py`: unexpected GET view exceptions return a
  failed JSON response instead of closing the socket; no state/exception body
  or secrets are included in the generic error. Server logs name the error class.
- `tools/dashboard/dashboard_app.js`, `dashboard.html`, `dashboard.css`: the
  **Now** view incorporates the monitor's status hierarchy, saved role models,
  objective, filtered activity, findings, coverage, and acceptance summary.
  Project navigation, plan approval, conversation, changes, checks, history,
  and native runner controls remain. Focus view and light/dark themes are local
  preferences, not separate applications.
- `tools/dashboard/dashboard_monitor.py`: saved unresolved findings and verdict,
  plus allowlisted numeric test totals without raw commands, logs, or reasoning.
- `tools/dashboard/dashboard_metrics.py`: explicit run-scoped, workspace-contained
  JSON counters; invalid data stays unknown. No subprocess/provider calls, dynamic
  imports, or task-state writes. Large-artifact projections are cached, not copied
  into a second workflow state.
- `README.md`: operation, limits, metric format, and test commands.
- `tools/dashboard/tests/test_unified_dashboard.py`, `test_status_ui.js`,
  `unified_browser_fixture.py`: packaged-launch regression, error recovery,
  path/counter guards, stale-state handling, and a disposable browser fixture.
- Local-only course configuration:
  `/Users/ankurkothari/Documents/workspace/idlecampus/.autocode/dashboard.json`.
  It points at the existing completion_coverage artifact for this exact run only.
  Course content and learner records were not edited.

## Executed validation

1. `python3 -m unittest discover -s tools/dashboard/tests -p 'test_*.py'`:
   **159 passed**. Includes both package and direct-source imports, loopback HTTP,
   error recovery, security boundaries, archive and approval controls.
2. All ten `tools/dashboard/tests/test_*.js` programs: **10 passed**.
3. `python3 -m unittest tools.test_dashboard_integration tools.test_dashboard_consumer`:
   **5 passed**, using the existing real runner with fake providers and temporary
   projects. No production model request was launched for these tests.
4. The new packaged-entry regression was also run against the unpatched checkout:
   **failed as expected** with missing `dashboard_monitor`. The patched code passes.
5. Real browser fixture: checked dark and light layouts, focus mode, actual task
   rendering, a deliberate task-detail HTTP failure, preserved last-known details,
   disabled controls, and automatic recovery. The fixture cannot execute agents.
6. Real installed dashboard: existing run's `/api/run` now succeeds; browser shows
   verified worker, correct models, review failures, and timestamped actual coverage.
   Real task mutation controls were **not exercised** during this read-only check.
7. `git diff --check`: passed. `lsof` confirmed loopback-only binding.

An additional final browser recheck after the numeric-summary refinement was
denied by the browser permission guard because it could expose repository-derived
task details to that connector. No alternate browser/network path was used to
bypass the denial. Earlier real-browser observations and the automated tests above
stand; another final visual inspection requires explicit user permission.

Initial sandboxed runs could not bind localhost and a runner fixture could not
create its disposable run. Both suites were rerun with the required local process
permissions and passed. A new fixture's incorrect mock return shape was corrected
before acceptance. None of these results prove the unfinished course mapping is
complete or educationally effective.

## Local operation and rollback

The installed editable CLI resolves to this repository's dashboard package.
Launch (only if no dashboard is already listening):

```sh
autocode-dashboard --port 5191
```

Logs for this activation:
`/Users/ankurkothari/Documents/workspace/autocode/.autocode/dashboard-unification-20260921/server.log`.
The activated server was launched in its own process session, independent of the
terminal. This is not a new login/startup service; after a machine restart, use
the command above. Autocode task checkpoint/resume behavior was not changed.

Pre-change dashboard source and README are preserved in
`/Users/ankurkothari/Documents/workspace/autocode/.autocode/dashboard-unification-20260921/`.
To roll back presentation, first verify the **dashboard** PID with
`lsof -nP -iTCP:5191 -sTCP:LISTEN`, ensure it has no active actions/conversations,
and stop that PID only. Do not signal the runner or its workers. From the Autocode
repository, restore the changed original files:

```sh
cp .autocode/dashboard-unification-20260921/README.md README.md
cp .autocode/dashboard-unification-20260921/dashboard/agent_console.py \
   .autocode/dashboard-unification-20260921/dashboard/dashboard_chat.py \
   .autocode/dashboard-unification-20260921/dashboard/dashboard_monitor.py \
   .autocode/dashboard-unification-20260921/dashboard/dashboard_app.js \
   .autocode/dashboard-unification-20260921/dashboard/dashboard.html \
   .autocode/dashboard-unification-20260921/dashboard/dashboard.css tools/dashboard/
autocode-dashboard --port 5191
```

Additional metric/test files can remain unreferenced. This restores the old package
import defect too; use rollback only when that tradeoff is understood. The former
read-only monitor source remains intact in the course workspace at
`.autocode/status-page/`; no backup or original monitor source was deleted.
No GitHub push was performed.
