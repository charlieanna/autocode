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
- The changed test files are the files under `tests/` (and other test paths). A test added to a
  `#[cfg(test)]` module inside a source file is part of the source: the proof cannot run it
  against the original code, so plans put a new Rust test in a file under `tests/`.

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
JUnit report is configured, Ruby test files run one by
one, or a runner AutoCode does not detect) cannot prove a `test:` or `guard:`
criterion by name, so a draft that has one there is refused when it is drafted,
and the Planner writes the criterion as the command that runs its test file
instead. Behavior that must keep working is guarded there by the command that runs
the existing tests, ``guard: run `ruby test/pager_test.rb` `` (or the detected suite,
``guard: run `make test` ``): the proof runs that command on the original code and with
the change, and both must pass. The command must run tests the original code has,
which is checked when the plan is drafted. When every criterion the proof is given
is such a guard and the change touches no test file, the change is coverage of
existing behavior, as one guarded by named tests is, and needs no new test: a
refactor of a Minitest, `make test` or shell project is proven this way. A bug fix
still needs a test that fails without it, a change with a test file is proven as
before, and changed product lines still need the Tester's evidence. A project whose
runner names its tests keeps naming them: a command guard there is refused when the
plan is drafted (`tools/autocode_command_guards.py`, owner decision 2026-11-13).

A repair whose regression test the project already had changes no test. Where the runner
names its tests, the tests that fail on the original code and pass with the change are
the proof. A Mocha suite, Minitest files, a test script the approved plan names, and a
Jest command AutoCode leaves as written report only an exit code, so there the suite shows the repair as a whole: it
failed on the original code and passes with the change. That counts when the change is
code alone and touches no test, and the suite's own files (its tests, manifests and
runner configs) are the ones the original code had, or the original definition also
passes over the new code. A file a shell test sources (`. ./pager.sh`) is the code
under test there, as a file a JavaScript test imports is, and not one of the suite's
own files; a script the test runs stays one
(`autocode_suite_definition_scan.sourced_product`). `make test` and a script
package.json names run whatever a recipe says, so they do not show a repair this way,
and a change that also edits another kind of file is judged as before
(`tools/autocode_first_suite.py`, `exit_flip`). When AutoCode detects no runner and nothing was typed, the regression
proof runs the approved plan's own test command and judges its exit code
(`tools/autocode_plan_proof_command.py`), recording that choice in the proof's
notes and review reasons; it never asks the person to pass `--test-command`. Do
not downgrade an approved named criterion to prose to make a run finish. A
draft whose test files the proof's suite would never run, such as RSpec specs in
a repository whose root suite is Node, is refused when it is drafted unless one
directory with its own suite holds them all (see the monorepo packages section of
[workflow](workflow.md)), so the Planner settles it before a Builder runs. So is a
build or bug-fix draft that names code files and no test in a project with no test
code: every change to code needs a test, and nothing there can guard the change
instead (`tools/autocode_plan_test_suite.py`, `untested_code`).

## JUnit XML from other test runners

With nothing typed, a Maven, Gradle or CMake project is proven by its build tool's
own test command and that command's exit code (`tools/autocode_toolchain_suite.py`).
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
- **What.** Ten operators: negate a condition (`if`, `while`, a conditional
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
  remaining run active time (#3524). The extra time is charged to the run. Each
  completed window publishes a new proof artifact and retains the old one, its
  captures and all attempted outcomes, including failures and timeouts. Continuation
  requires unchanged source bytes, exact sites, test selection, commands, contract,
  runtime and dependencies, with intact owned captures. It stops if no new site ran.
  A later review can resume the remaining sites without repeating functional proof
  checks; a changed context requires fresh proof. Unrun sites never count as evidence.
  The older advisory collector and standalone review study keep one window.
  Current task proofs prefer the independently
  observed passing regression tests by exact ID. When the regression command cannot
  select observed tests, they use the passing candidate-suite tests instead. Suite
  preservation still runs unchanged on the original and candidate code. The selected
  tests must pass a fresh, complete unmutated run before any mutant runs; an earlier
  suite or regression receipt cannot replace it. This preference does not itself
  refresh a saved measurement at Completion Review; normal validation requests proof
  under the current runtime identity, while previously qualified evidence stays valid.
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
- **Which proofs.** Python `unittest` and `pytest` only for now. A Go, `node:test`
  or Vitest proof gets a note and no run.

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
inside a package (`django/test/`) may be a shipped subpackage and is not a root. Mixed or
unresolved changed roles remain unverified without executing proof commands. Dynamic
build configuration is not interpreted, and candidate packaging cannot relabel the
original product. Original top-level assertions with a literal `import unittest`
also declare an oracle; packaging or runtime import ownership still makes that mixed
role ambiguous. Unknown unchanged oracle entries remain protected conservatively.
Proofs from an earlier role policy require fresh execution before advancing.
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
