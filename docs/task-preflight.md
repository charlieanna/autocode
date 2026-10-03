# Task prerequisites before paid dispatch

Start a run with `--task-preflight MANIFEST.json` to check operator-declared
prerequisites before planning, building or validation. This is an opt-in gate
for ordinary runs. Existing runs without a manifest retain their behavior.

A prerequisite runs in both the project workspace and a source copy beneath
the actual run's `.autocode/runs/.../preflight/.../scratch/candidate` directory.
The copy, linked dependencies, scrubbed environment, interpreter selection and
process cleanup come from the verification engine. This catches setup that
works at the project root but fails in the runner's nested copy. The runner
does not guess a collection command from an arbitrary test command or execute
a whole failing acceptance suite as a prerequisite.

## Basic command manifest (version 1)

Use [version 2](task-preflight-v2.md) for actual worker execution, browser
readiness, readable Figma exports and proof eligibility. Version 1 remains a
runner-environment command contract.

```json
{
  "version": 1,
  "inputs": [],
  "runtime_files": ["/absolute/path/to/browser-executable"],
  "checks": [
    {
      "id": "named-and-discovered-tests",
      "phase": "planning",
      "argv": [
        "{python}", "{runtime}/autocode_preflight_unittest.py",
        "--named", "tests.test_example",
        "--discover", "tests", "--top-level", "."
      ],
      "recovery": "Correct the module import without removing tests, then resume",
      "reuse": true
    },
    {
      "id": "browser-and-font-access",
      "phase": "build",
      "argv": ["node", "qualification/check_browser_setup.cjs"],
      "recovery": "Restore the approved local browser/fonts and resume",
      "reuse": false
    }
  ]
}
```

`argv` is a nonempty argument array, not a shell script. `{python}` selects the
repository's verification interpreter, `{workspace}` selects the current
workspace or scratch root, and `{runtime}` selects this AutoCode runtime's
directory. Both contexts must return zero. Commands are trusted operator
inputs: do not declare model-generated commands, live providers, installs,
product edits or an acceptance test whose intended baseline behavior fails.
Probe output belongs in ignored temporary paths. Source mutation stops dispatch
and preserves the workspace changes for inspection.

Each check requires a unique ID, a phase (`planning`, `build` or `validate`),
arguments and a concrete bounded recovery instruction. Builder and parallel
orchestration admission use `build`; Validator, checkpoint and completion review
use `validate`; other stage boundaries use `planning`. Declare the checks needed
for each phase; a check declared only for validation does not protect planning.

`inputs` declares public source-copy dependencies using the existing input
inventory format:

```json
{
  "path": "qualification/reference.png",
  "type": "file",
  "mode": "0644",
  "size": 1234,
  "sha256": "64 hexadecimal characters from the approved file"
}
```

`inputs` and `runtime_files` are task-wide prerequisites, checked in every phase.
Phase-specific needs can be checked by that phase's explicit command instead.
The file must match in both roots, including mode and hash. A matching ignored
file omitted from the source copy blocks dispatch with its path and remedy.
Store public proof assets in a non-ignored location; do not copy private run
state to satisfy this check. `runtime_files` identifies external capability
files whose current contents/metadata must invalidate receipt reuse. This does
not copy them or approve their use by a provider.

A zero model/tool limit no longer makes a version 1 prerequisite unbounded:
it uses a separate 120-second prerequisite timeout. No model route or stage
budget changes. Version 2 declares a positive finite timeout per check.

## Collection helper

`autocode_preflight_unittest.py` loads named modules and/or discovery in separate
interpreter processes, so discovery's `sys.path` additions cannot hide a broken
named import. It rejects loader errors, empty collections and duplicate test
identities. `--expected-ids inventory.json` additionally requires an exact
operator-approved identity list for each selected mode, for example
`{"named": ["tests.test_example.Example.test_a"], "discovery": [...]}`. Hash-bind
that inventory through `inputs`; retain the canonical suite's declared scope
and exclusions. This helper neither chooses a smaller suite nor runs methods.
`--exclusions exclusions.json` audits collected discovery IDs mapped to nonempty
reasons without removing their identities. Paired
`--setup module:function --teardown module:function` hooks check the approved
fixture lifecycle independently in each collection interpreter. Hook failures
are setup errors; collection still never executes test methods.

Output says `COLLECTION_READY` and `tests_executed: false`. Collection and all
other prerequisite receipts cannot establish test PASS, fail-to-pass proof,
visual fidelity, independent acceptance or task completion. Existing proof and
acceptance gates still execute independently.

## Receipts, reuse and recovery

`--status` / `TaskRun.status()` exposes `task_preflight`, including phase,
`READY`/`BLOCKED`, errors and the retained receipt path/hash. During execution,
the existing `runner_check` view identifies `task_preflight` and the probe log.
The sole writer of the `task_preflight` state record is
`autocode_task_preflight.guard`; status reads it without establishing proof.

Reuse requires the same phase and unchanged source snapshot, manifest, runtime
Python sources, selected interpreter/executable identities, repository venv
package records, scrubbed environment, declared inputs/runtime files, saved
model routes, verification commands, limits and design-manifest identity. The
receipt itself must still match its hash. Failed checks are never reused. A
phase change, changed bindings or tampered receipt triggers fresh execution.
Observable file/environment bindings do not reveal every dependency or OS
permission change: declare relevant external files and use `reuse: false` for
volatile capability/remote access probes.

On failure the run stops as `PAUSED_TASK_PREFLIGHT` before the affected provider
dispatch. Inspect the exact log, apply an authorized bounded setup correction,
then use the ordinary `--resume-paused` operation. To correct the manifest
itself, supply `--task-preflight NEW.json --resume-paused` at that exact stopped
boundary, with no active/uncertain stage or runner check. The CLI saves the old
and new manifests in a user event; historical failure receipts remain. It does
not install dependencies, alter pins/caps, approve a plan or weaken test gates.

## Scope of readiness

Version 1 checks run in the runner test environment. Version 2 explicitly
selects runner or worker execution; its status distinguishes
`worker_permissions_checked` from native `provider_sandbox_attested`.
The status view includes per-check receipts and design provenance. Readiness
is never inferred from a login, a binary on PATH or a collected test name.
It does not establish visual fidelity, race-free tests or candidate correctness.
