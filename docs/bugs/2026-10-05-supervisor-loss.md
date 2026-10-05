# A provider outlived its supervisor (#454)

A live qualification attempt lost its supervisor before writing a result: the
scenario harness and the AutoCode CLI were gone, an OpenCode provider was still
running with parent PID 1, and the saved run still said `RUNNING`. There was no
`result.json`.

**The root cause of the live incident is unproven.** The fake reproductions below
give the same signature only when the harness's process group gets SIGKILL, or
SIGHUP from a `killpg` or a terminal close. Which of these (or something else)
happened on the host is not established.

## Why a provider could outlive its controller

- Every bound on a provider's lifetime was a thread inside the controller: the
  stage cap, the idle and tool limits, process-tree cleanup and the interrupt
  handler. A controller that died uncleanly took them all with it.
- The provider runs in its own session (`start_new_session=True`), so a signal to
  the controller's process group or a terminal hangup never reached it.
- The controller had no SIGHUP handler, and nothing stopped an orphan afterwards.
  The inherited checkout lock only kept a second run out while the orphan lived.

## Reproduction matrix

Fake providers, no model spend. Scenario `greenfield-greeting-cli --fake
--max-stage-seconds 15`; a holder blocked the first Requirements stage for 50 s and
the fault landed 1.5 s into it. The OpenCode row uses the offline fixture in its
`TIMEOUT_ONCE` form (a 60 s challenge) and a SIGKILL to the controller.

| Fault | Provider after the fault, before | After the stage keeper | Saved status after |
|---|---|---|---|
| 1. SIGKILL the controller | ran 48.7 s, to its natural end | stopped by the keeper in 0.1 s | `RUNNING`, `stale` true |
| 2. SIGTERM the controller | stopped in 0.1 s | unchanged | `PAUSED_INTERRUPTED` |
| 3. SIGKILL the harness only | bounded by the surviving CLI (13.5 s) | unchanged (harness-side, not in this fix) | the CLI continues unsupervised |
| 4a. SIGHUP to the harness's group | ran 48.8 s | stopped by the controller in 0.1 s | `PAUSED_INTERRUPTED` (signal `SIGHUP`) |
| 4b. Terminal close (pty master closed) | ran 48.7 s | stopped by the controller in 0.1 s | `PAUSED_INTERRUPTED` |
| 4c. SIGTERM to the harness's group | stopped in 0.1 s | unchanged | `PAUSED_INTERRUPTED` |
| 4d. SIGKILL to the harness's group | ran 48.8 s | stopped by the keeper in 0.1 s | `RUNNING`, `stale` true |
| 5. OpenCode, SIGKILL the controller | ran 59.1 s | stopped by the keeper within 0.3 s | `RUNNING`, `stale` true |

Times are seconds after the fault. In the kill rows the keeper saw the lifeline
close at once and wrote `<attempt>.supervision.json` (cause `supervisor_lost`,
outcome `stopped`) about 60 ms later. Rows 2, 3 and 4c are unchanged: none of them
left an orphaned provider.

## The fix so far

- `tools/autocode_stage_keeper.py`: a keeper per provider attempt, tied to the
  controller by a pipe, started by fork inversion so the provider is still the
  controller's direct child with the same pid, session and exit status. On end of
  file without a release it stops the identity-checked tree at once and records why.
  See [Process ownership](../task-run.md#process-ownership).
- `interruption_handler` turns SIGHUP into a clean interrupt like SIGTERM, unless
  SIGHUP was ignored on entry (`nohup`), and records `active_stage.interrupted`.
- `active_stage.pid` and `active_stage.supervision` are saved before supervision
  starts, which also shrinks the window in which status could not see the provider.

A report finished by an orphan is no longer possible once the keeper stops it, so
`test_finished_orphan_report_resumes_without_second_builder` now kills the keeper
too; adoption stays covered as the second line of defence.

## Still open (#454)

- Status still trusts the saved `RUNNING` and does not read the keeper's report.
- The harness writes nothing durable before `result.json`, grades an external
  interruption instead of marking it ungraded, and does not own the CLI's lifetime
  (fault 3: the CLI keeps running stages with no supervisor).
- Builder worker processes and runner check commands are not kept this way.
- The fault tests have run on Linux only; the incident host was macOS.
