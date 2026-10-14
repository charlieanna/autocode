# Editable package identity during parallel CI

The acceptance CLI could stop with "Execution context changed while proving the candidate" when other test modules created fixtures in AutoCode's editable checkout. The collector filtered a whole-checkout snapshot to `tools/` only after traversal. An unrelated empty Git repository made that traversal fail; its later removal restored the binding and changed six identity fields during proof.

A deterministic reproduction creates an empty Git fixture outside the package between two calls to `execution_identity`, without changing the candidate or controller package. It changes `cache_binding_complete`, `cache_policy`, `dependencies`, `editable_sources`, `reuse_supported` and `unbound_editables` on the original collector.

Collecting the Git snapshot from the package directory prevents inspection of unrelated fixtures. Digest keys retain their checkout-relative package prefix. Package content, executable modes and symlink targets still affect the identity; unreadable package snapshots still prohibit reuse. The proof's before/after identity comparison is unchanged.

Focused coverage is in `ExecutionIdentityTests` in `tests/test_verification_schedule.py`, plus the existing acceptance CLI case `test_without_the_setting_the_builder_writes_the_tests_as_before`. Qualification uses fake providers only.
