"""Every level AutoCode ships names one its model has (#3630).

The Builder default asked GLM-5.3 for ``medium``, which its tools do not declare, and the same level sat in
the model pins, the qualification table, the scoring ladder and the live profiles. Each table was written by
hand, so each could name a level the model lacks. This test reads every shipped table and checks it against
the one record of what a model's tools declare (``autocode_route_fit.KNOWN_DECLARED``).
"""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path

import autocode_route_fit as route_fit
import live_profiles
import model_catalogue
import score_autocode_run
from providers import opencode

from scenarios.harness import profiles as scenario_profiles

ROOT = Path(__file__).resolve().parents[1]


def shipped_levels():
    """``(where, model, effort)`` for every model and level AutoCode ships in a table."""
    for role, model in opencode.DEFAULT_MODELS.items():
        yield f"opencode.DEFAULT_MODELS[{role}]", model, opencode.DEFAULT_REASONING_EFFORTS.get(role)
    kilo = tomllib.loads((ROOT / "tools/providers/configs/kilocode.toml").read_text())
    for role, spec in kilo["roles"].items():
        yield f"kilocode.toml roles.{role}", spec["model"], spec.get("effort")
    for role, (model, effort) in model_catalogue.PREFERRED.items():
        yield f"model_catalogue.PREFERRED[{role}]", model, effort
    for role, spec in json.loads((ROOT / "tools/model-pins.json").read_text())["roles"].items():
        yield f"model-pins.json {role}", spec["model"], spec.get("effort")
    table = json.loads((ROOT / "tools/autocode_route_qualification.json").read_text())
    for role, entries in table["roles"].items():
        for entry in entries:
            for route in entry.get("routes") or [entry["model"]]:
                yield f"autocode_route_qualification.json {role}", route, entry.get("effort")
    for role, spec in score_autocode_run.LADDER.items():
        yield f"score_autocode_run.LADDER[{role}]", spec["model"], spec["effort"]
    for name, profile in live_profiles.PROFILES.items():
        models = profile.get("role_models") or {}
        for role, effort in (profile.get("effort") or {}).items():
            yield f"live_profiles {name}.{role}", models.get(role) or profile.get("base"), effort
    for name, profile in scenario_profiles.PROFILES.items():
        models = profile.get("models") or {}
        for role, effort in (profile.get("effort") or {}).items():
            yield f"scenarios profile {name}.{role}", models.get(role), effort


class KnownDeclaredTest(unittest.TestCase):
    def test_one_entry_covers_every_route_of_a_model(self):
        for route in ("zai-coding-plan/glm-5.3", "zhipuai-coding-plan/glm-5.3", "kilo/z-ai/glm-5.3", "glm-5.3"):
            self.assertEqual(route_fit.known_declared(route), ("low", "high", "max"), route)

    def test_an_unrecorded_model_is_unknown_not_guessed(self):
        self.assertIsNone(route_fit.known_declared("zai-coding-plan/glm-5.3-flash"))
        self.assertIsNone(route_fit.known_declared("openai/gpt-6-sol"))
        self.assertIsNone(route_fit.known_declared(None))


class ShippedLevelsTest(unittest.TestCase):
    def test_every_shipped_level_is_one_the_model_declares(self):
        wrong = [
            f"{where}: {model} at {effort} (declares {', '.join(declared)})"
            for where, model, effort in shipped_levels()
            if effort and (declared := route_fit.known_declared(model)) is not None and effort not in declared
        ]
        self.assertEqual(wrong, [])

    def test_the_tables_are_read(self):
        # The check above means nothing if no table names GLM-5.3: each source must reach it.
        sources = {
            where.split(" ")[0].split("[")[0]
            for where, model, _effort in shipped_levels()
            if route_fit.known_declared(model)
        }
        self.assertTrue(
            {
                "opencode.DEFAULT_MODELS",
                "kilocode.toml",
                "model_catalogue.PREFERRED",
                "model-pins.json",
                "autocode_route_qualification.json",
                "score_autocode_run.LADDER",
                "live_profiles",
                "scenarios",
            }
            <= sources,
            sources,
        )

    def test_the_builder_default_runs_at_a_level_glm_has(self):
        self.assertEqual(opencode.DEFAULT_REASONING_EFFORTS["builder"], "high")
        self.assertEqual(model_catalogue.PREFERRED["builder"], ("zai-coding-plan/glm-5.3", "high"))


if __name__ == "__main__":
    unittest.main()
