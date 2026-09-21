# Enforced milestone checkpoints — 2026-09-20

New Autocode runs enforce independent milestone evidence before advancement. Saved
runs can explicitly adopt the same policy at a reconciled boundary or queue the
configuration while a provider owns the workspace lock.

## Implemented behavior

- Terra hands each completed bounded task to Sol, then Astra. Final-only and
  combined-review overrides cannot bypass an enabled milestone gate.
- Switching milestones requires passing evidence for the entire current milestone,
  matching goal/task/source identities, intact evidence, no blocking findings and
  required human reviews. Later unbuilt criteria can remain NOT_VERIFIED; the
  existing all-criterion completion gate remains mandatory.
- Three independent reviews without any newly passing criteria require a changed,
  evidence-backed REWORK. One automatic replan is allowed. Continued failure then
  pauses; changing source files or repeatedly requesting validation does not bypass it.
- A 5,400-second milestone budget blocks further implementation at stage boundaries;
  verification of existing work remains possible. Role durations, milestone budget
  use and recovery progress appear in CLI updates and read-only status.
- Queued upgrades preserve goals, models, artifacts and operator pause markers.
  Report-only repair completes under its original schema before an upgrade applies.
  A concurrent change to queued activation was retained: running loops continue to
  Sol without a new manual-resume gate; existing user/goal pauses remain controlling.

Per-stage timeout supervision was not redesigned in this change. Existing timeout,
subscription and permission settings remain controlling. The runner checks evidence
provenance and state transitions; Astra/Sol still judge whether an executed check
actually proves the criterion and whether a revised approach is meaningful.

## Verification

- Full offline source suite: **261 tests passed**, 361.923 seconds. See `validation.log`.
- Final targeted milestone/recovery suite: **19 tests passed**, 8.097 seconds. This
  includes the additional queued-repair, repeated-validation and budget/replan edge
  cases added after the broad suite was launched. See `targeted.log`.
- Temporary installed-wheel smoke: **3 tests passed**, 44.547 seconds: two complete
  milestones, repeated failed checks with one replan, and queued legacy migration.
  See `wheel-validation.log`. The wheel was rebuilt after the final focused fixes;
  its five changed runtime modules were compared byte-for-byte with the checkout.
- Python compilation, CLI flag discovery and `git diff --check`: passed.
- A preexisting temporary-path fixture mismatch was corrected by resolving the Git
  fixture root. The process-watchdog test now observes termination while sampling
  is stalled instead of imposing a host-dependent one-second total cleanup bound.

All validation providers were offline fixtures. No hosted model was invoked by
the implementation, tests or configuration migration. Tests do not prove model
interviewing or semantic validation quality.

## Existing-run rollout

Both existing runs now have `milestone_checkpoints.enabled=true`. Their approved
contracts and model mappings were preserved. The migration initially left both in
`PAUSED_MILESTONE_ACTIVATED` with `sol` next, without a provider call. A subsequent
read-only status check found both had been resumed and were `RUNNING` with an active
`sol` stage, independently validating the retained work. Those ongoing model runs
are separate from the completed offline verification above.

- ddia-tutor: `20260919-150739-continue-the-private-capability-tutor-through-al-a0a5d93c`.
  Its writer reached the requested boundary; the queued configuration was applied
  without a provider call.
- IdleCampus: `20260918-113408-complete-the-adaptive-dsa-mapping-and-validation`.
  The existing writer exited with code 1 without a terminal report. The runner's
  stopped-worker guard was used before archiving attempt `023/terra-03`; all ten
  changed files and execution logs were retained. The queued configuration was
  then applied without replaying implementation or launching a provider.

The global editable `autocode` installation points to this checkout. The rebuilt
wheel is `dist/autocode_supervisor-0.5.4-py3-none-any.whl`. Source fingerprints for
this change are recorded in `source-manifest.json`.
