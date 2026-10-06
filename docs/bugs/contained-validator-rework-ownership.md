# Contained Validator evidence refused at REWORK

On 2026-10-06 a live self-build on master `ac0a4587` (glm53-mimo through OpenCode
1.18.33 with strict tool containment) failed milestone M1's regression proof on one
load timeout. The Completion Reviewer then returned REWORK twice, and the runner
refused both reports: `PAUSED_STALE_HANDOFF`, "Repair evidence belongs to another run".

The accepted Validator (GLM 5.3) had pinned all eight capture receipts under its own
`.autocode/tool-containment-<hex>/scratch/`. That was the only place under `.autocode/`
its kernel sandbox let it write, and its prompt asked for it there. In the same prompt,
the shared COMMON text still named `<run-directory>/evidence/`. Report acceptance pinned
any workspace file by hash. `rework_policy.route` re-verifies those pins on every
REWORK, and at `ac0a4587` it accepted `.autocode/` paths only in the run directory or
`.autocode/evidence/`. The Completion Reviewer cannot change the Validator's pins, so no
retry could succeed. The operator recovered by abandoning the review and resuming into
fresh validation.

#419 (`1d7c42eb`, merged after that run started) made `route` accept a scratch named
by any of the run's stage records. Now one rule decides which Validator evidence a
later repair can use:

- `route` accepts only the scratch named by the accepted Validator's own launch record.
  The Builder's scratch, an earlier rejected attempt's scratch and another run's scratch
  are refused. The record is in the runner-written `state.json`, and pins are
  re-verified only through a sealed, accepted report.
- `autopilot.apply_review_result` applies the same rule when it accepts a Validator
  report (`rework_policy.require_owned`, with only the stage's own scratch). A pin in
  another stage's `tool-containment-*` scratch is now an ordinary rejected report, which
  a bounded report repair can still fix. Before, it became a REWORK that could never
  pass. A repaired report is checked with its original attempt's record, whose launch
  made the scratch.
- An earlier form of this fix checked only containment directories at acceptance,
  because design Validators legitimately cite runner-written captures under
  `.autocode/captures/` and `route` did not own them. `rework_policy.owned` now owns a
  capture-bundle file through its manifest for the run's design, and the retained
  design inputs (`2026-10-06-design-capture-rework-ownership.md`). So acceptance and
  `route` apply the whole rule to design runs as well as build runs. An unbound file
  under `.autocode/captures/` is refused at acceptance.
- In a contained launch, `provider_launch.containment_prompt` rewrites both capture
  examples, the provider contract's and COMMON's, to the stage's scratch. The boundary
  note says this replaces any other evidence directory. `VALIDATOR_NOTE` says which
  `.autocode/` locations the runner accepts in a report.

`resolver_recovery` still accepts a scratch named by any of the run's stage records.
Its pins come from several stages, and every Validator pin among them has now passed
the narrower rule at acceptance.

Evidence:

- **Replay of the observed run.** The run's saved `state.json`, the accepted
  Validator's record and pins, and both refused REWORK decisions were replayed
  in-process through `route`, read-only (queue and snapshot stubbed).
  - `ac0a4587`: both reviews pause with "Repair evidence belongs to another run".
  - Current master and this change: both go to the Resolver.
  - The acceptance check passes on those pins and refuses them when the scratch is
    attributed to the Builder's launch instead.
- **Saved live runs on the development machine.** The new acceptance rule would have
  refused none of the pins in 187 accepted live Validator validations, including the one
  contained run.
- **Live run.** On 2026-10-06, `greenfield-greeting-cli` passed with glm53-mimo through
  the pinned OpenCode 1.18.33 with containment: TASK_COMPLETE, oracle 12/12, 1,590 s.
  - The contained Builder and Validator prompts each named only their own scratch,
    in both capture examples.
  - The accepted GLM Validator pinned 14 files in its own scratch.
  - A REWORK cannot be produced on demand live, so `route` is covered by the replay
    and the unit tests.
  - The build phase started before the acceptance check was narrowed, so that run
    used the stricter earlier form. The final form also accepts that Validator's pins
    and refuses them when they are credited to the Builder's launch.
