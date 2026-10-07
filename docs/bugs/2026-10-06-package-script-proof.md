# A narrowed package suite could still pass the proof (#528)

`npm test` (and Yarn, pnpm, `node --run`) runs each scratch tree's own
`package.json` and package-manager configuration. The suite command text is
the same on base and candidate, the command reports no per-test results, and
so a green exit was treated as preservation. A candidate could point
`scripts.test` at only its new test and hide a broken old one.

#570 compared the root `package.json`'s `scripts[name]` for the script a bare
npm/yarn/pnpm command names, and gave UNVERIFIED when it differed. Reproduced
afterwards with real npm and pnpm (issue comment "Follow-up input for #570"),
these still gave PASS with `add` broken: a script the test script reaches
(`npm run test:unit`, `node --run test:unit`), a new `pretest`, a narrowed
workspace package (`npm test --workspaces`, `pnpm -r test`), `config` or a custom
field a runner reads, a new `.npmrc` (`script-shell=/usr/bin/true`,
`node-options=--test-name-pattern=mul`), and wrapped or relocated commands
(`timeout 120 npm test`, `sh -c 'npm test'`, `node --run test`,
`cd web && npm test`). A script that was only widened gave a false UNVERIFIED.

## The proof now runs the original suite

When the project suite passes on the candidate and no per-test comparison with
the base run decided it (either run has no per-test results), and the candidate
changed a package definition anywhere in the tree, `verify` runs the suite once
more on the candidate's code with the original definitions put back
(`tools/autocode_original_scripts.py`). The suite command is never parsed.

- **Definitions** are `package.json` and package-manager or monorepo-runner
  configuration: `.npmrc`, `.yarnrc`, `.yarnrc.yml`, `.pnpmfile.cjs`/`.mjs`,
  `pnpm-workspace.yaml`, `bunfig.toml`, `turbo.json`, `nx.json`, `lerna.json`.
  The last three decide which package scripts run, and when a cached result is
  replayed (the cache lives in the linked `node_modules`). None of these is code
  the product loads. Files under test paths are test data, not definitions.
- **A changed `package.json`** keeps the candidate's fields that say what the code
  is, how it loads and what it installs (`type`, `main`, `exports`, `imports`,
  `bin`, `types`, dependencies, `overrides`, `engines`, `babel`, `browserslist`,
  `version` and other metadata). Every other field comes from base: `scripts`,
  `config`, `workspaces`, `wireit`, `name`, `files`, `packageManager`, runner keys
  such as `jest` or `mocha`, and any custom field a runner might read. One that
  is not a JSON object is put back whole.
- **Configuration** is put back whole: an added file is removed and a deleted
  one comes back. **A deleted `package.json`** comes back. **An added
  `package.json`** stays: it is a new package, and a workspace member without its
  `test` script would make `npm test --workspaces` fail.
- **Existing test files** the candidate changed or deleted are put back too, as
  the protected-test gate does (docs/protected-tests.md). That gate reruns the
  original tests under the candidate's own definitions, so an edited old
  assertion together with a narrowed suite would otherwise pass both checks.
  Added tests stay and run alongside.
- The original is base with any reviewed patch applied, read through a private
  index, so a follow-up that puts back the definitions the reviewed change
  replaced is caught. The tree is built like the candidate's, with the same
  dependencies, ignored inputs and timeout, and removed afterwards; the restored
  files stay in the run directory under `original-definitions/`.

The run is the check `suite_with_original_definitions`:

- It passes: preservation is proven, with a note. A widened script is PASS.
- It fails and the base suite passed: FAIL, "The project suite as originally
  defined passes on base but fails on the candidate", naming what was restored,
  as for an unchanged script.
- It fails and base failed too, it times out, or it cannot be prepared:
  UNVERIFIED with the reason.

When restoring changes nothing (only kept fields such as a new dependency
changed) no extra run is made. A first project on a documentation-only base has
nothing to preserve and is not rerun. A suite with per-test results on both runs
(pytest, unittest, Go, `node --test`, Vitest through `npm test`) never needs the
run: a base test that no longer passes on the candidate already fails it. Any
other exit-code suite, `make test` included, may reach npm, so it gets the run
whenever a definition changed; that costs one more suite run only then.

`regression.prove` reaches the same verdicts, through `verify`.

## Evidence

On master eb00408, with git, Node 22.22.0, npm 10.9.4 and pnpm 10.28.0 through
`verify.baseline` + `verify.verify(new_behavior=True)`: every case above was
PASS (a widened script UNVERIFIED), and so was a deleted workspace member's
`package.json`. With the reviewed patch's widened script put back by a
follow-up, and with an edited old assertion plus a narrowed script, it was PASS
or UNVERIFIED. `tests/test_package_suite_proof.py` (real git and npm, pnpm when
present) now gives FAIL for each, and PASS for a widened script and for
unrelated scripts on a correct change, also through `regression.prove`.
`tests/test_original_scripts.py` covers the restoration rules and the Git
reading. The controls in `tests/test_verify.py` keep their verdicts: an unchanged
script with broken behavior is FAIL, a dependency-only change is PASS without a
second run, a first project on a README-only base is PASS.

## Limits

- A file a script runs is code the candidate may change: `"test": "node
  run-tests.js"` with `run-tests.js` narrowed still passes. When the runner is
  under a test path (`test/index.js`, `test/run.sh`), the protected-test gate
  reruns the original.
- Test-runner configuration in its own file (`.mocharc.*`, `jest.config.*`,
  `ava.config.*`, `.c8rc`, `.nycrc`, `.taprc`) is not restored; nor are Nx
  `project.json` targets, Yarn releases and plugins under `.yarn/`, or user and
  global npm configuration outside the tree.
- A `package.json` added in a directory the suite command enters that had none
  on base (`cd web && npm test`, where npm used to find the parent's) stays, so
  it can stand in for the parent's scripts.
- A deliberate change to how the suite runs (another runner, or a flag or runner
  key the new tests need) is FAIL until the original suite also passes on the new
  code.
- Existing tests are always put back as base has them: a user's
  `--revise-protected-tests` revision does not reach this run, so a revised test
  plus a definition change is FAIL when the original test fails.
- The `make test` suite has the same flaw for its `Makefile`; that needs its own
  issue.
