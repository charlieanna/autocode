"""Project memory exports only this run's actual reusable human answers (#719)."""

import copy
import hashlib
import io
import json
import shlex
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_activity_log as activity_log
import autocode_checkpoints as checkpoints
import autocode_code_checkpoints as code_checkpoints
import autocode_controller_owner as controller_owner
import autocode_evidence_export as evidence_export
import autocode_goal_lifecycle as goals_lifecycle
import autocode_goals as goals
import autocode_project_memory as store
import autocode_project_memory_lifecycle as memory
import autocode_run_setup as run_setup
import autocode_run_view as run_view
import autocode_source_log as source_log
import autocode_status as status
import autocode_usage as usage
import autocode_util as util
from autocode_test_framework import Framework
from goal_fixtures import body


def question(category="technical"):
    return {
        "id": "PROJECT_LOGGER",
        "question": "Which logger should this project use?",
        "why": "Keep future changes consistent with this project.",
        "options": [],
        "proposed_default": "Use the existing logger.",
        "kind": "decision",
        "category": category,
        "delegable": True,
    }


def record():
    return {
        "version": 1,
        "framework": "unittest",
        "test_command": "python3 -m unittest discover -v",
        "conventions": [{"question": "Keep the public API?", "answer": "Keep the public API unchanged."}],
        "routes": [],
        "human_pause_states": [],
    }


def replay_check(command, **changes):
    # The lifecycle consumes an already-admitted record; native admission itself
    # is independently tested. Pure tests patch completed(), never launch it.
    return {
        "command": command,
        "exit_code": 0,
        "timed_out": False,
        "error": "",
        "supervision": {"fixture": "owned synthetic replay"},
        "supervision_sha256": "a" * 64,
        "supervision_errors": [],
        **changes,
    }


def issue(state, asked):
    identity = {
        "binding": {"fixture": "owned current-run publication"},
        "reason": "A project convention needs the person's choice.",
        "proposal": {
            "scope": "clarification",
            "origin": {"stage": "requirements_gather", "pause_status": "PAUSED_REQUIREMENTS"},
            "status": "WAITING_FOR_USER",
            "questions": [copy.deepcopy(asked)],
        },
    }
    pin = util.digest(identity)
    entry = {"identity": identity, "receipt_hash": pin, "issued_at": "2026-10-15T12:00:00+00:00", "status": "consumed"}
    state["resolver"] = {"human_escalations": {pin: entry}}
    return entry


def answer(state, asked, text="Use the existing logger."):
    event = {
        "kind": "answer",
        "actor": "user_cli",
        "question_id": asked["id"],
        "question": copy.deepcopy(asked),
        "text": text,
        "at": "2026-10-15T12:01:00+00:00",
    }
    state.setdefault("user_events", []).append(event)
    source_log.save_answer(state, asked["id"], event)
    return event


class ProjectMemoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="project-memory-lifecycle-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / ".autocode/runs/current/state.json"
        self.path.parent.mkdir(parents=True)
        self.state = {
            "version": 3,
            "workspace": str(self.root),
            "run_dir": str(self.path.parent),
            "task": "Implement the requested feature.",
            "status": "TASK_COMPLETE",
            "answers": {},
            "user_events": [],
            "acceptance_criteria": [],
            "settings": {"regression": {"test_command": "python3 -m unittest discover -v"}},
            "regression_proof": {
                "framework": {"name": "unittest"},
                "commands": {"suite": "python3 -m unittest discover -v"},
            },
        }
        source_log.seed(self.state)
        memory.install(self.state, {"status": "absent", "record": None, "sha256": None, "note": ""})

    def capture(self, asked=None, *, tags=None):
        asked = asked or question()
        memory.capture(
            self.state, {"open_questions": [asked], "project_conventions": tags if tags is not None else [asked["id"]]}
        )
        return asked

    def observed(self, state=None):
        state = state or self.state
        with patch.object(store, "write", side_effect=lambda root, current: current) as write:
            memory.publish(self.path, state)
        self.assertTrue(write.called)
        return write.call_args.args[1]

    def test_fresh_snapshot_is_copied_and_resume_never_imports_changed_memory(self):
        snapshot = {"status": "loaded", "record": record(), "sha256": "a" * 64, "note": "historical"}
        state = {"task": "A new task", "answers": {}, "user_events": [], "source_log": []}
        with patch.object(store, "load", side_effect=AssertionError("resume reloaded the project file")):
            memory.install(state, snapshot)
            frozen = copy.deepcopy(state["project_memory"])
            snapshot["record"]["conventions"][0]["answer"] = "changed after launch"
            memory.install(state, {"status": "invalid"})
            shown = memory.context(state)
            shown["conventions"][0]["answer"] = "changed by a context consumer"
            self.assertEqual(frozen, state["project_memory"])
        self.assertEqual([], state["source_log"])
        self.assertEqual({}, state["answers"])
        self.assertNotIn("goal_contract", state)
        self.assertEqual({"framework", "test_command", "conventions"}, set(memory.context(state)))
        self.assertEqual({}, memory.context({"task": "legacy saved run"}))

    def test_capture_before_human_answer_joins_later_exact_issued_event_and_source(self):
        asked = self.capture()
        self.assertEqual([], self.observed()["conventions"])
        issued = issue(self.state, asked)
        event = answer(self.state, asked)
        before = copy.deepcopy(self.state["project_memory"]["input"])
        self.assertTrue(memory.publish(self.path, self.state))
        found = store.load(self.root)["record"]
        self.assertEqual([{"question": asked["question"], "answer": event["text"]}], found["conventions"])
        self.assertEqual(["PAUSED_REQUIREMENTS", "WAITING_FOR_USER"], found["human_pause_states"])
        self.assertEqual(before, self.state["project_memory"]["input"])
        saved = (self.root / ".autocode/project.json").read_text()
        for private in ("issued_at", "receipt_hash", "source_log", issued["receipt_hash"], event["at"]):
            self.assertNotIn(private, saved)

    def test_delegated_model_unissued_changed_or_unlogged_answers_cannot_be_exported(self):
        asked = self.capture()
        issue(self.state, asked)
        answer(self.state, asked)
        original = copy.deepcopy(self.state)
        changes = (
            lambda state: state["answers"][asked["id"]].update(kind="delegated"),
            lambda state: state["answers"][asked["id"]].update(actor="model"),
            lambda state: state.update(user_events=[]),
            lambda state: state.update(resolver={}),
            lambda state: next(iter(state["resolver"]["human_escalations"].values())).update(receipt_hash="changed"),
            lambda state: next(iter(state["resolver"]["human_escalations"].values()))["identity"]["proposal"].update(
                scope="permission"
            ),
            lambda state: state["answers"][asked["id"]]["question"].update(question="A different question"),
            lambda state: state.update(source_log=[]),
            lambda state: state["source_log"][-1].update(sha256="0" * 64),
            lambda state: state["source_log"][-1].update(text="A model-authored paraphrase"),
            lambda state: state["answers"][asked["id"]].update(kind="approval"),
        )
        for index, change in enumerate(changes):
            with self.subTest(change=index):
                state = copy.deepcopy(original)
                change(state)
                self.assertEqual([], self.observed(state)["conventions"])

    def test_only_explicit_reusable_categories_are_eligible(self):
        for category in (
            "technical",
            "behavior",
            "other",
            "permission",
            "cost",
            "quota",
            "external_side_effect",
            "requested_outcome",
            None,
        ):
            with self.subTest(category=category):
                state = copy.deepcopy(self.state)
                asked = question(category)
                if category is None:
                    asked.pop("category")
                memory.capture(state, {"open_questions": [asked], "project_conventions": [asked["id"]]})
                issue(state, asked)
                answer(state, asked)
                self.assertEqual(
                    1 if category in {"technical", "behavior", "other"} else 0, len(self.observed(state)["conventions"])
                )

    def test_requirements_history_can_tag_an_exact_old_question_but_not_a_task_or_unknown_id(self):
        asked = question()
        self.state["requirements_history"] = [{"report": {"open_questions": [asked]}}]
        memory.capture(self.state, {"open_questions": [], "project_conventions": [asked["id"]]})
        issue(self.state, asked)
        answer(self.state, asked)
        self.assertEqual(1, len(self.observed()["conventions"]))
        frozen = copy.deepcopy(self.state["project_memory"])
        for tags in (["task:0"], ["invented"], [{"question": "model prose"}]):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                memory.capture(self.state, {"open_questions": [], "project_conventions": tags})
        self.assertEqual(frozen, self.state["project_memory"])

    def test_unsafe_and_oversized_exact_human_answers_are_omitted_without_rewriting(self):
        for text in (
            "api_key=not-a-real-credential-value",
            "Use /Users/example/private-logger.py",
            "x" * (store.MAX_ANSWER + 1),
        ):
            with self.subTest(text_length=len(text)):
                state = copy.deepcopy(self.state)
                asked = question()
                memory.capture(state, {"open_questions": [asked], "project_conventions": [asked["id"]]})
                issue(state, asked)
                event = answer(state, asked, text)
                self.assertEqual([], self.observed(state)["conventions"])
                self.assertEqual(text, event["text"])
                self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), state["source_log"][-1]["sha256"])

    def test_publication_uses_current_observations_not_configured_unused_routes_or_loaded_conventions(self):
        memory_state = self.state["project_memory"]
        memory_state["input"] = {"status": "loaded", "record": record(), "sha256": "a" * 64, "note": "historical"}
        self.state["settings"]["roles"] = {"never_launched": {"model": "must-not-be-reported"}}
        self.state["stages"] = [
            {
                "stage": "requirements_gather",
                "route_role": "requirements",
                "started_at": "now",
                "engine": "opencode",
                "provider": "kilocode",
                "launch_route": {"model": "first-model"},
            },
            {
                "stage": "requirements_gather",
                "route_role": "requirements",
                "started_at": "later",
                "engine": "opencode",
                "provider": "kilocode",
                "launch_route": {"model": "actual-later-model"},
            },
            {"role": "runner", "started_at": "now", "runner_owned": True, "model": "not-a-model-call"},
            {"role": "queued", "model": "not-launched"},
        ]
        found = self.observed()
        self.assertEqual("unittest", found["framework"])
        self.assertEqual("python3 -m unittest discover -v", found["test_command"])
        self.assertEqual([], found["conventions"])
        self.assertEqual(
            [{"role": "requirements", "engine": "opencode", "provider": "kilocode", "model": "actual-later-model"}],
            found["routes"],
        )
        self.assertNotIn("must-not-be-reported", json.dumps(found))
        self.assertEqual(record(), memory_state["input"]["record"])

    def test_ordinary_features_learn_framework_from_successful_owned_replay_without_regression_proof(self):
        for command, name in (
            ("python3 -m unittest discover", "unittest"),
            ("python3 -m pytest -q tests", "pytest"),
            ("go test ./...", "go"),
            ("node --test tests/example.js", "node"),
        ):
            with self.subTest(framework=name):
                state = copy.deepcopy(self.state)
                state.pop("regression_proof")
                state["settings"] = {
                    "regression": {},
                    "protected_tests": {"command": command, "binding_hash": "b" * 64},
                }
                state["validation"] = {"check_replay": {"verdict": "PASS", "checks": [replay_check(command)]}}
                before = copy.deepcopy(state)
                with patch.object(memory.command_receipt, "completed", return_value=True):
                    found = self.observed(state)
                self.assertEqual((name, command), (found["framework"], found["test_command"]))
                self.assertEqual(before["settings"], state["settings"])
                self.assertEqual(before["validation"], state["validation"])
                self.assertEqual([], state["acceptance_criteria"])

    def test_unknown_failed_simulated_unowned_and_mixed_replays_do_not_supply_a_framework(self):
        command = "python3 -m unittest discover"
        good = replay_check(command)
        unowned = dict(good)
        unowned.pop("supervision")
        replays = [
            {"verdict": "PASS", "checks": [replay_check("python3 custom_tests.py")]},
            {"verdict": "PASS", "checks": [replay_check(command + "; echo another command")]},
            {"verdict": "FAIL", "checks": [good]},
            {"verdict": "PASS", "simulated": True, "checks": [good]},
            *(
                {"verdict": "PASS", "checks": [replay_check(command, **change)]}
                for change in ({"exit_code": 1}, {"exit_code": True}, {"timed_out": True}, {"error": "failed"})
            ),
            {"verdict": "PASS", "checks": [unowned]},
            {"verdict": "PASS", "checks": [good, replay_check("go test ./...")]},
        ]
        for replay in replays:
            with self.subTest(replay=replay):
                state = copy.deepcopy(self.state)
                state.pop("regression_proof")
                state["validation"] = {"check_replay": replay}
                with patch.object(memory.command_receipt, "completed", return_value=True):
                    self.assertIsNone(self.observed(state)["framework"])
        state = copy.deepcopy(self.state)
        state.pop("regression_proof")
        state["validation"] = {"check_replay": {"verdict": "PASS", "checks": [good]}}
        with patch.object(memory.command_receipt, "completed", return_value=False):
            self.assertIsNone(self.observed(state)["framework"])

    def test_existing_default_discovery_is_called_once_and_bound_before_portable_publication(self):
        state = copy.deepcopy(self.state)
        state.pop("regression_proof")
        state["settings"] = {"regression": {}}
        python = "/private/runtime/bin/python"
        command = shlex.join([python, "-m", "unittest", "discover", "-v"])
        framework = Framework("unittest", command, python=python)

        def reconcile(state, settings, args, workspace, run_dir, **callbacks):
            self.assertNotIn("detected_suite", state["project_memory"])
            selected = callbacks["discover_command"]()
            return {**settings, "protected_tests": {"command": selected, "binding_hash": "b" * 64}}

        with (
            patch.object(run_setup.verify, "detect_framework", return_value=framework) as detect,
            patch.object(run_setup.verify, "python_for", return_value=python),
            patch.object(run_setup.protected_oracles, "reconcile", side_effect=reconcile),
        ):
            state["settings"] = run_setup._protected_test_settings(
                state, state["settings"], SimpleNamespace(run_dir=None), self.root, self.path.parent
            )
        detect.assert_called_once_with(self.root, python=python)
        self.assertEqual(
            {"framework": {"name": "unittest", "python": python, "suite": command}, "binding_hash": "b" * 64},
            state["project_memory"]["detected_suite"],
        )
        state["validation"] = {"check_replay": {"verdict": "PASS", "checks": [replay_check(command)]}}
        with patch.object(memory.command_receipt, "completed", return_value=True):
            found = self.observed(state)
        self.assertEqual(("unittest", "python3 -m unittest discover -v"), (found["framework"], found["test_command"]))
        self.assertEqual([], state["acceptance_criteria"])
        self.assertEqual({}, state["answers"])
        self.assertNotIn("/private", json.dumps(found))
        for change in ({"binding_hash": "c" * 64}, {"command": "/private/custom/test-runner"}):
            drifted = copy.deepcopy(state)
            drifted["settings"]["protected_tests"].update(change)
            with patch.object(memory.command_receipt, "completed", return_value=True):
                self.assertIsNone(self.observed(drifted)["test_command"])

    def test_bound_non_python_discovery_remains_a_known_fact_without_new_project_reads(self):
        for name, command in (
            ("vitest", "npx --no-install vitest run"),
            ("junit", "mvn -o test"),
            ("rspec", "bundle exec rspec"),
        ):
            with self.subTest(framework=name):
                state = copy.deepcopy(self.state)
                state.pop("regression_proof")
                protected = {"command": command, "binding_hash": "b" * 64}
                state["settings"] = {"regression": {}, "protected_tests": protected}
                memory.capture_suite(state, {"name": name, "python": None, "suite": command}, protected)
                with (
                    patch.object(
                        memory, "_observed_framework", side_effect=AssertionError("known framework was reparsed")
                    ),
                    patch.object(memory.Path, "read_text", side_effect=AssertionError("added project read")),
                ):
                    found = self.observed(state)
                self.assertEqual((name, command), (found["framework"], found["test_command"]))

    def test_explicit_feature_suite_wins_and_does_not_invent_default_discovery(self):
        state = copy.deepcopy(self.state)
        state.pop("regression_proof")
        configured = "python3 -m unittest discover -s tests -t ."
        state["settings"] = {"regression": {"test_command": configured}}

        def reconcile(state, settings, args, workspace, run_dir, **callbacks):
            selected = settings["regression"].get("test_command") or callbacks["discover_command"]()
            return {**settings, "protected_tests": {"command": selected, "binding_hash": "b" * 64}}

        with (
            patch.object(run_setup.verify, "detect_framework", side_effect=AssertionError("added discovery")) as detect,
            patch.object(run_setup.protected_oracles, "reconcile", side_effect=reconcile),
        ):
            state["settings"] = run_setup._protected_test_settings(
                state, state["settings"], SimpleNamespace(run_dir=None), self.root, self.path.parent
            )
        detect.assert_not_called()
        self.assertNotIn("detected_suite", state["project_memory"])
        state["settings"]["protected_tests"]["command"] = "python3 -m unittest test_old.py"
        state["validation"] = {"check_replay": {"verdict": "PASS", "checks": [replay_check(configured)]}}
        with patch.object(memory.command_receipt, "completed", return_value=True):
            found = self.observed(state)
        self.assertEqual(("unittest", configured), (found["framework"], found["test_command"]))

    def test_saved_or_legacy_runs_cannot_capture_an_invented_discovery_observation(self):
        for legacy in (False, True):
            state = copy.deepcopy(self.state)
            if legacy:
                state.pop("project_memory")
            framework = Framework("unittest", "python3 -m unittest discover -v", python="python3")

            def reconcile(state, settings, args, workspace, run_dir, **callbacks):
                # Even an incorrectly invoked saved-run callback cannot publish
                # a fresh launch observation; real reconcile never calls it.
                command = callbacks["discover_command"]()
                return {**settings, "protected_tests": {"command": command, "binding_hash": "b" * 64}}

            with (
                patch.object(run_setup.verify, "detect_framework", return_value=framework),
                patch.object(run_setup.verify, "python_for", return_value="python3"),
                patch.object(run_setup.protected_oracles, "reconcile", side_effect=reconcile),
            ):
                run_setup._protected_test_settings(
                    state,
                    state["settings"],
                    SimpleNamespace(run_dir=None if legacy else str(self.path.parent)),
                    self.root,
                    self.path.parent,
                )
            self.assertNotIn("detected_suite", state.get("project_memory", {}))

    def test_portable_configured_command_wins_over_normalized_absolute_proof_command(self):
        portable = "python3 -m unittest discover -s tests -t ."
        observed = "/private/runtime/python -m unittest discover -s tests -t ."
        self.state["settings"]["regression"]["test_command"] = portable
        self.state["regression_proof"] = {
            "framework": {"name": "unittest", "python": "/private/runtime/python", "suite": observed},
            "commands": {"suite": observed, "suite_source": "explicit"},
        }
        before = copy.deepcopy(self.state)
        found = self.observed()
        self.assertEqual(("unittest", portable), (found["framework"], found["test_command"]))
        self.assertEqual(before["settings"], self.state["settings"])
        self.assertEqual(before["regression_proof"], self.state["regression_proof"])
        self.assertEqual({}, self.state["answers"])
        self.assertEqual([], self.state["acceptance_criteria"])
        self.assertNotIn("goal_contract", self.state)

    def test_recorded_generated_python_suites_get_only_a_portable_interpreter(self):
        for name, arguments in (
            ("unittest", ["discover", "-s", "tests", "-t", ".", "-v"]),
            ("pytest", ["-q", "-p", "no:cacheprovider", "tests with spaces"]),
        ):
            with self.subTest(framework=name):
                state = copy.deepcopy(self.state)
                state["settings"]["regression"] = {}
                python = "/private/runtime with spaces/bin/python"
                command = shlex.join([python, "-m", name, *arguments])
                framework = {"name": name, "python": python, "suite": command}
                state["regression_proof"] = {
                    "framework": framework,
                    "execution_context": {"detected_framework": copy.deepcopy(framework)},
                    "commands": {"suite": command, "suite_source": "detected:" + name},
                }
                found = self.observed(state)
                self.assertEqual(name, found["framework"])
                self.assertEqual(["python3", "-m", name, *arguments], shlex.split(found["test_command"]))
                self.assertNotIn("/private", json.dumps(found))
                self.assertEqual({}, state["answers"])
                self.assertEqual([], state["acceptance_criteria"])
                self.assertNotIn("goal_contract", state)

    def test_custom_absolute_mismatched_and_shell_suites_are_not_rewritten_into_defaults(self):
        python = "/private/runtime/python"
        for command, source, overrides in (
            ("/private/custom/test-runner tests", "detected:unittest", {}),
            (python + " custom_tests.py", "detected:unittest", {}),
            ("python3 custom_tests.py", "detected:unittest", {}),
            ("/different/python -m unittest discover", "detected:unittest", {}),
            (python + " -m pytest", "detected:unittest", {}),
            (python + " -m unittest discover;echo done", "detected:unittest", {}),
            (python + " -m unittest discover && echo done", "detected:unittest", {}),
            (python + " -m unittest discover | tee output", "detected:unittest", {}),
            (python + " -m unittest discover > output", "detected:unittest", {}),
            (python + " -m unittest ${TEST_TARGET}", "detected:unittest", {}),
            (python + " -m unittest `echo tests`", "detected:unittest", {}),
            (python + " -m unittest discover /private/tests", "detected:unittest", {}),
            (python + " -m unittest discover", "builder", {}),
            (python + " -m unittest discover", "detected:unittest", {"suite": "a different recorded suite"}),
        ):
            with self.subTest(command=command, source=source):
                state = copy.deepcopy(self.state)
                state["settings"]["regression"] = {}
                state["regression_proof"] = {
                    "framework": {"name": "unittest", "python": python, "suite": command, **overrides},
                    "commands": {"suite": command, "suite_source": source},
                }
                found = self.observed(state)
                self.assertEqual("unittest", found["framework"])
                self.assertIsNone(found["test_command"])
                self.assertEqual({}, state["answers"])
                self.assertEqual([], state["acceptance_criteria"])
                self.assertNotIn("goal_contract", state)

    def test_success_is_idempotent_and_failure_is_advisory_retryable_and_secret_free(self):
        before = {key: copy.deepcopy(value) for key, value in self.state.items() if key != "project_memory"}
        with patch.object(store, "write", side_effect=OSError("api_key=must-never-be-in-diagnostics")) as write:
            self.assertTrue(memory.publish(self.path, self.state))
            self.assertFalse(memory.publish(self.path, self.state))
            self.assertEqual(2, write.call_count)
        self.assertEqual("TASK_COMPLETE", self.state["status"])
        self.assertNotIn("api_key", json.dumps(self.state["project_memory"]["publication"]))
        with patch.object(store, "write", wraps=store.write) as write:
            self.assertTrue(memory.publish(self.path, self.state))
            first = (self.root / ".autocode/project.json").stat().st_mtime_ns
            self.assertFalse(memory.publish(self.path, self.state))
            self.assertEqual(1, write.call_count)
            self.assertEqual(first, (self.root / ".autocode/project.json").stat().st_mtime_ns)
        self.assertEqual(before, {key: value for key, value in self.state.items() if key != "project_memory"})

    def test_only_successful_parent_runs_publish_to_the_original_shared_project(self):
        for changed in (
            {"status": "RUNNING"},
            {"status": "PAUSED_COST_LIMIT"},
            {"parent_run": "other"},
            {"active_stage": {"stage": "terra"}},
            {"uncertain_artifacts": [{"output": "uncertain"}]},
        ):
            with self.subTest(changed=changed), patch.object(store, "write") as write:
                self.assertFalse(memory.publish(self.path, {**self.state, **changed}))
                write.assert_not_called()
        shared = self.root / "shared-project"
        shared.mkdir()
        self.state["project_workspace"] = str(shared)
        with patch.object(Path, "glob", side_effect=AssertionError("another run was scanned")):
            self.assertTrue(memory.publish(self.path, self.state))
        self.assertTrue((shared / ".autocode/project.json").is_file())
        self.assertFalse((self.root / ".autocode/project.json").exists())
        self.assertFalse(memory.publish(self.path, {"task": "legacy", "status": "TASK_COMPLETE"}))

    def test_v2_report_tags_survive_acceptance_without_becoming_contract_or_approval(self):
        value = body()
        for key in goals.BRIEF_FIELDS:
            value.pop(key)
        asked = question()
        value["open_blocking_questions"] = [asked]
        self.state.update(task_id="task", settings={"joint_planning": True, "planning_flow": "v2"}, status="RUNNING")
        goals_lifecycle.apply_requirements_report(
            self.state, {"requirements": value, "project_conventions": [asked["id"]]}, artifact_sha256="a" * 64
        )
        self.assertNotIn("goal_contract", self.state)
        self.assertEqual([], self.state["acceptance_criteria"])
        self.assertEqual([asked], self.state["project_memory"]["tagged_questions"])
        issue(self.state, asked)
        answer(self.state, asked)
        self.state["status"] = "TASK_COMPLETE"
        self.assertEqual(1, len(self.observed()["conventions"]))
        frozen = copy.deepcopy(self.state["project_memory"])
        with self.assertRaises(ValueError):
            goals_lifecycle.apply_requirements_report(
                self.state,
                {"requirements": {**value, "acceptance_criteria": []}, "project_conventions": [asked["id"]]},
                artifact_sha256="invalid",
            )
        self.assertEqual(frozen, self.state["project_memory"])

    def test_tracked_project_memory_changes_neither_source_revision_nor_evidence_binding(self):
        (self.root / "app.py").write_text("VALUE = 1\n")
        tracked = b"app.py\0.autocode/project.json\0"

        def git_output(argv, **kwargs):
            return tracked if "ls-files" in argv else "a" * 40 + "\n"

        with patch.object(util.subprocess, "check_output", side_effect=git_output):
            source_before = util.snapshot(self.root)
            evidence_before = evidence_export.binding(self.state, {})
            self.assertTrue(memory.publish(self.path, self.state))
            self.assertTrue((self.root / ".autocode/project.json").is_file())
            self.assertEqual(source_before, util.snapshot(self.root))
            self.assertEqual(evidence_before, evidence_export.binding(self.state, {}))
        self.assertEqual({"app.py"}, set(source_before["files"]))

    def test_status_publishes_after_durable_checkpoint_and_evidence_then_saves_diagnostics(self):
        calls = []
        real_publish = memory.publish

        def evidence(run_dir, state, view, provenance):
            self.assertEqual("TASK_COMPLETE", util.read(self.path)["status"])
            calls.append("evidence")
            return False

        def publish(path, state):
            self.assertEqual(["evidence"], calls)
            self.assertNotIn("publication", util.read(self.path)["project_memory"])
            calls.append("memory")
            return real_publish(path, state)

        with (
            patch.object(code_checkpoints, "update"),
            patch.object(checkpoints, "update"),
            patch.object(status, "record", return_value=None),
            patch.object(activity_log, "record"),
            patch.object(usage, "record"),
            patch.object(run_view, "view", return_value={"status": "TASK_COMPLETE"}),
            patch.object(evidence_export, "publish", side_effect=evidence),
            patch.object(controller_owner, "checkpoint"),
            patch.object(memory, "publish", side_effect=publish),
            redirect_stderr(io.StringIO()),
        ):
            status.persist(self.path, self.state)
        self.assertEqual(["evidence", "memory"], calls)
        self.assertEqual("written", util.read(self.path)["project_memory"]["publication"]["status"])


if __name__ == "__main__":
    unittest.main()
