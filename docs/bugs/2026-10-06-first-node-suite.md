# First Node suite on a documentation-only baseline (#526)

A project pinned with only a regular, non-executable root README.md can add
its first Node implementation and tests. Running the new test command on the
original revision exits before producing test events because the test file does
not exist. Treating that as unproven preservation rejects an otherwise valid
first implementation.

The preservation exception requires the existing positive pinned-tree inventory,
new-behavior mode, the matching baseline revision and suite command, and no base
patch. The baseline receipt must expect structured results, exit 1 without a
timeout, and contain no results. The candidate must execute a complete, nonempty
named suite with no failures, skips or timeout. The baseline remains recorded as
broken; a note explains that there was no existing behavior to preserve. Separate
new-behavior proof still applies. Existing source, unknown files, executable
README files, links and submodules do not qualify for the inventory exception.

## Evidence

On unmodified e5a0efab, a native OpenCode 1.18.33 live run used Sol for planning
and building and Astra for review, testing and recovery. In its second attempt,
the runner recorded four named failures on baseline and the same four passes on
the candidate, with no uncollected cases. It still returned UNVERIFIED solely
because the original suite could not run. Independent Node behavior checks and
the full named suite passed. The first attempt and the recovery attempt remain
retained; this reproduction does not itself qualify the fix.

Real Node regression tests cover first-suite success, import-time new-module
failure, a broken candidate, an unknown existing source, bug-fix mode and
preservation-only mode. Both success cases fail against unmodified upstream;
the rejection controls pass on both versions. Fresh live qualification of the
final change is still required before opening its PR.
