# Tests, evidence, and legacy migration

[← Back to README](../README.md)

## Progressive testing plan

See the [progressive testing plan](testing-plan.md) for the simple-to-complex
task ladder, independent pass criteria, failure-injection matrix, evidence format,
and promotion gates. It separates offline runner checks from authorized live-model
delivery trials and records harness hazards and their verification status. Do not
assume unrestricted discovery is offline: use `tools/run_suite.py` with live-provider
toggles unset, and report its skipped and excluded cases separately from passes.

## Tests and evidence

AutoReview generation pins contract/task identity and reviewer-owned finding IDs
in each attempt's schema. Report-only repairs receive the original executed
commands and exit codes; normal evidence validation still applies. Repairs cannot
close findings. Fresh reviews can resolve a finding only when all criteria in its
recorded scope have passing evidence; unsupported closure claims remain open with
`pending_resolution`. Because older findings carry milestone-wide scope, they
conservatively require that entire scope to pass. Explicit retractions remain a
separate disposition.

Reviewers stay read-only. Missing devices, credentials, browser access or compiler
scratch permissions are verification blockers, not permission to change source
or bypass restrictions. A shell wrapper exiting zero does not prove its nested
test command succeeded. Live audit limitations are recorded alongside results.

### Unit suite

```sh
python3 -m unittest tests/test_escalation.py tests/test_autocode.py tests/test_goals.py tests/test_subprocess.py tests/test_opencode.py tests/test_process.py
```

The unit suite uses isolated Git fixtures. The subprocess test drives the actual CLI,
runner, schemas, snapshots and approval commands with a deterministic **fake Codex**
provider. That provider writes a tiny greeting program, then executes both success and
failure cases in its validation stage. It never calls a real model or reads credentials.
Set `AUTOCODE_TEST_CLI=/path/to/installed/autocode` to test the installed package.
These tests prove runner behavior, not a model's interviewing quality or semantic
review accuracy. See [`VALIDATION.md`](../VALIDATION.md) for measured results and remaining limits.

The OpenCode subprocess tests use a fake OpenCode executable with native event shapes,
including separate resumed sessions, command evidence and usage. An optional tiny
live check is available via `python3 tools/opencode_smoke.py --run-live`; it makes one
request to each selected provider in a temporary workspace and saves raw evidence.
Process tests require local `ps` access and exercise detached-worker cleanup, timeouts,
interruption and checkpoint-write failure. The repair audit (archived at tag `archive/pre-restructure-2026-09-26`, under
`audits/opencode-repair-2026-09-19/`) also verifies the original defects with native
OpenCode metadata and a loopback provider fixture, without hosted model requests.

Codex launch compatibility was checked against installed exec/resume help and
[official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

### Opt-in product audits

The [shared provider conformance probe](provider-conformance.md) exercises a Builder,
a fresh Validator and resumed validation through the same small fixture on Codex,
OpenCode and KiloCode. Its offline mode includes fault injection; live mode requires
explicit spend authorization. It complements the full workflow scenarios below.

`tests.test_autoreview_products` is skipped unless `REVIEW_AUDIT_LIVE_CODEX` is
explicitly set. These tests make real model calls using the selected Codex
executable and its credentials; they are not part of offline model verification.
Run individual cases rather than wrapping the entire live module in one long
tool call, and retain the case directories:

```sh
REVIEW_AUDIT_LIVE_CODEX="$(command -v codex)" \
BUILD_AUDIT_ARTIFACTS=/absolute/path/to/audit-artifacts \
python3 -m unittest tests.test_autoreview_products.ReviewProducts.test_06_go_tld_override_parity_exception -v
```

The Go case checks a local toolchain before dispatch and uses a canonical probe
that disables toolchain/dependency downloads. The browser case, `test_04_mobile_initial_visibility_requires_rendered_evidence`,
requires Python Playwright and its Chromium installation in the **same Python
environment running the test**. Provision these deliberately in an isolated
environment, not automatically inside a test:

```sh
PYTHON=/absolute/path/to/browser-env/bin/python
export PLAYWRIGHT_BROWSERS_PATH=/absolute/path/to/browser-cache
"$PYTHON" -m pip install playwright
"$PYTHON" -m playwright install chromium
REVIEW_AUDIT_LIVE_CODEX="$(command -v codex)" \
BUILD_AUDIT_ARTIFACTS=/absolute/path/to/audit-artifacts \
"$PYTHON" -m unittest tests.test_autoreview_products.ReviewProducts.test_04_mobile_initial_visibility_requires_rendered_evidence -v
```

Preflight results are **host-only**, not proof that the reviewer sandbox can run
the same tools. A missing prerequisite skips with `NOT_VERIFIED`; that skip does
not satisfy a live acceptance requirement. Neither test disables sandboxing.
The reviewer must execute the exact candidate-bound probe and independently
identify the seeded defect. A passing audit means the defect was correctly
detected, not that the intentionally defective application is correct.

Case directories retain write-once invocation receipts, including nonzero exits,
timeouts and interruptions. Earlier completed receipts survive a later failure;
an incomplete receipt or a green subset must never stand in for the full suite.
The canonical browser evidence requires rendered geometry and a screenshot, not
DOM presence or a browser keyword in a report. The Go evidence requires raw
program output, not an echoed shell summary.

### Dashboard tests

See [Dashboard](dashboard.md#dashboard-verification) for the dashboard test commands.

### Live-trial results

`tools/live_trial.py` uses the same verdict in `live-trial.json` and the bundle's
`result.json`. Its exit codes describe delivery, not merely whether the harness ran:

- `0`: `PASS`, runner completion with passing independent oracle checks.
- `2`: `HONEST_BLOCKER`, a recorded pause rather than delivered work. Missing live
  spending authorization also exits `2` without starting a trial.
- `1`: unsuccessful or unverified delivery, including `FALSE_COMPLETE` when the
  runner claims completion but an independent check fails, and `ERROR` for an
  unexpected stopped state or an oracle-reported infrastructure error.

An oracle-reported error or deferred check is not proof of a product defect and
cannot establish successful delivery. The fixture-profile tests in
`tests/test_live_trial.py` exercise these verdicts without hosted-model requests.
These scoring checks do not remove the other live-driver limitations listed in
the progressive testing plan.

The driver also registers the [task-type scenarios](scenarios.md) (bug fix,
feature, architecture, multi-service program, design-reference UI). Their oracles
have offline positive and targeted negative controls in `tests/test_scenario_oracles.py`; `--score-only PATH` scores a
workspace delivered by any route, and `--mode program` drives a scenario through
[`autocode program`](program.md). Every task-type baseline is `NOT_RUN` until a live
result is recorded.

In program mode, `--i-authorize-live-model-spend` authorizes model calls only.
`--authorize-deployment` is a separate opt-in for deployment workstreams and is never
added automatically. `PROGRAM-01` needs no deployment authorization: generating its
descriptors is ordinary code work, and no deployment is performed.

`--timeout` is a single wall-clock budget shared by all CLI invocations in the
runner-driving phase, including program child gates. Independent oracle scoring and
bounded process cleanup are separate. On deadline the harness stops the CLI and its
provider descendants, then records `ERROR` and scores whatever was delivered; it
does not rewrite a still-`RUNNING` checkpoint into a successful or paused run.
Unhandled runner pauses remain honest blockers instead of being blindly resumed.

The first GLM 5.3 / MiMo v2.6 Pro task-type live trials produced no completed delivery;
see [the evidence record](../VALIDATION.md). Passing offline controls must not be
presented as live model success.

### Diagnosis trials

`tools/live_diagnosis_trial.py` records a controlled trial of the operator-triggered
diagnostic route. Its CLI phases share one deadline and invocation budget. Candidate
stdout and candidate tests are not grading authority: a bounded isolated adapter
returns function results over a separate channel, and the parent checks the numeric
contract and protected-test bytes. Import failures, skipped tests, early exit, missing
results, forged stdout, wrong signatures, timeouts, and changed protected tests fail.
The subprocess boundary and process-group cleanup are not a hostile-code sandbox.

The report separates the seeded implementation defect from the actual report-rejection
diagnosis target. A mechanically raised repetition count is labeled fault injection,
not proof of organic repeated-failure detection. Policy acceptance and an observed
original-stage retry are recorded separately from the model's recommendation.

`code_verdict` can be PASS or FAIL, but a code PASS is never an automatic diagnosis PASS.
The overall result is `RECORDED` (exit 3) when diagnosis is not exercised or causal
assessment still needs human review, `FAIL` (exit 1) for failed independent checks, and
`ERROR` (exit 1) for a driving/budget failure. `--i-authorize-live-model-spend` remains
required for non-fixture profiles. Passing fixture regressions does not close the
real-model diagnosis-validation requirement.

Whether AutoResolver diagnoses a real failure correctly (#59) is covered by the scenario
`feature-stock-refusals` through `astra_resolve`: refusal tests that also pass on the
original code fail the runner's regression proof, and the oracle's `diagnosis()` scores the
Resolver's diagnosis and repair task apart from the run verdict (see
[the scenario harness](../scenarios/README.md#diagnosis)). `tools/live_diagnosis_trial.py`
exercises `astra_diagnose` only: a different stage, for repeated Builder report rejections,
that writes no repair task. Its results never count toward #59.

## Legacy migration — opt-in only

Existing v3 approved contracts retain their exact content, hash and approval. New
discovery drafts include the expanded build-brief fields. Older `TASK_COMPLETE` and
`VALIDATE` stage results remain readable during recovery; newly requested Plan Reviewer
decisions use the four statuses in [Execution](execution.md).

This extraction does not modify or attach to any existing project/run. Keep a live
legacy process running until it reaches a natural saved exit; it cannot hot-load these
gates. Do not launch a second writer or restore old state over newer work.

At a confirmed idle boundary, an operator may deliberately use this standalone tool:

```sh
autocode --workspace /path/to/project --run-dir /path/to/existing/run --migrate-only
autocode --workspace /path/to/project --run-dir /path/to/existing/run
```

Migration retains the original task, sessions, completed artifacts, stage history,
criteria, plan, iteration and limits. It backs up `state.pre-v2.json` where applicable
and `state.pre-v3.json`, then reconstructs an **unapproved** draft. Old evidence is
archived for inspection and must be revalidated. The Requirements Gatherer uses known answers/artifacts
to ask only material unresolved questions. User decisions are never backdated.
Completed interrupted stages reconcile before migration; live/uncertain stages
refuse migration. No migration was applied to the original IdleCampus run.

See also: [Execution](execution.md) · [Reliability priorities](../RELIABILITY.md)
