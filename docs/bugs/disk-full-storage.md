# Disk-full preparation failures (#971)

`autocode_util.snapshot` and `autocode_source_snapshot.snapshot` retain source
identities: hashes, executable bits, symbolic-link targets and deletions. They do
not copy every source file at each stage.

Read-only native verification does allocate a fresh, run-owned verification tree
in `autocode_verification_copy.allocate`. Its source copies, streamed Git blob
input and independent Git object store consume storage. Parallel Builder
preparation also records source in Git through an alternate index. A linked
worktree can put Git objects on a different filesystem from its workspace.

The first fix explains an authentic ENOSPC or captured Git disk-full diagnostic
at parallel dispatch and verification-copy preparation. It preserves the pause,
original error and accepted work. Free the relevant filesystem and resume the
paused run; the failed verification allocation cleans up only its own fresh
control directory. Do not delete another run's retained evidence.

Storage deduplication, compression policy, retention limits and advance disk-space
checks remain separate work. This diagnostic fix does not reduce peak storage or
handle every possible disk write in a run.
