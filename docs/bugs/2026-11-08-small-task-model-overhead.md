# Small-task Requirements and report-repair overhead

Requested on 2026-11-08: remove redundant Requirements work first, then reduce
report-repair overhead, retaining the existing verification strength. Starting
revision: `c168a495f3d04496b669f7c36951b6089f144350` (#2940). Implementation is
isolated on `codex/requirements-report-overhead`; the original checkout's local
documentation changes are untouched.

## Changes

- New small briefs use one ordinary Requirements pass at the configured effort.
  The existing `autocode_workflows.brief_size` supplies the size rule: at most
  120 words and eight numbered asks. Larger briefs retain the triage default;
  explicit choices and saved runs retain their recorded flow. Programmatic
  configuration without a task preserves the previous default. The ordinary
  Requirements handoff, source checks, clarification and plan approval still run.
- An initial completed report with trailing commas can be normalized without a
  model repair. The correction removes at most 32 commas outside strings, within
  the existing 512 KiB bound, and requires one complete object without duplicate
  keys or nonfinite constants. Other syntax failures, incomplete attempts,
  rejected provider output and report-only responses retain their repair path.
  Exact original bytes remain in `.response.txt`; `syntax_corrections` records
  their SHA-256 and removed Unicode offsets. Ordinary schema, provenance,
  protected-contract and execution-evidence admission remains authoritative.
- Changed-runtime CI selection retains both public CLI regressions, including
  if they are subsequently classified as slow.

Short ambiguous briefs now ask through the ordinary Requirements pass, rather
than the low-effort triage probe. This preserves clarification but can change
first-question latency. No live timing, model effectiveness, subscription-credit
or dollar-saving claim follows from these offline tests.

## Qualification

Provider-backed checks ran in the pinned Linux fake image, as an ordinary user
with the existing dropped-capability, PID, seccomp and bubblewrap boundary. The
source was archived with standalone Git metadata and extracted into Linux tmpfs;
macOS bind-mounted source was too slow for the initial CLI attempt. Host home,
credentials and network were not supplied to those checks. No live models ran
during that initial deterministic qualification. The live follow-up below is
separate evidence.

The retained local evidence directory is
`.autocode/qualification/requirements-report-overhead/`. It includes source
manifests, the fake-only Docker driver and exact selectors, complete final logs,
mutation observations and changed-line observations. Archive manifests identify
the tested source bytes; later documentation and additional pure-test changes do
not alter the tested runtime behavior.

| Check | Result | Measured test duration |
| --- | --- | ---: |
| Linux `tests.test_requirements_triage` | 20 passed; clear small request has one Requirements dispatch; ambiguity, explicit triage and opt-out retain their gates | 67.815 s |
| Linux `tests.test_report_syntax_cli` | Passed; same explicit Requirements policy on base and candidate, same exact-plan approval gate, zero repair-model dispatches | 25.072 s |
| Both CLI regressions on the starting runtime with the same final fixtures | Both failed as intended: two Requirements dispatches, and a model report-repair dispatch | 36.981 s |
| `tests.test_report_syntax` | 14 passed, including configured report files, terminal OpenCode output, Claude rejection, package imports and negative source/schema cases | 0.127 s |
| Mocked report repair, report source/identity/edits, repair effort, repair envelopes and run-state consumers | 173 passed | 53.202 s |
| Requirements handoff/review and OpenCode interface consumers | 63 passed | 2.957 s |
| Planning configuration and focused Requirements/syntax regressions | 64 passed, including configuration without a task | 0.147 s |
| CI selection and CLI documentation | 108 passed | 0.689 s |
| Import-cycle and function-size architecture checks | 3 passed | 8.422 s |

The coverage package is absent from the host venv. The deterministic fallback
used standard-library line tracing plus the repository's existing mutant
generator; nothing was installed. A 180-test focused run observed all 97 changed
executable lines identified by bytecode line mapping across the seven changed
runtime/selection modules. No unexecuted changed executable line needed an
exception under that observation. This is execution evidence, not a claim of
complete branch or mutation coverage.

The existing `autocode_mutants.python_mutants` selection produced 20 mutants
across Requirements policy, syntax normalization, report loading and OpenCode
parsing. All 20 were killed by named tests; no collection failure or skip was
counted as a kill. The run took 44.823 s. Initial survivors exposed missing tests
for the package import, CLI help and exact byte bound; those tests were added
before the passing mutation run.

The complete architecture check has an existing failure on the starting revision:
`autopilot.py` is 1,401 lines against its 1,397-line ceiling. The same focused
failure was reproduced on an untouched `c168a495f` checkout. This change does not
edit that controller or relax the guard. The full unit suite and scenario catalog
were not prerequisites for this localized change.

## Retained unsuccessful attempts

The first bind-mounted Linux Requirements run exceeded its 180-second invocation
deadline; a direct-copy attempt was stopped before testing finished. Archived
source on Linux tmpfs supplied the final passing qualification. A diagnostic
faulthandler-instrumented invocation exited -11 and is not qualification evidence.
A mixed macOS report-repair module was interrupted when it entered a supervised
provider case; the relevant mocked class and supported Linux regressions were
then selected explicitly. Those attempt logs are retained; their complete wall
durations were not measured.

During development, a Claude-rejection assertion expected wording the provider
does not use, the first report CLI assertion omitted the fixture's clarification
dispatch, and planning consumers exposed the new dependency on a missing task
key. These were corrected and the affected checks rerun. The final report CLI
comparison explicitly pins triage on both runtimes to isolate report overhead.
Initial syntax-regression failures and a postprocessing error in the first
standard-library line observer are described in the retained attempt inventory;
they confer no passing evidence.

## Live follow-up, 2026-11-08

The owner subsequently asked why a live run had not been used and whether all
the broader cost issues were fixed. The fake-first policy made deterministic
checks sufficient PR evidence, but they did not establish actual model cost or
latency. The repository already authorizes a relevant, bounded live check; lack
of a newly requested spending cap was not a reason to stop. The live-test policy
uses uncapped test runs, explicit time limits, and the saved model routes.

One existing `bugfix-trivial` brief was driven through the public `TaskRun`
interface on runtime `fc70a9703`, with real Kilo 7.8.3 on the qualified Linux
boundary. Saved Kilo stage choices were copied unchanged into the isolated test
home; all seven resolved model/effort choices match the public saved choices.
The task had `--max-cost-usd 0 --max-seconds 900 --max-stage-seconds 300`, with a
1,200-second outer invocation timeout. Authentication stayed with Kilo; no model
or provider substitution, boundary waiver or private run-state mutation was used.

The saved routes were Requirements Opus 5.5/medium, Planner Fable 5.1/high,
Plan Reviewer GPT-6 Astra/high, Builder GPT-6.1 Sol/high, Tester Opus 5.5/medium,
Completion Reviewer Fable 5.1/high and Resolver/per-task Planner GPT-6 Astra/high.
Only Requirements ran as a task stage. Sol, Fable and Astra were called by model
preflight; those probes do not establish that their task stages completed.

The reproducible driver and logs are retained under the evidence directory's
`live/` folder. Its commands were:

```sh
docker exec autocode-requirements-report-live-fc70a9703 \
  /opt/autocode-venv/bin/python /evidence/live_driver.py preflight
docker exec autocode-requirements-report-live-fc70a9703 \
  /opt/autocode-venv/bin/python /evidence/live_driver.py start
docker exec autocode-requirements-report-live-fc70a9703 \
  /opt/autocode-venv/bin/python /evidence/live_driver.py start-native
docker exec autocode-requirements-report-live-fc70a9703 \
  /opt/autocode-venv/bin/python /evidence/live_driver.py resume
```

| Attempt | Result | Measured invocation duration |
| --- | --- | ---: |
| Test project on macOS bind mount | `PAUSED_TOOL_CONTAINMENT` before Requirements: verification-copy Git setup reported `fatal: not in a git directory` | 281.997 s |
| Same brief in fresh native Linux tmpfs project | Requirements accepted in one full pass; Planner's model-free Python toolchain startup check timed out after 60 s, before its model launch | 876.850 s |
| One public resume of that same native run | Accepted Requirements retained; Kilo `debug config` timed out after 30 s; public status reconciled to `PAUSED_CONTROLLER_EXIT` | 134.301 s |

A model-free diagnostic reproduced the Git setup failure on the bind mount and
passed on `/tmp`; it justified the fresh native-filesystem attempt. The final
resume was bounded to one attempt because the same qualified Python toolchain
had passed for Requirements, and no uncertain Planner model call would be
replayed. Its decision and unchanged limits/routes are retained in
`live/resume-decision.json`. No further paid retry was launched after it failed.

The native run is
`20261109-025213-pager-page-count-11-5-returns-2-but-eleven-items-4e3c33a5`.
Its public accounting identifies the single `requirements_gather` stage on
`anthropic/claude-opus-5-5`, started `2026-11-09T02:59:58.327191+00:00`, finished
`2026-11-09T03:03:41.304044+00:00`, exit 0, not rejected, followed by
`astra_discovery`. It took 222.990 s and made four provider API requests, with
70,285 cumulative input tokens (52,344 cache reads and 17,931 cache writes) and
1,798 output tokens. One stage is not one API request, and cumulative input is
not a single context-window size. This verifies the Requirements optimization's
real-provider path; there was no extra ready-only Requirements pass.

No plan was shown for approval, no approval was granted, no Builder ran and no
delivery oracle passed. End-to-end live qualification remains **blocked** by
native Kilo startup timeouts. Live coverage of the report-syntax correction was
not established; its deterministic fault and mutation evidence remains separate.

Cost is also incomplete. The first run's public known subtotal is $0.005790 and
the native run's is $0.134615; both have `complete=false` and two unpriced stages.
GPT-6.1 Sol lacks a dated price-table row, and timed-out calls lack complete token
receipts. These are accounting floors, not actual invoices or measured savings.
There is no matched paid baseline and no cost-per-delivered-task result.

The public redacted bundles are retained locally. Their manifest SHA-256 values:

- Bind-mount attempt: `c6847564e353b260fc6d34543b12fe07c6d19a120b8efe5a7f449d96e76971ef`.
- Native attempt and resume: `a585343de77741174431b922b78f787334bf660bd50cf22470b77681bd083ff0`.

## Remaining scope

The two optimizations do not resolve the broader credit-drain concern. In
particular, missing-comma reports still use the existing model repair path;
the local syntax change handles trailing commas only. Prompt/context overhead,
broader retry efficiency, enforcement during active spending and a matched live
cost comparison remain open. The pre-existing controller size failure also
remains. The live check additionally identifies native Kilo startup reliability
and incomplete usage/pricing as concrete follow-up work. Successful end-to-end
live delivery and demonstrated economic improvement are not claimed.
