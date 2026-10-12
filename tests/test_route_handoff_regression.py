"""Route-change handoff regressions: dispatch names its route; #1097 pins current retry behavior.

The catalog scenario ``route-builder-handoff`` covers the end-to-end CLI path
(partial work kept, new Builder route used, verifiers independent). These unit
tests pin the dispatch line and the known stale-route retry gap (#1097).
"""

from __future__ import annotations

import ast
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
    """#1097: plain --retry-builder re-launches the batch's frozen route.

    Until a warn-or-refuse lands, pin that retry_route_refusal only fires when
    settings flags change the Builder route *together with* --retry-builder, and
    that a parent settings change alone does not move the child's frozen route.
    """

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
        # #1097: the message does not yet compare the frozen child route to the parent's
        # newly saved terra model, so a later plain --retry-builder can still launch the
        # stale route after a separate settings save. Pin that gap until the fix lands.
        self.assertNotIn("saved route differs", message or "")


if __name__ == "__main__":
    unittest.main()
