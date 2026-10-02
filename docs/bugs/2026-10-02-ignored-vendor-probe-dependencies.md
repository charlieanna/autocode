# Preserve offline dependencies in independent scratch probes

Inspection of the Jira pilot prompted an independent dependency check. Its
repository supplies Git-ignored Go `vendor` assets, but `make_tree` only linked
Node/Python dependencies and copied selected generated source. A focused scratch
probe failed with `FileNotFoundError` for an ignored vendor resource before this
change. This was a deterministic reproduction, not a claim that the live pilot
had reached its independent probe or final verification.

Verification now copies ignored vendored dependencies into each scratch tree.
Files are independent copies, so tests cannot mutate the original dependency
snapshot through a shared link. A vendor tree already supplied by the chosen
Git base/overlay is preserved. Linked roots, external/state links and directory
links fail closed, using the same source-copy boundary as investigation staging.
Only files returned by Git as ignored and untracked are supplemental dependencies.
New tracked vendor files (including force-added files under an ignore rule) and
non-ignored untracked files remain part of the candidate overlay alone; copying
them into a base that lacks `vendor` would invalidate the fail-to-pass comparison.

The helper and scratch-probe regressions failed before implementation. The
verification, investigation, bug-job and architecture modules pass together:
103 tests, two optional pytest tests skipped. Local Go tests need a writable
temporary `GOCACHE`; their initial failures also reproduced on the unchanged
baseline because this tool sandbox cannot write the user's normal Go cache.
No test result, verification verdict, source comparison or approval rule was
relaxed.
