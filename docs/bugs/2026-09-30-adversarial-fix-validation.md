# Adversarial fixes and their validation

The fixes address the five original failing attacks (including vacuous-test false
completion), the missing-package planning defect, and integer defects in six
retained generated applications. This note records the original local campaign.
Historical failures and generated deliveries are preserved in the originating
checkout; repaired application copies and test output are ignored artifacts.
The pull request carries the core fixes, regression harness and reproducible
catalog fixtures. Its validation on current master is recorded below.

| Defect | Resulting behavior |
| --- | --- |
| Orphaned Builder permits a new run in its checkout | The child inherits the writer lock; durable birth-identified receipts also exclude surviving workers. A forced controller crash before its first receipt stays exclusive. |
| Unrelated successful check replaces approved verification | The runner independently replays explicit commands from the approved current-task verification plan. |
| Empty generated tests certify broken behavior | A narrow static guard rejects provably inert unittest suites before their PASS can count. |
| Completion permission recovery loses validation | Unchanged, currently bound evidence is retained; invalid evidence sends completion through the workflow's verification stage. |
| Investigator cannot replay cited run-root evidence | Staging uses the authoritative run directory and includes valid cited sibling evidence under existing boundaries. |
| Required test package marker is outside approved scope | Missing unittest package markers must be assigned before approval; persistent impossible plans stop before the Builder. |
| Generated applications overflow or reject unbounded integers | Six copied applications use exact storage, arithmetic and decimal conversion; Planner and Validator instructions require boundary examples. |

The verification change initially treated a quoted README command template as
an executable check. The fresh live temperature run exposed this. Extraction now
requires an execution instruction for quoted commands; inspecting a template as
documentation does not execute it. A scratch-replay regression checks this behavior.

## PR qualification on current master

The combined fix branch is based on `f87104b63ba4ea35e3ee103ba6d9c0c294a85817`.
Permission recovery is integrated into the newly extracted
`autocode_stage_recovery` module; the refactored CLI and controller line caps
remain intact.

The changed suite passes **1,005 tests in 68 modules** with an isolated Python
3.11 environment matching CI (`pip install -e .`, with its venv on `PATH`).
The focused checks pass 25 tests and the adversarial suite passes 40/40 on the
same branch under the existing Python 3.14 interpreter. All 352 recorded
adversarial provider identities have stopped, cleanup reports are empty, and
provider-trace hashes match.

The earlier Python 3.14 changed-suite run retained the previously documented
zero-test fixture failures. Its installed-package checks also failed because
that older environment did not have this package installed. Both categories
pass in the fresh Python 3.11 environment; no test assertions were changed.

The harness and catalog controls pass 69 tests. All 48 fake end-to-end scenarios
finished: 45 PASS and the same three pre-existing outcomes described below
(one FALSE_COMPLETE, one NOT_EXERCISED, one intentional live-only SKIPPED).
Fresh evidence is under `.scenario-runs/pr-adversarial-final/` and
`.scenario-runs/pr-fake-final/` in the PR worktree; logs and generated application
copies are excluded from the commit.

The historical results below precede this requalification and are retained to
make the initial failures and limits of the live evidence explicit.

## Deterministic and application checks

The final checkout inheritance implementation passed all 40 adversarial attacks
in `.scenario-runs/20260930-adversarial-fixed-v2/`: evidence 11, recovery 7,
lifecycle 15, persistence 5, planning 2. All original failing assertions remain
ordinary assertions. All 352 recorded provider identities had stopped after
cleanup. The final rerun after the documentation-template correction also passes
40/40, with matching trace hashes, no cleanup errors and no live recorded provider
identities. Its evidence is under `.scenario-runs/20260930-adversarial-fixed-v3/`.

The harness and adversarial reporter tests passed 69 tests. The focused final
verification, replay, architecture, test-quality and recovery tests passed 25.
The final fake catalog completed all 48 entries: 45 PASS, one known
FALSE_COMPLETE, one NOT_EXERCISED, one intentionally SKIPPED. These exceptions
match the previous catalog run and remain visible rather than being reported as
passes. `bugfix-trivial` passes its application oracle but disagrees with the
catalog's expected short-path stages; `feature-refund-window` passes behavior
without exercising the required Resolver stage; `stuck-planner-citation` requires
a live Investigator. Final changed and fake reruns are logged under
`.scenario-runs/*fixed-v3*`.

Every manually repaired application passes its original independent oracle:
inventory 6/6, CSV import 6/6, ledger 5/5, lease queue 5/5, outbox 5/5,
event replay 5/5. Seven additional public-interface regressions fail against
the original copies and pass against repairs, including 5001-digit persistence,
64-bit transitions, rollback and idempotency. Source hashes prove the original
deliveries stayed unchanged. Evidence and repair paths are recorded in
`.scenario-runs/20260930-integer-repairs/summary.json` and
`integer-regressions.json`.

## Fresh OpenCode runs using only Codex models

All recorded live model routes use OpenCode with `openai/gpt-5.6-terra` or
`openai/gpt-5.6-sol`; no alternate provider or model route appeared.

| Scenario | AutoCode outcome | Independent oracle |
| --- | --- | --- |
| Transaction ledger | TASK_COMPLETE, PASS | 6/6 |
| SQLite inventory | RESOLVER_PENDING, active-time limit reached | 7/7 |
| Temperature CLI | WAITING_FOR_USER, attempt budget exhausted | 7/7 |

The temperature run's spurious template replay caused its stop. Replaying its
preserved Validator checks with the final extraction correction passed and is recorded
separately; it does not turn the historical blocked run into a completed run.
Inventory also encountered malformed planning citations and an incomplete
OpenCode tool-call turn before hitting its time limit. Those remaining provider
and convergence limitations prevent a claim that AutoCode works perfectly.

Live runs started before the final lock-inheritance and template-extraction
corrections. Their results qualify the initializer and numeric-boundary changes;
they are not one uniform final-runtime qualification. Their original results,
route audit and source-version note are in
`.scenario-runs/20260930-live-fixes/final-audit.json`.
All 294 recorded live provider identities had stopped after these runs.

## Repository suite limitations

All 140 full-suite modules finished. Seventy distinct failures reproduce on
untouched HEAD `628b257df5ed6c07d00d3d59a94ee44fc8a078db`, tested in separate
baseline checkouts. One additional descendant-cleanup test timed out under
parallel load; that entire 39-test module passed in an isolated rerun. The 67
modules in both final changed-suite runs had exactly the same 67 failing test identities as
untouched HEAD, with none introduced. The full run crossed implementation edits;
final targeted reruns qualify the final changes.

Comparison evidence is `.scenario-runs/suite-baseline-comparison-final.json`
and `.scenario-runs/suite-baseline-comparison-v3.json`.
Existing fixtures that claim a successful unittest run without providing tests
remain failing; the replay gate was not weakened to make them green. The suite
runner's printed test count can undercount failing modules by matching an echoed
inner `Ran 0 tests` line; module counts and distinct failure identities are used
here instead.

The inert-suite guard does not establish semantic test coverage. Numeric prompt
instructions do not guarantee future generated applications are correct. These
fixes close reproduced defects; independent behavior oracles remain necessary.
