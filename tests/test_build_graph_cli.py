"""Repeated fan-out and joins through the public CLI and real Builder worktrees."""

import json
import subprocess
import sys
import unittest

from . import test_build_blackbox as fixtures


def joined_graph():
    dependencies = [[], [1], [1], [1], [2, 3], [3, 4], [4], [5, 6], [6, 7], [8, 9]]
    rows, payloads, checks, values = [], {}, {}, {}
    for number, parents in enumerate(dependencies, 1):
        mid = f"M{number}"
        values[number] = number + sum(values[parent] for parent in parents)
        imports = "".join(f"from graph_nodes.node_{parent} import value as parent_{parent}\n" for parent in parents)
        expression = " + ".join([str(number), *(f"parent_{parent}()" for parent in parents)])
        files = {
            f"graph_nodes/node_{number}.py": imports + f"\n\ndef value():\n    return {expression}\n",
            f"test_node_{number}.py": (
                "import unittest\n"
                f"from graph_nodes.node_{number} import value\n\n\n"
                f"class Node{number}Tests(unittest.TestCase):\n"
                "    def test_value_includes_every_prerequisite(self):\n"
                f"        self.assertEqual({values[number]}, value())\n"
            ),
        }
        if number == 1:
            files["graph_nodes/__init__.py"] = ""
        payloads[mid] = files
        rows.append(([f"M{parent}" for parent in parents], f"Node {number} includes its prerequisites", list(files)))
        checks[mid] = f"from graph_nodes.node_{number} import value; assert value() == {values[number]}"
    spec = fixtures.plan(rows, payloads, checks, "Build a ten-node computation with repeated fan-out and joins")
    spec["complete_product"] = True
    return spec, dependencies, values


class JoinedGraphCLI(unittest.TestCase):
    setUp = fixtures.BuildBlackbox.setUp
    command = fixtures.BuildBlackbox.command
    invoke = fixtures.BuildBlackbox.invoke
    seed = fixtures.BuildBlackbox.seed
    state = fixtures.BuildBlackbox.state
    events = fixtures.BuildBlackbox.events
    build = fixtures.BuildBlackbox.build

    def status(self):
        return json.loads(self.invoke("autocode", ["--run-dir", str(self.run), "--status"]).stdout)

    def test_format_failure_in_first_repair_keeps_the_original_evidence_error(self):
        spec = fixtures.notes()
        spec["changed_code_citation_repair"] = True
        spec["repair_loses_error"] = True
        self.seed(spec, max_parallel=2)
        self.build()
        starts = self.events()
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])
        status = self.status()
        self.assertEqual(1, status["view"]["progress"]["tasks"]["done"])
        self.assertEqual(starts, self.events(), "A reporting failure must not replay the Builder")
        repairs = self.events("citation_repair", stage="sol_report_repair")
        self.assertEqual(2, len(repairs))
        self.assertEqual(repairs[0]["error"], repairs[1]["original_error"])
        self.assertNotEqual(repairs[0]["error"], repairs[1]["error"])
        self.assertFalse(status["completion_current"])

    def test_changed_line_citation_repair_uses_enclosing_proof_without_new_builder(self):
        spec = fixtures.notes()
        spec["changed_code_citation_repair"] = True
        self.seed(spec, max_parallel=2)
        self.build()
        starts = self.events()
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])
        status = self.status()
        self.assertEqual(1, status["view"]["progress"]["tasks"]["done"])
        self.assertEqual(starts, self.events())
        repairs = self.events("citation_repair", stage="sol_report_repair")
        self.assertEqual(1, len(repairs))
        self.assertTrue(repairs[0]["context"]["proof_reference"].endswith("/verification.json"))
        self.assertTrue(any(row["kind"] == "report_repaired" for row in status["view"]["recoveries"]))
        self.assertFalse(status["completion_current"])

    def test_combined_report_repair_uses_current_pair_without_rebuilding_or_adding_accepted_parent(self):
        spec = fixtures.notes()
        spec["batch_report_repair"] = True
        self.seed(spec, max_parallel=2)
        self.build()
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])
        self.assertEqual(1, self.status()["view"]["progress"]["tasks"]["done"])
        self.build()
        starts = self.events()
        self.assertEqual(["M1", "M2", "M3"], sorted(row["milestone"] for row in starts))

        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])

        status = self.status()
        self.assertEqual(3, status["view"]["progress"]["tasks"]["done"])
        self.assertEqual(starts, self.events(), "Report repair never launches another Builder")
        repairs = self.events("batch_repair", stage="sol_report_repair")
        self.assertEqual(1, len(repairs), "The exact member inventory repairs the first rejected report")
        self.assertEqual(["M2", "M3"], repairs[0]["scope"]["milestone_ids"])
        self.assertEqual(["C2", "C3"], repairs[0]["scope"]["acceptance_criteria"])
        self.assertTrue(any(row["kind"] == "report_repaired" for row in status["view"]["recoveries"]))
        self.assertFalse(status["completion_current"], "The dependent CLI milestone has not been delivered")

    def test_ten_milestones_join_only_reviewed_inputs_and_finish_without_duplicate_builders(self):
        spec, dependencies, values = joined_graph()
        self.seed(spec, max_parallel=3)
        waves = [{"M1"}, {"M2", "M3", "M4"}, {"M5", "M6", "M7"}, {"M8", "M9"}, {"M10"}]
        completed = 0
        for wave in waves:
            with self.subTest(wave=sorted(wave)):
                if len(wave) > 1:
                    self.env["BUILD_AUDIT_OVERLAP"] = str(completed + len(wave))
                self.build()
                self.env.pop("BUILD_AUDIT_OVERLAP", None)
                starts = self.events()[completed:]
                ends = self.events("finish")[completed:]
                self.assertEqual(wave, {row["milestone"] for row in starts})
                self.assertEqual(len(wave), len(starts))
                self.assertEqual(len(wave), len(ends))
                if len(wave) > 1:
                    self.assertEqual(len(wave), len({row["workspace"] for row in starts}))
                    self.assertLess(max(row["time"] for row in starts), min(row["time"] for row in ends))
                for event in starts:
                    number = int(event["milestone"][1:])
                    for parent in dependencies[number - 1]:
                        path = f"graph_nodes/node_{parent}.py"
                        self.assertEqual(spec["payloads"][f"M{parent}"][path], event["inputs"][path])
                    for sibling in wave - {event["milestone"]}:
                        self.assertNotIn(f"graph_nodes/node_{int(sibling[1:])}.py", event["inputs"])
                # No successor launches until the independently checked wave is accepted.
                before = len(self.events())
                view = self.status()["view"]
                self.assertEqual(completed, view["progress"]["tasks"]["done"])
                self.assertEqual(before, len(self.events()))
                self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])
                completed += len(wave)
                self.assertEqual(completed, self.status()["view"]["progress"]["tasks"]["done"])
                self.assertEqual(before, len(self.events()))

        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])
        final = self.status()
        self.assertEqual("TASK_COMPLETE", final["status"])
        self.assertTrue(final["completion_current"])
        self.assertEqual(10, len(self.events()))
        self.assertEqual([], self.events(stage="astra_resolve"))
        check = subprocess.run(
            [sys.executable, "-c", f"from graph_nodes.node_10 import value; assert value() == {values[10]}"],
            cwd=self.project,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, check.returncode, check.stdout + check.stderr)
        # A broken shared prerequisite must invalidate the result and fail downstream tests.
        (self.project / "graph_nodes/node_3.py").write_text("def value():\n    return -1000\n")
        broken = subprocess.run(
            [sys.executable, "-m", "unittest", "test_node_10"], cwd=self.project, capture_output=True, text=True
        )
        self.assertNotEqual(0, broken.returncode)
        self.assertIn("FAIL", broken.stderr)
        self.assertFalse(self.status()["completion_current"])


if __name__ == "__main__":
    unittest.main()
