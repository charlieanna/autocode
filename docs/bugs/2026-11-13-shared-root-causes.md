# Why new project kinds keep breaking the stages: three shared causes (2026-11-13)

The task-kind survey ([2026-11-13-task-kind-survey.md](2026-11-13-task-kind-survey.md)) found three
stops on real models: a Ruby Minitest fix asked the person for `--test-command`, a Rust fix with its
test inside `src/lib.rs` asked the person to widen the plan, and a Rust project without `Cargo.lock`
paused in planning. Recent fixes (#3374 Minitest detection, #3349, #3261, #3314, #3262: "send X to
the Investigator, not the person") look like separate bugs. They come from three shared causes.

## 1. What a project is, is a hand-written list per language, kept in several places

AutoCode decides "which files are tests", "which command runs them", "what a stage wrote" and "what
counts as the change" from per-language tables, and the same knowledge is copied:

- Test files: `autocode_verify.TEST_NAME`/`TEST_DIRS`, `autocode_test_examples.TEST_FILE`,
  `autocode_plan_test_suite.KINDS` (no Minitest), `autocode_test_framework.targeted`, and more.
- Suites: `autocode_toolchain_suite.detect` (Go, Node, Cargo, RSpec, `make test`, and Minitest since
  #3374), plus Python detection in `autocode_verify`.
- Files a toolchain leaves behind: `autocode_util.inventory_excluded` and `autocode_verify._ignored`
  both list `__pycache__`; `autocode_assignment.build_output` exempts compiled executables for the
  Builder only; Go's `go.mod`/`go.sum` are special-cased in three modules; `Cargo.lock` nowhere.

Each language or layout the lists miss is a new bug, found one run at a time. The `Cargo.lock` pause
is the by-product case; it is being fixed with a shared table of toolchain by-products
(`autocode_toolchain_byproducts`, the task-kind thread's pull request).

## 2. The evidence model assumes named per-test results and whole-file test/product roles

The regression proof matches a `test:` or `guard:` criterion to a test only from per-test results
(`autocode_regression.check_cases`), which AutoCode reads for unittest, pytest, Go, node:test, Vitest
and RSpec. `make test`, `cargo test`, Minitest, Jest and Mocha report only an exit code, so every named
criterion there ends `UNVERIFIED`, after a full Builder and Tester round. Separately, a file is either a
test or product code, so a Rust test inside the source file it tests is not a test at all.

## 3. When the core cannot decide, the gap is written as a flag for a person

The proof's trusted commands come from a typed flag or detection; the Builder's report is never
trusted. With no runner detected the proof said "pass --test-command", the Resolver turned that into a
question ("set --regression-command 'ruby test/pager_test.rb'"), and the person was asked about
machinery, although the approved plan already said `ruby test/pager_test.rb` runs the tests. The
Planner prompt likewise said to "raise the compatibility blocker" when no supported runner fits.
AGENTS.md calls such a question a defect; each new gap found its own way to it.

## What changed here

- **The approved plan is a trusted command source** (`tools/autocode_plan_proof_command.py`). When
  nothing is typed and nothing is detected, the proof runs the first approved command that runs a test
  file: as the suite when every test file it names exists in the original code, as the regression
  command when it names a changed test file. It was fixed and reviewed before any Builder ran, so the
  candidate is not judged by a check it chose; the proof still runs it on the original code and with the
  change, and records the choice in its notes and review reasons. This removes the "pass
  --test-command" question for every language detection does not list, not only Ruby.
- **The original-test gate uses the same command.** It binds the project's tests when the run starts,
  before any plan exists, so with no runner detected it bound no command and later stopped with "No
  original suite command was available; explicit user revision required" (a live shell bug fix,
  2026-11-13, after the regression proof had passed on the plan's command). It now runs the approved
  plan's command when that command runs only the bound original test files
  (`autocode_protected_oracles._plan_suite`); the binding still fixes which original bytes run.
- **Named criteria on an exit-code runner go back to the Planner when drafted**
  (`autocode_plan_test_suite.unnamed_cases`). A draft with `test:`/`guard:` criteria is refused, with
  the fix (write the criterion as the command that runs its test file), when the detected runner reports
  no per-test results, or when the project has test code and no runner is detected, and no suite around
  the planned tests reports them. A new project with no tests yet is not judged. The decision follows
  `Framework.per_test`, so a runner that gains per-test results (Cargo, in progress) keeps named
  criteria without another change. The named-proof prompt no longer tells the Planner to raise a
  blocker for the person.

## One table of project kinds

What cause 1 lists by hand is now one table, `tools/autocode_project_kinds.py`, one row per language:
its source suffixes, its test file names, the files only its tests use (`conftest.py`, a Jest snapshot),
the test files its targeted runners collect, which runners report per-test results, its manifests and the
lockfiles its toolchain writes on its own. `autocode_verify` (test role, code suffixes, test directories),
`autocode_python_test_roles`, `autocode_python_tests`, `autocode_test_examples`, `autocode_plan_test_suite`,
`autocode_test_framework`, `autocode_toolchain_suite`, `autocode_package_scope` and
`autocode_toolchain_byproducts` derive their lists from it, so a language or test layout is added in one
row. Every derived list matches the copy it replaces, except that the test files shown to a stage as
examples (`autocode_test_examples`) now follow the same names as everywhere else: Java, Kotlin, C#, Swift,
Scala, `.mjs`/`.cjs` and `test_*.sh` tests are shown, and `x_test.rs`, `test_x.go` or `x_spec.py`, which no
runner collects, are not.

Still open: suite detection order (`autocode_toolchain_suite.detect`) and the Minitest layout are still
logic in that module, Go's `go.mod`/`go.sum` special cases and the `__pycache__` exclusions are still
written in each module, inline Rust tests (cause 2), and the by-product table's growth to other lockfiles.
