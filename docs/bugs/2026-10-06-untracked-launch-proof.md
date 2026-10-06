# Uncommitted launch files could still pass an in-place proof (#540)

An in-place run pinned `base_commit` to HEAD and then treated every uncommitted
or untracked file as the run's own change. The base suite ran on that commit
alone, so a README-only or empty HEAD looked like a new project, and a passing
launch suite the Builder later broke or deleted never ran on the base.

A new in-place run now copies those files aside without touching HEAD, the
index, or the working tree. The proof runs that snapshot on the base and counts
only what changed after the run started. A worktree run still starts from the
committed checkout. A run created before the snapshot existed keeps the old
comparison: the bytes are no longer recoverable.
