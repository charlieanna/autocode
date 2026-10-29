# Named test proof

A plan's `test: test_c1_example` criterion requires that named test to pass with
the change and not before it. A `guard:` criterion must pass before and after.
When every criterion the proof covers is a `guard:` and the change adds or edits no test
(it may change nothing, as a validation-only re-check does),
the guards rest on tests the project already has: the proof runs the suite on the base and
the candidate, and each guard's named test must pass on both. A `test:` criterion still needs
a change, unless the person granted the test-only regression-proof exception.

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
Node's built-in `node:test` runner, Vitest 4, and an explicitly configured
JUnit XML report from another test runner.

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

Shell pipelines, custom reporters, ordinary npm/Jest/Mocha summaries and custom
assertion scripts do not currently provide named proof. A direct supported
targeted command can accompany an existing package-script suite. If the project
cannot use a supported runner, settle that compatibility blocker during planning;
do not downgrade an approved named criterion to prose to make a run finish.

## JUnit XML from other test runners

Use `--test-report junit:RELATIVE_PATH` with an explicit `--test-command` to
attribute Java, Kotlin, .NET, Rust or Ruby tests through their runner's JUnit
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
- **Ruby / RSpec:** include `rspec_junit_formatter` in the project's bundle and
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
- **What.** Five operators: negate a condition (`if`, `while`, a conditional
  expression, `assert`, a comprehension filter or a `case` guard); swap a bound
  (`<` and `<=`, `>` and `>=`); drop a branch (an `if` body or its `else` becomes
  `pass`, a decorated definition with its decorators; an `elif` is mutated as
  its own `if`); return a constant instead of the value; and shift an integer
  bound by one: a literal beside `<`, `<=`, `>` or `>=` (an `==`, `!=`, `is` or
  `in` operand is not a bound). Each mutant replaces the bytes of one syntax node,
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
- **How long.** One 900-second budget, the same limit as a check replay, covers
  one unmutated run and every mutant. Current task proofs select the independently
  observed passing candidate-suite tests by exact ID; older advisory collectors
  select only the named proving tests. Each mutant uses its own empty
  bytecode cache and its own modification time, so a compile cached by a Python
  the tests start themselves is never reused. A mutant may take ten times the
  unmutated run's duration, at least 60 seconds, and never more than the budget
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

Every changed executable line needs demonstrated mutation evidence or a precise
reason checked by the existing independent Tester. This is its code-review
responsibility within the existing workflow, and cannot be self-approved by
Builder. A survivor can be equivalent (no test could observe the change); the
Tester can record that specific reason against the current offered gap ID and
pin its source/test/check evidence. An unavailable tool alone is not a blanket
exception. Empty survivor lists and the old six-site sample never prove that
all changed lines were tested.

The core returns unresolved exact lines to Builder for two bounded repair rounds,
then uses its existing Investigator path and saved run limits. It records those
decisions and the fallback in the public verification view and final report.
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
the integrity of its check artifacts using the existing completion-proof
validator. The entire presented proof must also match the runner's fresh handoff
projection, including JSON types. Only then is `runner_regression_proof_current`
true; missing or altered handoff evidence is not authenticated even when the
saved runner proof is valid. A PASS receipt alone is not sufficient authority.
The tests must assert the approved flow, not only health or static behavior;
missing, stale, incomplete, failed, skipped or zero-test proof cannot establish
HTTP PASS.

The Tester still executes permitted independent non-network checks and captures
their receipts in `tool_containment.scratch`. It cites `regression_proof` directly
(`verdict`, `source_revision`, `case_tests`, `checks`, `path`), never through a
check that reads run files, which are absent from clean replay. This changes no
network permissions, replay policy or completion requirements. Uncontained
Testers retain the existing instruction to run the regression command themselves.
