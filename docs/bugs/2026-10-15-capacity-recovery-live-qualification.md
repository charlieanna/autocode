# Capacity recovery: live qualification of #1071

Runtime: master `ec5e3f577d1f21caa490eb1e3b51f2de99beb6f7`, with no production
runtime changes. The remaining acceptance check for #1071 was a real-model
run continuing after an injected capacity failure. The existing fake scenario
proved dispatch and retention but deliberately could not qualify a live run.

## Scenario change

`capacity-recovery --hybrid` scripts only the first `astra_discovery` call.
The next call and delivery use the configured report-file provider. The hybrid
trace retains the recovery handoff. The oracle binds the failed archive and
retry output to public accounting, requires the same model and one recovery,
and checks the delivered CLI independently. Fake hybrid rehearsal is still
labelled fake; a natural live run without the injected fault is NOT_EXERCISED.

A real Requirements stage may ask a clarification after recovering. Later calls
must remain live, match public accounting outputs/models, and succeed without
interruption or rejection. They do not replace the required initial failed and
successful attempts. Tests reject a wrong retry output, a scripted retry and
an additional interrupted call. Existing missing-source and broken-product
controls remain.

## Bounded real-model result

On 2026-10-15, Codex CLI 0.161.0 with the existing ChatGPT login ran the synthetic
greeting task. A disposable `codex_capacity` report-file provider invoked
`codex exec --ephemeral --sandbox {sandbox}` with the assigned workspace,
model, effort, output schema and `-o {report} --json -`. It retained read-only
planning/review restrictions and used no sandbox adapter or session resume.
No global provider configuration was changed.

Routes, all medium effort:

- Requirements, Planner, Builder: `gpt-6-luna`.
- Plan Reviewer, Tester, Completion Reviewer, Investigator: `gpt-6-sol`.
- Resolver: `gpt-6-astra`.

The local harness profile `capacity-codex` selected this provider and a $3 cost
cap. The exact invocation was `.venv/bin/python .scenario-runs/issue1071-live-setup/run.py`;
its harness arguments were `run capacity-recovery --profile capacity-codex
--hybrid --i-authorize-live-model-spend --timeout-minutes 25 --max-seconds 1200
--max-stage-seconds 300 --out .scenario-runs/issue1071-live`. The setup file only
registered those explicit routes and cleared conflicting ambient OpenAI API
variables; it did not alter runtime source or saved state.

Run directory: `20261015T201118Z-capacity-recovery-capacity-codex-hybrid-hor7296c`.
The scripted call emitted a terminal capacity error without a successful report.
The next real Requirements call received retry number 1 with no changed files
and the bound archived source/events. It asked one normal usage-text question;
the scenario harness served its proposed default. Real planning, building,
Tester report repair and Completion Review followed. AutoCode returned
TASK_COMPLETE in **286.3 seconds**; all 21 independent product checks passed,
and the public canonical report check passed. The harness's plan approval and
clarification answer are harness actions, not autonomous human acceptance.

The original harness result is retained as **FALSE_COMPLETE / 22 of 23 checks**:
its recovery oracle incorrectly unpacked exactly two Requirements attempts,
although a legitimate third call followed clarification. Original result SHA256:
`ee44ae7c4f707a287c9e9557e1e2808a03e02f9c227a8056a9de0a84d36fadd4`.
After fixing and testing that oracle assumption, a separate evaluation of the
unchanged product, retained attempts and public status passed every check with
CORRECT recovery diagnosis in **0.596 seconds**, with **zero new model requests**.
The original result was not overwritten or relabelled.

Its hybrid summary also misclassified the archived scripted failure as live.
The harness now binds the archived event path to the original scripted call.
A separate recheck of the retained trace and public accounting identifies one
scripted Requirements failure and two live Requirements calls; it makes no
model requests.

The public usage estimate was $0.518166, but 10 stages were unpriced and cost
coverage was incomplete; this is not a claim of complete billed cost.

AutoCode bundle manifest SHA256:
`ec997c7602b8c1a2425531eba829ded44025d3c154a46f82c0f445994a1cd976`.
The bundle, original result and separate recheck remain ignored local artifacts.

## Deterministic verification and scope

- Recovery dispatch, status, architecture, pause-category and capacity tests:
  46 tests passed in 73.314 seconds before the clarification-oracle correction.
- Final capacity scenario and hybrid-route tests: 15 passed in 65.889 seconds.
  Ordinary fake PASS, hybrid rehearsal PASS, broken-product FALSE_COMPLETE.
- Final archive-label regression and hybrid retry: 10 tests passed in 23.052
  seconds, including a 21.6-second fake hybrid PASS.
- Both existing `HybridRunTests` consumers passed in 441.439 seconds.
- Ruff check/format and diff whitespace checks pass.

The merged one-loop, shared recovery order, named resume/serial tests and status
writer ratchet supply #1071's implementation acceptance. Individual recoverers
still own their bounds and counters; the stage-timeout budget top-up remains
live-loop-only. Changing those allowances was explicitly outside this refactor's
behavior-preserving scope. This qualification covers one capacity recovery,
not cap exhaustion or every provider's transport.
