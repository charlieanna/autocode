# Saved limits at a parallel Builder retry (#1096)

Children copied the parent's settings when their batch was prepared. Parent
limit overrides changed only the parent's checkpoint, while an explicit member
retry reloaded the child's old settings. A new attempt therefore still used its
old tool and stage deadlines, and an exhausted child milestone repeated its
5400-second stop after the parent accepted a 21600-second budget.

Explicit parent retry admission now applies only saved operator-owned tool,
stage and milestone bounds, together with their ownership, to the selected
children. It locks and validates the whole selection against the current
parent, batch, approved contract, task and paths before saving any update.
An automatic worker retry or ordinary recovery never performs this handoff.

The update changes settings only. Routes, earlier attempt records, elapsed
accounting, consumed allowances and paused state are retained. Reconciliation
still judges a stopped attempt by its captured deadlines and receipt, and any
new pause it commits still prevents further work. A new attempt and its
milestone gate use the accepted saved bounds. Zero keeps its existing disabled
meaning; an explicit override does not reset a child's spent time.

Focused offline CLI coverage checks separately saved tool/stage overrides in
the next actual launch, the unchanged historical deadlines, an exhausted
milestone's continuation with its spend retained, successful sibling retention,
and the unchanged approved goal. Small policy tests cover ownership, exact
values, routes, all-member validation and a busy later child without partial
updates. No real-time timeout wait or live model is needed to reach this path.
