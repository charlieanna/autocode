# First Go suite on a base that has no Go project (#685)

A port whose pinned base is only source in another language (the live case was
`reference/Policy.cs`) adds the first Go module and tests. `go test ./...` on
that base exits 1. Inside a parent module — the candidate's new `go.mod`, when
the proof tree sits under the workspace — the output is `matched no packages`.
Outside a module it is `does not contain main module`. Either way the base
suite was recorded as broken and the proof stayed UNVERIFIED, with no Builder
change that could make the base suite run.

The exception applies only when all of these hold: new behavior, no base patch,
the suite command is plain `go test`, the pinned base is regular files with no
`go.mod`, `go.work` or `.go` file (a submodule or link does not qualify, and
neither does ignored Go source copied into the proof trees), the base receipt
is that no-package report, and the candidate suite passes in full. A base that
already has a Go module or Go source, including one whose packages fail to
build, keeps the ordinary rule. The baseline stays recorded as broken. A note
says there was no Go project to preserve. Separate new-behavior proof still
applies.

When the regression tree is not inside a module, `go test` on the new files
exits before any test event (`go.mod file not found`). Those tests did not pass
on the base, so they count as not run there. A base that has a Go project does
not get that reading.
