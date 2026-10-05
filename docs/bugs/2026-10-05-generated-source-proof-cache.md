# Generated dependency sources invalidate regression proof reuse (#478)

A task worktree can import ignored build-generated source files copied from its
project workspace into verification scratch trees. The candidate receipt key
previously hashed generated files only in the task worktree. Changing a generated
input in the project workspace could therefore reuse an old candidate PASS and
an old baseline result without executing the changed input.

Scratch copying and receipt identity now share the eligibility rules for generated
sources. Candidate and baseline receipts bind those dependency-owned files; normal
candidate source edits still leave the baseline reusable. The reproduction in
`VerificationProofCache.test_generated_dependency_changes_invalidate_real_candidate_and_base_proofs`
uses actual Git worktrees, an isolated virtualenv and Python subprocesses: unchanged
inputs reuse PASS, a candidate edit reuses only the baseline, changing the imported
generated constant returns FAIL, and an independent fresh proof also returns FAIL.
