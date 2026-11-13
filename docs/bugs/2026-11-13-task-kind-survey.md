# Which kinds of task AutoCode completes: a survey (2026-11-13)

The catalog covers bug fixes, features, new projects, reviews, designs and discussions, mostly in
Python, with Go, Node, Vitest and Java cases. This survey ran small tasks of kinds it does not hold,
to find where a run refuses, stalls or asks the person about AutoCode's own machinery. Each task is a
one-function paging library with a one-line change, so a stop is AutoCode's and not the model's.

Method: master `6d8f4e4`, Linux cloud container. Twelve task kinds ran with the scripted
provider (`scenarios/run.py run --fake`). Eight of them then ran once on Claude Code
(`--profile claude-tiers`: Sonnet 5.5 Requirements, Planner and Tester, Opus 5.5 reviewers,
Haiku 4.5 Builder), $9.15 in all.

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
| Bug fix, Rust, tests inside `src/lib.rs` | stops | **asks the person to widen the plan** |
| Bug fix, Rust, no `Cargo.lock` | stops | **pauses in planning** |

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

## 2. Rust: a test inside the source file is not a test (open)

Manifest `685ac9b901c446d81f5115590ee3648e27424a18882e4e0a99d7b3851193a78a`. Rust unit tests live in
a `#[cfg(test)]` module of the file they test. The Builder fixed `src/lib.rs` and added its
regression test there, as the seed's own test is. The proof classifies `src/lib.rs` as product only,
so it failed with "No regression test was added or changed", and the Resolver asked the person to
allow a new `tests/page_count.rs`. A test under `tests/` is proved today (scripted run, exit code),
but it reaches only public items. Proving an inline test needs the candidate's test module run
against the original product code of the same file.

## 3. Rust: `Cargo.lock` written by a read-only stage pauses the run (open)

Manifest `f817c5e0ea6db43cc36bec3e922c8647c144e8f907ef0267a87465672cff69fa`. The project has no
`Cargo.lock` and does not ignore it. The Planner ran Cargo to read the project, Cargo wrote
`Cargo.lock`, and the run paused at `PAUSED_STALE_VALIDATION` ("Repository changed during read-only
review") 65 s in. A scripted Builder that runs `cargo test` is rejected the same way for a file
outside its paths (`PAUSED_INVALID_OUTPUT`). A lockfile the toolchain writes as a by-product is not
an edit by the stage.

## Also seen, not yet run on real models

- **A new repository with no commit is refused** with a Git command for the person to type
  (`autocode_workspaces.create`), while an empty folder that is not a repository is set up
  automatically.
- **A folder of code that is not a Git repository starts an empty project**: `bootstrap` gives a
  populated non-Git folder a private scratch project, so a task about the code there cannot see it,
  and nothing says so.
