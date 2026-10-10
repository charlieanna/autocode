"""Conversation continuity and approval boundaries through public dashboard APIs."""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import autocode_conversation as protocol
import autocode_goals as goals
import autocode_support as support
from agent_console import CODEX_DEFAULT_MODELS, Console
from autocode_planner_routes import MANDATED_ROUTES
from dashboard_conversation_journal import append_feedback, project_conversation
from goal_fixtures import body
from units import autoplanner


class InlinePool:
    def submit(self, function, *args):
        function(*args)

    def shutdown(self, **kwargs):
        pass


class JournalBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / "project"
        (self.workspace / ".git").mkdir(parents=True)
        self.calls = []

        def gatherer(messages, model, workdir):
            self.calls.append(("gatherer", messages[-1]["text"]))
            return "I have updated the requirements."

        def planner(messages, route, workdir):
            human = next(row for row in reversed(messages) if row["role"] == "user")
            self.calls.append(("planner", human["text"]))
            turn = human["logical_turn_id"]
            revision = sum(row["role"] == "user" for row in messages)
            return json.dumps(
                {
                    "contract_version": 1,
                    "kind": "autocode.planner-structured-draft",
                    "goal": human["text"],
                    "requirements": [human["text"]],
                    "milestones": ["Implement"],
                    "parallelism": [],
                    "unresolved_questions": [],
                    "source_revision": {"requirements_revision": revision, "logical_turn_id": turn},
                    "attribution": {"role": "planner", "model": route["model"], "reasoning_effort": "high"},
                    "freshness": {"state": "fresh", "updated_at": protocol.now()},
                }
            )

        self.console = Console(
            [self.workspace],
            self.root / "unused-runner.py",
            lambda: None,
            conversation_root=self.root / "conversations",
            conversation_provider=gatherer,
            conversation_planner=planner,
        )
        self.addCleanup(self.console.pool.shutdown, wait=True)
        self.addCleanup(lambda: self.console.conversations.close())
        self.available = sorted(
            {route["model"] for route in MANDATED_ROUTES.values()}
            | {"openai/" + model for model in CODEX_DEFAULT_MODELS.values()}
        )
        self.console.catalogue.fetch = lambda **kwargs: {"usable": True, "models": self.available}
        self.console._probe_conversation_transport = lambda *_: {"status": "ok", "data": {"version": "1.18.33"}}
        store = self.console.conversations.continuous
        store.pool.shutdown(wait=True)
        store.pool = InlinePool()
        self.actions = []

        def enqueue(workspace, run, label, args, **kwargs):
            self.actions.append((workspace, run, label, args))
            return {"id": "action-1", "status": "queued"}

        self.console.enqueue = enqueue

    def conversation(self):
        doc = self.console.conversation_create({"text": "Build a greeting CLI", "request_id": "first"})
        doc = self.console.conversation_get(doc["id"])
        self.assertEqual("ready", doc["status"])
        self.assertEqual("current", doc["plan_drafts"][-1]["status"])
        return doc

    def refresh_draft(self, doc):
        target = doc["draft_update"]
        return self.console.conversations.refresh_draft(
            doc["id"],
            requirements_revision=target["requirements_revision"],
            logical_turn_id=target["logical_turn_id"],
            request_id="refresh-" + target["logical_turn_id"],
        )

    def source_handoff(self, *, legacy=False, human="Build a greeting CLI in `greet.py`.", adoption=None):
        """Real attach/create path, with deterministic providers and no runner launch."""
        store = self.console.conversations
        store.new = store.intake if legacy else store.continuous
        if legacy and not isinstance(store.intake.pool, InlinePool):
            store.intake.pool.shutdown(wait=True)
            store.intake.pool = InlinePool()
        store.provider = lambda *args: "Optional suggestion: add `assistant_only.py`."
        original_planner = store.continuous.planner

        def draft(*args):
            value = json.loads(original_planner(*args))
            value["milestones"] = ["Optional suggestion: add `draft_only.py`."]
            return json.dumps(value)

        store.continuous.planner = draft
        doc = self.console.conversation_create({"text": human, "request_id": "source-" + str(len(self.actions))})
        doc = self.console.conversation_get(doc["id"])
        if adoption:
            store.send(doc["id"], adoption, "adoption-" + doc["id"])
            doc = self.console.conversation_get(doc["id"])
            if not legacy:
                self.refresh_draft(doc)
                doc = self.console.conversation_get(doc["id"])
        self.assertEqual("ready", doc["status"])
        handoff = store.handoff(doc["id"])
        available = sorted(
            set(self.available) | {route["model"] for route in doc.get("configured_routes", {}).values()}
        )
        self.console.catalogue.fetch = lambda **kwargs: {"usable": True, "models": available}
        self.console.conversation_attach({"id": doc["id"], "workspace": str(self.workspace)})
        args = self.actions[-1][-1]
        self.assertEqual(not legacy, "--conversation-handoff" in args)
        self.assertNotIn("--approve-goal", args)
        return {"task": args[0], "answers": {}, "brief_feedback": []}, handoff

    def literal_contract(self, *names):
        contract = body()
        contract["deliverables"] = list(names)
        contract["milestones"][0]["affected_paths"] = [name for name in names if name.endswith(".py")]
        contract["acceptance_criteria"] = [
            {
                "id": "C1",
                "criterion": "The CLI prints a greeting.",
                "verification_method": "test: greeting output",
                "human_review": False,
            }
        ]
        return contract

    def test_actual_handoff_trace_does_not_promote_assistant_or_draft_literals(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                state, handoff = self.source_handoff(legacy=legacy)
                # The same public trace gate used by the Planner sees only the human literal.
                goals.check_requirement_trace(state, {}, self.literal_contract("greet.py"))
                with self.assertRaisesRegex(ValueError, "greet.py"):
                    goals.check_requirement_trace(state, {}, self.literal_contract("other.py"))
                suggestion = handoff["messages"][1]["text"]
                with self.assertRaisesRegex(ValueError, "source_quote is not in"):
                    goals.check_requirement_handoff(
                        state, {"requirements": [{"id": "R1", "text": suggestion, "source_quote": suggestion}]}
                    )
                # The full role-attributed context is still what every worker receives as task.
                self.assertEqual(handoff, json.loads(state["task"])["handoff"])
                prompt, _ = autoplanner.context(
                    {
                        **state,
                        "workspace": str(self.workspace),
                        "settings": {"engine": "opencode", "joint_planning": True, "roles": {"requirements": {}}},
                    },
                    "requirements_gather",
                    self.root / "owned-state.json",
                )
                packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual(handoff, json.loads(packet["task"])["handoff"])
                self.assertEqual([handoff["messages"][0]["text"]], goals.source_texts(state))

    def test_cold_dashboard_projects_title_and_chat_without_exposing_task_envelope(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                state, handoff = self.source_handoff(legacy=legacy)
                run = self.workspace / (".autocode/runs/owned-cold-view-" + str(legacy))
                run.mkdir(parents=True)
                state.update(workspace=str(self.workspace), status="WAITING_FOR_USER")
                checkpoint = run / "state.json"  # This test owns the whole temporary run.
                checkpoint.write_text(json.dumps(state))
                before = checkpoint.read_bytes()
                cold = Console(
                    [self.workspace],
                    self.root / "unused-runner.py",
                    lambda: None,
                    conversation_root=self.root / "cold-conversations",
                    project_store_root=self.root / "cold-projects",
                )
                self.addCleanup(cold.pool.shutdown, wait=True)
                cold._intervention_view = lambda *args: {"mode": "unavailable"}
                cold._registered = lambda: {
                    "workspaces": [],
                    "workspace_ids": {},
                    "runs": {},
                    "diagnostics": [],
                    "error": None,
                    "location": None,
                }
                self.assertIsNone(cold._conversation_store)
                listed = next(row for row in cold.discover() if row.get("run") == str(run))
                self.assertEqual(handoff["title"], listed["task"])
                view = cold.task_view(self.workspace, run)
                self.assertEqual(handoff["title"], view["task"])
                self.assertEqual(state["task"], view["original_task"])
                self.assertEqual(handoff["messages"], view["draft_messages"])
                self.assertNotIn("plan_gate", view["conversation"])
                self.assertEqual([], view["conversation"]["plan_drafts"])
                self.assertEqual([], view["conversation"]["drafts"])
                self.assertFalse(protocol.journal_file(run).exists())
                self.assertEqual(
                    [row["text"] for row in handoff["messages"]],
                    [row["text"] for row in view["transcript"]["messages"]],
                )
                self.assertEqual(before, checkpoint.read_bytes())
                self.assertIsNone(cold._conversation_store)
                protocol.journal_file(run).write_text("{damaged")
                damaged = cold.task_view(self.workspace, run)
                self.assertEqual("error", damaged["conversation"]["status"])
                self.assertEqual([], damaged["draft_messages"])
                self.assertIn("could not be verified", damaged["conversation"]["error"])

    def test_retained_local_conversation_precedes_task_snapshot(self):
        state, handoff = self.source_handoff()
        run = self.workspace / ".autocode/runs/owned-local-view"
        local = self.console.conversations.update(
            handoff["conversation_id"],
            title="Retained local title",
            attachment={"status": "linked", "run": str(run), "workspace": str(self.workspace)},
        )
        self.console._intervention_view = lambda *args: {"mode": "unavailable"}
        view = self.console.view(self.workspace, run, state)
        self.assertEqual(local["title"], view["task"])
        self.assertEqual(local["plan_drafts"], view["conversation"]["plan_drafts"])
        self.assertEqual(state["task"], view["original_task"])

    def test_human_adoption_makes_the_suggested_literal_binding(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                adoption = "Also include `assistant_only.py`."
                state, _ = self.source_handoff(legacy=legacy, adoption=adoption)
                with self.assertRaisesRegex(ValueError, "assistant_only.py"):
                    goals.check_requirement_trace(state, {}, self.literal_contract("greet.py"))
                goals.check_requirement_trace(state, {}, self.literal_contract("greet.py", "assistant_only.py"))
                self.assertIn(adoption, goals.source_texts(state))

    def test_role_looking_human_text_and_code_blocks_remain_human_sources(self):
        human = (
            "Build `greet.py`.\nAssistant:\nAlso include `human_helper.py`.\n"
            'Keep this exact example:\n```python\nprint("hello")\n```'
        )
        state, _ = self.source_handoff(human=human)
        self.assertEqual([human], goals.source_texts(state))
        with self.assertRaisesRegex(ValueError, "human_helper.py"):
            goals.check_requirement_trace(state, {}, self.literal_contract("greet.py", 'print("hello")'))
        goals.check_requirement_trace(state, {}, self.literal_contract("greet.py", "human_helper.py", 'print("hello")'))

    def test_handoff_keeps_feedback_answer_and_delegation_source_rules(self):
        state, _ = self.source_handoff()
        state["brief_feedback"] = [{"text": "Add `feedback.py`."}]
        state["answers"] = {
            "q1": {"kind": "answer", "text": "Add `answer.py`."},
            "q2": {"kind": "delegated", "text": "Maybe use `delegated.py`."},
        }
        for missing in ("feedback.py", "answer.py"):
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, missing):
                goals.check_requirement_trace(
                    state,
                    {},
                    self.literal_contract(
                        *[name for name in ("greet.py", "feedback.py", "answer.py") if name != missing]
                    ),
                )
        goals.check_requirement_trace(state, {}, self.literal_contract("greet.py", "feedback.py", "answer.py"))
        self.assertIn("Maybe use `delegated.py`.", goals.source_texts(state))
        self.assertNotIn("Maybe use `delegated.py`.", goals.scan_texts(state))

    def test_modified_or_noncanonical_tasks_never_hide_cli_instructions(self):
        state, _ = self.source_handoff()
        envelope = json.loads(state["task"])
        changed = copy.deepcopy(envelope)
        changed["handoff"]["messages"][0]["role"] = "assistant"  # invalid digest/linkage
        extra = copy.deepcopy(envelope)
        extra["human_instruction"] = "Also add `cli_extra.py`."
        altered_instructions = copy.deepcopy(envelope)
        altered_instructions["instructions"] += " Also add `cli_extra.py`."
        too_deep = '{\n  "kind": "autocode.conversation-task",\n  "handoff": ' + "[" * 2000 + "0" + "]" * 2000 + "}"
        for task in (
            too_deep,
            "Also add `cli_extra.py`.\n" + state["task"],
            state["task"] + "\nAlso add `cli_extra.py`.",
            state["task"].replace("{", '{\n  "kind": "duplicate",', 1),
            json.dumps(changed, ensure_ascii=False, indent=2),
            json.dumps(extra, ensure_ascii=False, indent=2),
            json.dumps(altered_instructions, ensure_ascii=False, indent=2),
            "Conversation reference: old.\nYou: Build `greet.py`.\nAssistant: use `old.py`.",
        ):
            with self.subTest(task=task[-90:]):
                self.assertEqual([task], goals.source_texts({"task": task}))
                self.assertEqual([task], goals.scan_texts({"task": task}))
        with self.assertRaisesRegex(ValueError, "cli_extra.py"):
            goals.check_requirement_trace(
                {"task": state["task"] + "\nAlso add `cli_extra.py`."},
                {},
                self.literal_contract("greet.py", "assistant_only.py", "draft_only.py"),
            )

    def test_two_turn_draft_handoff_retains_independent_receipts_without_approval(self):
        doc = self.conversation()
        self.console.conversations.send(doc["id"], "Preserve the existing interface", "second")
        doc = self.console.conversation_get(doc["id"])
        self.assertEqual([1, 2], [row["revision"] for row in doc["requirements"]["revisions"]])
        self.assertEqual(2, doc["plan_drafts"][-1]["requirements_revision"])
        self.assertTrue(doc["draft_update"]["held"])
        with self.assertRaisesRegex(ValueError, "Update draft"):
            self.console.conversation_attach({"id": doc["id"], "workspace": str(self.workspace)})
        self.refresh_draft(doc)
        attached = self.console.conversation_attach({"id": doc["id"], "workspace": str(self.workspace)})
        args = self.actions[-1][-1]
        self.assertIn("--conversation-handoff", args)
        self.assertNotIn("--approve-goal", args)
        source = Path(args[args.index("--conversation-handoff") + 1])
        handoff = protocol.load_handoff(source, workspace=self.workspace)
        self.assertEqual(doc["id"], handoff["conversation_id"])
        self.assertEqual(2, len(handoff["planner_dispatches"]))
        self.assertEqual(4, len(handoff["messages"]))
        self.assertEqual("openai/gpt-6-sol", handoff["messages"][1]["execution"]["model"])
        self.assertEqual("starting", attached["attachment"]["status"])

    def test_task_feedback_is_idempotent_and_invalidates_preview_without_approval(self):
        doc = self.conversation()
        run = self.workspace / ".autocode/runs/test"
        run.mkdir(parents=True)
        protocol.ingest_handoff(run, self.console.conversations.handoff(doc["id"]))
        row = {"id": "message-2", "text": "Add a JSON output option", "status": "received"}
        append_feedback(run, row, product_change=True)
        append_feedback(run, row, product_change=True)
        saved = protocol.read_journal(run)["conversation"]
        self.assertEqual(1, sum(item["id"] == "task-message-2" for item in saved["messages"]))
        self.assertEqual(2, len(saved["requirements"]["revisions"]))
        self.assertEqual("stale", saved["plan_drafts"][-1]["freshness"]["state"])
        self.assertNotIn("approval_event", saved)

    def test_concurrent_turn_cannot_attach_an_older_handoff(self):
        doc = self.conversation()
        handoff = self.console.conversations.handoff(doc["id"])
        self.console.conversations.send(doc["id"], "Also support Unicode names", "second")
        self.refresh_draft(self.console.conversation_get(doc["id"]))
        with self.assertRaisesRegex(ValueError, "conversation changed"):
            self.console.conversations.claim_attachment(
                doc["id"], {"status": "starting", "workspace": str(self.workspace), "handoff_digest": handoff["digest"]}
            )
        self.assertIsNone(self.console.conversation_get(doc["id"])["attachment"])

    def test_checkpoint_does_not_link_until_runner_accepts_same_journal(self):
        doc = self.conversation()
        attached = self.console.conversation_attach({"id": doc["id"], "workspace": str(self.workspace)})
        self.console.action_log = lambda *args: [{"id": "action-1", "status": "running"}]
        run = self.workspace / ".autocode/runs/created"
        run.mkdir(parents=True)
        args = self.actions[-1][-1]
        state = {"workspace": str(self.workspace), "task": args[0], "status": "RUNNING"}
        checkpoint = run / "state.json"
        checkpoint.write_text(json.dumps(state))
        self.assertEqual("starting", self.console.conversation_get(doc["id"])["attachment"]["status"])
        handoff = protocol.load_handoff(Path(attached["attachment"]["handoff"]), workspace=self.workspace)
        protocol.ingest_handoff(run, handoff)
        state["conversation_handoff"] = {"conversation_id": doc["id"], "digest": handoff["digest"]}
        checkpoint.write_text(json.dumps(state))
        self.assertEqual("linked", self.console.conversation_get(doc["id"])["attachment"]["status"])

    def test_plan_gate_uses_current_architect_token_and_approval(self):
        doc = self.conversation()
        run = self.workspace / ".autocode/runs/test"
        run.mkdir(parents=True)
        protocol.ingest_handoff(run, self.console.conversations.handoff(doc["id"]))
        contract = {"task_id": "fixture", "revision": 2, "body": {"open_blocking_questions": []}}
        contract["hash"] = support.digest(contract)
        token = "r2:" + contract["hash"]
        event = {"kind": "goal_approval", "actor": "user_cli", "token": token}
        contract.update(approval_status="approved", approval_event=event)
        state = {
            "workspace": str(self.workspace),
            "goal_contract": contract,
            "user_events": [event],
            "planning": {"final_token": "r1:old"},
        }
        self.assertFalse(project_conversation(run, state)["plan_gate"]["approved"])
        state["planning"]["final_token"] = token
        self.assertTrue(project_conversation(run, state)["plan_gate"]["approved"])
        state["user_events"] = []
        self.assertFalse(project_conversation(run, state)["plan_gate"]["approved"])
        state["user_events"] = [event]
        append_feedback(
            run,
            {"id": "change-1", "text": "Support JSON output too", "status": "received"},
            product_change=True,
            goal_token=token,
        )
        gate = project_conversation(run, state)["plan_gate"]
        self.assertTrue(gate["pending_product_change"])
        self.assertFalse(gate["approved"])
        self.assertFalse(gate["architect_reviewed"])

    def test_public_build_action_forwards_exact_token_through_actual_mixin(self):
        run = self.workspace / ".autocode/runs/task"
        run.mkdir(parents=True)
        self.console.workspace_for = lambda raw: self.workspace
        self.console.run_for = lambda workspace, raw: run
        self.console.view = lambda workspace, run: {
            "goal_token": "r2:abc",
            "goal": {
                "revision": 2,
                "hash": "abc",
                "approval_status": "approved",
                "approval_event": {"token": "r2:abc"},
            },
        }
        self.console._intervention_view = lambda workspace, run: {"mode": "durable", "capable": True}
        request = {
            "action": "continue",
            "workspace": str(self.workspace),
            "run": str(run),
            "token": "r2:abc",
            "confirmation": "r2:abc",
            "expected_goal_token": "r2:abc",
        }
        self.console.mutate(request)
        self.assertEqual(["--no-chat", "--resume-paused", "--expected-goal-token", "r2:abc"], self.actions[-1][-1])
        self.actions.clear()
        with self.assertRaisesRegex(ValueError, "approved plan changed"):
            self.console.mutate({**request, "expected_goal_token": "r1:old"})
        self.assertEqual([], self.actions)


if __name__ == "__main__":
    unittest.main()
