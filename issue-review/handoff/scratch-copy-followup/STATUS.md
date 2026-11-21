# Shared scratch-copy follow-up

The merged #3992 suite exposed two independent false refusals in shared scratch preparation. A deleted tracked path has the source identity `deleted` but has no physical file in the copied tree. Also, a probe can legitimately place its owned scratch Git worktree under an unignored workspace path; the post-copy workspace snapshot then sees that worktree as a new nested repository.

The correction compares deleted paths with actual absence, preserving refusal if any copy resurrects one. The post-copy comparison disregards only the exact newly created owned worktree, only when it was absent from the original inventory and not within an explicitly selected source scope. A new sibling source remains a mismatch. Review-specific source admission still rejects tampering before a patch, with its existing review-specific error.

The two positive controls failed on master `6b803f17e`; resurrection and neighboring-source negatives already failed closed there. Local verification passed the full protected-oracle and design-probe modules (46 tests), review-proof, review-mutation, copy-identity and source-snapshot modules (82 tests), workflow-scope Ruff and mypy (990 files). Exact-head CI remains pending. The historical #3992 Ubuntu suite failure is retained in `/private/tmp/autocode-3992-suite-failure.log`; do not represent it as green.
