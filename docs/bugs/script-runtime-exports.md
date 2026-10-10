# Direct CLI startup lost delegated runtime exports (#823)

The lint cleanup in #817 removed imports that appeared unused locally but were
part of the controller/support interface used by delegated runtime modules.
On master `7243516e`, `python tools/autocode.py --help` failed while importing
`revision_guard` from `autocode_goals`. Other missing exports affected status,
role schemas, report loading, recovery and dashboard conversation routes.

Restore the consumed exports in both package and direct-script import branches.
Explicit `name as name` aliases declare intentional reexports to the linter.
The focused regression exercises both import modes in fresh processes and starts
both public CLI entry modes; existing role/report tests exercise schema behavior.

The same master also exceeds the existing controller/support line-count limits
after import formatting. That architecture failure is retained separately; its
assertions and limits are unchanged by this runtime repair.
