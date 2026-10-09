# Retention policy

The existing C# implementation is `legacy/Policy.cs`. Domain comparisons are
exact and case-sensitive: `de` maps to 7 days, `exception` to 1, everything else
to 30. Keep this reference when adding other language implementations.

There is no Go module or package yet. For example,
`GO111MODULE=off go test ./...` reports `matched no packages` and exits 1.
A module-aware Go toolchain instead reports that no main module exists.
Neither result indicates a failing old Go test: there are no old Go tests.
