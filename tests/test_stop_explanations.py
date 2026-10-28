"""Every pause state has a plain-English explanation (#715)."""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

from tests.source_inventory import python_sources

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import autocode_controller_owner as controller_owner
import autocode_run_view as run_view
import autocode_stop_explanations as stop_explanations


def named_pause_states(root: Path = TOOLS) -> set[str]:
    named = set()
    for path in python_sources(root):
        text = path.read_text()
        named.update(re.findall(r"PAUSED_[A-Z_]+", text))
        named.update(
            re.findall(
                r"WAITING_FOR_USER|AWAITING_GOAL_APPROVAL|BLOCKED_HUMAN|RESOLVER_PENDING|WAITING_FOR_DEPENDENCY", text
            )
        )
    # Truncated prefixes (PAUSED_MILESTONE_ etc.) are not states.
    return {status for status in named if not status.endswith("_")}


class StopExplanationTests(unittest.TestCase):
    def test_completed_views_explain_the_saved_outcome_without_recovery_or_freshness_claims(self):
        for status in ("TASK_COMPLETE", "COMPLETE"):
            for current in (None, False, True):
                for note in ("", "retained completion note"):
                    with self.subTest(status=status, completion_current=current, note=note):
                        view = run_view.view({"status": status, "stop_reason": note}, completion_current=current)
                        text = view["explanation"]
                        self.assertEqual(status, view["status"])
                        self.assertIsNone(view["needs"])
                        self.assertIsNone(view["next_command"])
                        self.assertIn(status, text["what_happened"])
                        if note:
                            self.assertIn(note, text["what_happened"])
                        self.assertIn("saved", text["what_it_means"].lower())
                        self.assertIn("current", text["what_it_means"].lower())
                        self.assertIn("--status", text["what_the_command_does"])
                        self.assertIn("follow-up", text["what_the_command_does"])
                        for misleading in ("stopped", "environment", "resume", "wrong deliverable"):
                            self.assertNotIn(misleading, text["explanation"].lower())

    def test_preparation_timeout_names_the_prelaunch_copy_and_preserves_budgets(self):
        text = stop_explanations.explain("PAUSED_VERIFICATION_PREPARATION", stop_reason="source pack timed out")
        self.assertIn("before the model started", text["what_it_means"])
        self.assertIn("existing run budgets", text["what_the_command_does"])
        self.assertIn("--resume-paused", text["what_the_command_does"])

    def test_a_host_that_refuses_provider_sessions_is_explained_as_the_host_not_a_stuck_process(self):
        """A root shell's PAUSED_PROCESS_CLEANUP was explained as a child process that did not clean up, with
        --abandon-stage as the way forward; the fix is the one autocode doctor names."""
        reason = (
            "Provider session boundary unavailable; no provider launched: Provider session ownership refuses "
            "CAP_SYS_PTRACE or CAP_SYS_ADMIN"
        )
        needs = {"kind": "resume", "abandon_stage": "001/requirements-gather-01"}
        text = stop_explanations.explain("PAUSED_PROCESS_CLEANUP", stop_reason=reason, needs=needs)
        self.assertIn("No provider was launched", text["what_it_means"])
        self.assertIn("root in most containers", text["what_it_means"])
        self.assertIn("setpriv --bounding-set=-sys_admin,-sys_ptrace autocode", text["what_the_command_does"])
        # A plain --resume-paused holds on the uncertain attempt (the never-launched one), so name both steps.
        self.assertIn("--abandon-stage 001/requirements-gather-01, then --resume-paused", text["what_the_command_does"])
        text = stop_explanations.explain("PAUSED_PROCESS_CLEANUP", stop_reason=reason)
        self.assertIn("--abandon-stage ATTEMPT (the status names it)", text["what_the_command_does"])
        unsupported = "Provider session boundary unavailable; no provider launched: Provider session ownership requires Linux native x86_64 or aarch64"
        text = stop_explanations.explain("PAUSED_PROCESS_CLEANUP", stop_reason=unsupported)
        self.assertIn("supported host", text["what_the_command_does"])
        # A real cleanup pause keeps the table's text.
        text = stop_explanations.explain("PAUSED_PROCESS_CLEANUP", stop_reason="child 4242 still running")
        self.assertEqual(stop_explanations.TABLE["PAUSED_PROCESS_CLEANUP"][0], text["what_it_means"])

    def test_a_private_store_containment_stop_is_not_explained_as_a_missing_sandbox(self):
        # #1250: these reasons come from autocode_tool_containment (tests/test_tool_containment.py
        # checks the real ones); every other PAUSED_TOOL_CONTAINMENT reason keeps the table's text.
        table = stop_explanations.TABLE["PAUSED_TOOL_CONTAINMENT"]
        appeared = (
            "Native tool boundary changed before launch: Private run-state read denial changed; no provider "
            "launched: the private .autocode store /w/.autocode/issues was made after this launch's boundary was "
            "prepared, so that boundary does not deny reading it. A plain --resume-paused prepares the boundary "
            "again with it denied"
        )
        symlinked = (
            "Native tool boundary was not established: Planning tool containment cannot deny reading the private "
            ".autocode store /w/.autocode/issues, which is a symlink; replace it with a real directory, or remove "
            "it, then --resume-paused"
        )
        for reason, means, does in (
            (appeared, "appeared after this planning launch's tool boundary was prepared", "--resume-paused prepares"),
            (symlinked, "is a symlink or not a directory", "Replace the path the stop reason names"),
        ):
            with self.subTest(reason=reason):
                text = stop_explanations.explain("PAUSED_TOOL_CONTAINMENT", stop_reason=reason)
                self.assertIn(means, text["what_it_means"])
                self.assertTrue(text["what_the_command_does"].startswith(does), text)
                self.assertNotIn(table[0], text["explanation"])
                self.assertNotIn(table[1], text["explanation"])
        for reason in (
            "",
            "Native tool boundary was not established: strict tool containment requires macOS sandbox-exec or "
            "Linux bubblewrap; this machine is linux. Restore the qualified native setup; native role isolation "
            "requires its kernel tool boundary",
            "Native tool boundary changed before launch: Private run-state read denial changed; no provider launched",
        ):
            with self.subTest(reason=reason):
                text = stop_explanations.explain("PAUSED_TOOL_CONTAINMENT", stop_reason=reason)
                self.assertEqual(table, (text["what_it_means"], text["what_the_command_does"]))
        # Another status is never given the store's explanation.
        self.assertEqual(
            stop_explanations.TABLE["PAUSED_TOOL"][0],
            stop_explanations.explain("PAUSED_TOOL", stop_reason=appeared)["what_it_means"],
        )

    def test_every_pause_state_in_the_table_has_three_parts(self):
        self.assertGreater(len(stop_explanations.TABLE), 50)
        for status, entry in stop_explanations.TABLE.items():
            with self.subTest(status=status):
                means, does = entry
                self.assertTrue(means.strip(), status)
                self.assertTrue(does.strip(), status)
                self.assertGreater(len(means), 20, status)
                self.assertGreater(len(does), 20, status)

    def test_every_pause_state_named_in_tools_has_an_explanation(self):
        # The issue's bar: a state without an explanation fails this test.
        named = named_pause_states()
        missing = sorted(named - set(stop_explanations.TABLE))
        self.assertEqual([], missing, f"pause states without an explanation: {missing}")

    def test_nested_runtime_pause_is_required_but_fixture_pauses_are_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "units").mkdir()
            (root / "units" / "planner.py").write_text(
                '"PAUSED_SENTINEL" "WAITING_FOR_USER" "PAUSED_TIME_LIMIT" "PAUSED_PREFIX_"'
            )
            (root / "tests").mkdir()
            (root / "tests" / "test_fixture.py").write_text('"PAUSED_FIXTURE_ONLY"')
            named = named_pause_states(root)
            self.assertEqual({"PAUSED_SENTINEL", "WAITING_FOR_USER", "PAUSED_TIME_LIMIT"}, named)
            self.assertEqual({"PAUSED_SENTINEL"}, named - set(stop_explanations.TABLE))

    def test_test_only_pause_states_are_excluded_from_runtime_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text('status = "PAUSED_BUDGET"\n')
            (root / "tests").mkdir()
            (root / "tests" / "test_runtime.py").write_text('status = "PAUSED_TEST"\nstatus = "PAUSED_TEST_DISPATCH"\n')
            self.assertEqual({"PAUSED_BUDGET"}, named_pause_states(root))

    def test_unknown_runtime_pause_still_fails_the_table_comparison(self):
        unknown = "PAUSED_UNKNOWN_RUNTIME_SCAN_FIXTURE"
        self.assertNotIn(unknown, stop_explanations.TABLE)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime.py").write_text(f'status = "{unknown}"\n')
            missing = sorted(named_pause_states(root) - set(stop_explanations.TABLE))
            self.assertEqual([unknown], missing)

    def test_explain_is_three_short_paragraphs_for_a_person(self):
        text = stop_explanations.explain("PAUSED_BUDGET", stop_reason="iteration ceiling")
        self.assertIn("PAUSED_BUDGET", text["what_happened"])
        self.assertIn("iteration ceiling", text["what_happened"])
        self.assertIn("budget", text["what_it_means"].lower())
        self.assertIn("--resume-paused", text["what_the_command_does"])
        self.assertIn(text["what_it_means"], text["explanation"])

    def test_the_view_carries_the_explanation_never_renamed(self):
        state = {
            "status": "WAITING_FOR_USER",
            "stop_reason": "a question is open",
            "settings": {"roles": {}},
            "acceptance_criteria": [],
        }
        view = run_view.view(state)
        self.assertIn("explanation", view)
        self.assertIn(
            "person", view["explanation"]["what_happened"].lower() + view["explanation"]["what_it_means"].lower()
        )
        self.assertTrue(view["explanation"]["what_the_command_does"])

    def test_a_supervised_running_view_explains_ongoing_work_without_resume(self):
        supervision = {
            "schema": 1,
            "nonce": "a" * 32,
            "receipt": "/run/requirements-supervision.json",
            "owner": {"pid": 101, "birth_identity": 123.25},
            "keeper": {"pid": 102, "birth_identity": 124.25},
            "provider": {"pid": 103, "birth_identity": 125.25},
        }
        inspection = {
            **{role: {"checked": True, "alive": True} for role in ("owner", "keeper", "provider")},
            "receipt": {
                **supervision,
                "phase": "armed",
                "cause": None,
                "cleanup_error": None,
                "observed_at": "2026-10-15T01:00:00Z",
                "processes": [supervision["provider"]],
            },
        }
        state = {
            "status": "RUNNING",
            "phase": "PLANNING",
            "settings": {"roles": {}},
            "acceptance_criteria": [],
            "active_stage": {"stage": "astra_discovery", "supervision": supervision},
        }
        view = run_view.view(state, liveness=inspection, controller={"kind": "running"})
        self.assertEqual("RUNNING", view["status"])
        self.assertEqual("PLANNING", view["phase"])
        self.assertEqual({"kind": "continue"}, view["needs"])
        self.assertEqual("supervised", view["liveness"]["kind"])
        self.assertFalse(view["done"])
        text = view["explanation"]
        self.assertIn("RUNNING", text["what_happened"])
        self.assertNotIn("stopped", text["explanation"].lower())
        self.assertNotIn("resume", text["explanation"].lower())
        self.assertIn("in progress", text["what_it_means"])
        self.assertIn("--status", text["what_the_command_does"])

    def test_paused_and_unknown_views_keep_their_stop_and_resume_guidance(self):
        for status in ("PAUSED_BUDGET", "PAUSED_NOT_A_REAL_STATE"):
            with self.subTest(status=status):
                view = run_view.view({"status": status, "stop_reason": "retained stop reason"})
                self.assertEqual(status, view["status"])
                self.assertEqual("resume", view["needs"]["kind"])
                self.assertFalse(view["done"])
                text = view["explanation"]
                self.assertIn(status, text["what_happened"])
                self.assertIn("stopped", text["what_happened"])
                self.assertIn("retained stop reason", text["what_happened"])
                self.assertIn("resume", text["what_the_command_does"])

    def test_ended_or_unknown_controller_still_requires_explicit_resume(self):
        for kind, status, reason in (
            ("ended", "PAUSED_CONTROLLER_EXIT", "Controller ended"),
            ("unknown", "PAUSED_PROCESS_CHECK", "ownership inspection unavailable"),
        ):
            with self.subTest(controller=kind):
                saved = {"status": "RUNNING", "phase": "PLANNING"}
                controller = {"kind": kind, "reason": reason}
                view = run_view.view(controller_owner.project(saved, controller), controller=controller)
                self.assertEqual("RUNNING", saved["status"])
                self.assertEqual("PLANNING", saved["phase"])
                self.assertEqual(status, view["status"])
                self.assertEqual("resume", view["needs"]["kind"])
                self.assertEqual(kind, view["controller"]["kind"])
                self.assertEqual("unknown", view["liveness"]["kind"])
                self.assertFalse(view["done"])
                self.assertIn("stopped", view["explanation"]["what_happened"])
                self.assertIn(reason, view["explanation"]["what_happened"])
                self.assertIn("--resume-paused", view["explanation"]["what_the_command_does"])

    def test_an_unknown_status_still_explains(self):
        text = stop_explanations.explain("PAUSED_NOT_A_REAL_STATE")
        self.assertIn("PAUSED_NOT_A_REAL_STATE", text["what_happened"])
        self.assertTrue(text["what_it_means"])
        self.assertTrue(text["what_the_command_does"])

    def test_missing_planning_route_explains_the_saved_route_refusal(self):
        text = stop_explanations.explain("PAUSED_PLANNING_ROUTE")
        self.assertIn("no saved route", text["what_it_means"])
        self.assertIn("re-checks the saved routes", text["what_the_command_does"])
        self.assertIn("--joint-planning --planning-v2", text["what_the_command_does"])


if __name__ == "__main__":
    unittest.main()
