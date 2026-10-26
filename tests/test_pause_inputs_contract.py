"""Every number a pause leaves to a person is one a client can fill (#2044).

A pause's named command may leave an ``N`` for a person: a new total for the bound that stopped the run,
a plan-review allowance, a recovery grant. Before #2044 only three of those were described in
``needs.command_inputs``; the cost, iteration, milestone and no-progress pauses named an ``N`` no client
may fill, and once AutoResolver held a bound pause its raise survived only in prose (#1950). These tests
hold every pause status to the rule through the shared client's public functions, so a new bound, a
renamed flag or a client table that drifts from the core fails here.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode as runner
import autocode_args
import autocode_client_actions as actions
import autocode_command_inputs as command_inputs
import autocode_configure
import autocode_run_view as run_view
import autocode_support as support

from dashboard import dashboard_recovery
from scenarios.harness import conformance, needs_command
from tests import test_needs_contract
from tests.test_stop_explanations import named_pause_states

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = Path("/work/project")
RUN = WORKSPACE / ".autocode" / "runs" / "r1"
_SEALED = {"task_id": "t", "revision": 1, "body": {"milestones": [{"id": "M1"}]}}
CONTRACT = {**_SEALED, "hash": support.digest(_SEALED)}
KEY = f"{CONTRACT['hash']}:M1"  # autocode_milestone_scope.key for the current task


def paused(status, **more):
    """A saved run stopped at ``status`` with every count a bound pause reads."""
    state = {
        "version": 2,
        "status": status,
        "stop_reason": "Repeated unchanged implementation batches require review"
        if status == "PAUSED_NO_PROGRESS"
        else "Saved limit reached",
        "workspace": str(WORKSPACE),
        "run_dir": str(RUN),
        "task_id": "t",
        "task": "Make a greeting tool",
        "stages": [],
        "history": [],
        "iteration": 6,
        "next_stage": "astra_challenge" if status == "PAUSED_PLANNING_BUDGET" else "terra",
        "phase": "EXECUTING",
        "active_seconds": 2800.5,
        "no_progress_batches": 3,
        "automatic_recoveries_since_resume": 3,
        "consecutive_timeout_recoveries": 3,
        "automatic_timeout_recoveries": [{"stage": "terra"}],
        "recovery_grants": [],
        "planning": {"astra_calls": 2, "review_call_limit": 2},
        "goal_contract": CONTRACT,
        "current_task": {"id": "T1", "milestone_id": "M1", "contract_hash": CONTRACT["hash"]},
        "milestone_progress": {KEY: {"seconds": 5500, "replans": 1}},
        "settings": {
            "limits": {"max_seconds": 2700, "max_cost_usd": 2.0, "iteration_ceiling": 5, "no_progress_batches": 3},
            "budget_origins": {key: "user_explicit" for key in ("max_seconds", "max_cost_usd", "iteration_ceiling")},
            "roles": {},
            "milestone_checkpoints": {"enabled": True, "max_seconds": 5400, "max_replans": 1, "stalled_reviews": 3},
        },
        "resolver": {},
    }
    state.update(more)
    return state


def offered(view):
    """Each command the view names with the rows bound to it: next_command, then a separate bound raise."""
    found = []
    if view.get("next_command") and not view["next_command"].startswith("autocode wait"):
        command = actions.named(view, WORKSPACE, RUN)
        found.append((command, actions.integer_inputs(view, command)))
    if offer := actions.bound_offer(view, WORKSPACE, RUN):
        found.append((offer[1], [offer[0]]))
    return found


def parses(command):
    with mock.patch.dict("os.environ", command.env):
        args, _ = autocode_args.parse(
            None, ["--run-dir", str(RUN), *command.argv], autocode_configure.DEFAULT_ROLE_MODELS
        )
    return args


def person_numbers(argv):
    return [
        index
        for index, word in enumerate(argv[1:], 1)
        if word == "N" and argv[index - 1] in command_inputs.PERSON_FLAGS
    ]


class EveryPauseNumberIsDescribed(unittest.TestCase):
    def check(self, view, label):
        """Every person's N in every named command has a row at its index; filled at its minimum it parses."""
        for command, rows in offered(view):
            with self.subTest(pause=label, argv=command.argv):
                slots = person_numbers(command.argv)
                self.assertEqual(slots, [row["arg_index"] for row in rows], f"{label}: an N no client can fill")
                if rows and "command" not in rows[0]:
                    self.assertIsNone(actions.unsupplied(view, command))
                if not rows:
                    continue
                row = rows[0]
                if "command" in row:
                    filled = actions.fill_offer(view, row, command, str(row["minimum"]))
                else:
                    filled = actions.fill(view, command, {row["id"]: str(row["minimum"])})
                args = parses(filled)
                self.assertEqual(view["recovery"]["token"], args.expected_recovery_token)
                # The browser dashboard types the number into the same command, with the token moved back
                # into argv for its launcher's private channel (dashboard_recovery.number_command).
                extra, taken = dashboard_recovery.number_command(view, WORKSPACE, RUN, row["id"], str(row["minimum"]))
                self.assertEqual(row, taken)
                token_at = filled.argv.index("--expected-recovery-token") + 1
                self.assertEqual(["--no-chat", *filled.argv[:token_at], view["recovery"]["token"]], extra)
                self.assertEqual(row["minimum"], round(getattr(args, row["flag"][2:].replace("-", "_"))))
                with self.assertRaisesRegex(ValueError, "whole number"):
                    dashboard_recovery.number_command(view, WORKSPACE, RUN, row["id"], str(row["minimum"] - 1))
                with self.assertRaises(actions.ClientGap):  # one below the minimum grants nothing
                    if "command" in row:
                        actions.fill_offer(view, row, command, str(row["minimum"] - 1))
                    else:
                        actions.fill(view, command, {row["id"]: str(row["minimum"] - 1)})
                # The headless conformance client takes exactly what the shared client does.
                if "command" not in row:
                    driver = object.__new__(conformance.ConformanceDriver)
                    words = driver.decide(
                        view["needs"],
                        list(command.argv),
                        command_inputs={row["id"]: str(row["minimum"])},
                        view=view,
                        env=command.env,
                    )
                    self.assertEqual(filled.argv, words)

    def test_every_pause_status_describes_each_number_it_names(self):
        described = set()
        for status in sorted(named_pause_states()):
            view = run_view.view(paused(status), run_dir=RUN, workspace=WORKSPACE)
            self.check(view, status)
            described.update(row["flag"] for row in (view["needs"] or {}).get("command_inputs") or [])
        self.assertEqual(set(command_inputs.PERSON_FLAGS), described, "a described number no pause reached")

    def test_each_bound_pause_offers_its_own_raise(self):
        for status, (identity, flag, *_rest) in command_inputs.BOUNDS.items():
            view = run_view.view(paused(status), run_dir=RUN, workspace=WORKSPACE)
            with self.subTest(status=status):
                rows = view["needs"].get("command_inputs") or []
                self.assertEqual([identity], [row["id"] for row in rows])
                self.assertIn(f"--resume-paused {flag} N --expected-recovery-token -", view["next_command"])

    def test_a_bound_held_by_autoresolver_still_offers_its_raise(self):
        # #1950's shape: after AutoResolver's operational request, next_command is a plain resume.
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            for status, (identity, flag, *_rest) in command_inputs.BOUNDS.items():
                state = paused(status, run_dir=str(run_dir))
                error = support.Paused(status, state["stop_reason"])
                self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, state, run_dir, error))
                view = run_view.view(state, run_dir=RUN, workspace=WORKSPACE)
                with self.subTest(status=status, held=view["status"]):
                    self.assertNotIn(" N", view["next_command"] or "")
                    row, command = actions.bound_offer(view, WORKSPACE, RUN)
                    self.assertEqual((identity, flag), (row["id"], row["flag"]))
                    self.assertEqual(["--resume-paused", flag, "N", "--expected-recovery-token", "-"], command.argv)
                    self.check(view, status)

    def test_minimum_exceeds_both_the_saved_bound_and_what_it_counted(self):
        cases = {
            "PAUSED_COST_LIMIT": 3,  # $2.00 cap, nothing priced yet
            "PAUSED_ITERATION_LIMIT": 7,  # ceiling 5, iteration 6
            "PAUSED_MILESTONE_BUDGET": 5501,  # 5400 s budget, 5500 s counted
            "PAUSED_MILESTONE_STALLED": 2,  # one replan allowed and used
            "PAUSED_NO_PROGRESS": 4,  # limit 3, three unchanged batches
        }
        for status, minimum in cases.items():
            with self.subTest(status=status):
                view = run_view.view(paused(status), run_dir=RUN, workspace=WORKSPACE)
                self.assertEqual(minimum, view["needs"]["command_inputs"][0]["minimum"])

    def test_no_number_is_offered_where_the_bound_is_not_what_holds_the_run(self):
        for status, changes in (
            ("PAUSED_COST_LIMIT", {"settings": {"limits": {}}}),  # no cap: not the cost bound
            ("PAUSED_NO_PROGRESS", {"stop_reason": "Owned workers must be reconciled"}),
            ("PAUSED_ITERATION_LIMIT", {"active_stage": {"stage": "terra"}}),
            ("PAUSED_ITERATION_LIMIT", {"applied_interventions": [{"kind": "stop"}]}),
        ):
            with self.subTest(status=status, changes=changes):
                view = run_view.view(paused(status, **changes), run_dir=RUN, workspace=WORKSPACE)
                self.assertNotIn("command_inputs", view["needs"] or {})
        state = paused("PAUSED_ITERATION_LIMIT")
        for action in ("--resume-paused --retry-failed-stage", "--feedback 'WHAT TO CHANGE'", "--explain"):
            self.assertIsNone(command_inputs.project(state, {"kind": "resume", "action": action}, None))
        self.assertIsNone(command_inputs.project(state, {"kind": "resume", "route": {"question_id": "q"}}, None))


class ClientTablesMatchTheCore(unittest.TestCase):
    def test_every_client_knows_exactly_the_numbers_the_core_describes(self):
        self.assertEqual(set(command_inputs.PERSON_FLAGS), set(actions.INTEGER_INPUTS))
        self.assertEqual(actions.INTEGER_INPUTS, conformance.INTEGER_INPUTS)
        for identity, flag, label, used, _read in command_inputs.BOUNDS.values():
            self.assertEqual((identity, label, used), actions.INTEGER_INPUTS[flag][:3])
        typescript = (ROOT / "vscode-extension/src/core/next-command.ts").read_text()
        for flag, (identity, label, used, fractional) in actions.INTEGER_INPUTS.items():
            row = f'"{flag}": ["{identity}", "{label}", "{used}", {str(fractional).lower()}]'
            self.assertIn(row, typescript, "vscode-extension's INTEGER_INPUTS drifted from the core")

    def test_the_vscode_panel_names_every_bound_it_may_raise(self):
        # Its confirmation says which limit a typed number changes; an unnamed bound would fall back to the label.
        panel = (ROOT / "vscode-extension/src/panel.ts").read_text()
        for identity, *_rest in command_inputs.BOUNDS.values():
            self.assertRegex(panel, rf"\n  {identity}: \[\(total\) =>", f"panel.ts BOUND_WORDING has no {identity}")

    def test_every_placeholder_filler_fills_every_person_number(self):
        for table in (needs_command._PERSON_INT, test_needs_contract._PERSON_INT):
            self.assertLessEqual(set(command_inputs.PERSON_FLAGS), set(table))

    def test_the_prose_reading_of_1950_is_gone(self):
        source = (ROOT / "tools/autocode_client_actions.py").read_text()
        self.assertIsNone(re.search(r"use autocode resume", source))
        self.assertFalse(hasattr(actions, "unapplied_bound"))


if __name__ == "__main__":
    unittest.main()
