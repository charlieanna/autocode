# Preservation needs usable baseline evidence

Issue #479: identical import failures on the original and candidate suites could
produce PASS even though neither suite established preserved behavior. A genuine
targeted fail-to-pass test did not make the unrelated broken suite usable.

The suite comparison now leaves preservation UNVERIFIED when the original suite
has collection errors or no passing tests, or when candidate collection is
broken. It still compares observed test identities so an actual named regression
remains FAIL. Existing assertion failures remain supported when complete
collection includes passing tests and no behavior newly fails.

A new project may introduce its first product source and suite. This exception
requires a positively identified document-only pinned Git base: an empty tree or
only a regular non-executable root `README.md` (mode `100644`). These are the
inventories of all seven current greenfield catalog seeds. Every other filename
or mode blocks the exemption, including extensionless programs, executable
README files, symlinks and submodules. Source/test filename conventions cannot
prove the absence of existing behavior. The proof also requires new behavior
without a base patch, recognized complete empty-collection evidence and a fully
passing candidate suite. Native unittest on Python 3.14 uses exit 5 for an empty
discovery; that observed empty-collector form is handled.

Actual programs named `legacy` and executable `README.md` each passed on the
pinned base and failed after a candidate edit. Filtering collection to only the
new feature test nevertheless produced PASS under the earlier absence-based
inventory check. Both public controls now return UNVERIFIED, including when the
candidate removes the README's executable bit. The inventory is read from the
pinned base, so candidate changes cannot erase pre-existing behavior.

Actual-process controls exercise baseline() and verify(), including import-only
and partial collection failures, an all-failing baseline, healthy preservation,
pre-existing assertion failures, an observed named regression and first-suite
compatibility. The import-only, partial and all-failing controls returned stale
PASS before the guard and UNVERIFIED afterward. These are local executable
controls, not a claim of live-model reliability.
