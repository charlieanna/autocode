# Parallel Builder classification and recovery fixtures

[Issue #670](https://github.com/charlieanna/autocode/issues/670) includes fifteen
suite failures on master `317eb853`. Four unchanged diagnostic methods reproduced
their CI failures through real CLI processes and offline providers on macOS;
the original Linux CI result and native diagnostic failures are retained.

The isolated Builder worker recursed directly into another Builder after a
no-progress report, although the controller had queued `investigate_stuck`.
Public provider receipts showed three weak M1 calls, no Investigator, and an
incorrect integrated candidate alongside the successful sibling. The worker now
dispatches its queued classification through the existing unit boundary, repairs
only its report when needed, and permits another writer only when the controller
returns the Builder stage. Classification pins, ordinary/strong retry caps,
checker separation, and serial strong deferral stay authoritative.

The audit also found fixture and assertion drift: scripted retry failures lacked
the new bound classification report, a config-tool report repair returned a
Builder-shaped report for the Investigator, two pure policy assertions omitted
the execution-cause prefix, and a retained-repair stage assertion omitted the
new classification stage. The scripted execution diagnosis is restricted to its
configured fault and a failed prescribed command receipt; the idle greeting
fixture checks that its approved output is absent in the scratch source tree.
An empty diff by itself remains unknown.

Two other failures reached a legitimate third-review replan and novelty hold,
with `RESOLVER_PENDING` and no third paid Resolver. Their public activity never
showed an execution-budget pause being overwritten. The execution-exhaustion
fixtures explicitly disable the independent stalled-review trigger, retaining
the three-call cap and real operator-grant checks. Public CLI coverage keeps the
default three-review plan hold and proves it does not renew the strong slot.

Public parallel regressions cover unknown, operational, plan, and execution
causes, retained sibling work, report-only repair before retry, and a colliding
strong attempt deferred until the parent has reviewed its sibling. The broader
issue remains separate from the four scenario-harness expectations in PR #688.

The first edited-source focused run was **17/20 PASS, three failures**, with
unchanged source/environment seals and no owned surviving children. Two failures
showed that a fresh run ignored `--max-milestone-stalled-reviews 0`; only the
resume configuration previously saved that flag. Fresh configuration now uses
the same default/positive/zero mapping, without changing other budgets or mutating
its inputs. The third correctly triggered the native bare-checker collision guard;
the new serial-deferral test now uses the movable checker alias already supported
by the existing fixture. The original refusal controls remain unchanged.
