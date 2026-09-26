# Progressive Autocode Testing Plan

Status: proposed execution plan, not an executed validation report.
Prepared against the working checkout on 2026-09-26, including uncommitted work.

## 1. Objective

Establish how reliably Autocode can deliver approved work, starting with a tiny
task and progressing to an existing application with dependencies, parallel
Builders, conflicting changes, human decisions, and interrupted execution.

Test two different claims separately:

- **Runner correctness:** approval, permissions, scheduling, evidence, budgets,
  persistence, and recovery remain correct under controlled failures.
- **Delivery effectiveness:** real models produce the requested working artifact,
  within the agreed scope and budget, without a person repairing Autocode for them.

Fake-provider success proves the first claim, not the second. A correct refusal
proves a safety case, not successful delivery. An exit code of zero proves neither.

This plan does not authorize model spending, changes to another project, production
deployments, process termination, or cleanup of existing runs. Approve each live
case's fixture/project, scope, provider/model configuration, and budget first.
Follow [reliability priorities](../RELIABILITY.md); reuse suitable authorized
projects rather than creating arbitrary applications to fill the matrix.

## 2. Preconditions

Complete T00 before interpreting higher-level results.

1. Pin the Autocode revision, Python version, dependency versions, provider CLI
   versions, and operating system. Record the dirty diff and a manifest of selected
   untracked inputs when testing a working checkout. Never silently stash or commit
   someone else's work to obtain a baseline.
2. Use the project interpreter with `psutil` installed. The earlier system-Python
   run that failed to import `psutil` is not a valid current-suite baseline. Neither
   that run nor historical audit counts establish today's pass/fail status.
3. Give each case a committed, reproducible seed repository and its own registry,
   temporary directory, artifact root, ports, database, and fixture identities.
   Use disposable test roots, not this checkout or a user's active project.
4. Keep the independent acceptance oracle outside Builder-writable paths. Protect
   it and hash it before and after execution; location alone is not protection.
   Holdout inputs must test disclosed requirements, not introduce secret scope.
5. Validate the oracle on a known-good reference and intentionally broken
   artifacts. A broken implementation and a weakened candidate test suite must
   fail even if the candidate's own tests exit zero.
6. Offline runs use verified fake executables or mocked transports and must make
   zero hosted-model requests. Allow required loopback servers, not unrestricted
   network access. Record actual executable identities and launch counts.
7. Before crash/timeout tests, prove that cleanup targets only that fixture's
   recorded processes and descendants, checks process identity, and leaves an
   unrelated sentinel process untouched. Never use host-wide name-based killing.
8. Capture the parent checkout before and after. Autocode-owned run artifacts may
   change, but protected source, unrelated dirty files, global auth/config, and
   other worktrees must not. Tool permissions are not an OS sandbox.

### Current harness blockers

These are test-infrastructure findings to resolve or isolate, not completed fixes:

| Finding | Required handling |
| --- | --- |
| `tools/test_autoreview_products.py` defaults to a real Codex executable without an explicit live opt-in. | Do not run unrestricted `unittest discover -s tools -t .` as an offline suite. Introduce a genuine live opt-in or separate live suite before restoring broad discovery. Unsetting an environment variable alone does not disable this module's default. |
| The blackbox fixture can use either `BUILD_AUDIT_LIVE_CODEX` or `REVIEW_AUDIT_LIVE_CODEX`. | Unset both for offline runs and verify fake launch identities. The build-audit entry point's single-variable guard is not sufficient on its own. |
| `test-scenarios/run-all.sh` includes crash and mutation scenarios, writes a shared `/tmp/autocode-results.tsv`, and scenario 03 uses a broad `pkill` pattern. | Quarantine blanket execution until process cleanup and result paths are case-local. Do not run it alongside active user tasks. |
| The shell harness treats any nonempty `AUTOCODE_LIVE`, including `0`, as live and can prefer an installed CLI over source. | Unset the variable for offline use; explicitly verify executable provenance before admitting this harness. |
| Live-trial scoring previously classified completion with failed oracle checks as `HONEST_BLOCKER`, then returned success. | Corrected with scorer/report/exit-code regressions in `test_live_trial.py`: completion plus a failed check is `FALSE_COMPLETE` (exit 1); a genuine blocker stays `HONEST_BLOCKER` (exit 2), never PASS. Inspect raw state/checks as well. |
| The live driver uses `--accept-review`, while the current CLI supports `--approve-review ID --review-token TOKEN`. | Repair and test the human-review path before qualifying those cases. No automatic human-review approval. |
| Some live profiles select identical/disallowed model pairings under current cross-model verification. | Validate the saved role map against the current routing policy before launch. Do not disable independence guards to make a profile run. |

`live_trial.py`, its profile/scenario helpers, and `test-scenarios/` are versioned.
Review harness changes like product changes before using them as release evidence.
The live driver registers `LIVE-01`, `LIVE-02`, `LIVE-05`, `LIVE-06` and the
[task-type scenarios](scenarios.md) `BUGFIX-01`, `FEATURE-01`, `ARCH-01`,
`PROGRAM-01` and `UI-01`; its fake provider implements the greeting case only. A
scenario name, especially one beginning with `LIVE`, does not establish live-model
coverage or that a scenario is currently runnable; only a recorded bundle does.

## 3. Complexity Ladder

T00-T11 below are **plan identifiers**, distinct from the historical catalogue's
same-numbered groups and not arguments implemented by an existing runner.
Examples define bounded fixture tasks; freeze exact criteria and exclusions
in a case manifest before execution. Start with serial execution, then add
concurrency deliberately rather than letting it obscure the basic baseline.

| Level | Task to give Autocode | Independent acceptance checks | Additional runner behavior |
| --- | --- | --- | --- |
| **T00: Environment and harness** | No product task yet. Verify source CLI, fake transport, test environment, scorer, and packaged installation. | Imports and help work; positive oracle control passes; broken/forged controls fail; fake-only runs emit no hosted requests; schemas/assets/configs exist in the installed wheel. | Dry-run and status cannot authorize work; protected state/source remain unchanged; evidence includes executable and revision identity. |
| **T01: Tiny CLI** | Build a small greeting CLI from a precise brief, using the existing FX01 specification if reusing that harness. | Exact stdout/stderr and exit behavior for normal input, missing input, whitespace, Unicode, and help as specified; only agreed deliverables; no unnecessary dependency. | Full requirements -> plan -> exact approval -> build -> independent validation -> completion flow. No source implementation before approval. |
| **T02: One bug in existing code** (`BUGFIX-01`) | Fix a seeded blank-name validation bug in a committed CLI without changing valid-input behavior. | The new regression fails on the seed and passes on the candidate; existing tests still pass; invalid input cannot silently succeed; unrelated files remain unchanged. | The task remains a bug fix, not a rewrite; the original defect and tested source are traceable in evidence. |
| **T03: Feature in an existing project** (`FEATURE-01`) | Add case-insensitive tag filtering to a seeded notes CLI while retaining its existing commands and output contract. | Matches, no matches, mixed case, empty/invalid filters, and old commands; tests start with realistic saved notes; existing behavior and stored data remain intact. | Planner identifies affected files and compatibility constraints; all roles receive the same approved requirements. |
| **T04: Multi-module application** | Build a local persistent task CLI: add, list, complete, and delete tasks across storage, domain, and CLI modules. | Restart persistence, stable IDs, duplicate input policy, invalid commands, missing records, and failed-write behavior; approved error handling must not corrupt previous data. | Several dependent milestones execute in order; a seeded failed check produces bounded rework and independent retesting. |
| **T05: API and database** | Expose a seeded task store through a local HTTP CRUD API using the fixture's chosen stack. | Status codes, request validation, pagination boundaries, missing records, persistence after restart, transaction failure, and duplicate/concurrent requests according to the contract. | Test-server lifecycle is owned and bounded; green unit tests cannot replace HTTP/database integration evidence. |
| **T06: Full-stack user flow** | Add a responsive browser interface to that API for create, edit, complete, filter, and delete. | Real browser -> HTTP -> database flow; refresh persistence; empty/loading/error states; keyboard operation and labels; declared desktop/mobile viewports; user text renders safely. | Exercise both artifact behavior and Autocode's dashboard approval/status flow. Screenshots or DOM stubs alone do not prove end-to-end behavior. |
| **T07: Migration and compatibility** | Add task priority to an existing application and migrate a populated older database without changing its existing API contract. | Fresh install, upgrade of a frozen old database, repeated migration, induced interruption, data invariants, preserved IDs, and the agreed backup/recovery procedure. | No destructive reset to make tests pass; permission or requirement changes stop for a decision; evidence covers both old and new data paths. |
| **T08: Parallel dependency graph** | Deliver a contract/schema prerequisite, then independent API and CLI/UI work, then an integration milestone; use disjoint ownership and criteria for the parallel batch. | Same acceptance outcomes with concurrency 1 and 2; prerequisite must finish first; combined output passes integration tests; successful siblings are retained after one worker fails. | Verify actual overlapping worker lifetimes, separate worktrees/sessions, dependency order, ownership, and bounded selective retry. Configuring two workers is not proof of concurrency. |
| **T09: Conflicting integration** | Extend a mature fixture where two planned changes overlap a shared schema or file; separately inject an unexpected worker overlap and a changed parent baseline. | Planned overlap is serialized; undeclared overlap or stale integration pauses without losing either change; unrelated parent edits survive; approved recovery eventually passes combined tests. | No forced overwrite, automatic approval, or automatic merge into the user's default branch. Check both the expected-block variant and a successful recovery variant. |
| **T10: Evolving requirements** | Start with an intentionally ambiguous workflow, answer bounded questions, then request a scoped change after one milestone is accepted. | Contradictions are surfaced; saved answers remain available; the old approval token fails after revision; partial work is preserved; current requirements and the entire flow are revalidated. | Feedback is applied once at a safe boundary; revised scope requires fresh approval. Carry-forward is allowed only where the existing supported identity/evidence rules permit it. |
| **T11: Complex capstone** | Deliver an agreed change to a realistic existing issue tracker: roles and project isolation, issue/comment workflow, search, a migration, and a local background outbox, across roughly 6-10 bounded milestones. | Admin/member/read-only journeys; authorization isolation; database upgrade; API/UI contracts; safe text rendering; duplicate/retried outbox delivery; deterministic dataset and load targets; full regression and human UX acceptance. | Mix serial prerequisites, safe parallel work, one worker failure, one controlled interruption, scoped feedback, and final independent whole-project validation. Use local service doubles; no production accounts, mail, payments, or deployment. |

Each level needs its own frozen seed, not whatever a previous model happened to
generate. Use a separate longitudinal run to test continuing an actual delivered
project. A complex application's acceptance criteria must be executable and
bounded; "production ready" or "handle scale" is not an acceptance test.

## 4. Common Case Protocol

1. **Freeze the case.** Record the exact user request, seed commit, allowed paths,
   exclusions, criterion IDs, oracle version/hash, expected outcome, permitted
   human decisions, provider/model/effort map, and budgets. For existing failures,
   record exact baseline identities; only an explicit approved exception can waive
   one. Matching failure counts is not sufficient.
2. **Prove the oracle.** Run the reference implementation and defect controls.
   Include a no-op implementation, an incorrect boundary result, a weakened test,
   and forged success evidence. Preserve results outside candidate control.
3. **Exercise planning.** Start with the real CLI or dashboard, not pre-approved
   state. Answer only actual question IDs. Check that answers are retained, known
   answers are not repeatedly requested under unchanged requirements, and open
   blockers cannot disappear without a recorded resolution.
4. **Verify approval.** Present the final brief; attempt a stale token and ordinary
   resume before approval. Both must fail to authorize implementation. Record the
   human's exact current-token approval separately from execution.
5. **Observe execution.** Record launches, role/session identity, effective models,
   file ownership, milestone order, checkpoints, and worktree changes. Exercise
   the public CLI path, not only helper functions that the CLI may never call.
6. **Inject one scheduled fault.** Use a deterministic fixture hook at a named
   boundary. Avoid timing guesses. Once isolated faults pass, combine selected
   faults in T11; retain the individual reproducer for diagnosis.
7. **Validate independently.** Run the protected oracle against the actual final
   candidate revision, not an earlier worktree or only the Builder's tests. Verify
   every required criterion, full flow, unresolved findings, and human review.
8. **Check completion and restart.** Saved status, CLI, and dashboard must agree.
   An unchanged completed run must not rebuild. Changed source/evidence must
   invalidate completion rather than reuse stale acceptance.
9. **Close the case.** Record elapsed/active time, reported usage including unknowns,
   interventions, outcome, and evidence references. Stop only owned fixture
   services. Preserve failing worktrees and logs for the retention period; do not
   erase failed attempts before a retry.

Goal approval, answering a question, and operational resume are distinct actions.
A required human artifact review uses the current `--approve-review` and
`--review-token` interface. Test automation must not impersonate a real user's
acceptance during a live trial.

## 5. Reliability Matrix

Apply these dimensions to representative levels, not every possible combination.
All safety rows require zero violations. An expected pause is a pass only for a
case explicitly declared to test that pause; it is not a delivery pass.

| ID | Fault or variation | Required result | Start at |
| --- | --- | --- | --- |
| R01 | Missing, stale, or edited approval; resume without approval | No Builder launch or source implementation; exact current approval required | T01 |
| R02 | Wrong output with green candidate tests; fabricated command receipts; missing criterion; forged COMPLETE; premature operator completion | Independent oracle fails; no accepted completion; `--accept-completion` cannot waive gates; false-completion scorer sentinel fails the campaign | T01-T02 |
| R03 | Malformed, wrapped, truncated, or report-only repaired response | Bounded repair or honest pause; preserve original report; no implementation replay or findings closure from format-only repair | T02 |
| R04 | Process crash before checkpoint, after accepted result, and around intervention acknowledgement | Atomic recoverable state; no replay of accepted work; preserve partial work; apply receipt once; reconcile uncertain attempts explicitly | T02, T04 |
| R05 | Quiet healthy tool, hung tool, idle provider, noisy duplicate events, detached child | Distinct bounded watchdog behavior; chatter cannot extend deadlines; stop owned children before releasing writer lock | T02 |
| R06 | Quota/auth failure, timeout, missing usage, changed tool/config/model route | No unauthorized billing route or silent provider swap; explicit bounded recovery; saved limits and identities remain authoritative | T02-T03 |
| R07 | Exhausted serial Builder, explicit resume, next milestone; repeat for a parallel worker | Exhaustion cannot be bypassed through the live CLI; retries are bounded and recorded; per-task policy/model state resets only as specified | T04, T08 |
| R08 | Repeated identical failure, no-op edits, renamed tasks, oscillating checks | No-progress/replan limits hold; a changed approach or explicit decision is required; no infinite spending loop | T04 |
| R09 | Feedback/pause accepted before versus after provider admission; duplicate/conflicting request IDs | Correct ordering and exactly-once receipt application; no extra admission before a prior request is consumed; reapproval after scope change | T03, T10 |
| R10 | Validation from a different task/source/contract; changed file mode, evidence, or human-review token | Reject stale acceptance; preserve provenance; require current independent verification | T04, T07 |
| R11 | Overlapping ownership, unmet dependency, failed sibling, parent drift | Serialize or pause safely; preserve valid siblings; integrate only against the pinned baseline | T08-T09 |
| R12 | Dashboard refresh loss, stale snapshot, double click, missing pending question, server restart | No misleading COMPLETE or duplicate action; actionable pending state; disabled unsafe controls; canonical state reconciles after recovery | T01, T06 |
| R13 | Missing compiler/browser/credentials; denied file/tool access | Explicit environment/permission blocker, not success; no broadened permission or changed acceptance criteria | T05-T07 |
| R14 | Defaults, overrides, escalation, and resume with allowed versus disallowed role pairings | Validate actual launched models and separate role sessions; reject prohibited pairings before launch; never reuse a session across incompatible engines | T03, T08 |
| R15 | Dirty parent checkout, symlink/path traversal, executable mode/submodule change, concurrent writer | Preserve unrelated work; reject invalid boundaries; retain source identity; enforce one writer per workspace | T03, T07 |
| R16 | Growing history, long prompts, repeated planning exploration, many findings | Requirements survive compaction; bounded useful planning/rework or an explained stop; record prompt size, stage latency, and progress | T10-T11 |

Prioritize regressions for the recently observed command-provider
`recover_wrapped` forwarding defect and the suspected serial Builder policy
integration gap.
Direct parser/guard tests are insufficient: exercise the command adapter and the
actual CLI resume path. Also test macOS process-enumeration fallback without
weakening process-identity checks.

## 6. Existing Coverage

These are starting points, not assertions that current tests pass or that all
cases above are already implemented. The
[catalogue map](../audits/autopilot-test-catalogue/coverage-map.json) is historical.

| Area | Existing modules or harness | Additional evidence needed |
| --- | --- | --- |
| Planning and approval | `test_goals`, `test_planning`, `test_planner_invariants`, `test_requirement_conflicts`, `test_requirements_coverage` | Live interviewing quality; stable scope across long handoffs; public-CLI stale approval and answer/revision journeys |
| Units and serial execution | `test_units`, `test_command_flow`, `test_subprocess`, `test_builder_policy` | Serial exhaustion/resume integration and one authoritative live dispatch path |
| Scheduling and workspaces | `test_dispatch`, `test_orchestrator`, `test_task_workspaces`, `test_tasks`, `test_carryforward` | Measured concurrency, conflicts, selective retry, and final combined acceptance at T08-T11 |
| Review and completion | `test_findings`, `test_findings_controller`, `test_runtime_reports`, `test_final_workflow`, `test_resolver_unit`, `test_resolver_runtime` | Protected product oracles, false-completion controls, and actual adapter forwarding |
| Checkpoints and recovery | `test_process`, `test_activity_runtime`, `test_execution_checkpoints`, `test_report_repair`, `test_milestone_checkpoints` | Deterministic process-level crash windows, including repair-internal boundaries; do not equate manually seeded checkpoints with full crash exploration |
| Interventions | `test_interventions`, `test_intervention_ordering` | End-to-end CLI/dashboard races with accepted receipts and restart |
| Providers and role routes | `test_command_provider`, `test_provider_registry`, `test_opencode`, `test_opencode_routing`, `test_all_role_models`; WIP `test_cross_model` | End-to-end launch identities across defaults, overrides, escalation, and resume; allowed model-tier combinations as well as negative cases |
| Dashboard | `tools/dashboard/tests/test_*.py`, `test_*.js`, browser fixtures | Count node/DOM-stub checks separately from real browser tests; desktop/mobile, keyboard, stale-state, and human-review acceptance |
| Product fixtures | `tools/run_build_blackbox_audit.py`, `tools/build_product_fixtures.py` | Reuse approved fixtures for defect controls; fake success is not live-model delivery |
| Live delivery | WIP `tools/live_trial.py`, `live_profiles.py`, `live_scenarios.py` | Harness/scorer fixes, versioned profiles, supported scenario adapters, then bounded authorized trials |

Preserve reported coverage gaps as unverified until tested. In particular, catalogue
notes defer some repair-internal checkpoints, dashboard failure injections,
browser-state matrices, and forced-colors checks. A document marked DONE is not
evidence those deferred cases passed.

Catalogue mutation/replay checks must operate on disposable source copies. Do not
reuse historical `git checkout` replay recipes against the working checkout.
Some catalogue checks also consume evidence from earlier groups: T14 SYS-06 reads
T08 recovery bundles. Record these prerequisites and do not treat a missing bundle
as a successful isolated run.

## 7. Execution Recipes

Commands in this section are for later execution after T00 review, not a request
to run them now. Run from the repository root in a dedicated test shell. Do not
change the user's global Python, provider configuration, or active sessions.

### Environment and explicit offline suites

Verify `.venv/bin/python` is Python 3.11+ and imports `psutil`. If unavailable,
prepare an approved isolated environment from `pyproject.toml`; do not mistake
missing dependencies for an Autocode defect.

```sh
.venv/bin/python -B -c 'import sys, psutil; print(sys.version); print(psutil.__version__)'
.venv/bin/python -B tools/autocode.py --help
.venv/bin/python -B tools/autopilot.py --help
```

Create a fresh test root and a helper for the explicitly selected fixture suites:

```sh
REPO="$PWD"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/autocode-testing.XXXXXX")"
mkdir -p "$TEST_ROOT/home" "$TEST_ROOT/config" "$TEST_ROOT/tmp" "$TEST_ROOT/artifacts"

fixture_python() {
  env -u AUTOCODE_TEST_CLI -u AUTOCODE_PROVIDER -u AUTOCODE_LIVE -u AUTOCODE_BIN \
    -u BUILD_AUDIT_LIVE_CODEX -u REVIEW_AUDIT_LIVE_CODEX \
    PATH="$REPO/.venv/bin:$PATH" HOME="$TEST_ROOT/home" \
    XDG_CONFIG_HOME="$TEST_ROOT/config" \
    TMPDIR="$TEST_ROOT/tmp" AUTOCODE_HOME="$TEST_ROOT/registry" \
    AUTOCODE_TEST_ARTIFACTS="$TEST_ROOT/artifacts" \
    GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off \
    GOCACHE="$TEST_ROOT/go-cache" GOMODCACHE="$TEST_ROOT/go-mod-cache" \
    PYTHONDONTWRITEBYTECODE=1 "$REPO/.venv/bin/python" -B "$@"
}

fixture_python -m unittest \
  tools.test_autocode tools.test_goals tools.test_findings tools.test_workflow \
  tools.test_units tools.test_subprocess tools.test_planning \
  tools.test_runtime_reports tools.test_command_provider

fixture_python -m unittest \
  tools.test_builder_policy tools.test_dispatch tools.test_orchestrator \
  tools.test_process tools.test_activity_runtime tools.test_report_repair \
  tools.test_execution_checkpoints tools.test_milestone_checkpoints \
  tools.test_interventions tools.test_intervention_ordering tools.test_carryforward

fixture_python -m tools.run_build_blackbox_audit \
  --artifacts "$TEST_ROOT/build-audit"
```

The helper isolates configuration and removes known live toggles; it is **not a
network sandbox** and cannot make arbitrary test modules offline. Recheck the
allowlist when tests change. Capture complete stdout/stderr, exit status, and
artifact paths for every invocation. If using `tee`, preserve the test process's
exit status rather than the logging process's success.

For the build audit, inspect `results.json`, including `tests_successful`,
`all_scenarios_verified`, per-scenario status, skipped tests, and limitations.
Its exit code reflects unittest success, not complete scenario verification;
the current runner explicitly reports partial reasoning/browser coverage.
Missing Go or another required local tool stays blocked/skipped, with no automatic
toolchain or dependency download during an offline campaign.

After explicit live opt-in and process/result isolation are fixed, the historical
full source discovery form is `python -m unittest discover -s tools -t . -p
'test_*.py'`. The `-t .` preserves package-relative imports. This is **not an
approved offline command for the current checkout**; classify every discovered
suite before restoring it to routine use.

### Dashboard and installation

With Node and the relevant browser/loopback prerequisites available:

```sh
fixture_python -m unittest discover -s tools/dashboard/tests -p 'test_*.py'
(
  for test_file in tools/dashboard/tests/test_*.js; do
    PATH="$REPO/.venv/bin:$PATH" node "$test_file" || exit 1
  done
)
```

Record each JS program's exit status and require all selected programs to run;
a loop that stops early is not a passing aggregate suite. Missing browser binaries
are `BLOCKED_ENV`, not an accepted UI result. Run the real-browser lifecycle and
accessibility cases as well as node-only tests, with fixture servers that cannot
launch live agents. Check the supported browser's console and network failures.

The real-browser suites are
[`test_m3_lifecycle_browser_ui.js`](../tools/dashboard/tests/test_m3_lifecycle_browser_ui.js)
and [`test_shell_a11y_ui.js`](../tools/dashboard/tests/test_shell_a11y_ui.js).
They require `agent-browser`, Python fixture servers, and localhost access, and
write evidence. Verify those prerequisites before selecting them; node/DOM-stub
results must not be substituted for these rendered-browser checks.

Build a wheel in an isolated build environment and install it into a fresh virtual
environment. From a directory outside the source checkout, exercise every script
declared in `pyproject.toml`, then rerun the applicable fake-provider workflows
with `AUTOCODE_TEST_CLI` pointing to that installed `autocode`. Verify packaged
schemas, dashboard assets, provider configs, and module imports. Record source and
installed results separately; an editable install is not a wheel-install test.

### Live trials

Do not reuse the fake HOME/auth setup for live trials. Preserve the approved
provider's normal authentication, while isolating Autocode's registry, target
workspace, and evidence. Never switch billing routes to work around a quota error.

Use the normal CLI with a precise approved case brief, selected provider/role
configuration, and finite limits. Set `AUTOCODE_CLI` to the absolute executable
whose installed revision was verified for this campaign, not an arbitrary command
found on PATH. The example selects the built-in OpenCode route; substitute the
approved route and role-model/effort flags explicitly for other configurations.
Follow the printed task workspace and run path:

```sh
# Templates only: substitute the agreed fixture, paths, and displayed tokens.
"$AUTOCODE_CLI" "$CASE_BRIEF" --workspace "$SEED_WORKSPACE" --no-chat \
  --engine opencode --provider opencode \
  --max-parallel-builders 1 --max-iterations 8 --max-seconds 1800 \
  --max-stage-seconds 600 --max-tool-seconds 300 --max-idle-seconds 180

"$AUTOCODE_CLI" --workspace "$TASK_WORKSPACE" --run-dir "$RUN" --status
"$AUTOCODE_CLI" --workspace "$TASK_WORKSPACE" --run-dir "$RUN" --show-goal
"$AUTOCODE_CLI" --workspace "$TASK_WORKSPACE" --run-dir "$RUN" \
  --approve-goal "$DISPLAYED_TOKEN"
"$AUTOCODE_CLI" --workspace "$TASK_WORKSPACE" --run-dir "$RUN" \
  --no-chat --pause-after-stage
```

Answer actual displayed questions before approval. Approval saves authorization;
the subsequent invocation executes. At a saved pause, inspect state and use the
appropriate explicit resume, never a blind retry loop. Exit 2 can mean a required
decision rather than a failure; exit 0 can mean only that an action was saved.
Use the currently documented [CLI](cli.md) and [intervention](interventions.md)
interfaces, not invented `plan`, `run`, `--doctor`, or `--accept-review` commands.

The WIP driver's fixture greeting invocation is:

```sh
fixture_python tools/live_trial.py LIVE-01 --profile fixture \
  --workspace "$TEST_ROOT/greeting" --budget-stages 40 --timeout 120
```

Use this only to diagnose/qualify the harness, not to establish live-model quality.
Check `live-trial.json` checks and the raw run state even when the process exits
zero. `--budget-stages` counts CLI invocations, not model requests; `--timeout` is
not a strict whole-trial wall-clock cap. Preserve an explicit workspace, since the
driver's implicit temporary workspace can be deleted after the run. Real profiles
require a separate approved spend decision and the driver's live authorization
flag; do not enable it merely to follow this document.

Do not overstate the registered scenario oracles: `LIVE-01` currently checks
output substrings rather than all exact-output/edge cases above; `LIVE-05` checks
Go golden vectors, not executed C# parity; `LIVE-06` checks DAG declarations and
artifact presence, not actual concurrency or integrated artifact behavior.
Extend and qualify the relevant oracle before crediting the stronger plan case.

## 8. Matrix and Budgets

Qualify one pinned primary provider/model configuration through the ladder first.
Then run representative cross-provider cases rather than multiplying every case by
every model and frontend:

| Dimension | Minimum qualification |
| --- | --- |
| OpenCode and Codex | T01, T03, T04 rework/recovery, and T08 on each supported engine configuration |
| KiloCode command provider | T01 plus event/report recovery, resumed session identity, read-only-stage mutation detection, and T04 |
| Other registered tools, including a Claude-based tool if configured | Verify adapter/config support first; qualify the same representative cases. Do not assume a bundled Claude adapter or identical sandbox/usage semantics. |
| Model assignments | Pin actual IDs/efforts; test valid defaults, explicit overrides, escalation, resume, and rejected pairings against the current independence policy |
| Frontends | Full CLI coverage; dashboard T01/T06/T10 journeys; macOS host launch, reconnect, and quit behavior on a fixture run |
| Platforms | Supported macOS and Linux; Python 3.11 and the current development version; WSL only if claimed, not native Windows by assumption |
| Optional task lanes/Figma | Separate opt-in qualification. Lanes retain independent branches and do not auto-merge; design handoffs and human visual acceptance retain their own gates. |

Proposed starting ceilings below are planning limits, not measured performance or
permission to spend. Approve final limits per case and record any revision before
another attempt. Do not silently extend them to manufacture a pass.

| Levels | Runner active-time cap | Total iteration ceiling | External wall-clock deadline |
| --- | --- | --- | --- |
| T01-T03 | 30 minutes | 8 | 45 minutes |
| T04-T06 | 90 minutes | 12 | 2 hours |
| T07-T10 | 2 hours | 15 | 3 hours |
| T11 | 4 hours | 20 | 6 hours |

Pin finite idle/tool/stage and milestone limits appropriate to the fixture. Runner
active-time limits are checked at boundaries; they are not a whole-process
wall-clock supervisor. Any external watchdog must use the case-owned process
registry and perform safe cleanup. Human-wait time is recorded separately even
when it consumes the campaign's wall-clock deadline.

Track provider-reported input/cache/output/reasoning usage and cost when available.
Unknown usage stays unknown, not zero. For transports that cannot report tokens,
do not claim an enforced token/cost cap; use approved time/iteration controls or
classify the missing required accounting as a blocker.

## 9. Results and Promotion

Record a per-trial manifest plus raw state, prompts/reports, executed command
events, oracle stdout/stderr/exit codes, code diffs, source/evidence hashes,
checkpoint and intervention history, process ledger, screenshots/browser traces
where relevant, elapsed/active time, usage, and a criterion-by-criterion verdict.
Redact credentials and private material before sharing or committing evidence.

| Outcome | Meaning |
| --- | --- |
| `PASS_DELIVERY` | Current approved scope delivered; every required independent criterion and full flow passes; required human acceptance is recorded; no blocking findings or safety violation |
| `PASS_EXPECTED_BLOCK` | A predeclared negative test reached the correct safe block, with preserved work and an actionable explanation; not counted as delivered work |
| `FALSE_COMPLETE` | Runner reports completion while a required independent check, approval, or evidence-integrity condition fails; critical stop condition |
| `FAIL` | Wrong artifact, lost work, scope/permission breach, unbounded behavior, or an unmet delivery criterion within the authorized attempt |
| `BLOCKED_ENV` | Required environment, tooling, access, or accounting is unavailable; neither product success nor proof of a product defect |
| `INFRA_ERROR` | Harness, oracle, fixture isolation, or scoring failed; invalidate the affected result and repair the infrastructure |
| `NOT_RUN` | No current evidence; never infer success from a related case or historical audit |

Also mark assistance separately: expected scope answers and approvals are normal;
manual code fixes, state edits, or repairs to Autocode are operational assistance.
A subsequently working artifact may pass delivery checks but must not count as an
unassisted successful trial. Preserve the original failure and the intervention.

Promotion rules:

- T00 oracle/safety controls must pass before any live trial is scored.
- First execute deterministic positive and negative cases; then one authorized
  live exploratory attempt. After fixes, qualify T01-T10 with three fresh attempts
  per required primary case, all passing, before promoting its tier.
- T11 needs two fresh successful deliveries, including its planned compound
  recovery exercise. These small sample counts are regression gates, not proof of
  a population success rate; report the sample size and every attempted run.
- Any approval bypass, false completion, data loss, unrelated-process kill,
  unauthorized spend/route change, or oracle tampering stops the campaign. Fix and
  rerun the reproducer plus impacted lower-level cases on a new pinned revision.
- An unexpected blocker in a delivery case does not pass the tier. Diagnose it;
  do not reclassify its expected outcome after seeing the result.
- Publish completion rate, expected-block correctness, false completions, manual
  repairs, retries, median/range of time and reported usage, with denominators.
  Do not claim meaningful tail percentiles from two or three trials.

## 10. Recommended Rollout

1. **Harness qualification:** isolate live tests, fix false-completion scoring and
   human-review invocation, replace broad process cleanup, and pin source/profile
   provenance. Establish a fresh offline baseline with the correct interpreter.
2. **Minimum product confidence:** T01, T02, and T03, including approval, failed
   verification, rework, interruption/resume, and dashboard status. These map to
   the new-application, existing-feature, and bug-fix live evidence required by
   `RELIABILITY.md` once actual case scopes are approved.
3. **Integration confidence:** T04-T07, real browser/database oracles, migration
   protection, installed-wheel testing, and representative provider parity.
4. **Orchestration confidence:** T08-T10, disjoint and overlapping ownership,
   selective retry, feedback races, and long-running context/evidence integrity.
5. **Complex delivery qualification:** T11 plus the bounded repeat campaign.
   Publish current results and unresolved gaps in validation records; do not
   declare dependable complex-project delivery based on fake fixtures alone.

See also: [testing guide](testing.md), [workflow](workflow.md),
[execution and recovery](execution.md), [providers](providers.md), and
[dashboard verification](dashboard.md#dashboard-verification).
