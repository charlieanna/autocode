# A Tester PASS needs a probe of its own (#3937, #3923, #3922, #3902, #3900)

## The shared cause

Five live runs (VS Code extension, Kilo GLM-5.3 Tester, MiMo Builder) delivered a defect as "checked"
for one reason: the independent Tester marked a behavioral acceptance criterion PASS from the approved
plan's own checks and examples alone, so a defect just outside those examples passed.

| Issue | What the approved examples held fixed | What a varied input finds |
| --- | --- | --- |
| #3937 | the base class only | a documented subclass comes back as the base class |
| #3923 | the base class and plain values | a subclass overriding the encoder hook, or another value the constructor accepts, is refused by the decoder |
| #3922 | ASCII text | non-ASCII digits are accepted |
| #3902 | small inputs | a field over the parser's size limit leaks a traceback |
| #3900 | one failure at a time, a valid store | combined failures, and the code's order against the order the plan states |

The Tester prompt asked only for "additional probes for concrete requirements or risks the approved
checks do not establish", and the runner accepted a PASS criterion whose only evidence was the approved
required commands, so the Tester's independence added nothing the Builder's own tests had not shown.

## The fix

- `tools/prompts/validator.md` states the Tester's job once: each behavioral criterion it marks PASS
  needs at least one probe of its own beyond the approved checks, varying the input along an axis the
  approved examples held fixed. It names the generic axes (other input types the public entry point
  accepts, character set and encoding, size limits of the parsers and libraries relied on, combinations
  of failures and their order, the plan's technical approach compared with the code), not the five cases.
  A probe that finds a defect is a FAIL finding with reproduction steps. Where nothing can vary, the
  Tester gives a short `probe_reason`.
- `tools/autocode_tester_probes.py` (pure; standard library and `autocode_util` only) records, for each
  PASS criterion, which cited checks are the Tester's own and which are approved required commands, and
  decides the one rejection. A cited check is an approved command when its command, without the runner's
  `cd <dir> && ... 2>&1` wrapper and a `sh -c` wrapper, has the argument list of an approved command
  (`verification_plan.approved_commands`); any other cited passing check is a probe.
- The Tester stage's generation schema (`report_schema.review_generation_schema`, stage `tester` only)
  offers an optional `criterion_results[].probe_reason`. The role schema, and with it the Builder
  self-assessment and `review_checkpoint` schemas, is unchanged.
- `autopilot.apply_review_result` calls the check for a Tester record after the existing evidence
  preflight and before clean replay, and records `validation.tester_probes`. Clean replay, changed-code
  evidence, the mutation check and the end-to-end rules keep their code paths.
- `autocode report-check` (Tester stage) gives the same message from the same function: the Tester
  spec carries the approved commands, and a receipt-cited check with a null command is read from its
  receipt.

## Rejection rate (AGENTS.md architecture rule 8)

**Saved live Tester responses: 130 rejected of 140.** The project's survey archive holds 236 saved
Tester reports (`iterations/NNN/validator-NN.json`, with each run's `state.json`) from live runs on
2026-11-13 to 2026-11-15, before this change. Each was passed to `autocode_tester_probes.admit` with
the approved required commands `verification_plan.approved_commands(state, progressive_context=...)`
derives from its own run state. 175 had verdict PASS; 35 of those had no approved required command (the
gate cannot refuse them: every cited passing check is the Tester's own) and 140 had at least one. Of the
140, **130 would be refused** (129 as `ProbeEvidenceRequired`, one Tester rerun; 1 as a report-only
repair because it listed an uncited probe) and 10 are accepted. The refusals are genuine, not
misclassified respellings: a typical one (`p6-python-behavior`) lists the one approved command
`python3 -m unittest discover -s tests -t .` and cites it, alone, for all four criteria.

That rate is the defect the five issues report, measured: Testers written to the old prompt almost
never ran a probe of their own. It is also the cost the gate would add if the new prompt paragraph did
not change that behavior: one extra Tester launch per refused report, then the bounded report-only
repair. The prompt paragraph and `autocode report-check` (which the Tester runs before finishing) are
there so a Tester catches it inside its own launch; the rate on answers to the new prompt is not
measured here, since this change ran no live model, and should be read from `autocode usage report`
rejection reasons once runs use it.

**Also measured: every Tester report the existing admission tests admit.** A wrapper around
`autocode_tester_probes.admit` (kept with the run's evidence, not committed) records, for each Tester
report the runner admits while the named test modules run, its verdict, the approved required commands of
its state, and the gate's decision on the report as written and on a copy with every `probe_reason`
removed, which is the shape the scripted Testers wrote before this change. It was run twice:

| Run | Modules | Tests | Tester admissions | PASS reports | PASS with approved commands | PASS rejected (as written / without `probe_reason`) |
| --- | --- | --- | --- | --- | --- | --- |
| A, in-process wrapper | tests.test_catalogue_t04, test_design_manifest, test_milestone_checkpoints, test_report_admission_parity, test_rework_policy, test_autocode, test_report_repair | 423 | 123 | 52 | 0 | 0 / 0 |
| B, wrapper installed in every Python process by a `sitecustomize` import hook | tests.test_coordination_gate_provider, test_check_command_from_receipt, test_autocode.RetrofitTest, test_report_admission_parity, test_milestone_checkpoints, test_catalogue_t04 | 160 | 121 | 52 | 0 | 0 / 0 |

The runs overlap (test_catalogue_t04 and test_milestone_checkpoints give the 52 PASS reports of each run;
test_autocode gives one in run A), so they are not added. **Result: 0 rejected of 52 saved-fixture
Tester PASS reports, as written and with `probe_reason` removed.** No admitted state had approved
required commands: these fixtures give their current task no `validation_plan`, so every cited passing
check counts as the Tester's own. The measurement therefore shows that the gate refuses nothing in
the existing admission fixtures; it does not measure how often a live Tester cites only approved
commands, which the five issues show happens. The cases where the gate does refuse (approved commands
present, cited alone) are the saved-report tests in `tests/test_tester_probes.py`. Run B recorded no
admission from a child `autocode` process (every one of its 121 admissions came from the test process
itself; whether those children did not inherit the hook's `PYTHONPATH` or admitted no `tester` stage was
not established), so the scripted Testers' updates (a
`probe_reason`, or in `test-scenarios/cart_fixture.py` its own behavior checks, which are not approved
commands) are verified by those flows' tests rather than by this count.

Many fake-provider flows in those modules stop in this environment before their Tester stage, at the
Linux kernel session boundary (`BoundaryError: Invalid or unbound listener descriptor transfer`), on the
base commit as with this change; their Tester admissions are therefore not in the count, and CI on
Ubuntu is their evidence.

## Why a hard gate

The cause is that a PASS rested on nothing the Tester observed beyond the Builder's own tests; recording
the probe evidence without rejecting would leave exactly the reports of the five issues accepted. The
rejection costs no person anything and at most one extra Tester launch: a report that lists no probe of
its own goes to the existing bounded `missing_execution` Tester rerun (`ProbeEvidenceRequired`,
classified before the exit-code heuristic, so an exploratory nonzero command does not send the Builder
back); a report that lists an uncited probe, or a second refusal after that rerun, goes to the existing
bounded report-only repair, which can cite the listed probe, give a `probe_reason` or report the
criterion NOT_VERIFIED. Past those limits the existing `PAUSED_INVALID_OUTPUT` /
`PAUSED_REPEATED_FAILURE` handling applies. Only a verdict PASS report is refused; a FAIL report is
admitted as before. Report-check exposes the same check so the Tester can fix it before finishing, and
the recorded reason path makes the gate cheap for criteria where nothing can vary.

## Still open

1. **Combined review routing.** Under `glm_first_v1` the testing job runs as `review_checkpoint`, whose
   prompt is not `validator.md` and whose schema has no `probe_reason`; it gets neither the probe
   paragraph nor the gate (`autopilot` gates only a record whose stage is `tester`).
2. **The gate compares argument lists, not intent.** A cited command whose argument list differs from
   every approved command counts as a probe, so rerunning the suite or an approved test with another
   flag (`-v`, `-k`) passes it. Whether a probe varies an input along one of the axes is the prompt's
   instruction and the Completion Reviewer's judgment, not something the runner checks.
3. **mypy.** mypy is not installed in this checkout's `.venv` and is never installed into the project;
   the type check of the changed `tools/` modules comes from the CI mypy job for the revision.
4. **`tests/coordination_provider.py`.** Updated as the person decided (answer Q1): its scripted Tester
   adds a `probe_reason` on the `tester` stage when the schema offers one. Because it is an original
   test-support file, AutoCode's original-test gate reruns the whole suite before completion, which does
   not pass in this environment; the operator verifies the coordination flow and the pull request's CI
   is the evidence (`tests.test_coordination_tui_e2e` fails here on the base commit as well, 7 failures
   and 2 errors of 9, for the session-boundary reason above).
5. **Any nonblank reason is accepted.** The gate does not check that a criterion with a `probe_reason`
   is really process, documentation or human-review only, or that no input can vary; a Tester could
   write a reason where a probe was due. That judgment stays with the prompt and the reviewers.

## Live run evidence

Resources: `tools/prompts/validator.md`
Exempt reason: The approved plan for this change forbids live model runs (permission boundary: no live model runs). The prompt paragraph is checked by tests.test_tester_probes (the axes and the plan-approach comparison are present, none of the five cases is named) and every runner and report-check behavior it relies on is covered by model-free saved-report tests; a live Tester run on the five issues' fixtures is left for the operator.
