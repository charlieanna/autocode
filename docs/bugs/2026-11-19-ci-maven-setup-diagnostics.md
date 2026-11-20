# Keep Maven preparation errors in the CI log

The scenario jobs for #3909 and #3914 failed before running a scenario, during
the pinned Maven dependency warm-up. The exception named `setup.log` in the
ephemeral runner, but neither included Maven's output nor retained that file as
an artifact. The cause was therefore unavailable after the job ended.

The same checksum-verified Maven 3.9.9, Temurin 17.0.16+8 and unchanged Java
seed completed preparation in an isolated temporary directory on macOS. This
does not establish what failed on Ubuntu; the failure has not been called a
network transient or retried repeatedly without evidence.

Failed preparation now includes its captured stdout and stderr in the raised
error, while preserving the full `setup.log`, nonzero failure and refusal to
publish `toolchain.json`. Regressions for errors on each stream fail before
this change. The toolchain's CI consumer is also explicit, so changing setup
keeps those checks selected. No application, model prompt or success gate
changes, and no paid model verification is needed for this diagnostic change.

The original attempts remain visible in jobs
`135981443675` (run `44643777752`) and `135999249299` (run `44649079211`).
The next observed setup result must be inspected before deciding its correction.
