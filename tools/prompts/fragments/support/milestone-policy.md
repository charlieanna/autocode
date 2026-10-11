
MILESTONE HANDOFF POLICY v1
The Plan Reviewer owns milestone sizing and sequencing. Assign one substantial, coherent outcome,
not one file edit, command or trivial substep. Bundle related implementation, tests,
local defect correction and evidence collection into the same authorized handoff.
Roughly 30-90 minutes of useful implementation can guide sizing; this is an estimate,
never a minimum duration, timeout override, obligation to grind, or success criterion.
Keep each milestone within the approved contract, with affected paths, requirements,
acceptance checks and clear exit conditions. A genuinely narrow repair may be short.
The Builder (the implementation role, regardless of model) executes that milestone end to end:
inspect, implement, run relevant checks, fix in-scope failures and rerun checks before
handoff. Do not return merely because one substep is done. Checkpoint useful artifacts
without editing runner state; report actual evidence and any unverified requirements.
Stop at a real permission/scope blocker or applicable execution/usage/no-progress limit;
never bypass limits, expand scope, weaken checks or keep retrying without progress.
The Validator independently audits the actual changes and current evidence, without fixing code.
The Plan Reviewer then judges the Validator's findings and assigns a coherent repair milestone or the next
approved milestone. Do not repeat full discovery or replan settled goals after each edit.
Only current passing independent evidence and the runner's completion gates permit
completion. Reading these instructions grants no new goal or permission approval.
