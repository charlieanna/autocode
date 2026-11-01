import copy
import json
import math
import unittest
from pathlib import Path

import autocode_builder_policy as policy
import autocode_cost as cost
import autocode_dispatch as dispatch
import autocode_route_ladder as route_ladder
from providers import claude, command, opencode

from . import test_build_blackbox as bb

# The config of the retired example Claude tool (examples/claude-provider/claude.toml before #717), kept
# here as a representative TOML-registered tool with its own [builder_retry] table.
CLAUDE_TOML_TEXT = r"""name = "claude"
# Runs each stage as one `claude -p` call, through claude_stage.py (installed beside this file).
# Install: copy claude.toml and claude_stage.py to ~/.config/autocode/providers/. See README.md.
command = ["sh", "-c", "exec python3 \"$HOME/.config/autocode/providers/claude_stage.py\" \"$@\"", "claude", "{workspace}", "{sandbox}", "{model}", "{effort}", "{schema}", "{report}"]
prompt = "stdin"
version_command = ["claude", "--version"]
models = ["claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5"]

# Verifiers differ from producers, as AutoCode requires: Haiku builds, Sonnet validates, Opus reviews.
[roles]
astra = { model = "claude-opus-5-5", effort = "medium" }
terra = { model = "claude-haiku-4-5-20251001", effort = "medium" }
sol = { model = "claude-sonnet-5-5", effort = "medium" }
completion = { model = "claude-opus-5-5", effort = "medium" }
glm = { model = "claude-sonnet-5-5", effort = "medium" }
plan_reviewer = { model = "claude-opus-5-5", effort = "medium" }

# After the ordinary retry a failing milestone's Builder moves to strong_model at strong_effort (xhigh when
# unset; `claude -p --model claude-sonnet-5-5 --effort xhigh` is accepted, checked 2026-10-07). A Tester or
# Completion Reviewer on that model moves to checker_model until the next milestone. Copy a changed file to
# ~/.config/autocode/providers/ only between batches: a saved run whose config changed stops with
# PAUSED_TRANSPORT_CHANGED.
[builder_retry]
strong_model = "claude-sonnet-5-5"
checker_model = "claude-opus-5-5"
"""
CLAUDE_TOML = Path("claude.toml")
CLAUDE_CONFIG = command.tomllib.loads(CLAUDE_TOML_TEXT)
CLAUDE_PROVIDER = command.CommandProvider(CLAUDE_CONFIG, CLAUDE_TOML)
# A Claude config written before [builder_retry] existed.
OLDER_CLAUDE_CONFIG = {key: value for key, value in CLAUDE_CONFIG.items() if key != "builder_retry"}
OLDER_CLAUDE_PROVIDER = command.CommandProvider(OLDER_CLAUDE_CONFIG, CLAUDE_TOML)


class PolicyTests(unittest.TestCase):
    def state(self):
        return {
            "settings": {
                "engine": "codex",
                "builder_retry": dict(policy.DEFAULTS),
                "roles": {"terra": {"model": "gpt-6-luna", "reasoning_effort": "medium"}},
            },
            "goal_contract": {"hash": "approved"},
            "current_task": {"id": "task1", "milestone_id": "M1"},
            "status": "RUNNING",
        }

    def test_retry_escalate_pause_is_durable_idempotent_and_bounded(self):
        state = self.state()
        self.assertEqual("retry", policy.failure(state, "e1", "failure"))
        state = json.loads(json.dumps(state))
        self.assertEqual("retry", policy.failure(state, "e1", "failure"))
        self.assertEqual("escalate", policy.failure(state, "e2", "failure"))
        self.assertEqual("gpt-6-sol", state["settings"]["roles"]["terra"]["model"])
        self.assertEqual("pause", policy.failure(state, "e3", "failure"))
        self.assertIn("Execution failure:", state["stop_reason"])
        self.assertIn("human decision or replanning required", state["stop_reason"])
        with self.assertRaises(policy.s.Paused):
            policy.guard(state)
        self.assertEqual(3, len(state["builder_retry_decisions"]))

    def test_automatic_retry_cannot_add_an_unconnected_strong_route(self):
        state = self.state()
        state["settings"]["engine"] = "opencode"
        state["settings"]["roles"]["terra"]["model"] = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
        state["settings"]["route_selection"] = {
            "candidates": {"terra": [{"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro", "levels": ["medium"]}]}
        }
        self.assertEqual("retry", policy.failure(state, "e1", "failure"))
        self.assertEqual("pause", policy.failure(state, "e2", "failure"))
        self.assertEqual("xiaomi-token-plan-sgp/mimo-v2.6-pro", state["settings"]["roles"]["terra"]["model"])
        self.assertIn("saved accounts", state["stop_reason"])

    def test_nonexecution_classes_never_spend_or_mutate_the_builder_route(self):
        for kind, action in (("plan", "replan"), ("operational", "recover"), ("unknown", "investigate")):
            with self.subTest(kind=kind):
                state = self.state()
                state["sessions"] = {"terra": "retained-session"}
                original = copy.deepcopy(state)
                self.assertEqual(action, policy.failure(state, "e1", "observed failure", classification=kind))
                self.assertEqual(action, policy.failure(state, "e2", "observed failure", classification=kind))
                self.assertEqual(original["settings"], state["settings"])
                self.assertEqual(original["sessions"], state["sessions"])
                self.assertNotIn("builder_retries", state)
                self.assertEqual([kind, kind], [row["failure_class"] for row in state["builder_retry_decisions"]])
                state = json.loads(json.dumps(state))
                self.assertEqual(action, policy.failure(state, "e1", "observed failure", classification=kind))
                self.assertEqual(2, len(state["builder_retry_decisions"]))

    def test_classification_observations_do_not_reset_or_exhaust_execution_allowance(self):
        state = self.state()
        self.assertEqual("investigate", policy.failure(state, "e1", "ambiguous", classification="unknown"))
        self.assertEqual("retry", policy.failure(state, "e1", "verified code failure", classification="execution"))
        self.assertEqual("recover", policy.failure(state, "e2", "permission denied", classification="operational"))
        self.assertEqual("gpt-6-luna", state["settings"]["roles"]["terra"]["model"])
        self.assertEqual("escalate", policy.failure(state, "e3", "verified code failure", classification="execution"))
        self.assertEqual(["e1", "e3"], policy.lane(state)["failures"])
        state = json.loads(json.dumps(state))
        self.assertEqual("escalate", policy.failure(state, "e3", "verified code failure", classification="execution"))
        self.assertEqual(4, len(state["builder_retry_decisions"]))

    def test_operational_classification_cannot_clear_existing_exhaustion(self):
        state = self.state()
        for evidence in ("e1", "e2", "e3"):
            policy.failure(state, evidence, "execution failure")
        original = copy.deepcopy(state["builder_retries"])
        self.assertEqual("recover", policy.failure(state, "e4", "cleanup uncertain", classification="operational"))
        self.assertEqual(original, state["builder_retries"])
        with self.assertRaises(policy.s.Paused):
            policy.guard(state)

    def test_execution_replay_observes_current_gate_without_charging_again(self):
        state = self.state()
        self.assertEqual("retry", policy.failure(state, "e1", "code failure"))
        self.assertEqual("escalate", policy.failure(state, "e2", "code failure"))
        state = json.loads(json.dumps(state))
        self.assertEqual("escalate", policy.failure(state, "e1", "code failure"))
        self.assertEqual(2, len(state["builder_retry_decisions"]))
        self.assertEqual("pause", policy.failure(state, "e3", "code failure"))
        self.assertEqual("pause", policy.failure(state, "e1", "code failure"))
        self.assertEqual(["e1", "e2", "e3"], policy.lane(state)["failures"])
        self.assertEqual(3, len(state["builder_retry_decisions"]))

    def test_replayed_uncertainty_cannot_restore_any_execution_allowance(self):
        state = self.state()
        policy.failure(state, "e1", "ambiguous", classification="unknown")
        policy.failure(state, "e1", "verified code failure", classification="execution")
        policy.failure(state, "e2", "verified code failure", classification="execution")
        policy.failure(state, "e3", "verified code failure", classification="execution")
        state = json.loads(json.dumps(state))
        original = copy.deepcopy(state)
        self.assertEqual("investigate", policy.failure(state, "e1", "ambiguous", classification="unknown"))
        self.assertEqual(original, state)
        with self.assertRaises(policy.s.Paused):
            policy.guard(state)

    def saw(self, state, *, open=(), resolved=(), failing=(), passing=(), changed=("app.py",)):
        """The evidence a charge sees: open blocking and resolved findings, checks that failed or passed, changed files."""
        state["findings_ledger"] = [{"id": ident, "status": "open", "blocking": True} for ident in open] + [
            {"id": ident, "status": "resolved", "blocking": True} for ident in resolved
        ]
        checks = [{"command": command, "exit_code": 1} for command in failing]
        state["validation"] = {"checks": checks + [{"command": command, "exit_code": 0} for command in passing]}
        state["changed_files"] = list(changed)
        return state

    def test_a_failure_repeating_an_earlier_one_skips_the_ordinary_retries_then_pauses(self):
        state = self.state()
        state["settings"]["builder_retry"]["ordinary_retries"] = 3
        self.saw(state, failing=["pytest -q"])
        self.assertEqual("retry", policy.failure(state, "e1", "code failure"))
        self.assertEqual("escalate", policy.failure(state, "e2", "code failure"))
        self.assertEqual("gpt-6-sol", state["settings"]["roles"]["terra"]["model"])
        self.assertEqual("pause", policy.failure(state, "e3", "code failure"))
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", state["status"])
        self.assertIn("remains blocked after configured retry/escalation", state["stop_reason"])
        self.assertIn("repeats an earlier one of this milestone with no new evidence", state["stop_reason"])
        decisions = state["builder_retry_decisions"]
        self.assertEqual(["new", "repeat", "repeat"], [row["novelty"] for row in decisions])
        self.assertIn("no new evidence", decisions[1]["reason"])
        self.assertEqual([1, 2, 3], [row["attempt"] for row in decisions])

    def test_new_failures_that_are_not_progress_spend_the_ordinary_retries_as_before(self):
        state = self.state()
        state["settings"]["builder_retry"]["ordinary_retries"] = 2
        actions = []
        for attempt in range(4):
            self.saw(state, failing=["pytest -q"], changed=[f"app{attempt}.py"])
            actions.append(policy.failure(state, f"e{attempt}", "code failure"))
        self.assertEqual(["retry", "retry", "escalate", "pause"], actions)
        self.assertEqual(["new"] * 4, [row["novelty"] for row in state["builder_retry_decisions"]])

    def test_failures_that_resolve_every_earlier_one_do_not_count_toward_the_limit(self):
        # Live run 9pvu43lu (2026-10-07): each review resolved the last round's findings and found a new one,
        # and the Builder stopped on the count alone.
        state = self.state()
        self.saw(state, open=["F-a"])
        self.assertEqual("retry", policy.failure(state, "e1", "review rework"))
        self.saw(state, resolved=["F-a"], open=["F-b"])
        self.assertEqual("retry", policy.failure(state, "e2", "review rework"))
        self.saw(state, resolved=["F-a", "F-b"], open=["F-c"])
        self.assertEqual("retry", policy.failure(state, "e3", "review rework"))
        policy.guard(state)
        self.assertEqual("gpt-6-luna", state["settings"]["roles"]["terra"]["model"])
        decisions = state["builder_retry_decisions"]
        self.assertEqual(["new", "progress", "progress"], [row["novelty"] for row in decisions])
        self.assertIn("does not count toward the retry limit", decisions[1]["reason"])
        self.assertEqual("escalate", policy.failure(state, "e4", "review rework"))
        self.saw(state, resolved=["F-a", "F-b"], open=["F-c"], changed=["other.py"])
        self.assertEqual("pause", policy.failure(state, "e5", "review rework"))
        self.assertEqual(["e1", "e2", "e3", "e4", "e5"], policy.lane(state)["failures"])

    def test_progress_after_the_stronger_attempt_retries_on_the_stronger_route(self):
        state = self.state()
        self.saw(state, failing=["pytest -q"])
        policy.failure(state, "e1", "code failure")
        self.saw(state, failing=["pytest -q"], changed=["lib.py"])
        self.assertEqual("escalate", policy.failure(state, "e2", "code failure"))
        self.saw(state, open=["F-b"], passing=["pytest -q"])
        self.assertEqual("retry", policy.failure(state, "e3", "code failure"))
        self.assertEqual("gpt-6-sol", state["settings"]["roles"]["terra"]["model"])
        policy.guard(state)
        self.saw(state, open=["F-b"], passing=["pytest -q"], changed=["lib.py"])
        self.assertEqual("pause", policy.failure(state, "e4", "code failure"))

    def test_progress_cannot_lift_a_pause(self):
        state = self.state()
        for attempt in range(3):
            self.saw(state, failing=["pytest -q"], changed=[f"app{attempt}.py"])
            policy.failure(state, f"e{attempt}", "code failure")
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", state["status"])
        self.saw(state, open=["F-b"], passing=["pytest -q"])
        self.assertEqual("pause", policy.failure(state, "e3", "code failure"))
        with self.assertRaises(policy.s.Paused):
            policy.guard(state)

    def test_a_repeat_with_no_stronger_model_pauses_instead_of_retrying_the_same_route(self):
        state = self.state()
        state["settings"]["builder_retry"]["strong_model"] = None
        self.saw(state, failing=["pytest -q"])
        self.assertEqual("retry", policy.failure(state, "e1", "code failure"))
        self.assertEqual("pause", policy.failure(state, "e2", "code failure"))
        self.assertIn("offers no stronger Builder model", state["stop_reason"])
        self.assertIn("no new evidence", state["stop_reason"])

    def test_the_classification_budget_grows_with_failures_that_did_not_count(self):
        state = self.state()
        self.assertEqual(3, policy.classification_limit(state))
        self.saw(state, open=["F-a"])
        policy.failure(state, "e1", "review rework")
        self.saw(state, resolved=["F-a"], open=["F-b"])
        policy.failure(state, "e2", "review rework")
        self.assertEqual(4, policy.classification_limit(state))

    def test_a_lane_saved_before_the_ledger_keeps_its_count(self):
        state = self.state()
        policy.lane(state)["failures"].append("e0")
        policy.lane(state)["action"] = "retry"
        self.saw(state, open=["F-b"])
        self.assertEqual("escalate", policy.failure(state, "e1", "code failure"))
        self.assertEqual("escalate", policy.failure(state, "e0", "code failure"))

    def climbed(self, cap, tokens=None):
        """A run whose Builder failed twice on GPT-6 Luna and escalated, under a cost cap of `cap`."""
        state = self.state()
        state["settings"]["limits"] = {"max_cost_usd": cap}
        tokens = {"input_tokens": 1000, "output_tokens": 200} if tokens is None else tokens
        luna = {"engine": "codex", "model": "gpt-6-luna", "reasoning_effort": "medium"}
        state["stages"] = [
            {"stage": "terra", "role": "terra", "launch_route": luna, "metrics": {"provider_tokens": tokens}}
        ]
        policy.failure(state, "e1", "code failure")
        self.assertEqual("escalate", policy.failure(state, "e2", "code failure"))
        return state

    def test_a_stronger_attempt_projected_past_the_cost_cap_pauses_before_it_launches(self):
        # Spent: GPT-6 Luna's 1,000 input and 200 output tokens, $0.0002. Projected: the same tokens at
        # GPT-6 Sol's price, $0.004. $0.0002 + $0.004 = $0.0042 crosses a $0.001 cap.
        state = self.climbed(0.001)
        with self.assertRaises(policy.s.Paused) as stop:
            policy.guard(state)
        self.assertEqual("PAUSED_COST_LIMIT", stop.exception.status)
        reason = str(stop.exception)
        for part in (
            "stronger attempt (gpt-6-sol at xhigh effort) did not launch",
            "projected at $0.004",
            "1,000 input and 200 output tokens at gpt-6-sol's price",
            "from $0.0002 to $0.0042, past the $0.001 cost cap",
            "--max-cost-usd 0.01 or more",
        ):
            self.assertIn(part, reason)
        # The escalation decision stands: raising the cap to what the stop names lets that attempt launch.
        self.assertEqual(
            ("gpt-6-sol", "xhigh"),
            tuple(state["settings"]["roles"]["terra"][key] for key in ("model", "reasoning_effort")),
        )
        state["settings"]["limits"]["max_cost_usd"] = 0.01
        policy.guard(state)
        state["settings"]["limits"]["max_cost_usd"] = 0
        policy.guard(state)

    def test_the_cost_cap_check_prices_only_a_climb_that_has_not_launched(self):
        state = self.state()
        state["settings"]["limits"] = {"max_cost_usd": 0.0000001}
        policy.failure(state, "e1", "code failure")
        policy.guard(state)  # an ordinary retry is no climb; the stage-boundary cap check governs it
        state = self.climbed(0.001)
        strong = {"engine": "codex", "model": "gpt-6-sol", "reasoning_effort": "xhigh"}
        state["stages"].append({**state["stages"][0], "launch_route": strong})
        policy.guard(state)  # the stronger attempt already launched
        for unknown in (self.climbed(0.001, tokens={}), {**self.climbed(0.001), "parent_run": "parent"}):
            with self.subTest(unknown=unknown.get("parent_run") or "no token counts"):
                policy.guard(unknown)
        unpriced = self.climbed(0.001)
        unpriced["settings"]["roles"]["terra"]["model"] = "mystery-model"
        policy.guard(unpriced)

    def test_a_stronger_attempt_projected_past_the_time_budget_pauses_before_it_launches(self):
        # Used: 650 s of active time. Projected: the last GPT-6 Luna attempt's 600 s. 1,250 s passes the 1,000 s
        # budget set with --max-seconds.
        state = self.climbed(None)
        state["settings"]["limits"] = {"max_seconds": 1000, "stage_timeout_seconds": 3600}
        state["settings"]["budget_origins"] = {"max_seconds": "user_explicit"}
        state["stages"][0]["duration_seconds"] = 600
        state["active_seconds"] = 650
        with self.assertRaises(policy.s.Paused) as stop:
            policy.guard(state)
        self.assertEqual("PAUSED_TIME_LIMIT", stop.exception.status)
        self.assertEqual(
            "The Builder's stronger attempt (gpt-6-sol at xhigh effort) did not launch: projected at 600 s of active "
            "time, the last Builder attempt's duration, it would take the run's active time from 650 s to 1,250 s, "
            "past the 1,000 s active-time budget you set. AutoCode never raises a budget you set; resume with "
            "--max-seconds 1250 or more (0 for no limit) to make this attempt",
            str(stop.exception),
        )
        # The escalation decision stands: a budget of at least the total the stop names lets that attempt launch.
        state["settings"]["limits"]["max_seconds"] = 1250
        policy.guard(state)
        # A budget AutoResolver may extend at the stage boundary is left to that boundary, as before.
        state["settings"]["limits"]["max_seconds"] = 1000
        state["settings"]["budget_origins"]["max_seconds"] = "runner_default"
        policy.guard(state)
        # Once the stronger attempt has launched, the stage-boundary check governs it.
        state["settings"]["budget_origins"]["max_seconds"] = "user_explicit"
        strong = {"engine": "codex", "model": "gpt-6-sol", "reasoning_effort": "xhigh"}
        state["stages"].append({**state["stages"][0], "launch_route": strong})
        policy.guard(state)

    def milestone_budgeted(self, budget, *, used=650, origin="user_explicit"):
        """The climbed run, with `used` seconds counted on milestone M1 under a milestone budget of `budget`
        seconds that `origin` set; the last GPT-6 Luna attempt took 600 s."""
        state = self.climbed(None)
        state["current_task"]["contract_hash"] = "approved"
        state["settings"]["limits"] = {"stage_timeout_seconds": 3600}
        state["settings"]["milestone_checkpoints"] = {"enabled": True, "max_seconds": budget}
        state["settings"]["budget_origins"] = {"milestone_max_seconds": origin}
        state["milestone_progress"] = {"approved:M1": {"id": "M1", "contract_hash": "approved", "seconds": used}}
        state["stages"][0]["duration_seconds"] = 600
        return state

    def test_a_stronger_attempt_projected_past_the_milestone_budget_pauses_before_it_launches(self):
        # Used: 650 s on milestone M1. Projected: the last GPT-6 Luna attempt's 600 s. 1,250 s passes the 1,000 s
        # milestone budget set with --max-milestone-seconds, though 650 s alone is within it.
        state = self.milestone_budgeted(1000)
        with self.assertRaises(policy.s.Paused) as stop:
            policy.guard(state)
        self.assertEqual("PAUSED_MILESTONE_BUDGET", stop.exception.status)
        self.assertEqual(
            "The Builder's stronger attempt (gpt-6-sol at xhigh effort) did not launch: projected at 600 s of active "
            "time, the last Builder attempt's duration, it would take this milestone's active time from 650 s to "
            "1,250 s, past the 1,000 s milestone budget you set. AutoCode never raises a budget you set; resume with "
            "--max-milestone-seconds 1250 or more (0 for no limit), which also restarts this milestone's count, to "
            "make this attempt",
            str(stop.exception),
        )
        # The escalation decision stands: a budget that admits the total, none, or the milestone's count restarted
        # by resuming with --max-milestone-seconds, lets that attempt launch.
        self.assertEqual("gpt-6-sol", state["settings"]["roles"]["terra"]["model"])
        for budget, used in ((1250, 650), (0, 650), (1000, 0)):
            with self.subTest(budget=budget, used=used):
                policy.guard(self.milestone_budgeted(budget, used=used))
        # An attempt projected past the budget on its own pauses even on a restarted count.
        with self.assertRaises(policy.s.Paused) as stop:
            policy.guard(self.milestone_budgeted(500, used=0))
        self.assertIn(
            "from 0 s to 600 s, past the 500 s milestone budget you set. AutoCode never raises a budget you set; "
            "resume with --max-milestone-seconds 600 or more",
            str(stop.exception),
        )

    def test_only_a_milestone_budget_you_set_is_projected_for_the_current_milestone(self):
        launched = self.milestone_budgeted(1000)
        strong = {"engine": "codex", "model": "gpt-6-sol", "reasoning_effort": "xhigh"}
        launched["stages"].append({**launched["stages"][0], "launch_route": strong})
        progressive = self.milestone_budgeted(1000)
        progressive["progressive"] = {"version": 1, "budget": {"run_limit": 3600}}
        revised = self.milestone_budgeted(1000)
        revised["current_task"]["contract_hash"] = "superseded"
        # The 600 s projection alone passes 500 s, so projecting from 0 s without a row would pause.
        unassigned = self.milestone_budgeted(500)
        del unassigned["milestone_progress"]
        disabled = self.milestone_budgeted(1000)
        disabled["settings"]["milestone_checkpoints"]["enabled"] = False
        undated = self.milestone_budgeted(1000)
        del undated["stages"][0]["duration_seconds"]
        for name, state in (
            # The writer-launch check governs these, as before: it pauses once the milestone's time reaches them.
            ("runner default", self.milestone_budgeted(1000, origin="runner_default")),
            ("delegated", self.milestone_budgeted(1000, origin="resolver_delegated")),
            ("checkpoints off", disabled),
            # A progressive run's slice budget has its own check, and resuming does not restart its count.
            ("progressive", progressive),
            ("task on another contract", revised),
            ("no milestone row", unassigned),
            ("no duration", undated),
            ("stronger attempt launched", launched),
            ("parallel Builder", {**self.milestone_budgeted(1000), "parent_run": "parent"}),
        ):
            with self.subTest(name):
                policy.guard(state)
        self.assertNotIn("milestone_progress", unassigned, "the check never creates a milestone row")

    def test_unknown_class_name_cannot_mutate_authority(self):
        state = self.state()
        original = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            policy.failure(state, "e1", "failure", classification="escalate")
        self.assertEqual(original, state)

    def test_pins_and_custom_providers_are_not_overridden(self):
        for engine in ("codex", "opencode"):
            for override in ({"model_pinned": True}, {"provider": "custom"}, {"provider": "claude"}):
                with self.subTest(engine=engine, override=override):
                    state = self.state()
                    state["settings"]["roles"]["terra"].update(engine=engine, **override)
                    original = copy.deepcopy(state["settings"]["roles"]["terra"])
                    policy.failure(state, "e1", "failure")
                    self.assertEqual("pause", policy.failure(state, "e2", "failure"))
                    self.assertEqual(original, state["settings"]["roles"]["terra"])

    def test_native_strong_model_is_bare_and_preserves_transport(self):
        for strong in ("openai/gpt-6-sol", "gpt-6-sol"):
            with self.subTest(strong=strong):
                state = self.state()
                state["settings"]["builder_retry"]["strong_model"] = strong
                state["settings"]["roles"]["terra"].update(engine="codex", provider="openai")
                self.assertEqual("retry", policy.failure(state, "e1", "failure"))
                self.assertEqual("escalate", policy.failure(state, "e2", "failure"))
                self.assertEqual(
                    {"model": "gpt-6-sol", "reasoning_effort": "xhigh", "engine": "codex", "provider": "openai"},
                    state["settings"]["roles"]["terra"],
                )
                self.assertEqual(strong, state["settings"]["builder_retry"]["strong_model"])
                self.assertEqual("gpt-6-sol", state["builder_retry_decisions"][-1]["selected_model"])

    def test_builder_role_engine_takes_precedence_over_run_engine(self):
        for run_engine, role_engine, expected in (
            ("opencode", "codex", "gpt-6-sol"),
            ("codex", "opencode", "openai/gpt-6-sol"),
        ):
            with self.subTest(run_engine=run_engine, role_engine=role_engine):
                state = self.state()
                state["settings"]["engine"] = run_engine
                state["settings"]["roles"]["terra"]["engine"] = role_engine
                policy.failure(state, "e1", "failure")
                self.assertEqual("escalate", policy.failure(state, "e2", "failure"))
                self.assertEqual(expected, state["settings"]["roles"]["terra"]["model"])
                self.assertEqual(role_engine, state["settings"]["roles"]["terra"]["engine"])
                self.assertEqual(run_engine, state["settings"]["engine"])

    def test_native_normalization_does_not_strip_another_provider_prefix(self):
        state = self.state()
        state["settings"]["builder_retry"]["strong_model"] = "custom/gpt-6-sol"
        policy.failure(state, "e1", "failure")
        self.assertEqual("escalate", policy.failure(state, "e2", "failure"))
        self.assertEqual("custom/gpt-6-sol", state["settings"]["roles"]["terra"]["model"])

    def test_a_provider_that_lists_its_models_without_the_strong_one_retries_once_more_then_pauses(self):
        claude = OLDER_CLAUDE_PROVIDER
        with self.assertRaisesRegex(ValueError, "is not a claude model"):
            policy.configured("openai/gpt-6-sol", claude)
        self.assertEqual("claude-opus-5-5", policy.configured("claude-opus-5-5", claude)["strong_model"])
        state = self.state()
        state["settings"]["builder_retry"] = policy.configured(None, claude)
        self.assertEqual("retry", policy.failure(state, "e1", "failure"))
        self.assertEqual("retry", policy.failure(state, "e2", "failure"))
        policy.guard(state)
        self.assertEqual("pause", policy.failure(state, "e3", "failure"))
        self.assertEqual("gpt-6-luna", state["settings"]["roles"]["terra"]["model"])
        self.assertIn("offers no stronger Builder model", state["stop_reason"])
        with self.assertRaises(policy.s.Paused):
            policy.guard(state)

    def test_a_run_whose_provider_did_not_offer_the_stronger_attempt_pauses_naming_the_model(self):
        """route_ladder.serve dropped the stronger attempt at run creation; the pause says which model (#612)."""
        state = self.state()
        state["settings"]["builder_retry"].update(strong_model=None, not_offered="zai-coding-plan/glm-5.3")
        self.assertEqual("retry", policy.failure(state, "e1", "failure"))
        self.assertEqual("retry", policy.failure(state, "e2", "failure"))
        self.assertEqual("pause", policy.failure(state, "e3", "failure"))
        self.assertEqual("gpt-6-luna", state["settings"]["roles"]["terra"]["model"])
        self.assertEqual(
            "Execution failure: " + policy.NOT_OFFERED.format(model="zai-coding-plan/glm-5.3"), state["stop_reason"]
        )
        self.assertIn("does not offer zai-coding-plan/glm-5.3", state["stop_reason"])

    def test_a_pinned_builder_whose_provider_lacks_the_strong_model_pauses_as_before(self):
        """Checking the stronger attempt's models at run creation must not give a pinned Builder an extra launch."""
        state = self.state()
        state["settings"]["engine"] = "opencode"
        state["settings"]["roles"] = {
            "terra": {"model": "openai/gpt-6-luna", "reasoning_effort": "medium", "model_pinned": True},
            "sol": {"model": "openai/gpt-6-luna", "reasoning_effort": "high"},
            "completion": {"model": "openai/gpt-6-luna", "reasoning_effort": "medium"},
        }
        route_ladder.serve(state["settings"], {"openai/gpt-6-luna", "zai-coding-plan/glm-5.3"})
        self.assertEqual(["retry", "pause"], [policy.failure(state, f"e{n}", "failure") for n in (1, 2)])
        self.assertEqual("Execution failure: " + policy.EXHAUSTED, state["stop_reason"])

    def test_providers_that_do_not_list_models_keep_the_default_strong_model(self):
        self.assertEqual(policy.DEFAULTS, policy.configured(None, opencode))
        unlisted = command.CommandProvider({**OLDER_CLAUDE_CONFIG, "models": None}, CLAUDE_TOML)
        self.assertEqual(policy.DEFAULTS["strong_model"], policy.configured(None, unlisted)["strong_model"])

    def test_new_milestone_restores_normal_route_and_new_budget(self):
        state = self.state()
        policy.failure(state, "e1", "f")
        policy.failure(state, "e2", "f")
        state["current_task"] = {"id": "task2", "milestone_id": "M2"}
        policy.guard(state)
        self.assertEqual("gpt-6-luna", state["settings"]["roles"]["terra"]["model"])
        self.assertEqual("retry", policy.failure(state, "e3", "f"))

    def test_new_contract_preserves_operator_pinned_route(self):
        state = self.state()
        policy.guard(state)
        selected = {
            "model": "openai/gpt-6-sol",
            "reasoning_effort": "max",
            "engine": "opencode",
            "provider": None,
            "model_pinned": True,
        }
        state["settings"]["roles"]["terra"] = copy.deepcopy(selected)
        state["goal_contract"]["hash"] = "reapproved"
        state = json.loads(json.dumps(state))
        policy.guard(state)
        self.assertEqual(selected, state["settings"]["roles"]["terra"])
        self.assertEqual(selected, policy.lane(state)["initial_route"])

    def default_routes(self):
        state = self.state()
        state["settings"]["engine"] = "opencode"
        state["settings"]["roles"] = {
            "terra": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "medium"},
            "sol": {"model": "openai/gpt-6-sol", "reasoning_effort": "high"},
            "completion": {"model": "openai/gpt-6-sol", "reasoning_effort": "medium"},
        }
        return state

    def test_escalated_builder_is_checked_by_glm_until_the_next_milestone(self):
        state = self.default_routes()
        dispatch.enforce_cross_model_verification(state)
        policy.failure(state, "e1", "f")
        self.assertEqual("escalate", policy.failure(state, "e2", "f"))
        roles = state["settings"]["roles"]
        self.assertEqual(("openai/gpt-6-sol", "xhigh"), (roles["terra"]["model"], roles["terra"]["reasoning_effort"]))
        self.assertEqual(("zai-coding-plan/glm-5.3", "high"), (roles["sol"]["model"], roles["sol"]["reasoning_effort"]))
        self.assertEqual(
            ("zai-coding-plan/glm-5.3", "medium"),
            (roles["completion"]["model"], roles["completion"]["reasoning_effort"]),
        )
        self.assertEqual(
            {"sol": "zai-coding-plan/glm-5.3", "completion": "zai-coding-plan/glm-5.3"},
            state["builder_retry_decisions"][-1]["checker_models"],
        )
        dispatch.enforce_cross_model_verification(state)
        state = json.loads(json.dumps(state))
        state["current_task"] = {"id": "task2", "milestone_id": "M2"}
        policy.guard(state)
        self.assertEqual(self.default_routes()["settings"]["roles"], state["settings"]["roles"])
        dispatch.enforce_cross_model_verification(state)

    def test_a_run_saved_before_checker_model_existed_still_moves_its_checkers(self):
        state = self.default_routes()
        del state["settings"]["builder_retry"]["checker_model"]
        policy.failure(state, "e1", "f")
        self.assertEqual("escalate", policy.failure(state, "e2", "f"))
        roles = state["settings"]["roles"]
        self.assertEqual(policy.DEFAULTS["checker_model"], roles["sol"]["model"])
        self.assertEqual(policy.DEFAULTS["checker_model"], roles["completion"]["model"])
        dispatch.enforce_cross_model_verification(state)

    def test_checkers_are_not_moved_onto_the_strong_model_itself(self):
        state = self.default_routes()
        state["settings"]["builder_retry"]["checker_model"] = policy.DEFAULTS["strong_model"]
        policy.failure(state, "e1", "f")
        self.assertEqual("escalate", policy.failure(state, "e2", "f"))
        self.assertNotIn("checker_models", state["builder_retry_decisions"][-1])
        with self.assertRaises(policy.s.Paused) as raised:
            dispatch.enforce_cross_model_verification(state)
        self.assertEqual("PAUSED_CROSS_MODEL", raised.exception.status)

    def test_a_new_run_refuses_a_strong_model_that_is_its_checker_model(self):
        self.assertEqual(policy.DEFAULTS, policy.configured())
        self.assertEqual("openai/gpt-6-luna", policy.configured("openai/gpt-6-luna")["strong_model"])
        with self.assertRaisesRegex(ValueError, "checked by its own model"):
            policy.configured(policy.DEFAULTS["checker_model"])

    def test_a_checker_in_the_strong_models_family_moves_so_the_guard_admits_the_attempt(self):
        # #612: the stronger attempt moved only a checker on the exact strong model, while the dispatch
        # guard refuses the whole GLM family, so a GLM Tester stayed and the escalated run paused.
        state = self.default_routes()
        state["settings"]["builder_retry"].update(
            strong_model="zai-coding-plan/glm-5.3", checker_model="openai/gpt-6-sol"
        )
        roles = state["settings"]["roles"]
        roles["terra"]["model"] = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
        roles["sol"]["model"] = "zai-coding-plan/glm-5.2"
        roles["completion"]["model"] = "openai/gpt-6-luna"
        dispatch.enforce_cross_model_verification(state)
        policy.failure(state, "e1", "f")
        self.assertEqual("escalate", policy.failure(state, "e2", "f"))
        self.assertEqual("zai-coding-plan/glm-5.3", roles["terra"]["model"])
        self.assertEqual({"sol": "openai/gpt-6-sol"}, state["builder_retry_decisions"][-1]["checker_models"])
        self.assertEqual("openai/gpt-6-luna", roles["completion"]["model"])
        dispatch.enforce_cross_model_verification(state)

    def test_a_new_run_refuses_a_strong_model_in_its_checker_models_family(self):
        # Refused at run creation: escalating would move its checkers onto its own family mid-run.
        with self.assertRaisesRegex(ValueError, "shares a model family with zai-coding-plan/glm-5.3"):
            policy.configured("zai-coding-plan/glm-5.2")
        self.assertEqual(
            "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            policy.configured("xiaomi-token-plan-sgp/mimo-v2.6-pro")["strong_model"],
        )

    def test_single_model_mode_keeps_builder_retry_on_the_same_model(self):
        configured = policy.configured(single_model="openai/gpt-6-sol")
        self.assertEqual("openai/gpt-6-sol", configured["strong_model"])
        self.assertEqual("openai/gpt-6-sol", configured["checker_model"])

    def test_a_climbed_checker_moves_to_a_glm_effort(self):
        state = self.default_routes()
        state["settings"]["roles"]["sol"]["reasoning_effort"] = "max"
        policy.failure(state, "e1", "f")
        policy.failure(state, "e2", "f")
        self.assertEqual("high", state["settings"]["roles"]["sol"]["reasoning_effort"])
        state["current_task"] = {"id": "task2", "milestone_id": "M2"}
        policy.guard(state)
        self.assertEqual("max", state["settings"]["roles"]["sol"]["reasoning_effort"])

    def test_a_pinned_checker_is_not_moved_and_the_cross_model_guard_still_pauses(self):
        state = self.default_routes()
        state["settings"]["roles"]["sol"]["model_pinned"] = True
        policy.failure(state, "e1", "f")
        self.assertEqual("escalate", policy.failure(state, "e2", "f"))
        self.assertEqual("openai/gpt-6-sol", state["settings"]["roles"]["sol"]["model"])
        self.assertEqual("zai-coding-plan/glm-5.3", state["settings"]["roles"]["completion"]["model"])
        with self.assertRaises(policy.s.Paused) as raised:
            dispatch.enforce_cross_model_verification(state)
        self.assertEqual("PAUSED_CROSS_MODEL", raised.exception.status)

    def test_native_builder_and_pinned_opencode_checker_alias_still_pause(self):
        state = self.state()
        checker = {
            "model": "openai/gpt-6-sol",
            "reasoning_effort": "high",
            "engine": "opencode",
            "provider": "openai",
            "model_pinned": True,
        }
        state["settings"]["roles"]["sol"] = copy.deepcopy(checker)
        dispatch.enforce_cross_model_verification(state)
        policy.failure(state, "e1", "failure")
        self.assertEqual("escalate", policy.failure(state, "e2", "failure"))
        self.assertEqual("gpt-6-sol", state["settings"]["roles"]["terra"]["model"])
        self.assertEqual(checker, state["settings"]["roles"]["sol"])
        with self.assertRaises(policy.s.Paused) as raised:
            dispatch.enforce_cross_model_verification(state)
        self.assertEqual("PAUSED_CROSS_MODEL", raised.exception.status)
        self.assertIn("Builder/Tester", str(raised.exception))

    def test_native_builder_leaves_bare_checker_collision_for_guard(self):
        for override in ({}, {"model_pinned": True}, {"provider": "custom"}):
            with self.subTest(override=override):
                state = self.state()
                checker = {"model": "gpt-6-sol", "reasoning_effort": "high", "engine": "codex", **override}
                state["settings"]["roles"]["completion"] = copy.deepcopy(checker)
                policy.failure(state, "e1", "failure")
                self.assertEqual("escalate", policy.failure(state, "e2", "failure"))
                self.assertEqual(checker, state["settings"]["roles"]["completion"])
                self.assertNotIn("checker_models", state["builder_retry_decisions"][-1])
                with self.assertRaises(policy.s.Paused) as raised:
                    dispatch.enforce_cross_model_verification(state)
                self.assertEqual("PAUSED_CROSS_MODEL", raised.exception.status)
                self.assertIn("Builder/Completion Reviewer", str(raised.exception))

    def test_native_builder_still_swaps_compatible_opencode_checker(self):
        state = self.state()
        state["settings"]["roles"]["sol"] = {
            "model": "openai/gpt-6-sol",
            "reasoning_effort": "medium",
            "engine": "opencode",
            "provider": "openai",
        }
        policy.failure(state, "e1", "failure")
        self.assertEqual("escalate", policy.failure(state, "e2", "failure"))
        self.assertEqual("gpt-6-sol", state["settings"]["roles"]["terra"]["model"])
        self.assertEqual(
            {
                "model": policy.DEFAULTS["checker_model"],
                "reasoning_effort": "medium",
                "engine": "opencode",
                "provider": "openai",
            },
            state["settings"]["roles"]["sol"],
        )
        self.assertEqual(
            {"sol": policy.DEFAULTS["checker_model"]}, state["builder_retry_decisions"][-1]["checker_models"]
        )
        dispatch.enforce_cross_model_verification(state)

    def test_a_parallel_builder_defers_its_stronger_attempt_to_the_parent(self):
        parent = self.default_routes()
        worker = copy.deepcopy(parent)
        worker["parent_run"] = "/runs/parent"
        policy.failure(worker, "e1", "f")
        self.assertEqual("defer", policy.failure(worker, "e2", "f"))
        self.assertEqual(self.default_routes()["settings"]["roles"], worker["settings"]["roles"])
        self.assertEqual(policy.SERIAL, worker["status"])
        self.assertIn("makes that attempt serially", worker["stop_reason"])
        # Restarting or retrying the worker cannot make the attempt there.
        worker = json.loads(json.dumps(worker))
        with self.assertRaises(policy.s.Paused) as raised:
            policy.guard(worker)
        self.assertEqual(policy.SERIAL, raised.exception.status)
        # The parent's own lane for the milestone was opened before the batch ran.
        policy.guard(parent)
        policy.adopt(parent, worker)
        policy.adopt(parent, worker)
        self.assertEqual(["retry", "defer"], [d["action"] for d in parent["builder_retry_decisions"]])
        self.assertTrue(policy.failed_before(parent, "M1"))
        self.assertFalse(policy.failed_before(parent, "M2"))
        parent = json.loads(json.dumps(parent))
        policy.guard(parent)
        roles = parent["settings"]["roles"]
        self.assertEqual(("openai/gpt-6-sol", "xhigh"), (roles["terra"]["model"], roles["terra"]["reasoning_effort"]))
        self.assertEqual("zai-coding-plan/glm-5.3", roles["sol"]["model"])
        self.assertEqual("zai-coding-plan/glm-5.3", roles["completion"]["model"])
        dispatch.enforce_cross_model_verification(parent)
        policy.guard(parent)
        self.assertEqual(["retry", "defer", "escalate"], [d["action"] for d in parent["builder_retry_decisions"]])
        # The budget carried over: the next failure pauses, and the next milestone restores the routes.
        self.assertEqual("pause", policy.failure(parent, "e3", "f"))
        parent["current_task"] = {"id": "task2", "milestone_id": "M2"}
        policy.guard(parent)
        self.assertEqual(self.default_routes()["settings"]["roles"], parent["settings"]["roles"])

    def test_a_deferred_attempt_does_not_override_a_builder_pinned_since(self):
        parent, worker = self.default_routes(), self.default_routes()
        worker["parent_run"] = "/runs/parent"
        policy.failure(worker, "e1", "f")
        policy.failure(worker, "e2", "f")
        policy.adopt(parent, worker)
        parent["settings"]["roles"]["terra"]["model_pinned"] = True
        with self.assertRaises(policy.s.Paused) as raised:
            policy.guard(parent)
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", raised.exception.status)
        self.assertEqual(self.default_routes()["settings"]["roles"]["sol"], parent["settings"]["roles"]["sol"])
        self.assertEqual("zai-coding-plan/glm-5.3", parent["settings"]["roles"]["terra"]["model"])

    def test_configured_strong_model_retains_opencode_transport(self):
        state = self.state()
        state["settings"]["engine"] = "opencode"
        state["settings"]["builder_retry"]["strong_model"] = "gpt-6-sol"
        policy.failure(state, "e1", "f")
        policy.failure(state, "e2", "f")
        self.assertEqual("openai/gpt-6-sol", state["settings"]["roles"]["terra"]["model"])
        self.assertEqual("xhigh", state["settings"]["roles"]["terra"]["reasoning_effort"])


class ToolProviderPolicyTests(unittest.TestCase):
    """A configured command tool (here the Claude example) names its models itself, verbatim."""

    def state(self, provider=CLAUDE_PROVIDER):
        def route(role, **extra):
            return {
                "engine": "opencode",
                "provider": None,
                "model": provider.DEFAULT_MODELS[role],
                "reasoning_effort": provider.DEFAULT_REASONING_EFFORTS[role],
                **extra,
            }

        return {
            "settings": {
                "engine": "opencode",
                "provider": provider.NAME,
                "builder_retry": policy.configured(None, provider),
                "roles": {
                    **{role: route(role) for role in ("astra", "terra", "sol", "completion", "glm")},
                    "plan_reviewer": route("plan_reviewer", model_pinned=True),
                },
            },
            "goal_contract": {"hash": "approved"},
            "current_task": {"id": "task1", "milestone_id": "M1"},
            "status": "RUNNING",
        }

    def test_the_builder_escalates_to_the_tools_stronger_model_and_the_validator_moves_until_the_next_milestone(self):
        state = self.state()
        before = copy.deepcopy(state["settings"]["roles"])
        self.assertEqual("retry", policy.failure(state, "e1", "f"))
        self.assertEqual("escalate", policy.failure(state, "e2", "f"))
        roles = state["settings"]["roles"]
        self.assertEqual({**before["terra"], "model": "claude-sonnet-5-5", "reasoning_effort": "xhigh"}, roles["terra"])
        self.assertEqual({**before["sol"], "model": "claude-opus-5-5"}, roles["sol"])
        self.assertEqual(before["completion"], roles["completion"])
        self.assertEqual({"sol": "claude-opus-5-5"}, state["builder_retry_decisions"][-1]["checker_models"])
        dispatch.enforce_cross_model_verification(state)
        CLAUDE_PROVIDER.check_models(roles)  # every route is a model the tool serves, spelled as it spells it
        self.assertEqual("pause", policy.failure(state, "e3", "f"))
        self.assertEqual("Execution failure: " + policy.EXHAUSTED, state["stop_reason"])
        state = json.loads(json.dumps(state))
        state["current_task"] = {"id": "task2", "milestone_id": "M2"}
        policy.guard(state)
        self.assertEqual(before, state["settings"]["roles"])

    def test_a_tool_validator_in_the_strong_models_family_moves_off_it(self):
        # #612: a Tester on another GLM model than the tool's stronger GLM Builder is a clash the cross-model
        # guard refuses, so it moves as one on the strong model itself does instead of pausing the escalated run.
        tool = command.CommandProvider(
            {
                **OLDER_CLAUDE_CONFIG,
                "name": "glmtool",
                "models": ["mimo-v2.6-pro", "glm-5.2", "glm-5.3", "claude-opus-5-5"],
                "roles": {
                    **{
                        role: {"model": "claude-opus-5-5", "effort": "medium"}
                        for role in ("astra", "completion", "plan_reviewer")
                    },
                    "terra": {"model": "mimo-v2.6-pro", "effort": "medium"},
                    "glm": {"model": "mimo-v2.6-pro", "effort": "medium"},
                    "sol": {"model": "glm-5.2", "effort": "medium"},
                },
                "builder_retry": {"strong_model": "glm-5.3", "checker_model": "claude-opus-5-5"},
            },
            CLAUDE_TOML,
        )
        state = self.state(tool)
        dispatch.enforce_cross_model_verification(state)
        policy.failure(state, "e1", "f")
        self.assertEqual("escalate", policy.failure(state, "e2", "f"))
        roles = state["settings"]["roles"]
        self.assertEqual(("glm-5.3", "claude-opus-5-5"), (roles["terra"]["model"], roles["sol"]["model"]))
        self.assertEqual({"sol": "claude-opus-5-5"}, state["builder_retry_decisions"][-1]["checker_models"])
        dispatch.enforce_cross_model_verification(state)
        tool.check_models(roles)

    def test_a_parallel_tool_builder_defers_and_the_parent_escalates_with_the_validator_moved(self):
        parent = self.state()
        worker = copy.deepcopy(parent)
        worker["parent_run"] = "/runs/parent"
        policy.failure(worker, "e1", "f")
        self.assertEqual("defer", policy.failure(worker, "e2", "f"))
        self.assertIn("stronger model claude-sonnet-5-5,", worker["stop_reason"])
        policy.guard(parent)
        policy.adopt(parent, worker)
        policy.guard(parent)
        roles = parent["settings"]["roles"]
        self.assertEqual(("claude-sonnet-5-5", "claude-opus-5-5"), (roles["terra"]["model"], roles["sol"]["model"]))
        dispatch.enforce_cross_model_verification(parent)

    def test_a_checker_that_cannot_move_stops_the_escalation_before_any_route_changes(self):
        for name, change in (
            ("pinned Validator", lambda st: st["settings"]["roles"]["sol"].update(model_pinned=True)),
            ("custom-provider Validator", lambda st: st["settings"]["roles"]["sol"].update(provider="custom")),
            (
                "checker model is the strong model",
                lambda st: st["settings"]["builder_retry"].update(checker_model="claude-sonnet-5-5"),
            ),
            (
                "checker model no role uses (a run saved before checker_model existed)",
                lambda st: st["settings"]["builder_retry"].pop("checker_model"),
            ),
            (
                "Validator spells the strong model another way",
                lambda st: st["settings"]["roles"]["sol"].update(model="openai/claude-sonnet-5-5"),
            ),
        ):
            with self.subTest(name):
                state = self.state()
                change(state)
                before = copy.deepcopy(state["settings"]["roles"])
                policy.failure(state, "e1", "f")
                self.assertEqual("pause", policy.failure(state, "e2", "f"))
                self.assertEqual(before, state["settings"]["roles"])
                self.assertNotIn("checker_models", state["builder_retry_decisions"][-1])
                self.assertIn("would be checked by its own model", state["stop_reason"])
                with self.assertRaises(policy.s.Paused) as raised:
                    policy.guard(state)
                self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", raised.exception.status)

    def test_a_new_tool_run_takes_its_policy_from_the_provider_config(self):
        self.assertEqual(
            {**policy.DEFAULTS, "strong_model": "claude-sonnet-5-5", "checker_model": "claude-opus-5-5"},
            policy.configured(None, CLAUDE_PROVIDER),
        )
        with self.assertRaisesRegex(ValueError, "checked by its own model"):
            policy.configured("claude-opus-5-5", CLAUDE_PROVIDER)
        with self.assertRaisesRegex(ValueError, "is not a claude model"):
            policy.configured("openai/gpt-6-sol", CLAUDE_PROVIDER)
        self.assertEqual(
            "claude-haiku-4-5-20251001", policy.configured("claude-haiku-4-5-20251001", CLAUDE_PROVIDER)["strong_model"]
        )
        # The built-in claude provider (#717) keeps the example's policy.
        self.assertEqual(policy.configured(None, CLAUDE_PROVIDER), policy.configured(None, claude))

    def test_a_tool_config_without_a_builder_retry_table_keeps_todays_policy(self):
        older = OLDER_CLAUDE_PROVIDER
        self.assertEqual({**policy.DEFAULTS, "strong_model": None}, policy.configured(None, older))
        state = self.state(older)
        self.assertEqual(["retry", "retry", "pause"], [policy.failure(state, f"e{n}", "f") for n in (1, 2, 3)])
        self.assertEqual("Execution failure: " + policy.NO_STRONG_MODEL, state["stop_reason"])
        # The pause names the fix that works on such a tool: the flag alone would move a checker to GLM.
        self.assertIn("[builder_retry] in the provider config", state["stop_reason"])
        self.assertNotIn("--builder-strong-model", state["stop_reason"])

    def test_a_tool_without_a_builder_retry_table_creates_runs_as_before(self):
        # A listed tool that serves GPT-6 Sol but not GLM, with no checker on GPT-6 Sol, still starts runs.
        listed = command.CommandProvider(
            {**OLDER_CLAUDE_CONFIG, "name": "oaitool", "models": [*OLDER_CLAUDE_CONFIG["models"], "openai/gpt-6-sol"]},
            CLAUDE_TOML,
        )
        self.assertEqual(policy.DEFAULTS, policy.configured(None, listed))
        self.assertEqual("claude-opus-5-5", policy.configured("claude-opus-5-5", OLDER_CLAUDE_PROVIDER)["strong_model"])

    def test_a_tool_builder_that_already_runs_the_strong_model_makes_its_stronger_attempt_on_it(self):
        # As an OpenAI Builder already on GPT-6 Sol gets its xhigh attempt, a Sonnet Builder gets Sonnet at xhigh,
        # and the Sonnet Tester moves to Opus.
        state = self.state()
        roles = state["settings"]["roles"]
        roles["terra"]["model"] = "claude-sonnet-5-5"
        policy.failure(state, "e1", "f")
        self.assertEqual("escalate", policy.failure(state, "e2", "f"))
        self.assertEqual(
            ("claude-sonnet-5-5", "xhigh", "claude-opus-5-5"),
            (roles["terra"]["model"], roles["terra"]["reasoning_effort"], roles["sol"]["model"]),
        )
        dispatch.enforce_cross_model_verification(state)

    def test_a_builder_on_the_default_strong_model_keeps_todays_policy_without_a_builder_retry_table(self):
        # An OpenAI Builder already on GPT-6 Sol still gets its xhigh attempt, as before [builder_retry] existed.
        for provider in (None, opencode, command.load("kilocode")):
            with self.subTest(provider=getattr(provider, "NAME", None)):
                self.assertEqual(policy.DEFAULTS, policy.configured(None, provider))

    def test_a_pause_before_escalating_names_jobs_by_their_screen_names(self):
        state = self.state()
        state["settings"]["roles"]["sol"].update(model_pinned=True)
        policy.failure(state, "e1", "f")
        self.assertEqual("pause", policy.failure(state, "e2", "f"))
        self.assertIn("a Tester or Completion Reviewer on it is pinned", state["stop_reason"])
        self.assertNotRegex(state["stop_reason"], r"\b(sol|completion|terra|astra)\b")

    def test_a_tool_with_bare_gpt_names_and_the_openai_default_pauses_without_changing_a_route(self):
        # The documented codex_receipts tool: bare GPT names, no models list, so the OpenAI defaults apply.
        receipts = command.CommandProvider(
            {
                **OLDER_CLAUDE_CONFIG,
                "name": "codex_receipts",
                "models": None,
                "roles": {
                    **{
                        role: {"model": "gpt-6-sol", "effort": "medium"}
                        for role in ("astra", "sol", "completion", "plan_reviewer")
                    },
                    "terra": {"model": "gpt-6-luna", "effort": "medium"},
                    "glm": {"model": "gpt-6-luna", "effort": "medium"},
                },
            },
            CLAUDE_TOML,
        )
        for strong in (None, "gpt-6-sol"):
            with self.subTest(strong=strong):
                state = self.state(receipts)
                state["settings"]["builder_retry"] = policy.configured(strong, receipts)
                before = copy.deepcopy(state["settings"]["roles"])
                policy.failure(state, "e1", "f")
                self.assertEqual("pause", policy.failure(state, "e2", "f"))
                self.assertEqual(before, state["settings"]["roles"])
                self.assertIn("would be checked by its own model", state["stop_reason"])

    def test_a_tool_that_names_models_with_a_slash_escalates_as_before(self):
        kilo = command.load("kilocode")
        state = self.state(kilo)
        self.assertEqual(policy.DEFAULTS, state["settings"]["builder_retry"])
        state["settings"]["roles"]["sol"]["model"] = "openai/gpt-6-sol"
        policy.failure(state, "e1", "f")
        self.assertEqual("escalate", policy.failure(state, "e2", "f"))
        roles = state["settings"]["roles"]
        self.assertEqual(
            ("openai/gpt-6-sol", "zai-coding-plan/glm-5.3"), (roles["terra"]["model"], roles["sol"]["model"])
        )

    def test_a_kilocode_builder_given_a_bare_strong_model_gets_the_openai_alias_as_before(self):
        kilo = command.load("kilocode")
        state = self.state(kilo)
        state["settings"]["builder_retry"] = policy.configured("gpt-6-sol", kilo)
        policy.failure(state, "e1", "f")
        self.assertEqual("escalate", policy.failure(state, "e2", "f"))
        self.assertEqual("openai/gpt-6-sol", state["settings"]["roles"]["terra"]["model"])

    def test_a_tool_that_names_models_with_a_slash_keeps_the_old_policy_where_a_checker_cannot_be_proven_safe(self):
        # Before tools had their own kind a kilocode Builder escalated here: a pinned Validator stayed for the
        # cross-model guard to catch, and an unrouted GLM still took the Validator's place. It still does.
        kilo = command.load("kilocode")
        for name, change, validator in (
            ("pinned Validator", lambda roles: roles["sol"].update(model_pinned=True), "openai/gpt-6-sol"),
            (
                "checker model no role uses",
                lambda roles: roles["glm"].update(model="openai/gpt-5.6-sol"),
                "zai-coding-plan/glm-5.3",
            ),
        ):
            with self.subTest(name):
                state = self.state(kilo)
                roles = state["settings"]["roles"]
                roles["sol"]["model"] = "openai/gpt-6-sol"
                change(roles)
                policy.failure(state, "e1", "f")
                self.assertEqual("escalate", policy.failure(state, "e2", "f"))
                self.assertEqual(
                    ("openai/gpt-6-sol", "xhigh", validator),
                    (roles["terra"]["model"], roles["terra"]["reasoning_effort"], roles["sol"]["model"]),
                )


class PolicyBlackbox(unittest.TestCase):
    setUp = bb.BuildBlackbox.setUp
    command = bb.BuildBlackbox.command
    invoke = bb.BuildBlackbox.invoke
    seed = bb.BuildBlackbox.seed
    state = bb.BuildBlackbox.state
    events = bb.BuildBlackbox.events
    build = bb.BuildBlackbox.build
    candidate = bb.BuildBlackbox.candidate

    def test_ordinary_retry_keeps_luna_and_successful_siblings(self):
        self.seed()
        self.env["BUILD_AUDIT_FAULT"] = "retry_success"
        self.build()
        self.candidate()
        self.assertEqual(["gpt-6-luna"] * 2, [r["model"] for r in self.events() if r["milestone"] == "M1"])
        self.assertEqual(1, len([r for r in self.events() if r["milestone"] == "M2"]))

    def test_strong_retry_uses_sol_high_and_integration_still_requires_review(self):
        self.seed()
        self.env["BUILD_AUDIT_FAULT"] = "escalate_success"
        self.build()
        self.candidate()
        strong = "gpt-6-sol"
        self.assertEqual(
            ["gpt-6-luna", "gpt-6-luna", strong], [r["model"] for r in self.events() if r["milestone"] == "M1"]
        )
        self.assertNotEqual("TASK_COMPLETE", self.state()["status"])

    def after_parallel_pair(self):
        """M1 and M2 can be built in parallel; M3 needs both. The checkers run the strong model."""
        spec = bb.independent()
        spec["contract"]["milestones"][2]["depends_on"] = ["M1", "M2"]
        self.seed(spec, checker=policy.DEFAULTS["strong_model"])
        self.env["BUILD_AUDIT_FAULT"] = "escalate_success"
        return self.state()["settings"]["roles"]

    def review(self):
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])

    def models(self, milestone):
        return [e["model"] for e in self.events() if e["milestone"] == milestone]

    def checkers(self, stage):
        return [(e["milestone"], e["model"]) for e in self.events(stage=stage)]

    def test_parallel_strong_retry_runs_serially_after_its_siblings_and_another_model_checks_it(self):
        strong, glm = policy.DEFAULTS["strong_model"], policy.DEFAULTS["checker_model"]
        routes = self.after_parallel_pair()
        self.build()
        # M2 is integrated on its own; M1 left its stronger attempt to the parent run.
        self.assertEqual(1, len(self.candidate()["implementation"]["builder_reports"]))
        batch = self.state()["orchestration_history"][-1]
        self.assertEqual(["M1"], batch["deferred"])
        self.assertEqual(
            {"M1": policy.SERIAL, "M2": "BUILT"}, {w["milestone_id"]: w["status"] for w in batch["workers"]}
        )
        self.assertFalse((self.project / "server/health.py").exists())
        self.review()
        self.build()
        self.candidate()
        self.assertEqual(["gpt-6-luna", "gpt-6-luna", "gpt-6-sol"], self.models("M1"))
        self.assertEqual(
            ["investigate", "retry", "investigate", "defer", "escalate"],
            [d["action"] for d in self.state()["builder_retry_decisions"]],
        )
        self.review()
        self.build()
        self.candidate()
        self.assertEqual(["gpt-6-luna"], self.models("M3"))
        self.assertEqual(routes, self.state()["settings"]["roles"])
        self.review()
        # No model checked its own work: GPT-6 Sol checked Luna's builds, GLM checked GPT-6 Sol's.
        for stage in ("sol", "astra_review"):
            self.assertEqual([("M2", strong), ("M1", glm), ("M3", strong)], self.checkers(stage))

    def test_when_every_parallel_builder_defers_the_parent_builds_each_serially(self):
        strong, glm = "gpt-6-sol", policy.DEFAULTS["checker_model"]
        self.after_parallel_pair()
        self.env["BUILD_AUDIT_FAULT_MILESTONES"] = "M1,M2"
        self.build()
        self.candidate()
        batch = self.state()["orchestration_history"][-1]
        self.assertEqual(("DEFERRED", ["M1", "M2"]), (batch["status"], batch["deferred"]))
        self.assertEqual(
            (["gpt-6-luna", "gpt-6-luna", strong], ["gpt-6-luna"] * 2), (self.models("M1"), self.models("M2"))
        )
        self.review()
        self.build()
        self.candidate()
        self.assertEqual(["gpt-6-luna", "gpt-6-luna", strong], self.models("M2"))
        self.review()
        for stage in ("sol", "astra_review"):
            self.assertEqual([("M1", glm), ("M2", glm)], self.checkers(stage))

    def test_exhaustion_resume_cannot_reset_budget_or_claim_built(self):
        self.seed()
        self.env["BUILD_AUDIT_FAULT"] = "retry_exhausted"
        self.build(2)
        before = self.events()
        worker = next(w for w in self.state()["orchestration_batch"]["workers"] if w["milestone_id"] == "M1")
        child = json.loads((Path(worker["run_dir"]) / "state.json").read_text())
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", child["status"])
        self.assertEqual(
            ["investigate", "retry", "investigate", "escalate", "investigate", "pause"],
            [r["action"] for r in child["builder_retry_decisions"]],
        )
        self.assertNotIn("implementation", child)
        self.build(2, extra=["--resume-paused", "--retry-builder", "M1"])
        self.assertEqual(before, self.events())
        self.assertNotIn("autocode", self.state().get("unit_handoffs", {}))

    def test_independent_failure_resolver_retries_escalates_then_pauses(self):
        spec = bb.plan(
            [([], "MAX_RETRIES must be 3", ["retry.py"])],
            {"M1": {"retry.py": "MAX_RETRIES = 5\n"}},
            {"M1": "from retry import MAX_RETRIES; assert MAX_RETRIES == 3"},
            "Preserve exactly three retries",
        )
        # Isolate execution exhaustion from the separately covered three-review replan gate.
        self.seed(spec, planner_extra=["--max-milestone-stalled-reviews", "0"])
        status = json.loads(self.invoke("autocode", ["--run-dir", str(self.run), "--status"]).stdout)
        self.assertIsNone(status["milestone_checkpoint"]["limits"]["stalled_reviews"])
        self.assertEqual([], self.events(), "Configuration must not consume a Builder slot")
        self.env["BUILD_AUDIT_FAULT"] = "validation_fails"
        for attempt in range(3):
            # #2485: every no-progress hold — the Builder's and the Resolver's — is settled by
            # the invocation itself, so each round is one plain build that continues.
            self.build()
            self.candidate()
            self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])
            self.assertEqual("FAIL", self.state()["validation"]["verdict"])
            args = ["--run-dir", str(self.run), "--no-chat"]
            if attempt == 1:
                # #2485: the no-progress hold is AutoCode's own machinery; the invocation
                # settles it by taking the one scoped retry itself, so one call continues.
                args += ["--resume-paused"]
            self.invoke("autoresolver", args, 2 if attempt == 2 else 0)
        self.assertEqual(["gpt-6-luna", "gpt-6-luna", "gpt-6-sol"], [r["model"] for r in self.events()])
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", self.state()["status"])
        self.assertEqual(["retry", "escalate", "pause"], [r["action"] for r in self.state()["builder_retry_decisions"]])
        # The fake Tester fails the same check every round: the ledger sees the second and third as repeats.
        self.assertEqual(["new", "repeat", "repeat"], [r["novelty"] for r in self.state()["builder_retry_decisions"]])
        self.assertIn("no new evidence", self.state()["stop_reason"])
        self.assertEqual(2, len(self.events(stage="astra_resolve")))

    def test_a_stronger_builder_attempt_past_the_cost_cap_pauses_before_it_launches(self):
        # Issue #611: the climb consults the person's cost cap before it spends, and names the arithmetic.
        self.seed(
            bb.plan(
                [([], "Version prints 1", ["version.py"])],
                {"M1": {"version.py": "print(1)\n"}},
                {"M1": "import version"},
                "Print version",
            )
        )
        self.env["BUILD_AUDIT_FAULT"] = "retry_exhausted"
        for _attempt in range(2):
            self.build()
            self.invoke("autoresolver", ["--run-dir", str(self.run), "--no-chat"])
        self.assertEqual(
            ["investigate", "retry", "investigate", "escalate"],
            [r["action"] for r in self.state()["builder_retry_decisions"]],
        )
        # Every fake stage reports 10 input and 10 output tokens: the stronger attempt on GPT-6 Sol is
        # projected at (10 x $2 + 10 x $10) / 1M = $0.00012. A cap halfway there admits the spend so far only.
        spent = self.view()["cost"]["spent_usd"]
        cap = round(spent + 0.00006, 6)
        before = self.events()
        self.build(2, extra=["--max-cost-usd", str(cap)])
        self.assertEqual(before, self.events(), "the stronger attempt must not launch past the cap")
        view = self.view()
        self.assertEqual("PAUSED_COST_LIMIT", view["needs"].get("pause_origin"), view["needs"])
        for part in (
            "The Builder's stronger attempt (gpt-6-sol at xhigh effort) did not launch: projected at $0.00012",
            f"from {cost.usd(spent)} to {cost.usd(spent + 0.00012)}, past the {cost.usd(cap)} cost cap",
            "resume with --max-cost-usd 0.01 or more",
        ):
            self.assertIn(part, view["stop_reason"])
        self.build(extra=["--resume-paused", "--max-cost-usd", "0.01"])
        self.assertEqual(["gpt-6-luna", "gpt-6-luna", "gpt-6-sol"], [r["model"] for r in self.events()])

    def test_a_stronger_builder_attempt_past_the_time_budget_pauses_before_it_launches(self):
        # Issue #611: the climb consults the active-time budget you set before it spends, and names the arithmetic.
        self.seed(
            bb.plan(
                [([], "Version prints 1", ["version.py"])],
                {"M1": {"version.py": "print(1)\n"}},
                {"M1": "import version"},
                "Print version",
            )
        )
        self.env["BUILD_AUDIT_FAULT"] = "retry_exhausted"
        self.build()
        self.invoke("autoresolver", ["--run-dir", str(self.run), "--no-chat"])
        self.build()
        self.invoke("autoresolver", ["--run-dir", str(self.run), "--no-chat", "--pause-after-stage"], 2)
        self.assertEqual(
            ["investigate", "retry", "investigate", "escalate"],
            [r["action"] for r in self.state()["builder_retry_decisions"]],
        )
        # The fake clock, at that pause (a running checkpoint changed after its controller's receipt reads as a
        # controller that ended): the last GPT-6 Luna attempt took 600 s, in its duration and the run's active
        # time, so the stronger attempt is projected at that long. A 1,000-second budget admits the active time
        # so far but not both.
        path = self.run / "state.json"
        saved = json.loads(path.read_text())
        last = [row for row in saved["stages"] if row["stage"] == "terra"][-1]
        last["duration_seconds"] += 600
        saved["active_seconds"] += 600
        path.write_text(json.dumps(saved, indent=2))
        needed = math.ceil(saved["active_seconds"] + last["duration_seconds"])
        before = self.events()
        self.build(2, extra=["--resume-paused", "--max-seconds", "1000"])
        self.assertEqual(before, self.events(), "the stronger attempt must not launch past the budget")
        view = self.view()
        self.assertEqual("PAUSED_TIME_LIMIT", view["needs"].get("pause_origin"), view["needs"])
        for part in (
            "The Builder's stronger attempt (gpt-6-sol at xhigh effort) did not launch: projected at",
            "of active time, the last Builder attempt's duration",
            "past the 1,000 s active-time budget you set",
            f"resume with --max-seconds {needed} or more",
        ):
            self.assertIn(part, view["stop_reason"])
        # A budget one second below the total the stop names is accepted and saved, but the run pauses again before
        # the same attempt: the new stop projects it against that budget, under a new request. A resume refused
        # when its arguments are read also exits 2, but leaves the stop and its request as they were.
        request = view["needs"].get("resolver_request_id")
        self.build(2, extra=["--resume-paused", "--max-seconds", str(needed - 1)])
        self.assertEqual(before, self.events(), "the stronger attempt must not launch past the smaller budget")
        view = self.view()
        self.assertEqual("PAUSED_TIME_LIMIT", view["needs"].get("pause_origin"), view["needs"])
        for part in (f"past the {needed - 1:,} s active-time budget you set", f"resume with --max-seconds {needed} or"):
            self.assertIn(part, view["stop_reason"])
        self.assertNotEqual(request, view["needs"].get("resolver_request_id"), "the resume must reach the new stop")
        self.build(extra=["--resume-paused", "--max-seconds", str(needed)])
        self.assertEqual(["gpt-6-luna", "gpt-6-luna", "gpt-6-sol"], [r["model"] for r in self.events()])

    def test_a_stronger_builder_attempt_past_the_milestone_budget_pauses_before_it_launches(self):
        # Issue #611: the climb consults the milestone budget you set before it spends, and names the arithmetic.
        self.seed(
            bb.plan(
                [([], "Version prints 1", ["version.py"])],
                {"M1": {"version.py": "print(1)\n"}},
                {"M1": "import version"},
                "Print version",
            ),
            planner_extra=["--max-milestone-seconds", "1000"],
        )
        self.env["BUILD_AUDIT_FAULT"] = "retry_exhausted"
        self.build()
        self.invoke("autoresolver", ["--run-dir", str(self.run), "--no-chat"])
        self.build()
        self.invoke("autoresolver", ["--run-dir", str(self.run), "--no-chat", "--pause-after-stage"], 2)
        self.assertEqual(
            ["investigate", "retry", "investigate", "escalate"],
            [r["action"] for r in self.state()["builder_retry_decisions"]],
        )
        # The fake clock, at that pause: the last GPT-6 Luna attempt took 600 s, in its duration and milestone M1's
        # counted time, so the stronger attempt is projected at that long. The 1,000-second milestone budget admits
        # the milestone's time so far, and the attempt alone, but not both.
        path = self.run / "state.json"
        saved = json.loads(path.read_text())
        last = [row for row in saved["stages"] if row["stage"] == "terra"][-1]
        last["duration_seconds"] += 600
        row = saved["milestone_progress"][f"{saved['goal_contract']['hash']}:M1"]
        row["seconds"] += 600
        path.write_text(json.dumps(saved, indent=2))
        needed = math.ceil(row["seconds"] + last["duration_seconds"])
        self.assertLess(max(row["seconds"], last["duration_seconds"]), 1000)
        self.assertGreater(needed, 1000)
        before = self.events()
        self.build(2, extra=["--resume-paused"])
        self.assertEqual(before, self.events(), "the stronger attempt must not launch past the milestone budget")
        view = self.view()
        self.assertEqual("PAUSED_MILESTONE_BUDGET", view["needs"].get("pause_origin"), view["needs"])
        for part in (
            "The Builder's stronger attempt (gpt-6-sol at xhigh effort) did not launch: projected at",
            "of active time, the last Builder attempt's duration, it would take this milestone's active time from",
            "past the 1,000 s milestone budget you set",
            f"resume with --max-milestone-seconds {needed} or more (0 for no limit), which also restarts this "
            "milestone's count",
            "To acknowledge this milestone-budget pause, use autocode resume --max-milestone-seconds N",
        ):
            self.assertIn(part, view["stop_reason"])
        # Information cannot release this stop, so its advice never offers that path.
        self.assertNotIn("provide_information", view["stop_reason"])
        # The climb stays saved: the budget the stop names launches exactly that attempt.
        self.build(extra=["--resume-paused", "--max-milestone-seconds", str(needed)])
        self.assertEqual(["gpt-6-luna", "gpt-6-luna", "gpt-6-sol"], [r["model"] for r in self.events()])

    def view(self):
        return json.loads(self.invoke("autocode", ["--run-dir", str(self.run), "--status"]).stdout)["view"]

    def test_serial_noop_uses_same_bounded_policy(self):
        self.seed(
            bb.plan(
                [([], "Version prints 1", ["version.py"])],
                {"M1": {"version.py": "print(1)\n"}},
                {"M1": "import version"},
                "Print version",
            )
        )
        self.env["BUILD_AUDIT_FAULT"] = "retry_exhausted"
        for attempt in range(3):
            self.build()
            self.assertEqual("investigate_stuck", self.state()["next_stage"])
            self.assertNotIn("implementation", self.state())
            self.invoke("autoresolver", ["--run-dir", str(self.run), "--no-chat"], 2 if attempt == 2 else 0)
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", self.state()["status"])
        self.assertEqual(["gpt-6-luna", "gpt-6-luna", "gpt-6-sol"], [r["model"] for r in self.events()])
        self.assertNotIn("implementation", self.state())
        self.assertNotIn("autocode", self.state().get("unit_handoffs", {}))
