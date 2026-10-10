"""Recorded sample tasks for Figma comparison, using the real dashboard UI.

All records, patches, receipts and workspaces are disposable. These demonstrate
presentation states, never acceptance of an implementation or a real run.
"""

import copy
import os

import unified_browser_fixture as fixture

original_scenarios = fixture.scenario_states


def scenarios(workspace):
    states = original_scenarios(workspace)
    milestones = [
        "Conversation and requirements",
        "Plan reviewed and approved",
        "Navigation and task overview",
        "Context panel and mobile drawer",
        "Full browser journey",
        "Independent implementation review",
    ]
    for name, source in [
        ("building", "running"),
        ("requirements", "waiting"),
        ("reviewed-plan", "plan"),
        ("recovery", "running"),
        ("ready", "completed"),
    ]:
        state = copy.deepcopy(states[source])
        state["task"] = "Build a clearer agent dashboard"
        state["goal_contract"]["body"]["intended_outcome"] = state["task"]
        state["goal_contract"]["body"]["milestones"] = [
            {"id": "M" + str(i + 1), "objective": text} for i, text in enumerate(milestones)
        ]
        state["_fixture_accepted"] = ["M1", "M2", "M3"] if name in ("building", "recovery") else []
        if name == "ready":
            state["_fixture_accepted"] = ["M" + str(i) for i in range(1, 7)]
        state["current_task"].update(id="visual-M4", milestone_id="M4", objective=milestones[3])
        monitor = state["_fixture_monitor"]
        monitor["findings"] = []
        monitor["objective"] = "Adding details while keeping the conversation visible."
        state["progress_messages"] = [
            {
                "role": "user",
                "speaker": "You",
                "status": "saved",
                "text": "Approved version 7. Build it.",
                "created_at": "2026-09-22T12:20:00Z",
            },
            {
                "role": "assistant",
                "speaker": "Builder",
                "status": "received",
                "text": "The navigation and conversation foundation are complete. I’m adding the context panel while keeping your chat and draft visible.",
                "created_at": "2026-09-22T12:24:00Z",
            },
            {
                "role": "assistant",
                "speaker": "Tester",
                "status": "received",
                "text": "Persistence checks passed. Mobile layout and the complete browser journey are next.",
                "created_at": "2026-09-22T12:28:00Z",
            },
        ]
        if name == "reviewed-plan":
            state["progress_messages"] = [
                {
                    "role": "assistant",
                    "speaker": "Plan Reviewer",
                    "status": "received",
                    "text": "Revision 7 is ready for review. Inspect the scope and verification requirements before approving it.",
                    "created_at": "2026-09-22T12:28:00Z",
                }
            ]
        if name in ("building", "recovery"):
            monitor["live"] = {"state": "alive", "label": "Worker verified alive", "pid": 2430}
            monitor["active_execution"] = {"kind": "model", "model": "openai/gpt-6-sol", "reasoning_effort": "high"}
        if name == "requirements":
            state["progress_messages"] = []
            state["pending_questions"] = [
                {
                    "id": "visual-question",
                    "question": "Which project should this conversation belong to?",
                    "options": ["AutoCode", "IdleCampus"],
                    "why": "New work must use the intended repository.",
                }
            ]
        if name == "recovery":
            state["active_stage"].update(stage="astra_resolve", role="astra")
            monitor.update(
                active_role="astra",
                next_stage="astra_resolve",
                objective="Investigating the failed browser check before retrying the saved step.",
            )
            state["progress_messages"].append(
                {
                    "role": "assistant",
                    "speaker": "Resolver",
                    "status": "received",
                    "text": monitor["objective"] + " The approved plan and saved work are preserved.",
                    "created_at": "2026-09-22T12:30:00Z",
                }
            )
        if name == "ready":
            state["validation"]["criterion_results"] = [{"id": "C" + str(i), "status": "PASS"} for i in range(1, 7)]
        state["_fixture_saved_diff"] = (
            "diff --git a/app.js b/app.js\n--- a/app.js\n+++ b/app.js\n@@ -1 +1,2 @@\n-renderTaskTabs();\n+renderConversation();\n+renderWorkPane();\n"
        )
        state["stages"].insert(
            0,
            {
                "stage": "terra",
                "role": "terra",
                "exit_code": 0,
                "finished_at": "2026-09-22T12:25:00Z",
                "source_revision": fixture.FIXTURE_SOURCE,
                "changed_files": ["app.js"],
                "diff_ref": "saved.diff",
                "execution": {"kind": "model", "model": "openai/gpt-6-sol", "reasoning_effort": "high"},
            },
        )
        states["flow-visual-" + name] = state
    return states


if __name__ == "__main__":
    fixture.scenario_states = scenarios
    os.environ["AUTOCODE_PREVIEW_FIXTURE"] = "1"
    fixture.main()
