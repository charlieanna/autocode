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
the base run decided it (either run has no per-test results), nothing else has
already failed the proof, and the candidate changed a package definition
anywhere in the tree, `verify` runs the suite once more on the candidate's code
with the original definitions and the original tests put back
(`tools/autocode_original_scripts.py`). The suite command is never parsed.

- **Definitions** are `package.json` and package-manager or monorepo-runner
  configuration: `.npmrc`, `.yarnrc`, `.yarnrc.yml`, `.pnpmfile.cjs`/`.mjs`,
  `pnpm-workspace.yaml`, `bunfig.toml`, `turbo.json`, `nx.json`, `lerna.json`,
  and Yarn's `.yarn/releases/` and `.yarn/plugins/`, which `.yarnrc.yml` names
  (`yarn set version` swaps both). turbo, nx and lerna decide which package
  scripts run, and when a cached result is replayed (the cache lives in the
  linked `node_modules`). None of these is code the product loads. A file that a
  definition-named link points at (`.npmrc -> config/npmrc`) counts as that
  definition. Definitions under test paths count too (a workspace member in
  `tests/`); they return whole, like any test file.
- **A changed `package.json`** keeps the candidate's fields that say what the code
  is, how it loads and what it installs (`type`, `main`, `exports`, `imports`,
  `bin`, `types`, dependencies, `overrides`, `engines`, `babel`, `browserslist`,
  `version` and other metadata). Every other field comes from base: `scripts`,
  `config`, `workspaces`, `wireit`, `name`, `files`, `packageManager`, runner keys
  such as `jest` or `mocha`, and any custom field a runner might read. One that
  is not a JSON object is put back whole.
- **Configuration** is put back whole: an added file is removed and a deleted
  one comes back.
- **An added `package.json`** is removed unless it holds only kept fields (a
  nested `{"type": "module"}`). It defines a package the original suite never
  ran: its scripts would run in `npm test --workspaces` (a new member's `test`
  could delete another package's old test), and in `cd web && npm test` it
  would stand in for the parent's scripts. Without it the folder drops out of
  npm and pnpm workspaces and npm finds the parent's `package.json`, as on base.
  On a base with no `package.json` at all the original suite does not run
  either, so that run fails as the base did and the proof is UNVERIFIED.
- **A deleted `package.json`** comes back while the candidate keeps files in its
  folder. When the whole folder is gone the package was removed, not narrowed,
  and the manifest stays deleted.
- **Tests** are as the original has them, so the run is the original suite:
  existing test files the candidate changed or deleted come back (files and
  links), as the protected-test gate puts them back (docs/protected-tests.md),
  and added tests are left out. They already ran in the candidate's own suite,
  and a new test that needs a changed template, helper or fixture would fail
  here for that reason alone. That gate reruns the original tests under the
  candidate's own definitions, so an edited old assertion together with a
  narrowed suite would otherwise pass both checks.
- **A test script changed together with a file it runs** proves nothing either
  way: the run would pair the original script with the new file, which existed
  on neither side. When a script whose name says `test` (pre and post hooks
  included) changed and so did a non-test file its original text names
  (`node run-tests.js`, `node build.js`), or test-runner configuration beside
  that `package.json` (`jest.config.*`, `vitest.config.*`, `.mocharc*`,
  `ava.config.*`, `.c8rc*`, `.nycrc*`, `.taprc`, `karma.conf.*`), the proof is
  UNVERIFIED without the extra run, naming the file and the script.
- The original is the run's base with any reviewed patch applied, read through
  a private index, so a follow-up that puts back the definitions the reviewed
  change replaced is caught. For an in-place run the base is the checkout as the
  run started (#575: HEAD plus a commit of its uncommitted and untracked files),
  so a `package.json`, `.npmrc` or test that was only in the working tree at
  launch is original: put back when the candidate changes it, never treated as
  added, and its suite is the one the base run ran. The candidate's files are
  the paths `changed_files` reports against that base, read from the workspace.
- The tree is built like the candidate's, with the same dependencies, ignored
  inputs and timeout, but in a temporary folder outside the workspace, and
  removed afterwards. `regression.prove` keeps its run folder
  inside the workspace, and there Yarn 1 (which reads `.npmrc` and `.yarnrc` from
  every parent folder) and pnpm (which takes settings from the nearest parent
  `pnpm-workspace.yaml`) would still apply the candidate's configuration. The
  restored files stay in the run directory under `original-definitions/`.

The run is the check `suite_with_original_definitions`:

- When both it and the base run report per-test results, they are compared as
  the suite is: a base test that is missing or fails is FAIL, even with exit 0.
- Otherwise its exit code decides. It passes: preservation is proven, with a
  note. A widened script is PASS.
- It fails and the base suite passed: FAIL, "The project suite as originally
  defined passes on base but fails on the candidate", naming what was restored,
  as for an unchanged script, and telling the builder to keep the files the
  original scripts run and the code working without the changed scripts, config
  or test-runner settings.
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

On master eb00408, with git, Node 22.22.0, npm 10.9.4, Yarn 1.22.22 and pnpm
10.28.0 through `verify.baseline` + `verify.verify(new_behavior=True)`: every
case above was PASS (a widened script UNVERIFIED), and so was a deleted
workspace member's `package.json`. With the reviewed patch's widened script put
back by a follow-up, and with an edited old assertion plus a narrowed script, it
was PASS or UNVERIFIED. Two adversarial reviews of the first version of this
change found more, each reproduced and now covered: a new `.yarnrc`, `.npmrc` or
`pnpm-workspace.yaml` still applied through `regression.prove`'s run folder; a
narrowed root runner with a changed test script; a per-test run that lost an old
test behind a new script; a workspace member under `tests/`; a new workspace
member whose `test` deletes another package's old test; an edited target of a
linked `.npmrc`; and false refusals for a new test that needs a changed template,
helper or fixture link, `yarn set version`, a removed workspace package and a
moved build step (now UNVERIFIED, naming it).
`tests/test_package_suite_proof.py` (real git and npm, Yarn 1 and pnpm when
present) covers each, also through `regression.prove`, and PASS for a widened
script, unrelated scripts on a correct change and those legitimate changes.
After master's #575 changed the in-place base, the same proof was checked on a
launch with an uncommitted widened script and an untracked old test: the
candidate's narrowed script is FAIL because the launch's script and test come
back, and launch files the candidate leaves alone (an untracked `.npmrc`
included) cause no second run; reading the original from HEAD instead gives
PASS there.
`tests/test_original_scripts.py` covers the restoration rules and the Git
reading, including the launch state. The controls in `tests/test_verify.py` keep their verdicts: an unchanged
script with broken behavior is FAIL, a dependency-only change is PASS without a
second run, a first project on a README-only base is PASS without one.
Rerun after merging master 0591e76, every review case gave the verdict above.
Each rule is pinned by a test: reading the original from HEAD, building the tree
inside the workspace, keeping an added `package.json` or added tests, skipping
definitions under test paths, linked definitions, Yarn releases, test links, the
removed-package rule, the script-and-file pairing, the per-test comparison, or
the guards against a decided failure, per-test results on both runs and a
document-only base each fail at least one test in these modules.

## Limits

- A file a script runs is code the candidate may change: `"test": "node
  run-tests.js"` with `run-tests.js` narrowed and the script unchanged still
  passes, and so does a narrowed file the original script reaches only through
  another file or another script whose name does not say `test`. When the
  runner is under a test path (`test/index.js`, `test/run.sh`), the original
  comes back.
- Test-runner configuration in its own file (`.mocharc.*`, `jest.config.*`,
  `ava.config.*`, `.c8rc`, `.nycrc`, `.taprc`) is not restored; only a change to
  it together with a test script is caught. Nx `project.json` targets and user
  and global npm configuration outside the tree are not restored either.
- Restoring is inherent to the proof, so the candidate's code must work under the
  original definitions: product code that starts depending on a changed runner
  key (`jest.moduleNameMapper` for a new import alias), on a new `config` or
  custom field, or a deliberate change of runner or flags, is FAIL until the
  original suite also passes on the new code. Keeping those keys would let them
  redirect the old tests.
- An added `package.json` that only marks a folder (kept fields) stays; one that
  also names a package is removed, so old code that loads files from that folder
  by relative path loads them without its `type` or `exports`.
- Existing tests are always put back as base has them: a user's
  `--revise-protected-tests` revision does not reach this run, so a revised test
  plus a definition change is FAIL when the original test fails.
- In `regression.prove` the base and candidate trees are still built inside the
  workspace, so Yarn 1 and pnpm apply the candidate's parent configuration to
  the base run too. The run here, outside the workspace, still decides: when it
  fails, a passing base makes the proof FAIL and a failing one UNVERIFIED. The
  other side of that: configuration that is not in Git (a gitignored root
  `.npmrc`) reaches Yarn 1 and pnpm in the base run but not in this run, so a
  suite that needs it is FAIL or UNVERIFIED here.
- The `make test` suite has the same flaw for its `Makefile`; that needs its own
  issue.
