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
three, $4.82. Sections 10 to 12 are fifteen runs on master `c6b9f7f`, $17.59.

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
code, where it runs for hours; each such run ends at the proof's 15-minute limit. The second run's test
bounded its own time and the task completed.

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
package.json script run whatever a recipe says, and a shell test that sources the product holds the
product at its original, so those keep the old judgement. On the fixed tree the same request completed
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
