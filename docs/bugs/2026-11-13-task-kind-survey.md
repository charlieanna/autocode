# Which kinds of task AutoCode completes: a survey (2026-11-13)

The catalog covers bug fixes, features, new projects, reviews, designs and discussions, mostly in
Python, with Go, Node, Vitest and Java cases. This survey ran small tasks of kinds it does not hold,
to find where a run refuses, stalls or asks the person about AutoCode's own machinery. Each task is a
one-function paging library with a one-line change, so a stop is AutoCode's and not the model's.

Method: master `6d8f4e4`, Linux cloud container. Twelve task kinds ran with the scripted
provider (`scenarios/run.py run --fake`). Eight of them then ran once on Claude Code
(`--profile claude-tiers`: Sonnet 5.5 Requirements, Planner and Tester, Opus 5.5 reviewers,
Haiku 4.5 Builder), $9.15 in all, and $12.59 more for the eight runs after a fix.

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

The same limit holds for other runners whose tests need no `test_` prefix (RSpec, node:test and
Vitest names are free text): a guard on an existing test there still falls back to the criterion id.

## Also seen, not yet run on real models

- **A new repository with no commit is refused** with a Git command for the person to type
  (`autocode_workspaces.create`), while an empty folder that is not a repository is set up
  automatically. A chat's start there (`--build-in-worktree`) stopped with Git's own
  "ambiguous argument 'HEAD'". Fixed: AutoCode makes the empty first commit itself, without the
  index, and the task works in the folder (`autocode_workspaces.first_commit`). Still open: once
  that commit exists and the person's files are uncommitted, the next task starts in a worktree
  of the empty commit and sees none of them, as a second task in a folder AutoCode made a
  project of does.
- **A folder of code that is not a Git repository starts an empty project**: `bootstrap` gives a
  populated non-Git folder a private scratch project, so a task about the code there cannot see it,
  and nothing says so.
