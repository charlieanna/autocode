"""Route-change handoff regressions: dispatch names its route; stale member retries are refused.

The catalog scenario ``route-builder-handoff`` covers the end-to-end CLI path
(partial work kept, new Builder route used, verifiers independent). These unit
tests pin the dispatch line and the saved-route retry guard (#1097).
"""

from __future__ import annotations

import ast
import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode_worker_quota as worker_quota

TOOLS = Path(__file__).resolve().parents[1] / "tools"


class DispatchRouteDisplayTests(unittest.TestCase):
    def test_every_builder_launch_line_names_the_model_route(self):
        # autocode.run_role prints "started; model=..." before the provider launches.
        source = (TOOLS / "autocode.py").read_text()
        self.assertIn("started; model=", source)
        self.assertIn("model={model or 'default'}", source)
        tree = ast.parse(source)
        prints = [
            node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "print"
        ]
        self.assertTrue(
            any("started; model=" in ast.dump(node) for node in prints),
            "run_role must print the model on every provider launch",
        )


class StaleRetryRouteTests(unittest.TestCase):
    """A separately saved parent route does not authorize an unnoticed frozen-route retry."""

    def test_retry_route_refusal_names_frozen_route_when_settings_change_with_retry(self):
        before = {"roles": {"terra": {"model": "old-model", "engine": "codex", "provider": "gocode"}}}
        after = {"roles": {"terra": {"model": "new-model", "engine": "codex", "provider": "gocode"}}}
        state = {
            "orchestration_batch": {
                "workers": [
                    {
                        "milestone_id": "M2",
                        "run_dir": "/absent-child",
                        "status": "PAUSED_BUDGET",
                    }
                ]
            }
        }
        message = worker_quota.retry_route_refusal(state, ["M2"], before, after)
        self.assertIsNotNone(message)
        self.assertIn("Builder M2's retry runs on the Builder route its batch started it with", message)
        self.assertIn("would not reach it", message)
        # With asked=M2 (open route-terra), the refusal also names the answer channel.
        asked = worker_quota.retry_route_refusal(state, ["M2"], before, after, asked="M2")
        self.assertIn("route-terra=MODEL", asked or "")

    def test_a_plain_retry_compares_every_selected_child_with_the_saved_parent_route(self):
        frozen = {
            "engine": "opencode",
            "roles": {"terra": {"model": "old-model", "provider": None, "reasoning_effort": "high"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            old_child, matching_child = Path(directory, "old"), Path(directory, "matching")
            old_child.mkdir()
            matching_child.mkdir()
            Path(old_child, "state.json").write_text(json.dumps({"settings": frozen}))
            state = {
                "orchestration_batch": {
                    "workers": [
                        {"milestone_id": "M1", "run_dir": str(matching_child), "status": "PAUSED_BUDGET"},
                        {"milestone_id": "M2", "run_dir": str(old_child), "status": "PAUSED_BUDGET"},
                    ]
                }
            }
            for key, value in (("model", "new-model"), ("reasoning_effort", "low")):
                with self.subTest(key=key):
                    saved = copy.deepcopy(frozen)
                    saved["roles"]["terra"][key] = value
                    Path(matching_child, "state.json").write_text(json.dumps({"settings": saved}))
                    message = worker_quota.retry_route_refusal(state, ["M1", "M2"], saved, saved, asked="M2")
                    self.assertIn("Builder M2's saved route differs", message or "")
                    self.assertIn(key.replace("_", " "), message or "")
                    self.assertIn(str(value), message or "")
                    self.assertIn("--answer route-terra=MODEL", message or "")
                    if key == "model":
                        self.assertNotIn("restore the run's other Builder route settings", message or "")
            for key, value in (("engine", "codex"), ("provider", "other-adapter"), ("reasoning_effort", "low")):
                for mixed_model in (False, True):
                    with self.subTest(key=key, mixed_model=mixed_model):
                        saved = copy.deepcopy(frozen)
                        if key == "provider":
                            saved["provider"] = value
                        else:
                            saved["roles"]["terra"][key] = value
                        if mixed_model:
                            saved["roles"]["terra"]["model"] = "new-model"
                        message = worker_quota.retry_route_refusal(state, ["M2"], saved, saved, asked="M2")
                        self.assertIn("--answer route-terra=MODEL", message or "")
                        self.assertIn("restore the run's other Builder route settings separately", message or "")
                        self.assertIn("keeping the member's other route settings", message or "")
            # Restoring the parent's route separately permits the explicit same-route retry.
            self.assertIsNone(worker_quota.retry_route_refusal(state, ["M2"], frozen, frozen))
            saved = copy.deepcopy(frozen)
            saved["roles"]["terra"]["model"] = "new-model"
            for asked in (None, "M1", ""):
                with self.subTest(asked=asked):
                    message = worker_quota.retry_route_refusal(state, ["M2"], saved, saved, asked=asked)
                    self.assertNotIn("--answer route-terra", message or "")
                    self.assertIn("separate settings command", message or "")

    def test_equivalent_inherited_engines_and_default_providers_do_not_block_retry(self):
        for engine, child_provider, parent_provider in (("codex", None, "openai"), ("opencode", None, "ignored")):
            with self.subTest(engine=engine), tempfile.TemporaryDirectory() as directory:
                child = {"engine": engine, "roles": {"terra": {"model": "same", "provider": child_provider}}}
                parent = {"roles": {"terra": {"engine": engine, "model": "same", "provider": parent_provider}}}
                Path(directory, "state.json").write_text(json.dumps({"settings": child}))
                state = {"orchestration_batch": {"workers": [{"milestone_id": "M2", "run_dir": directory}]}}
                self.assertIsNone(worker_quota.retry_route_refusal(state, ["M2"], parent, parent))


if __name__ == "__main__":
    unittest.main()
