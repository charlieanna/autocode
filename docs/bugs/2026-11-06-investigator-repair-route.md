# Investigator repair loses its route after a pause

The Investigator uses a temporary independent route. A boundary pause releases
that route before saving the run, while report-only dispatch bypasses normal
Investigator preparation on resume. Route lookup then fell back to the default
review role, changing the model used to repair the saved report.

Report-repair preparation now restores the original attempt's recorded temporary
route, including model, engine, provider and reasoning level. It does so after
source and evidence validation and before ordinary boundary/transport admission.
When the role inherited its engine, the attempt's resolved engine supplies the
value; a null snapshot field is never installed as an engine. An existing
conflicting route or missing recorded route/engine refuses launch. Other
stages and old records without a temporary route marker retain their behavior.
No new transport identity, session, approval or retry allowance is created.

The existing repair-epoch reset helper moves to the repair queue module without
changing its compatibility function or behavior. This keeps the controller within
its module-size boundary while the new route logic remains in its own module.

A public CLI regression pauses before the Investigator report repair, resumes,
and compares the original and repair models before asserting task completion.
It fails on base because the repair switches to the default model. Pure cases
check restoration, engine/provider/effort conflicts, absent metadata and isolation
of copied route values.
