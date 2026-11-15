"""Requirements runs GLM-5.3 at low by default (owner decision 2026-11-15).

GLM-5.3 offers low, high and max. The OpenCode default and the route qualification table named
medium, a level the route does not have, and the Kilo config gave Requirements a copy of the
Planner's high. Requirements answers a waiting person, so every default for it is GLM-5.3 low.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_route_selector as route_selector
import model_catalogue
from providers import kilocode, opencode

GLM_LEVELS = {"low", "high", "max"}


class RequirementsDefaultRouteTests(unittest.TestCase):
    def test_opencode_default_is_glm_low(self):
        self.assertEqual("zai-coding-plan/glm-5.3", opencode.DEFAULT_MODELS["requirements"])
        self.assertEqual("low", opencode.DEFAULT_REASONING_EFFORTS["requirements"])

    def test_kilo_default_is_glm_low_not_the_planners_high(self):
        with tempfile.TemporaryDirectory() as home, patch.dict("os.environ", {"XDG_CONFIG_HOME": home}):
            provider = kilocode.load("kilocode")
        self.assertEqual("zhipuai-coding-plan/glm-5.3", provider.DEFAULT_MODELS["requirements"])
        self.assertEqual("low", provider.DEFAULT_REASONING_EFFORTS["requirements"])
        self.assertEqual("high", provider.DEFAULT_REASONING_EFFORTS["planner"])

    def test_catalogue_suggestion_is_glm_low(self):
        self.assertEqual(("zai-coding-plan/glm-5.3", "low"), model_catalogue.PREFERRED["requirements"])

    def test_qualified_requirements_route_names_a_level_glm_offers(self):
        table = json.loads(Path(route_selector.QUALIFICATION_PATH).read_text())
        (entry,) = [row for row in table["roles"]["requirements"] if row["model"] == "zai-coding-plan/glm-5.3"]
        self.assertEqual("low", entry["effort"])
        self.assertIn(entry["effort"], GLM_LEVELS)


if __name__ == "__main__":
    unittest.main()
