# Design capture evidence refused at REWORK

Found while fixing the contained-Validator REWORK refusal; latent, not yet seen live.

A Completion REWORK re-verifies every pin of the accepted Validator before the repair is
handed on: `rework_policy.route` for an unrepaired, sealed report, then
`resolver_recovery.prepare_resolution` (and `route_known_change`) for the Resolver packet.
Both accepted a file under `.autocode/` only in the run directory, `.autocode/evidence/`,
or a recorded tool-containment scratch; the Resolver alone also accepted the run's
permission-recovery directory. Report acceptance pinned any workspace file.

A design Validator legitimately pins runner-written files elsewhere under `.autocode/`:

- the capture bundle, `.autocode/captures/<id>/` (`manifest.json`, `candidate.png`,
  `browser.json`, `build.json`), added by `design_coverage.report_refs` and
  `visual_evidence.native_refs`;
- reference images in the retained design inputs, `.autocode/design-inputs/<manifest hash>/`,
  which passing criterion evidence may cite.

Its prompt also told it to write comparison artifacts anywhere under `.autocode/`. Such a
report was accepted, and then every later REWORK paused with `PAUSED_STALE_HANDOFF`,
"Repair evidence belongs to another run". The Completion Reviewer cannot change the
Validator's pins, so no retry could pass. The existing CLI design tests did not reach it:
their Validator reports are report-repaired, and `route` skips pin re-verification for a
repaired report.

## Rule

`rework_policy.owned` is now the one rule. A pinned file outside `.autocode/` is the
project's. Under `.autocode/`, these belong to the run:

- its run directory;
- `.autocode/evidence/`, the shared area that capture commands name;
- its permission-recovery directory, `.autocode/recovery-evidence/<digest of run directory>/`;
- the tool-containment scratch its launch records name (#419);
- its retained design reference, `.autocode/design-inputs/<manifest hash>/`, current or
  historical;
- a file of a capture bundle that the bundle's own `manifest.json` names: the manifest, or an
  artifact that still has the hash the manifest recorded. The manifest must be an
  implementation capture for the run's design reference, checked as `visual_evidence.verify`
  checks it (`visual_evidence.bundle_file`). This is ownership only; freshness is still
  checked where a PASS needs it.

Anything else under `.autocode/` (another run's directory, `.autocode/scratch/`, a file the
manifest does not name, a bundle for another design) is refused.

Three places apply the rule:

- `route` uses only the scratch named by the accepted Validator's own launch record, which
  is the scratch acceptance allowed (see `contained-validator-rework-ownership.md`).
- `resolver_recovery._artifact_owned` uses the scratch of any of the run's stage records,
  because its pins come from several stages.
- `autopilot.apply_review_result` now applies it to every independent validation (`sol`,
  including a checkpoint's) when the report is accepted (`rework_policy.require_owned`),
  using only the stage's own scratch.

A pin the repair paths would refuse is now an ordinary rejected report: a bounded report
repair can drop or replace the citation, which no stage can do once a REWORK has started.

The design capture instruction and `docs/visual-captures.md` now tell reviewers to write
capture configs, fixtures they create, and comparison artifacts under `.autocode/evidence/`.
The test fixture `make_capture` writes its configs there too.

## Evidence

- **Reproduction.** Unit tests seal a real unrepaired Validator report, pin a real capture
  bundle (made by the test fixture and checked by `visual_evidence.verify`), and route a
  REWORK. Before the change, both the bundle and a retained reference image paused with
  "Repair evidence belongs to another run". `prepare_resolution` paused the same way
  ("Recovery packet: evidence belongs to another run"). After the change:
  - the bundle reaches direct assignment;
  - through the real `queue_resolution`, it reaches the Resolver with the bundle archived
    in the packet.
- **Refusals kept.** These are still refused at REWORK, and at acceptance:
  - a file in a bundle that its manifest does not name;
  - an artifact changed after capture;
  - a bundle for another design reference;
  - a bundle in a run without a design reference;
  - a reviewer file under `.autocode/scratch/`;
  - another stage's containment scratch, at acceptance.
- **Saved runs on this machine.** Where accepted Validator reports (current and archived)
  pinned files:
  - 28 reports from 64 live scenario runs and 2,612 from fake runs: every pin was outside
    `.autocode/`, in the run directory, in `.autocode/evidence/`, or in the stage's own scratch.
  - 199 reports from self-build workspaces: all but 6 were in those places.
  - The 6 exceptions are 2026-09-18/19 `agent-console` reports that cited operator files under
    `.autocode/inputs/`. Acceptance now refuses those, as `route` already would at a REWORK.
  - No saved run has a capture bundle.

- **Live run.** On 2026-10-06, `greenfield-greeting-cli` passed with glm53-mimo through the
  pinned OpenCode 1.18.33 with containment: TASK_COMPLETE, oracle 12/12, 1,619 s.
  - The contained GLM Validator's PASS was accepted through the new check with 25 pins:
    - 12 in its own containment scratch;
    - 12 in the run directory;
    - 1 outside `.autocode/`.
  - A read-only replay of the saved state confirms that both the acceptance rule and the
    repair-time rule accept every pin.

A design REWORK cannot be produced on demand in a live run (scenario projects have no
browser capture), so the REWORK paths are covered by the fault-injected tests above.
