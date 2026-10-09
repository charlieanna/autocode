# Read-only captures must keep generated output out of the source tree

Fixes #782. During the original Django Arena run after the #778 interpreter
repair, the Tester ran the exact planned `tests/runtests.py filtered_relation`
command. Its parallel database setup created three unused SQLite files in the
original repository. The read-only source guard correctly refused that PASS.
The original TypeScript run also stopped correctly when a configured provider's
Tester created extra probe fixtures before invoking capture.

Read-only Codex and configured-provider judging launches now allocate the existing source-bound verification
copy and hand its manifest and hash to the capture command. Exact native argv
and environment remain unchanged; the capture executes from that copy and
retains its receipt at the requested evidence path. Generated outputs can
persist there between captures. New copies and manifests remain inside the
owning `.autocode/runs/<run-id>/` directory as stage evidence, without adding
project-level housekeeping files. Legacy kernel-contained copies retain their
existing layout. Dry runs, planning and writers allocate no
copy; scratch-owning workflow jobs keep their existing investigation directories.
Inherited copy authority is removed, including from adapter-returned environments
without a newly established native containment copy.

Extra test fixtures must be created inside the captured command, so the copy and
clean replay both receive their setup. A preceding heredoc still writes to the
original repository. Dependency installation and original lockfile or setup
changes remain outside validation's authority.

The copy does not add an OS sandbox or protect existing inputs against model
tools. Manifest/input checks, the original repository's read-only source guard
and independent clean-source replay continue to enforce validation. Direct or
concurrent original-source writes still pause validation and remain untouched.
