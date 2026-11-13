# Which kinds of task AutoCode completes: a survey (2026-11-13)

The catalog covers bug fixes, features, new projects, reviews, designs and discussions, mostly in
Python, with Go, Node, Vitest and Java cases. This survey ran small tasks of kinds it does not hold,
to find where a run refuses, stalls or asks the person about AutoCode's own machinery. Each task is a
one-function paging library with a one-line change, so a stop is AutoCode's and not the model's.

Method: master `6d8f4e4`, Linux cloud container. Twelve task kinds ran with the scripted
provider (`scenarios/run.py run --fake`). Eight of them then ran once on Claude Code
(`--profile claude-tiers`: Sonnet 5.5 Requirements, Planner and Tester, Opus 5.5 reviewers,
Haiku 4.5 Builder), $9.15 in all, and $12.59 more for the eight runs after a fix. Sections 5 to 7
(JavaScript) are six later runs, $10.86.

## Scripted stops that real models do not hit

A scripted run copies a reference solution and plans from the brief, so it showed where to look and
no more. A refactor, a rename and a deletion all stopped in scripted runs: the scripted Planner
writes a suite command as each criterion, never `guard:`, and the scripted Tester has no reasons for
their changed lines. On Claude models the Planner wrote `guard:` criteria naming existing tests and
all three completed. The same holds for a C project with `make test` and a shell project.

| Task | Scripted | Claude models |
| --- | --- | --- |
| Documentation only; CI workflow only; tests only | complete | not run |
| Refactor (extract a helper) | stops | complete, 131 s, $0.86 |
| Move the implementation to a new module | stops | complete, 445 s, $1.82 |
| Remove a deprecated function | stops | complete, 173 s, $1.12 |
| Bug fix, C with `make test` | stops (fixture reasons) | complete, 159 s, $0.77 |
| Bug fix, shell script | stops | complete, 147 s, $0.75 |
| Bug fix, Ruby with Minitest | stops | **asks the person for flags** |
| Bug fix, Rust, tests inside `src/lib.rs` | stops | **asks the person to widen the plan** (fixed: complete, 123 s, $0.74) |
| Bug fix, Rust, no `Cargo.lock` | stops | **pauses in planning** (fixed: complete, 195 s, $0.96) |
| Refactor, Rust (extract a helper) | not run | **stops, guards unproven** (fixed: complete, 116 s, $0.77) |

## 1. Ruby with Minitest: the person is asked for a test command (fixed)

Bundle manifest `b9f62b583107991dc47b497685c84b2db9c879c5f8ce3e2376cf4713ad023713`. The fix and its
three Minitest tests were correct and the Tester's own before/after run showed it. The runner
detected no suite (`toolchain_suite.detect` knew RSpec with a `Gemfile`, nothing else in Ruby), so
the regression proof was `UNVERIFIED` ("No project test command was found ... pass
--test-command") and the Resolver asked the person to set `--regression-command` and
`--test-command`. That is a question about machinery.

A Ruby project with no RSpec suite and no `make test` now runs the `*_test.rb` and `test_*.rb`
files under its `test/` directory, through Bundler when it has a `Gemfile`
(`toolchain_suite.ruby_tests`, `tests.test_ruby_test_suite`). The same task then completed on the
same models with the proof `PASS` on `detected:minitest` (manifest
`32ff1be331278adf723c8a68903fc3fc4860722a2cda2ad42db0ff5759bf0c69`, 373 s, $2.28). Minitest results
are not read per test, so the proof rests on the suite's exit code, as for Cargo and `make test`.

## 2. Rust: a test inside the source file is not a test (planned around)

Manifest `685ac9b901c446d81f5115590ee3648e27424a18882e4e0a99d7b3851193a78a`. Rust unit tests live in
a `#[cfg(test)]` module of the file they test. The Builder fixed `src/lib.rs` and added its
regression test there, as the seed's own test is. The proof classifies `src/lib.rs` as product only,
so it failed with "No regression test was added or changed", and the Resolver asked the person to
allow a new `tests/page_count.rs`. Proving an inline test needs the candidate's test module run
against the original product code of the same file, which the proof cannot do.

The planning and Builder note on named tests now says to plan and write a new Rust test in a file
under `tests/`. The same task then planned `tests/page_count.rs` from the start and completed
(manifest `3d0c2a9117f2dd7904b49efd35ac3c8f3e8e6bc07e00f615f8b917c76f615d71`, 123 s, $0.74). A test
that can only reach a private item is still not provable; that needs the inline test module moved
onto the original source, and is open.

## 3. Rust: `Cargo.lock` written by a read-only stage pauses the run (fixed)

Manifest `f817c5e0ea6db43cc36bec3e922c8647c144e8f907ef0267a87465672cff69fa`. The project has no
`Cargo.lock` and does not ignore it. The Planner ran Cargo to read the project, Cargo wrote
`Cargo.lock`, and the run paused at `PAUSED_STALE_VALIDATION` ("Repository changed during read-only
review") 65 s in. A scripted Builder that runs `cargo test` is rejected the same way for a file
outside its paths (`PAUSED_INVALID_OUTPUT`). A lockfile the toolchain writes as a by-product is not
an edit by the stage.

A `Cargo.lock` that did not exist when the stage began, is untracked and sits beside a `Cargo.toml`
is now removed before the stage's changes are read, and named in the stage record as
`toolchain_byproducts` (`autocode_toolchain_byproducts`, `tests.test_toolchain_byproducts`). A
lockfile the project already had, one a Builder's task owns, and one written by a Builder with no
assigned paths are left alone. The same task on the same models then went through planning and the
build with `Cargo.lock` discarded at the Plan Reviewer and the Builder (manifest
`a10301015bb318cac7fbf20cba3557a77ab7cff4bc039470099897261514bdac`, 259 s, $1.39) and stopped at the
next defect, section 4. OpenCode and Kilo check a read-only stage with their own snapshot
(`autocode_readonly_events`); that path was not exercised here.

## 4. Rust: a named test cannot be proved, and the person is asked (fixed)

Same run as the fix in section 3. The plan named its tests (`test:` criteria in `tests/pages.rs`).
Cargo results were not read per test, so the proof was `UNVERIFIED` ("the test run reported no
per-test results") although its own log shows each named test failing on the original code and
passing with the change. The Resolver then asked the person to accept the log lines, wait "until
the runner supports cargo named-test parsing", or deliver without a proof. In an earlier run the
Plan Reviewer raised the same limit at planning and the Planner asked the person how the fix should
be verified.

The proof now reads `cargo test`'s own per-test lines (`autocode_cargo_tests`,
`tests.test_cargo_tests`, [named test proof](../named-test-proof.md)). A first pass on the same
models then matched every `test:` criterion and found one more gap: every plan guarded an existing
test by its own name (`guard: exact_multiple`), and only `test_*` and Go `Test*` names bind, so the
guards stayed unproven. The two bug fixes got through after a rework in which the Builder added a
second, `test_ac2_...` copy of each guarded test ($1.65 and $2.21); the refactor, which may add no
test, stopped (manifest `18b8f5506064c9c6e76f0f1f33917578ce360aafb983f5e56f5cf6262b3e52a8`, 371 s,
$2.60). With a Cargo suite a declared identifier without a `test_` prefix now names that Rust test
(`test_cases.bound`); every other runner keeps the criterion-id convention. The second pass
completed all three with no rework and no question:

| Task | Manifest | Result |
| --- | --- | --- |
| Bug fix, no `Cargo.lock` | `adc0a1c1eac513f3778ccc5a986b86b120460854ccac9c303092ece63bab9d7a` | complete, 195 s, $0.96 |
| Bug fix, tests inside `src/lib.rs` | `3d0c2a9117f2dd7904b49efd35ac3c8f3e8e6bc07e00f615f8b917c76f615d71` | complete, 123 s, $0.74 |
| Refactor (extract a helper) | `bd1c857047013f9e3103f1d0c489435216e9b0081cbc26a00d702ed3f133912b` | complete, 116 s, $0.77 |

The same limit holds for RSpec, whose examples are named by free text: a guard on an existing
example there still falls back to the criterion id. For node:test and Vitest it is fixed in section 5.

## 5. JavaScript: a guard on a test with a free-text title is not found (fixed)

The catalog's Node and Vitest seeds name their tests `test_base_...`, which real projects do not. Two
probes used a node:test project whose tests have ordinary titles (`test('counts a partial last
page', ...)`). Both completed, but each only after a failed proof and a second build, at about twice
the usual cost:

| Task | Manifest | Result |
| --- | --- | --- |
| Bug fix | `746bad32c0b13c66c125e89e28511db3c50049ed30dc49a7a5541783d7fc4c80` | complete, 336 s, $2.38, 18 stages |
| Refactor (extract a helper) | `228193c9454e2d38fcc1e57d4415dbc34bf4c2bc7e8cfe1705419c6b87bba42e` | complete, 332 s, $1.98, 18 stages |

The Planner wrote what a person would: `guard: returns the page count for an exact multiple`. The
proof looked for a test named `test_ac2_...`, found none, and failed ("Preserve case AC2 … has no
test named test_ac2_<what it checks>"). The Investigator's diagnosis says it plainly: "the runner maps
preserve cases to tests only through the test_acN_ prefix". Both runs then copied the existing tests
under `test_acN_...` names (the refactor into a new `tests/pager.guard.test.js`), which is what the
rule against renaming tests to fit a case id is meant to prevent.

A declaration is now bound to a test when the runner names tests by title (node:test, Vitest) and a
test the proof ran has exactly that title (`autocode_test_cases.titled`); otherwise the criterion
keeps the id convention. The planning and Builder note says to guard existing behavior with the
existing test and never to copy it.

On the same models the bug fix then bound each guard to the existing test and completed in one pass
(manifest `e62b16d6c57f7ed901b497083987f1f8dc8f87771a2efa03830766cf002796d9`, 194 s, $1.24, 10
stages). The refactor planned the right thing, three guards on the three existing tests and no test
changed, and that exposed the next gap.

## 6. JavaScript: `npm test` names no test, so a refactor asks the person (fixed)

Manifest `48dac0d65894394c9d3c1c38fed38068800553f6295aa163d0e59485ccc14724` (252 s, $1.47). The
project's suite is `npm test`, whose script is `node --test tests/*.test.js`. AutoCode attached its
reporter only to a direct `node --test` command: to the changed test files of a bug fix, never to
`npm test`. A refactor changes no test file, so its proof ran only the suite, got an exit code and no
test names, and was `UNVERIFIED` ("the test run reported no per-test results"). The Resolver asked the
person to choose between the proof and the plan, a question about machinery, with every existing
test passing.

Node reads a reporter option only before the test paths, so one appended after `npm test --` is
ignored. When the package's `test` script is a direct `node --test` command, alone or after build
steps (`tsc -p . && node --test out/*.test.js`), the runner now runs that command with its reporter,
the build steps first as `npm test` runs them (`autocode_node_tests.npm_test`,
`tests.test_node_tests`). A package with a `pretest` or `posttest` script or an `.npmrc`, and a
script behind `--prefix`, keep `npm test` as written.

The same task then completed without a question, but again only after a failed proof and a second
build (manifest `21a7888e60c48d908411c1dc857c59384299e6a796f6fb6fa67d5038a644f10f`, 413 s, $2.94, 20 stages): this plan added one new test file
beside its three guards on the existing tests, and that showed the last gap.

## 7. JavaScript: a new test file takes every titled test out of the guards (fixed)

Same run. The suite now named its tests, and the three guarded tests passed on the original code and
with the change, yet the proof bound none of them ("has no test named counts a partial last page").
The proof drops a suite test from the guards when a changed file names it, because that file may have
redefined it. A test's name was read from its whole id, where
`tests/pager.test.js::counts a partial last page` is "test", from `.test.js`. The new test file says
`test(`, so it "named" every test of the suite. The Resolver diagnosed the proof's limit and had the
Builder copy the three existing tests into the new file under `test_acN_...` names.

A JavaScript test's own name is now its title, and a changed file names a titled test only by spelling
that title (`autocode_test_cases.function_name`, `named_in`).

With the three fixes the same task, planned the same way (three guards on the existing tests and one
new test file), bound every guard to its test in the first proof and completed in one pass (manifest
`ba45fa34ba020074129cbfbc89834c324e80078d447dd95a108e57587a495b7b`, 149 s, $0.85, 8 stages).

RSpec, whose examples are also named by free text, still falls back to the criterion id.

## Also seen, not yet run on real models

- **A new repository with no commit is refused** with a Git command for the person to type
  (`autocode_workspaces.create`), while an empty folder that is not a repository is set up
  automatically. A chat's start there (`--build-in-worktree`) stopped with Git's own
  "ambiguous argument 'HEAD'". Fixed: AutoCode makes the empty first commit itself, without the
  index, and the task works in the folder (`autocode_workspaces.first_commit`).
- **A second task in a new project started without the first task's files**: a folder AutoCode
  made a project of holds what the first task built, uncommitted, and its branch holds only
  AutoCode's empty first commit. A second task there ("add an option to hello.py") was given a
  task worktree of that empty commit, so it started in a folder without `hello.py`; a chat was
  offered the same worktree as its default place. Reproduced with the scripted provider. Fixed:
  while the branch holds nothing but that commit, a task works in the folder itself
  (`autocode_workspaces.only_first_commit`), and `worktrees list` offers only the folder.
- **A folder of code that is not a Git repository started an empty project**: `bootstrap` gave a
  populated non-Git folder a private scratch project, so a task about the code there could not see
  it. On a real run of the paging bug fix in such a folder (`pager.py`, `tests/test_pager.py`),
  Requirements wrote "the workspace contains no files, so I could not find pager.py" and still rated
  the request clear, and the Planner then asked the person "The workspace repository contains no
  files, so pager.py and its page_count function cannot be found. Where is the code…?" (33 s, $0.17;
  bundle manifest `af95aa04832c4d5908e414b156673db1968af43657e3b3fd4182e57fab19a29b`). Fixed, owner
  decision 2026-11-13 ("work on the code there"): a folder of code outside Git is made a repository
  where it is, with an empty first commit and none of the person's files added, and the task works
  on the files there (`autocode_folder_adoption`). A home folder, a folder of Git checkouts, a
  folder without code and a very large folder keep the private project. The same request in the
  same folder on the fixed tree: Requirements understood the defect from `pager.py`, the plan named
  `pager.py:5` and the existing guard test, and the run completed with the fix and four new tests
  written in the folder, nothing committed for the person ($0.83, under four minutes; bundle
  manifest `4f13a30471d960a4ce98a43b66f5ab40198adadb62d535d8fd8f9a0ee9b94a8e`).
