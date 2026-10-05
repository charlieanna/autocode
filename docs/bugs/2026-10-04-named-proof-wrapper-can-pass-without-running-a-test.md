# Named-proof wrapper can pass without running a test

Date: 2026-10-04
Found during: IdleCampus Think First bugfix trial (plan `r9`)

## Symptom

A runner-attributable `node:test` wrapper that proves named regression cases by
shelling out to Vitest with `-t "<case name>"` and asserting only:

1. the child exit status is `0`, and
2. the case name appears in stdout+stderr

reports **pass** even when the filter matches no test.

## Reproduction

```sh
node -e 'const {spawnSync}=require("node:child_process");
const name="test_t9_typo_that_does_not_exist";
const r=spawnSync("npm",["--prefix","frontend","test","--","--no-cache",
  "src/app/__tests__/ThinkFirstPanel.test.tsx","-t",name],{encoding:"utf8"});
const out=(r.stdout||"")+(r.stderr||"");
console.log({exit_code:r.status, name_in_output:out.includes(name),
  wrapper_assertions_pass:(r.status===0 && out.includes(name))});'
```

Observed:

```json
{ "exit_code": 0, "name_in_output": true, "wrapper_assertions_pass": true }
```

Vitest reports `Tests 4 skipped (4)` and exits `0`. `npm` echoes the `-t`
argument in its own banner, so the name is always present in the output.

## Why it matters

This is the evidence channel a bugfix run uses to satisfy "Named-proof
honesty" — the requirement that a case which fails or cannot run must report
failure, never a silent pass. A typo in a registered case name, a rename of the
underlying Vitest case, or a filter that matches nothing all produce a green
wrapper with zero executed assertions. The wrapper then certifies nothing.

The actual fix correctness is still covered by running the focused Vitest file
directly and by an independently authored held-out test; the flaw is in the
self-certification artifact, not in the product code under test.

## Correct assertion shape

The wrapper must prove that the selected case actually executed and passed,
not merely that its name was printed. For Vitest's default reporter this means
parsing the per-case result (or using `--reporter=json` and matching the case
in `testResults` with `status: "passed"`), and failing when the case is absent
from the result set regardless of process exit code.

A cheap robust version: require the run summary line to show at least one
*passed* test and require the exact case name in a `✓`/`✔` result line, or
switch to `--reporter=json` and assert the case is present with pass status.

## Scope

Not fixed here. This note records the defect so the verification helper is not
trusted as-is in future trials.
