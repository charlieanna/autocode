
NAMED TEST PROOF: the runner attributes cases with Python unittest/pytest, Go tests, Node's built-in
node:test, native Vitest 4, and Rust tests run by `cargo test`. The identifier immediately after test:/guard: names the required test;
an explanation after it does not define an alias. Preserve an explicitly requested supported native name.
For example, Go TestCacheExpiry uses "test: TestCacheExpiry", and TestCacheRetainsFresh uses
"guard: TestCacheRetainsFresh". Preserve case and the complete native subtest path. Do not substitute
"test_ac1_cache_expiry" and claim in prose that it resolves to TestCacheExpiry: those names do
not match. During plan review, check the declared proof identifiers against the required native test
names; block a mismatch instead of accepting an explanatory alias. The criterion ID remains unchanged. In a Vitest project keep cases in Vitest and run
`npx --no-install vitest run <test files>` or an npm test script that is a single `vitest run` command.
The runner owns the reporter and checks actual named outcomes; missing, skipped and ambiguous cases
never pass. Do not create node:test wrappers just to relabel existing Vitest cases.
For node:test register each case with `test('test_c2_example', async () => { /* assertions */ });`
and run `node --test tests/example.cjs`. Keep fixture helpers and assertions; await every async check.
A node:test or Vitest test the project already has is named by its exact title, as its test file spells it:
test('counts a partial last page', ...) is "guard: counts a partial last page". Guard existing behavior
with the existing test; never copy one under a test_* name, or into a new file, to name it.
Custom scripts printing PASS labels, or ordinary npm/Jest/Mocha summaries, do not supply named proof.
A node:test case must assert the behavior itself, never spawn another test runner (npm/pnpm/yarn test,
npx vitest, jest, mocha or node --test through child_process): its pass would be that runner's exit code,
which is 0 even when a -t filter matches no test, so the runner refuses such a file as named proof.
Running the product's own CLI from a node:test case is fine.
For Rust a case is named by its test function: `fn test_c2_example` is "test: test_c2_example". Plan and write
each new Rust test in a file under tests/ (tests/example.rs, listed in affected_paths), never in a #[cfg(test)]
module of a source file: the runner runs the changed test files against the original source, and a test inside
src/ is part of that source, so it proves nothing.
Keep the existing project suite and protected tests intact. Add supported named tests within the approved
test paths; plan any needed test paths before approval. Do not replace test:/guard: criteria with prose to
avoid proof. If the project's tests run with none of these runners (`make test`, Ruby test files
run one by one), the runner cannot match a test to a test: or guard: criterion: write that criterion as the
command that runs its test file and what it shows ("`ruby test/pager_test.rb` fails on the original code and
passes with the fix"); the runner proves it by that command's exit code before and after the change.
