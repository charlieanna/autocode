# Rejected planning report replay loop

The K8s task repeatedly recovered the same finished repair output (original finish time 2026-09-20T09:11:36Z) and nested it in more archive folders. Continue did not start a fresh provider request.

Root cause: accept_repaired_report validated a speculative state copy and saved rejection into that copy. The outer pause handler then saved the original owner state, restoring its active attempt and old pending-repair error. The repair record itself was shared and already pointed into the archive, so later recovery repeated the same rejection.

Fixes:
- Commit repair rejection to the authoritative owner checkpoint.
- Never reconcile an attempt already marked rejected.
- Explicit planning retry recognizes the old corrupted checkpoint shape, retains its rejected evidence, archives the exhausted repair budget, and dispatches a fresh numbered planning attempt. It does not clear uncertain attempts, retry implementation, or approve a goal.
- Joint planning and report-repair prompts explain original conversation context versus actual saved feedback IDs, proposed defaults versus explicit delegation, and milestone references to acceptance criterion IDs.
- The browser's initial migration placeholder no longer presents the full input conversation as a completed draft plan. Paused drafts no longer claim the agents are still working.

Validation: 121 runner/planning/goal tests passed, plus a new provenance test. Subsequent focused retry/provenance checks passed, including full fake-provider CLI reproduction of an old rejected checkpoint. The fake resume produced a different output file and stopped for user input without approving implementation. Browser verification confirmed the plan placeholder and live Running state.

Live verification: explicitly retried K8s planning once through the existing dashboard Continue action (926a8ea966f14bc986fd62fdf501eef2). The worker started astra_discovery-02, instead of recovering the old rejected repair. The fresh report used original_request for the user's Kubernetes/adaptive-learning corrections. Bounded repairs then addressed milestone IDs and proposed decisions incorrectly placed in delegated_decisions. No brief approval or implementation action was submitted.

Result: the repaired draft passed validation. K8s is WAITING_FOR_USER with three saved questions (tech-stack, topic-structure, content-volume), no active stage, and a draft goal. The repaired report preserves the user's adaptive-learning/Kubernetes scope and references actual acceptance criterion IDs. This is a normal clarification checkpoint, not the previous invalid-output pause.
