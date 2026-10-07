# Approved tool commands survive task rework

In the native #526 campaign on e5a0efab, the first Tester ran Node successfully.
After Resolver rework, the current task described the same command in prose.
The command selector returned an empty list, and the second Tester's Seatbelt
policy omitted Homebrew Node's shared libraries. Node exited -6 with a sandbox
library denial. This no-package.json fixture could not recover Node through
manifest discovery.

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
outside-file reads and application writes remain denied. Fresh live-after
qualification is still pending; these kernel checks alone do not establish it.
