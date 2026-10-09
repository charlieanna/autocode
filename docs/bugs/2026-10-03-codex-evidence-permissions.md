# Codex evidence-write qualification (#313, part A)

A registered `report_file` command forwarded `--sandbox read-only` to Codex.
AutoCode then required its judging stages to create capture receipts in
`.autocode/evidence`, which that sandbox denied. A public scoped-permission
answer recorded intent but did not change the subsequent launch policy.
The report prompt also asked the model to write a file already persisted by
Codex's host-side `-o` option.

On AutoCode `f91575a2` with Codex 0.160.0 on macOS, a live Luna/Sol clamp build
reproduced three failed Validator capture commands and then two more after the
public permission answer. These were five failed commands in one scenario.
The implementation itself passed its tests and an independent 9,261-case check.
A disposable wrapper recovered that retained run within its original limits;
that prototype was not the production fix.

The registered-provider adapter now offers an explicit `codex_artifacts`
policy and `{sandbox_args}` splice. Read-only stages may write the operational
evidence/output directories and their exact stage report; the Builder retains
workspace-write. Config validation rejects conflicting sandbox options and
unsupported Codex versions. The output prompt uses Codex's final-response
persistence. Requirements inherits the configured Planner's reasoning default,
preventing the empty effort value observed in the original live setup.
See [provider setup and compatibility limits](../providers.md#codex-commands-that-write-capture-receipts).

A real OS-sandbox control found a gap in the first prototype: a pre-existing
hard link inside evidence could modify its source target. Direct source and
symlink writes were denied, as was creating a new source hard link. The adapter
now refuses existing aliases and special files in both operational trees before
launch, without deleting them. A fresh live Luna component executed six denied
actions and successful real capture/log/report writes; a separate hard-link
fixture was rejected before any model call.

The production candidate was based on local merge `258c9fd5` (PR branch
`6b611d8b` and master `40295d62`). A fresh full AutoCode run used Luna for
Requirements, Planner and Builder, and Sol for Plan Reviewer, Validator and
Completion Owner. It reached `TASK_COMPLETE` with:

- 11 criterion results and full-flow validation PASS;
- five fresh Validator capture receipts with matching command, attempt, source
  revision and output hashes;
- 11 runner fail-before/pass-after tests, and all 12 delivered tests passing;
- all 9,261 independent integer cases passing, versus zero on the original stub;
- unchanged protected tests, no permission grant and unchanged limits:
  1,800 seconds total, 360 per stage, 180 idle, three iterations;
- Completion Owner COMPLETE and public `completion_current: true`.

Requirements and Discovery each needed one bounded report repair. Their original
reports remain archived. During planning, the unit gate exposed `KeyError('glm')`
for programmatically constructed providers with no role defaults. The guard was
corrected at the saved plan-approval boundary, before Builder/Validator/Owner;
there was no run reset or state, source, config or budget edit. The sandbox helper
was unchanged, and the final provider files match the working-tree implementation.
This is not a claim that every stage used one immutable runtime revision.

Final affected-provider checks passed: 97 tests across nine modules, including
slow registered-provider flows. The supplemental fake catalog had 54 PASS, one
existing NOT_EXERCISED and one live-Investigator SKIPPED before the empty-role
compatibility guard; its behavior for complete provider configs was unchanged.
The preceding merged snapshot passed 1,575 affected tests and the same catalog.
The original failed 522-test gate is retained, with its only failing module
corrected and retested. No assertion, deadline or run allowance was weakened.

Local evidence is under
`.scenario-runs/remaining-defect-proof/evidence-writes-313/`:
`qualification-candidate.json`, `production-component/audit.json`,
`qualification-production.json`, and the original failed reports and OS controls.
Do not commit these run artifacts.

Part B of #313—the old failed job without an original source capture—remains
unverified. The reported run predates capture support; that is a lead, not proof
of its cause. This change does not relax the source-identity recovery gate or
close the whole issue. Other provider wrappers, language cache requirements and
platform/version combinations need their own live qualification.
