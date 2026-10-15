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

### Host requirements for tests

Fake providers avoid model requests and credentials; they do not exempt a
supervised task-stage launch from [provider process ownership](providers.md#provider-process-ownership).
On Linux, CLI regressions and scenarios that launch those stages require
native little-endian x86_64 or aarch64, a matching `/proc` PID namespace, and
working seccomp user notifications with syscall continuation; another Linux
host pauses `PAUSED_PROCESS_CLEANUP` before provider spawn, which is a launch
blocker, not a passing fake run. macOS runs these launches under the portable
supervision described in [provider process ownership](providers.md#provider-process-ownership),
without the kernel boundary, so fake and live runs work there. Tests that
assert the kernel boundary itself run on Linux only.

For a provider-backed regression, use a supported Linux checkout of the same
revision and the [development setup](../CONTRIBUTING.md#set-up-a-development-checkout).
Run as an ordinary user and install only the toolchains the selected test needs.
For example, this existing fake CLI regression needs no model account:

```sh
.venv/bin/python -m unittest \
  tests.test_job_route.JobModelRouteTests.test_a_model_opencode_does_not_list_is_refused_and_changes_nothing
```

#### One focused Linux fake run from macOS

A macOS contributor with Docker Desktop running Linux containers can execute one
provider-backed fake check in the pinned Ubuntu 24.04 image. The wrapper accepts
exactly one explicit `tests.test_*` unittest selector (a module, class or test) or
one catalog scenario ID:

```sh
python3 scripts/run_linux_fake.py --unittest \
  tests.test_job_route.JobModelRouteTests.test_a_model_opencode_does_not_list_is_refused_and_changes_nothing
python3 scripts/run_linux_fake.py --scenario bugfix-go-interval-boundary
```

The first invocation builds the image; later invocations use Docker's build
cache. Building needs internet access for the pinned Python 3.11, Node 22.18,
Go 1.25.5 and package inputs. The selected test runs with `--network=none`
(container-local loopback remains available), all capabilities dropped, a
read-only image and only this checkout mounted at `/workspace`. The host home,
credentials and Docker socket are not mounted or forwarded. Scenario mode adds
`--fake` itself; live profiles, providers and model-spend authorization are
refused.

Docker must support Linux host-PID mode, seccomp user notifications and nested
unprivileged namespaces. The wrapper uses `--pid=host` so the process and its
container `/proc` view match, and removes Docker's outer seccomp filter so
AutoCode can install its own listener; it never uses `--privileged` or adds a
capability. A preflight exercises the real session listener and bubblewrap and
fails before the selected check if Docker Desktop's Linux VM cannot provide
them. This is execution inside supported Linux, not a macOS admission bypass;
macOS provider launches remain refused.

This path intentionally does not run discovery, the full suite or the full
scenario catalog. The image covers the CI Python/Node/Go toolchains, not Java,
browsers, live providers or scenarios that need the Docker socket (including
real local-Compose mode). The checkout is writable so normal test artifacts can
be produced; review them under `.scenario-runs/` as with a native Linux run.

A Linux root shell holding `CAP_SYS_ADMIN` or `CAP_SYS_PTRACE` is also refused.
For such a shell, use the existing capability drop for a direct focused run:

```sh
setpriv --bounding-set=-sys_admin,-sys_ptrace .venv/bin/python -m unittest \
  tests.test_job_route.JobModelRouteTests.test_a_model_opencode_does_not_list_is_refused_and_changes_nothing
```

`tools/run_suite.py` applies this prefix to test modules when those capabilities
are held and `setpriv` is available. Dropping them does not supply a missing
kernel feature. Scenario runs in that Linux shell need
the same prefix.

The existing [Ubuntu CI jobs](../.github/workflows/tests.yml) provide PR and
master checks. Report the exact revision, selected checks and any local checks
left unrun; PR selection can omit unchanged slow tests, and the harness gate is
not a pass of the entire fake catalog. Keep focused fakes as the default; a
[live run remains optional](../AGENTS.md#fake-first-verification-and-optional-live-runs).

PR CI uses the event's fixed base and head commits to select affected tests.
Python selection includes explicit prompt/schema and public CLI consumers;
required mapped consumers are retained even when normally slow. Heavy browser,
Docker and native platform checks run when their relevant area changes.
Prompt changes retain rendered-prompt controls and an installed-wheel resource
check. Process/provider changes retain refusal, supervision and cleanup controls;
fake providers never bypass unsupported-host admission. The always-reporting
`ci-required` job fails when scope selection fails or a selected job fails,
cancels or skips. Master and manual runs retain their existing complete coverage.

Maintain the mappings in `tools/ci_scope.py` and `tools/ci_test_selection.py`
alongside new consumers. A tests-workflow edit exercises its heavy job wiring;
it does not enroll the whole fake catalog as a PR prerequisite.

### Unit suite

Select the checks affected by the change. This host-admission policy test mocks
the platform and never launches a provider, so it can run on macOS too:

```sh
.venv/bin/python -m unittest \
  tests.test_session_boundary.SessionBoundaryTests.test_only_qualified_native_linux_abis_pass_without_loading_libc
```

Many unit tests use isolated Git fixtures. The subprocess tests drive the actual CLI,
runner, schemas, snapshots and approval commands with a deterministic **fake Codex**
provider. That provider writes a tiny greeting program, then executes both success and
failure cases in its validation stage. It never calls a real model or reads credentials.
Set `AUTOCODE_TEST_CLI=/path/to/installed/autocode` to test the installed package.
These tests prove runner behavior, not a model's interviewing quality or semantic
review accuracy. See [`VALIDATION.md`](../VALIDATION.md) for measured results and remaining limits.

A few tests read historical commits from local Git objects, so they need the
repository's full history, not a shallow clone (CI checks out with
`fetch-depth: 0`): `tests.test_catalogue_t14` replays a saved regression from
an earlier revision, and `tests.test_reliability_campaign` and
`tests.test_reliability_import` read the reliability campaign's pinned commit
(`source_revision` in `arena/campaign.json`), its Arena oracles and its
runner files. In a shallow clone run `git fetch --unshallow` first. These
tests fail, not skip, without the commit, and the reliability import tests say
which commit is missing.

The OpenCode subprocess tests use a fake OpenCode executable with native event shapes,
including separate resumed sessions, command evidence and usage. An optional tiny
live check is available via `.venv/bin/python tools/opencode_smoke.py --run-live`; it makes one
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
OpenCode, KiloCode and Claude Code. Its offline mode includes fault injection; live mode requires
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

Whether AutoResolver diagnoses a real failure correctly (#59) is measured by the scenario
`feature-stock-refusals` through `astra_resolve`: refusal tests that also pass on the
original code fail the runner's regression proof, and the oracle's `diagnosis()` scores the
Resolver's diagnosis and repair task apart from the run verdict (see
[the scenario harness](../scenarios/README.md#diagnosis)). Its scope is narrow. The defect is
a test-validity defect that the runner's model-free proof already flags, naming each vacuous
test, and in the three real 2026-10-04 Resolver calls the Completion Owner's review had named
the cause first (`review_already_named_cause`). A `CORRECT` therefore shows that AutoResolver
confirms the cause, writes a bounded repair that works and stays read-only; it does not show
unaided discovery of a product-logic defect. The scripted runs prove only the route and the
scoring. In eight natural live runs on 2026-10-05 (seven `claude-tiers`, one OpenCode) no
Builder wrote the vacuous tests, so none reached the trap. A hybrid run
(`run --profile NAME --hybrid`, see [the scenario harness](../scenarios/README.md#hybrid-runs))
scripts planning and the first Builder, which delivers the vacuous tests, and keeps the
Validator, Completion Owner, Resolver and repair Builder live; its results have their own mode
(`NAME-hybrid`) and its diagnosis counts only the live Resolver's calls. The live results and
their limits are in
[AutoResolver's live diagnosis](bugs/2026-10-05-autoresolver-live-diagnosis.md). In the 12
hybrid runs of #59's batches, a hand read found that the Resolver named the vacuous tests and
the cause in all 12; in 11 of them the Completion Owner had already named the cause (by word
match). On the fixed product the scorer gave 2 of 6 `CORRECT` at run time and 6 of 6 once it
was revised on those answers. #59 was closed on the owner's decision: the plan's
pre-registered criterion (at least 3 scored attempts from one pre-registered natural
`claude-tiers` batch) was not met.
`tools/live_diagnosis_trial.py` exercises `astra_diagnose` only: a different stage, for
repeated Builder report rejections, that writes no repair task. Its results never count
toward #59.

<a id="legacy-migration--opt-in-only"></a>

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
