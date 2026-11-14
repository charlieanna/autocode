"""Automatic model-stop recovery across permitted, configured, and subscription routes.

When a stage, report repair, workflow job, or parallel Builder worker hits a model stop
(``PAUSED_BUDGET``, ``PAUSED_CONTENT_FILTER``, ``PAUSED_CONTEXT_WINDOW``, ``PAUSED_OUTPUT_CAP``),
AutoCode switches to the next eligible model on the same backend — preserving cross-model
verifier independence across both configured routes and already-produced work — and pauses
to ask the person only when no eligible model remains, automatic recoveries are spent, or
the run opted out (``--single-model`` or ``--no-auto-select``).
"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_quota_route as quota_route
import autocode_recovery_dispatch as dispatch
import autocode_resolver_recovery as resolver_recovery
import autocode_route_pool as route_pool
import autocode_route_recovery as route_recovery
import autocode_route_selector as selector
import autocode_support as support
import autocode_verifier_independence as independence

QUOTA = {"type": "error", "error": {"message": "subscription usage limit reached"}}
REFUSAL = {"type": "error", "error": {"name": "ContentFilterError", "message": "blocked by content filter"}}
GLM = "zai-coding-plan/glm-5.3"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
MIMO_ALT = "mimo-token-plan/mimo-v2.6-pro"
SOL = "openai/gpt-6-sol"
LUNA = "openai/gpt-6-luna"
ASTRA = "openai/gpt-6-astra"
OPUS = "anthropic/claude-opus-5-5"


def accounts():
    return [
        {"model": m, "tool": "opencode", "account": a, "billing": "subscription", "levels": None, "probe": "known"}
        for m, a in (
            (GLM, "Z.AI Coding Plan"),
            (MIMO, "Xiaomi Token Plan (Singapore)"),
            (SOL, "ChatGPT login"),
            (ASTRA, "ChatGPT login"),
        )
    ]


class RouteRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

        def abandon(state, run_dir, workspace, attempt, **kwargs):
            record = state.pop("active_stage")
            if record.get("report_only") and "pending_report_repair" in state:
                state.pop("pending_report_repair", None)
            state.setdefault("stages", []).append({**record, "abandoned": True})
            state["status"] = "PAUSED_STAGE_ABANDONED"

        self.runtime = SimpleNamespace(
            now=lambda: "2026-10-17T00:00:00Z",
            native_calls=None,
            automatically_recover_truncated_review=mock.Mock(return_value=False),
            timeout_recovery_guard=mock.Mock(),
            count_automatic_recovery=mock.Mock(),
            abandon_stage=mock.Mock(side_effect=abandon),
            write_json=lambda path, value: path.write_text(json.dumps(value)),
        )

    def make_events(self, error=QUOTA, name="validator-01.jsonl"):
        events = self.root / name
        events.write_text(json.dumps({"type": "thread.started", "thread_id": "t"}) + "\n" + json.dumps(error) + "\n")
        return events

    def state(self, error=QUOTA, *, role="builder"):
        events = self.make_events(error)
        result = selector.select(selector.PIPELINE_ROLES, accounts())
        result["candidates"] = selector.permitted_set(accounts())
        self.assertEqual(
            (SOL, GLM, GLM), tuple(result["routes"][r]["model"] for r in ("builder", "tester", "completion"))
        )
        return {
            "status": "RUNNING",
            "settings": {
                "engine": "opencode",
                "roles": {
                    role_: {"model": route["model"], "reasoning_effort": route["reasoning_effort"]}
                    for role_, route in result["routes"].items()
                },
                "route_selection": result,
            },
            "sessions": {role: "old-session"},
            "active_stage": {
                "stage": "review_completion" if role == "tester" else "builder",
                "role": role,
                "route_role": role,
                "iteration": 1,
                "output": str(self.root / "validator-01.json"),
                "events": str(events),
            },
        }

    def test_a_dead_builder_switches_on_its_own_and_the_pause_never_happens(self):
        state = self.state()
        error = support.Paused("PAUSED_BUDGET", "quota")
        self.assertTrue(route_recovery.recover(self.runtime, state, self.root, self.root, error))
        self.assertEqual(MIMO, state["settings"]["roles"]["builder"]["model"])
        self.assertNotIn("builder", state.get("sessions") or {})
        (record,) = [row for row in state["user_events"] if row["kind"] == "route_assignment"]
        self.assertEqual("runner", record["actor"])
        self.assertEqual(SOL, record["from"])
        self.assertEqual(MIMO, record["to"])
        self.assertEqual("quota_assigned", state["settings"]["route_selection"]["routes"]["builder"]["origin"])

    def test_a_switch_onto_another_way_of_paying_is_recorded_and_said(self):
        state = self.state()
        for candidates in state["settings"]["route_selection"]["candidates"].values():
            for candidate in candidates:
                if candidate["model"] == MIMO:
                    candidate["billing"] = "pay per token"
        with mock.patch("builtins.print") as printed:
            self.assertTrue(
                route_recovery.recover(
                    self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota")
                )
            )
        (record,) = [row for row in state["user_events"] if row["kind"] == "route_assignment"]
        self.assertEqual({"from": "subscription", "to": "pay per token"}, record["billing"])
        self.assertIn(f"it bills pay per token where {SOL} was subscription", printed.call_args.args[0])

    def test_a_switch_on_the_same_way_of_paying_adds_no_billing_note(self):
        state = self.state()
        self.assertTrue(
            route_recovery.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota"))
        )
        (record,) = [row for row in state["user_events"] if row["kind"] == "route_assignment"]
        self.assertNotIn("billing", record)

    def test_replacement_checks_actual_routes_after_catalogue_substitution(self):
        # When Tester uses MiMo, Completion uses GLM, and Builder already produced work on both MiMo and GLM,
        # moving the stopped Sol Builder to MiMo or GLM would let a checker grade its own family's work.
        state = self.state()
        state["settings"]["roles"]["tester"]["model"] = MIMO
        state["stages"] = [
            {"stage": "builder", "launch_route": {"model": MIMO}, "changed_files": ["a.py"]},
            {"stage": "builder", "launch_route": {"model": GLM}, "changed_files": ["b.py"]},
        ]
        before = json.dumps(state, sort_keys=True)
        self.assertFalse(
            route_recovery.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota"))
        )
        self.assertEqual(before, json.dumps(state, sort_keys=True))
        self.runtime.abandon_stage.assert_not_called()
        self.runtime.count_automatic_recovery.assert_not_called()

        # Without prior edits, Tier 1 still refuses MiMo alone and Tier 2b atomically moves
        # Builder -> GLM and Completion -> MiMo so cross-model independence holds.
        paired = self.state()
        paired["settings"]["roles"]["tester"]["model"] = MIMO
        self.assertTrue(
            route_recovery.recover(self.runtime, paired, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota"))
        )
        self.assertEqual([], independence.violations(paired["settings"]))
        self.assertEqual(GLM, paired["settings"]["roles"]["builder"]["model"])
        self.assertEqual(MIMO, paired["settings"]["roles"]["tester"]["model"])
        self.assertEqual(MIMO, paired["settings"]["roles"]["completion"]["model"])

    def test_exhausted_alternatives_pause_even_after_a_restart(self):
        state = self.state()
        error = support.Paused("PAUSED_BUDGET", "quota")
        attempt = dict(state["active_stage"])
        self.assertTrue(route_recovery.recover(self.runtime, state, self.root, self.root, error))
        self.runtime.abandon_stage.assert_called_once()
        saved = json.loads((self.root / "state.json").read_text())
        self.assertNotIn("active_stage", saved)
        self.assertEqual("RUNNING", saved["status"])
        saved["active_stage"] = {**attempt, "iteration": 2, "command": ["opencode", "--model", MIMO]}
        # Both OpenAI and Xiaomi accounts are now quota-exhausted; moving Builder to GLM has no live checker account.
        self.assertFalse(route_recovery.recover(self.runtime, saved, self.root, self.root, error))
        self.assertEqual(MIMO, saved["settings"]["roles"]["builder"]["model"])
        self.runtime.abandon_stage.assert_called_once()

    def test_real_abandonment_retains_evidence_and_accounts_before_retry(self):
        import autocode_recovery_accounting as accounting
        import autocode_stage_recovery as stage_recovery

        state = self.state()
        state.update(task="Build the change", workspace=str(self.root))
        state["active_stage"]["duration_seconds"] = 7
        before = self.root / "validator-01.before.json"
        before.write_text(json.dumps({"revision": "before", "files": {}}))
        state["active_stage"]["before_ref"] = str(before)
        partial = self.root / "partial.txt"
        partial.write_text("keep this work")
        self.runtime.abandon_stage = stage_recovery.abandon_stage
        self.runtime.count_automatic_recovery = accounting.count
        self.runtime.write_json = stage_recovery.records.write_json
        output, log = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(
                stage_recovery.source_scope,
                "snapshot",
                return_value={"revision": "after", "files": {"partial.txt": "hash"}},
            ),
            redirect_stdout(output),
            redirect_stderr(log),
        ):
            self.assertTrue(
                route_recovery.recover(
                    self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota")
                )
            )
        saved = json.loads((self.root / "state.json").read_text())
        self.assertEqual("RUNNING", saved["status"])
        self.assertNotIn("active_stage", saved)
        self.assertEqual(7, saved["active_seconds"])
        self.assertEqual(1, accounting.spent(saved))
        self.assertEqual("keep this work", partial.read_text())
        self.assertTrue(Path(saved["stages"][-1]["events"]).is_file())
        self.assertEqual(["partial.txt"], saved["stages"][-1]["changed_files"])
        self.assertEqual("runner", saved["user_events"][-1]["actor"])
        self.assertEqual("builder", saved["next_stage"])
        self.assertIn("set aside for automatic recovery", log.getvalue())
        self.assertIn(f"switched to {MIMO}", output.getvalue())
        self.assertNotIn("Resume explicitly", log.getvalue() + output.getvalue())
        self.assertNotIn("Paused Stage Abandoned", log.getvalue() + output.getvalue())

    def test_a_live_attempt_cannot_change_the_route(self):
        state = self.state()
        before = json.dumps(state, sort_keys=True)
        self.runtime.abandon_stage.side_effect = support.Paused("PAUSED_WORKSPACE_BUSY", "still running")
        with self.assertRaises(support.Paused):
            route_recovery.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota"))
        self.assertEqual(before, json.dumps(state, sort_keys=True))

    def test_when_nothing_remains_the_pause_stands(self):
        # Only GLM and OpenAI exist, and Builder already produced work on SOL: a quota-stopped GLM Tester
        # cannot switch onto SOL/ASTRA without grading SOL's work, so the pause stands.
        two_accounts = [row for row in accounts() if row["model"] != MIMO]
        events = self.make_events(QUOTA)
        result = selector.select(selector.PIPELINE_ROLES, two_accounts)
        result["candidates"] = selector.permitted_set(two_accounts)
        state = {
            "status": "RUNNING",
            "settings": {
                "engine": "opencode",
                "roles": {
                    r: {"model": route["model"], "reasoning_effort": route["reasoning_effort"]}
                    for r, route in result["routes"].items()
                },
                "route_selection": result,
            },
            "sessions": {"tester": "old-session"},
            "stages": [{"stage": "builder", "launch_route": {"model": SOL}, "changed_files": ["app.py"]}],
            "active_stage": {
                "stage": "tester",
                "role": "tester",
                "route_role": "tester",
                "iteration": 1,
                "output": str(self.root / "tester-01.json"),
                "events": str(events),
            },
        }
        error = support.Paused("PAUSED_BUDGET", "quota")
        self.assertFalse(route_recovery.recover(self.runtime, state, self.root, self.root, error))
        self.assertEqual(GLM, state["settings"]["roles"]["tester"]["model"])
        self.assertNotIn("user_events", state)

    def test_explicit_role_flags_switch_when_blocked_while_opt_out_flags_hold(self):
        # Explicit --builder-model (USER_EXPLICIT) switches away when blocked.
        state = self.state()
        state["settings"]["route_selection"]["routes"]["builder"]["origin"] = selector.role_route.USER_EXPLICIT
        self.assertTrue(
            route_recovery.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota"))
        )
        self.assertEqual(MIMO, state["settings"]["roles"]["builder"]["model"])

        # --no-auto-select and --single-model keep the pause standing.
        for key in ("auto_select_disabled", "single_model_mode"):
            opted_out = self.state()
            opted_out["settings"][key] = True
            before = json.dumps(opted_out, sort_keys=True)
            self.assertFalse(
                route_recovery.recover(
                    self.runtime, opted_out, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota")
                )
            )
            self.assertEqual(before, json.dumps(opted_out, sort_keys=True))

    def test_a_run_without_a_permitted_set_switches_within_configured_roles(self):
        state = self.state()
        del state["settings"]["route_selection"]
        state["settings"]["roles"]["requirements"] = {"model": MIMO, "reasoning_effort": "medium"}
        self.assertTrue(
            route_recovery.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota"))
        )
        self.assertEqual(MIMO, state["settings"]["roles"]["builder"]["model"])

    def test_two_account_pair_swap_and_swap_cycle_guard(self):
        # Live two-account setup (e.g. GLM Builder + MiMo Tester/Completion, no route_selection):
        # content filter on GLM swaps Builder -> MiMo and Tester/Completion -> GLM atomically,
        # and refuses to cycle back if MiMo also hits content filter.
        events = self.make_events(REFUSAL)
        state = {
            "status": "RUNNING",
            "phase": "IMPLEMENTING",
            "settings": {
                "engine": "opencode",
                "roles": {
                    "requirements": {"model": GLM, "reasoning_effort": "medium"},
                    "planner": {"model": GLM, "reasoning_effort": "high"},
                    "plan_reviewer": {"model": OPUS, "reasoning_effort": "high"},
                    "builder": {"model": GLM, "reasoning_effort": "medium"},
                    "tester": {"model": MIMO, "reasoning_effort": "medium"},
                    "completion": {"model": MIMO, "reasoning_effort": "medium"},
                    "task_planner": {"model": GLM, "reasoning_effort": "high"},
                },
            },
            "sessions": {"builder": "s1", "tester": "s2"},
            "active_stage": {
                "stage": "builder",
                "role": "builder",
                "route_role": "builder",
                "iteration": 1,
                "output": str(self.root / "builder-01.json"),
                "events": str(events),
            },
        }
        # Restrict configured roles to just GLM and MIMO to prove pure 2-account swap + cycle prevention.
        state["settings"]["roles"]["plan_reviewer"]["model"] = SOL
        del state["settings"]["roles"]["plan_reviewer"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertTrue(
                route_recovery.recover(
                    self.runtime, state, self.root, self.root, support.Paused("PAUSED_CONTENT_FILTER", "refused")
                )
            )
        self.assertIn("paired swap:", buf.getvalue())
        self.assertEqual(MIMO, state["settings"]["roles"]["builder"]["model"])
        self.assertEqual(GLM, state["settings"]["roles"]["tester"]["model"])
        self.assertEqual(GLM, state["settings"]["roles"]["completion"]["model"])
        self.assertEqual([], independence.violations(state["settings"]))
        assignments = [e for e in state["user_events"] if e["kind"] == "route_assignment"]
        self.assertEqual(3, len(assignments))
        self.assertTrue(assignments[1]["paired_partner"])
        self.assertEqual("builder", assignments[1]["paired_with"])

        # Second refusal on MIMO must not swap back to the already-seen (GLM, MIMO, MIMO) signature.
        events2 = self.make_events(REFUSAL, "builder-02.jsonl")
        state["active_stage"] = {
            "stage": "builder",
            "role": "builder",
            "route_role": "builder",
            "iteration": 2,
            "launch_route": {"model": MIMO},
            "output": str(self.root / "builder-02.json"),
            "events": str(events2),
        }
        self.assertFalse(
            route_recovery.recover(
                self.runtime, state, self.root, self.root, support.Paused("PAUSED_CONTENT_FILTER", "refused again")
            )
        )

    def test_verifier_and_planning_pair_swaps_and_mimo_reviewer_exclusion(self):
        # 1. Tester stop before Builder edits swaps Tester -> SOL, Builder -> GLM, Completion -> SOL.
        events = self.make_events(REFUSAL)
        tester_state = {
            "status": "RUNNING",
            "settings": {
                "engine": "opencode",
                "roles": {
                    "builder": {"model": SOL, "reasoning_effort": "medium"},
                    "tester": {"model": GLM, "reasoning_effort": "medium"},
                    "completion": {"model": GLM, "reasoning_effort": "medium"},
                },
            },
            "sessions": {},
            "active_stage": {
                "stage": "tester",
                "role": "tester",
                "route_role": "tester",
                "iteration": 1,
                "output": str(self.root / "t.json"),
                "events": str(events),
            },
        }
        self.assertTrue(
            route_recovery.recover(
                self.runtime, tester_state, self.root, self.root, support.Paused("PAUSED_CONTENT_FILTER", "refused")
            )
        )
        self.assertEqual(
            (GLM, SOL, SOL),
            tuple(tester_state["settings"]["roles"][r]["model"] for r in ("builder", "tester", "completion")),
        )

        # 2. Planner <-> Plan Reviewer swap succeeds with Opus/Sol, and never puts MiMo on plan_reviewer.
        plan_state = {
            "status": "RUNNING",
            "phase": "PLANNING",
            "settings": {
                "engine": "opencode",
                "roles": {
                    "planner": {"model": GLM, "reasoning_effort": "high"},
                    "plan_reviewer": {"model": SOL, "reasoning_effort": "high"},
                    "requirements": {"model": MIMO, "reasoning_effort": "medium"},
                },
            },
            "sessions": {},
            "active_stage": {
                "stage": "draft_plan",
                "role": "task_planner",
                "route_role": "planner",
                "iteration": 1,
                "output": str(self.root / "p.json"),
                "events": str(events),
            },
        }
        # Planner first switches single-role to MIMO without touching plan_reviewer (SOL);
        # if only SOL and MIMO are configured and Planner on SOL refuses, MiMo is blocked as plan_reviewer!
        mimo_only = {
            "status": "RUNNING",
            "phase": "PLANNING",
            "settings": {
                "engine": "opencode",
                "roles": {
                    "planner": {"model": SOL, "reasoning_effort": "high"},
                    "plan_reviewer": {"model": OPUS, "reasoning_effort": "high"},
                    "requirements": {"model": MIMO, "reasoning_effort": "medium"},
                },
            },
            "sessions": {},
            "active_stage": dict(plan_state["active_stage"]),
        }
        # Force paired swap by removing requirements MIMO and checking SOL <-> OPUS swap:
        del mimo_only["settings"]["roles"]["requirements"]
        self.assertTrue(
            route_recovery.recover(
                self.runtime, mimo_only, self.root, self.root, support.Paused("PAUSED_CONTENT_FILTER", "refused")
            )
        )
        self.assertEqual(
            (OPUS, SOL),
            (
                mimo_only["settings"]["roles"]["planner"]["model"],
                mimo_only["settings"]["roles"]["plan_reviewer"]["model"],
            ),
        )

        # Verify plan_reviewer never accepts MiMo even in paired swap.
        bad_pr = {
            "status": "RUNNING",
            "phase": "PLANNING",
            "settings": {
                "engine": "opencode",
                "roles": {
                    "planner": {"model": MIMO, "reasoning_effort": "high"},
                    "plan_reviewer": {"model": SOL, "reasoning_effort": "high"},
                },
            },
            "sessions": {},
            "active_stage": {
                "stage": "review_plan",
                "role": "plan_reviewer",
                "route_role": "plan_reviewer",
                "iteration": 1,
                "output": str(self.root / "pr.json"),
                "events": str(events),
            },
        }
        self.assertFalse(
            route_recovery.recover(
                self.runtime, bad_pr, self.root, self.root, support.Paused("PAUSED_CONTENT_FILTER", "refused")
            )
        )

    def test_verifier_history_blocks_swapping_checkers_onto_already_produced_work(self):
        # 1. Active Builder stage with live workspace edits via before_ref blocks moving Tester onto GLM.
        before_file = self.root / "builder.before.json"
        before_file.write_text(json.dumps({"revision": "r0", "files": {}}))
        (self.root / "edited.py").write_text("x = 1\n")
        state = {
            "settings": {
                "engine": "opencode",
                "roles": {
                    "builder": {"model": GLM},
                    "tester": {"model": MIMO},
                    "completion": {"model": MIMO},
                },
            },
            "active_stage": {
                "stage": "builder",
                "role": "builder",
                "before_ref": str(before_file),
                "launch_route": {"model": GLM},
            },
        }
        self.assertFalse(
            route_pool.verifier_history_ok(
                state, self.root, {"builder": MIMO, "tester": GLM, "completion": GLM}, state["settings"]
            )
        )
        # Corrupted before_ref conservatively assumes edited=True.
        before_file.write_text("{not-json")
        self.assertFalse(
            route_pool.verifier_history_ok(
                state, self.root, {"builder": MIMO, "tester": GLM, "completion": GLM}, state["settings"]
            )
        )

        # 2. Unapproved goal_contract drafted by GLM blocks moving plan_reviewer onto GLM; once approved, allowed.
        plan_state = {
            "goal_contract": {"approved": False},
            "stages": [{"stage": "draft_plan", "launch_route": {"model": GLM}}],
            "settings": {
                "engine": "opencode",
                "roles": {"planner": {"model": GLM}, "plan_reviewer": {"model": SOL}},
            },
        }
        self.assertFalse(
            route_pool.verifier_history_ok(
                plan_state, self.root, {"planner": SOL, "plan_reviewer": GLM}, plan_state["settings"]
            )
        )
        plan_state["goal_contract"]["approved"] = True
        self.assertTrue(
            route_pool.verifier_history_ok(
                plan_state, self.root, {"planner": SOL, "plan_reviewer": GLM}, plan_state["settings"]
            )
        )

    def test_vendor_and_account_keys_across_gateways_and_single_vendor_engines(self):
        # 3-part gateway routes separate upstream vendors on content filter.
        self.assertEqual("openrouter/z-ai", route_pool.vendor_key("openrouter/z-ai/glm-5-turbo", "opencode", {}))
        self.assertEqual("openrouter/xiaomi", route_pool.vendor_key("openrouter/xiaomi/mimo-v2-pro", "opencode", {}))
        # Both Xiaomi prefixes normalize to one quota account.
        self.assertEqual(
            route_pool.account_key(MIMO, "opencode", {}),
            route_pool.account_key(MIMO_ALT, "opencode", {}),
        )
        self.assertEqual("codex:openai", route_pool.account_key("gpt-6-sol", "codex", {"engine": "codex"}))
        self.assertEqual("claude:anthropic", route_pool.account_key("sonnet", "opencode", {"provider": "claude"}))
        self.assertEqual("opencode:default", route_pool.account_key("", "opencode", {}))

        # Gateway refusal rejects same-vendor GLM sibling and chooses cross-vendor MiMo.
        gw_state = {
            "settings": {
                "engine": "opencode",
                "roles": {
                    "requirements": {"model": "openrouter/z-ai/glm-5-turbo"},
                    "planner": {"model": "openrouter/z-ai/glm-5.3"},
                    "task_planner": {"model": "openrouter/xiaomi/mimo-v2-pro"},
                },
            }
        }
        chosen = route_pool.choose(
            self.runtime,
            gw_state,
            self.root,
            "requirements",
            "openrouter/z-ai/glm-5-turbo",
            "PAUSED_CONTENT_FILTER",
        )
        self.assertEqual("openrouter/xiaomi/mimo-v2-pro", chosen["primary"]["model"])

        # Codex single-vendor engine blocks PAUSED_BUDGET across same login, but allows PAUSED_CONTENT_FILTER.
        codex_state = {
            "settings": {
                "engine": "codex",
                "roles": {
                    "builder": {"model": "gpt-6-luna"},
                    "tester": {"model": "gpt-6-sol"},
                    "completion": {"model": "gpt-6-sol"},
                    "task_planner": {"model": "gpt-6-astra"},
                },
            }
        }
        self.assertIsNone(
            route_pool.choose(self.runtime, codex_state, self.root, "builder", "gpt-6-luna", "PAUSED_BUDGET")
        )
        codex_cf = route_pool.choose(
            self.runtime, codex_state, self.root, "builder", "gpt-6-luna", "PAUSED_CONTENT_FILTER"
        )
        self.assertEqual("gpt-6-astra", codex_cf["primary"]["model"])

    def test_tier3_subscription_pool_orders_saved_choices_ignores_pay_per_token_and_checks_models(self):
        stages_toml = self.root / "stages.toml"
        stages_toml.write_text(
            '[opencode.requirements]\nmodel = "xiaomi-token-plan-sgp/mimo-v2.6-pro"\n'
            '[opencode.builder]\nmodel = "openai/gpt-6-luna"\n'
            '[codex.task_planner]\nmodel = "gpt-6-astra"\n'
        )
        offers = [
            {
                "model": "kilo/deepseek/deepseek-v4.1-flash",
                "billing": "pay per token",
                "account": "Kilo",
                "levels": ["low"],
                "levels_listed": True,
            },
            {
                "model": SOL,
                "billing": "subscription",
                "account": "ChatGPT",
                "levels": ["low", "medium"],
                "levels_listed": True,
            },
            {
                "model": MIMO,
                "billing": "subscription",
                "account": "Xiaomi",
                "levels": ["low", "medium"],
                "levels_listed": True,
            },
        ]
        state = {
            "settings": {
                "engine": "opencode",
                "roles": {"requirements": {"model": GLM, "reasoning_effort": "high"}},
            }
        }
        plan = route_pool.choose(
            self.runtime,
            state,
            self.root,
            "requirements",
            GLM,
            "PAUSED_BUDGET",
            offers_fn=lambda backend, ws: offers,
            stages_file=stages_toml,
        )
        self.assertEqual("connected_subscription", plan["tier"])
        self.assertEqual(MIMO, plan["primary"]["model"])
        self.assertEqual(["low", "medium"], plan["primary"]["levels"])

        # Pay-per-token alone is never auto-selected from unconfigured offers.
        ppt_only = [offers[0]]
        self.assertIsNone(
            route_pool.choose(
                self.runtime,
                state,
                self.root,
                "requirements",
                GLM,
                "PAUSED_BUDGET",
                offers_fn=lambda backend, ws: ppt_only,
                stages_file=stages_toml,
            )
        )

        def reject_sol(mapping, ws):
            if any(entry.get("model") == SOL for entry in mapping.values()):
                raise RuntimeError("Models unavailable in OpenCode: " + SOL)

        facade = SimpleNamespace(check_models=reject_sol, CONFIGURED=False)
        opencode_runtime = SimpleNamespace(**self.runtime.__dict__, opencode=facade)
        two_role = {
            "settings": {
                "engine": "opencode",
                "roles": {
                    "builder": {"model": GLM, "reasoning_effort": "high"},
                    "tester": {"model": OPUS, "reasoning_effort": "high"},
                },
            }
        }
        with (
            mock.patch.object(route_pool.role_transport, "adapter", return_value=facade),
            mock.patch.object(route_pool.stage_choices, "offers", return_value=offers),
        ):
            # SOL is rejected by facade.check_models, so MIMO is chosen!
            chosen = route_pool.choose(
                opencode_runtime, two_role, self.root, "builder", GLM, "PAUSED_BUDGET", stages_file=stages_toml
            )
            self.assertEqual(MIMO, chosen["primary"]["model"])

        # Codex Tier 3 reads stages.toml without calling stage_choices.offers.
        codex_state = {
            "settings": {
                "engine": "codex",
                "roles": {"architect": {"model": "gpt-6-sol"}},
            }
        }
        codex_plan = route_pool.choose(
            self.runtime,
            codex_state,
            self.root,
            "architect",
            "gpt-6-sol",
            "PAUSED_OUTPUT_CAP",
            workflow_job=True,
            stages_file=stages_toml,
        )
        self.assertEqual("gpt-6-astra", codex_plan["primary"]["model"])

    def test_report_repair_model_stop_restores_planning_and_implementing_phases(self):
        # Live run 1ea9fa04: draft_plan_report_repair in phase REPORT_REPAIR hits PAUSED_CONTENT_FILTER.
        events = self.make_events(REFUSAL, "draft_plan_report_repair-01.jsonl")
        state = {
            "status": "RUNNING",
            "phase": "REPORT_REPAIR",
            "next_stage": "draft_plan",
            "pending_report_repair": {"original": {"stage": "draft_plan"}, "attempts": 1},
            "settings": {
                "engine": "opencode",
                "roles": {
                    "planner": {"model": GLM, "reasoning_effort": "high"},
                    "plan_reviewer": {"model": SOL, "reasoning_effort": "high"},
                    "requirements": {"model": MIMO, "reasoning_effort": "medium"},
                },
            },
            "sessions": {},
            "active_stage": {
                "stage": "draft_plan_report_repair",
                "original_stage": "draft_plan",
                "report_only": True,
                "role": "task_planner",
                "route_role": "planner",
                "iteration": 1,
                "output": str(self.root / "repair.json"),
                "events": str(events),
            },
        }
        self.assertTrue(
            route_recovery.recover(
                self.runtime, state, self.root, self.root, support.Paused("PAUSED_CONTENT_FILTER", "refused")
            )
        )
        self.assertEqual("RUNNING", state["status"])
        self.assertEqual("PLANNING", state["phase"])
        self.assertEqual(MIMO, state["settings"]["roles"]["planner"]["model"])

        # Non-planning report repair restores phase IMPLEMENTING.
        impl_events = self.make_events(REFUSAL, "tester_report_repair-01.jsonl")
        impl = {
            **state,
            "phase": "REPORT_REPAIR",
            "next_stage": "tester",
            "pending_report_repair": {"original": {"stage": "tester"}, "attempts": 1},
            "settings": {
                "engine": "opencode",
                "roles": {
                    "builder": {"model": GLM, "reasoning_effort": "medium"},
                    "tester": {"model": SOL, "reasoning_effort": "medium"},
                    "completion": {"model": SOL, "reasoning_effort": "medium"},
                    "requirements": {"model": MIMO, "reasoning_effort": "medium"},
                },
            },
            "active_stage": {
                "stage": "tester_report_repair",
                "original_stage": "tester",
                "report_only": True,
                "role": "tester",
                "route_role": "tester",
                "iteration": 1,
                "output": str(self.root / "tester-repair.json"),
                "events": str(impl_events),
            },
        }
        self.assertTrue(
            route_recovery.recover(
                self.runtime, impl, self.root, self.root, support.Paused("PAUSED_CONTENT_FILTER", "refused")
            )
        )
        self.assertEqual("IMPLEMENTING", impl["phase"])
        self.assertEqual(MIMO, impl["settings"]["roles"]["tester"]["model"])

    def test_held_recovery_packet_binding_stays_valid_after_automatic_route_switch(self):
        # Live run 1719e9ee: resolve stops on content filter under a held recovery packet and switches to
        # a model with declared_variants (causing route_fit effort adjustment + route_selection update).
        # _bound_settings before and after must match identically.
        events = self.make_events(REFUSAL, "resolve-01.jsonl")
        state = self.state(error=REFUSAL, role="task_planner")
        state["settings"]["declared_variants"] = {GLM: ["low", "medium"]}
        state["settings"]["roles"]["task_planner"] = {"model": ASTRA, "reasoning_effort": "high"}
        state["settings"]["roles"]["resolver"] = {"model": ASTRA, "reasoning_effort": "high"}
        state["active_stage"] = {
            "stage": "resolve",
            "role": "task_planner",
            "route_role": "resolver",
            "iteration": 1,
            "launch_route": {"model": ASTRA},
            "output": str(self.root / "resolve-01.json"),
            "events": str(events),
        }
        bound_before = resolver_recovery._bound_settings(state, {})
        self.assertTrue(
            route_recovery.recover(
                self.runtime, state, self.root, self.root, support.Paused("PAUSED_CONTENT_FILTER", "refused")
            )
        )
        self.assertEqual(GLM, state["settings"]["roles"]["resolver"]["model"])
        self.assertEqual("medium", state["settings"]["roles"]["resolver"]["reasoning_effort"])
        bound_after = resolver_recovery._bound_settings(state, {})
        self.assertEqual(bound_before, bound_after)

        # Also check selector-effort + route_selection_before undo on builder.
        b_state = self.state()
        b_state["settings"]["roles"]["builder"]["reasoning_effort"] = "high"
        b_before = quota_route.unassigned(b_state, b_state["settings"])
        self.assertTrue(
            route_recovery.recover(
                self.runtime, b_state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota")
            )
        )
        b_after = quota_route.unassigned(b_state, b_state["settings"])
        self.assertEqual(b_before["roles"]["builder"], b_after["roles"]["builder"])
        self.assertEqual(
            b_before["route_selection"]["routes"]["builder"], b_after["route_selection"]["routes"]["builder"]
        )

    def test_recovery_budget_pause_requests_job_recovery_and_worker_recovery_guards(self):
        # 1. Exhausted automatic recovery budget returns False cleanly instead of raising PAUSED_TIMEOUT_RECOVERY.
        spent_runtime = SimpleNamespace(
            **{
                **self.runtime.__dict__,
                "timeout_recovery_guard": mock.Mock(
                    side_effect=support.Paused("PAUSED_TIMEOUT_RECOVERY", "budget exhausted")
                ),
            }
        )
        state = self.state()
        self.assertFalse(
            route_recovery.recover(spent_runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "q"))
        )
        self.assertEqual(SOL, state["settings"]["roles"]["builder"]["model"])

        # 2. Pause-requested flag or file blocks automatic route switching.
        paused_flag = self.state()
        paused_flag["pause_requested"] = True
        self.assertFalse(
            route_recovery.recover(
                self.runtime, paused_flag, self.root, self.root, support.Paused("PAUSED_BUDGET", "q")
            )
        )

        # 3. Workflow job unit recovery and guards.
        job_state = {
            "status": "PAUSED_JOB_FAILURE",
            "settings": {
                "engine": "codex",
                "roles": {
                    "architect": {"model": "gpt-6-sol"},
                    "task_planner": {"model": "gpt-6-astra"},
                },
            },
            "job_failure": {
                "stage": "check_design",
                "attempt_id": "001/check_design-01",
                "pause_status": "PAUSED_CONTENT_FILTER",
                "source_identity": {"revision": "r1"},
                "route": {"route_role": "architect", "stopped_model": "gpt-6-sol"},
            },
            "recovery_context": {"events": "e.jsonl"},
        }
        self.assertFalse(
            route_recovery.recover_job(
                self.runtime,
                None,
                {**job_state, "pause_intent": {"requested_at": "t"}},
                self.root,
                self.root,
                reroute=lambda *a: "tok-1",
            )
        )
        self.assertFalse(
            route_recovery.recover_job(
                self.runtime,
                None,
                job_state,
                self.root,
                self.root,
                reroute=mock.Mock(side_effect=ValueError("stale")),
            )
        )
        self.assertTrue(
            route_recovery.recover_job(self.runtime, None, job_state, self.root, self.root, reroute=lambda *a: "tok-ok")
        )
        self.assertEqual("RUNNING", job_state["status"])
        self.assertEqual("gpt-6-astra", job_state["settings"]["roles"]["architect"]["model"])
        self.assertEqual(
            {"token": "tok-ok", "stage": "check_design", "consumed": False},
            job_state["job_retry_authorization"],
        )

        # 4. Parallel worker unit recovery syncs effort onto child state.
        child_dir = self.root / "worker-m1"
        child_dir.mkdir()
        child_events = child_dir / "builder-01.jsonl"
        child_events.write_text(json.dumps(REFUSAL) + "\n")
        child_output = child_dir / "001" / "builder-01.json"
        child_output.parent.mkdir()
        child_state = {
            "status": "PAUSED_CONTENT_FILTER",
            "parent_batch": "batch-1",
            "current_task": {"id": "M1"},
            "settings": {
                "engine": "opencode",
                "roles": {
                    "builder": {"model": GLM, "reasoning_effort": "high"},
                    "tester": {"model": MIMO, "reasoning_effort": "medium"},
                },
            },
            "sessions": {"builder": "child-ses"},
            "active_stage": {
                "stage": "builder",
                "role": "builder",
                "iteration": 1,
                "attempt": 1,
                "output": str(child_output),
                "events": str(child_events),
                "launch_route": {"model": GLM},
            },
        }
        (child_dir / "state.json").write_text(json.dumps({**child_state, "parent_batch": None}))
        parent_state = {
            "status": "RUNNING",
            "phase": "EXECUTING",
            "next_stage": "orchestrator",
            "settings": {
                "engine": "opencode",
                "declared_variants": {SOL: ["low", "medium"]},
                "roles": {
                    "builder": {"model": GLM, "reasoning_effort": "high"},
                    "tester": {"model": MIMO, "reasoning_effort": "medium"},
                    "completion": {"model": MIMO, "reasoning_effort": "medium"},
                    "plan_reviewer": {"model": SOL, "reasoning_effort": "high"},
                },
            },
            "orchestration_batch": {
                "status": "BUILDING",
                "workers": [
                    {
                        "milestone_id": "M1",
                        "task": {"id": "M1"},
                        "status": "PAUSED_CONTENT_FILTER",
                        "run_dir": str(child_dir),
                        "workspace": str(self.root),
                    }
                ],
            },
        }
        w_err = support.Paused("PAUSED_CONTENT_FILTER", "refused")
        w_err.quota_worker = {
            "role": "builder",
            "milestone_id": "M1",
            "model": GLM,
            "attempt_id": "001/builder-01",
            "stage": "builder",
            "pause_status": "PAUSED_CONTENT_FILTER",
            "events": str(child_events),
            "tester_model": MIMO,
            "completion_model": MIMO,
        }
        # Stale child state (parent_batch=None) returns False cleanly.
        self.assertFalse(route_recovery.recover(self.runtime, parent_state, self.root, self.root, w_err))
        (child_dir / "state.json").write_text(json.dumps(child_state))
        self.assertTrue(route_recovery.recover(self.runtime, parent_state, self.root, self.root, w_err))
        self.assertEqual(SOL, parent_state["settings"]["roles"]["builder"]["model"])
        self.assertEqual("medium", parent_state["settings"]["roles"]["builder"]["reasoning_effort"])
        updated_child = json.loads((child_dir / "state.json").read_text())
        self.assertEqual(SOL, updated_child["settings"]["roles"]["builder"]["model"])
        self.assertEqual("medium", updated_child["settings"]["roles"]["builder"]["reasoning_effort"])

    def test_other_stops_and_native_runs_are_not_this_recovery_s_business(self):
        capacity = {"type": "error", "error": {"message": "Selected model is at capacity"}}
        state = self.state(error=capacity)
        self.assertFalse(
            route_recovery.recover(
                self.runtime, state, self.root, self.root, support.Paused("PAUSED_PROVIDER_CAPACITY", "c")
            )
        )
        native = SimpleNamespace(now=lambda: "t", native_calls=object())
        self.assertFalse(
            route_recovery.recover(native, self.state(), self.root, self.root, support.Paused("PAUSED_BUDGET", "q"))
        )

    def test_tier1_permitted_set_filters_cross_role_candidates_on_refusal_and_quota(self):
        # next_route flattens candidates across all role lists; a same-vendor candidate in
        # another role's list must never leak back onto the refused or quota-stopped role.
        state = {
            "settings": {
                "engine": "opencode",
                "roles": {
                    "planner": {"model": LUNA},
                    "plan_reviewer": {"model": GLM},
                    "builder": {"model": "openai/gpt-5.6-terra"},
                    "tester": {"model": GLM},
                    "completion": {"model": GLM},
                    "requirements": {"model": LUNA},
                    "task_planner": {"model": LUNA},
                },
                "route_selection": {
                    "routes": {
                        "planner": {"model": LUNA, "origin": "auto_selected"},
                        "plan_reviewer": {"model": GLM, "origin": "auto_selected"},
                        "builder": {"model": "openai/gpt-5.6-terra", "origin": "user_explicit"},
                        "tester": {"model": GLM, "origin": "auto_selected"},
                        "completion": {"model": GLM, "origin": "auto_selected"},
                        "requirements": {"model": LUNA, "origin": "auto_selected"},
                        "task_planner": {"model": LUNA, "origin": "auto_selected"},
                    },
                    "candidates": {
                        "builder": [{"model": GLM, "account": "Z.AI", "billing": "subscription"}],
                        "tester": [
                            {"model": SOL, "account": "ChatGPT", "billing": "subscription"},
                            {"model": GLM, "account": "Z.AI", "billing": "subscription"},
                        ],
                    },
                },
            }
        }
        chosen = route_pool.choose(
            self.runtime,
            state,
            self.root,
            "builder",
            "openai/gpt-5.6-terra",
            "PAUSED_CONTENT_FILTER",
        )
        self.assertIsNotNone(chosen)
        self.assertEqual("configured_pair_swap", chosen["tier"])
        self.assertEqual(GLM, chosen["primary"]["model"])
        self.assertEqual(
            {("tester", LUNA), ("completion", LUNA)}, {(p["role"], p["model"]) for p in chosen["partners"]}
        )

    def test_the_chain_runs_same_model_output_cap_repairs_before_route_switch(self):
        names = [row[0] for row in dispatch.RECOVERY_CHAIN]
        self.assertLess(names.index("truncated_review"), names.index("route"))
        self.assertLess(names.index("reasoning_exhaustion"), names.index("route"))
        state = self.state()
        self.assertEqual(
            "route",
            dispatch.recover(self.runtime, state, self.root, self.root, support.Paused("PAUSED_BUDGET", "quota")),
        )


if __name__ == "__main__":
    unittest.main()
