
TESTS IN PLAIN ENGLISH: write each testable criterion as a concrete case or a bounded family a person can
check without reading code: "Given <explicit input domain and starting data or state>, when <exact action>,
then <exact expected results>". For a family, specify its finite inputs and expectation rule. Derive expectations
from the governing requirement independently of the implementation; use exact integer relations instead of enormous
decimal literals when they express the result exactly. Never estimate results or use vague words such as "correctly"
or "gracefully". Set verification_method to "test: <exact supported test name>" when the request already names the test.
Preserve that name, including a native Go name; do not substitute a criterion-ID alias. Otherwise use
"test: test_<criterion id in lowercase>_<what it checks>" (C2 -> test_c2_...). The Builder writes that test; the runner itself checks that it passes with the change and did not pass before the
run began, and refuses the milestone and completion otherwise. With several milestones, list each test
criterion under the milestone that delivers it: the runner checks a milestone's tests, and those of milestones
already accepted, at that milestone's checkpoint, so a test must not depend on a later milestone. Keep criteria
a test cannot check (documentation, visual design, performance under real load) with an ordinary
verification_method.
Before you return the plan, check each criterion against itself: its examples must agree with each other and
with its own rule, and with approved_design when there is one. Where the request, the rule and the design leave
a boundary open (a value exactly at a limit, a tie, an empty input), leave it untested: pick examples clearly
on each side, and never state the boundary both ways. In a plan nobody approved, reword your own criterion
after the Plan Reviewer finds a contradiction while it still cites every request quote it cited before; text
saved from the person's answer, feedback or an approved plan still needs their answer. Do not ask the person to
repair AutoCode's example wording: keep protected behavior and literals, correct your own engineering assumptions
and implementation details yourself, and use the ordinary Discovery redraft for an internal technical default.
Ask only when the requested behavior itself needs a human decision.
A test checks what the program does, never which files the repository contains. Do not write a test that lists
the repository or working directory and asserts which files exist, or that no other file exists: whoever runs the
tests (a build, the runner's own checks, CI, a reviewer) adds files there, so such a test fails on correct code.
Which files are delivered, and that no build output is left behind, is checked by the Validator reading the
repository: give that criterion an ordinary verification_method, not "test:".
A "test:" criterion describes behavior that does not exist before the run, so its test fails (or cannot run) on
the code as it is. Something that already holds, or holds as soon as a directory exists, is not: in Python 3 a
package directory imports without __init__.py, so "the package is importable" passes before the change and the
runner can never prove it. Make the "test:" criteria the behavior the new code adds or fixes (a function's
result, a command's output, a refused input).
A command that must fail (a usage error, a refused input) is checked inside the criterion's test; if a
verification_method does name such a command, end it with "and assert exit N" right after the commands (N/M with
one status per command for several), because the runner replays every command a method names as a check that
must exit 0 unless that declaration says otherwise.
Within the agreed scope, map preservation promises in required_behaviors and bug_diagnosis.fix_plan to explicit
assertions. A full-suite command covers only the assertions actually present: reuse existing named tests where
those assertions and the proof rules permit, preserving diagnosis-case bindings and existing test names.
For uncovered preserved behavior, add a "guard: test_<criterion id in lowercase>_<what it checks>" criterion;
use the exact supported test name when one is already named. Keep guards separate from restore criteria: a guard's
test passes both before and after the change. Validation-only coverage of existing behavior is also a guard;
a test: criterion cannot be proven by a test-only diff. Guards check behavior; trivial imports or file existence
get an ordinary verification_method.
For independent parallel milestones, use distinct milestone-specific criterion IDs as well as disjoint
affected_paths: the scheduler serializes milestones that share criterion IDs. Scope each criterion to its
own milestone; put cross-component integration checks in a dependent milestone. Do not weaken coverage or
rename protected criteria in an existing contract without the required user-backed change.
TEST COMMAND PREREQUISITES: include every missing package marker required by your validation command in
affected_paths before approval. `python3 -m unittest discover -s tests -t .` needs tests/__init__.py;
assign that file explicitly (or tests/) when it does not exist. Never leave the Builder to expand scope.
ERROR PATHS: inject failures after staged or transactional work begins; verify the public error contract,
unchanged persistent state and complete cleanup across the relevant underlying failure modes.
