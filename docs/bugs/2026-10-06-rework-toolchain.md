# Approved tool commands survive task rework

In the native #526 campaign on e5a0efab, the first Tester ran Node successfully.
After Resolver rework, the current task described the same command in prose.
The command selector returned an empty list, and the second Tester's Seatbelt
policy omitted Homebrew Node's shared libraries. The actual native tool reported
exit 250 with a sandbox library denial; the separate direct kernel reproduction
returned -6. This no-package.json fixture could not recover Node through manifest
discovery.

The current approved contract still contains the initial executable command.
Retain that declaration when the current task overlaps its criteria, or when
checking the whole task at completion. Unrelated task slices do not inherit it.
This preserves both tool discovery and required replay, including repetitions
and explicit exit expectations. Progressive runs retain their existing
cumulative required-check policy. Historical tasks and arbitrary prose do not
supply new executable authority.

The saved native policies reproduce the difference outside the model: the
initial policy runs Node, the rework policy denies its library. A candidate
policy derived from the same rework context runs the actual Node test while
outside-file reads and application writes remain denied.

Fresh native AFTER qualification passed on 7a713843 over eb004089. The Tester
used immutable copies of the actual blocked rework's approved contract, current
task and three protected files. OpenCode 1.18.33 with GPT-6 Astra/high executed
`node --test greet.test.cjs`: all four existing named cases passed, with exit 0
and no failures, cancellations, skips or todo cases. The production selector
retained the approved command while the current task's methods remained prose.

All 21 terminal audit checks passed: the raw provider stream matched delivered
and saved bytes, actual read-only kernel conformance passed, protected source
and Git bytes remained unchanged, and the outer owner admitted the exact
provider and keeper identities before discharge. A separately owned native
oracle checked the export and six greeting inputs. Cleanup covered 82 recorded
process identities with no survivors or unknown liveness; the native action
and terminal auditor both returned exit 0. The qualification covers this
captured Tester rework. A fresh whole public TaskRun and arbitrary toolchains
were not exercised.

The same source passed four architecture tests, 105 affected tests across seven
modules, and the full fake catalog: 61 PASS, one existing NOT_EXERCISED and one
live-Investigator SKIPPED, with exit 0. No full-suite run was needed for this
scoped selector change. These pins describe 7a713843 over eb004089; they are not
live qualification of a later master revision.
