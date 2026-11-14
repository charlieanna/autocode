# Which kinds of task AutoCode completes: a survey (2026-11-13)

The catalog covers bug fixes, features, new projects, reviews, designs and discussions, mostly in
Python, with Go, Node, Vitest and Java cases. This survey ran small tasks of kinds it does not hold,
to find where a run refuses, stalls or asks the person about AutoCode's own machinery. Each task is a
one-function paging library with a one-line change, so a stop is AutoCode's and not the model's.

Method: master `6d8f4e4`, Linux cloud container. Twelve task kinds ran with the scripted
provider (`scenarios/run.py run --fake`). Eight of them then ran once on Claude Code
(`--profile claude-tiers`: Sonnet 5.5 Requirements, Planner and Tester, Opus 5.5 reviewers,
Haiku 4.5 Builder), $9.15 in all, and $12.59 more for the eight runs after a fix. Sections 5 to 7
(JavaScript) are six later runs, $10.86, section 8 (RSpec) four more, $5.51, and section 9 (Minitest)
three, $4.82. Sections 10 to 12 are fifteen runs on master `c6b9f7f`, $17.59, and section 14 four more, $5.10.

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

The same limit held for RSpec, whose examples are named by free text. It is fixed in section 5 for
node:test and Vitest and in section 8 for RSpec.

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

RSpec, whose examples are also named by free text, is section 8.

## 8. Ruby with RSpec: the plan is never told RSpec names its tests (fixed)

The proof has read RSpec's results per example since #2889, but the planning note on named tests listed
Python, Go, node:test, Vitest and Cargo, and told the Planner to write a command for a project "with
none of these runners". Two tasks in a paging library with a `Gemfile`, a `.rspec` and one spec file:

- **Refactor (extract a helper)**: manifest
  `91e6a760613716de56b8d389e9cd7fede59d2e39e6488eeeec09245ff19d3ab9`. The plan checked both criteria
  with `ruby -e` commands and `bundle exec rspec`, no `test:` or `guard:`, and allowed `lib/pager.rb`
  alone. The proof failed twice with "No regression test was added or changed", and the Resolver
  asked the person "Should the approved scope be widened so the Builder may add one new test-only
  file, spec/pager_require_positive_spec.rb?" (254 s, $1.58). That is a question about machinery.
- **Bug fix**: manifest `de3bc8b1c7da031cf6a5d02234892f5b1e636b8de079458f717eeda4d0d9c941`. The plan
  guarded the two existing examples in prose ("guard: the existing example 'returns the page count
  for an exact multiple' in spec/pager_spec.rb; run ..."). Each was looked for as `test_ac2_...`,
  two proofs failed, and after the Completion Reviewer, the Resolver and the Investigator the
  Builder copied both examples under those names (312 s, $1.89, 16 stages).

The note now names RSpec, how to name a new example (`it "test_c2_example" do`) and an existing one
(by its own description). A guard written as an existing example's description, or its full
description, names that example (`autocode_test_cases.titled`): RSpec's id is the full description,
so the example's own words are its last words. An example's own name was the last word of its
description ("page"), so any changed file using that word took every example out of the guards; it
is now the full description, which a file names by quoting it or its last words
(`autocode_test_cases.function_name`, `named_in`).

On the fixed tree both tasks were planned with `test:` and `guard:` criteria, bound every guard to
its existing example in the first proof and completed without a question: the bug fix in 138 s,
$0.84, 8 stages (manifest `5fc86af1253c48a6d3014697c2ac0964d16702271f06ef2fe2f121695b0b04ad`) and the
refactor in 185 s, $1.19, 10 stages (manifest
`ccb0c9e87921dda10a1378a664e8bdac4b5fb288fb5b2bfc38ab00047c92c350`), with one new spec file for the
helper and the three existing examples as guards.

A refactor in a project whose runner names no test is section 9.

## 9. Minitest: a refactor cannot be guarded, and the person is asked (fixed)

Manifest `390c97ca66d6ca6dfd91d921defcef0c42832c0c2e726d671c73c1389e9c627d`. The same refactor (extract
`require_positive`) in a project with one Minitest file. Minitest results are not read per test, so
the plan could name no guard: it checked the change with `ruby test/pager_test.rb` and a `ruby -e`
probe and allowed `lib/pager.rb` alone. The proof failed twice with "No regression test was added or
changed", and after the Completion Reviewer, the Resolver and the Investigator the Resolver asked
"Allow add-only edits to test/pager_test.rb? ... Without a new test, the regression proof stays FAIL".
The scenario's stand-in person allowed it, the plan was revised, the Builder added tests and the run
finished: 431 s, $3.04, 20 stages. A project whose tests are named guards such a change with its
existing tests; one that reports only an exit code (Minitest, `make test`, a shell suite) could not,
so a change that alters no behavior could only be proven with a new test.

Owner decision, 2026-11-13 ("existing tests pass before and after"): there a plan guards with the
command that runs the existing tests, ``guard: run `ruby test/pager_test.rb` ``, or the detected suite. The
proof runs it on the original code and with the change, and both must pass. When every criterion the
proof is given is such a guard and the change touches no test file, the change needs no new test, as
with named guards (`autocode_command_guards`). The command must run tests the original code has,
checked when the plan is drafted; a bug fix still needs a test that fails without it; a change with
a test file is proven as before; a project whose runner names its tests keeps naming them.

On the fixed tree the Planner wrote ``guard: run `ruby test/pager_test.rb` `` and two criteria for the
Tester, the first proof passed with the guard's command at exit 0 on both sides, and the run completed
without a question: 142 s, $0.88, 8 stages (manifest
`7adb00ce5a6656d0ef855594ebcce3fdbbb966adcd1a22e2b9bce050d23a04b9`). A first form without the word "run"
completed the same way (160 s, $0.90, manifest
`f65d7c79103a8dc82babad76d2165269c6f2bb5bc3f9f12bbe8e5f3c33be06e4`) but could not be told from a
test's name or title, which plans also write in backticks.

## 10. More kinds of task, on Claude models

Twelve more kinds on master `c6b9f7f`. A question the harness answered for the person
counts as asking the person.

| Task | Result | Bundle manifest |
| --- | --- | --- |
| Jest: bug fix with a regression test | complete, 142 s, $0.87 | `1eacda67c99dba3c3a15a07afa1ad6bcb4ce2a7a192a8f5fc280be7803705683` |
| Jest: refactor | complete, 158 s, $0.94 | `239c5133bb27636f92c64fd1783b32ed48697c9b45e4caa96e4bd61288c76119` |
| Documentation only (README section) | complete, 116 s, $0.66 | `d0d3dfd1107a11a0eb3a8dd69ec26a7701816e6617fd8fec8705f499be23a9d6` |
| CI workflow only | complete, 159 s, $0.75 | `da9fee47bb16965b7e79da9904042061492f765260d0447758c1c29ca1b9cb22` |
| Tests only (Python) | complete, 117 s, $0.72 | `2d5f451eecdba97870c0ed05a6a12b7e162dd938beea812b38590add85fbf648` |
| CSS change in a static site with no tests | complete, 139 s, $0.72 | `1328921765433c26b4f0c9ec3ba8b40f67d19906e960678280c90f470282f854` |
| Add a Dockerfile and .dockerignore | complete, 178 s, $0.84 | `bc3015eb14cfa2a20ef19318c441623b496858141805a11f77d7801ad2051591` |
| Add a SQL migration | complete, 215 s, $1.31 | `d3946f93fa2d5241d810bebdf7739fcdcac88d64bf46ac4e76a55dbf4666c217` |
| "This test fails, fix the code, do not change the tests" (Python) | complete, 109 s, $0.69 | `c6678043b526dd9a0c11528c5a1f1fe01b8814c2be7a80a89978955b3b9334b3` |
| The same in a Jest project | asks the person (section 11) | `c6afa2cbbca195dcc315394a5f633f07d5fa372c79605e46cf6634f274e655f0` |
| "Make this slow function fast" (Python) | complete, 221 s, $1.55, on the second run | `fbe91e7d494838c5170f656a064d0afc10e6d0308613927c15495da135d9b689` |
| Add an option to a script in a project with no tests | complete after asking the person, 394 s, $2.91 | `5b748731f3a0361cac4f1a9495765f0af660d2ccfd543f45c7dcfb3438669633` |

Both Jest plans were sent back once when drafted: the Planner first guarded with test titles, as it
does for `node:test` and Vitest, and the plan check told it that Jest names no test. The refactor then
used ``guard: run `npm test --silent` `` (section 9).

The first run of the performance task did not finish in the harness's 30 minutes ($0.84). Its new test
called `count_primes(2000000)` with no time bound, and the proof ran that test on the original, slow
code, where it runs for hours; each such run ends at the proof's limit, 30 minutes in this run. The
second run's test bounded its own time and the task completed (section 13).

The script with no tests planned `hello.py` alone, with shell commands as its criteria. A project with
no test has nothing to guard the change with, so the proof failed twice with "No regression test was added
or changed", and the Resolver asked "Allow one new test file, tests/test_hello.py, as an exception to
'Edit only hello.py'?" The run finished after 20 stages. This is known when the plan is drafted (section 12).

## 11. A failing test to repair, where no test is named: the person is asked (fixed)

"The test 'counts a partial last page' in tests/pager.test.js fails. Fix src/pager.js so that it passes. Do
not change the tests." In a Python project the plan names the failing test (`test: test_partial_last_page`),
it fails on the original code and passes with the change, and that is the proof
(`first_suite.existing_flip`). Jest reports only an exit code, so the plan's criterion was "`npm test
--silent` fails on the original code and passes with the fix", and the change was `src/pager.js` alone.
The proof failed twice with "No regression test was added or changed", and the Resolver asked the person to
set a regression command (256 s, $1.57).

Fixed: a suite that names no test shows the same repair as a whole. When the change is code alone and
touches no test, and the suite failed on the original code, the proof needs it to pass with the change,
with the suite's own files (its tests, manifests and runner configs) as the original code had them
(`first_suite.exit_flip`). That holds for the suites whose inputs AutoCode knows: Jest, Mocha, Minitest
files, and a test script the approved plan names where no runner is detected. `make test` and a
package.json script run whatever a recipe says, so those keep the old judgement (a shell test that
sources the product did too, until section 14). On the fixed tree the same request completed
without a question (346 s, $2.20, of which about half was a Tester report repair unrelated to the proof;
manifest `3cacb2fc3b57d06c8fba7b474c3d94e3576ac73e8c0c515f2376af2e48560521`).

## 12. A project with no tests: the plan changes code and plans no test (fixed)

"Add a --shout option to hello.py that prints the greeting in upper case", in a folder that holds
`hello.py` and nothing else. The plan named `hello.py` alone, with shell commands as its criteria and the
constraint "Edit only hello.py". Every change to code owes the proof a test, and a project with no test
has none to guard the change with either, so that plan could not pass: the proof failed twice with "No
regression test was added or changed", the Resolver asked the person to allow `tests/test_hello.py`, and
the run finished after 20 stages (394 s, $2.91).

Fixed: such a plan goes back to the Planner when it is drafted (`autocode_plan_test_suite.untested_code`).
Only the certain case is judged: a build or bug fix, in a project with no test code, whose plan names a
code file, names no test file or test directory, and has no `test:` or `guard:` criterion. A project that
has tests is not judged, since its tests may guard the change or already hold the failing test.

On the fixed tree the same request completed without a question (154 s, $1.02, 8 stages, manifest
`173589766bdd2b0910947f4a63c38f0b769ddb2ee4fbbb8a75a9f41c286e897a`). In that run the Planner planned
`test_hello.py` in its first draft, so the new check had nothing to refuse; what a plan of code alone
gets is shown by the unit tests.

## 13. A new test that never finishes on the original code holds the run (fixed)

"count_primes(2000000) is too slow, make it fast." The Builder's test called `count_primes(2000000)` and
compared the count, with no time limit. With the change it passes in under a second. The proof then runs
the same test on the original code, where it must fail, and there it only runs: until the suite's limit,
which follows the run's tool-call limit and was 30 minutes. A proof whose cases name tests would have run
it there twice more to check the result is stable, and once more with the whole suite for the plan's
guards, and would then have said that the command did not complete. A fix for a loop that never ends
meets the same wait.

Fixed (`tools/autocode_base_bound.py`): the run with the change says how long the proving tests take when
they finish, and on the original code they get thirty times that, never less than five minutes, since
tests that fail there may first wait out timeouts of their own. Past that the run is stopped. The proof
is unverified, as for any run that does not complete on the original code, and says why in words the
Builder can act on: give the test its own time limit, so that it fails on the original code. Nothing
repeats a run that timed out there, and the guards do not run the suite with the same test again. A
stopped run is never counted as a failing test.

Not run again on real models: whether a Builder writes an unbounded test is the model's choice (one run
of two did), and the proof's part is shown by `tests/test_base_bound.py`, which proves a real Python
project whose original code never returns.

Still open: a project whose runner names no test and has no targeted command proves with the whole suite
on the original code, which is run under the suite's own limit.

## 14. Shell: three more runs, and a failing shell test to repair asks the person (fixed)

Three runs on the tree with the fixes above, $4.28, and one after this fix, $0.82:

| Task | Result | Bundle manifest |
| --- | --- | --- |
| Shell bug fix with a regression test (`pager.sh`, `tests/test_pager.sh`) | complete, 268 s, $1.65; one question (below) | `d1965f9eb89e8133dd69e1eca028a1e7e746a1c70772f9797b0e1754d7596503` |
| "`sh tests/test_pager.sh` fails, fix `pager.sh`, do not change the tests" | asked the person, 195 s, $1.21 | `45c40a2554d79b47d85b26e247fbb490af423cae5ce895ac85dea87dddd64bc6` |
| Python: "drain() never returns for a list with items", with a regression test | complete, 191 s, $1.42 | `126d298960b227cd3f9ea3077e4ba2366ab5a9ad9ec9334773c1814b8178e1ba` |

The repair of the failing shell test is section 11 again. The test sources the code it tests
(`. ./pager.sh`), and the suite's definition held every file a test script names at the original, so the
original test never ran the fixed `pager.sh`: the proof failed twice with "the suite that fails on the
original code is not shown to pass with the change", and the Resolver asked the person to allow a new test
file. Fixed: when a repair rests on the suite's exit code, a file the original shell tests source is the
code under test, as a file a JavaScript test imports is (#587), and the original test runs over the
changed file (`autocode_suite_definition_scan.sourced_product`). A script the test runs
(`sh ./checks.sh`) is still one of the suite's own files, so a change that drops a check from it shows no
repair.

On the fixed tree the same request completed without a question (132 s, $0.82, 8 stages, manifest
`a20978095797ba616679c3e3e61681cbce444751750d1edd3c7a1019b544478f`): the proof ran the original test over
the changed `pager.sh` and passed with "its suite `sh tests/test_pager.sh` fails on the original code
(exit 1) and passes with the change".

In the fix for the function that never returns, the Builder's test gave itself a time limit, so the
bound of section 13 had nothing to stop.

The shell bug fix asked one question, which the harness answered (section 15).

## 15. The person is asked to accept "new wording" when only a proof changed (fixed)

In the shell bug fix the plan guarded the existing behavior with ``guard: run `sh tests/test_pager.sh` ``
and changed that test file. The Plan Reviewer raised a blocking concern: it took the guard to run the
changed test file on the original code, where it would fail. (It does not: a guard's command runs the
original code's own files there, and that proof later passed.) The Planner answered by replacing the
guard with an inline command. A proof the runner checks may not become a plain command, and because a
blocking concern had asked for the change, the refusal became a question to the person (#1630):

    AC2 approved: "Given pager.sh sourced, when `page_count 10 5` runs, then it prints exactly 2 ..."
    AC2 proposed: "Given pager.sh sourced, when `page_count 10 5` runs, then it prints exactly 2 ..."
    AC2 approved proof: "guard: run `sh tests/test_pager.sh`"
    AC2 proposed proof: "sh -c '. ./pager.sh; [ \"$(page_count 10 5)\" = 2 ]'"
    Reply 'Accept the new wording' to let the next plan use it, or 'Keep the approved wording'.

The two sentences are the same. What is asked is how AutoCode proves a criterion, which is its own
machinery (owner decision, 2026-10-29).

Fixed: a revision that leaves a criterion's words and its human review as they were is not the
person's question. It is refused as before and goes back to the Planner with the hint that names what
to keep (`autocode_contract_revision._review_question`). A revision the Plan Reviewer asked for that
changes the words, or hands the criterion to the person to review, still asks. Shown by
`tests/test_rewording_question.py`; not rerun on real models, since the question depends on the Plan
Reviewer raising that concern.

Not fixed: the Plan Reviewer's concern itself. The prompt note on command guards does not say which
files a guard's command runs on the original code.

## 16. A requested change that an existing test contradicts (fixed for Python, Node, Jest, RSpec and Vitest)

"pageCount(0, 5) returns 0. An empty list should still show one page: make pageCount return 1 when total
is 0. Update the tests to match." The project has a test that says an empty list has no pages. Four runs
of the same request, $6.85:

| Project | Result | Bundle manifest |
| --- | --- | --- |
| Python, unittest | complete after two failed proofs, 18 stages, 327 s, $2.48 | `608f9558ec693b0ffc558a2d6d03a330498e97e515c413ab0f647a0ba105f30d` |
| Node, `node:test` run by `npm test` | stopped for the person, 15 stages, 317 s, $1.82 | `669680619a414015e458846fa6c661e876105648068d4e51275d8c600e69c139` |
| Jest | asked the person to approve a hand-built plan revision, 276 s, $1.69 | `f877a77ab350ba103c03936191f7650633c06d0b56c76fca2d7288ac51bbc648` |
| Shell | stopped for the person, 169 s, $0.86 | `0012058fe6377620fbc816a1ba9ec1aadd2005b576b8b304de215f7010b28027` |

An approved plan may list the existing tests it replaces (`supersedes`, #2748), and the Planner did so in
the Python and Node runs. Three things then went wrong.

**The replaced test had to keep its old name.** In both runs the Builder wrote the new test and took the
old one out. The proof failed twice with "Existing tests were removed" and "renamed or missing", the
Completion Reviewer asked for rework, and an Investigator had the old name put back around the new
assertion. The Python run ended with `test_empty_list_has_no_pages` asserting one page. Fixed, owner
decision 2026-11-13: a test the approved plan lists as replaced may be renamed or removed. The proof no
longer counts it among the tests that disappeared and records it in its notes
(`autocode_superseded_tests.replaced`). A listed test that is still there and skipped is not gone, and a
test the plan does not list may not go.

**The check of the original tests had no results per test.** It runs the original tests against the
change and accepts a failure only of the listed tests. In the Node run its command was the project's
suite, `npm test --silent`, which it ran for its exit code alone: only a plain `python`, `node --test`,
`go test` or `cargo test` command was run with the runner's reporter. It could not see that the one
failing test was the listed one, and the run stopped for the person although the plan, the code and the
tests were right. Fixed: a command whose reporter the proof owns (`npm test` in a `node:test` project,
RSpec, Vitest) is run with it there too (`autocode_verify.scratch_run`), and a listed test is matched to
a Vitest id (`FILE::group > title`) and an RSpec id (the example's full description).

**Jest and shell report no test by name.** The plan cannot list a test to replace there, since only a
`test:` criterion may. Jest is the next section; shell, Minitest, Mocha and `make test` projects are
open.

The same requests on the fixed tree, $2.12, no question and one plan approval each:

| Project | Result | Bundle manifest |
| --- | --- | --- |
| Python | complete, 10 stages, 159 s, $1.29; the old test replaced by `test_ac1_empty_list_has_one_page` | `f2734cbca6be9bb92cae0f899b018ae8bf5ff5baa6268d90caf21de101fbd8f2` |
| Node | complete, 8 stages, 147 s, $0.83; the old test renamed and given the new assertion | `e0d9f61456af3ba5cfc8bee63f505379732d8e2604ebb2697a696ba636df9fcf` |

Both proofs passed first time with "Tests the approved plan replaces are no longer in the suite", and
the check of the original tests accepted the one listed failure.

## 17. A Jest suite reports no test by name (fixed)

A Jest run gave the proof only an exit code, so a plan could not name a Jest test as proof, as a guard
or as a test it replaces. The Jest run of section 16 therefore ended with the Resolver asking the person
to approve a hand-built plan revision, and every earlier Jest run proved its criteria with whole commands.

Jest ships a JSON report. The proof now adds `--json --outputFile` to a plain Jest command (`jest`,
`npx --no-install jest`, `node node_modules/jest/bin/jest.js`, an `npm test` whose script is one) and
reads it (`tools/autocode_jest_tests.py`): ids are `FILE::describe > title`, as Vitest's are. Nothing is
installed. Checked against Jest 30.5.2's own report, including a file that does not load, tests that
share a name, skipped and todo tests, and a run that fails outside every test.

Two runs on the fixed tree, $1.63, each complete at its first proof with one plan approval and no question:

| Task | Result | Bundle manifest |
| --- | --- | --- |
| The change of section 16 | complete, 8 stages, 161 s, $0.82 (before: a question after 276 s, $1.69); the plan listed `an empty list has no pages` as replaced, the proof named six tests by title | `28741c5118595607cc32a4c66f1ffe8f2c871b2554e9da9f65cc9ccf6a5936ad` |
| Bug fix with a regression test | complete, 8 stages, 141 s, $0.80; two new tests named by title, two existing tests as guards | `2b047541cd0d95311cf66f8fb4193ee186ed84e8f081913d02a21f95ef1e268e` |

Open:

- A package script that runs more than Jest (`jest && eslint .`, `react-scripts test`) is left as written
  and proves by its exit code, while the plan rules now take a Jest project's tests to be named. A new or
  changed test is still proven by name, by the targeted `npx --no-install jest FILE`; a guard on an
  existing test is not. Vitest has the same limit.
- The proving tests are not repeated for stability in a Jest project: the repeat cannot yet select tests
  by name there (`autocode_proof_stability.narrow`), so each ran once and the proof says so.
- Jest run in the project folder itself also collects test copies under `.autocode/`; the proof runs in
  clean trees, so it is not affected.
- Mocha (its JSON reporter) and Minitest (verbose output) could be read the same way.

## 18. TypeScript, a monorepo package and a dependency upgrade: a check's `npm ci` deletes the person's packages (fixed)

Three kinds not yet tried, each with a brief as a person would write it. Five runs, $8.54:

| Task | Result | Bundle manifest |
| --- | --- | --- |
| TypeScript with Vitest: "bookings that only touch should not conflict. Fix it and add a regression test." | complete, 8 stages, 138 s, $0.89; two new tests and three guards named by title | `3d4f8e209ba452dcbe218be0cc5fcc122809807e4872836fc52e062a6f6029aa` |
| A bug in `packages/invoices` of a two-package repository, "leave packages/search alone", no `--package` | complete, 8 stages, 141 s, $0.85; only that package changed | `760dc85fd18e0f0856efb586c2f6a0efd0a12c4bf4686ff54d7e695748f04654` |
| "Upgrade the ms dependency from 2.0.0 to 2.1.3, in package.json and package-lock.json, and make sure the tests still pass." | stopped (`PAUSED_INVALID_OUTPUT`), 282 s, $1.76 | `2190260b671dd61967e54d8cc2a6ccff42d4007e894f521ed476576c4d5ad4f9` |
| The same upgrade on the fixed tree | complete, 19 stages, 475 s, $2.89, no question | `e0db0bc800c742f0356c55f5af6558b6775e7fc33a896239183db42805046265` |

(A first run of the upgrade used a probe whose `npm test` script was wrong for Node 22, so the suite failed
before the change. It asked the person whether it may correct the script, which the request did not cover:
875 s, $2.15, manifest `b33dde4491ffaaaee61c947df6414c26b767361df9a7336d49e9f557735165ad`.)

**The upgrade deleted the workspace's installed packages.** The plan's checks were `npm ci`, the installed
version and `npm test`. A scratch copy's `node_modules` was a link to the workspace's own directory, and
`npm ci` empties the directory behind such a link before it installs. The Tester's captured `npm ci`, run in
its verification copy, emptied the workspace's `node_modules`; the runner's replay of `npm test` in a fresh
scratch tree then failed with "Cannot find module 'ms'", the Tester's report was refused, and the Investigator
(who took the cause to be the checks running in separate trees) sent the run to the person. A run of the same
command by the runner would have done the same: its scratch trees were linked the same way.

Fixed: a scratch tree and a verification copy get a directory of their own behind the link, which links each
installed package and copies the files the package manager keeps beside them
(`autocode_dependency_dirs.expose`). Emptying, replacing or reinstalling `node_modules` there changes that
directory and nothing the person installed, and `npm ci` then works in the copy. The tree's files look as
before: one link. A virtualenv and `vendor/` are still linked whole, so `pip install` in a copy still writes
to the person's virtualenv. On the fixed tree the upgrade completed and the workspace kept its packages.

The tests for this fix also showed `tests.test_package_scope_cli` red on master: since the mutation check
stops at a mutant's first failing test, a `--package` run's `cd PACKAGE && RUNNER` was not read as the
stopping runner, so no mutant a package's test caught was counted. Fixed separately (#3516).

Open, from the fixed run, which needed two failed proofs and an Investigator before it completed:

- **The original code is tested with the new packages.** Both proof trees use the packages installed in the
  workspace, which after the Builder's `npm install` are the upgraded ones. A test that the installed version
  is 2.1.3 passed on the original code too, so it proved nothing until the Investigator had it compare the
  installed version with `package.json`. A dependency change cannot be shown to fail without it unless the
  original tree gets the original packages.
- **Guards that were all titles named no guard** (fixed, section 19). The four existing tests were guarded
  by their titles, found nowhere, and the Builder copied them into the new test file as `test_ac3_...` to
  `test_ac6_...`, as in section 5.
- The plan named `node_modules` among its paths, so the proof counted the installed files as changed files of
  the task.

## 19. JavaScript: guards that are all titles are found nowhere beside a new test file (fixed)

Seen in section 18's fixed run (manifest `e0db0bc800c742f0356c55f5af6558b6775e7fc33a896239183db42805046265`).
The plan was what a person would want: two `test:` criteria for the upgraded manifest and lock file, in a new
`tests/dependency.test.js`, and four guards on the project's existing tests, each by its title
(`guard: a remembered session lasts thirty days`). The proof failed twice with "Preserve case AC3 … has no
test named test_ac3_<what it checks>", and the Investigator had the Builder copy the four existing tests
into the new file under `test_ac3_...` to `test_ac6_...` names, which is what section 5's fix was meant to end.

A title is bound to a test only after the suite has run (`autocode_test_cases.titled`). The proof step told
the verifier which guards exist by their exact test names, and a title is not one until then, so with every
guard a title it was told of none. The verifier runs the whole suite on the original code with the change's
test files only when there is a guard to find, so it never ran, and the existing tests in a file the change
left alone were in no list the guards are searched in. Section 5's runs passed because the bug fix edited the
existing test file, so the targeted run saw its tests, and the refactor's plan had one guard that was a test
name.

The proof step now names a guard by its declared title where the runner names tests by title
(`autocode_test_cases.guard_names`), so the suite runs on the original code with the new test file and each
titled guard rests on the existing test that passed there and with the change. A guard whose titled test
fails with the change is still not proved.

## 22. Java with Maven and C++ with CMake: the person is asked for test commands (fixed)

Sections 20 and 21 (a Python dependency upgrade, and a rename or removal that existing tests must follow)
are in a separate pull request that waits for an owner decision. The runs below were made on trees that
also held its change (a Planner prompt paragraph about replaced tests, and the original-test gate's handling
of a test file that can no longer load); none of these tasks replaces a test.

Two bug fixes with a regression test, written as a person would write them, on master `40cf369`:

| Task | Result | Bundle manifest |
| --- | --- | --- |
| Java, Maven and JUnit 5, tests named `exactMultipleNeedsNoExtraPage` | asks the person, 9 stages, 232 s, $1.66 | `d03847af3ecb6eb4bcb6923606b973e712cd2d5f4ef62f545042f7839863514e` |
| C++, CMake and CTest, one test executable of plain checks | asks the person, 9 stages, 186 s, $1.14 | `d5e1b2eb7505121ae5c80b8a0abad6e413842aef68ebafaf9c824b9fcaa81faf` |

Both Builders delivered the correct fix and a regression test. Both proofs were `UNVERIFIED`: "No
command to run the regression tests; pass --regression-command" and "No project test command was found;
existing behavior was not checked (pass --test-command)". The Resolver then asked the person to "Set
the saved verification commands through the operator command correction: regression command `mvn test`
(--regression-command 'mvn test') and suite/test command `mvn test`", and the same with the `cmake` and
`ctest` command: a question about AutoCode's own flags, which a person in the VS Code client cannot even
type.

Two causes. Detection (`autocode_toolchain_suite.detect`) listed Go, Node, Cargo, RSpec, `make test` and
Minitest, so a Maven, Gradle or CMake project had no suite. And the fallback for such a project, the
approved plan's own command (section 1), takes only a command that names a test file, so it can tell
which role the command plays; `mvn test` and `cmake ... && ctest ...` name none.

Fixed in detection: a root `pom.xml` is a Maven project (`mvn -B -q test`), a root Gradle build or
settings file a Gradle project (`gradle --no-daemon -q test`), each through the project's own `mvnw` or
`gradlew` when it has one, and a root `CMakeLists.txt` that turns testing on (`enable_testing()` or
`include(CTest)`) a CMake project (`cmake -S . -B build && cmake --build build && ctest --test-dir build
--output-on-failure`). Each is judged by its exit code, as `make test` is: the suite passes on the
original code, fails there with the change's test files, and passes with the change. A suite the
project declares another way (`make test`, `go.mod`, `Cargo.toml`, a `package.json` test script) keeps
priority, and a Python project that also builds a native part stays a Python project. Named JUnit
results still need `--test-report` (docs/named-test-proof.md).

The same two tasks with the fix:

| Task | Result | Bundle manifest |
| --- | --- | --- |
| Java, Maven | complete, 8 stages, 201 s, $1.11; suite `detected:maven`, guards ``guard: run `mvn -B -q test` `` | `c78eecd8887a097a0952ef171e24ec832d81a979b7bd3867f92f958f4ec08dcc` |
| C++, CMake | complete, 8 stages, 152 s, $0.83; suite `detected:cmake` | `ca595b22f82c267453211869f3dbe3d0ce009bfe9464ea2592671e3f436fa2b3` |
| The Java task in a Gradle project | complete, 8 stages, 254 s, $1.07; guards ``guard: run `gradle --no-daemon -q test` `` | `0e8af291ccae3ccfe8f6b0438a897d67964b69fa63a6916d6e4aad77c7243a18` |

Open: a project whose runner is still not detected and whose plan command names no test file (`dotnet
test`, `composer test`, `mix test`, `swift test`, `sbt test`) stops the same way. A build directory the
project does not ignore is section 24.

## 23. Three more kinds of task (complete)

On the tree with section 22's fix, each with one plan approval and no question:

| Task | Result | Bundle manifest |
| --- | --- | --- |
| "This test fails on some runs and passes on others. Find out why and fix it" (Python; the code returned a set's order) | complete, 8 stages, 147 s, $0.84 | `59e4067f985d6605af6aad446fbb66987eda4e42a315098df7e1bc01c1b12c52` |
| A new endpoint in an Express app, tested with Jest and supertest | complete, 8 stages, 157 s, $0.89 | `800873960cd8804d8c78c84ae17cd51a32dca173eb2b1f8d3dcd8a1672845211` |
| "Add type hints to every function. Do not change what the code does" (Python) | complete, 8 stages, 151 s, $0.85 | `8bb24b005bcef8ec3e3e20414591e823a93fee31f1a42555c1bbe448ebae46ed` |

## 24. A project that does not ignore its build directory: the Builder is refused for its build output (fixed)

Section 22's Java and C++ tasks again, in the same projects without their `.gitignore`, on the tree with
section 22's fix:

| Task | Result | Bundle manifest |
| --- | --- | --- |
| C++, CMake, `build/` not ignored | `PAUSED_INVALID_OUTPUT`, 152 s, $0.67 | `7a9e7926fe26c94ef81ccb0431962e471e7764eb104ef026674b059fb5422428` |
| Java, Maven, `target/` not ignored | complete after two refused Builder attempts, 13 stages, 587 s, $2.19 | `17f7aa24f0fbe444053b3a4481110122364d5e1463adeb36309c544e62ae2882` |

Each Builder fixed the code, added the test and ran the project's tests, as its prompt asks. The build
tool wrote its output beside the sources (`build/CMakeCache.txt`, `build/libpager.a`, ...;
`target/maven-status/...`, `target/surefire-reports/...`), nothing ignores it, and the attempt was refused:
"Builder attempts for this task changed files outside the assigned paths". The C++ plan's own check was
`cmake -S . -B build && cmake --build build && ./build/pager_test`, so no retry could pass, and the
Investigator ended with `needs_user`. The Maven run's Investigator told the Builder to build in a scratch
copy and clean `target/`, which worked once and was lost in the next round.

This is section 3's `Cargo.lock` again, many files at a time: a tool's by-product, not the stage's edit.
`autocode_toolchain_byproducts` now also removes, when a stage ends, the files a build tool wrote into
its own directory beside the manifest it builds (`target/` beside `pom.xml` or `Cargo.toml`, `build/` and
`.gradle/` beside a Gradle file, `build/` beside `CMakeLists.txt`; `autocode_project_kinds.BUILD_DIRS`),
and only when that directory held no file when the stage began. A directory that already held a file,
committed or not, is the project's own and is measured as before; so is one a link leads to, and one the
Builder's assigned paths touch. A file the project ignores there stays. The stage's record names each
removed directory (`toolchain_byproducts`).

The same two tasks with the fix:

| Task | Result | Bundle manifest |
| --- | --- | --- |
| C++, CMake, `build/` not ignored | complete, 11 stages (the Tester sent the first build back once), 226 s, $1.14; both Builder records name `build/` | `5c82eb7513897bd7d540ae35b475c311d02e4f6d0841a5d6dc2cb8eb793c5217` |
| Java, Maven, `target/` not ignored | complete, 8 stages, 206 s, $1.00 | `586fd988491db4f945b997d5d72275f783af851088a3569d646bcf47d849324d` |

Open: build output in a directory that already held files when the stage began (a person's own earlier
build, never ignored) is still charged to the stage. `node_modules/` a stage installs in a project that
does not ignore it is not handled either.

## 25. Four more kinds of task (complete)

On master with sections 22 and 24's fixes, each with one plan approval and no question:

| Task | Result | Bundle manifest |
| --- | --- | --- |
| Rust bug fix in a project with neither a `.gitignore` nor a `Cargo.lock` | complete, 10 stages, 158 s, $1.23 | `b76e5d9698a4575fd4266357ebe3c3487b3fbead880940c87c918032080b8175` |
| Python package in a `src/` layout, pytest with a `conftest.py` fixture and parametrized tests, its own virtualenv | complete, 10 stages, 219 s, $1.35 | `411889d1b49c830e4d5642ebd9489d2eb25e5b64067175f3213490ea45b5dd9c` |
| Two unrelated changes in one request (a display bug and a new command) | complete, 8 stages, 158 s, $1.10 | `9764e1921a54bc254d195ed49fd873bb708da1ca6890e28b0376c790114bb198` |
| TypeScript refactor (extract a function, behavior unchanged), Vitest | complete, 10 stages, 198 s, $1.36; the Tester's report was refused twice for its form and repaired without a new check | `1532d861bfda72f903033a261cf491496c1282e78344e2acf6ff0d100d72f0b9` |

## 26. Five more kinds of task: three complete, two stop

With section 20's fix on top of master, each with one plan approval:

| Task | Result | Bundle manifest |
| --- | --- | --- |
| Bug fix in a project whose suite already has an unrelated failing test, which the request says to leave | complete, 8 stages, 124 s, $0.86; the failing test is untouched | `f49cfc876dad4f017f95f4bcf70f524615f222e5a9a27316fd376ada39397144` |
| A question about the code ("what does it do when ..., and is that tested? do not change anything") | answered by Requirements with the lines it read, nothing changed, 12 s, $0.05 | `10b66731534b91d11387d800d484b7f9da1a1185032621b3dccb76ab677f2c1f` |
| A new endpoint in a Flask app, pytest with a fixture, its own virtualenv | complete, 10 stages, 252 s, $1.57 | `3a50676e0c169013420e5b1d754c44dcf70653fcbc6566be5b4fd2f7078dcdc5` |
| Move a module into a new package and update its imports | `PAUSED_INVALID_OUTPUT`, 265 s, $1.50, with a correct move | `842776c4b8338566fde172e2413cd0f48429e75a4e23dbe9f6ae1b6f5eb411fe` |
| One change across a Python package and a Node package of the same repository, tests on both sides | `PAUSED_REPEATED_FAILURE` before any Builder, 147 s, $0.90 (section 27) | `92120aada17d98ed37640f29df153d163be9c0aeef06d79e7b8505a38f49da69` |

Open: the module move is section 20's stop in another form. `tests/test_pager.py` changed only its import
(`from pager import ...` became `from paging.pager import ...`), no test in it changed, and the suite passes
with the change. The original file can no longer be loaded once `pager.py` is gone, so the original-test
gate refused the Tester's PASS, and the Investigator asked the person to approve a plan that lists the
four untouched tests as replaced. Section 20's fix does not accept it: it requires that an import the
file gains binds no name the file already bound, and here the same names are imported from the module's
new place. Whether the gate may accept the same names from a moved module waits for the same decision.

## 27. One change with tests in two projects of a repository: the plan is refused (fixed)

"Notes can be archived. Return it from the API as a boolean `archived` field in packages/api, and in
packages/web show an archived note's line in brackets. Add tests on both sides." `packages/api` is Python
with unittest, `packages/web` a Node package with `node --test`.

The Planner named both test files. The plan check refused the draft three times, the same way: "The
regression proof would never run the plan's tests packages/web/tests/format.test.js: it runs the plan's
tests with one suite". The Planner tried plain milestones, then a new `packages/package.json`, then a new
root `package.json` whose script runs both suites; none can satisfy a check that accepts only a suite
that already exists. The Investigator then asked the person: "the runner's regression proof can run only
one suite per plan. How should it proceed? (a) split it into two runs ... (b) let the web test sit
outside the regression proof; or (c) approve a cross-language workaround". Every option is a question
about AutoCode's own machinery.

The refusal protected something real. Without it the proof ran only the tests its own suite collects:
the other project's new test was run with that project's suite on the candidate, as part of the suite
that must keep passing, and never on the original code. A new test there that the original code already
passes was accepted (`tests.test_companion_tests` shows it failing on master).

Fixed in the proof, not in the check. Each changed test the proof's suite cannot run is proven by the
suite of the project around it, with the same steps as a change to that project alone
(`autocode_companion_suites.test_suites`, `autocode_companion_tests`): that suite's run on the original
code, the changed tests on the original code and with the change, their repeats, and the whole suite
with the change. Its failures are the proof's, named with the project, and a `test:` criterion may rest
on a test that suite showed fail before and pass after. The plan check now refuses only a test that no
suite would run: one of another kind than the proof's suite runs, in a directory with no suite of its
own for that kind.

The first real run with that fix accepted the plan and proved both sides: the root's suite showed the
Python test fail on the original code, and `packages/web`'s suite showed the Node test fail there and pass
with the change. It then failed four proofs on one criterion, "guard: a pinned note is starred", a test
the Node package already has, and the Resolver asked the person "How should AC4's preserve check be bound
so the proof can map it?" (`WAITING_FOR_USER`, 23 stages, 517 s, $3.69; bundle manifest
`8376372751c3e3b810542971133095f561bb5eccba56ce8d7e1913f769994151`). A guard names a `node:test` test by
its title (section 19), but only where the proof's own runner names tests by title: beside a Python suite
the title was read as prose and looked for as `test_ac4_...`. A guard is now matched in another project's
suite as that suite names its tests, by name or by title, and a title guard's test file in a project the
change does not test is found by the title it spells.

With both, a real run completes: the root's suite proved the two Python criteria and their guard, and
`packages/web`'s suite proved the two Node criteria and the two guards named by title (`TASK_COMPLETE`,
17 stages, 315 s, $2.15; bundle manifest
`c84eacfed28d445eb5cca4569a462fe9f280ea4e5bff6be4c54477f78a996aae`). That run built the two milestones
one after the other (`--max-parallel-builders 1`).

**A parallel Builder that edits the project instead of its own copy (fixed).** Two runs of the same
task with the default two Builders side by side stopped before any proof, `PAUSED_ORCHESTRATOR_DRIFT`,
"Parent source changed during the build" (10 stages, 287 s, $1.74, bundle manifest
`47c4f972fdc3e91804b71a371531b834692f272086ea9f96cc52b14d203bfbd2`; 7 stages, 233 s, $1.18, bundle
manifest `5942b71fa793a5c7b678822d6cbcfe3971ad4e7bbf13ca9cdd040fd4be018cc8`). Each Builder works in its
own worktree under `.autocode/builders/`, and its prompt names only that directory. In both runs the
Builder for `packages/web` (Haiku) read and edited `PROJECT/packages/web/...` by absolute path instead,
with the Edit and Write tools. In the first the Investigator saw it and the retry built in the worktree,
but the edits left in the project still ended the batch. Nothing was lost or merged wrongly: the stop is
the guard against exactly this. But the person is left to clean up after AutoCode's own Builder.

The Claude Code backend pre-approved Edit and Write (`--allowedTools`), and a pre-approved tool edits
any path on the machine. Measured on Claude Code 2.1.325 with a stage's own flags: without the
pre-approval, `--permission-mode acceptEdits` accepts the same edits inside the stage's directory
(source files, `.github/`, `.gitignore`, `.autocode/`) and refuses one outside it, in the parent
directory or anywhere else. The backend no longer pre-approves them (`providers/claude.WRITE_ALLOWED`),
so a Builder's stray edit is refused where it is made. Of the 248 Edit and Write calls in the real runs
of this survey up to then, 242 were inside the stage's own directory and the other six were these stray
ones.

That alone was not enough. Claude Code's refusal reads "Claude requested permissions to write to PATH,
but you haven't granted it yet". In four runs with it, two Builders strayed: one corrected the path, and
one took the refusal for a permission to ask for, reported "Grant file edit permissions within the
current_task.affected_paths", and the person was asked (`WAITING_FOR_USER`, 8 stages, 161 s, $0.95,
bundle manifest `256dffabf5d146d334c632980d1e30d6edcfaed0078f0b21970942f73dfa3d9c`). The mistake is a
path, and the runner knows the right one. Before each Edit and Write, Claude Code now runs a small hook
of AutoCode's (`providers/claude_edit_scope.py`): for a path outside the stage's directory its refusal
says so, says that no permission changes it, and names the stage's own file at the same place. In the
next real run where a Builder strayed, it read that and edited its worktree's file in the next call
(bundle manifest `20079fd8529bbf4a0c37fa15306fbab60a83954b5d34e92d97d4deb1bd90dcf5`). The hook grants
nothing: an edit it lets through is still judged by Claude Code, and when it fails Claude Code's own
refusal stands.

Bash is not confined by this: the Claude Code backend still has no kernel containment, and a command may
write where the process can. The stop above remains the guard for that.

**A Tester's check that only worked from where its last command had left it (fixed).** That same run
then paused for another reason, `PAUSED_BUILDER_CLASSIFICATION` (13 stages, 287 s, $1.33). The proof had
passed. The Tester (Sonnet) ran `cd packages/api && python3 -m unittest discover`, and Claude Code keeps
a `cd` for the next command, so its `cd packages/web && npm test` failed and it cited
`cd ../web && npm test --silent` instead. The runner replays a cited check from the repository root,
where that command fails, and rightly refused the report. The repaired report could then only say the
web criteria were not verified, the run went back to a Builder with nothing to change, and the
Investigator called it "a routing/operational issue". The backend now sets
`CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR=1`, so every command of a stage starts in the stage's
directory (measured on 2.1.325), and a check written for one command is one the runner can replay.
Three real runs with all of this, two Builders side by side, each completed in the eleven stages the task
needs with no report repair (191 s, 199 s and 212 s; $1.45, $1.38 and $1.46; bundle manifests
`c2eed3f6a662b98ea6e8b522f1fff5717e446aa3cd00dffa8b6c79754cae0e56`,
`fd6aa8fc149cd31dee6114131385437d2481db4cec240b9c3414b210d93c0a2d`,
`71d210e81900df59a0825dfd4b08936bfccb0e95053dcc6457b2d128ecd18f56`).

**A Tester report refused only because a cited check does not replay (fixed).** That run's refusal went
to a report repair, which has no tools: it withdrew the check and marked the web criteria not verified.
The blocked report sent the task to a Builder, which had nothing to build, and the Investigator paused
the run for the person. The cause is removed on Claude Code, but the route was the same on every
backend. Now the stage that cited the check runs again on the same source, once for each source and
twice for a task, and is told the command, how its replay ended and to run every check from the
repository root (`tools/autocode_reported_check_rerun.py`). A second refusal at the same source keeps
the earlier route.

The original-test gate is as before: in a run of the whole repository it replays the original tests with
the root's suite, so the original tests of a nested project of another kind are not replayed there. The
nested project's own suite still runs with the change, where an existing test the change breaks fails
the proof.

## 28. Five more kinds of task (complete)

On master with section 27's fixes, each with one plan approval and no question:

| Task | Result | Bundle manifest |
| --- | --- | --- |
| "Make both linear. Results must stay exactly the same, including order" (two quadratic Python functions) | complete, 10 stages, 487 s, $2.72; the plan grew to ten criteria, with unhashable and NaN keys kept exact | `3545e4987d949b526183193d7c4d6ec6c54a056a00e81962928d34102cdfe29a` |
| A new column in a SQLite table, with database files of the current version kept working | complete, 10 stages, 225 s, $1.66; two of the new tests open a database made with the old schema | `e303d13ea208982d9b5d4146a714fe093982bc4b51469608b77b076415431b53` |
| A bug reported as a pasted traceback and the value that caused it | complete, 10 stages, 198 s, $1.35 | `46f76b4e7d75bec3828efd5238fe6e303f9c858d98062a744eb9cc13052a475e` |
| A new command-line flag that must also be documented in the README | complete, 9 stages, 171 s, $1.05; the Builder's last message was not a report and was repaired; the Tester checked the README row with `grep` | `b609958acf986f6acada0fab19a256e1997d7d3b583c9ec720ff0eda8ea89789` |
| "`ruff check .` reports a pile of problems. Clean them all up. Don't change what the code does, and don't loosen the config" | complete, 11 stages, 195 s, $1.41; the Tester's report cited a file as evidence for a criterion and was repaired | `a394f76c1b520027d4b433e5af90f62dc6f5c7796aa875d48d4da08490344d70` |

## Also seen, not yet run on real models

- **Claude Code refuses to edit some directories inside the stage's own directory.** With Edit and
  Write accepted for the directory (`acceptEdits`), Claude Code 2.1.325 still refuses a file under
  `.vscode/`, `.husky/` or `.claude/`, with or without a pre-approved rule; `.github/`, `.gitignore`
  and `.autocode/` are accepted (measured with `claude -p`, 2026-11-14). A task that must change
  such a file can only do it through a shell command. No task run has needed one yet.

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
