# First interaction latency and the duplicate model roster (#727)

On master `e18cf6607d625a1c58e6011a1668c30ad50778ec`, new-run setup calls
`model_catalogue.choose`, which lists the provider's available models. Immediately
before entering the build loop, `autocode_run_actions.handle` calls `check_models`,
which lists them again. Both calls precede the first Requirements or Job recognizer
request. The second list validates all model-backed routes, including job routes
that the chooser does not display, so deleting that validation would be incorrect.

A read-only baseline probe on this host took **1.211170 seconds** for the first
native roster and **0.902396 seconds** for the second: **2.113566 seconds** total.
Both returned the same 52 models, with inventory SHA256
`379726a47a47d27993ec02478149b228566714e173a32e177a6229a4d84ac30a`.
This is one local observation, not a general provider-latency estimate. The
removed wait in that observation is 0.902396 seconds. No inference requests were
made by the probe.

The fix retains one successful roster inside the new CLI invocation and consumes
it for the immediate full route check. Neither the provider module nor saved run
state caches it. A failed roster is retried by the original hard validation;
resumes, independent invocations and subsequent routing checks still list anew.
The public CLI regression counts one actual fixture roster at launch, then removes
an offered model and confirms resume lists again and refuses before more inference.

New runs record their CLI main-entry time and first rendered question, first
approval-ready plan and first Builder job dispatch. The additive TaskRun status
fields and canonical reports preserve unknown events and legacy runs. See
[the timing contract](../task-run.md#first-interaction-timing) for the boundary,
human-wait and parallel-worker limitations. The dated live sample and initial
profile target are below; older reliability sweeps remain unknown because they
never recorded these timestamps.


## Paired real-model sample, 2026-10-10

Baseline source is master `e18cf6607d625a1c58e6011a1668c30ad50778ec`.
Candidate is the frozen `codex/first-interaction-727` branch on that base, with
runtime wheel SHA256
`756c8a1080ec93ed4d8ce12cb17d4daff7f1eb0e07b642f58c52cf3573dd8d26`;
it is not a clean-master observation. The same host used native
OpenCode **1.18.33**, Z.AI Coding Plan API authentication and
`zai-coding-plan/glm-5.3` for every configured model-backed role. Actual stages
were Requirements, Planner, Plan Reviewer and Builder; Tester, Completion
Reviewer and Resolver were not reached.

Profile `native-glm53-latency` uses joint/adaptive build planning, active/stage/idle
budgets **5,400/1,200/300 seconds**, public invocation timeout **3,900 seconds**,
Requirements/Builder medium effort and Planner/Plan Reviewer/Resolver high
effort. The workload asks for a Python standard-library `greet.py` CLI, tests and
README: one nonempty name prints exactly `Hello, NAME\n` with exit zero; no name,
an empty name or multiple names print a stderr usage line and exit two. It
requires a blocking punctuation question, answered with no exclamation mark.
The driver uses the public TaskRun interface to start, read status, answer,
show the approval-ready plan, approve its token and advance. It repeats only
`--pause-after-stage --no-verbose`; all provider/workflow/model/budget creation
settings go through `TaskRun.start_options` and remain saved.

The qualification commands were:

```sh
.venv/bin/python -B .scenario-runs/issue-727/live-qualification/record_live.py baseline --i-authorize-live-model-spend --root-released-727-live
.venv/bin/python -B .scenario-runs/issue-727/live-qualification/record_live.py candidate --i-authorize-live-model-spend --root-released-727-live
```

These local helpers and raw outputs are ignored evidence, not committed product
files. Their observer aligns launch after imports, first rendered blocking
question, first rendered approval-ready plan and owned Builder job dispatch.
Candidate's saved public status and canonical evidence have identical seven
fields, validated against external observations within 0.2 seconds. Baseline
has only external measurements; its legacy saved state is not backfilled.
Automated answer/approval time remains included. Builder dispatch does not
measure acceptance or first token; parallel dispatch is a worker-job handoff
and that worker may fail its own preflight.

| Boundary | Baseline external timestamp (UTC) | Baseline seconds | Candidate saved timestamp (UTC) | Candidate seconds |
| --- | --- | ---: | --- | ---: |
| Launch | 2026-10-10T14:56:22.193716+00:00 | 0 | 2026-10-10T15:17:56.934482+00:00 | 0 |
| First question shown | 2026-10-10T15:03:17.432274+00:00 | 415.238558 | 2026-10-10T15:22:48.185894+00:00 | 291.251412 |
| First plan shown | 2026-10-10T15:13:18.844672+00:00 | 1016.650956 | 2026-10-10T15:27:20.046636+00:00 | 563.112154 |
| First Builder dispatched | 2026-10-10T15:14:53.414307+00:00 | 1111.220591 | 2026-10-10T15:28:52.096595+00:00 | 655.162113 |

Baseline startup made **two** successful 52-model roster calls, taking
**7.288997 + 6.359634 = 13.648631 seconds**. Candidate made **one**, taking
**6.081805 seconds**, and immediate full route validation consumed that successful
inventory. The extra baseline wait was 6.359634 seconds; later separate
invocations still listed fresh inventories. The pair returned the same roster
content (observer SHA256
`944e8cb276b6536e593326bf4e0a5a5b84e002985025965c2ad054c6337eea7b`).
This observer hash and the earlier read-only probe hash use different framing.
The removed roster subprocess is the reproducible improvement. Overall latency
also varied with model responses, two baseline Planner report repairs versus
none in the candidate, and shared-host/provider load; the full before/after
difference is not a causal speedup estimate.

Both targeted trials **passed** after a real successful Builder and the same
independent oracle **4/4**, including exact normal greeting and all three
invalid-input usage responses. Delivered project tests passed (baseline seven,
candidate six). Both stopped at **PAUSED_REQUESTED**, with no task-completion
claim. Baseline duration was **1235.117090 seconds**;
candidate **781.654734 seconds**. Run directories:

- Baseline: `20261010-075622-build-a-small-python-standard-library-cli-named--c4b30ee2`.
- Candidate: `20261010-081756-build-a-small-python-standard-library-cli-named--5d277d95`.

Actual driver child waits and separately collected tool outer exits were zero.
Source bytes/modes, HEAD/index, runtime wheel, installed package files, native
executable, qualification helpers and prior evidence held before/after. Owned
baseline-source and installed package directories were read-only during live
qualification, with workspace/provider outputs writable; unchanged file bytes
and modes were checked separately from directory custody. The recorder sampled
109 baseline and 78 candidate process births, all finished, with no observation
errors or partial identities. This is a sampled-process observation, not a
complete OS process census.

Initial same-profile/workload targets are **360 seconds to question, 720 seconds
to approval-ready plan and 840 seconds to Builder dispatch**, documented in
[RELIABILITY.md](../../RELIABILITY.md). One candidate sample meets them. No
population percentile, default-profile target, multi-vendor independence or
completed-delivery claim follows from this pair. The reliability table records
only the candidate's actual saved timestamps, with zero completed deliveries;
older rows retain unknown timing fields.

## Qualification and retained failures

The focused 40 tests and canonical Ruff lint/format (1,987 files) and Mypy
(392 source files) passed. The original full run remains **FAIL**: all 405 modules,
6,183 reported tests, 403 successful modules, with 130 skip records (129 cases
and one class setup). Two signal callbacks rejected the new optional launch
keyword before their assertions; two installed-CLI cases could not obtain
psutil from the offline wheel directory. The only later source change made
those two callbacks accept the keyword, preserving their bodies and signal/exit
assertions. The exact cached psutil wheel was supplied beside the unchanged
product wheel. Both complete affected modules then passed, **85 unique tests,
zero skips**, and canonical checks passed again. Qualification is the 403
original successful modules plus those two whole-module replays; the raw full
result was not regraded, runtime was not changed and the product wheel was not
rebuilt.

The earlier fake run completed all **72** catalog entries: **70 PASS**, the
known feature-refund-window Resolver coverage gap NOT_EXERCISED, and the
live-only stuck-planner-citation SKIPPED, with four natural lane exits and a
collected outer exit of zero. It used the checkout CLI and explicitly set
`--timeout-minutes 45`: that is a **per-scenario harness wall allowance**, not an
external lane cap, and it overrides the catalog's timeout values. Supplying
`AUTOCODE_TEST_WHEEL` did not select the installed CLI. The raw success remains
recorded, but it does not establish the installed-wheel/original-budget gate.
The separate corrected replay passed against the unchanged installed
`.venv/bin/autocode`, with no timeout, step or product-budget overrides: **72 exact
catalog IDs**, **70 PASS**, and the same two disclosed gaps. All **71** admitted
scenarios retained their exact original catalog timeout/step limits and no
product-budget override. All four natural child waits and the separate collected
tool outer exited zero. The recorder sampled **2,080** process births, all
finished, with no observation errors or partial identities; source, runtime,
wheel, helpers and all previous failed/successful evidence held. This corrected
installed/original-budget run supplements the retained earlier checkout result;
it does not rewrite that run, the raw full failure or either earlier live failure.

Two earlier baseline live attempts remain **FAIL**: both reached a successful
real Requirements request, then the qualification helper repeated a start-only
flag on resume (`--single-model`, then `--workflow`). The first also recorded
seven generated archive bytecode files, retained as evidence before restoring
only those owned additions. The helper was corrected to use the complete public
`start_options` API; an actual spend-free baseline/candidate lifecycle then
passed all 12 public actions through question, answer, displayed plan, approval
and Builder, with a requested pause. That offline protocol used explicitly
simulated providers with no model calls; its adapter was not used by either
real trial and did not waive real containment. Both earlier real failures and
all setup failures remain retained, distinct from this fresh successful pair.
