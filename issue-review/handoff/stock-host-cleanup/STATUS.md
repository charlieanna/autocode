# Stock extension-host cleanup (#4012)

Branch `codex/stock-host-owned-cleanup`, based on master `73400c323`.

The actual Linux editor log for #4022 at head `9708bd9a3` recorded a live stock
`autocode_source_host.py` after the editor closed (PID 4092, birth 1795261376.59).
GitHub cleanup later named Python PID 4092. The latter lacks birth identity,
so it is supporting evidence rather than proof of the final process identity.
The old stock callback never stopped that host and removed its fixture even
when editor cleanup failed.

The fixture now selects its own registry, configuration and temporary/socket
location before setup, validates the returned registry, and uses the existing
shared all-steps cleanup. HOME and CODEX_HOME remain inherited in this host's
explicit fixture mode. Setup runs without the warm host. Cleanup releases held
readers, closes the owned editor (with its PID/birth fallback), observes wrapper
and real CLI child identities, then stops and checks only the private warm host.
A final read-only scan refuses clean completion when an unrecorded Python child
still references the owned fixture or cannot be inspected. It signals no PID.
Failed setup or cleanup preserves all fixture evidence. Both original actual
editor behavior tests and their assertions remain.

Review found and corrected a wrapper/child lifetime gap: a wrapper can exit
while its CLI child survives, including before the child identity is recorded.
The child record and final owned-path scan cover these separately. Existing
shared cleanup controls also require later cleanup to run after an earlier
cleanup fails, and prohibit stopping an inherited host after early setup failure.

Verification:

- The real stock before/after callback regression fails exact master 73400c323
  (no explicit host stop / removed failure evidence) and passes the branch.
  `/private/tmp/autocode-stock-host-master-regression.log` retains the negative.
- Eight focused JS cleanup controls passed, then the two affected stock
  callbacks passed after adding the unrecorded-child check. Four Python reader
  controls cover live/gone/reused/zombie identities, wrapper exit, unrecorded
  child and inspection refusal; they pass.
- `tools/run_suite.py --changed --jobs 4`: 343 tests in 16 modules passed before
  the final additional reader control. The final affected reader/scope selection
  passed 37 tests. `/private/tmp/autocode-stock-host-changed.log`.
- Extension unit suite: 801 passed, 22 skipped; typecheck passed. Final callback
  changes were then verified by their focused regression. Full Ruff check,
  format and mypy passed (993 runtime files).
- Owned offline public CLI setup with inherited homes reached the expected
  Planner HTTP 401 pause. Its returned environment has no HOME/CODEX_HOME
  overrides; the final read-only process scan found no fixture-owned Python.
  `/private/tmp/autocode-stock-host-precheck-4012.json` and `.log`.

Actual Linux VS Code Extension Host and exact-head ci-required remain pending.
Do not close #4012 or merge this branch until both have passed, the actual
stock cleanup diagnostic says stopped, and final job cleanup is inspected.
No live model call was made; no operator or unrelated workspace host was stopped.

Final outcome (2026-11-21): exact head `eee6d8eb63e9c1799aca180cbc9ee5a257836bc5`
passed `ci-required` and every selected job in run `44902500568`, including
advisory coverage. The actual Linux Extension Host passed all eight cases.
Its stock host reported `running:false`, `pid:null` on its private socket;
recorded/unrecorded reader checks passed and final GitHub cleanup reported no
terminated orphan process. Log `/private/tmp/autocode-4041-eee6-editor.log`.
PR #4041 merged as `e5df5caa7b588000bde9f6c0676da59f926cf2cf`; #4012 is closed.
PR comment `7062474227` records the exact merge evidence. This local status
append will be committed with the next required integration rather than changing
the qualified head merely to repeat its checks for documentation.
