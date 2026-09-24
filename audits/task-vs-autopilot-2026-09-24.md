# Task product vs Autopilot — 2026-09-24

Offline comparison of two questions, on `05842b902b419bf110fee1c5c7092de3a16f1c43`:

1. Given a bounded task and a Builder product, what does the task runner keep?
2. Given agent results, crashes, and out-of-order events, what does Autopilot keep?

No live model was called. Both runs used the project virtualenv and fake providers.

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tools.test_assignment_scenarios -v
# 17 tests, 23.146s, OK

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tools.test_autopilot_torture -v
# 23 tests, 22.888s, OK
```

The suites do not share inputs. The task suite feeds one greeting assignment a scripted Builder product and checks the tree and the saved stage. The Autopilot suite feeds the controller contradictory, stale, and crashed results and checks workflow invariants. Where the same property shows up in both, the outcomes agree.

## Same property, both sides

| Situation | What the task produces | What Autopilot keeps |
| --- | --- | --- |
| Edit stays inside `src/greeting.py` | Candidate is integrated. Greeting becomes `Welcome`. Tests and config stay put. Next stage is validation. Status is not complete. | A Builder result applies only when it matches the current task and contract. Completion still needs the current candidate's evidence. |
| Tests, config, or an unrelated file change with the greeting | Parent tree is unchanged. The escaped edit stays in the worker tree for inspection and is not promoted. | Serial assignment uses the same ownership gate: files outside `affected_paths` pause as `PAUSED_ASSIGNMENT_SCOPE` and are not recorded as the implementation. |
| No source change | Greeting stays `Hello`. The milestone records a no-progress batch. | An empty change increments `no_progress_batches` and does not count as implementation progress. |
| Syntax error, or tests still expect `Hello`, while the report says complete | The broken or unverified tree can be stored. A completion claim pauses at `PAUSED_COMPLETION_GATE`. | `COMPLETE` requires current checks, current evidence, and no open blocker. Prose that says complete has no authority. |
| Builder writes `Welcome` and then exits | Parent stays `Hello`. The worker tree still has `Welcome`. The run pauses instead of launching a replacement that drops the file. | A crash at a stage boundary reloads the saved state and does not duplicate accepted work. |
| Report is missing its summary | The edit is repaired in place and integrated once. The Builder is not asked to rewrite the file. | Malformed or contradictory model output is rejected. Structured state is what advances. |
| Builder hangs | The worker is stopped. That milestone is not `BUILT`. Parent files stay as seeded. | A late or duplicate worker cannot become the authoritative mutation. |
| Builder asks for permission or an architecture change | No file is invented. The run pauses with the request kind `permission` or `infeasible`. | Human authorization is not manufactured, broadened, or reused from a stale plan. |
| Two independent tasks | Two worktrees, two Builder sessions, both results integrated. | Disjoint work can proceed. A finished sibling is kept when the other side fails. |
| Two tasks edit `src/greeting.py` | They are not scheduled together. A forced overlapping batch is refused and the parent greeting stays `Hello`. | Overlapping paths are not merged. |
| Parts A and B exist, C and D do not, report says "substantially complete" | A and B are stored. C and D are absent. The prose is saved. Status is not complete. | A reviewer's `PASS` cannot close an open correction, and unverified criteria cannot become pass. |

## What only the task suite checks

These are products of the greeting assignment. Autopilot's torture suite does not replay this fixture.

- Correct greeting candidate, with tests and config byte-for-byte unchanged.
- Hidden test edit on the serial Builder path, retained in the tree and refused before `implementation` is saved.
- Compile failure left on disk, then an explicit completion claim refused.
- Sol `FAIL` with exit code 1, then the same completion claim refused.
- Malformed-report repair that does not replay the edit.
- Partial files A and B stored while the completion sentence is ignored.

## What only the Autopilot suite checks

These are controller invariants. The greeting task suite does not generate these event orders.

- Planner cannot drop a requirement, weaken a criterion, approve a cycle, or approve a missing dependency.
- Reviewer rejection and a scope change during revision do not approve a new plan.
- Stale Builder, Sol, and Astra results, including an old session after replan, do not apply to the current candidate.
- Sol `PASS` cannot override an open Astra rework, in either order. Sol `FAIL` plus Astra `COMPLETE` stays open.
- One reviewer `BLOCKED` or crashed keeps the work open.
- Finding identity survives omission, duplication, retraction, and a close attempt from the wrong reviewer.
- Evidence must name the current candidate and a real executed check. `UNVERIFIED` never becomes `PASS`. One unchecked screen blocks completion.
- Corrections stay bounded and the latest candidate is revalidated.
- Restart after a crash does not duplicate accepted work. A second launch and a late worker are rejected.
- A `PASS` that arrives after completion cannot invent another completion.
- A runner upgrade is explicit. An old worker stays stale.
- 360 seeded chaos steps hold the ten invariants after every event.

## Reading the two results together

The task runner's job on this assignment is scope and evidence: keep the greeting edit, refuse the escaped edit, and never treat a claim as completion. That held for all 17 cases.

Autopilot's job is the state machine around that work: approved intent, current candidate, open defects, and a single authoritative mutation. That held for all 23 cases, including the chaos sequences.

Nothing in this comparison is a live-model product trial. A real Builder can still write a bad greeting; these checks only show that the runner and Autopilot refuse to treat that product as finished work.
