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
the rejection controls pass on both versions. Fresh live qualification on runtime commit e3bd5799 completed in one iteration
with TASK_COMPLETE after 448.99 active seconds. The fresh fixture used the same
brief, native OpenCode 1.18.33 binary, Sol/Astra routes and 1800/360/120/120-second
run/stage/idle/tool caps as the before run. No extra retry or budget was added.
The first runner proof was PASS and recorded the documentation-only preservation
note. Four candidate cases passed; their module was absent on base, retained as
not-run-on-base evidence under the existing new-behavior policy.

The independent Node oracle passed and its file hashes still matched at
completion. Public `--status --inspect-evidence` confirmed current evidence and
completion. All six native streams reached EOF without dropped events, and no
owned workers remained. The first audit's assumptions about an uninspected
status view, the fixture's pre-existing virtualenv link and runner-owned Git
exclude rules were corrected explicitly; both audits remain retained.

After run: `20261005-230713-create-the-first-node-js-implementation-in-this--8a0d7414`.
The earlier run stopped waiting for permission after 1261.74 active seconds;
its second Validator also encountered the distinct #424 rework sandbox failure.
That request was not answered, and that failure was not retried or erased.

Validation: 310 changed-file tests, 90 slower proof/CLI tests, and the catalog
with 60 PASS, one existing NOT_EXERCISED and one live-Investigator SKIPPED.
Earlier scratch Go failures remain recorded; the cases passed on both upstream
and candidate, and the final complete proof-module run passed.
