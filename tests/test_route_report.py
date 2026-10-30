"""Setup shows the route each role will use, before any run spends (#2057 D).

`connections setup` ends with one line per role, `connections list --routes --json`
carries the same rows for clients, and a new run with no model flags says what it chose.
All three read the tool's own local listing; none launches a model and none returns a
credential.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_configure as configure
import autocode_connections as connections
import autocode_route_discovery as discovery
import model_catalogue as catalogue


def candidate(model, account="Kilo Gateway", billing="pay per token"):
    return {
        "model": model,
        "tool": "kilocode",
        "account": account,
        "billing": billing,
        "levels": None,
        "probe": None,
    }


FOUND = {
    "tool": "kilocode",
    "accounts": [
        {"name": "Kilo Gateway", "mode": "oauth", "billing": "pay per token", "prefixes": ["kilo/"]},
        {"name": "Z.AI Coding Plan", "mode": "api", "billing": "subscription", "prefixes": ["zai-coding-plan/"]},
        {"name": "OpenAI", "mode": "oauth", "billing": "subscription", "prefixes": ["openai/"]},
    ],
    "candidates": [
        candidate("zai-coding-plan/glm-5.3", "Z.AI Coding Plan", "subscription"),
        candidate("openai/gpt-6-sol", "OpenAI", "subscription"),
        candidate("openai/gpt-6-astra", "OpenAI", "subscription"),
        candidate("kilo/deepseek/deepseek-v4.1-flash"),
    ],
}
ONE_FAMILY = {
    "tool": "kilocode",
    "accounts": [FOUND["accounts"][1]],
    "candidates": [candidate("zai-coding-plan/glm-5.3", "Z.AI Coding Plan", "subscription")],
}


def capture(call):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = call()
    return code, output.getvalue()


class RouteLineTests(unittest.TestCase):
    def test_each_role_line_names_the_model_level_and_why(self):
        with mock.patch.object(discovery, "discover", return_value=FOUND):
            with mock.patch("autocode_providers.default_name", return_value="kilocode"):
                found = catalogue.preview()
        lines = catalogue.route_lines(found)
        self.assertTrue(lines)
        rows = {row["role"]: row for row in catalogue.route_rows(found)}
        builder = rows["terra"]
        self.assertEqual("Builder", builder["job"])
        self.assertEqual("kilocode", builder["tool"])
        self.assertIn(
            f"Builder: {builder['model']} at "
            f"{builder['reasoning_effort'] or 'its own default'} "
            f"({builder['account']} {builder['billing']}; qualified for Builder)",
            lines,
        )
        # One line per role, and each says it is a qualified choice.
        self.assertEqual(len(rows), len(lines))
        self.assertTrue(all("qualified for" in line and line.endswith(")") for line in lines))

    def test_the_lines_can_be_narrowed_to_the_roles_a_run_uses(self):
        with mock.patch.object(discovery, "discover", return_value=FOUND):
            found = catalogue.preview(provider="kilocode")
        lines = catalogue.route_lines(found, {"terra", "sol"})
        self.assertEqual(2, len(lines))
        self.assertTrue(lines[0].startswith("Builder: "))
        self.assertTrue(lines[1].startswith("Tester: "))

    def test_a_selection_without_a_checker_names_the_stop_instead_of_lines(self):
        with mock.patch.object(discovery, "discover", return_value=ONE_FAMILY):
            found = catalogue.preview(provider="kilocode")
        self.assertTrue(found["stop"])
        self.assertEqual([], catalogue.route_lines(found))


class ConnectionRoutesTests(unittest.TestCase):
    def call(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = connections.cli(list(argv))
        return code, output.getvalue()

    def test_the_plain_listing_runs_no_backend_and_names_how_to_get_routes(self):
        with mock.patch.object(connections.subprocess, "run", side_effect=AssertionError("the listing ran a backend")):
            code, output = self.call("list", "--json")
        self.assertEqual(0, code)
        payload = json.loads(output)
        self.assertIsNone(payload["routes"])
        self.assertEqual(["connections", "list", "--routes", "--json"], payload["routes_next_command"])

    def test_without_json_the_listing_is_text_a_person_can_read(self):
        """`autocode connections list` printed one line of JSON; `--json` was a no-op."""
        with (
            mock.patch.object(connections.subprocess, "run", side_effect=AssertionError("the listing ran a backend")),
            mock.patch.object(
                connections.shutil, "which", side_effect=lambda name: f"/bin/{name}" if name == "opencode" else None
            ),
        ):
            code, output = self.call("list")
        self.assertEqual(0, code)
        self.assertNotIn("{", output)
        self.assertIn("OpenCode:", output)
        self.assertIn("Check connection: autocode connections check --provider opencode\n", output)
        self.assertIn("autocode connections list --routes", output)
        self.assertNotIn("--json", output)
        with mock.patch.object(
            connections.direct,
            "describe",
            return_value={
                "id": "autocode",
                "label": "AutoCode",
                "connection_status": "ok",
                "detail": "",
                "actions": [],
            },
        ):
            code, output = self.call("check", "--provider", "autocode")
        self.assertEqual(0, code)
        self.assertEqual("✔ AutoCode: ok\n", output)

    def test_the_routes_listing_names_each_roles_model_and_level(self):
        with mock.patch.object(discovery, "discover", return_value=FOUND):
            with mock.patch("autocode_providers.default_name", return_value="kilocode"):
                code, output = self.call("list", "--routes", "--json")
        self.assertEqual(0, code)
        payload = json.loads(output)
        self.assertEqual("kilocode", payload["tool"])
        rows = {row["role"]: row for row in payload["routes"]}
        self.assertEqual("zai-coding-plan/glm-5.3", rows["requirements"]["model"])
        self.assertEqual("Planner", rows["glm"]["job"])
        self.assertEqual("subscription", rows["requirements"]["billing"])
        self.assertEqual(len(rows), len(payload["routes_lines"]))
        self.assertIn("Requirements: zai-coding-plan/glm-5.3 at", payload["routes_lines"][0])

    def test_a_tool_that_cannot_list_models_names_the_reason_instead_of_failing(self):
        with mock.patch.object(discovery, "discover", side_effect=RuntimeError("cannot verify kilocode login")):
            code, output = self.call("list", "--routes", "--json")
        self.assertEqual(0, code)
        payload = json.loads(output)
        self.assertEqual([], payload["routes"])
        self.assertIn("cannot verify kilocode login", payload["routes_stop"])
        self.assertTrue(payload["connections"])  # the account listing still works


class SetupRoutesTests(unittest.TestCase):
    def test_setup_ends_by_naming_each_roles_route(self):
        with mock.patch.object(connections.direct, "setup", return_value=0):
            with mock.patch.object(discovery, "discover", return_value=FOUND):
                with mock.patch("autocode_providers.default_name", return_value="kilocode"):
                    code, output = capture(lambda: connections.cli(["setup"]))
        self.assertEqual(0, code)
        self.assertIn("Routes for a new run, from kilocode's local listing:", output)
        self.assertIn("Planner: ", output)
        self.assertIn("qualified for Planner", output)

    def test_a_cancelled_setup_prints_no_routes(self):
        with mock.patch.object(connections.direct, "setup", return_value=2):
            with mock.patch.object(discovery, "discover", return_value=FOUND) as listed:
                code, output = capture(lambda: connections.cli(["setup"]))
        self.assertEqual(2, code)
        self.assertNotIn("Routes for a new run", output)
        listed.assert_not_called()


def arguments(**extra):
    roles = ("requirements", "glm", "terra", "plan_reviewer", "sol", "completion", "astra")
    values = {f"{role}_model": None for role in roles}
    values |= {f"{role}_provider": None for role in roles}
    values |= {f"{role}_reasoning_effort": None for role in roles}
    values.update(auto_select=None, reasoning_effort=None, _model_inventory=None)
    values.update(extra)
    return SimpleNamespace(**values)


class NewRunRoutesTests(unittest.TestCase):
    provider = SimpleNamespace(
        DEFAULT_MODELS={"astra": "fixed", "terra": "fixed", "sol": "fixed", "completion": "fixed"}
    )

    def select(self, args=None, *, joint=True, started=False):
        roles = {role: {"model": "fixed", "reasoning_effort": None} for role in ("astra", "terra", "sol", "completion")}
        with mock.patch.object(discovery, "discover", return_value=FOUND):
            code, output = capture(
                lambda: configure._auto_select(
                    args or arguments(),
                    roles,
                    self.provider,
                    None,
                    {},
                    {},
                    "opencode",
                    Path("."),
                    joint=joint,
                    started=started,
                )
            )
        return roles, code, output

    def test_a_new_run_with_no_model_flag_says_what_it_chose(self):
        roles, result, output = self.select()
        self.assertIsNotNone(result)
        self.assertIn("Routes for this run, from kilocode's local listing:", output)
        self.assertIn("Builder: ", output)
        self.assertIn("Planner: ", output)  # joint planning runs the planning roles too
        self.assertEqual(result["routes"]["terra"]["model"], roles["terra"]["model"])

    def test_a_legacy_run_does_not_name_planning_roles_it_never_runs(self):
        _roles, result, output = self.select(joint=False)
        self.assertIsNotNone(result)
        self.assertNotIn("\n  Planner: ", output)
        self.assertNotIn("\n  Requirements: ", output)
        self.assertIn("\n  Builder: ", output)

    def test_a_named_model_keeps_the_quiet_default(self):
        _roles, _result, output = self.select(arguments(terra_model="openai/gpt-6-sol"))
        self.assertNotIn("Routes for this run", output)

    def test_a_saved_run_does_not_repeat_the_choice(self):
        _roles, _result, output = self.select(started=True)
        self.assertNotIn("Routes for this run", output)


if __name__ == "__main__":
    unittest.main()
