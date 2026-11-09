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
credentials and network were not supplied to the checks. No live models ran.

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
