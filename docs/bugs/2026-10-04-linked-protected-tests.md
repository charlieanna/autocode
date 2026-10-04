# Linked tests at startup (#307)

A read-only review of etcd exited 2 before any model launch: the new-run test
inventory refused every symbolic link, and etcd tracks eleven
`client/v3/**/example_*_test.go` links into `tests/integration/`.

A test link is now bound as a link: its exact relative target and the identity
of what it names (`docs/protected-tests.md`). The bundle and the original
replay recreate the link and restore its targets, and the scratch overlay
replaces a candidate's link or linked directory instead of writing through it.
Absolute, escaping, dangling, cyclic, directory and non-canonical links still
stop a new run before any model launch. Regular files keep their previous
identity, so saved bindings keep their hashes.

Checked without a model on etcd `64f26db`: the inventory binds 595 tests,
including the 11 links, and the retained bundle verifies with the links kept
as links. A fresh live review qualification on etcd has not been run.
