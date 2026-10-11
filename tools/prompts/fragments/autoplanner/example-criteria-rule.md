
TESTS IN PLAIN ENGLISH: write every acceptance criterion a test can check as one concrete example a person can
check without reading code: "Given <the exact starting data or state>, when <the exact action or command>,
then <the exact result, with literal values>". No vague words such as "correctly" or "gracefully". Work each
literal result out from the criterion's own rule (count the items, do the arithmetic), never estimate it. Set its
verification_method to "test: <exact supported test name>" when the request already names the test.
Preserve that name, including a native Go name; do not substitute a criterion-ID alias. Otherwise use
"test: test_<criterion id in lowercase>_<what it checks>" (C2 -> test_c2_...). The Builder writes that test; the runner itself checks that it passes with the change and did not pass before the
run began, and refuses the milestone and completion otherwise. With several milestones, list each test
criterion under the milestone that delivers it: the runner checks a milestone's tests, and those of milestones
already accepted, at that milestone's checkpoint, so a test must not depend on a later milestone. Keep criteria
a test cannot check (documentation, visual design, performance under real load) with an ordinary
verification_method.
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
Behavior that already works and must keep working (the change must not break it) is a guard: write it as the
same kind of example, with verification_method "guard: <exact supported test name>" when the request
already names the test; otherwise use "guard: test_<criterion id in lowercase>_<what it checks>".
The runner checks that its test passes both before and after the change. Coverage of behavior the product already
implements — a named scenario, an existing rule, tests added with no product change — is a guard for every
such criterion. A test: criterion cannot be proven by a test-only diff. A guard needs a real behavior to check;
something trivially true (a package that imports, a file that exists) gets an ordinary verification_method.
For independent parallel milestones, use distinct milestone-specific criterion IDs as well as disjoint
affected_paths: the scheduler serializes milestones that share criterion IDs. Scope each criterion to its
own milestone; put cross-component integration checks in a dependent milestone. Do not weaken coverage or
rename protected criteria in an existing contract without the required user-backed change.
TEST COMMAND PREREQUISITES: include every missing package marker required by your validation command in
affected_paths before approval. `python3 -m unittest discover -s tests -t .` needs tests/__init__.py;
assign that file explicitly (or tests/) when it does not exist. Never leave the Builder to expand scope.
ERROR PATHS: inject failures after staged or transactional work begins; verify the public error contract,
unchanged persistent state and complete cleanup across the relevant underlying failure modes.
