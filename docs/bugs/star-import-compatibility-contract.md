# Compatibility export contracts

The OpenCode, planning and orchestrator compatibility modules star-import their
implementations. Removing an implementation import can therefore remove an
integration's patch target even when it appears unused in that implementation.

`tests/test_architecture.py` pins the names used or patched by active runtime and
suite consumers, in both flat and installed-package imports. It also checks that
each export is the implementation's same object, preserving patch behavior.
The expected names are explicit rather than derived from the current exports.

The audit included direct attributes, named imports and `patch.object` targets.
The orchestrator import consumers use `drive` and `SKIP`.
An obsolete `tools/test_planning.py` reference to removed `planning.apply` is
outside the discovered suite and does not expand the current API contract.
Structural shim replacement remains part of #695; broad lint exceptions are
unnecessary for these tests and would conceal unrelated unused imports.
