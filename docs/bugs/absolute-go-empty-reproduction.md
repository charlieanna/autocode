# Absolute Go commands must collect tests before proving a reproduction

Fixes #783. The original Go Arena investigation cited an absolute Go executable
with a nonexistent `-run` selector. Go returned exit zero and `[no tests to run]`.
The runner recognized only commands beginning with `go test`, so the absolute
command bypassed its existing requirement for complete, nonempty test results.

Literal Go test invocations now share that requirement, including absolute
executables, quoted selectors and literal environment assignments behind `env`.
The runner preserves the native arguments and adds only Go's existing JSON
reporting flag. Native per-test events still go through the existing collector
and completion check. An explicit test probe must also contain at least one
passed or failed test; a fully attributed collection of only skipped tests cannot
prove a reproduction. Mixed passed and skipped results remain valid. This probe
check does not change global suite or completion semantics.
Explicitly disabled reporting cannot qualify as collection.
Shell programs and unknown executables gain no collection claim, and plain
assertion probes keep their existing behavior.

Regression coverage uses real Go commands for empty and named selectors and the
public task-run investigation workflow with scripted provider answers. The
original live failure remains separate evidence; it was not changed or regraded.
