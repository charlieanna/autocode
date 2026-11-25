# Named test proof

A plan's `test: test_c1_example` criterion requires that named test to pass with
the change and not before it. A `guard:` criterion must pass before and after.
When every criterion the proof covers is a `guard:` and the change adds or edits no test
(it may change nothing, as a validation-only re-check does),
the guards rest on tests the project already has: the proof runs the suite on the base and
the candidate, and each guard's named test must pass on both. A `test:` criterion still needs
a change, unless the person granted the test-only regression-proof exception.

Before building, a new acceptance test can already pass on an unfinished candidate
retained from an earlier attempt. The runner checks those exact observed tests on
the original product code with today's test files and fixtures. A fresh original
failure keeps the approved `test:` criterion; a fresh original pass proposes a
`guard:` correction for plan review. Missing, skipped, incomplete or import-only
results leave verification unresolved. This classification needs no report-only
model repair, and never replaces the final regression or changed-code proof.

A change may rewrite only files that tests read: a golden file, a snapshot or a
fixture (`tests/golden/report.txt`, `src/__snapshots__/x.test.js.snap`,
`testdata/report.golden`). No runner can be pointed at such a file, and the tests
that read it are in files the change left alone. Where the runner names each test,
the project's own suite is then the regression command: it runs on the original
code with the changed files and with the change, and the tests that fail first and
pass after are the proof (`autocode_verification_commands.held_by_suite`). A changed
test file that holds code is never left to this, and a rewritten file that no test
reads proves nothing.

A guard that names its test exactly (`guard: test_c4_adds`) may name a test the project
already has in a file the change leaves alone, as a final check does for the
criteria earlier milestones proved. The targeted run covers only the changed test files,
so the whole suite also runs on the original code with the change's test files (its tests,
fixtures and goldens), and such a guard is matched against the tests that pass there and on
the candidate, with these limits:

- a test whose own name appears in any changed code file is left to the targeted run (a changed
  test file) or not used (a changed product module that defines it); a document that mentions it
  does not count;
- if that suite run times out or does not report every test, a guard left without its test is
  unverified rather than refuted;
- with git-ignored test files that the proof copies into its trees, the whole suite is not
  consulted, so a guard cannot rest on an ignored file, and the failure says so;
- a case matched by its id alone (a diagnosis's T4) is never matched this way, since another
  fix's `test_t4_...` is not its test;
- one failing variant of a matched test breaks the guard, even when it failed before the change
  too: the same function in the same file or module, with other parameters, as a Go subtest, or
  run by another class. A failing test elsewhere that only shares the name does not;
- a node:test file that spawns another test runner is refused here as everywhere.

A guard whose test belongs to another project of the repository, which the proof's suite
cannot run, is matched against that project's own suite, run on its file on the original code
and with the change (see the monorepo packages section of `docs/workflow.md`).

When the change edits no test file, the guards are matched against the base suite's own
pass-to-pass, by the case id as well as by exact name, so a guard may name several tests; one
of them failing beside a test it matched breaks the guard.

Preservation normally rests on base tests that still pass. When the original code holds no
test file and its suite had nothing to run (an empty collection, or a known runner with no test
directory), the change's own tests stand in (#2259). Any of them that pass on the original
code and on the change show its existing behavior was kept, and the full candidate suite must
pass. Without such a test the proof stays `UNVERIFIED`, and its reason tells the Builder to
add one; no person is asked. A base holding only documents, manifests, data files or a short
extensionless text note has no behavior to preserve at all.
An exit code or a printed `PASS test_c1_example` is not enough to identify which
case ran. AutoCode currently attributes tests from Python unittest/pytest, Go,
Node's built-in `node:test` runner, Vitest 4, RSpec, Rust's `cargo test`, and an
explicitly configured JUnit XML report from another test runner.

RSpec needs no added gem: a plain `rspec` or `bundle exec rspec` command also runs
RSpec's built-in JSON formatter, writing to a file in the proof's evidence while the
progress output stays on the screen. Each example is named `FILE::FULL DESCRIPTION`,
the file relative to where RSpec ran, so `test: test_grades_case_insensitively` names
`it "test_grades_case_insensitively"` inside any `describe`. A spec file that fails to
load runs no example and is reported as `FILE::[collection]`. A command that already
writes its own output file (`--out`) or reads another options file is left as it is.

Cargo needs no reporter either: the test harness prints one line for every test of every
test binary, and the proof reads those lines from a plain `cargo test` command's own output
(`tools/autocode_cargo_tests.py`). Each test is named `FILE::NAME`: the file Cargo says it is
running (`tests/pages.rs`, or `src/lib.rs` for unit tests) and the harness's name, so
`test: test_partial_last_page` names `fn test_partial_last_page`, also inside a module
(`src/lib.rs::tests::test_partial_last_page`). A documentation test is
`Doc-tests CRATE::FILE - item (line N)`, and an ignored test is skipped.

- The command gets `--no-fail-fast`, so one failing test binary does not hide the results of the
  binaries after it. A command that changes what the harness prints (`-q`, `--format`,
  `--nocapture`) is left as it is and judged by its exit code.
- A binary's results count only when they agree with its own `test result:` line. A binary that
  exited or crashed mid-run is reported as `FILE::[no result]` and the results are incomplete.
- A build that fails runs no test and is reported as `TARGET::[build failed]`, like a Go package
  that does not build: a bug fix needs a test that ran and failed on the original code. For a
  feature, whose new test usually calls a function the original code lacks, only the tests the
  change adds or edits can show the new behavior; an untouched test that passed on the original
  code is kept as preserved.
- In a workspace two crates may print the same file and test name. The two are one identity,
  failed when either failed.
- A Rust test needs no `test_` prefix, so with a Cargo suite the identifier after the mark names
  the test even without one: `guard: exact_multiple` is the project's existing
  `fn exact_multiple`, and `guard: tests::exact_multiple` the one in `mod tests`. A lone word
  (`guard: manual`) is still prose. Every other runner keeps the rule above: only a `test_*` or
  Go `Test*` name binds, and anything else falls back to the criterion id.
- The changed test files are the files under `tests/` (and other test paths), and the code files
  whose tests changed. Some tests are written inside a code file: a Python doctest in a docstring,
  a Rust example in a doc comment, a `#[cfg(test)]` module, and the examples of a doctest document
  (`.txt`, `.rst`, `.md`). Each language's own syntax says which lines those are
  (`autocode_test_parts.parts`). The proof puts the change's edits to those lines on the original
  code, and nothing else: it refuses to when the original program would differ, read without its
  docstrings, doc comments and test modules, so a fix cannot ride along. An example of a function
  the change adds goes into doctest's own `__test__` table, so it fails on the original code as a
  new test of a new function does. A docstring example is the change's test only when no test file
  changed, since a project with test files may not run its doctests; a Rust doc example is one
  always, since `cargo test` runs them. A command that names a file holding such tests
  (`python -m doctest textutil.py`) runs existing tests, so it can guard a change; the plan check,
  the guard and the proof's choice of command read that from `autocode_test_parts.named_holding`.
  Open: a Rust doc example on a function the change adds
  cannot be put on the original code, and an edit that mixes test lines and code on one line is not
  split, so such a proof finds no test rather than passing.

A literal Go check with an explicit empty selector (`go test -run '^$' ./...`)
may validate compilation in a plan. Its exit must be zero, but it supplies no
named-test results or behavioral proof. Unknown flags, benchmark/fuzz requests,
and custom test-program arguments retain the normal test-result requirement.
Submitting an empty-selector command as a regression or suite still cannot
satisfy that proof: it must execute named tests.

The identifier right after `test:` or `guard:` is the one the runner proves; an
explanation may follow after ` — ` or in parentheses. Without one, the test is
named after the criterion ID (`test_c1_...`; Go's `TestC1...` matches it).

Use a bare Python test name, such as `test_health`, after `test:` or `guard:`.
Qualified selectors such as `tests.test_health.HealthTests.test_health`, including
those inside paired Markdown backticks, are not supported as proof declarations:
a new draft, or a saved draft at approval, is refused before building. No module,
class or method is inferred from a suffix.
Supported Go test/subtest names and annotations remain valid. Prose,
placeholders and declarations naming multiple tests retain the criterion-ID
fallback; they do not bind a qualified selector. An already approved proof is
not rewritten; changing it needs a goal edit and approval of the new exact plan.

When the user's brief asks for Go tests by name ("add Go tests
`TestFixedReturnsTwo` and `TestFixedPreservesCrash`"), the plan declares those
exact names, in the user's spelling: `test: TestFixedReturnsTwo`, `guard:
TestFixedPreservesCrash`, one criterion each. A requested test the runner cannot
run to a pass (one that skips without a database) may instead be named by an
ordinary criterion, and no other test with it, for the Validator. Planning
stages are told the names, and the runner refuses a draft, before it is
installed or approved, that leaves a requested name unaccounted for, declares a
respelling of it (`test_fixed_returns_two`), or declares another identifier (or
none it can read, as in `test: TestFixedReturnsTwo.`) while its method or
criterion text names the requested one, as in prose saying it "resolves to" it
(#498). A name counts as requested when the user wrote it right after "test",
"tests" or a Go test-shaped "func" signature (or in a list introduced as tests,
each name with or without a description). Name-first requests such as
"Add TestA, a real regression ... plus TestB and TestC" also count. Negations,
examples and alternatives do not. The proof must run Go tests, and the project
must not already declare the name as a top-level test in a `*_test.go` file.
Comments, strings and callable production APIs (`func TestConnection() error`,
"a func named TestConnection that returns an error", "a func called
TestConnection in product.go") do not make a test existing or requested; "a
func named TestParse in parser_test.go" or "a func named TestA, a regression
test" does. This inventory does not replace Go compilation or execution proof.
A subtest path such as `TestCacheExpiry/expired` is requested as the user wrote
it (a quoted one with its spaces as underscores, as Go runs it): planning
stages and refusals ask for `test: TestCacheExpiry/expired`. Declaring that
path, a path under it or its whole test function accounts for it; another
subtest of the function does not, since the runner binds that one to its own
outcome only. "Instead of", "rather than" or "not" right before the path
withdraws that path; before the function, the function and the paths of it an
earlier message asked for. The user's own `--edit-goal` is never
refused by this check and settles which requested names stay; an edit of a
design job's plan, which "Build it." archives when it plans the build afresh,
settles nothing for the build. `TestMain` and `TestXxx` are never tests to
write. Other frameworks, and bug fixes proven by their diagnosis's cases, keep
the criterion-ID convention. The details and limits are in
`docs/bugs/2026-10-06-native-proof-names.md`.

For Node, register each case as a real test, keeping its existing assertions
and fixture helpers:

```js
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {add} = require('../app.cjs');

test('test_c1_adds_two_numbers', () => {
  assert.equal(add(2, 3), 5);
});
```

Run it with `node --test tests/cases.cjs`. Use unique case names within each
suite and await asynchronous assertions. AutoCode detects `node:test` imports
in changed test files and derives the targeted command. It runs identical tests
against the original and candidate source in isolated worktrees. Keep the
project's existing suite (including custom npm scripts) and protected tests;
`--test-command` can continue to run that suite.

AutoCode attaches its own structured reporter to direct `node --test` commands.
It checks completed event streams and final counts, ignores test stdout, and
does not count skipped, todo, empty-file or ambiguous duplicate results as named
passes. Import and fixture-hook failures cannot demonstrate a reproduced bug.
Missing or incomplete evidence stays unverified. The adapter uses the
[documented Node TestsStream custom reporter API](https://nodejs.org/api/test.html#custom-reporters),
including final summary events available in current Node 22 and later releases.
The reporter handles Node 22.14's omitted ordinary-test type while preserving
suite identity; a missing type in retained proof files is still invalid evidence.

A project whose suite is `npm test` reports its tests by name too when the package's
`test` script is that command: `node --test tests/*.test.js`, or build steps and then
it (`tsc -p . && node --test out/*.test.js`). Node reads a reporter option only before
the test paths, so AutoCode runs the script's own command with its reporter (after the
build steps, run as `npm test` runs them) and leaves the path pattern for the shell to
expand as it does under npm. A change that touches no test file is then proved by the
suite's own tests, which is what a refactor guarded by the existing tests needs. A
package with a `pretest` or `posttest` script or an `.npmrc` (which can set the
script's shell or node's options), a script behind `--prefix`, and any other script
keep `npm test` as written and its exit code (`autocode_node_tests.npm_test`).

For Vitest 4, keep the tests in their existing framework. Give each case a unique
name, such as `test('test_c1_adds_two_numbers', ...)`, and use
`npx --no-install vitest run tests/example.test.js`. An npm `test` script containing
a single `vitest run` command is supported too, including `npm --prefix frontend test`.
AutoCode attaches its owned reporter, reads a fresh structured result file, and
excludes its internal `.autocode/` test backups. Missing or skipped cases, duplicate
identities, interrupted reports, and unsupported versions cannot supply named
passes. Collection and hook failures are separated from application failures.
The reporter uses Vitest's reporting lifecycle and v4 hook-result metadata;
qualification currently covers Vitest 4.1.6.

Jest needs no reporter of its own: its built-in JSON report names every test. AutoCode
adds `--json --outputFile` to a plain Jest command (`jest`, `npx --no-install jest`,
`node node_modules/jest/bin/jest.js`, with or without an environment prefix) and to an
`npm test` whose script is one, and reads the report: a test's id is its file, the
titles of its `describe` blocks and its own title, `tests/pager.test.js::rounding >
counts a partial last page`, as a Vitest id is. Tests that share a name (`test.each`
without a placeholder) are one id, failed when any of them failed. A file that could
not be loaded is a failed, uncollected entry, and a run that failed outside every test
(a coverage threshold) is a failure with no test to blame. `npx jest` without
`--no-install`, a command that watches, writes its own report or chooses tests from Git
(`--watch`, `--json`, `-o`), and a script that runs more than Jest are left as written
and prove by their exit code, as before (`tools/autocode_jest_tests.py`).

A test the project already has in `node:test`, Vitest or Jest is named by its title,
which is free text: for `test('counts a partial last page', ...)` a plan writes
`guard: counts a partial last page`. AutoCode binds such a declaration to the test
when a test the proof ran has exactly that title (the last part of its id; inside
a Vitest or Jest `describe` block, `suite > title` or `title` alone), quoted or followed by an
annotation if the plan wrote one. A declaration that is no test's title is prose,
and its criterion keeps the id convention (`test_ac2_...`). So a refactor or a bug
fix in a project whose tests have ordinary titles guards its existing behavior
with the existing tests, without a `test_*` copy of them
(`autocode_test_cases.titled`).

A JavaScript test's own name is that title, never a word of its file's name. The proof
leaves a suite test out of the guards when a changed file names it, since the changed
file may have redefined it: for a titled test that means a changed file spells the
title. Read from the whole id, `tests/pager.test.js::counts a partial last page` was
named "test", so any changed file that says `test(` took every test of the suite out
(`autocode_test_cases.function_name`).

An RSpec example the project already has is named the same way, by what it says. RSpec
reports an example by its full description, the descriptions of its groups followed by
its own: `it "counts a partial last page"` in `describe ".page_count"` of
`RSpec.describe Pager` is `spec/pager_spec.rb::Pager.page_count counts a partial last page`.
A plan writes `guard: counts a partial last page`, and AutoCode binds it to each example
the proof ran whose full description is that text or ends with it at a word. Words from
the middle of a description, or a sentence about the example, name nothing. An example's
own name is its full description (or the `test_*` name in it), and a changed file names
it only by quoting the description or its last words, as a spec file does.

A `node:test` case must assert the behavior itself. A case that runs another
test runner through `child_process` (`npm test -- -t NAME`, `npx vitest`, Jest,
Mocha or `node --test`) passes on that runner's exit code, and Vitest exits 0
when a `-t` filter matches no test, so a misspelt or renamed case would pass
without running (#380). AutoCode reads each `node:test` file that a case's test
lives in and does not match a case to a test in a file that spawns a test
runner; the proof names the file. Running the product's own CLI from a
`node:test` case (`node cli.js add x`) is fine. The check only sees commands
the file spells out; a wrapper is not named proof in any form.

Where the runner can select the proving tests, each runs three times on the
candidate, inside the scratch tree the proof already has, and a `test:` criterion's test also runs three times on the
original code. A test that passes on one run and fails on another is `unstable`:
it does not count as proof, the evidence names the test and its runs, and
completion stops until the test is fixed. Retrying the proof leaves the same
disagreement. Guards repeat on the candidate only. Only the proving tests repeat,
so a stable proof costs two extra executions of those tests, not of the suite.

When the project already has a coverage tool, the proving-test command runs once
more to record `diff_coverage`: each changed product hunk, the hunks no proving
test executed, and the changed files the report did not measure. That run cannot
change the verdict, and nothing is installed into the project.

Shell pipelines, custom reporters, ordinary npm/Mocha summaries and custom
assertion scripts do not currently provide named proof. A direct supported
targeted command can accompany an existing package-script suite. A runner that
reports only an exit code (`make test`, Maven, Gradle or CMake with CTest when no
JUnit report is configured, PHPUnit, Perl's `prove`, Ruby test files run one by
one, or a runner AutoCode does not detect) cannot prove a `test:` or `guard:`
criterion by name, so a draft that has one there is refused when it is drafted,
and the Planner writes the criterion as the command that runs its test file
instead. Behavior that must keep working is guarded there by the command that runs
the existing tests, ``guard: run `ruby test/pager_test.rb` `` (or the detected suite,
``guard: run `make test` ``): the proof runs that command on the original code and with
the change, and both must pass. The command must run tests the original code has.
Where no runner is detected, a guard gives the project's own test command
(``guard: run `./run_tests.sh` ``) and the proof sees whether it runs the project's
tests by running it; only a command that names a test file the project does not
have is refused when the plan is drafted. Elsewhere it is checked when the plan is
drafted: it names an existing test file, it is the
detected suite, or, where the build tool selects tests by class, it selects existing
test classes (``guard: run `mvn -B -q test -Dtest=PagerTest,ReportTest` ``, or
`--tests PagerTest` with Gradle), a module that has some (`mvn -q -pl core test`,
`gradle :core:test`), or every test class, whatever its other options (`mvn -q test`
where the detected suite is `mvn -B -q test`). When every criterion the proof is given
is such a guard and the change touches no test file, the change is coverage of
existing behavior, as one guarded by named tests is, and needs no new test: a
refactor of a Minitest, `make test` or shell project is proven this way. A bug fix
still needs a test that fails without it, a change with a test file is proven as
before, and changed product lines still need the Tester's evidence. A project whose
runner names its tests keeps naming them: a command guard there is refused when the
plan is drafted (`tools/autocode_command_guards.py`, owner decision 2026-11-13).

A bug can be in what the tests are given rather than in the code they test: a fixture that
builds the wrong week, a helper that rounds too early, a data file with a wrong row. The
right fix edits only those files, and "Only test files changed; a fix must change product
code" refused it. Such a change is proven like a repair whose test the project already had (owner
decision, 2026-11-16)
(`tools/autocode_test_support.py`): the task is a bug fix, every path its approved plan
changes is a test file, no changed file is deleted, and the runner's own per-test results
name no test defined in any changed file, on the original code and with the change. The
tests that ran and failed on the original code and pass with the change are then the proof,
and every test that passed there must still pass. The proof records those files as its
`test_support`, and the original-test gate reads that answer: it puts back the original bytes of
every other changed test file and keeps the repaired support, so the original tests' own
assertions run on it, where before it put the broken fixture back and could never pass. A
changed test file, a test edited in
place, a plan that also names product code, a helper that comes to define a test and a
runner that names no test keep the rule as it was: a test's own assertions never change to
make it pass.

A repair whose regression test the project already had changes no test. Where the runner
names its tests, the tests that fail on the original code and pass with the change are
the proof. Code that cannot be loaded at all (a syntax error, merge conflict markers left
in a file) lets no test run, so there the proof is the tests the original code could not
load (owner decision, 2026-11-14: existing tests that now pass prove such a fix). The base
suite must itself name the modules it could not load, the change must modify and delete no
test file, the whole suite must load with the change, nothing may fail that did not already
run and fail on the original code, and every one of those modules must have a test the
project already had that passes now, so code that never had a test is not proved by the
change's tests alone. The tests that could not run on the original code and pass with the change are
then the proof, listed as not run on the original code, and the modules count as repaired,
so the base's collection errors leave nothing unproven. A change may add test files of its
own: their tests cannot run on such code either, so they count in the same way
(`tools/autocode_first_suite.py`, `loaded` and `reloaded`). A test file that cannot be loaded
and that the change neither touches nor repairs proves nothing either way: the proof leaves
it out, names it and compares every other test
([workflow](workflow.md), `tools/autocode_unloadable_tests.py`). A change that edits one of the
project's test files is judged as before, and so is any change to code that loads: a bug
fix's new test must run and fail
on the original code. `cargo test` builds every target before it runs a test, and names the
target that did not build (`amounts (lib)`), not a test file, while its test names name no
crate. So in a Cargo project every test of the run is one that could not run, those of a
workspace's other crates among them, and one the project already had must pass now. Rust
keeps tests beside the code, where removing one is no edit of a test file, and on code that
did not build none ran to be missed: the repair must leave each `#[cfg(test)]` module of
the files it edits or deletes as it was. It may add a module, as it may add a test file,
and a file with a test item outside such a module is not shown to keep its tests
(`autocode_rust_inline_tests.tests_kept`). A Mocha suite, Minitest files, PHPUnit, Perl's `prove`, Maven, Gradle, a test script the approved plan names, and a
Jest command AutoCode leaves as written report only an exit code, so there the suite shows the repair as a whole: it
failed on the original code and passes with the change. That counts when the change is
code alone and touches no test, and the suite's own files (its tests, manifests and
runner configs, among them PHPUnit's `phpunit.xml` and prove's `.proverc`) are the ones the original code had, or the original definition also
passes over the new code. A file a shell test sources (`. ./pager.sh`) is the code
under test there, as a file a JavaScript test imports is, and not one of the suite's
own files; a script the test runs stays one
(`autocode_suite_definition_scan.sourced_product`). For Maven and Gradle the suite's own
files are the ones a JUnit proof of the same command holds to the original: the build
files, the wrapper and the test sources (`autocode_junit_proof.definition`), so a
`build.gradle.kts` that stops the tests running does not show a repair. `make test`,
CMake and a script
package.json names run whatever a recipe says, so they do not show a repair this way,
and a change that also edits another kind of file is judged as before
(`tools/autocode_first_suite.py`, `exit_flip`). When AutoCode detects no runner and nothing was typed, the regression
proof runs the approved plan's own test command and judges its exit code
(`tools/autocode_plan_proof_command.py`), recording that choice in the proof's
notes and review reasons; it never asks the person to pass `--test-command`.
Which of the plan's commands that is, it finds by running them
(`tools/autocode_plan_command_roles.py`). A command that names a test file is taken
by its words, as before. Any other command the plan asks to be run and this machine
can run (its first word is a program on the `PATH`, or a file of the project that
may be executed, such as `./run_tests.sh`) is run on the original code: one that
passes there and fails there once the change's test files are added runs those
tests, so it is the suite and the regression command; one that passes there and
fails there when the project's own test files are made unloadable runs the tests
the project already has, so it is the suite and a guard may use it. `dotnet test`,
`mix test`, `swift test`, `make tests` and a test script name no test file and need
no runner of their own in AutoCode. At most six commands are tried, in the plan's
order, each for at most five minutes; what a command does on the original code is
kept for the run (`plan_command_roles` in its state and in the proof's receipt),
and the gate of the original tests runs them with the command found. Finding a
command's role proves nothing about the change: the proof still runs it on the
original code, there with the change's tests, and with the change.

Which files are tests is a rule over paths (`tests/`, `test_*.py`, `*_spec.rb`), and
a project that keeps its checks elsewhere (`checks/pager_check.rb` run by
`ruby run_checks.rb`) has no test by it. There the approved plan says which command
runs the project's tests, and AutoCode checks it by running that command. The plan
says so with the criterion it is asked to write where no runner names a test,
"run `ruby run_checks.rb`: it fails on the original code and passes with the fix",
or with a command guard (``guard: run `ruby run_checks.rb` ``). A code file of the
change is then a test the command runs when the command passes on the original
code, fails once the file is made unloadable (it runs the file) and passes once the
file is emptied (nothing else needs it). A file the change edits is looked at on the
original code and one it adds on the changed code, at most six files. Such a file is
taken with the change's tests: the original code the command must fail on holds it,
and the check of the original tests keeps it as the original code has it. It stays
code for the evidence of changed lines. A guard there needs a test of the original
code, which is the edited file or one beside a new file. What was seen is in the
proof (`plan_command_roles.tests`, `tests_command`, `existing_test`) and in its notes
and review reasons.

The looks alone never make a file a test. A program's own entry file passes both of
them, and so does a module a program loads when it is there, so a command the plan
only asks to be run ("run `./app.sh` and see the new line") is not looked with, and
a file the command names (`sh pager.sh`, `./app.sh`) is the program and never its
test. A draft in such a project that plans no test is refused as before
(`untested_code`), unless it says which command runs the tests and that command is
seen to run a file the plan names, or one beside a file it adds, as a test is run;
when another of its commands is seen to, the Planner is told how the plan says so
(`tools/autocode_plan_test_suite.py`, `seen_to_run_tests`). A guard there is usable
when its command is seen to run such a file. Do
not downgrade an approved named criterion to prose to make a run finish. A
draft whose test files the proof's suite would never run, such as RSpec specs in
a repository whose root suite is Node, is refused when it is drafted unless one
directory with its own suite holds them all (see the monorepo packages section of
[workflow](workflow.md)), so the Planner settles it before a Builder runs. So is a
build or bug-fix draft that names code files and no test in a project with no test
code: every change to code needs a test, and nothing there can guard the change
instead (`tools/autocode_plan_test_suite.py`, `untested_code`).

### Tests another file of the change lists

The proof runs the change's tests on the original code, and "the change's tests" are
its test files. Some projects run a test only because a file that is no test names it:
a `Makefile` with a rule for each test program, a `CMakeLists.txt` that registers each
test, an `npm test` script of `node test/a.js && node test/b.js`, a `run_tests.sh` that
names each file. There a new test runs only once that file is edited, so on the original
code with the test files alone the command passed without running the new test, and the
proof said "The new tests pass on the unfixed base code" of a test that had not run. Set
the regression command by hand to the new test's own command and the same change failed
next with "The base suite definition fails against the candidate code": the check that
the original tests still pass with the change ran the change's listing without the
change's tests.

Which file lists a test is seen by running, not by its name
(`tools/autocode_listed_tests.py`). When a proof judged by exit code fails for one of
those two reasons alone, AutoCode runs its command again:

- on the original code with the change's test files made unloadable, to see which of them
  the command does not run there;
- on the change with those made unloadable: a command that still passes runs them nowhere,
  and the Builder is told to add them where the project's test command runs its tests;
- on the change with one other changed file put back as the original code has it, the
  tests still unloadable: a command that passes now no longer runs them and nothing else
  broke, so that file lists them.

The proof then runs once more with those files taken with the tests: the original code
the tests are run on holds them, and the check of the original tests keeps them as the
original code has them. What must pass does not change. The command must still fail on
the original code and pass with the change, the original tests must still pass with the
change, a listed test that passes on the original code still fails the proof, and a
file that carries the fix itself makes the command pass on the original code. At most
six other files are looked at, files that are no code first, each run for at most five
minutes; a proof that fails for another reason, or whose runner names its tests, costs
nothing. What was seen is in the proof (`listed_tests`: the command, the tests, the
files and each exit code) and in its notes and review reasons.

## Source resolution belongs to the proof copy

Moving a proof outside the workspace does not by itself isolate it. An editable
install can add the live candidate through a `.pth` file, a test can import an
absolute workspace path, and a package can resolve through a link into the live
tree. A clean-looking test result from that source is neither a pass nor a
failure of the original code.

Before each selected Python, Node or Go proof command, AutoCode classifies the
source-observation plan from the actual command, rather than a previously detected
framework label, and retains the observer and its import closure. After the command
it runs a bounded observation in the same owning copy
(`tools/autocode_source_probe.py`), without rerunning the tests, and checks the
retained bytes again. A command that cannot be classified as one of those literal
runners records the reason in an explicit non-evidential binding and remains outside
this phase's detector, keeping its existing admission.

Python observes targeted modules, static imports (including names that can shadow
the standard library), pytest root, effective `addopts`, `pythonpath` and ancestor
`conftest.py` files. It also checks executable startup paths, `sitecustomize` and
`usercustomize` (an interpreter-shipped empty or Ubuntu apport-only hook is
neutral, as are Ubuntu's exact distutils-precedence and zope namespace `.pth`
hooks when they install only their own dependency), pytest environment options
and plugin autoload. Only a narrow
virtualenv/editable-install bootstrap is recognized. A deterministic top-level
prelude may use unaliased `sys`, `os.path`, `pathlib.Path` and `Path`, append or
insert file-relative roots,
and use those roots in following imports; each import is resolved with only the
mutations active at that statement, and that context follows into imported modules.
Path changes in `conftest.py` or imported source, cross-module path changes,
reassignment of the bootstrap names, aliased or computed access to import
machinery, `from sys import path`, and mutations of `meta_path`,
`path_hooks`, built-in imports or dynamic loaders are blind spots (below). Ordinary
`env NAME=value` prefixes remain part of the Python observation environment.
The owned component/repository collector is modeled as its separate root, family
and isolated member interpreter contexts. Markerless member namespaces retain
that context, and a parent `load_tests` hook or an unobserved mutation is a blind
spot for the family. Multiple files in one interpreter may share identical deterministic path
bootstraps; different mutations, or a mixture of mutating and non-mutating files,
remain order-uncertain.

Node observes package self-resolution, selected or recursively default-discovered
tests and local imports. CommonJS `require` and ESM `import` use their respective
resolvers, so export conditions are observed distinctly. A bounded CommonJS
preload is supported only for `--require`/`-r` files inside the owning root whose
top-level assignments set JSON values on explicit globals. Other options, scripts
before `--test`, conditions, loaders, custom reporters, global setup, dynamic
loaders, resolver monkey-patching, aliased `require`, `module.paths`,
`require.cache`, `NODE_PATH` changes and child-process execution are blind spots.
Explicit tests under `node_modules` cannot be mistaken for owned source.

Go observes its module and transitive package directories, distinguishing
toolchain/module-cache dependencies from local source and replacements.
`GOFLAGS` is classified (safe build-version-control, parallelism and trim-path
flags are neutral), as are `GOWORK`, `-modfile` roots and dynamic plugins;
configured workspace/modfile roots outside the copy, other configured flags and
unobserved plugin loading are blind spots.

A plain literal npm test is observed through the direct Node command it rewrites
to; `package.json` is hash-bound across that classification. Opaque scripts,
build/lifecycle steps, non-root package-prefix invocations, package-local npm
configuration and workspace fan-out are blind spots. A first empty Node project can establish
its own root through a plain package-less npm test. An explicit test selector that
is absent on the original code is neutral source evidence, not a named test pass.
Expected missing Python/Node
modules on original code remain neutral, while every required positive source or
root observation must belong to the copy. An empty first-project base therefore
needs an explicit Python, Node or Go root observation. A first Go project with no
module keeps its existing exception.

Every new proof receipt requires a binding pinned to its exact execution root,
command, output path and output hash. The request, JSON capture, probe output,
supervised receipt and retained observer/import-closure bytes are authenticated
together (`tools/autocode_source_binding.py`). The observer also authenticates its
original tools directory for the narrow editable-install allowance. A binding
cannot be moved to another receipt, and merged receipts authenticate every child
against its own receipt. Raw commands and runners outside Python, Node and Go may
carry a recorded `UNVERIFIED` binding for disclosure, but this phase does not use
it to change their existing proof admission. A later integrity check
recomputes the boundary answer from those bytes.

A blind spot is what the observer could not see: an opaque npm script, `NODE_OPTIONS`,
a dynamic loader, an aliased import machinery call, source that does not parse, an absent
pytest. The binding records it as `unobserved` with its reason, and the proof keeps its
existing admission (`autocode_source_binding.holds`). A blind spot is not evidence that
source came from outside the copy, and holding it paused ordinary Jest, Vitest, pytest and
syntax-repair runs for the person, so only what the observer saw holds a proof.

A required test, root or source origin outside the owning copy makes the proof
`UNVERIFIED`, before timeout, ownership, merged-suite, failed-name or exit-code
judgment. The same hold applies to the original code, stability repeats,
first-suite repair, independent check replay, protected replay, compatibility and acceptance evidence,
companion suites, guards, the plan-time guard check, flake rechecks, follow-up
baselines, original-reference checks and changed-line mutation evidence. Contaminated evidence cannot supply a
failure either. External dependency origins remain allowed; an
interpreter-controlled `._pth` runtime is runtime custody unless one of its roots
enters the live workspace, which is held. Legacy receipts
without an observation remain readable and neutral; deleting a required binding,
or retaining a malformed or changed one, fails closed.

### Linked dependencies keep custody of the copy (#4152, phase 2)

A proof copy uses the person's installed dependencies through a link, and an installed directory
can lead back into the live tree: an editable install's `.pth` file or finder names the live
`src/`, a script's `#!` line names the live `.venv`, and `npm link` or a workspace package makes
`node_modules/<name>` a link to the live package. The original code's copy then imported the
candidate's new module through it. Each copy now gets an environment of its own
(`autocode_dependency_dirs.expose`): a `.venv` or `venv` view whose directories down to each
`site-packages` and `bin` are its own and whose other entries link to the person's, with the files
that name paths (`.pth`, editable finders and egg-links, top-level modules, `#!` scripts,
`activate`, `pyvenv.cfg`) copied and re-pointed at the copy; and a `node_modules` view in which a
package, a scope's package or a `.bin` command that links into live source links to the copy's own
package. Python finds `pyvenv.cfg` beside the path it was started by, so the copy's interpreter
reads the copy's `site-packages`. The person's environment is never written, and a package
manager run in the copy changes only the view. The same view serves the regression proof's trees,
a package's own `node_modules` and the Tester's clean copy.

What a package manager installed is a dependency wherever it is installed. A file found in the
interpreter's site directories is classified by where it was found, before links are followed, and
an observed dependency inside the workspace's own `.venv`, `venv` or `node_modules` is not live
source (`autocode_source_binding.assess`). Before, a project that kept its virtualenv or its
`node_modules` in the project folder had every proof that imported an installed package held as
`UNVERIFIED`, while the same package installed beside the main checkout passed. Live source
reached through those directories resolves outside them and is still held: custody is what keeps
it out, and the boundary still holds what custody missed (a link nested inside an installed
package, a path a test names itself). Installed directories are shared by the original code's and
the candidate's copies, as before.

### A read boundary around each proof command (#4152, phase 3)

A test can still name the live tree itself: an absolute path, a fixture link, an ancestor a tool
walks up to that the observer does not model. Each command a proof runs in a proof copy therefore
runs inside a kernel read boundary where the host has one (`autocode_read_boundary`, applied in
`autocode_command_supervision.run`). A proof copy is a Git worktree of the live repository under a
scratch mirror, so Git lists what it must not read: every other checkout of the repository, which
is the live workspace, the main checkout, other chats' worktrees and the other proof copy. Those
are hidden; inside them the copy itself, its Git directories, the directory the command writes its
results to and the installed dependencies the copy links to stay visible. A deeper rule overrides a
shallower one, so a task worktree inside a visible directory is hidden again. Nothing else changes:
writes, the network, processes and the environment are as before.

Linux uses bubblewrap (an empty tmpfs over each hidden path, the visible ones bound back from the
host) and macOS Seatbelt (`file-read-data` denied under each hidden path and allowed again under
each visible one, in the same order; a path still resolves through a hidden directory, so links
into installed dependencies keep working). Each receipt records the boundary as `read_boundary`:
its backend and the hidden and visible paths. Where no boundary can be set up (no bubblewrap able
to create its namespaces, no working `sandbox-exec`, another platform, a checkout that holds the
home directory) the command runs as before and `read_boundary` records why, owner decision
2026-11-25: phases 1 and 2 still hold or prevent what they see, and holding every proof would stop
AutoCode on such a host. A command run outside a proof copy (in the workspace itself, or a native
check with its own owner) is not wrapped.

The saved-proof inventory found 180 retained verification receipts (168 unittest,
5 pytest, 3 Node, 2 Vitest and 2 Go), with zero source-binding fields. Those are
legacy compatibility evidence, not observations this detector made. The eligible
denominator for its hold rate is therefore zero and the rate is undefined; no
percentage is inferred from reconstructed fixtures. This is a receipt-admission
check, not a completed-model report check, so `autocode report-check` does not
apply.

## JUnit XML from other test runners

With nothing typed, a Maven, Gradle or CMake project is proven by its build tool's
own test command and that command's exit code, a Composer project with PHPUnit by
`vendor/bin/phpunit` and a Perl distribution by `prove -lr t` (`tools/autocode_toolchain_suite.py`).
Use `--test-report junit:RELATIVE_PATH` with an explicit `--test-command` to
attribute Java, Kotlin, .NET, Rust or Ruby tests by name through their runner's JUnit
XML output. For example, the catalog's Java case runs one real JUnit class:

```sh
autocode --workspace /path/to/project "Count partial pages" \
  --test-command 'mvn -o -B -q -Dtest=PageCountTest test' \
  --test-report junit:target/surefire-reports/TEST-example.PageCountTest.xml
```

Install the project's locked dependencies before running offline. The ordinary
suite command is also used for the candidate/original-source regression runs
when `--regression-command` is omitted. If provided, the regression command
must produce the same relative report path. Keep every named `guard:` case in
the command's selected tests as well as the new `test:` cases.

The selection persists in the run and is also available as project settings:

```toml
# pyproject.toml
[tool.autocode]
test-command = "mvn -o -B -q -Dtest=PageCountTest test"
test-report = "junit:target/surefire-reports/TEST-example.PageCountTest.xml"
```

A saved command/report correction uses the existing reconciled
`--resume-paused` repair boundary before the Tester or combined checkpoint.
When a completed Tester report was rejected and its queued repair has not launched,
a material verification-command correction archives that obsolete repair after
settings validation. Rejected artifacts, counters, usage and plan approval remain
retained; fresh runner proof and an independent Tester attempt are required.
Active or uncertain ownership and already launched report repairs remain held.
It cannot replace evidence during an active command or late acceptance.
Generic JUnit commands have no portable selector: stability repeats the unchanged
command only when its complete report contains exclusively the proving cases.
Otherwise the stability record reports that they ran once, without widening to
an unrelated whole suite. Existing reporter selection is unchanged.

This interface accepts one XML file per command, not a directory or glob. Use a
single test class/project or a runner that writes one combined report. The path
must stay inside each proof tree. Each command starts with that report removed,
so a previous result or a command that does not rewrite it cannot supply proof.
The runner captures the fresh XML beside its execution receipt and pins its
hash; changing that captured artifact invalidates the proof. Missing, malformed,
zero-test, incomplete or inconsistent reports cannot establish a named pass.
Failure/error/skipped outcomes retain their meanings; a nonzero command exit
with no genuine failing case cannot demonstrate a regression. This does not
change the existing Python, Go, node:test or Vitest reporters.

JUnit suite preservation pins the base test bodies and recognized build-system
selectors before running them over the candidate product. Standard Maven,
Gradle, .NET, cargo-nextest and RSpec commands have this boundary; an unknown
command cannot establish preservation merely by writing valid XML. Changes to
unclassified suite inputs remain `UNVERIFIED`. Existing Rust product files can
also define inline tests, so changing such a file currently leaves preservation
`UNVERIFIED`, even when its reported cases pass. The same conservative boundary
applies when an existing product file may contain nonstandard test definitions.
Command-selected custom build/test files, unbound project paths and arbitrary
build properties also leave preservation `UNVERIFIED` until their base inputs
can be bound. The trusted command still runs; its XML cannot prove that those
hidden definitions stayed intact.

Runner setup examples (configure unique test names matching the named criteria):

- **Maven / Java:** pin JUnit Jupiter and Maven Surefire in `pom.xml`. Run
  `mvn -Dtest=PageCountTest test`; use
  `junit:target/surefire-reports/TEST-example.PageCountTest.xml`.
  Surefire emits XML per class; `-Dtest` selects the class.
  [Surefire reports](https://maven.apache.org/surefire/maven-surefire-plugin/),
  [single-class selection](https://maven.apache.org/surefire/maven-surefire-plugin/examples/single-test.html).
- **Gradle / Java or Kotlin:** keep JUnit XML enabled and run
  `./gradlew test --tests example.PageCountTest --rerun-tasks`; use
  `junit:build/test-results/test/TEST-example.PageCountTest.xml` with Gradle's
  default layout. `reports.junitXml.outputLocation` changes the directory;
  align the report flag with it. Re-running tasks avoids treating Gradle's
  up-to-date result as fresh execution after the report was removed.
  [Gradle XML reports](https://docs.gradle.org/current/userguide/java_testing.html#sec:test_reporting).
- **.NET / VSTest:** add the compatible `JunitXml.TestLogger` NuGet package to
  the test project, then run from that project's directory:
  `dotnet test Tests.csproj --framework net8.0 --logger "junit;LogFilePath=junit.xml"`;
  use `junit:junit.xml`. Select one project and target framework so outputs do
  not overwrite each other. This is a third-party JUnit logger.
  `dotnet test --logger trx` writes **TRX, not JUnit XML**, and is not accepted
  by this interface.
  [JUnit logger](https://github.com/spekt/testlogger),
  [Microsoft's TRX documentation](https://learn.microsoft.com/dotnet/core/tools/dotnet-test-vstest).
- **Rust / cargo-nextest:** in `.config/nextest.toml`, set:

  ```toml
  [profile.default.junit]
  path = "junit.xml"
  ```

  Run `cargo nextest run` and use
  `junit:target/nextest/default/junit.xml`. The path is relative to the profile's
  store directory; with `--profile ci` and `[profile.ci.junit]`, it becomes
  `target/nextest/ci/junit.xml`. JUnit output is configured in the profile,
  not with a `--junit` command flag.
  [Nextest JUnit configuration](https://nexte.st/docs/machine-readable/junit/).
- **Ruby / RSpec:** a plain `bundle exec rspec` needs none of this (see above).
  To use JUnit XML instead, include `rspec_junit_formatter` in the project's bundle and
  run `bundle exec rspec --format RspecJunitFormatter --out junit.xml`; use
  `junit:junit.xml`. Keep the formatter in the same dependency group as RSpec.
  [Formatter usage](https://github.com/sj26/rspec_junit_formatter).

The `feature-java-page-count` catalog case exercises this interface with real
Maven 3.9.9, JUnit Jupiter 5.11.4 and Surefire 3.5.2. Its independent Java oracle
rejects wrong behavior and a green regression that also passes on the original
source. The other setup examples document their runners' interfaces; they are
not claims of locally executed qualification for those ecosystems.

## Mutation check

A proving test can fail without the fix and pass with it and still not pin the
behavior: `assertEqual(3, page_count(11, 5))` holds for a correct ceiling and for
a fix that is wrong when there is only one, partial page. Once a named proof
passes (its verdict final, after the case check), the runner looks for that by
changing the product code one small step at a time and running the proving tests
against each change (#699, `tools/autocode_mutation_check.py`, with the changes
from `tools/autocode_mutants.py`).

- **Where.** Only the changed lines of the change's product `.py` files, in the
  proof's own candidate scratch tree. Tests, files the change does not touch,
  type stubs (`.pyi`), files that are not UTF-8 (ASCII counts, however the coding
  cookie spells it), files that do not parse with the runner's Python and files
  with a carriage return no line feed follows (Python and git number their lines
  differently) are never changed; the record notes why.
- **What.** The operators negate a condition (`if`, `while`, a conditional
  expression, `assert`, a comprehension filter or a `case` guard); swap a bound
  (`<` and `<=`, `>` and `>=`); drop a branch (an `if` body or its `else` becomes
  `pass`, a decorated definition with its decorators; an `elif` is mutated as
  its own `if`); return a constant instead of the value; shift an integer
  bound by one: a literal beside `<`, `<=`, `>` or `>=` (an `==`, `!=`, `is` or
  `in` operand is not a bound); bind another constant where a bool, number
  or string literal is assigned to a name (`TEXT = "hello"` becomes `TEXT = ""`),
  so a line whose whole behavior is the value it binds has a site a test can
  see. The tuple assignment operator swaps the first two distinct names in a
  simple unpacking on a changed line. A line none of those reaches gets one
  statement site (#2815): a call made for its effect is dropped (`log.write(x)`
  becomes `None`), a computed value bound to a name becomes `None`, a `for`
  loop runs over nothing, an import or a `raise` becomes `pass`, an `except`
  clause catches `()` (nothing), the context manager of a `with` and the value
  of an augmented assignment (`count += 1`) become `None`, and a keyword argument
  or dictionary entry on a line of its own takes another constant or `None`. A
  list or tuple element on its own changed line also takes another constant,
  subject to the same import-safety and exact-mutation checks. A
  mutant counts for the line it starts on, so the later statements of a dropped
  branch get their own sites. A line holding only `try:` or `pass` does nothing a
  test could notice and is not a changed-line obligation at all. No mutant is
  made where the module would then fail to load: in a function that runs on
  import, or in module-level code whose result code run on import reads
  afterwards (an `except` around an optional import, a module-level `with`).
  Test collection would fail before any test ran, so that mutant could only end
  `not_run`, and the line counts as having no site.
  Each mutant replaces a span of source bytes,
  keeps every line number, must compile, and must change its statement exactly as
  intended; a site that fails one of these, or cannot be checked at all (a
  statement nested too deep, an integer too long to print), is skipped with a
  note and the next one takes its place.
  `TYPE_CHECKING` and `__main__` guards, f-strings, annotations and branches that
  already do nothing are left alone.
- **How many.** Current task proofs enumerate the eligible sites on every changed
  line within the existing mutation budget. Unrun sites stay explicit obligations.
  The older advisory collector and standalone review study retain their six-site
  sample per file. The same source and selection give the same sites.
- **How long.** Each window has a 900-second budget, the same limit as a check replay,
  covering its unmutated run and mutants. An incomplete current task proof continues
  its never-launched sites in further windows, within the saved stage allowance and
  remaining run active time (#3524). A proof the same invocation just made continues
  this way even when its runtime cannot be reused across invocations (a global
  interpreter, Node or JUnit), so a large study finishes there too (#3950); a saved
  proof from an earlier invocation still needs a reusable runtime identity. The extra time is charged to the run. Each
  completed window publishes a new proof artifact and retains the old one, its
  captures and all attempted outcomes, including failures and timeouts. Continuation
  requires unchanged source bytes, exact sites, test selection, commands, contract,
  runtime and dependencies, with intact owned captures. It stops if no new site ran.
  While a reusable study is progressing and unattempted sites remain, the controller
  schedules another bounded local step before launching the independent review.
  Saved run and model limits stay unchanged. A later step can resume the remaining
  sites without repeating functional proof checks; a changed context requires fresh
  proof. Unrun sites never count as evidence.
  Each refresh retains the exact prior site inventory and every attempted
  observation. A timed-out, failing or incomplete new reference records an
  unsuccessful refresh with its command receipts, rather than erasing the prior
  study. Completion stays unverified until a fresh measurement resolves that gap;
  line exceptions cannot waive an unsuccessful reference. The next window reserves
  the observed reference duration as well as one mutant's minimum allowance.
  The older advisory collector and standalone review study keep one window.
  Current task proofs prefer the independently
  observed passing regression tests by exact ID. When the regression command cannot
  select observed tests, they use the passing candidate-suite tests instead. Suite
  preservation still runs unchanged on the original and candidate code. The selected
  tests must pass a fresh, complete unmutated run before any mutant runs; an earlier
  suite or regression receipt cannot replace it. This preference does not itself
  refresh a saved measurement at Completion Review; normal validation requests proof
  under the current runtime identity, while previously qualified evidence stays valid.
  Once that unmutated reference run passes on all selected tests, enforcement mutation
  checks narrow each mutant's test command to the conservative AST/import/symbol
  reachability closure of selected tests (`tools/autocode_affected_tests.py`, recorded
  in `test_selection` and per-mutant `selected_tests`/`selection_rule`), falling back
  to all selected tests on dynamic or unmapped edges and recording provably unobserved
  lines as `survived` without launching unrelated tests. An import reaches every workspace
  module whose path ends in the imported name, so a module reached through any `sys.path`
  entry (a directory a test or `conftest.py` inserts, `PYTHONPATH`, `src/`, a start
  directory) selects the tests that import it.
  Older advisory collectors
  select only the named proving tests. Each collection starts with a fresh bytecode
  cache and reuses unchanged dependencies within that collection. Every mutated
  source version has a distinct whole-second modification time, so its old compile
  is invalidated, including in a Python the tests start without that cache setting.
  Original source bytes and modification times are restored after each mutant. A mutant may take
  ten times the unmutated run's duration, at least 60 seconds, and never more than the budget
  left: with fast proving tests one that never returns cannot spend the budget,
  but with slow ones it can use the rest of it. No mutant starts with less than
  60 seconds left; those, and a mutant the budget cut short, are `not_run`.
- **Which proofs.** Python `unittest` and `pytest` proofs, as above. Go,
  `node:test`, Vitest, Jest, Mocha, Cargo, RSpec and Minitest proofs are mutated too (#3817,
  `tools/autocode_text_mutants.py`): with no parser for those languages, a
  scanner marks which bytes are code, not a string, comment, template literal or
  regular expression, and eight operators match only code on a changed `.go`,
  `.js`, `.mjs`, `.cjs`, `.jsx`, `.ts`, `.mts`, `.cts` or `.tsx` line: negate a
  one-line `if` condition; swap a spaced bound (`<` and `<=`, `>` and `>=`),
  equality (`==` and `!=`, `===` and `!==`), logic (`&&` and `||`) or arithmetic
  operator (`+` and `-`, not beside a string); flip `true` and `false`; return
  `undefined` (JavaScript) or `nil` for a returned name (Go); and blank a call made
  as a statement on its own line. Spaces on both sides keep generics, shifts,
  arrows and Go's `<-` out. Nothing compiles a mutant beforehand: a Go mutant whose
  package does not build (`return nil` for an `int`, a blanked call that was a
  name's only use) is no mutant at all, so it is left out with a file note and its
  line needs a reason like any line without a site. A Ruby mutant is checked with
  `ruby -c` (through `bundle exec` when the proof runs through it) before its tests
  run: one Ruby refuses does not parse, so it is left out the same way, with the
  note `PATH:LINE: the OPERATOR mutant did not parse; not a site`; a parse check
  that times out or cannot start decides nothing, and the mutant runs. Any other
  mutant that does not load reports no complete result for its selected tests and
  ends `not_run`, never `killed`.
  Changed `.rs` and `.rb` lines get the same operators. Rust's scanner also skips
  raw strings (`r#"…"#`), character literals and nested block comments, keeps a
  lifetime as code, leaves an empty closure's `||` and a declaring line (`fn`,
  `impl`, `where`) alone, and returns `Default::default()`; a Rust call is blanked
  only when it ends with `;`. Ruby's skips `#` comments, `=begin` blocks, each
  `#{…}` interpolation inside its string, heredoc bodies and percent literals
  (`%w[…]`), returns `nil`, negates `if`, `elsif` and `unless`, and never swaps
  `===`, Ruby's case equality. A Cargo proof is narrowed with `-- --exact` and
  each test's harness path, an RSpec proof with one `--example` per full
  description, and a Minitest proof with Minitest's `-n` and one anchored regular
  expression over the proving tests' `Class#method` names (`-n
  '/^(LabelTest#test_one)$/'`): after the test file of `ruby FILE`, after `--` for
  `ruby -e` (the detected suite) and in `TESTOPTS` for `rake test`. A Minitest
  command that already filters by name (`-n`, `--name`, `-e`, `--exclude`) or that
  AutoCode does not read is not narrowed, and its record says
  `runner_cannot_select`. Cargo rebuilds a crate only when a source is newer than its last
  build, so each Cargo mutant and each restored source is given a modification
  time later than the one before, never an older one: a backdated mutant ran the
  unmutated build and survived whatever its tests asserted. Maven, Gradle and CTest
  builds (Make, Ninja) decide the same way and get the same later times.
- **Runners that name tests only in JUnit XML.** A Maven, Gradle, PHPUnit or CTest
  proof (the detected CMake suite ends with `ctest`) is judged by exit code, but each
  of these runners writes JUnit XML that names every test
  (`tools/autocode_runner_reports.py`): Surefire's `target/surefire-reports` and
  Gradle's `build/test-results` in every module, PHPUnit's `--log-junit` and CTest's
  `--output-junit` into the mutation check's evidence. Every run's earlier reports
  are removed first. The proof's own command runs unchanged for the unmutated
  reference and for each mutant, and the selected tests are the ones the reference
  reports passing; a mutant is `killed` only when one of them failed in a report
  that run wrote. A run that failed and wrote no report (a Java mutant that does not
  compile, a C mutant that breaks the build) is left out with the note
  `the OPERATOR mutant did not build; not a site`. Maven and Gradle stop at the
  first module whose tests fail, so a later module's tests may be missing from a
  killing run. Changed `.java`, `.kt`, `.kts`, `.c`, `.h`, `.cc`, `.cpp`, `.cxx`,
  `.hh`, `.hpp` and `.php` lines get the operators above, with a call blanked only
  when it ends with `;` (including `$this->save($x);` and `ptr->reset();`); a C or
  C++ `return VALUE;` returns `0`, and Java, Kotlin and PHP returns are no site,
  since they have no constant every type takes. PHP's scanner skips `#` comments
  but not `#[` attributes. A test that ends in an exception rather than a failed
  assertion is a JUnit `error`, which the report cannot tell from a broken setup, so
  it never kills a mutant.
  Go runs every mutant with `-count=1`: its test cache answered a test that builds
  and runs the package's command with the unmutated pass. These runners select tests by a name pattern, so the
  parent and siblings of a selected Go subtest, or another test the pattern
  matches, may run too; only the selected tests are judged, and a run where only
  such another test failed proves nothing. They are not narrowed per mutant and
  do not stop at the first failure. Any other runner's record says so in
  `unavailable` (`no_mutation_source`, or `runner_cannot_select` when the command
  cannot name the proving tests). A line with no mutation outcome, because its
  runner has no mutation source or the line has no mutation site, is settled only
  by the Tester's reason, so no Builder change can help it. When every open line
  is such a line and the Tester asked for no test, the lines go to the Investigator
  from the first round, not to the Builder (`builder_skipped` in the repair
  record). When only some are, the Builder is told which lines it can settle and
  which wait for the Tester's reasons.

A mutant that makes a proving test fail is `killed`. One that every proving test
still passes has `survived`, and is reported beside each due test case whose
test passed with it (keyed by the same test-case ids as `case_tests`), for
example ``test_partial_last_page_is_counted passes with pager/__init__.py:9
changed (`>` -> `>=`)``. The status view shows the record as
`evidence.mutation_check` ([the task-run interface](task-run.md)), the evidence
report lists each survivor under the regression proof, and the Tester and the
Completion Reviewer receive it with the proof. The runner combines exact-source
coverage and authenticated mutation observations in `changed_code_evidence`
(#2474). A line merely executed without a meaningful assertion does not pass.
A named test must actually fail under the mutation; a setup/collection failure,
missing test, interrupted command or timeout grants no positive evidence.
Enforced Python mutation runs stop after the first selected test failure. The
unmutated reference must still pass every selected test, and a survivor must run
and pass them all. A completed early-stop capture proves only the selected tests
it actually reports failing; its owned capture and explicit execution policy are
checked again when reconstructing changed-line evidence. Older reports retain
their requirement for every selected test result.

Every changed executable line needs demonstrated mutation evidence or a precise
reason checked by the existing independent Tester. This is its code-review
responsibility within the existing workflow, and cannot be self-approved by
Builder. A survivor can be equivalent (no test could observe the change); the
Tester can record that specific reason against the current offered gap ID and
pin its source/test/check evidence. Each obligation also carries a short handle
(`L1`, `L2`, … in inventory order), which the offer accepts beside the full ID and
the runner maps to the same exact line, so a model never retypes 64-hex IDs. An unavailable tool alone is not a blanket
exception. Installed dependencies (a file under any `node_modules`, `.venv` or `venv`
directory) are third-party code, not changed product lines, even in a project that does
not ignore them: the runner leaves them out of the inventory and records their count and
directories as `changed_code_inputs.installed_dependencies` (#3164). The gate's reason
names the first 40 open lines and counts the rest; every line stays in `obligations`. Empty survivor lists and the old six-site sample never prove that
all changed lines were tested.

Python's source inventory includes runtime-expression tokens as well as compiler
line-table entries, because constant folding can move a multiline element onto
another line in the bytecode. It retains meaningful string contents and delimiters
conservatively, excludes actual comments and existing inert declarations, and
uses Git's physical line numbering for the line quoted to the reviewer. A mutant
that starts on a parent line cannot prove a changed child line; nonpositive parent
observations spanning that line remain visible as diagnostic context.

A handle only names a line; it cannot show that the reason under it is about
that line. An accepted S6 report (#2977) put the reason for one line under the
handle of another: the handles resolved, so the gate passed lines no reason
described. Each gap the Tester decides therefore carries `source_line`, the text
of its exact line read from the measured bytes (whitespace runs collapsed to one
space, at most 200 characters), and each decision quotes it back in its own
`source_line`. A decision whose quote is another line's text is returned for
report repair, before clean verification runs and again when the decisions are
bound, with each handle's quoted and actual line; a repaired report is held to
the same check. The final report shows each line beside its reason, so the
reviewers judge the reason against the line it covers. A matching quote proves
only that the reason names its own line; whether the reason is adequate stays
the independent reviewers' judgment. A line whose bytes could not be read
carries no text and binds by its handle alone, and a report offered before this
check binds without a quote.

A PASS that omits an offered Tester decision for a line with no mutation
outcome is returned for report repair before clean verification commands run.
This early check uses the current source and offered gap bindings; missing or
stale bindings cannot establish such an omission. Complete and repaired reports
still take the fresh verification and exact evidence-admission gates.

The core returns unresolved exact lines to Builder for two bounded repair rounds,
then uses its existing Investigator path and saved run limits. It records those
decisions and the fallback in the public verification view and final report.
When a batch correction has narrowed the assignment to one component, a rejected
gap in another batch member first returns to the Completion Reviewer for
reassignment within the approved plan. That scope review neither expands the
Builder's permission nor consumes a Builder repair round; unresolved evidence
continues to block acceptance.
A validation-only task likewise returns to the Completion Reviewer when changed
lines remain unverified: its read-only assignment cannot authorize a Builder
repair. Review must assign any correction within the approved plan before a
Builder runs. This handoff preserves the line evidence, milestone obligations,
attempt history and no-progress counter; it grants no acceptance or new retry.
Missing project coverage tooling uses demonstrated mutation evidence alone where
available; otherwise the line remains open for independent judgment. No project
dependency is installed. Blank/comment-only lines, Python declaration-only lines
and non-executable lines excluded by an authenticated statement map are omitted.
Legacy proofs and standalone review mutation studies remain advisory records;
active task completion requires current evidence rather than inferring it from
those older records. A proof that did not pass runs no mutant.

It complements the check replay's sanity gate (`tools/autocode_test_quality.py`),
which rejects test bodies that assert nothing at all. A test that asserts
something, but not enough, passes that gate and shows up here.

### Tests in a reviewed patch

The review workflow keeps two test groups separate. A reviewer's delivered
finding tests must fail on the change to prove its blocking findings. They are
not the passing tests used for mutation checks.

For an explicit `change_patch`, the runner selects added or modified standard
Python test files from that patch, excluding `review/`. It applies the pinned
patch in its clean scratch copy, runs those original tests, and selects only
complete, observed passing test IDs. Every selected ID must pass again before
mutants run. It never executes the model's free-form `tests_run` strings or
falls back to the whole suite.

The check changes only product lines actually added by the patch, excluding
context lines, tests and unchanged files. It uses the same Python operators,
six-mutant file limit and one 900-second budget. Missing patches, unsupported
runners, absent test selections and changes without eligible mutation sites
produce an explicit not-run note. An empty selection is not evidence that the
tests reject wrong code. Unknown process cleanup remains a hold.

Review results expose `subject: reviewed_patch_tests` in
`evidence.mutation_check`, with the patch hash, selected files and test IDs.
Survivors are keyed by those actual test IDs, rather than invented acceptance
criteria. The findings artifact and canonical
`workflow_result.mutation_check` retain the same report; Markdown presents it
under **Review test mutation check**. This creates no named regression proof
and changes neither the review verdict nor the finding-test requirement.

## Contained Tester Evidence

A contained Tester cannot bind or connect sockets. If the named regression
tests exercise real HTTP/end-to-end behavior, it must not run that network-dependent
regression command again: inspect the named tests and cite the runner's existing
clean-copy proof, not a model claim of execution. At launch, the runner checks
the proof's source revision against the current source, its receipt hash, and
the integrity of its check artifacts using the existing regression validator,
without requiring the independent changed-line decisions this Tester is about
to supply. The full completion gate still requires those decisions. The entire
presented proof must also match the runner's fresh handoff or a deterministic
Core projection, including JSON types. A compact projection must bind every
moved original field, receipt index and count to the run-owned, content-addressed
context artifact and its exact byte hash; an arbitrary summary cannot supply
that authentication. Only then is `runner_regression_proof_current`
true; missing or altered handoff evidence is not authenticated even when the
saved runner proof is valid. A PASS receipt alone is not sufficient authority.
The tests must assert the approved flow, not only health or static behavior;
missing, stale, incomplete, failed, skipped or zero-test proof cannot establish
HTTP PASS. A current executed regression PASS with pending changed-line review
is evidence for that execution, not permission to complete the task. Generic
workloads with unavailable evidence are told to retain BLOCKED/NOT_VERIFIED,
rather than receiving an unrelated HTTP failure instruction.

The Tester still executes permitted independent non-network checks and captures
their receipts in `tool_containment.scratch`. It cites `regression_proof` directly
(`verdict`, `source_revision`, `case_tests`, `checks`, `path`), never through a
check that reads run files, which are absent from clean replay. This changes no
network permissions, replay policy or completion requirements. Uncontained
Testers retain the existing instruction to run the regression command themselves.

### Go test prerequisite metadata

A test may import a package from an already selected transitive module whose
indirect requirement or checksum is missing from the root manifests. Where the
approved plan permits it, the acceptance Tester may add that loading metadata
alongside its tests. The core admits only additive indirect requirements at the
original Go selection and missing checksums verified against offline cached
module artifacts. Existing requirements, versions, module/toolchain directives
and checksums cannot be replaced or removed through this exception.

The runner retains both native graph observations and exact manifest hashes,
including any change in transitive module selection. Those observations grant no
plan approval: a version constraint in the approved plan still applies. The
Builder must retain admitted metadata byte-for-byte, including for blind tests.
The final regression comparison uses identical admitted metadata on original and
corrected production source, while the original suite baseline keeps its original
inputs. Compilation failures never establish a bug reproduction. Other dependency
changes remain on the ordinary proof path and are not overlaid onto the original
source as test prerequisites.


### Python product and test roles

A test-like filename alone does not make a shipped Python module an oracle. The runner
uses the original Git tree, including its launch commit's dirty and untracked inputs,
to read literal setuptools package and module lists and ordinary literal imports.
Original product ownership keeps those bytes on the base side of a regression proof.
Dedicated test roots and supported test declarations keep test authority. A test root is
a `tests/`-style directory at any depth, or a `test/` or `spec/` directory at the repository
root or directly under a directory that is not a Python package (a project kept one directory
down, such as Docutils' `docutils/test/` beside its `docutils/docutils/` package); a `test/`
inside a package (`django/test/`) may be a shipped subpackage and is not a root. A directory
the original tree's pytest configuration names in `testpaths` is a test root too, read relative to
the directory of the configuration pytest itself would read there (`pytest.toml`, `pytest.ini`,
`pyproject.toml`, `tox.ini` or `setup.cfg`, in pytest's order): pytest keeps its suite in `testing/`.
A named directory that the packaging ships, or that code outside it imports by any dotted name, is
not one, so `testpaths = ["src", "tests"]` never makes the product a test; the configuration read
is recorded with the roles, and a change cannot name new test paths. One test
file beside the code it tests is a test in the same way: a `tests.py` at any depth (an
application's tests, as Django's `startapp` writes them and unittest discovery collects them),
and a `test.py` at the top of the repository. A `tests.py` the package's own code imports is product code whatever it declares
(`jinja2/tests.py` holds the template language's tests as functions named `test_odd`), and
a `test.py` inside a package is product code (a `test` command). Mixed or
unresolved changed roles remain unverified without executing proof commands. Dynamic
build configuration is not interpreted, and candidate packaging cannot relabel the
original product. Original top-level assertions with a literal `import unittest`
also declare an oracle; packaging or runtime import ownership still makes that mixed
role ambiguous. Unknown unchanged oracle entries remain protected conservatively.
Proofs from an earlier role policy require fresh execution before advancing.

In the other languages a file is a test by its name (`pager.test.js`, `pager_test.go`,
`PagerTest.java`) or by its directory. A single test file at the top of the repository is one too,
where the language's own tools do not say which files are tests: `test.js` (also `.mjs`, `.cjs`,
`.ts`, and `tests.`), which `node --test` and Jest collect and many small packages run as
`node test.js`; `test.c`, `test.cc` or `test.cpp`; and `test.sh`. A change to it is the change's
regression test, proven by its named tests or, where the suite reports only an exit code, by the
suite failing on the original code with the new test and passing with the change. Deeper in the
tree such a file stays product code (`lib/commands/test.js`).

A repository may hold several projects, each with its own manifest (`web/package.json` beside
`api/pyproject.toml`, `packages/pager/package.json`, `services/api/go.mod`). What holds tests at
the top of the repository holds them at the top of each of those projects: `web/test/pager.js`,
`web/spec/`, a single `web/test.js`. The projects are read from the original tree
(`autocode_project_kinds.project_roots`), so a manifest the change adds makes no code a test, and a
`test/` directory with no manifest beside it (`lib/test/`) stays product code. A Python project
below the root, and a Python file anywhere, are left to the role inventory above, which reads a
`test/` directory below the root from the Python packages around it. The proof, the original tests a run keeps and the plan check ask the rule of
the tree they read (`test_rule`); a reader with only a path sees the repository's own top.

The manifests say where the proof first runs a changed test, not where it must run. Many projects
have none: a backend with only `requirements.txt`, a directory of code and tests that declares
nothing, a Python directory inside a Node project. The proof runs the changed tests once where the
rules put them, and a file that does not load there (`collection_errors`, `uncollected` or no test
at all in the runner's results), or that no suite there runs, is run from each directory around
it, innermost first, then from the root. It runs from the first place where it loads
(`cd backend && python3 -m unittest -v tests/test_pager.py`; `autocode_test_homes`), a note says
which run showed what, and the home is kept for the run as `test_homes` in its state and in the
proof's receipt. No file name, language or layout is consulted, and nothing about passing changes:
the test must still fail on the original code and pass with the change, under one command for
both. See [the workflow](workflow.md) for the whole rule.

The original-test gate runs the command the run bound when it started, the suite of the
repository's root. Where that is a Python suite, or the root has none, and the changed original
tests are another language's files in one project below the root, the gate runs them with that
project's own suite, as the proof does (`cd web && node --test test/pager.js`;
`autocode_protected_project_suite`).
Python tests have the mirror problem: the bound Python runner can run them, but from the root, where
`python3 -m unittest -v api/tests/test_orders.py` cannot import `orders` from `api/`. Where the root
is no project of its own (it holds no manifest) and the changed test modules are all in one project
below it, the gate runs them inside that project with the bound runner
(`cd api && python3 -m unittest -v tests/test_orders.py`), where the proof runs them
(`autocode_package_scope.for_tests`). A command the person typed (`--test-command`, or a correction
of the original tests' command) runs where they typed it, and so does a runner that names something
by where it runs: an environment set before it, an interpreter named by a relative path, a pytest
option that names a path (`--rootdir`, `-c`, `--ignore`).
The gate does not work out again where a changed test runs. A test the proof saw load anywhere
but the root (`test_homes`, above: one it moved, or one the rules put in a project below the root)
runs in the gate with the command the proof used, on the change and again with the original tests
put back, so the two agree by reading one answer (`autocode_protected_oracles.replay_parts`,
`autocode_test_homes.away`, `replay_commands`). Changed tests
in more than one place (`api/` and `worker/`, or one project and the root) give one command for
each (`cd api && python3 -m unittest -v tests/test_orders.py ; cd worker && python3 -m unittest -v
tests/test_jobs.py`). Each must pass: the receipt keeps the runs under `suites`, and one that fails
with the original tests fails the gate. A changed file in such a project that is no test of its
own (a helper of its tests), or a deleted test, runs that project's whole suite. A Python
directory inside another language's project is covered the same way, with or without a manifest of
its own: its changed original tests now run in the gate, where the root's `npm test` never ran
them. Before the proof has run, and for a test that loads at the root, the bound command and the
rules above decide, as before.
An original internal test link also retains a declared oracle outside test roots
when packaging, runtime imports and dynamic uncertainty do not give it a conflicting
role. A candidate-added link cannot promote an original product or unknown endpoint.

At an idle saved pause before independent validation, an explicit resume can remove
positively proven original product entries from an old protected inventory. The runner
checks their bytes and modes against the original Git pin and authenticated archive,
retains every remaining original file and the same command, records both bindings and
the retired proof, and requires fresh proof. Status reads and terminal stops cannot
perform this correction. Ignored launch-source overlays leave the correction refused.

The correction remains in full historical custody. Planning packets and reviewer
handoffs carry only bounded counts, reasons, binding hashes and archive references;
they do not repeat entire original inventories. The public recovery history explains
the correction, and the evidence report cites its original and corrected inventories.

### Failed replay work

When a reported or approved check fails, times out, cannot execute, or lacks
complete owned-command evidence, its replay is rejected immediately. The saved
FAIL receipt retains completed checks and lists later checks and acceptance
observations under `not_run`; those observations remain unverified, not passed.
A corrected report must still satisfy every prescribed check and independent
acceptance gate. Deadlines, required repetitions and proof-reuse rules do not
change. Work already completed before the failure remains retained.
