
NAMED TEST PROOF: the runner attributes cases with Python unittest/pytest, Go tests, Node's built-in
node:test, native Vitest 4, Jest, RSpec, and Rust tests run by `cargo test`. The identifier immediately after test:/guard: names the required test;
an explanation after it does not define an alias. Preserve an explicitly requested supported native name.
For example, Go TestCacheExpiry uses "test: TestCacheExpiry", and TestCacheRetainsFresh uses
"guard: TestCacheRetainsFresh". Preserve case and the complete native subtest path. Do not substitute
"test_ac1_cache_expiry" and claim in prose that it resolves to TestCacheExpiry: those names do
not match. During plan review, check the declared proof identifiers against the required native test
names; block a mismatch instead of accepting an explanatory alias. The criterion ID remains unchanged. In a Vitest project keep cases in Vitest and run
`npx --no-install vitest run <test files>` or an npm test script that is a single `vitest run` command.
The runner owns the reporter and checks actual named outcomes; missing, skipped and ambiguous cases
never pass. Do not create node:test wrappers just to relabel existing Vitest cases.
In a Jest project keep cases in Jest: name each new case in its title, `test('test_c2_example', () => { /* assertions */ });`,
and run `npx --no-install jest <test files>` or an npm test script that is a single jest command.
For node:test register each case with `test('test_c2_example', async () => { /* assertions */ });`
and run `node --test tests/example.cjs`. Keep fixture helpers and assertions; await every async check.
A node:test, Vitest or Jest test the project already has is named by its exact title, as its test file spells it:
test('counts a partial last page', ...) is "guard: counts a partial last page".
For RSpec name each new case in its example's description, `it "test_c2_example" do`, in a *_spec.rb file,
and run `bundle exec rspec spec/example_spec.rb` (plain `rspec` without a Gemfile). An RSpec example the
project already has is named by its own description, as its spec file spells it:
it "counts a partial last page" is "guard: counts a partial last page", the description itself and not a
sentence about it. Guard existing behavior with the existing test; never copy one under a test_* name, or
into a new file, to name it.
Custom scripts printing PASS labels, or ordinary npm/Mocha summaries, do not supply named proof.
A node:test case must assert the behavior itself, never spawn another test runner (npm/pnpm/yarn test,
npx vitest, jest, mocha or node --test through child_process): its pass would be that runner's exit code,
which is 0 even when a -t filter matches no test, so the runner refuses such a file as named proof.
Running the product's own CLI from a node:test case is fine.
Do not make a Python test run its own module, file or self-including discovery suite, even with a
reentry guard: the whole suite would run inside each proof and mutation invocation. Assert the product's
behavior directly. A suite-delivery criterion is verified from the Core runner's actual current suite
command, named outcomes and proof artifact, citing that artifact in criterion_results.evidence_refs;
keep its individual behavioral test:/guard: criteria. Do not invent another named test to prove the suite
exists or passes. A product that implements a test runner can be tested by running distinct isolated
fixture tests; ordinary unittest.main entry points and product CLI subprocesses remain valid.
Keep approved criterion text, literals and exact test identities. Do not silently change an already
approved test: binding into aggregate prose; an invalid verification strategy needs plan review.
For Rust a case is named by its test function: `fn test_c2_example` is "test: test_c2_example". Plan and write
each new Rust test in a file under tests/ (tests/example.rs, listed in affected_paths), never in a #[cfg(test)]
module of a source file: the runner runs the changed test files against the original source, and a test inside
src/ is part of that source, so it proves nothing.
Keep the existing project suite and protected tests intact. Add supported named tests within the approved
test paths; plan any needed test paths before approval. Do not replace test:/guard: criteria with prose to
avoid proof. If the project's tests run with none of these runners (`make test`, Minitest files
run one by one), the runner cannot match a test to a test: or guard: criterion: write that criterion as the
command that runs its test file and what it shows ("`ruby test/pager_test.rb` fails on the original code and
passes with the fix"); the runner proves it by that command's exit code before and after the change.
Behavior that already works and must keep working is guarded there by the command that runs its existing
test file, written "guard: run `ruby test/pager_test.rb`" (or the project's suite, "guard: run `make test`"):
the runner needs that command to pass on the original code and with the change. A change that must not
alter behavior (a refactor, a rename) whose criteria are such guards needs no new test and no test file in
affected_paths; a change that adds or fixes behavior still needs a test file that shows it.
