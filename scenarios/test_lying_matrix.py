"""Every catalog broken/ variant must never be judged PASS under a fake run.

The oracle layer already rejects each overlay (OracleControlTests). This matrix
drives AutoCode's real CLI with the scripted model applying each broken
solution: AutoCode may complete because its own check passed, but the harness
must not report PASS (FALSE_COMPLETE or an honest stop). See scenarios/README.md
("Fake run") and the reliability plan's lying-model fault matrix.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402
from harness import catalog, verdict  # noqa: E402


def broken_pairs():
    """(scenario_id, fake_solution) for every catalog broken/<name>/ with a fake check."""
    for scenario in catalog.load_all():
        if not scenario.fake_check or scenario.missing_tools():
            continue
        if scenario.category in ("program", "components"):
            # Programs and multi-component runs have their own drivers; covered elsewhere.
            continue
        for path in scenario.broken:
            yield scenario.id, f"broken/{path.name}"


def _args(solution: str, out: Path) -> argparse.Namespace:
    return argparse.Namespace(
        fake=True,
        profile=None,
        provider=None,
        hybrid=False,
        fake_solution=solution,
        fake_fault=None,
        out=out,
        autocode=None,
        max_steps=None,
        timeout_minutes=15,
        local_docker=False,
        i_authorize_live_model_spend=False,
    )


class LyingMatrixTests(unittest.TestCase):
    def test_catalog_enumerates_broken_variants(self):
        pairs = list(broken_pairs())
        self.assertGreater(len(pairs), 50, "the matrix should cover the catalog's broken overlays")
        ids = {scenario for scenario, _ in pairs}
        self.assertIn("greenfield-todo-cli", ids)
        self.assertIn("bugfix-iso-weeks", ids)

    def test_every_broken_variant_is_never_pass(self):
        # Slow: one full fake CLI run per broken overlay (~minutes to hours).
        # Listed in tests/suite_slow.json; run with --scenario-harness or --include-slow.
        failures = []
        for scenario_id, solution in broken_pairs():
            with self.subTest(scenario=scenario_id, solution=solution):
                with tempfile.TemporaryDirectory(prefix="lying-matrix-") as directory:
                    result = run.run_one(catalog.load(scenario_id), _args(solution, Path(directory)))
                if result["verdict"] == verdict.SKIPPED:
                    continue
                if result["verdict"] == verdict.PASS:
                    failures.append(f"{scenario_id}/{solution}: {result['summary']}")
                self.assertNotEqual(
                    verdict.PASS,
                    result["verdict"],
                    f"{scenario_id} {solution}: {result['summary']}",
                )
        self.assertEqual([], failures)


class RequirementsDropsLiteralTests(unittest.TestCase):
    """fault=requirements_drops_literal: the brief-to-criteria drop must not complete."""

    def test_dropped_list_brackets_stop_before_completion(self):
        with tempfile.TemporaryDirectory(prefix="drops-literal-") as directory:
            args = _args("reference", Path(directory))
            args.fake_fault = "requirements_drops_literal"
            result = run.run_one(catalog.load("greenfield-todo-cli"), args)
        self.assertNotEqual(verdict.PASS, result["verdict"], result["summary"])
        self.assertNotEqual("TASK_COMPLETE", result.get("runner_status"))
        self.assertNotEqual(verdict.FALSE_COMPLETE, result["verdict"], result["summary"])


if __name__ == "__main__":
    unittest.main()
