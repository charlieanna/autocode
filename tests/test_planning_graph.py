import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_planning_graph as graph


def body():
    return {"acceptance_criteria": [{"id": "AC1"}, {"id": "AC2"}],
            "initial_task": {"kind": "implement", "milestone_id": "M1"},
            "milestones": [
                {"id": "M1", "acceptance_criteria": ["AC1"], "depends_on": [], "boundaries": ["a.py"]},
                {"id": "M2", "acceptance_criteria": ["AC2"], "depends_on": ["M1"], "boundaries": ["b.py"]},
            ]}


class GraphTests(unittest.TestCase):
    def test_derivation_is_deterministic_and_data_only(self):
        self.assertEqual(graph.serialize(body()), graph.serialize(body()))
        self.assertFalse(graph.derive(body())["automatic_execution"])

    def test_rejects_cycle_unknown_and_disagreeing_model_graph(self):
        cyclic = body(); cyclic["milestones"][0]["depends_on"] = ["M2"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            graph.derive(cyclic)
        unknown = body(); unknown["milestones"][1]["depends_on"] = ["unknown"]
        with self.assertRaisesRegex(ValueError, "unknown"):
            graph.derive(unknown)
        with self.assertRaisesRegex(ValueError, "disagrees"):
            graph.validate(body(), {"nodes": []})

    def test_rejects_missing_explicit_dependency_declaration(self):
        candidate = body()
        del candidate["milestones"][1]["depends_on"]
        with self.assertRaisesRegex(ValueError, "must declare depends_on"):
            graph.validate(candidate)

    def test_independent_pairs_require_disjoint_boundaries(self):
        candidate = body()
        candidate["milestones"].append({"id": "M3", "acceptance_criteria": [], "depends_on": ["M1"], "boundaries": ["c.py"]})
        candidate["acceptance_criteria"].append({"id": "AC3"})
        candidate["milestones"][-1]["acceptance_criteria"] = ["AC3"]
        output = graph.derive(candidate)
        self.assertIn({"milestones": ["M2", "M3"]}, output["parallel_eligible"])
        candidate["milestones"][-1]["boundaries"] = ["b.py"]
        output = graph.derive(candidate)
        self.assertIn({"milestones": ["M2", "M3"], "boundaries": ["b.py"]}, output["shared_boundaries"])

    def test_orchestration_graph_is_not_consumed_by_launch_or_dispatch_modules(self):
        root = Path(__file__).resolve().parent
        for name in ("autocode.py", "autocode_tasks.py", "autocode_milestones.py", "autocode_workflow.py",
                     "autocode_opencode.py"):
            with self.subTest(name=name):
                source = (root / name).read_text()
                self.assertNotIn("graph.json", source)
                self.assertNotIn("parallel_eligible", source)
                self.assertNotIn("planning_graph.consume", source)
