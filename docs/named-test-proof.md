# Named test proof

A plan's `test: test_c1_example` criterion requires that named test to pass with
the change and not before it. A `guard:` criterion must pass before and after.
When every criterion the proof covers is a `guard:` and the change adds or edits no test
(it may change nothing, as a validation-only re-check of a merged program workstream does),
the guards rest on tests the project already has: the proof runs the suite on the base and
the candidate, and each guard's named test must pass on both. A `test:` criterion still needs
a change, unless the person granted the test-only regression-proof exception.

A guard that names its test exactly (`guard: test_c4_adds`) may name a test the project
already has in a file the change leaves alone, as a program's final check does for the
criteria the merged workstreams proved. The targeted run covers only the changed test files,
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
An exit code or a printed `PASS test_c1_example` is not enough to identify which
case ran. AutoCode currently attributes tests from Python unittest/pytest, Go,
Node's built-in `node:test` runner, and Vitest 4.

The identifier right after `test:` or `guard:` is the one the runner proves; an
explanation may follow after ` — ` or in parentheses. Without one, the test is
named after the criterion ID (`test_c1_...`; Go's `TestC1...` matches it).

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
"tests" or "func" (or in a list introduced as tests, each name with or without
a description), not negated or offered as an example, the proof will run Go
tests, and the project's Go files do not already contain it.
The user's own `--edit-goal` is never refused by this check and settles which
requested names stay. `TestMain` and `TestXxx` are never tests to write. Other
frameworks, and bug fixes proven by their diagnosis's cases, keep the
criterion-ID convention. The details and limits are in
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

Shell pipelines, custom reporters, ordinary npm/Jest/Mocha summaries and custom
assertion scripts do not currently provide named proof. A direct supported
targeted command can accompany an existing package-script suite. If the project
cannot use a supported runner, settle that compatibility blocker during planning;
do not downgrade an approved named criterion to prose to make a run finish.
