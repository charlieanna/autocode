"""Repeated fan-out and joins through the public CLI and real Builder worktrees."""

import json
import subprocess
import sys
import unittest

from . import test_build_blackbox as fixtures


def joined_graph(dependencies=None):
    if dependencies is None:
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
    spec = fixtures.plan(rows, payloads, checks, "Build a computation with repeated fan-out and joins")
    spec["complete_product"] = True
    return spec, dependencies, values


def citation_repair_spec():
    spec = fixtures.notes()
    # Statement mutants cover the original storage module completely. Future
    # imports have no mutation site; test the postponed annotation they enable
    # so the fixture Tester must cite a reason and the bad citation reaches repair.
    source = spec["payloads"]["M1"]["notes/storage.py"]
    spec["payloads"]["M1"]["notes/storage.py"] = "from __future__ import annotations\n" + source.replace(
        "def load(p):", "def load(p: Path):"
    )
    spec["payloads"]["M1"]["test_storage.py"] += (
        "\n    def test_load_postpones_its_path_annotation(self):\n"
        "        self.assertEqual({'p': 'Path'}, load.__annotations__)\n"
    )
    spec["changed_code_citation_repair"] = True
    return spec


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

    def test_large_graph_combines_worker_crash_idempotent_resume_and_repeated_report_repair(self):
        dependencies = [[] for _ in range(4)]
        for first in (1, 5, 9, 13):
            dependencies.extend([[first + offset, first + (offset + 1) % 4] for offset in range(4)])
        dependencies.append([17, 18, 19, 20])
        spec, dependencies, values = joined_graph(dependencies)
        spec["batch_report_repair"] = True
        self.seed(spec, max_parallel=4)
        waves = [{f"M{i}" for i in range(first, first + 4)} for first in (1, 5, 9, 13, 17)]
        waves.append({"M21"})
        completed = 0
        for wave_number, wave in enumerate(waves):
            with self.subTest(wave=wave_number):
                before = len(self.events())
                if len(wave) > 1:
                    self.env["BUILD_AUDIT_OVERLAP"] = str(completed + len(wave))
                if wave_number == 0:
                    self.env["BUILD_AUDIT_FAULT"] = "crash"
                    self.build(2)
                    self.env.pop("BUILD_AUDIT_FAULT")
                    self.env.pop("BUILD_AUDIT_OVERLAP")
                    self.assertEqual(0, self.status()["view"]["progress"]["tasks"]["done"])
                    self.assertEqual(wave, {row["milestone"] for row in self.events()})
                    self.build(extra=["--resume-paused", "--retry-builder", "M1"])
                else:
                    self.build()
                self.env.pop("BUILD_AUDIT_OVERLAP", None)
                starts = self.events()[before:]
                self.assertEqual(wave, {row["milestone"] for row in starts})
                self.assertEqual(len(wave) + (wave_number == 0), len(starts))
                for event in starts:
                    number = int(event["milestone"][1:])
                    for parent in dependencies[number - 1]:
                        path = f"graph_nodes/node_{parent}.py"
                        self.assertEqual(spec["payloads"][f"M{parent}"][path], event["inputs"][path])
                if len(wave) > 1 and wave_number != 0:
                    ends = [row for row in self.events("finish") if row["milestone"] in wave]
                    self.assertEqual(4, len({row["workspace"] for row in starts}))
                    self.assertLess(max(row["time"] for row in starts), min(row["time"] for row in ends))
                all_starts = self.events()
                self.build()
                self.assertEqual(all_starts, self.events(), "Repeated build must retain successful sibling work")
                self.assertEqual(completed, self.status()["view"]["progress"]["tasks"]["done"])
                # The last review also proves the complete 21-node product. Its
                # real subprocess checks exceed the small fixture's 90 s bound.
                self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"], timeout=300)
                completed += len(wave)
                self.assertEqual(completed, self.status()["view"]["progress"]["tasks"]["done"])
                self.assertEqual(all_starts, self.events(), "Report repair must not replay a Builder")
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"], timeout=300)
        final = self.status()
        self.assertEqual("TASK_COMPLETE", final["status"])
        self.assertTrue(final["completion_current"])
        self.assertEqual(22, len(self.events()), "Only the crashed worker may have a second invocation")
        repairs = self.events("batch_repair", stage="tester_report_repair")
        self.assertEqual(5, len(repairs))
        self.assertEqual(waves[:-1], [set(row["scope"]["milestone_ids"]) for row in repairs])
        check = subprocess.run(
            [sys.executable, "-c", f"from graph_nodes.node_21 import value; assert value() == {values[21]}"],
            cwd=self.project,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, check.returncode, check.stdout + check.stderr)

    def test_format_failure_in_first_repair_keeps_the_original_evidence_error(self):
        spec = citation_repair_spec()
        spec["repair_loses_error"] = True
        self.seed(spec, max_parallel=2)
        self.build()
        starts = self.events()
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])
        status = self.status()
        self.assertEqual(1, status["view"]["progress"]["tasks"]["done"])
        self.assertEqual(starts, self.events(), "A reporting failure must not replay the Builder")
        repairs = self.events("citation_repair", stage="tester_report_repair")
        self.assertEqual(2, len(repairs))
        self.assertEqual(repairs[0]["error"], repairs[1]["original_error"])
        self.assertNotEqual(repairs[0]["error"], repairs[1]["error"])
        self.assertFalse(status["completion_current"])

    def test_changed_line_citation_repair_uses_enclosing_proof_without_new_builder(self):
        spec = citation_repair_spec()
        self.seed(spec, max_parallel=2)
        self.build()
        starts = self.events()
        self.invoke("autoreview", ["--run-dir", str(self.run), "--no-chat"])
        status = self.status()
        self.assertEqual(1, status["view"]["progress"]["tasks"]["done"])
        self.assertEqual(starts, self.events())
        repairs = self.events("citation_repair", stage="tester_report_repair")
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
        repairs = self.events("batch_repair", stage="tester_report_repair")
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
        self.assertEqual([], self.events(stage="resolve"))
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
